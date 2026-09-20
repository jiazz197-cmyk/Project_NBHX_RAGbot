"""BGE-M3 嵌入适配器与 PGVector 写入封装（issue #35，含 #28 范围）。

``BGEM3EmbeddingWrapper`` 是 ``llama_index.embeddings.openai.OpenAIEmbedding`` 的
薄子类：推理网关（GPUStack）暴露的就是标准 OpenAI 兼容 ``POST /v1/embeddings``，
传输层（连接复用、超时、批量分片、按 index 解析）交给 openai SDK，本模块只保留
BGE-M3 侧的承重语义：

1. 空文本 → 零向量（空串会让 rerank 网关直接 400，pgvector 也不接受空向量）；
2. NaN / Inf → 零向量（否则污染 pgvector）；
3. 批量整批失败 → 逐条回退（整批 400 时不能整批丢）；
4. 每次重试的日志行（``_embedding_retry_before_sleep``，排障依据）；
5. ``probe(timeout_sec)`` 启动探活 + ``embed_text`` / ``embed_texts`` 公开方法。

``_get_text_embeddings`` 覆写走真实批量接口，入库链路（``embed_nodes →
get_text_embedding_batch``）一次 HTTP 带 ``embed_batch_size`` 条，不再逐 chunk
串行（即 #28）。
"""

import logging
import math
import threading
from typing import Dict, List, Optional, Tuple

from llama_index.core import Settings, StorageContext, VectorStoreIndex
from llama_index.core.schema import TextNode
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.postgres import PGVectorStore
from pydantic import Field
from tenacity import (
    AsyncRetrying,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

from app.core.config import settings
from app.core.http_client import HttpClientManager, get_sync_http_client
from .exceptions import EmbeddingError, VectorStoreError

logger = logging.getLogger(__name__)

# BGE-M3 输出维度：空文本零向量占位用，与 PGVectorStore 的 embed_dim 一致
_EMBED_DIM = 1024

# 显式要 float 编码：openai SDK 不传 encoding_format 时会发 ``"base64"``（响应侧再做
# base64 解码）；改造前旧实现解析的是 JSON float 数组，这里保持同一种请求/响应形状。
_ENCODING_FORMAT = "float"

_EMBEDDING_INSTANCES: Dict[str, "BGEM3EmbeddingWrapper"] = {}
_EMBEDDING_LOCK = threading.Lock()

# 调用参数从配置读取（.env）：BGE_M3_MAX_RETRIES / BGE_M3_RETRY_DELAY_SEC；
# 模块导入时固化（settings 为导入期单例，与全局常量语义一致）
_EMBEDDING_MAX_RETRIES = settings.BGE_M3_MAX_RETRIES
_EMBEDDING_RETRY_DELAY_SEC = settings.BGE_M3_RETRY_DELAY_SEC
_RETRY_KWARGS = dict(
    stop=stop_after_attempt(_EMBEDDING_MAX_RETRIES),
    wait=wait_fixed(_EMBEDDING_RETRY_DELAY_SEC),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)


def _embedding_retry_before_sleep(label: str):
    """tenacity before_sleep 回调：按原日志格式记录每次失败重试（仅在尝试之间触发）。"""

    def _log(retry_state):
        outcome = retry_state.outcome
        exc = outcome.exception() if outcome is not None else None
        logger.warning(
            "BGE-M3 %s第 %s/%s 次失败，%ss 后重试: %s",
            label,
            retry_state.attempt_number,
            _EMBEDDING_MAX_RETRIES,
            _EMBEDDING_RETRY_DELAY_SEC,
            exc,
        )

    return _log


def _retry_call(call, label: str):
    """同步：带重试日志地执行一次请求（重试 N 次后原样抛出）。"""
    for attempt in Retrying(before_sleep=_embedding_retry_before_sleep(label), **_RETRY_KWARGS):
        with attempt:
            return call()


async def _aretry_call(call, label: str):
    """异步版本，重试参数与日志格式和同步完全一致。"""
    async for attempt in AsyncRetrying(before_sleep=_embedding_retry_before_sleep(label), **_RETRY_KWARGS):
        with attempt:
            return await call()


def _to_api_base(api_url: str) -> str:
    """``.../v1/embeddings`` → ``.../v1``。

    配置项 ``BGE_M3_API_URL`` 存的是**完整端点**（历史手写 httpx 直接 POST 它，
    ``probe_services`` 也把它当展示地址），而 openai SDK 的 ``base_url`` 会自动
    补 ``/embeddings``，所以这里只剥掉尾部的 ``/embeddings``。
    """
    base = (api_url or "").rstrip("/")
    return base[: -len("/embeddings")] if base.endswith("/embeddings") else base


def _zero_nan(vec: List[float]) -> List[float]:
    """NaN / Inf 向量回退零向量（pgvector 不接受 NaN，会污染整列）。"""
    if any(math.isnan(x) or math.isinf(x) for x in vec):
        logger.warning("检测到 NaN/Inf 嵌入，返回零向量")
        return [0.0] * len(vec)
    return vec


def _split_empty(texts: List[str]) -> Tuple[List[Optional[List[float]]], List[Tuple[int, str]]]:
    """空文本直接占位零向量，其余待请求；返回（按输入顺序的槽位, [(下标, 文本)]）。"""
    slots: List[Optional[List[float]]] = [None if t and t.strip() else [0.0] * _EMBED_DIM for t in texts]
    return slots, [(i, t) for i, t in enumerate(texts) if slots[i] is None]


class BGEM3EmbeddingWrapper(OpenAIEmbedding):
    """BGE-M3 嵌入封装（通过 OpenAI 兼容 HTTP 接口调用远程模型）。"""

    api_url: str = Field(default="", description="BGE-M3 嵌入服务完整端点（/v1/embeddings）")

    def __init__(
        self,
        api_url: Optional[str] = None,
        model_name: Optional[str] = None,
        http_client=None,
        async_http_client=None,
    ):
        try:
            api_url = api_url or settings.BGE_M3_API_URL
            # 需与服务启动时的 --served-model-name 保持一致
            model_name = model_name or settings.BGE_M3_MODEL_NAME
            super().__init__(
                # 从 kwargs 传入 model_name：绕开 OpenAIEmbedding 对官方模型枚举的校验
                model_name=model_name,
                api_base=_to_api_base(api_url),
                # 网关 Bearer Key；为空时 openai SDK 不发 Authorization 头
                api_key=(settings.AI_INFERENCE_API_KEY or "").strip(),
                timeout=float(settings.BGE_M3_TIMEOUT_SEC),
                # 重试由本模块 tenacity 统一负责（保留 BGE-M3 日志行），SDK 侧关掉
                max_retries=0,
                embed_batch_size=settings.BGE_M3_BATCH_SIZE,
                # 复用全局 httpx 连接池（与改造前 get_http_client/get_sync_http_client 一致）；
                # 这两个参数同时是测试注入口（传 MockTransport 客户端）
                http_client=http_client if http_client is not None else get_sync_http_client(),
                async_http_client=(
                    async_http_client if async_http_client is not None else HttpClientManager.get_instance()
                ),
                api_url=api_url,
            )
            with _EMBEDDING_LOCK:
                _EMBEDDING_INSTANCES[f"{api_url}:{model_name}"] = self
            logger.info(
                "BGE-M3 远程接口配置完成（仅登记地址，不校验连通性）: %s (model=%s, batch=%s)",
                api_url,
                model_name,
                self.embed_batch_size,
            )
        except Exception as exc:
            raise EmbeddingError(f"初始化 BGE-M3 远程接口失败: {exc}") from exc

    # ---- 传输层：同步 / 异步各一份，其余全部收敛到这两个 ----

    def _vectors(self, response) -> List[List[float]]:
        """按 index 归位（网关并发返回时顺序不保证），并逐条做 NaN/Inf → 零向量。"""
        items = sorted(response.data, key=lambda d: d.index)
        if any(not isinstance(item.embedding, list) for item in items):
            # issue #35 删掉的就是 `data[0].embedding` 或 `.vector` 那种形状嗅探；
            # 非 OpenAI 标准形状在这里显式报错，而不是抛出 `'NoneType' object is not iterable`
            raise EmbeddingError("嵌入响应缺少 embedding 字段（只接受 OpenAI 标准的 data[].embedding）")
        return [_zero_nan(list(item.embedding)) for item in items]

    def _post_sync(self, texts: List[str]) -> List[List[float]]:
        response = self._get_client().embeddings.create(
            model=self.model_name,
            input=texts,
            timeout=self.timeout,
            encoding_format=_ENCODING_FORMAT,
        )
        if len(response.data) != len(texts):
            raise EmbeddingError(f"嵌入接口返回条数不符: 期望 {len(texts)}，实际 {len(response.data)}")
        return self._vectors(response)

    async def _post_async(self, texts: List[str]) -> List[List[float]]:
        response = await self._get_aclient().embeddings.create(
            model=self.model_name,
            input=texts,
            timeout=self.timeout,
            encoding_format=_ENCODING_FORMAT,
        )
        if len(response.data) != len(texts):
            raise EmbeddingError(f"嵌入接口返回条数不符: 期望 {len(texts)}，实际 {len(response.data)}")
        return self._vectors(response)

    # ---- 承重语义：空文本占位、批量失败逐条回退、错误包成 EmbeddingError ----

    def _embed_texts_sync(self, texts: List[str]) -> List[List[float]]:
        """空文本零向量占位 + 单次批量请求 + 整批失败逐条回退 + 按输入顺序还原。"""
        slots, pending = _split_empty(texts)
        if pending:
            payload = [text for _, text in pending]
            try:
                vectors = _retry_call(lambda: self._post_sync(payload), "批量嵌入")
            except Exception as batch_exc:
                logger.warning("批量嵌入失败，回退逐条请求: %s", batch_exc)
                vectors = [self._embed_one_sync(text) for text in payload]
            for (slot, _), vec in zip(pending, vectors):
                slots[slot] = vec
        return [vec for vec in slots if vec is not None]

    async def _embed_texts_async(self, texts: List[str]) -> List[List[float]]:
        slots, pending = _split_empty(texts)
        if pending:
            payload = [text for _, text in pending]
            try:
                vectors = await _aretry_call(lambda: self._post_async(payload), "异步批量嵌入")
            except Exception as batch_exc:
                logger.warning("异步批量嵌入失败，回退逐条请求: %s", batch_exc)
                vectors = [await self._embed_one_async(text) for text in payload]
            for (slot, _), vec in zip(pending, vectors):
                slots[slot] = vec
        return [vec for vec in slots if vec is not None]

    def _embed_one_sync(self, text: str) -> List[float]:
        """单条路径（查询嵌入走这条）：空文本不发请求，失败只走**一个**重试周期。

        等价于改造前的 ``_fetch_embedding_sync``；不经 ``_split_empty``，所以空文本
        判断要在这里自己兜（批量路径则由 ``_split_empty`` 过滤）。
        """
        if not text or not text.strip():
            return [0.0] * _EMBED_DIM
        try:
            return _retry_call(lambda: self._post_sync([text]), "同步嵌入")[0]
        except Exception as exc:
            logger.exception("调用远程 BGE-M3 接口生成嵌入失败")
            raise EmbeddingError(
                f"调用远程 BGE-M3 接口失败（重试 {_EMBEDDING_MAX_RETRIES} 次）: {exc}"
            ) from exc

    async def _embed_one_async(self, text: str) -> List[float]:
        if not text or not text.strip():
            return [0.0] * _EMBED_DIM
        try:
            return (await _aretry_call(lambda: self._post_async([text]), "异步嵌入"))[0]
        except Exception as exc:
            raise EmbeddingError(
                f"异步调用远程 BGE-M3 接口失败（重试 {_EMBEDDING_MAX_RETRIES} 次）: {exc}"
            ) from exc

    # ---- llama-index BaseEmbedding 接口（单条走 _embed_one_*，批量走 _embed_texts_*） ----

    def _get_text_embedding(self, text: str) -> List[float]:
        return self._embed_one_sync(text)

    def _get_query_embedding(self, query: str) -> List[float]:
        return self._get_text_embedding(query)

    def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        """批量入库主链路（#28）：一次 HTTP 带 embed_batch_size 条。"""
        return self._embed_texts_sync(texts)

    async def _aget_text_embedding(self, text: str) -> List[float]:
        return await self._embed_one_async(text)

    async def _aget_query_embedding(self, query: str) -> List[float]:
        return await self._aget_text_embedding(query)

    async def _aget_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        return await self._embed_texts_async(texts)

    # ---- 对外公开方法（外部调用点沿用） ----

    def embed_text(self, text: str) -> List[float]:
        """对外暴露的文本向量化接口"""
        return self._get_text_embedding(text)

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """批量向量化文本（单次批量请求，批量失败时回退逐条）"""
        return self._embed_texts_sync(texts)

    async def probe(self, timeout_sec: float = 5.0) -> List[float]:
        """单次最小请求探活远程接口；不重试、短超时，失败抛异常。

        供启动阶段连通性检查使用：用一个极小 payload 真实走一遍
        「发请求 → 状态码 → 解析嵌入」链路，地址错误/服务未起
        会在这里暴露，而不是推迟到首次 RAG 调用。
        """
        response = await self._get_aclient().embeddings.create(
            model=self.model_name, input=["ping"], timeout=timeout_sec, encoding_format=_ENCODING_FORMAT
        )
        if not response.data or not isinstance(response.data[0].embedding, list):
            raise EmbeddingError("嵌入接口探活响应不符合 OpenAI 兼容格式（缺 data[].embedding）")
        return _zero_nan(list(response.data[0].embedding))

    @classmethod
    def cleanup_all_instances(cls):
        """清理缓存的实例（远程模式下即清缓存字典，不涉及本地显存）"""
        with _EMBEDDING_LOCK:
            for key in list(_EMBEDDING_INSTANCES.keys()):
                logger.info("清理 BGE-M3 远程实例: %s", key)
                _EMBEDDING_INSTANCES.pop(key, None)

    def get_memory_info(self) -> Dict:
        """查看当前使用信息（远程模式只展示接口信息）"""
        return {
            "mode": "remote",
            "api_url": self.api_url,
            "api_base": self.api_base,
            "model_name": self.model_name,
            "embed_batch_size": self.embed_batch_size,
            "instances_count": len(_EMBEDDING_INSTANCES),
        }


class VectorStoreManager:
    """封装 PGVector 存储（语义集合名）"""

    def __init__(self, db_config: Dict):
        self.db_config = db_config

    def _build_vector_store(self, collection_name: str) -> PGVectorStore:
        # PGVector 内部将物理表存为 data_<table_name>；
        # 这里必须传逻辑表名（如 knowledge_chunks），避免 data_data_* 重复前缀。
        try:
            return PGVectorStore.from_params(
                database=self.db_config["database"],
                host=self.db_config["host"],
                password=self.db_config["password"],
                port=self.db_config["port"],
                user=self.db_config["user"],
                table_name=collection_name,
                embed_dim=1024,
            )
        except Exception as exc:
            raise VectorStoreError(f"创建 PGVectorStore 失败: {exc}") from exc

    def upsert_chunks(self, chunks: List[TextNode], collection_name: str, embedding_model: BGEM3EmbeddingWrapper):
        """
        将文档块写入向量存储

        当前实现：使用 PGVector（PostgreSQL），数据直接存储在数据库中
        """
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
