"""PGVector 存储管理：入库 upsert 与检索侧实例缓存（issue #37 收敛版）。

本模块由 ``app/adapters/ragsystem/RAGretriever.py`` 与
``app/adapters/doc_processing/embedding_store.py`` 里两份同名实现合并而来：
二者都只是 ``PGVectorStore.from_params(..., embed_dim=1024)`` 的包装，却各自
带着不同错误处理，容易让调用方误判该用哪一份。现在全仓只有这一个
``VectorStoreManager``：

- 入库侧（document_processing pipeline）：``upsert_chunks`` 接收**逻辑集合名**
  （如 ``knowledge_chunks``；PGVector 内部映射成物理表 ``data_knowledge_chunks``），
  失败统一抛 ``VectorStoreError``；
- 检索侧（RAGRetrieverSystem / ModelManager）：``create_vector_store`` 按
  ``{table_prefix}_{instance_id}`` 做线程安全的实例缓存，避免每次检索都新建
  PGVectorStore 连接池；``list_available_collections*`` / ``aclose_all_vector_stores``
  供检索侧枚举与生命周期清理。
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from typing import TYPE_CHECKING, Dict, Iterable, List

from llama_index.core import Settings, StorageContext, VectorStoreIndex
from llama_index.core.schema import TextNode
from llama_index.vector_stores.postgres import PGVectorStore
from sqlalchemy import text

from app.core.async_bridge import run_async
from app.core.logging import get_logger
from app.adapters.doc_processing.exceptions import VectorStoreError

if TYPE_CHECKING:  # 仅为类型标注，避免与 embedding_store 循环 import
    from app.adapters.doc_processing.embedding_store import BGEM3EmbeddingWrapper

logger = get_logger("adapters.vector_store_manager")

# BGE-M3 输出维度：入库列宽与检索侧建表维度必须一致，收敛前两处各自硬编码 1024
_EMBED_DIM = 1024


class VectorStoreManager:
    """PGVector 表管理（语义集合名 + 检索侧实例缓存）。"""

    def __init__(self, db_config: Dict, table_prefix: str = "doc_collection", async_engine=None):
        self.db_config = db_config
        self.table_prefix = table_prefix
        self.vector_stores: Dict[str, PGVectorStore] = {}
        self.async_engine = async_engine
        self._stores_lock = threading.Lock()

    def _build_vector_store(self, collection_name: str) -> PGVectorStore:
        """按逻辑集合名建 PGVectorStore；失败统一抛 VectorStoreError。

        PGVector 内部将物理表存为 ``data_<collection_name>``；这里必须传逻辑表名
        （如 ``knowledge_chunks`` / ``doc_collection_1``），避免 ``data_data_*`` 重复前缀。
        """
        try:
            return PGVectorStore.from_params(
                database=self.db_config["database"],
                host=self.db_config["host"],
                password=self.db_config["password"],
                port=self.db_config["port"],
                user=self.db_config["user"],
                table_name=collection_name,
                embed_dim=_EMBED_DIM,
            )
        except Exception as exc:
            raise VectorStoreError(f"创建 PGVectorStore 失败: {exc}") from exc

    def create_vector_store(self, instance_id: int) -> PGVectorStore:
        """线程安全的 PGVectorStore 单例缓存（检索侧，保留原缓存行为）。"""
        collection_name = f"{self.table_prefix}_{instance_id}"

        cached = self.vector_stores.get(collection_name)
        if cached is not None:
            return cached

        vector_store = self._build_vector_store(collection_name)

        with self._stores_lock:
            if collection_name in self.vector_stores:
                return self.vector_stores[collection_name]

            self.vector_stores[collection_name] = vector_store
            return vector_store

    def upsert_chunks(
        self,
        chunks: List[TextNode],
        collection_name: str,
        embedding_model: "BGEM3EmbeddingWrapper",
    ):
        """将文档块写入向量存储（入库侧；失败统一抛 VectorStoreError）。"""
        try:
            vector_store = self._build_vector_store(collection_name)

            storage_context = StorageContext.from_defaults(vector_store=vector_store)
            Settings.embed_model = embedding_model

            index = VectorStoreIndex.from_vector_store(
                vector_store,
                storage_context=storage_context,
                embed_model=embedding_model,
                show_progress=False,
            )
            index.insert_nodes(chunks)

            logger.info("成功写入 PGVector: %s 条 (collection=%s)", len(chunks), collection_name)

        except Exception as exc:
            raise VectorStoreError(f"写入 PGVector 失败: {exc}") from exc

    def existing_fingerprints(
        self, collection_name: str, fingerprints: Iterable[str]
    ) -> set[str]:
        """写入前的内容级去重预检（issue #17）：返回入参中**已存在**的指纹。

        判定用 PG 侧现算的 ``md5(text)``（见 ``app/adapters/knowledge/chunk_fingerprint``），
        因此存量老数据（没有 content_hash metadata）同样覆盖，无需回填。

        - 集合表懒建表：表不存在 → 空集（视为「无重复」）；
        - 其他查询异常**向上抛**，由调用方（pipeline）决定降级，不在本层吞掉。
        """
        from app.adapters.knowledge.chunk_fingerprint import existing_fingerprints

        return existing_fingerprints(collection_name, fingerprints)

    def fingerprint_write_guard(self, collection_name: str):
        """同集合「指纹预检 + 写入」的临界区（issue #17 并发竞态）。

        用法：``with manager.fingerprint_write_guard(collection):`` —— 预检与
        upsert 必须**都**在这个 with 里，否则并发上传同一内容仍会双写（两个任务的
        预检都早于对方插入 → 都判定「无重复」）。拿不到锁不阻断写入（降级为尽力
        去重），细节见 ``app/adapters/knowledge/chunk_fingerprint``。
        """
        from app.adapters.knowledge.chunk_fingerprint import fingerprint_write_guard

        return fingerprint_write_guard(collection_name)

    def list_available_collections_sync(self) -> List[str]:
        """information_schema 里 data_% 表（同步，供 worker/线程池）。

        语义化后统一扫描全部 data_ 前缀表（data_knowledge_chunks / data_excel_db_chunks /
        历史 data_doc_collection_* 均可见），不再只扫 table_prefix 匹配的旧 instance 表。
        """
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
                result = conn.execute(query, {"pattern": "data_%"})
                tables = [row[0] for row in result.fetchall()]
            logger.debug(f"找到 {len(tables)} 个向量存储表")
            return tables
        except Exception as e:
            logger.error(f"获取向量存储表列表失败: {e}")
            return []

    async def list_available_collections(self) -> List[str]:
        """information_schema 里 data_% 表（语义化后含新集合表）。"""
        try:
            query = """
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name LIKE :pattern
            """
            async with self.async_engine.connect() as conn:
                result = await conn.execute(text(query), {"pattern": "data_%"})
                tables = [row[0] for row in result.fetchall()]
            logger.debug(f"找到 {len(tables)} 个向量存储表")
            return tables
        except Exception as e:
            logger.error(f"获取向量存储表列表失败: {e}")
            return []

    async def aclose_all_vector_stores(self) -> None:
        """关闭缓存的 PGVectorStore 并清空缓存（检索侧生命周期清理）。

        llama-index 的 ``PGVectorStore.close()`` 是**协程**（内部要 dispose 连接池），
        必须 await，否则会静默不关闭并抛 "coroutine was never awaited" RuntimeWarning。
        """
        with self._stores_lock:
            stores = list(self.vector_stores.items())
            self.vector_stores.clear()

        for collection_name, vector_store in stores:
            close_fn = getattr(vector_store, "close", None)
            if not callable(close_fn):
                continue
            try:
                result = close_fn()
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                logger.warning(f"关闭向量存储 {collection_name} 失败: {e}")

    def close_all_vector_stores(self) -> None:
        """同步入口：无事件循环时转交 :meth:`aclose_all_vector_stores`。

        若当前已在事件循环里（例如从 async 代码调用），这里不会阻塞等待，
        只告警并跳过——请改用 ``await vector_store_manager.aclose_all_vector_stores()``。
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            run_async(self.aclose_all_vector_stores())
            return
        logger.warning(
            "close_all_vector_stores() 在事件循环内被调用，已跳过关闭；"
            "请改用 await vector_store_manager.aclose_all_vector_stores()"
        )
