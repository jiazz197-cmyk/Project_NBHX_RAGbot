"""检索查询级缓存（issue #21）：结果缓存 + 查询嵌入缓存 + 集合版本号。

为什么在这里
------------
缓存挂在本模块，由**端口装饰器**（``app/adapters/retriever.py`` 的
:class:`~app.adapters.retriever.CachingRetrieverAdapter`）与**嵌入客户端**
（``app/adapters/doc_processing/embedding_store.py`` 的异步查询钩子）消费，
不进 ragsystem 内部、也不改 UseCase 签名——与 issue #21 的方案方向一致。

承重语义（别顺手删）
--------------------
1. **集合版本号做失效**：每个 collection 一个 ``retrieval:ver:<collection>``
   计数器（无 TTL；按集合数量有界），写入端 ``INCR``，缓存键携带版本号。
   旧条目不再可达、按 TTL 自然过期，写路径因此**不需要 SCAN 删键**。
   ⚠️ 版本键若被 maxmemory 淘汰会退回 0（旧键可能被重新命中）——这正是 TTL
   必须保留的原因；见 ``docs/retrieval-cache.md``。
2. **全链路 fail-open**：Redis 任何异常只记日志 + 指标，绝不抛进检索 / 入库链路。
3. **空结果、过大载荷、形状不符的内容都不缓存**：网关抖动导致的空 chunks
   不能被固化成 5 分钟的「空答案」；``/excel`` 整表 JSON 也不能撑爆 Redis。
4. **两套客户端**：读路径用全局 ``redis_manager``（async）；文档处理 worker
   线程 / 离线脚本用**惰性创建的 sync 客户端**（短超时）——worker 线程自建事件
   循环，复用全局 async 客户端会跨事件循环复用连接。
5. **键只含 collection，不含用户身份**：集合白名单（ACL）在路由层先于缓存
   执行；若将来出现按用户过滤的检索，必须重审键设计。
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

import redis as redis_sync

from app.adapters.monitoring.prometheus import (
    RETRIEVAL_CACHE_INVALIDATIONS,
    RETRIEVAL_CACHE_LOOKUPS,
)
from app.core.cache import redis_manager
from app.core.config import settings
from app.core.logging import get_logger
from app.domain.retrieval.cache_key import (
    collection_version_key,
    embedding_cache_key,
    normalize_question,
)

logger = get_logger("adapters.retrieval_cache")

# 载荷 schema 版本：形状变更时递增，旧条目自动判为无效（不解析、直接删）
PAYLOAD_VERSION = 1

# 指标层标签
LAYER_RESULT = "result"
LAYER_EMBEDDING = "embedding"

# 同步客户端超时：写入路径的 bump 不允许拖慢入库（Redis 抖动时最多等这么久）
_SYNC_SOCKET_TIMEOUT_SEC = 1.0
_SYNC_CONNECT_TIMEOUT_SEC = 0.5

# 读原始载荷的状态
_OK = "ok"
_MISS_OR_ERROR = "error"
_OFF = "off"


def _record_lookup(layer: str, outcome: str) -> None:
    """记一次缓存查询结果；指标失败绝不影响主链路。"""
    try:
        RETRIEVAL_CACHE_LOOKUPS.labels(layer=layer, outcome=outcome).inc()
    except Exception:  # noqa: BLE001 - 指标是旁路
        logger.debug("检索缓存指标记录失败: layer=%s outcome=%s", layer, outcome, exc_info=True)


def _record_invalidation() -> None:
    try:
        RETRIEVAL_CACHE_INVALIDATIONS.inc()
    except Exception:  # noqa: BLE001 - 指标是旁路
        logger.debug("检索缓存失效指标记录失败", exc_info=True)


def _coerce_vector(vector: Any) -> Optional[List[float]]:
    """缓存里的向量还原为 ``list[float]``；形状/类型不符返回 None。"""
    if not isinstance(vector, list) or not vector:
        return None
    try:
        return [float(x) for x in vector]
    except (TypeError, ValueError):
        return None


class RetrievalCache:
    """Redis 版检索缓存（结果 / 嵌入 / 集合版本号）。

    构造参数默认取 ``settings``；测试可显式传值或用
    :func:`set_retrieval_cache_for_tests` 整体替换单例。
    """

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        embedding_enabled: Optional[bool] = None,
        ttl_sec: Optional[int] = None,
        max_payload_bytes: Optional[int] = None,
        store: Any = None,
    ) -> None:
        self.enabled = (
            bool(settings.RETRIEVAL_CACHE_ENABLED) if enabled is None else bool(enabled)
        )
        self.embedding_enabled = (
            bool(settings.RETRIEVAL_EMBEDDING_CACHE_ENABLED)
            if embedding_enabled is None
            else bool(embedding_enabled)
        )
        self.ttl_sec = int(settings.RETRIEVAL_CACHE_TTL_SEC if ttl_sec is None else ttl_sec)
        self.max_payload_bytes = int(
            settings.RETRIEVAL_CACHE_MAX_PAYLOAD_BYTES
            if max_payload_bytes is None
            else max_payload_bytes
        )
        self._store = store if store is not None else redis_manager
        self._sync_client = None
        self._sync_lock = threading.Lock()
        # 同类错误只 WARNING 一次（Redis 长时间不可用时避免打爆日志），之后降 DEBUG
        self._warned: set = set()

    # ---- 日志限噪 ----

    def _warn_once(self, kind: str, message: str, *args: Any) -> None:
        if kind in self._warned:
            logger.debug(message, *args)
            return
        self._warned.add(kind)
        logger.warning(message, *args)

    # ---- 低层读写（全部 fail-open） ----

    async def _load_raw(self, key: str) -> Tuple[Any, str]:
        """读原始载荷，返回 ``(value, status)``；status ∈ {ok, error, off}。"""
        if not self.enabled or not key:
            return None, _OFF
        try:
            return await self._store.get(key), _OK
        except Exception as exc:  # noqa: BLE001 - 缓存不可用不影响检索
            self._warn_once("load", "检索缓存读取失败: key=%s err=%s", key, exc)
            return None, _MISS_OR_ERROR

    async def _delete_quiet(self, key: str) -> None:
        try:
            await self._store.delete(key)
        except Exception:  # noqa: BLE001 - 清理失败留给 TTL
            logger.debug("检索缓存键清理失败: key=%s", key, exc_info=True)

    async def _store_payload(
        self,
        key: str,
        payload: Dict[str, Any],
        *,
        layer: str,
        ttl_sec: Optional[int] = None,
    ) -> bool:
        """序列化 + 体积闸门 + 写入；跳过/失败都只记指标与日志。"""
        if not self.enabled or not key:
            # 总开关关闭 = 整体旁路；子开关（embedding_enabled）由调用方先判
            return False
        try:
            raw = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            _record_lookup(layer, "skip")
            logger.debug("检索缓存载荷无法序列化，跳过写入: key=%s err=%s", key, exc)
            return False

        size = len(raw.encode("utf-8"))
        if self.max_payload_bytes > 0 and size > self.max_payload_bytes:
            _record_lookup(layer, "skip")
            logger.debug(
                "检索缓存条目过大（%d > %d 字节），跳过写入: key=%s",
                size,
                self.max_payload_bytes,
                key,
            )
            return False

        try:
            return bool(await self._store.set(key, raw, ttl_sec or self.ttl_sec))
        except Exception as exc:  # noqa: BLE001 - 写入失败等价于没缓存
            _record_lookup(layer, "error")
            self._warn_once("store", "检索缓存写入失败: key=%s err=%s", key, exc)
            return False

    # ---- 检索结果缓存 ----

    async def get_result(self, key: str) -> Optional[Dict[str, Any]]:
        """读检索结果条目；命中返回载荷，其余（未命中 / 形状不符 / 不可用）返回 None。"""
        value, status = await self._load_raw(key)
        if status == _OFF:
            return None
        if status != _OK:
            _record_lookup(LAYER_RESULT, "error")
            return None

        valid = (
            isinstance(value, dict)
            and value.get("v") == PAYLOAD_VERSION
            and isinstance(value.get("answer"), str)
        )
        if not valid:
            if value is not None:
                logger.debug("检索缓存条目形状不符，删除后按未命中处理: key=%s", key)
                await self._delete_quiet(key)
            _record_lookup(LAYER_RESULT, "miss")
            return None

        _record_lookup(LAYER_RESULT, "hit")
        return value

    async def set_result(self, key: str, payload: Dict[str, Any]) -> bool:
        return await self._store_payload(key, payload, layer=LAYER_RESULT)

    # ---- 查询嵌入缓存（同步钩子刻意不接：HTTP 链路全异步） ----

    async def get_query_embedding(self, text: str, model: str) -> Optional[List[float]]:
        """命中返回查询向量；不可用 / 未命中 / 形状不符返回 None。"""
        if not self.embedding_enabled or not normalize_question(text):
            # 空文本由嵌入客户端直接返回零向量（确定性），不进缓存
            return None

        key = embedding_cache_key(model=model, text=text)
        value, status = await self._load_raw(key)
        if status == _OFF:
            return None
        if status != _OK:
            _record_lookup(LAYER_EMBEDDING, "error")
            return None

        vector = value.get("vector") if isinstance(value, dict) else None
        valid = isinstance(value, dict) and value.get("v") == PAYLOAD_VERSION
        floats = _coerce_vector(vector) if valid else None
        if floats is None:
            if value is not None:
                logger.debug("查询嵌入缓存条目形状不符，删除后按未命中处理: key=%s", key)
                await self._delete_quiet(key)
            _record_lookup(LAYER_EMBEDDING, "miss")
            return None

        _record_lookup(LAYER_EMBEDDING, "hit")
        return floats

    async def set_query_embedding(self, text: str, model: str, vector: Sequence[float]) -> bool:
        if not self.embedding_enabled or not normalize_question(text):
            return False
        floats = _coerce_vector(list(vector) if vector is not None else None)
        if floats is None or not any(floats):
            # 全零向量是「空文本 / NaN 回退」的占位，缓存它只会固化降级结果
            return False
        return await self._store_payload(
            embedding_cache_key(model=model, text=text),
            {"v": PAYLOAD_VERSION, "vector": floats},
            layer=LAYER_EMBEDDING,
        )

    # ---- 集合版本号（失效） ----

    async def collection_version(self, collection: str) -> Optional[int]:
        """当前集合版本号；``None`` 表示缓存不可用（调用方应旁路缓存）。"""
        if not self.enabled or not collection:
            return None
        key = collection_version_key(collection)
        try:
            raw = await self._store.get(key)
        except Exception as exc:  # noqa: BLE001 - 版本取不到就旁路
            self._warn_once("version", "读取检索缓存版本号失败: key=%s err=%s", key, exc)
            return None
        if raw is None:
            return 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.debug("检索缓存版本号形状异常，按 0 处理: key=%s value=%r", key, raw)
            return 0

    async def invalidate_collection(self, collection: str) -> Optional[int]:
        """异步失效：集合版本号自增（知识库写入 / 删除后调用）。"""
        if not self.enabled or not collection:
            return None
        key = collection_version_key(collection)
        try:
            version = await self._store.incr(key)
        except Exception as exc:  # noqa: BLE001 - 失效失败由 TTL 兜底
            self._warn_once("invalidate", "检索缓存失效失败: key=%s err=%s", key, exc)
            return None
        if version is None:
            self._warn_once("invalidate_none", "检索缓存失效未拿到版本号: key=%s", key)
            return None
        logger.info("检索缓存已失效: collection=%s version=%s", collection, version)
        _record_invalidation()
        return int(version)

    def invalidate_collection_sync(self, collection: str) -> Optional[int]:
        """同步失效：给文档处理 worker 线程 / 离线脚本用（独立 sync 客户端）。"""
        if not self.enabled or not collection:
            return None
        key = collection_version_key(collection)
        try:
            version = int(self._get_sync_client().incr(key))
        except Exception as exc:  # noqa: BLE001 - 失效失败由 TTL 兜底
            self._warn_once(
                "invalidate_sync", "检索缓存失效失败（同步路径）: key=%s err=%s", key, exc
            )
            return None
        logger.info("检索缓存已失效（同步）: collection=%s version=%s", collection, version)
        _record_invalidation()
        return version

    def _get_sync_client(self):
        """惰性创建线程安全的 sync redis 客户端（``from_url`` 不建连接）。"""
        if self._sync_client is not None:
            return self._sync_client
        with self._sync_lock:
            if self._sync_client is None:
                self._sync_client = redis_sync.Redis.from_url(
                    settings.REDIS_URL,
                    decode_responses=True,
                    socket_timeout=_SYNC_SOCKET_TIMEOUT_SEC,
                    socket_connect_timeout=_SYNC_CONNECT_TIMEOUT_SEC,
                )
                logger.debug("检索缓存已创建同步 Redis 客户端")
        return self._sync_client

    def close(self) -> None:
        """关闭同步客户端（async 客户端由 ``redis_manager.close()`` 负责）。"""
        client, self._sync_client = self._sync_client, None
        if client is None:
            return
        try:
            client.close()
            logger.info("检索缓存同步客户端已关闭")
        except Exception as exc:  # noqa: BLE001 - 关闭失败不影响退出
            logger.warning("关闭检索缓存同步客户端失败: %s", exc)


_cache_instance: Optional[RetrievalCache] = None
_cache_lock = threading.Lock()


def get_retrieval_cache() -> RetrievalCache:
    """进程级单例：首次调用按 ``settings`` 构造（不建立 Redis 连接）。"""
    global _cache_instance
    if _cache_instance is None:
        with _cache_lock:
            if _cache_instance is None:
                _cache_instance = RetrievalCache()
    return _cache_instance


def set_retrieval_cache_for_tests(cache: Optional[RetrievalCache]) -> None:
    """测试注入口：整体替换单例；传 ``None`` 表示还原为「按配置构造」。"""
    global _cache_instance
    _cache_instance = cache
