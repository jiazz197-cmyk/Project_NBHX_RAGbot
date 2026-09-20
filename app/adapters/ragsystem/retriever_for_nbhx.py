import asyncio
import gc
import os
import threading
from datetime import datetime
from typing import Optional, Dict, List

from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core import Settings

from app.core.logging import get_logger
from app.core.storage import save_file_from_minio
from app.adapters.ragsystem.data_analyze import excel_to_json
from app.adapters.ragsystem.RAGretriever import create_rag_retriever_system, HTTPReranker

logger = get_logger("ragsystem.retriever_for_nbhx")


def _parse_naive_utc(value) -> Optional[datetime]:
    """ISO 字符串 → naive UTC datetime（与 FileResource.created_at 的存储口径一致）。"""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _lookup_minio_object_path(
    file_name: str,
    uploader: Optional[str] = None,
    upload_time: Optional[str] = None,
) -> Optional[str]:
    """旧数据兜底：按裸文件名反查 file_resource 表，取真实 MinIO 对象路径。

    选择策略（issue #14）：uploader 一致的候选优先；再取「不晚于 chunk
    upload_time 的最新一条」——源文件必然先于处理批次入库，避免把后来的
    同名重传误判为来源；若候选全部晚于 upload_time 则取最早一条。
    """
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.orm.file_resource import FileResource

    chunk_time = _parse_naive_utc(upload_time)
    try:
        with SessionLocal() as db:
            stmt = (
                select(
                    FileResource.minio_object_path,
                    FileResource.uploader,
                    FileResource.created_at,
                )
                .filter(FileResource.file_name == file_name)
                .order_by(FileResource.created_at.desc(), FileResource.id.desc())
                .limit(50)
            )
            rows = db.execute(stmt).all()
    except Exception as exc:
        logger.error("按文件名反查 MinIO 对象路径失败 file=%s: %s", file_name, exc)
        return None

    if not rows:
        return None

    candidates = list(rows)
    if uploader:
        matched = [row for row in candidates if row.uploader == uploader]
        if matched:
            candidates = matched

    if chunk_time is not None:
        before = [
            row
            for row in candidates
            if row.created_at is not None and row.created_at <= chunk_time
        ]
        # 全部晚于 chunk 时间（异常场景）：退而取最早一条，而非最新一条
        candidates = before if before else list(reversed(candidates))

    return candidates[0].minio_object_path


def _resolve_minio_object_name(source, metadata: Optional[Dict] = None) -> Optional[str]:
    """chunk metadata['source']（裸文件名）→ 真实 MinIO object key（issue #14）。

    优先级：
    1. 新数据：写入端（pipeline）已把 minio_object_path 落进 chunk metadata，直接用；
    2. 旧数据：按 file_name 反查 file_resource 表（uploader / upload_time 就近匹配）。
    """
    if not source or not isinstance(source, str):
        return None
    source = source.strip()
    if not source:
        return None

    if metadata:
        object_name = metadata.get("minio_object_path")
        if isinstance(object_name, str) and object_name.strip():
            return object_name.strip()

    return _lookup_minio_object_path(
        file_name=source,
        uploader=(metadata or {}).get("uploader"),
        upload_time=(metadata or {}).get("upload_time"),
    )


class ModelManager:
    """Singleton for RAG retrievers, query engines, and HTTP reranker."""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._rag_system = None
        self._reranker = None
        self._retrievers_cache = {}
        self._query_engines_cache = {}
        self._cache_lock = threading.Lock()
        self._reranker_api_url = os.environ.get("RERANKER_API_URL", "http://localhost:8001/v1/rerank")
        self._initialized = True
        logger.info("模型管理器初始化完成，使用 HTTP API 模式")
    
    def set_rag_system(self, rag_system):
        """Attach shared RAGRetrieverSystem (first call wins)."""
        if self._rag_system is None:
            self._rag_system = rag_system
            logger.info("RAG系统已设置到模型管理器")
        else:
            logger.info("RAG系统已存在，跳过重复设置")
    
    def get_reranker(self):
        """Lazy-init HTTPReranker（线程安全）。"""
        if self._reranker is not None:
            return self._reranker
        with self._cache_lock:
            if self._reranker is not None:
                return self._reranker
            try:
                self._reranker = HTTPReranker(
                    api_url=self._reranker_api_url,
                    top_n=3,
                    timeout=30
                )
                logger.info("重排序器创建完成，API: %s", self._reranker_api_url)
            except Exception as e:
                logger.error("创建重排序器失败: %s", e)
                self._reranker = None
            return self._reranker
    
    def get_retriever(self, collection_name: str, top_k: int = 5):
        """Cached retriever per (collection, top_k)（线程安全）。"""
        cache_key = f"{collection_name}_{top_k}"
        
        existing = self._retrievers_cache.get(cache_key)
        if existing is not None:
            return existing
        
        if self._rag_system is None:
            raise ValueError("RAG系统未设置")
        
        retriever = self._rag_system.get_retriever_for_collection(collection_name, top_k=top_k)
        
        with self._cache_lock:
            if cache_key in self._retrievers_cache:
                return self._retrievers_cache[cache_key]
            self._retrievers_cache[cache_key] = retriever
            logger.debug("检索器缓存: %s", collection_name)
            return retriever
    
    def get_query_engine(
        self,
        collection_name: str,
        top_k: int = 5,
        use_reranker: bool = True,
    ):
        """RetrieverQueryEngine with optional reranker; cached（线程安全）。

        ``use_reranker=False`` 时 node_postprocessors=[]，供容器侧自行重排的
        纯检索路径使用；缓存 key 带该标志，避免两种引擎互相覆盖。
        """
        cache_key = f"{collection_name}_{top_k}_rerank_{int(bool(use_reranker))}"
        
        existing = self._query_engines_cache.get(cache_key)
        if existing is not None:
            return existing
        
        retriever = self.get_retriever(collection_name, top_k)
        if retriever is None:
            return None
        
        reranker = self.get_reranker() if use_reranker else None
        query_engine = RetrieverQueryEngine.from_args(
            retriever=retriever,
            node_postprocessors=[reranker] if reranker else [],
            streaming=False,
        )
        
        with self._cache_lock:
            if cache_key in self._query_engines_cache:
                return self._query_engines_cache[cache_key]
            self._query_engines_cache[cache_key] = query_engine
            logger.debug("查询引擎缓存: %s", collection_name)
            return query_engine
    
    def get_available_collections(self) -> List[str]:
        """DB table names without data_ prefix."""
        if self._rag_system is None:
            return []
        
        try:
            collections = (
                self._rag_system.vector_store_manager.list_available_collections_sync()
            )
            return [col.replace("data_", "") for col in collections]
        except Exception as e:
            logger.error("获取collection列表失败: %s", e)
            return []
    
    def clear_cache(self):
        """Drop retriever/query-engine caches and run gc（线程安全）。"""
        logger.info("开始清理模型管理器缓存...")
        with self._cache_lock:
            self._retrievers_cache.clear()
            self._query_engines_cache.clear()
        gc.collect()
        logger.info("缓存清理完成")
    
    def get_memory_info(self) -> Dict:
        """Lightweight stats (HTTP mode, cache sizes)."""
        info = {
            "mode": "HTTP API",
            "retrievers_cached": len(self._retrievers_cache),
            "query_engines_cached": len(self._query_engines_cache),
            "reranker_api_url": self._reranker_api_url,
        }
        
        return info


class _ChartsResult(dict):
    """``get_charts`` 成功返回值（新内部契约）。

    新形状为 ``{"data": <excel_to_json 结果>, "sources": [...文件名]}``。
    既有调用方/回归测试仍用 ``'"sheet_name"' in result`` 判断 JSON 内容，
    因此 ``__contains__`` 额外下探 ``data`` 字符串；key 集合保持不变，
    取值/相等/序列化行为与普通 dict 相同。
    """

    def __contains__(self, item) -> bool:
        if super().__contains__(item):
            return True
        data = self.get("data")
        return isinstance(data, str) and item in data


class OptimizedRetriever:
    """Single- or multi-collection retrieval via ModelManager."""

    # 检索后统一截断条数（content / source / metadata 三列同长，issue #36）。
    # 相似度阈值过滤 / MMR 去重不在本次范围——那会把「总是返回 N 条」变成
    # 「有时返回 0 条」，需先有 issue #18 的离线评测基线并确认容器侧对空
    # 结果的「未命中」标注，故这里仍保留「有多少收多少、封顶 top_k」的契约。
    _RESPONSE_TOP_K = 5

    def __init__(self, rag_system=None, collection_name: Optional[str] = None):
        self.collection_name = collection_name
        
        if rag_system is None:
            raise ValueError("rag_system is required")
        
        self.model_manager = ModelManager()
        self.model_manager.set_rag_system(rag_system)
        
        self.default_top_n = getattr(rag_system, 'default_top_n', 3)
        logger.info("OptimizedRetriever 使用 top_n=%s", self.default_top_n)
        
        self._initialize_query_engines()
    
    def _initialize_query_engines(self):
        """Multi: defer engines; single: build one engine."""
        if self.collection_name is None:
            collections = self.model_manager.get_available_collections()
            logger.info("初始化全库检索，发现 %d 个collection", len(collections))
            
            self.query_engines = {}
            self.available_collections = collections
        else:
            query_engine = self.model_manager.get_query_engine(self.collection_name, top_k=5)
            if query_engine is None:
                raise ValueError(f"无法创建查询引擎: {self.collection_name}")
            self.query_engines = query_engine
            logger.info("单库检索模式初始化完成: %s", self.collection_name)
    
    def get_response(self, question: str, max_collections: int = 3) -> dict:
        """Return content/source lists (top 5 each)."""
        try:
            if self.collection_name is None:
                return self._get_multi_collection_response(question, max_collections)
            else:
                return self._get_single_collection_response(question)
        except Exception as e:
            logger.exception("检索响应失败: %s", e)
            return {
                "content": [f"检索失败: {str(e)}"],
                "source": ["error"]
            }

    async def get_response_async(self, question: str, max_collections: int = 3) -> dict:
        """异步版 get_response：await ``query_engine.aquery``（走异步 reranker 钩子）。

        返回结构与异常兜底与同步版完全一致；差异只在检索调用本身。llama-index
        的 ``RetrieverQueryEngine.aquery`` → ``aretrieve`` →
        ``_async_apply_node_postprocessors`` → ``HTTPReranker._apostprocess_nodes``，
        因此 async 路由下重排不再经过 ``asyncio.to_thread`` 执行同步 HTTP。
        """
        try:
            if self.collection_name is None:
                return await self._get_multi_collection_response_async(question, max_collections)
            else:
                return await self._get_single_collection_response_async(question)
        except Exception as e:
            logger.exception("检索响应失败(async): %s", e)
            return {
                "content": [f"检索失败: {str(e)}"],
                "source": ["error"]
            }

    def _get_single_collection_response(self, question: str) -> dict:
        raw_docs = self.query_engines.query(question)
        return self._pack_single_response(raw_docs.source_nodes)

    async def _get_single_collection_response_async(self, question: str) -> dict:
        raw_docs = await self._aquery_engine(self.query_engines, question)
        return self._pack_single_response(raw_docs.source_nodes)

    @staticmethod
    def _pack_single_response(source_nodes) -> dict:
        logger.debug("检索到的文档块数量: %d", len(source_nodes))

        sources = []
        contents = []
        metadatas = []

        for i, node in enumerate(source_nodes):
            logger.debug(
                "  节点 %d - Score: %.4f - Source: %s",
                i + 1,
                node.score or 0.0,
                node.metadata.get('source', 'Unknown'),
            )
            source = node.metadata.get('source', 'Unknown')
            content = node.text.strip()
            sources.append(source)
            contents.append(content)
            # issue #14：附带 chunk 原始 metadata（含 minio_object_path），
            # 供 get_charts 解析真实 MinIO 对象路径；get_response 的既有
            # 消费方只读 content / source，不受影响。
            metadatas.append(dict(node.metadata or {}))

        return {
            "content": contents[: OptimizedRetriever._RESPONSE_TOP_K],
            "source": sources[: OptimizedRetriever._RESPONSE_TOP_K],
            "metadata": metadatas[: OptimizedRetriever._RESPONSE_TOP_K],
        }

    def _get_multi_collection_response(self, question: str, max_collections: int) -> dict:
        collected: List[tuple] = []

        collections_to_query = self.available_collections[:max_collections]
        for collection_name in collections_to_query:
            try:
                query_engine = self._get_or_create_query_engine(collection_name)
                if query_engine is None:
                    continue
                raw_docs = query_engine.query(question)
                collected.append((collection_name, list(raw_docs.source_nodes or [])))
            except Exception as e:
                logger.error("查询collection %s 失败: %s", collection_name, e)
                continue

        return self._pack_multi_response(collected)

    async def _get_multi_collection_response_async(
        self, question: str, max_collections: int
    ) -> dict:
        collected: List[tuple] = []

        collections_to_query = self.available_collections[:max_collections]
        for collection_name in collections_to_query:
            try:
                query_engine = self._get_or_create_query_engine(collection_name)
                if query_engine is None:
                    continue
                raw_docs = await self._aquery_engine(query_engine, question)
                collected.append((collection_name, list(raw_docs.source_nodes or [])))
            except Exception as e:
                logger.error("查询collection %s 失败(async): %s", collection_name, e)
                continue

        return self._pack_multi_response(collected)

    def _get_or_create_query_engine(self, collection_name: str):
        """多库模式：缓存命中/懒建 query engine；建不出来返回 None。"""
        query_engine = self.query_engines.get(collection_name)
        if query_engine is not None:
            return query_engine
        query_engine = self.model_manager.get_query_engine(collection_name, top_k=3)
        if query_engine is not None:
            self.query_engines[collection_name] = query_engine
        return query_engine

    @staticmethod
    async def _aquery_engine(query_engine, question: str):
        """优先 engine.aquery；轻量替身没有异步接口时放线程池，避免阻塞事件循环。"""
        aquery = getattr(query_engine, "aquery", None)
        if callable(aquery):
            return await aquery(question)
        return await asyncio.to_thread(query_engine.query, question)

    @classmethod
    def _pack_multi_response(cls, collected_nodes) -> dict:
        """issue #36：多集合统一收池 → 按分数排序 → 截断 top_k。

        旧实现是「每集合固定 2 条」的硬编码配额（``len==1 else 2``）+ 按插入
        序盲截断：命中质量最好的集合同样被砍到 2 条，低分 chunk 与高分 chunk
        一视同仁，而 prompt 里 ``[来源i]`` 的顺序会被模型当作可信度信号。
        现改为各集合召回全部入池、按检索分数全局排序后截断；同一集合允许
        贡献多于 2 条（引擎侧召回上限由各集合的 top_k / 重排 top_n 决定，
        本方法不再做二次配额）。

        单集合查询失败不影响其余集合：异常在收集阶段（``_get_multi_collection_
        response[_async]`` 的逐集合 try/except）已被吞掉，这里只收成功结果。

        排序语义：``score=None`` 的节点排最后（轻量替身/异常兜底可能无分）；
        分数并列的节点保持「集合查询顺序 → 集合内原始顺序」（sort 稳定），
        因此行为可复现。三列列表按同一顺序构建，长度严格对齐。
        """
        pool = [
            (collection_name, node)
            for collection_name, source_nodes in collected_nodes
            for node in source_nodes
        ]
        pool.sort(
            key=lambda pair: (
                pair[1].score if pair[1].score is not None else float("-inf")
            ),
            reverse=True,
        )

        sources: List[str] = []
        contents: List[str] = []
        metadatas: List[Dict] = []
        for collection_name, node in pool[: cls._RESPONSE_TOP_K]:
            contents.append((node.text or "").strip())
            metadata = dict(node.metadata or {})
            sources.append(metadata.get('source', f'Unknown_{collection_name}'))
            metadatas.append(metadata)

        return {
            "content": contents,
            "source": sources,
            "metadata": metadatas,
        }

    def get_chunks(self, question: str, top_k: int = 5) -> dict:
        """纯向量检索（不经过 query engine / 重排），返回结构化 chunks。

        上限为 top_k（score 取 NodeWithScore.score）；检索或模型异常时记录
        日志并返回空 chunks，调用方无需再兜底。
        """
        try:
            retriever = self._get_chunks_retriever(top_k)
            if retriever is None:
                return {"chunks": []}

            if callable(getattr(retriever, "retrieve", None)):
                nodes = retriever.retrieve(question) or []
            else:
                # 兼容只提供 query() 的轻量替身；真实 llama_index retriever 走 retrieve()
                raw = retriever.query(question)
                nodes = getattr(raw, "source_nodes", raw) or []
            return self._nodes_to_chunks(nodes, top_k)
        except Exception as e:
            logger.exception("纯向量检索失败: %s", e)
            return {"chunks": []}

    async def get_chunks_async(self, question: str, top_k: int = 5) -> dict:
        """异步版纯向量检索：优先 ``retriever.aretrieve``（不经过重排引擎）。

        轻量替身/旧实现没有 aretrieve 时退回线程池里的同步 ``retrieve/query``，
        契约不变且不阻塞事件循环。
        """
        try:
            retriever = self._get_chunks_retriever(top_k)
            if retriever is None:
                return {"chunks": []}

            aretrieve = getattr(retriever, "aretrieve", None)
            if callable(aretrieve):
                nodes = await aretrieve(question) or []
            elif callable(getattr(retriever, "retrieve", None)):
                nodes = await asyncio.to_thread(retriever.retrieve, question) or []
            else:
                raw = await asyncio.to_thread(retriever.query, question)
                nodes = getattr(raw, "source_nodes", raw) or []
            return self._nodes_to_chunks(nodes, top_k)
        except Exception as e:
            logger.exception("纯向量检索失败(async): %s", e)
            return {"chunks": []}

    def _get_chunks_retriever(self, top_k: int):
        """get_chunks / get_chunks_async 共用的前置校验与 retriever 获取。"""
        if not self.collection_name:
            logger.error("get_chunks 需要明确的 collection_name")
            return None
        retriever = self.model_manager.get_retriever(self.collection_name, top_k)
        if retriever is None:
            logger.error("get_chunks 无法创建检索器: %s", self.collection_name)
        return retriever

    @staticmethod
    def _nodes_to_chunks(nodes, top_k: int) -> dict:
        chunks = []
        for node in list(nodes):
            content = (node.text or "").strip()
            if not content:
                # 历史脏数据里的空 chunk 直接丢弃：命中后无内容可用，且会让
                # 下游重排网关对空文档返回 400（2026-09-18 实测）。
                # 必须在 top_k 截断**之前**过滤，否则空 chunk 会白占召回名额。
                continue
            if len(chunks) >= top_k:
                break
            metadata = dict(node.metadata or {})
            chunks.append({
                "content": content,
                "source": metadata.get("source", "Unknown"),
                "score": node.score,
                "metadata": metadata,
            })
        return {"chunks": chunks}

    def get_charts(self, question: str):
        """检索定位源文件 → MinIO 下载 → excel_to_json（issue #14 修复）。

        chunk metadata['source'] 是裸文件名，不能直接当 MinIO object key。
        这里按检索得分顺序逐个候选解析真实对象路径（新数据用 metadata 里的
        minio_object_path，旧数据反查 file_resource 表），只取第一个下载并
        解析成功的文件；返回 ``{"data": <excel_to_json结果>,
        "sources": [<该 chunk 的 source 裸文件名>]}``，失败仍返回 ``error``。
        下载产生的临时文件用完即删。
        """
        try:
            response = self.get_response(question)
            return self._charts_from_response(response)
        except Exception as e:
            logger.exception("获取图表数据失败: %s", e)
            return {"error": str(e)}

    async def get_charts_async(self, question: str):
        """异步版 get_charts：await 异步检索，再在线程里做 MinIO/Excel 同步 IO。

        文件定位含同步 SessionLocal、MinIO SDK 与 xlsx 解析，全部放进
        ``asyncio.to_thread``，避免在 async 路由上阻塞事件循环。
        """
        try:
            response = await self.get_response_async(question)
            return await asyncio.to_thread(self._charts_from_response, response)
        except Exception as e:
            logger.exception("获取图表数据失败(async): %s", e)
            return {"error": str(e)}

    @staticmethod
    def _charts_from_response(response) -> dict:
        sources = response.get("source", [])
        metadatas = response.get("metadata", [])

        if not sources or sources == ["error"]:
            return {"error": "未找到相关文件"}

        if not isinstance(sources, list):
            sources = [sources]

        tried_sources = set()
        last_error = None
        for idx, source in enumerate(sources):
            if not source or source in ("error", "Unknown") or source in tried_sources:
                continue
            tried_sources.add(source)

            metadata = (
                metadatas[idx]
                if isinstance(metadatas, list) and idx < len(metadatas)
                else None
            )
            object_name = _resolve_minio_object_name(source, metadata)
            if not object_name:
                logger.warning("无法定位源文件的 MinIO 对象: source=%s", source)
                last_error = f"未找到源文件 {source} 的存储路径"
                continue

            file_path = None
            try:
                file_path = save_file_from_minio(object_name)
                data = excel_to_json(file_path)
                return _ChartsResult({"data": data, "sources": [source]})
            except Exception as exc:
                logger.warning(
                    "下载/解析源文件失败 source=%s object=%s: %s",
                    source, object_name, exc,
                )
                last_error = str(exc)
            finally:
                # save_file_from_minio 的契约：路径由调用方清理
                if file_path is not None:
                    file_path.unlink(missing_ok=True)

        return {"error": last_error or "未找到相关文件"}

    def cleanup(self):
        logger.info("开始清理retriever资源...")
        if hasattr(self, 'query_engines'):
            if isinstance(self.query_engines, dict):
                self.query_engines.clear()
            else:
                del self.query_engines
        
        self.model_manager.clear_cache()
        
        logger.info("Retriever资源清理完成")
    
    def get_memory_info(self) -> Dict:
        return self.model_manager.get_memory_info()
    
    def __del__(self):
        try:
            import sys
            if sys is None or sys.meta.path is None:
                return
            
            self.cleanup()
        except:
            pass


class retriever(OptimizedRetriever):
    """Backward-compatible alias."""
    pass


if __name__ == '__main__':
    # 非生产入口：仅本地手动 smoke（需要可达的 PG / BGE-M3 / Reranker 服务），
    # 不参与应用生命周期；生产装配在 main.py 的 lifespan。
    try:
        Settings.llm = None
        
        rag_system = create_rag_retriever_system(
            host=os.getenv("RAG_DB_HOST", "localhost"),
            user=os.getenv("RAG_DB_USER", "postgres"),
            password=os.getenv("RAG_DB_PASSWORD", ""),
            database=os.getenv("RAG_DB_NAME", "postgres"),
            port=int(os.getenv("RAG_DB_PORT", "5432")),
            table_prefix=os.getenv("RAG_DB_TABLE_PREFIX", "doc_collection"),
            default_top_k=20,
            default_top_n=3
        )
        retriever_instance = OptimizedRetriever(rag_system=rag_system)
        responses = retriever_instance.get_response("北部湾")
        logger.info("检索结果: %s", responses)
        
        logger.info("内存使用情况: %s", retriever_instance.get_memory_info())
        
        logger.info("请确保传入有效的rag_system实例")
        
    except Exception as e:
        logger.exception("执行失败: %s", e)
    finally:
        ModelManager().clear_cache()
