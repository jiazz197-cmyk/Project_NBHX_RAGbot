"""PGVector + HTTP 嵌入/重排 API 的 RAG 检索；环境变量 BGE_M3_API_URL、RERANKER_API_URL。"""

import os
import re
import threading
from typing import List, Dict, Optional
from pathlib import Path
import httpx

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import settings
from app.core.async_bridge import run_async
from app.core.http_client import get_http_client, get_sync_http_client
from app.core.logging import get_logger
from app.adapters.doc_processing.embedding_store import BGEM3EmbeddingWrapper
from pydantic import Field

from llama_index.vector_stores.postgres import PGVectorStore
from llama_index.core import VectorStoreIndex, StorageContext, load_index_from_storage
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import NodeWithScore, QueryBundle
from llama_index.core import Settings

logger = get_logger("ragsystem.RAGretriever")


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

    def _rerank_payload(self, query_str: str, documents: List[str]) -> dict:
        return {
            "query": query_str,
            "documents": documents,
            "top_n": self.top_n,
            "model": "BAAI/bge-reranker-v2-m3",
        }

    async def _rerank_request(self, query_str: str, documents: List[str]) -> dict:
        client = await get_http_client()
        response = await client.post(
            self.api_url,
            json=self._rerank_payload(query_str, documents),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def _rerank_request_sync(self, query_str: str, documents: List[str]) -> dict:
        client = get_sync_http_client()
        response = client.post(
            self.api_url,
            json=self._rerank_payload(query_str, documents),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()
    
    def _postprocess_nodes(
        self, nodes: List[NodeWithScore], query_bundle: Optional[QueryBundle] = None
    ) -> List[NodeWithScore]:
        """请求失败或格式不对时退回截断后的原 nodes。"""
        if not query_bundle or not nodes:
            return nodes
        
        query_str = query_bundle.query_str
        
        logger.debug(f"[debug] Reranker 输入: {len(nodes)} 个节点, top_n={self.top_n}")
        
        try:
            documents = [node.node.get_content() for node in nodes]
            result = self._rerank_request_sync(query_str, documents)
            
            if "results" in result:
                logger.debug(f"[debug] Reranker API 返回了 {len(result['results'])} 个结果")
            elif "rankings" in result:
                logger.debug(f"[debug] Reranker API 返回了 {len(result['rankings'])} 个结果")
            
            if "results" in result:
                ranked_results = result["results"]
                reranked_nodes = []
                for item in ranked_results[:self.top_n]:
                    idx = item["index"]
                    score = item.get("relevance_score", item.get("score", nodes[idx].score))
                    node = nodes[idx]
                    node.score = score
                    reranked_nodes.append(node)
                logger.debug(f"[debug] Reranker 输出: {len(reranked_nodes)} 个节点")
                return reranked_nodes
            elif "rankings" in result:
                ranked_results = result["rankings"]
                reranked_nodes = []
                for item in ranked_results[:self.top_n]:
                    idx = item["doc_index"]
                    score = item["score"]
                    node = nodes[idx]
                    node.score = score
                    reranked_nodes.append(node)
                logger.debug(f"[debug] Reranker 输出: {len(reranked_nodes)} 个节点")
                return reranked_nodes
            else:
                logger.warning(f"未知的重排序响应格式: {result}，返回原始节点")
                return nodes[:self.top_n]
                
        except httpx.HTTPError as e:
            logger.error(f"调用重排序 API 失败: {e}，返回原始节点")
            return nodes[:self.top_n]
        except (KeyError, IndexError, ValueError) as e:
            logger.error(f"解析重排序响应失败: {e}，返回原始节点")
            return nodes[:self.top_n]


class VectorStoreManager:
    """PGVector 表 + 可选本地 persist 目录。"""

    def __init__(self, db_config: Dict, table_prefix: str = "doc_collection", async_engine=None):
        self.db_config = db_config
        self.table_prefix = table_prefix
        self.persist_base_dir = Path("./index_storage")
        self.vector_stores: Dict[str, PGVectorStore] = {}
        self.async_engine = async_engine
        self._stores_lock = threading.Lock()

    def get_persist_dir(self, instance_id: int) -> Path:
        """index_storage 下按 collection 分子目录。"""
        collection_name = f"{self.table_prefix}_{instance_id}"
        persist_dir = self.persist_base_dir / f"index_storage_{collection_name}"
        persist_dir.mkdir(parents=True, exist_ok=True)
        return persist_dir

    def check_persist_exists(self, persist_dir: Path) -> bool:
        """需同时存在 docstore 与 index_store。"""
        if not persist_dir.exists():
            return False
        required_files = ["docstore.json", "index_store.json"]
        return all((persist_dir / file_name).exists() for file_name in required_files)

    def create_vector_store(self, instance_id: int) -> PGVectorStore:
        """线程安全的 PGVectorStore 单例缓存。"""
        collection_name = f"{self.table_prefix}_{instance_id}"

        if collection_name in self.vector_stores:
            return self.vector_stores[collection_name]

        vector_store = PGVectorStore.from_params(
            database=self.db_config["database"],
            host=self.db_config["host"],
            password=self.db_config["password"],
            port=self.db_config["port"],
            user=self.db_config["user"],
            table_name=collection_name,
            embed_dim=1024,
        )

        with self._stores_lock:
            if collection_name in self.vector_stores:
                return self.vector_stores[collection_name]

            self.vector_stores[collection_name] = vector_store
            return vector_store

    def create_index(self, instance_id: int, embed_model) -> VectorStoreIndex:
        """有 persist 则 load，否则 from_vector_store。"""
        vector_store = self.create_vector_store(instance_id)
        persist_dir = self.get_persist_dir(instance_id)

        try:
            if self.check_persist_exists(persist_dir):
                logger.debug(f"从持久化存储加载索引: 实例 {instance_id}")
                storage_context = StorageContext.from_defaults(
                    vector_store=vector_store,
                    persist_dir=persist_dir
                )
                Settings.embed_model = embed_model
                return load_index_from_storage(storage_context, embed_model=embed_model)
            else:
                logger.debug(f"创建新索引: 实例 {instance_id}")
                storage_context = StorageContext.from_defaults(vector_store=vector_store)
                return VectorStoreIndex.from_vector_store(
                    vector_store,
                    storage_context=storage_context,
                    embed_model=embed_model,
                    show_progress=True
                )
        except Exception as e:
            logger.warning(f"索引创建/加载失败，创建新索引: {e}")
            storage_context = StorageContext.from_defaults(vector_store=vector_store)
            return VectorStoreIndex.from_vector_store(
                vector_store,
                storage_context=storage_context,
                embed_model=embed_model,
                show_progress=True
            )

    def persist_index(self, index: VectorStoreIndex, instance_id: int):
        """storage_context.persist。"""
        persist_dir = self.get_persist_dir(instance_id)
        try:
            index.storage_context.persist(persist_dir=persist_dir)
            logger.debug(f"索引已持久化: 实例 {instance_id} -> {persist_dir}")
        except Exception as e:
            logger.error(f"持久化失败: 实例 {instance_id}, 错误: {e}")
            raise

    def list_persisted_instances(self) -> List[int]:
        """扫描 persist 目录名解析 instance_id。"""
        persisted_instances = []
        if not self.persist_base_dir.exists():
            return persisted_instances

        for item in self.persist_base_dir.iterdir():
            if item.is_dir() and item.name.startswith(f"index_storage_{self.table_prefix}_"):
                try:
                    instance_id = int(item.name.split("_")[-1])
                    if self.check_persist_exists(item):
                        persisted_instances.append(instance_id)
                except (ValueError, IndexError):
                    continue

        return sorted(persisted_instances)

    def list_available_collections_sync(self) -> List[str]:
        """information_schema 里 data_{prefix}_% 表（同步，供 worker/线程池）。"""
        from app.core.database import engine

        try:
            query = text(
                """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name LIKE :pattern
            """
            )
            with engine.connect() as conn:
                result = conn.execute(
                    query, {"pattern": f"data_{self.table_prefix}_%"}
                )
                tables = [row[0] for row in result.fetchall()]
            logger.debug(f"找到 {len(tables)} 个向量存储表")
            return tables
        except Exception as e:
            logger.error(f"获取向量存储表列表失败: {e}")
            return []

    async def list_available_collections(self) -> List[str]:
        """information_schema 里 data_{prefix}_% 表。"""
        try:
            query = """
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name LIKE :pattern
            """
            async with self.async_engine.connect() as conn:
                result = await conn.execute(
                    text(query), {"pattern": f"data_{self.table_prefix}_%"}
                )
                tables = [row[0] for row in result.fetchall()]
            logger.debug(f"找到 {len(tables)} 个向量存储表")
            return tables
        except Exception as e:
            logger.error(f"获取向量存储表列表失败: {e}")
            return []

    def close_all_vector_stores(self):
        """Close cached PGVectorStore instances and clear cache."""
        with self._stores_lock:
            for collection_name, vector_store in list(self.vector_stores.items()):
                try:
                    close_fn = getattr(vector_store, "close", None)
                    if callable(close_fn):
                        close_fn()
                except Exception as e:
                    logger.warning(f"关闭向量存储 {collection_name} 失败: {e}")
            self.vector_stores.clear()

    async def drop_vector_store(self, instance_id: int):
        """DROP TABLE IF EXISTS data_..."""
        name = f"data_{self.table_prefix}_{instance_id}"
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
            raise ValueError(f"Invalid table name: {name}")
        logger.debug(f"删除向量存储表: {name}")
        try:
            async with self.async_engine.begin() as conn:
                await conn.execute(text(f'DROP TABLE IF EXISTS {name}'))
        except Exception as e:
            logger.error(f"删除向量表时出错: {e}", exc_info=True)


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
            instance_id: int = 1,
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

        self.instance_id = instance_id
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
            logger.debug(f"BGE-M3 API 连接初始化完成: {self.bge_m3_api_url}")
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
            logger.debug(f"重排序器 API 连接初始化完成: {self.reranker_api_url}")
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

    def get_retriever_by_instance_id(self, instance_id: int, top_k: int = None):
        """{table_prefix}_{id} 表名转 get_retriever_for_collection。"""
        if top_k is None:
            top_k = self.default_top_k
        collection_name = f"{self.table_prefix}_{instance_id}"
        return self.get_retriever_for_collection(collection_name, top_k=top_k)

    def get_query_engine_for_collection(self, collection_name: str, embedding_model=None, top_k: int = None, 
                                       use_reranker: bool = True, reranker_top_n: int = None):
        """RetrieverQueryEngine；可选 HTTPReranker 后处理。"""
        try:
            if embedding_model is None:
                embedding_model = self.embedding_model
            
            if top_k is None:
                top_k = self.default_top_k
            
            if reranker_top_n is None:
                reranker_top_n = self.default_top_n

            retriever = self.get_retriever_for_collection(collection_name, embedding_model=embedding_model, top_k=top_k)

            if use_reranker and self.reranker:
                if reranker_top_n != self.default_top_n:
                    reranker = HTTPReranker(
                        api_url=self.reranker_api_url,
                        top_n=min(reranker_top_n, top_k)
                    )
                else:
                    reranker = self.reranker
                return RetrieverQueryEngine.from_args(
                    retriever=retriever,
                    node_postprocessors=[reranker],
                    streaming=False,
                )
            else:
                return RetrieverQueryEngine.from_args(
                    retriever=retriever,
                    streaming=False,
                )

        except Exception as e:
            logger.error(f"创建Query Engine失败: {e}")
            raise

    async def list_available_collections(self) -> List[str]:
        """委托 VectorStoreManager。"""
        return await self.vector_store_manager.list_available_collections()

    def list_persisted_collections(self) -> List[str]:
        """persist 实例 id 转成表名前缀形式。"""
        persisted_instances = self.vector_store_manager.list_persisted_instances()
        return [f"{self.table_prefix}_{instance_id}" for instance_id in persisted_instances]

    def get_all_retrievers(self, embedding_model=None, top_k: int = None) -> Dict[str, any]:
        """枚举库表，去掉 data_ 前缀后逐个 get_retriever_for_collection。"""
        try:
            if embedding_model is None:
                embedding_model = self.embedding_model
            
            if top_k is None:
                top_k = self.default_top_k

            collection_names = (
                self.vector_store_manager.list_available_collections_sync()
            )
            retrievers = {}

            for collection_name in collection_names:
                clean_name = collection_name.replace("data_", "")
                try:
                    retriever = self.get_retriever_for_collection(clean_name, embedding_model, top_k)
                    retrievers[clean_name] = retriever
                except Exception as e:
                    logger.warning(f"为表 {clean_name} 创建检索器失败: {e}")

            logger.debug("批量创建检索器完成: %s 个", len(retrievers))
            return retrievers

        except Exception as e:
            logger.error(f"创建统一检索器失败: {e}")
            raise

    async def cleanup(self, silent=False):
        """Dispose async engine and cached PGVectorStore instances."""
        try:
            if not silent:
                logger.debug("开始清理RAG系统资源...")

            if hasattr(self, "vector_store_manager") and self.vector_store_manager:
                self.vector_store_manager.close_all_vector_stores()
                if not silent:
                    logger.debug("PGVectorStore 缓存已清理")

            try:
                from app.adapters.ragsystem.retriever_for_yamato import ModelManager

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
        instance_id: int = 1,
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
        instance_id=instance_id,
        bge_m3_api_url=bge_m3_api_url,
        reranker_api_url=reranker_api_url,
        default_top_k=default_top_k,
        default_top_n=default_top_n
    )


if __name__ == "__main__":
    Settings.llm = None

    rag_system = create_rag_retriever_system(
        host="localhost",
        user="postgres",
        password=settings.POSTGRES_PASSWORD,
        database="postgres",
        port=5432,
        table_prefix="doc_collection",
        instance_id=1,
        default_top_k=20,
        default_top_n=3
    )

    try:
        retriever = rag_system.get_retriever_by_instance_id(instance_id=1)
        results = retriever.retrieve("你的查询问题")
        print("检索结果:", results)

        retriever_custom = rag_system.get_retriever_by_instance_id(instance_id=1, top_k=30)
        results_custom = retriever_custom.retrieve("你的查询问题")
        print("自定义检索结果:", results_custom)

        query_engine = rag_system.get_query_engine_for_collection(
            collection_name="doc_collection_1",
            use_reranker=True
        )
        response = query_engine.query("你的查询问题")
        print("查询响应:", response)

        query_engine_custom = rag_system.get_query_engine_for_collection(
            collection_name="doc_collection_1",
            top_k=50,
            use_reranker=True,
            reranker_top_n=10
        )
        response_custom = query_engine_custom.query("你的查询问题")
        print("自定义查询响应:", response_custom)

        collections = run_async(rag_system.list_available_collections())
        print("可用的collections:", collections)

        all_retrievers = rag_system.get_all_retrievers()
        print("所有检索器:", all_retrievers.keys())

    finally:
        run_async(rag_system.cleanup())

