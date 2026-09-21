"""pytest 全局夹具（issue #21 起引入）。

单元测试不应触碰真实 Redis：检索缓存默认开启，若不隔离，任何走到检索 / 入库的
用例都会对 ``REDIS_HOST`` 发真实命令——本地 dev 容器有 Redis 时会污染共享实例
（版本号自增、缓存条目落库），CI 无 Redis 时会刷连接失败告警。

因此这里把单例替换成**关闭态**的缓存：语义等价于「缓存不可用 → 全链路旁路」，
被测代码路径与改造前完全一致。需要验证缓存语义的用例自行用
``retrieval_cache.set_retrieval_cache_for_tests(...)`` 注入内存假件 / 带假 store
的真实 ``RetrievalCache``。
"""

from __future__ import annotations

import pytest

from app.adapters import retrieval_cache


@pytest.fixture(autouse=True)
def _disable_retrieval_cache():
    retrieval_cache.set_retrieval_cache_for_tests(
        retrieval_cache.RetrievalCache(enabled=False, embedding_enabled=False)
    )
    try:
        yield
    finally:
        retrieval_cache.set_retrieval_cache_for_tests(None)
