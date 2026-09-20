"""PGVector + HTTP 嵌入/重排 API 的 RAG 检索；环境变量 BGE_M3_API_URL、RERANKER_API_URL、AI_INFERENCE_API_KEY。"""

import asyncio
import os
from typing import List, Dict, Optional
import httpx

from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import settings
from app.core.async_bridge import run_async
from app.core.http_client import get_http_client, get_sync_http_client
from app.core.logging import get_logger
from app.adapters.doc_processing.embedding_store import BGEM3EmbeddingWrapper
from app.adapters.vector_store_manager import VectorStoreManager
from pydantic import Field

from llama_index.vector_stores.postgres import PGVectorStore
from llama_index.core import VectorStoreIndex, StorageContext
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import NodeWithScore, QueryBundle
from llama_index.core import Settings

logger = get_logger("ragsystem.RAGretriever")

# 重排网关（bge-reranker-v2-m3，8192 token）的上下文限制是**按单条文档**算的，不是整批
# 总量：2026-09-18 实测 10 条 × 8000 字符（共 8 万字符）→ 200，单条 20000 字符 → 400
# （"maximum context length is 8192 tokens ... 13351 tokens"）；空串同样整次 400
# （"Only one multi-modal item is supported"）。实测 data_excel_db_chunks 曾存在空文本
# chunk（切割器产出），会让整次重排失败并静默降级为原始向量顺序。这里统一清洗入参：
# 空内容替换为占位符、单条超长则截断（不按文档数均分，均分会把答案行截掉）。
# 只替换/截断、不过滤，保证 index 与 nodes 下标一一对应。
_RERANK_MAX_DOC_CHARS = 6000
_EMPTY_DOC_PLACEHOLDER = "（空内容）"


def prepare_rerank_documents(
    documents: List[str],
    max_doc_chars: int = _RERANK_MAX_DOC_CHARS,
) -> List[str]:
    """清洗重排入参（空文档占位 + 单条超长截断），长度与顺序保持不变。

    6000 字符 ≈ 4000 token，对中文留足余量（实测 20000 字符 ≈ 13351 token）。
    """
    prepared: List[str] = []
    for document in documents:
        text = str(document or "").strip()
        if not text:
            text = _EMPTY_DOC_PLACEHOLDER
        if max_doc_chars > 0 and len(text) > max_doc_chars:
            text = text[:max_doc_chars]
        prepared.append(text)
    return prepared


class HTTPReranker(BaseNodePostprocessor):
    """HTTP 重排 API，解析 results / rankings 两种返回。"""
    
    api_url: str = Field(description="重排序模型 API 地址")
    top_n: int = Field(default=5, description="返回的最相关结果数量")
    timeout: int = Field(default=30, description="请求超时时间（秒）")
    
    def __init__(self, api_url: str = None, top_n: int = 5, timeout: int = 30):
        """api_url 默认 RERANKER_API_URL。"""
        if api_url is None:
            api_url = os.environ.get("RERANKER_API_URL", "http://localhost:8001/v1/rerank")
        
        super().__init__(api_url=api_url, top_n=top_n, timeout=timeout)
        logger.debug(f"重排序模型 API: {api_url}")

    def _auth_headers(self) -> Dict[str, str]:
        """网关鉴权头；AI_INFERENCE_API_KEY 为空时返回空 dict（兼容无鉴权端点）。"""
        key = (settings.AI_INFERENCE_API_KEY or "").strip()
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _rerank_payload(self, query_str: str, documents: List[str]) -> dict:
        return {
            "query": query_str,
            "documents": documents,
            "top_n": self.top_n,
            "model": settings.RERANKER_MODEL_NAME,
        }

    async def _rerank_request(self, query_str: str, documents: List[str]) -> dict:
        client = await get_http_client()
        response = await client.post(
            self.api_url,
            json=self._rerank_payload(query_str, documents),
            timeout=self.timeout,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        return response.json()

    def _rerank_request_sync(self, query_str: str, documents: List[str]) -> dict:
        client = get_sync_http_client()
        response = client.post(
            self.api_url,
            json=self._rerank_payload(query_str, documents),
            timeout=self.timeout,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        return response.json()
    
    def _parse_rerank_response(
        self, result: dict, nodes: List[NodeWithScore]
    ) -> List[NodeWithScore]:
        """解析 results / rankings 并映射回原 nodes（同步/异步共用）。

        保留改造前逐字段语义：results 用 index + relevance_score/score（缺失时回落
        原 node 分），rankings 用 doc_index + score；未知响应形状返回 nodes[:top_n]；
        字段解析异常（KeyError / IndexError / ValueError）由调用方统一兜底。
        """
        reranked_nodes: List[NodeWithScore] = []
        if "results" in result:
            logger.debug(f"[debug] Reranker API 返回了 {len(result['results'])} 个结果")
            for item in result["results"][:self.top_n]:
                idx = item["index"]
                score = item.get("relevance_score", item.get("score", nodes[idx].score))
                node = nodes[idx]
                node.score = score
                reranked_nodes.append(node)
        elif "rankings" in result:
            logger.debug(f"[debug] Reranker API 返回了 {len(result['rankings'])} 个结果")
            for item in result["rankings"][:self.top_n]:
                idx = item["doc_index"]
                score = item["score"]
                node = nodes[idx]
                node.score = score
                reranked_nodes.append(node)
        else:
            logger.warning(f"未知的重排序响应格式: {result}，返回原始节点")
            return nodes[:self.top_n]
        logger.debug(f"[debug] Reranker 输出: {len(reranked_nodes)} 个节点")
        return reranked_nodes

    def _postprocess_nodes(
        self, nodes: List[NodeWithScore], query_bundle: Optional[QueryBundle] = None
    ) -> List[NodeWithScore]:
        """同步路径：请求失败或格式不对时退回截断后的原 nodes。

        路由当前仍是同步 ``def``（FastAPI 线程池），保留本方法供同步
        ``retrieve / query`` 使用；async 调用链见 :meth:`_apostprocess_nodes`。
        """
        if not query_bundle or not nodes:
            return nodes

        query_str = query_bundle.query_str
        logger.debug(f"[debug] Reranker 输入: {len(nodes)} 个节点, top_n={self.top_n}")

        try:
            documents = prepare_rerank_documents(
                [node.node.get_content() for node in nodes]
            )
            result = self._rerank_request_sync(query_str, documents)
            return self._parse_rerank_response(result, nodes)
        except httpx.HTTPError as e:
            logger.error(f"调用重排序 API 失败: {e}，返回原始节点")
            return nodes[:self.top_n]
        except (KeyError, IndexError, ValueError) as e:
            logger.error(f"解析重排序响应失败: {e}，返回原始节点")
            return nodes[:self.top_n]

    async def _apostprocess_nodes(
        self, nodes: List[NodeWithScore], query_bundle: Optional[QueryBundle] = None
    ) -> List[NodeWithScore]:
        """异步路径：显式 await ``_rerank_request``，不再走同步 HTTP（issue #37 方案②）。

        llama-index 的 ``RetrieverQueryEngine.aretrieve / aquery`` 会走
        ``BaseNodePostprocessor.apostprocess_nodes`` → 本钩子。基类默认实现是
        ``asyncio.to_thread(self._postprocess_nodes, ...)``：若没有本覆写，async
        查询链上的重排仍会把同步 HTTP 丢进线程池（占线程、也不是真异步）。
        这里显式 ``await self._rerank_request(...)``，复用共享 AsyncClient 连接池；
        失败兜底与同步路径完全一致（退回截断后的原 nodes，不抛异常）。

        选择原因（2026-09-20 决定）：不删除异步分支，而是把它真正接上——后续
        ``/retriever/db``、``/retriever/excel`` 路由若要改 ``async def``，
        async 查询链无需再改 HTTPReranker 即可不阻塞事件循环。
        """
        if not query_bundle or not nodes:
            return nodes

        query_str = query_bundle.query_str
        logger.debug(f"[debug] Reranker(async) 输入: {len(nodes)} 个节点, top_n={self.top_n}")

        try:
            documents = prepare_rerank_documents(
                [node.node.get_content() for node in nodes]
            )
            result = await self._rerank_request(query_str, documents)
            return self._parse_rerank_response(result, nodes)
        except httpx.HTTPError as e:
            logger.error(f"调用重排序 API 失败(async): {e}，返回原始节点")
            return nodes[:self.top_n]
        except (KeyError, IndexError, ValueError) as e:
            logger.error(f"解析重排序响应失败(async): {e}，返回原始节点")
            return nodes[:self.top_n]

    async def probe(self, timeout_sec: float = 5.0) -> None:
        """单次最小请求探活重排接口；不重试、短超时，失败抛异常。

        供启动阶段连通性检查使用：走一遍「发请求 → raise_for_status →
        校验响应结构」链路，并要求返回含 results / rankings 之一，
        确保地址、路径、模型服务三者都真实可用。
        """
        client = await get_http_client()
        response = await client.post(
            self.api_url,
            json=self._rerank_payload("ping", ["ping"]),
            timeout=timeout_sec,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        result = response.json()
        if not (isinstance(result, dict) and ("results" in result or "rankings" in result)):
            raise ValueError(f"重排响应格式不符合预期: {str(result)[:200]}")


class RAGRetrieverSystem:
    """组装 embed、rerank、PG 引擎；Settings.llm 置空只做检索。"""

    def __init__(
            self,
            POSTGRES_SERVER: str,
            POSTGRES_USER: str,
            POSTGRES_PASSWORD: str,
            POSTGRES_DB: str,
            POSTGRES_PORT: int,
            table_prefix: str = "doc_collection",
            bge_m3_api_url: str = None,
            reranker_api_url: str = None,
            default_top_k: int = 10,
            default_top_n: int = 5,
    ):
        self.db_config = {
            "host": POSTGRES_SERVER,
            "user": POSTGRES_USER,
            "password": POSTGRES_PASSWORD,
            "database": POSTGRES_DB,
            "port": POSTGRES_PORT
        }

        self.table_prefix = table_prefix
        
        self.bge_m3_api_url = bge_m3_api_url or os.environ.get("BGE_M3_API_URL", "http://localhost:8000/v1/embeddings")
        self.reranker_api_url = reranker_api_url or os.environ.get("RERANKER_API_URL", "http://localhost:8001/v1/rerank")
        
        self.default_top_k = default_top_k
        self.default_top_n = default_top_n
        logger.debug(f"检索参数配置 - top_k: {default_top_k}, top_n: {default_top_n}")

        Settings.llm = None
        Settings.context_window = 8192
        Settings.num_output = 512
        logger.debug("已配置检索模式（禁用 LLM，仅做向量检索）")

        self.embedding_model = self._init_embedding_model()
        self.reranker = self._init_reranker()
        self._init_database()
        self.vector_store_manager = VectorStoreManager(
            self.db_config, table_prefix, async_engine=self.async_engine
        )

    def _init_embedding_model(self) -> BGEM3EmbeddingWrapper:
        """构造 BGEM3EmbeddingWrapper。"""
        try:
            embedding_model = BGEM3EmbeddingWrapper(api_url=self.bge_m3_api_url)
            logger.debug(f"BGE-M3 API 配置完成（不校验连通性）: {self.bge_m3_api_url}")
            return embedding_model
        except Exception as e:
            logger.error(f"嵌入模型 API 初始化失败: {e}")
            raise

    def _init_reranker(self) -> HTTPReranker:
        """构造 HTTPReranker。"""
        try:
            reranker = HTTPReranker(
                api_url=self.reranker_api_url,
                top_n=self.default_top_n,
                timeout=30
            )
            logger.debug(f"重排序器 API 配置完成（不校验连通性）: {self.reranker_api_url}")
            return reranker
        except Exception as e:
            logger.error(f"重排序器 API 初始化失败: {e}")
            raise

    def _init_database(self):
        """SQLAlchemy async engine for metadata queries."""
        async_connection_string = (
            f"postgresql+asyncpg://{self.db_config['user']}:{self.db_config['password']}"
            f"@{self.db_config['host']}:{self.db_config['port']}/{self.db_config['database']}"
        )
        self.async_engine = create_async_engine(
            async_connection_string,
            pool_size=10,
            max_overflow=5,
            pool_timeout=30,
        )

    def get_retriever_for_collection(self, collection_name: str, embedding_model=None, top_k: int = None):
        """按表名建 VectorStoreIndex.as_retriever。"""
        if top_k is None:
            top_k = self.default_top_k
        try:
            if embedding_model is None:
                embedding_model = self.embedding_model

            try:
                instance_id = int(collection_name.split('_')[-1])
            except (ValueError, IndexError):
                logger.warning(f"无法从表名 {collection_name} 提取实例ID")
                instance_id = None

            if instance_id is not None:
                vector_store = self.vector_store_manager.create_vector_store(instance_id)
            else:
                vector_store = PGVectorStore.from_params(
                    database=self.db_config["database"],
                    host=self.db_config["host"],
                    password=self.db_config["password"],
                    port=self.db_config["port"],
                    user=self.db_config["user"],
                    table_name=collection_name,
                    embed_dim=1024,
                )

            storage_context = StorageContext.from_defaults(vector_store=vector_store)
            index = VectorStoreIndex.from_vector_store(
                vector_store,
                storage_context=storage_context,
                embed_model=embedding_model,
                show_progress=False
            )

            retriever = index.as_retriever(similarity_top_k=top_k)
            logger.debug(f"成功创建检索器，表名: {collection_name}")
            return retriever

        except Exception as e:
            logger.error(f"创建检索器失败，表名: {collection_name}, 错误: {e}")
            raise

    async def list_available_collections(self) -> List[str]:
        """委托 VectorStoreManager。"""
        return await self.vector_store_manager.list_available_collections()

    async def probe_services(self, timeout_sec: float = 5.0) -> List[Dict]:
        """并发探活 BGE-M3 / Reranker 服务，返回逐服务的探活结果。

        单次最小请求、短超时、不重试；任一服务失败只体现在返回值里
        （ok=False + error），本方法自身不抛异常、不阻断启动——与
        lifespan 里 Redis/MinIO 的降级约定一致。
        """

        async def _probe_one(name: str, api_url: str, probe_call) -> Dict:
            try:
                await probe_call()
                return {"name": name, "ok": True, "api_url": api_url, "error": None}
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                logger.warning("%s 探活失败: %s (%s)", name, api_url, error)
                return {"name": name, "ok": False, "api_url": api_url, "error": error}

        async def _embedding_probe():
            await self.embedding_model.probe(timeout_sec=timeout_sec)

        async def _reranker_probe():
            if self.reranker is None:
                raise RuntimeError("重排序器未初始化")
            await self.reranker.probe(timeout_sec=timeout_sec)

        results = await asyncio.gather(
            _probe_one("BGE-M3 嵌入服务", self.bge_m3_api_url, _embedding_probe),
            _probe_one("Reranker 重排服务", self.reranker_api_url, _reranker_probe),
        )
        return list(results)

    async def cleanup(self, silent=False):
        """Dispose async engine and cached PGVectorStore instances."""
        try:
            if not silent:
                logger.debug("开始清理RAG系统资源...")

            if hasattr(self, "vector_store_manager") and self.vector_store_manager:
                # PGVectorStore.close() 是协程，必须 await（否则连接池不会真正关闭）
                await self.vector_store_manager.aclose_all_vector_stores()
                if not silent:
                    logger.debug("PGVectorStore 缓存已清理")

            try:
                from app.adapters.ragsystem.retriever_for_nbhx import ModelManager

                ModelManager().clear_cache()
            except Exception as e:
                if not silent:
                    logger.warning(f"清理 ModelManager 缓存时出错: {e}")

            if hasattr(self, "async_engine") and self.async_engine:
                await self.async_engine.dispose()
                if not silent:
                    logger.debug("数据库连接已清理")

            if not silent:
                logger.debug("RAG系统资源清理完成")

        except Exception as e:
            if not silent:
                try:
                    logger.warning(f"清理资源时出错: {e}")
                except Exception:
                    pass


def create_rag_retriever_system(
        host: str = "localhost",
        user: str = "postgres",
        password: str = "",
        database: str = "postgres",
        port: int = 5432,
        table_prefix: str = "doc_collection",
        bge_m3_api_url: str = None,
        reranker_api_url: str = None,
        default_top_k: int = 10,
        default_top_n: int = 5
) -> RAGRetrieverSystem:
    """工厂：参数原样传给 RAGRetrieverSystem。"""
    return RAGRetrieverSystem(
        POSTGRES_SERVER=host,
        POSTGRES_USER=user,
        POSTGRES_PASSWORD=password or settings.POSTGRES_PASSWORD,
        POSTGRES_DB=database,
        POSTGRES_PORT=port,
        table_prefix=table_prefix,
        bge_m3_api_url=bge_m3_api_url,
        reranker_api_url=reranker_api_url,
        default_top_k=default_top_k,
        default_top_n=default_top_n
    )


if __name__ == "__main__":
    # 非生产入口：仅本地手动 smoke（需要可达的 PG / BGE-M3 / Reranker 服务），
    # 不参与应用生命周期；生产装配在 main.py 的 lifespan。
    Settings.llm = None

    rag_system = create_rag_retriever_system(
        host="localhost",
        user="postgres",
        password=settings.POSTGRES_PASSWORD,
        database="postgres",
        port=5432,
        table_prefix="doc_collection",
        default_top_k=20,
        default_top_n=3,
    )

    try:
        retriever = rag_system.get_retriever_for_collection(collection_name="knowledge_chunks")
        print("检索结果:", retriever.retrieve("你的查询问题"))

        collections = run_async(rag_system.list_available_collections())
        print("可用的collections:", collections)
    finally:
        run_async(rag_system.cleanup())
