"""SQL Server client factory (pymssql or sqlserver_tools).

提供两种 client：
- ``get_sql_client``：单连接 client（向后兼容，单线程顺序查询场景）。
- ``get_sql_client_pool``：连接池，供多线程并行查询使用，每个工作线程独占一条连接。
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional

from app.core.circuit_breaker import CircuitBreakerOpenError, get_breaker
from app.core.config import settings
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger
from app.adapters.sqlserver.exceptions import raise_if_cancelled

logger = get_logger("database.sqlserver.client")


def _build_pymssql_conn(conf: Dict[str, Any]) -> Any:
    import pymssql  # type: ignore

    return pymssql.connect(
        server=conf["server"],
        port=conf.get("port", 1433),
        user=conf["username"],
        password=conf["password"],
        database=conf["database"],
        charset="utf8",
        as_dict=True,
        # 全部为只读 SELECT，开启 autocommit 避免每条连接携带隐式事务：
        # 这样一次查询超时不会在 LIFO 复用的下一条连接上留下 doomed 事务。
        autocommit=True,
        timeout=settings.SQLSERVER_QUERY_TIMEOUT_SEC,
        login_timeout=settings.SQLSERVER_LOGIN_TIMEOUT_SEC,
    )


def _run_pymssql_query(conn: Any, sql: str, params: Any = None) -> List[Dict[str, Any]]:
    """Execute one query on a raw pymssql connection and return rows as dicts.

    Shared by ``_PymssqlClient`` (single-connection) and ``_PooledConnClient``
    (pooled-connection) so cursor/row-conversion logic stays in one place.
    """
    with conn.cursor(as_dict=True) as cursor:
        if params is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql, params)
        rows = cursor.fetchall()
    return [dict(row) for row in rows]


def _breaker_name(conf: Dict[str, Any]) -> str:
    """Stable backend identifier for the circuit breaker (U8 vs PDM differ by database)."""
    db = conf.get("database") or conf.get("server") or "sqlserver"
    return f"sqlserver:{db}"


def get_sql_client(config: Dict[str, Any]):
    """Prefer sqlserver_tools, fall back to pymssql."""
    try:
        from sqlserver_tools import ConnectionConfig, SqlServerClient  # type: ignore

        return SqlServerClient(ConnectionConfig(**config))
    except ImportError:
        pass

    try:
        import pymssql  # type: ignore  # noqa: F401
    except ImportError as exc:
        raise ExternalServiceError(
            "SQLServer",
            "缺少依赖，请安装 sqlserver_tools 或 pymssql",
        ) from exc

    class _PymssqlClient:
        """Reuses a single pymssql connection across queries to avoid repeated
        TCP/TLS/auth handshakes (each handshake costs multiple seconds).

        Calls are guarded by a per-backend circuit breaker so that a timing-out
        backend (e.g. U8 20003) fast-fails instead of stalling worker threads."""

        def __init__(self, conf: Dict[str, Any]):
            self.conf = conf
            self._conn: Any = None
            self._breaker = get_breaker(_breaker_name(conf))

        def _ensure_conn(self) -> Any:
            if self._conn is None:
                self._conn = _build_pymssql_conn(self.conf)
            return self._conn

        def query(self, sql: str, params: Any = None) -> List[Dict[str, Any]]:
            # Fast-fail when the backend breaker is open (failure isolation).
            self._breaker.before_call()
            try:
                conn = self._ensure_conn()
                result = _run_pymssql_query(conn, sql, params)
                self._breaker.record_success()
                return result
            except Exception:
                self.close()
                self._breaker.record_failure()
                raise

        def close(self) -> None:
            conn = self._conn
            self._conn = None
            if conn is None:
                return
            try:
                conn.close()
            except Exception:
                pass

        def __enter__(self) -> "_PymssqlClient":
            return self

        def __exit__(self, exc_type, exc_val, exc_tb) -> None:
            self.close()

    return _PymssqlClient(config)


class PoolTimeout(RuntimeError):
    """Raised when an ``acquire`` cannot obtain a connection within its timeout."""


class PymssqlConnectionPool:
    """Thread-safe, bounded, BLOCKING pool of pymssql connections.

    A ``BoundedSemaphore`` (``_slots``) gates the total number of live
    connections (idle + checked-out) to ``max_size``. ``acquire`` reserves a
    slot (blocking up to ``timeout``) then reuses an idle connection or creates
    a new one; ``release``/``discard`` return the slot. Idle connections do NOT
    hold slots — a slot represents "one checked-out connection", so reuse from
    idle is free and the pool never blocks on a stash of idle conns.

    Designed as a long-lived GLOBAL shared pool: all BOM tasks check connections
    out of one instance so the ERP database sees at most ``max_size`` concurrent
    connections regardless of how many tasks/threads exist. Acquire may BLOCK
    (with timeout) instead of raising when capacity is reached — callers waiting
    for a connection is correct back-pressure, not an error.
    """

    def __init__(self, conf: Dict[str, Any], max_size: Optional[int] = None):
        self._conf = conf
        self._max_size = max_size or settings.U8_BOM_MAX_TOTAL_CONNECTIONS
        self._idle: List[Any] = []
        self._lock = threading.Lock()
        self._created_count = 0
        self._closed = False
        # One permit per allowed live connection. Acquired on checkout/create,
        # released on release/discard. Bounded so a stray double-release raises.
        self._slots = threading.BoundedSemaphore(self._max_size)
        # Keepalive 策略（对齐 PG：出借前校验 + 按年龄回收）。
        # _recycle_sec：连接 age（自创建起）超过此值则 checkout 时换新，零 ERP 往返。
        # _pre_ping：checkout 时 SELECT 1，失败透明换新；默认关闭（每次借出多一次往返）。
        self._recycle_sec: int = settings.SQLSERVER_POOL_RECYCLE_SEC
        self._pre_ping: bool = settings.SQLSERVER_POOL_PRE_PING
        # id(conn) -> time.monotonic() 出生时间戳。覆盖所有存活连接（idle + 已借出），
        # 供 checkout 时按年龄回收判定。用 id() 而非在 conn 上设属性：pymssql 连接
        # 不保证可挂任意属性；同一时刻存活的连接 id 必不重复，pop 后才可能被复用。
        self._born: Dict[int, float] = {}

    def acquire(self, timeout: Optional[float] = None) -> Any:
        """Reserve a connection, blocking up to ``timeout`` seconds.

        Reuses an idle connection if available, else creates one (capacity
        permitting). On reuse, applies keepalive (对齐 PG):

        - 按年龄回收：连接 age 超过 ``SQLSERVER_POOL_RECYCLE_SEC`` 则 checkout 时
          关闭旧连接、新建一条，零 ERP 往返（治 idle-death 的首选手段）。
        - 出借前校验：若开启 ``SQLSERVER_POOL_PRE_PING``，复用前先 ``SELECT 1``，
          失败则透明换新（行为最贴近 PG ``pool_pre_ping``，默认关闭）。

        Raises ``PoolTimeout`` on timeout, ``RuntimeError`` if the pool is closed.
        """
        if not self._slots.acquire(timeout=timeout):
            raise PoolTimeout(
                f"连接池获取连接超时({timeout}s)：当前已创建 {self._created_count}/"
                f"{self._max_size}，可能 ERP 连接被长时间占满"
            )
        with self._lock:
            if self._closed:
                self._slots.release()
                raise RuntimeError("连接池已关闭")
            conn: Any = None
            recycle = False
            if self._idle:
                # 复用空闲连接：slot 已持有，created_count 不变。
                conn = self._idle.pop()
                born_at = self._born.get(id(conn), time.monotonic())
                recycle = (
                    self._recycle_sec > 0
                    and (time.monotonic() - born_at) > self._recycle_sec
                )
            else:
                # 无空闲：新建，预先记一笔 created_count（失败时由 _materialize 回滚）。
                self._created_count += 1
        # 持有 slot。建连 / 回收 / pre-ping 均在锁外执行，避免长时间持锁阻塞其他线程。
        if conn is None:
            return self._materialize_new_conn()
        if recycle:
            # 到期换新：关闭旧连接、新建一条。created_count 净不变（关 1 + 开 1）。
            self._retire(conn)
            return self._materialize_new_conn()
        if self._pre_ping:
            # 出借前校验：SELECT 1 失败则透明换新，对调用方不可见。
            try:
                _run_pymssql_query(conn, "SELECT 1")
            except Exception:
                self._retire(conn)
                return self._materialize_new_conn()
        return conn

    def _materialize_new_conn(self) -> Any:
        """Build a fresh connection (caller already holds a slot).

        On success registers its birth timestamp. On failure releases the slot
        and applies the counter rollback:

        - 全新 checkout：acquire 已 +1，这里 -1 回滚 -> 净 0。
        - 回收/pre-ping 换新：旧连接已由 _retire 关闭但未减计数，这里 -1 补旧连接 -> 净 -1。
        """
        try:
            conn = _build_pymssql_conn(self._conf)
        except Exception:
            with self._lock:
                if self._created_count > 0:
                    self._created_count -= 1
            self._slots.release()
            raise
        # _born 受 _lock 保护：所有 _born 访问（acquire 的 get、_retire/discard/
        # close/release 的 pop）均在锁内，此处写入也纳入锁内，守住"_born 受 _lock
        # 保护"的不变量。free-threaded Python（3.13t+，PEP 703）下避免真数据竞争。
        # _build_pymssql_conn（网络 IO）仍留在锁外，不违背"建连不持锁"。
        with self._lock:
            self._born[id(conn)] = time.monotonic()
        return conn

    def _retire(self, conn: Any) -> None:
        """Close a connection being replaced (recycle / pre-ping rebuild) and
        drop its birth record. Does NOT touch ``created_count`` or the slot: the
        caller's subsequent ``_materialize_new_conn`` re-uses the slot, and the
        counter is reconciled there (success -> net 0 via the new conn; failure
        -> the -1 accounts for this retired conn)."""
        _safe_close(conn)
        with self._lock:
            self._born.pop(id(conn), None)

    def release(self, conn: Any) -> None:
        """Return a healthy connection to the idle list (frees its slot)."""
        if conn is None:
            return
        closed = False
        with self._lock:
            if self._closed:
                closed = True
                if self._created_count > 0:
                    self._created_count -= 1
                self._born.pop(id(conn), None)
            else:
                # 归还到 idle：born 记录保留，下次 checkout 据此判年龄。
                self._idle.append(conn)
        if closed:
            _safe_close(conn)
        self._slots.release()

    def discard(self, conn: Any) -> None:
        """Drop a broken connection: close it, decrement counter, free its slot."""
        if conn is None:
            return
        with self._lock:
            if self._created_count > 0:
                self._created_count -= 1
            self._born.pop(id(conn), None)
        _safe_close(conn)
        self._slots.release()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            idle = self._idle
            self._idle = []
            n_idle = len(idle)
            if self._created_count >= n_idle:
                self._created_count -= n_idle
            for c in idle:
                self._born.pop(id(c), None)
        # idle 连接不持 slot（slot 在 release 时已归还），故此处不再 release slot——
        # 否则过度 release BoundedSemaphore 抛 ValueError，且循环中断会漏关 idle 连接
        # （对齐 app/adapters/doc_processing/model_pool.py 的 BoundedInstancePool.close）。
        # 阻塞中的 acquire 由借出方 release/discard（post-close 仍释放 slot）或自身超时
        # 唤醒，醒来后看到 _closed 即抛 RuntimeError。
        for conn in idle:
            _safe_close(conn)
        logger.debug(
            "PymssqlConnectionPool closed: created_total=%s", self._created_count
        )

    @property
    def created_count(self) -> int:
        with self._lock:
            return self._created_count

    @property
    def idle_count(self) -> int:
        with self._lock:
            return len(self._idle)

    @property
    def checked_out(self) -> int:
        """当前已借出（不在 idle）的连接数 = created_count - idle_count。"""
        with self._lock:
            return max(self._created_count - len(self._idle), 0)

    @property
    def max_size(self) -> int:
        return self._max_size

    def __enter__(self) -> "PymssqlConnectionPool":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def _safe_close(conn: Any) -> None:
    try:
        conn.close()
    except Exception:
        pass


def get_sql_client_pool(config: Dict[str, Any], max_size: Optional[int] = None) -> "PymssqlConnectionPool":
    """Build a pymssql connection pool for parallel queries.

    Always uses pymssql (sqlserver_tools wrapper is single-connection oriented).
    Raises ExternalServiceError if pymssql is unavailable.
    """
    try:
        import pymssql  # type: ignore  # noqa: F401
    except ImportError as exc:
        raise ExternalServiceError(
            "SQLServer",
            "缺少 pymssql 依赖，无法创建连接池",
        ) from exc
    return PymssqlConnectionPool(config, max_size=max_size)


class _PooledConnClient:
    """Adapter exposing the same ``.query()`` interface as ``_PymssqlClient``,
    backed by a raw pooled pymssql connection.

    Unlike ``_PymssqlClient``, ``close()`` is a no-op — the connection lifecycle
    is managed by the surrounding ``pooled_client`` context manager (which
    releases on success / discards on error). Query errors are NOT auto-closed
    here so that deadlock-retry loops can reuse the same connection.
    """

    def __init__(self, conn: Any):
        self._conn = conn

    def query(self, sql: str, params: Any = None) -> List[Dict[str, Any]]:
        return _run_pymssql_query(self._conn, sql, params)

    def close(self) -> None:
        pass  # lifecycle managed by pool


@contextmanager
def pooled_client(
    pool: "PymssqlConnectionPool",
    *,
    cancel_checker: Optional[Callable[[], bool]] = None,
) -> Iterator[_PooledConnClient]:
    """Check out a connection from ``pool`` and yield a ``.query()``-compatible client.

    Acquire polls in 1s windows so a ``cancel_checker`` can interrupt a blocked
    checkout (a worker waiting for a free connection can be cancelled). On normal
    exit the connection is returned to the pool; on ANY exception (including
    BaseException) it is discarded so broken connections are never reused.
    """
    # 轮询式获取：每 1s 检查一次取消，避免被阻塞的 worker 无法响应取消。
    while True:
        try:
            conn = pool.acquire(timeout=1.0)
            break
        except PoolTimeout:
            raise_if_cancelled(cancel_checker)
            # 未取消 → 继续等待空闲连接（合理的背压）
    client = _PooledConnClient(conn)
    try:
        yield client
    except BaseException:
        # Discard (close + decrement) on ANY unwind path — including a
        # non-Exception BaseException — so a checked-out connection is never
        # orphaned outside the pool's tracking (close() only reclaims idle
        # connections). Mirrors SharedChildrenCache's BaseException handling.
        pool.discard(conn)
        raise
    else:
        pool.release(conn)


def close_sql_client(client: Any) -> None:
    """Best-effort close for any sql client shape (pymssql wrapper or sqlserver_tools)."""
    if client is None:
        return
    closer = getattr(client, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass


__all__ = [
    "get_sql_client",
    "close_sql_client",
    "CircuitBreakerOpenError",
]
