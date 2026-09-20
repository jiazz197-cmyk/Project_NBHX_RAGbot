"""Shared httpx client singletons: connection pooling, timeouts, lifecycle.

Provides both an async (:class:`HttpClientManager` / :func:`get_http_client`) and a
sync (:class:`SyncHttpClientManager` / :func:`get_sync_http_client`) singleton. The
sync singleton replaces per-call ``with httpx.Client(...)`` blocks in worker-thread
paths (embedding/rerank/OCR); per-request ``timeout=`` overrides still apply and must
be preserved by callers so request-level timeouts do not fall back to the default.
"""

from __future__ import annotations

import threading

import httpx

from app.core.config import settings


def _client_kwargs() -> dict:
    """Shared timeout/limits config for both async and sync httpx clients.

    Defined once so the async and sync singletons cannot drift; callers may still
    override per-request ``timeout=`` on individual requests.
    """
    return {
        "timeout": httpx.Timeout(settings.HTTP_CLIENT_TIMEOUT, connect=10.0),
        "limits": httpx.Limits(
            max_connections=settings.HTTP_CLIENT_MAX_CONNECTIONS,
            max_keepalive_connections=settings.HTTP_CLIENT_MAX_KEEPALIVE,
        ),
    }


class HttpClientManager:
    _instance: httpx.AsyncClient | None = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> httpx.AsyncClient:
        """同步取（必要时惰性创建）共享 async client。

        适配器可能在协程外构造（如 OpenAIEmbedding 子类在 ``__init__`` 里把 client
        注入 openai SDK），那里没法 ``await``；建连本身与事件循环无关，真正的连接池
        在首个请求时才建立，因此与 :meth:`get` 共用同一单例。加锁的原因同理：
        ``__init__`` 可能来自多个 worker 线程，并发首访不能各建一个客户端
        （那样会分裂成两个连接池）。
        """
        if cls._instance is None or cls._instance.is_closed:
            with cls._lock:
                if cls._instance is None or cls._instance.is_closed:
                    cls._instance = httpx.AsyncClient(**_client_kwargs())
        return cls._instance

    @classmethod
    async def get(cls) -> httpx.AsyncClient:
        return cls.get_instance()

    @classmethod
    async def close(cls) -> None:
        if cls._instance and not cls._instance.is_closed:
            await cls._instance.aclose()
            cls._instance = None


async def get_http_client() -> httpx.AsyncClient:
    return await HttpClientManager.get()


class SyncHttpClientManager:
    """Shared sync ``httpx.Client`` singleton for worker-thread HTTP calls.

    ``httpx.Client`` is thread-safe via its internal connection pool, so a single
    shared instance replaces per-call client creation. Callers keep their own
    per-request ``timeout=`` so request-level timeouts are unchanged. Init is guarded
    by a lock so concurrent first-access from worker threads cannot orphan an
    ``httpx.Client`` (the async manager is atomic on the event loop and needs none).
    """

    _instance: httpx.Client | None = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> httpx.Client:
        if cls._instance is None or cls._instance.is_closed:
            with cls._lock:
                if cls._instance is None or cls._instance.is_closed:
                    cls._instance = httpx.Client(**_client_kwargs())
        return cls._instance

    @classmethod
    def close(cls) -> None:
        with cls._lock:
            if cls._instance and not cls._instance.is_closed:
                cls._instance.close()
                cls._instance = None


def get_sync_http_client() -> httpx.Client:
    return SyncHttpClientManager.get()
