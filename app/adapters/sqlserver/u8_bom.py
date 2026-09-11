"""U8 BOM + Inventory recursive walk.

`InvCode` / PartId style codes are passed as pymssql parameters (`%s` placeholders);
parent codes are normalized from trusted API input, not ad-hoc SQL.
"""

from __future__ import annotations

import atexit
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from app.core.config import settings
from app.core.logging import get_logger

from app.adapters.sqlserver.client import (
    PymssqlConnectionPool,
    get_sql_client_pool,
    pooled_client,
)
from app.adapters.sqlserver.exceptions import (
    QueryCancelledError,
    U8RootFailureBreakerError,
    raise_if_cancelled,
)

logger = get_logger("database.sqlserver")

_DEADLOCK_RETRY_DELAYS_SEC: tuple[float, ...] = (0.3, 0.8, 1.5)

# 连续根节点失败上限：达到即判定系统性故障（ERP 宕机/连接耗尽/饱和）并中止任务，
# 避免在 ERP 不可用时每个根各自熬满查询超时（默认 120s）而放大 DB 负载。
# 并发下成功与失败交错会重置计数，故仅在持续大面积失败时触发。
_MAX_CONSECUTIVE_ROOT_FAILURES: int = 5

# SQL Server 单次参数化查询硬上限为 2100 个参数。IN 列表分批上限留出余量，
# 避免大 BOM（根编码 × 深度展开后子件数无界）触发 8003 "too many parameters"
# 错误而被静默吞掉，导致价格/名称补充丢失。
_IN_CLAUSE_BATCH_SIZE: int = 1000


# ---------------------------------------------------------------------------
# 诊断计数器：追踪单次查询并行展开的实际并发度和 SQL vs Python 时间比。
# 仅用于日志诊断，不影响功能。
#
# 每次请求创建独立的 _DiagCounters 实例并下传，而非使用模块全局变量——
# 否则并发的多个 BOM 任务会互相 reset/累加同一组计数器，导致数值串台
# （甚至 _diag_active_threads 被减成负数）。
# ---------------------------------------------------------------------------
class _DiagCounters:
    """Per-request diagnostic counters, thread-safe across the request's workers."""

    def __init__(self) -> None:
        self.active_threads: int = 0
        self.max_concurrent: int = 0
        self.sql_time_sec: float = 0.0
        self.python_time_sec: float = 0.0
        self.query_count: int = 0
        self._lock = threading.Lock()

    def thread_enter(self) -> None:
        with self._lock:
            self.active_threads += 1
            if self.active_threads > self.max_concurrent:
                self.max_concurrent = self.active_threads

    def thread_exit(self) -> None:
        with self._lock:
            self.active_threads -= 1

    def record_sql(self, elapsed: float) -> None:
        with self._lock:
            self.sql_time_sec += elapsed
            self.query_count += 1

    def record_python(self, elapsed: float) -> None:
        with self._lock:
            self.python_time_sec += elapsed

    def log_summary(self, label: str) -> None:
        with self._lock:
            sql_t = self.sql_time_sec
            py_t = self.python_time_sec
            qc = self.query_count
            mc = self.max_concurrent
        if qc > 0:
            logger.info(
                "[DIAG] %s: max_concurrent_threads=%s, query_count=%s, "
                "sql_time=%.2fs, python_time=%.2fs, avg_sql=%.1fms, "
                "sql_pct=%.0f%%",
                label, mc, qc, sql_t, py_t,
                (sql_t / qc) * 1000,
                (sql_t / (sql_t + py_t) * 100) if (sql_t + py_t) > 0 else 0,
            )


class SharedChildrenCache:
    """Thread-safe cross-root cache for BOM children lookups.

    Multiple worker threads (each with its own pooled connection) consult this
    shared cache so that a subassembly appearing under several root codes is
    fetched from SQL Server only once globally.

    The SQL fetch runs OUTSIDE the lock: a short critical section registers a
    per-key ``threading.Event`` in-flight marker (or returns the cached result
    if already present). Same-code waiters block on the Event while the first
    fetcher runs the query on its own connection; different-code requests
    proceed in parallel without contending. This preserves cross-root dedup
    without serializing distinct queries through a single lock.

    On a fetch failure the exception is cached against the key so woken waiters
    re-raise it directly instead of each independently re-querying (which would
    amplify DB load under persistent failures via deadlock-retry backoff). A
    short negative-TTL (``_NEG_TTL_SEC``) bounds how long a cached failure is
    reused, allowing a later retry after the condition clears.

    Rows are shared read-only after population, so returning the same list
    object to multiple walkers is safe as long as walkers do not mutate it
    (they only read).
    """

    _NEG_TTL_SEC: float = 5.0

    def __init__(self) -> None:
        self._store: Dict[str, List[Dict[str, Any]]] = {}
        self._inflight: Dict[str, threading.Event] = {}
        # key -> (exception, expiry_timestamp)
        self._errors: Dict[str, tuple[BaseException, float]] = {}
        self._lock = threading.Lock()

    def get_or_fetch(
        self,
        parent_inv_code: str,
        fetch_fn: Callable[[], List[Dict[str, Any]]],
        cancel_checker: Optional[Callable[[], bool]] = None,
    ) -> List[Dict[str, Any]]:
        # Fast path: already cached.
        cached = self._store.get(parent_inv_code)
        if cached is not None:
            return cached

        # Short critical section: register an in-flight marker or join an
        # existing one. Does NOT run fetch_fn() under the lock.
        with self._lock:
            cached = self._store.get(parent_inv_code)
            if cached is not None:
                return cached

            # Expire a stale cached failure so a later caller may retry.
            err_entry = self._errors.get(parent_inv_code)
            if err_entry is not None and time.time() >= err_entry[1]:
                self._errors.pop(parent_inv_code, None)
                err_entry = None

            # Honor a still-valid (non-expired) cached failure: re-raise it
            # instead of becoming a fetcher, so the negative-TTL actually bounds
            # re-querying under persistent failures (the inflight marker was
            # removed on the prior failure, so without this a fresh caller would
            # otherwise re-query the DB immediately).
            if err_entry is not None:
                raise err_entry[0]

            event = self._inflight.get(parent_inv_code)
            if event is None:
                # We are the first fetcher for this code.
                event = threading.Event()
                self._inflight[parent_inv_code] = event
                is_fetcher = True
            else:
                is_fetcher = False

        if is_fetcher:
            # Run the SQL query OUTSIDE the lock on this thread's connection.
            try:
                rows = fetch_fn()
                with self._lock:
                    self._store[parent_inv_code] = rows
                    self._inflight.pop(parent_inv_code, None)
                    self._errors.pop(parent_inv_code, None)
                event.set()
                return rows
            except BaseException as exc:
                # Cache the failure so waiters re-raise instead of re-querying.
                with self._lock:
                    self._errors[parent_inv_code] = (exc, time.time() + self._NEG_TTL_SEC)
                    self._inflight.pop(parent_inv_code, None)
                event.set()
                raise
        else:
            # Wait for the in-flight fetcher to finish, polling cancel between
            # short waits so a blocked waiter can be interrupted (it otherwise
            # holds a pooled connection it is not using until the fetcher's SQL
            # returns — which may be up to the query timeout).
            while not event.wait(timeout=1.0):
                raise_if_cancelled(cancel_checker)
            cached = self._store.get(parent_inv_code)
            if cached is not None:
                return cached
            # Fetcher failed: re-raise the cached exception (no re-query).
            with self._lock:
                err_entry = self._errors.get(parent_inv_code)
            if err_entry is not None:
                raise err_entry[0]
            # No cached error and no result (shouldn't happen) — fail loudly.
            raise RuntimeError(
                f"SharedChildrenCache: in-flight fetch for {parent_inv_code!r} "
                f"completed without result or cached error"
            )


# ---------------------------------------------------------------------------
# 并发控制（共享连接池模型）：
#
# 1. 全局共享连接池 _shared_pool：大小 = U8_BOM_MAX_TOTAL_CONNECTIONS，是到 U8 ERP
#    的连接硬上限。所有 BOM 任务共用，按需 acquire/release。这是保护 ERP 的唯一闸门。
#
# 2. 全局 BOM 任务信号量 _bom_concurrency_semaphore：大小 = U8_BOM_MAX_CONCURRENT_TASKS，
#    限制同时运行的 BOM 任务数（即嵌套线程池总数 / 线程数上限），与 ERP 连接数解耦。
#
# 3. 每用户信号量 _per_user_semaphores：大小 = U8_BOM_MAX_CONCURRENT_TASKS_PER_USER，
#    限制单用户同时运行的 BOM 任务数，保证 30 人团队公平性，防单人刷爆全局额度。
#
# 4. 全局线程池 EXECUTOR_MAX_WORKERS：≥ 任务信号量，否则任务会在执行器队列“看戏”。
# ---------------------------------------------------------------------------
_bom_concurrency_semaphore: Optional[threading.BoundedSemaphore] = None
_bom_semaphore_init_lock = threading.Lock()


def _get_bom_concurrency_semaphore() -> threading.BoundedSemaphore:
    """Lazily build (once) the BOM task concurrency semaphore from settings."""
    global _bom_concurrency_semaphore
    if _bom_concurrency_semaphore is None:
        with _bom_semaphore_init_lock:
            if _bom_concurrency_semaphore is None:
                size = settings.U8_BOM_MAX_CONCURRENT_TASKS
                _bom_concurrency_semaphore = threading.BoundedSemaphore(size)
                logger.info(
                    "U8 BOM 任务信号量初始化: max_concurrent_tasks=%s (每用户=%s, "
                    "共享连接池=%s)",
                    size,
                    settings.U8_BOM_MAX_CONCURRENT_TASKS_PER_USER,
                    settings.U8_BOM_MAX_TOTAL_CONNECTIONS,
                )
    return _bom_concurrency_semaphore


# 全局共享 U8 连接池（懒初始化，进程级单例）。
_shared_pool: Optional[PymssqlConnectionPool] = None
_shared_pool_init_lock = threading.Lock()


def _get_shared_pool() -> PymssqlConnectionPool:
    """Lazily build (once) the process-global shared U8 connection pool."""
    global _shared_pool
    if _shared_pool is None:
        with _shared_pool_init_lock:
            if _shared_pool is None:
                conf = {
                    "backend": "pymssql",
                    "server": settings.U8_SQLSERVER_HOST,
                    "port": settings.U8_SQLSERVER_PORT,
                    "database": settings.U8_SQLSERVER_DATABASE,
                    "username": settings.U8_SQLSERVER_USER,
                    "password": settings.U8_SQLSERVER_PASSWORD,
                    "encrypt": settings.U8_SQLSERVER_ENCRYPT,
                }
                _shared_pool = get_sql_client_pool(
                    conf, max_size=settings.U8_BOM_MAX_TOTAL_CONNECTIONS
                )
                logger.info(
                    "U8 共享连接池初始化: max_size=%s (所有 BOM 任务共用)",
                    settings.U8_BOM_MAX_TOTAL_CONNECTIONS,
                )
                # 进程级单例永不在请求结束时关闭；注册 atexit 优雅释放空闲连接，
                # 避免进程退出时连接未关（在测试日志里留 warning）。main.py 的有序
                # shutdown 也会显式调用 close_shared_u8_pool()（pool.close() 幂等）。
                atexit.register(close_shared_u8_pool)
    return _shared_pool


def close_shared_u8_pool() -> None:
    """关闭共享连接池的空闲连接（供 main.py 有序 shutdown 与 atexit 共用）。

    仅关闭 idle 连接；已借出的连接仍由调用方持有，归还时走 release/discard 的
    post-close 路径关闭。``pool.close()`` 幂等，重复调用安全。
    """
    global _shared_pool
    pool = _shared_pool
    if pool is not None:
        try:
            pool.close()
        except Exception as exc:  # noqa: BLE001 — 退出钩子不能抛
            logger.warning("U8 共享连接池关闭异常: %s", exc)


# 每用户 BOM 任务信号量表（user_key -> BoundedSemaphore）。
# user_key 来自同步 API 的 current_user.id 或 Phase2 的 task owner_id。
# 30 人团队规模下条目数有限，不做淘汰；若未来 user_key 基数变大需加 LRU。
_per_user_semaphores: Dict[str, threading.BoundedSemaphore] = {}
_per_user_sem_lock = threading.Lock()


def _get_per_user_semaphore(user_key: str) -> threading.BoundedSemaphore:
    with _per_user_sem_lock:
        sem = _per_user_semaphores.get(user_key)
        if sem is None:
            sem = threading.BoundedSemaphore(
                settings.U8_BOM_MAX_CONCURRENT_TASKS_PER_USER
            )
            _per_user_semaphores[user_key] = sem
        return sem


def _acquire_per_user_slot(
    user_key: Optional[str],
    cancel_checker: Optional[Callable[[], bool]] = None,
) -> bool:
    """Block until the user's BOM-task slot is available. No-op if no user_key."""
    if not user_key:
        return False
    sem = _get_per_user_semaphore(user_key)
    waited = False
    while not sem.acquire(timeout=1.0):
        if not waited:
            logger.info("U8 BOM 每用户并发已满，排队等待: user=%s", user_key)
            waited = True
        raise_if_cancelled(cancel_checker)
    return True


def _release_per_user_slot(user_key: Optional[str], held: bool) -> None:
    if not user_key or not held:
        return
    sem = _get_per_user_semaphore(user_key)
    try:
        sem.release()
    except ValueError:
        logger.warning("U8 BOM 每用户信号量重复释放: user=%s", user_key)


def _acquire_bom_slot(
    cancel_checker: Optional[Callable[[], bool]] = None,
) -> None:
    """Block until a BOM concurrency slot is available (supports cancellation).

    Polls with a 1s timeout so a cancel request can still interrupt the wait;
    re-checks ``cancel_checker`` between attempts.
    """
    sem = _get_bom_concurrency_semaphore()
    waited = False
    while not sem.acquire(timeout=1.0):
        if not waited:
            logger.info("U8 BOM 并发槽位已满，任务排队等待...")
            waited = True
        raise_if_cancelled(cancel_checker)
    if waited:
        logger.info("U8 BOM 并发槽位已获取，开始执行查询")


def _release_bom_slot() -> None:
    sem = _get_bom_concurrency_semaphore()
    try:
        sem.release()
    except ValueError:
        # BoundedSemaphore.release() 仅在重复释放（超过初始计数）时抛 ValueError。
        # 当前 acquire/release 一一配对、由 slot_held 守卫，正常不会触发；
        # 若触发说明存在重复释放 bug（会让槽位计数虚高、绕过并发上限），
        # 记日志而非静默吞掉，便于发现会计错误。
        logger.warning(
            "U8 BOM 并发信号量重复释放（ValueError 已忽略），"
            "可能存在 slot 重复释放 bug"
        )


def _is_sqlserver_deadlock_error(exc: BaseException) -> bool:
    for arg in getattr(exc, "args", ()):  # pymssql usually exposes 1205 in args[0]
        if arg == 1205:
            return True
        if isinstance(arg, bytes) and b"1205" in arg:
            return True
        if isinstance(arg, str) and "1205" in arg:
            return True
    text = str(exc)
    return "1205" in text and "deadlock" in text.lower()


# 可跳过的根查询错误码（pymssql 把 SQL Server 错误号放在 args[0]）：
# 20003 = 读超时，20047 = 锁超时，1205 = 死锁（_is_sqlserver_deadlock_error 覆盖）。
# 仅这些 DB/ERP 层瞬时故障允许故障隔离；代码缺陷（KeyError/AttributeError 等）
# 不在此列，应原样上抛暴露。
_U8_SKIP_ROOT_ERROR_CODES: tuple[int, ...] = (20003, 20047, 1205)


def _is_recoverable_root_failure(exc: BaseException) -> bool:
    """判断异常是否属于可"跳过该根继续其余根"的可恢复 DB 层故障。

    仅通过结构化 error code（pymssql args[0]）和死锁文本特征判定；
    不使用 str(exc) 模糊匹配，避免含数字串的非 DB 异常被误判为可恢复。
    """
    if _is_sqlserver_deadlock_error(exc):
        return True
    for arg in getattr(exc, "args", ()):
        if isinstance(arg, int) and arg in _U8_SKIP_ROOT_ERROR_CODES:
            return True
    return False


def _query_with_deadlock_retry(
    client: Any,
    sql: str,
    *,
    log_label: str,
    cancel_checker: Optional[Callable[[], bool]] = None,
    params: Any = None,
) -> List[Dict[str, Any]]:
    attempt = 0
    while True:
        try:
            return client.query(sql, params)
        except Exception as exc:
            if not _is_sqlserver_deadlock_error(exc) or attempt >= len(_DEADLOCK_RETRY_DELAYS_SEC):
                raise
            delay = _DEADLOCK_RETRY_DELAYS_SEC[attempt]
            attempt += 1
            logger.warning(
                "U8 SQLServer deadlock 1205，sleep 后重试: label=%s, attempt=%s/%s, delay=%.1fs",
                log_label,
                attempt,
                len(_DEADLOCK_RETRY_DELAYS_SEC),
                delay,
            )
            raise_if_cancelled(cancel_checker)
            time.sleep(delay)
            raise_if_cancelled(cancel_checker)


def _is_price_missing(value: Any) -> bool:
    """Return True if the price value is NULL, empty, or zero."""
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    try:
        return float(text) == 0
    except Exception:
        return True


def _query_recordoutlist_prices(
    client: Any,
    missing_codes: set[str],
    cancel_checker: Optional[Callable[[], bool]] = None,
) -> Dict[str, float]:
    """Query recordoutlist for latest iunitcost per cinvcode.

    Returns a dict mapping cinvcode -> iunitcost (latest record per code).
    Falls back to simpler ORDER BY if ddate column is unavailable.

    Codes are queried in batches of ``_IN_CLAUSE_BATCH_SIZE`` so a large BOM
    (which can yield far more distinct child codes than the 1500-root input
    cap) never exceeds SQL Server's 2100-parameter limit.
    """
    if not missing_codes:
        return {}

    codes = sorted(missing_codes)

    # Try with ddate first (standard U8 date column), fallback to autoid only
    for order_by in ("ddate DESC, autoid DESC", "autoid DESC"):
        result: Dict[str, float] = {}
        all_failed = True
        for start in range(0, len(codes), _IN_CLAUSE_BATCH_SIZE):
            raise_if_cancelled(cancel_checker)
            batch = codes[start:start + _IN_CLAUSE_BATCH_SIZE]
            placeholders = ", ".join(["%s"] * len(batch))
            sql = f"""
                SELECT cinvcode, iunitcost
                FROM (
                    SELECT
                        cinvcode,
                        iunitcost,
                        ROW_NUMBER() OVER (
                            PARTITION BY cinvcode
                            ORDER BY {order_by}
                        ) AS rn
                    FROM UFDATA_888_2016.dbo.recordoutlist
                    WHERE cinvcode IN ({placeholders})
                      AND iunitcost IS NOT NULL
                      AND iunitcost <> 0
                ) t
                WHERE rn = 1
            """
            try:
                rows = _query_with_deadlock_retry(
                    client,
                    sql,
                    log_label=f"recordoutlist_prices:{order_by}",
                    cancel_checker=cancel_checker,
                    params=batch,
                )
                for row in rows:
                    code = str(row.get("cinvcode", "")).strip()
                    cost = row.get("iunitcost")
                    if code and cost is not None:
                        try:
                            result[code] = float(cost)
                        except (ValueError, TypeError):
                            pass
                all_failed = False
            except Exception as exc:
                logger.warning(
                    "recordoutlist 价格补充查询失败 (ORDER BY %s, batch=%s..%s): %s",
                    order_by, start, start + len(batch), exc,
                )
                # 单批失败不致命：继续尝试剩余批次，d 缺失的编码保持原值。
                # 整个 ORDER BY 策略仅当所有批次都失败时才回退到下一个 ORDER BY。
                continue

        if not all_failed:
            return result
        # 所有批次均失败 → 尝试下一个 ORDER BY 策略

    logger.warning("recordoutlist 价格补充查询失败 (所有 ORDER BY 回退)")
    return {}


def _supplement_missing_prices(
    result_rows: List[Dict[str, Any]],
    client: Any,
    cancel_checker: Optional[Callable[[], bool]] = None,
) -> None:
    """Supplement missing iInvNcost from recordoutlist, in-place.

    For every row where iInvNcost is NULL/empty/0, look up the latest
    iunitcost from recordoutlist for that ChildInvCode and fill it in.
    Also recalculate TOTAL_PRICE for supplemented rows.
    """
    missing_codes: set[str] = set()
    for row in result_rows:
        if _is_price_missing(row.get("iInvNcost")):
            code = str(row.get("ChildInvCode") or "").strip()
            if code:
                missing_codes.add(code)

    if not missing_codes:
        return

    supplement_prices = _query_recordoutlist_prices(client, missing_codes, cancel_checker)
    if not supplement_prices:
        logger.info("U8 价格补充: recordoutlist 无匹配价格, missing_codes=%s", len(missing_codes))
        return

    supplemented_count = 0
    for row in result_rows:
        if not _is_price_missing(row.get("iInvNcost")):
            continue
        code = str(row.get("ChildInvCode") or "").strip()
        fallback_price = supplement_prices.get(code)
        if fallback_price is None:
            continue

        row["iInvNcost"] = fallback_price

        cum_qty = row.get("CUM_QTY")
        if cum_qty is not None:
            try:
                row["TOTAL_PRICE"] = float(cum_qty) * fallback_price
            except (ValueError, TypeError):
                row["TOTAL_PRICE"] = None

        supplemented_count += 1

    if supplemented_count > 0:
        logger.info(
            "U8 价格补充完成: supplemented=%s/%s missing, queried_codes=%s",
            supplemented_count,
            len(missing_codes),
            len(supplement_prices),
        )


def split_parent_inv_codes(value: Any) -> List[str]:
    """Normalize parent_inv_codes input; dedupe by order."""
    if value is None:
        return []

    codes: List[str] = []
    if isinstance(value, str):
        parts = re.split(r"[;,/|\s、，；]+", value)
        for part in parts:
            code = part.strip()
            if code:
                codes.append(code)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        for item in value:
            code = str(item).strip()
            if code:
                codes.append(code)
    else:
        code = str(value).strip()
        if code:
            codes.append(code)

    return list(dict.fromkeys(codes))


def _fetch_root_inv_names(
    client: Any,
    codes: List[str],
    cancel_checker: Optional[Callable[[], bool]] = None,
) -> Dict[str, str]:
    """批量查询根父件编码对应的名称。

    分批查询以避免超大批量编码触发 SQL Server 的 2100 参数上限。
    """
    if not codes:
        return {}

    result: Dict[str, str] = {}
    for start in range(0, len(codes), _IN_CLAUSE_BATCH_SIZE):
        raise_if_cancelled(cancel_checker)
        batch = codes[start:start + _IN_CLAUSE_BATCH_SIZE]
        placeholders = ", ".join(["%s"] * len(batch))
        sql = f"""
            SELECT cInvCode, cInvName
            FROM Inventory
            WHERE cInvCode IN ({placeholders})
        """
        try:
            rows = _query_with_deadlock_retry(
                client,
                sql,
                log_label="root_inv_names",
                cancel_checker=cancel_checker,
                params=batch,
            )
            for row in rows:
                code = str(row.get("cInvCode") or "").strip()
                name = str(row.get("cInvName") or "").strip()
                if code and name:
                    result[code] = name
        except Exception as exc:
            logger.warning(
                "根父件名称查询失败 (batch=%s..%s): %s",
                start, start + len(batch), exc,
            )
            continue

    # 找不到名称的编码，使用编码本身作为名称
    for code in codes:
        if code not in result:
            result[code] = code

    return result


def _fill_inventory_only_rows(
    result_rows: List[Dict[str, Any]],
    client: Any,
    no_bom_codes: List[str],
    root_name_map: Dict[str, str],
    cancel_checker: Optional[Callable[[], bool]] = None,
) -> None:
    """For codes without BOM children (purchased/leaf items), query Inventory
    directly and append a single self-referencing row per code.

    This handles items like 铭牌 (108xxx), 标准件 (104xxx), etc. that are
    purchased parts with no BOM structure — they only exist in Inventory.
    """
    if not no_bom_codes:
        return

    # Map code -> inventory row
    inv_by_code: Dict[str, Dict[str, Any]] = {}
    for start in range(0, len(no_bom_codes), _IN_CLAUSE_BATCH_SIZE):
        raise_if_cancelled(cancel_checker)
        batch = no_bom_codes[start:start + _IN_CLAUSE_BATCH_SIZE]
        placeholders = ", ".join(["%s"] * len(batch))
        sql = f"""
            SELECT
                cInvCode,
                cInvName,
                iInvSprice,
                iInvNcost,
                cInvStd,
                cInvDepCode,
                cDefWareHouse,
                bForeExpland,
                iSupplyType
            FROM Inventory
            WHERE cInvCode IN ({placeholders})
        """
        try:
            inv_rows = _query_with_deadlock_retry(
                client,
                sql,
                log_label="inventory_only_rows",
                cancel_checker=cancel_checker,
                params=batch,
            )
        except Exception as exc:
            logger.warning(
                "Inventory 采购件回退查询失败 (batch=%s..%s): %s",
                start, start + len(batch), exc,
            )
            continue

        for row in inv_rows:
            code = str(row.get("cInvCode") or "").strip()
            if code:
                inv_by_code[code] = row

    filled_count = 0
    for code in no_bom_codes:
        inv = inv_by_code.get(code)
        if not inv:
            logger.info("U8 采购件回退: 编码 %s 在 Inventory 中无记录，保留空行", code)
            result_rows.append(
                {
                    "ROOT_INV_CODE": code,
                    "ROOT_INV_NAME": root_name_map.get(code, code),
                    "ParentInvCode": code,
                    "ChildInvCode": code,
                    "CHINANAME": None,
                    "BomId": None,
                    "SortSeq": 1,
                    "BaseQtyN": 1,
                    "BaseQtyD": 1,
                    "COUNTS": 1.0,
                    "CUM_QTY": 1.0,
                    "LEVEL": 1,
                    "CompScrap": None,
                    "cInvCode": code,
                    "HAS_INVENTORY": "NO",
                    "iInvSprice": None,
                    "iInvNcost": None,
                    "cInvStd": None,
                    "cInvDepCode": None,
                    "cDefWareHouse": None,
                    "bForeExpland": None,
                    "iSupplyType": None,
                    "TOTAL_PRICE": None,
                }
            )
            filled_count += 1
            continue

        inv_cost = inv.get("iInvNcost")
        inv_cost_num = to_float_local(inv_cost, 0.0) if inv_cost is not None else None
        total_price = inv_cost_num if inv_cost_num is not None else None

        result_rows.append(
            {
                "ROOT_INV_CODE": code,
                "ROOT_INV_NAME": root_name_map.get(code, code),
                "ParentInvCode": code,
                "ChildInvCode": code,
                "CHINANAME": inv.get("cInvName"),
                "BomId": None,
                "SortSeq": 1,
                "BaseQtyN": 1,
                "BaseQtyD": 1,
                "COUNTS": 1.0,
                "CUM_QTY": 1.0,
                "LEVEL": 1,
                "CompScrap": None,
                "cInvCode": code,
                "HAS_INVENTORY": "YES",
                "iInvSprice": inv.get("iInvSprice"),
                "iInvNcost": inv.get("iInvNcost"),
                "cInvStd": inv.get("cInvStd"),
                "cInvDepCode": inv.get("cInvDepCode"),
                "cDefWareHouse": inv.get("cDefWareHouse"),
                "bForeExpland": inv.get("bForeExpland"),
                "iSupplyType": inv.get("iSupplyType"),
                "TOTAL_PRICE": total_price,
            }
        )
        filled_count += 1

    if filled_count > 0:
        logger.info(
            "U8 采购件回退完成: filled=%s/%s codes",
            filled_count,
            len(no_bom_codes),
        )


def to_float_local(value: Any, default: float = 0.0) -> float:
    """Local to_float for use outside _query_u8_bom_inventory closure."""
    try:
        return float(value)
    except Exception:
        return default


_BOM_FETCH_CHILDREN_SQL = """
    ;WITH PartMap AS (
        SELECT
            vp.PartId,
            COALESCE(
                NULLIF(LTRIM(RTRIM(vp.InvCode)), ''),
                NULLIF(LTRIM(RTRIM(vp.cInvCode)), '')
            ) AS PartInvCode,
            ROW_NUMBER() OVER (
                PARTITION BY COALESCE(
                    NULLIF(LTRIM(RTRIM(vp.InvCode)), ''),
                    NULLIF(LTRIM(RTRIM(vp.cInvCode)), '')
                )
                ORDER BY vp.PartId
            ) AS part_rn
        FROM v_bas_part vp
    ),
    LatestBom AS (
        SELECT
            bp.ParentId,
            bp.BomId,
            b.ModifyDate,
            b.ModifyTime,
            ROW_NUMBER() OVER (
                PARTITION BY bp.ParentId
                ORDER BY b.ModifyDate DESC, b.ModifyTime DESC, b.BomId DESC
            ) AS bom_rn
        FROM bom_parent bp
        JOIN bom_bom b ON b.BomId = bp.BomId AND b.Status = 3
    )
    SELECT
        parent.PartInvCode AS ParentInvCode,
        child.PartInvCode AS ChildInvCode,
        lb.BomId,
        lb.ModifyDate,
        lb.ModifyTime,
        oc.SortSeq,
        oc.BaseQtyN,
        oc.BaseQtyD,
        oc.CompScrap,
        CAST(1.0 * oc.BaseQtyN / NULLIF(oc.BaseQtyD, 0) AS DECIMAL(38,12)) AS QtyPer,
        ic.cInvName,
        ic.iInvSprice,
        ic.iInvNcost,
        ic.cInvStd,
        ic.cInvDepCode,
        ic.cDefWareHouse,
        ic.bForeExpland,
        ic.iSupplyType,
        child.PartId AS ChildPartId
    FROM PartMap parent
    JOIN LatestBom lb ON lb.ParentId = parent.PartId AND lb.bom_rn = 1
    JOIN bom_opcomponent oc ON oc.BomId = lb.BomId
    JOIN PartMap child ON child.PartId = oc.ComponentId AND child.part_rn = 1
        LEFT JOIN Inventory ic ON ic.cInvCode = child.PartInvCode
        WHERE parent.PartInvCode = %s AND parent.part_rn = 1
    ORDER BY oc.SortSeq, child.PartInvCode
"""

_PARTMAP_PROBE_SQL = """
    SELECT
        SUM(CASE WHEN COALESCE(
            NULLIF(LTRIM(RTRIM(vp.InvCode)), ''),
            NULLIF(LTRIM(RTRIM(vp.cInvCode)), '')
        ) = %s THEN 1 ELSE 0 END) AS InvCodeHits,
        SUM(CASE WHEN CAST(vp.PartId AS NVARCHAR(100)) = %s THEN 1 ELSE 0 END) AS PartIdHits
    FROM v_bas_part vp
"""


def _probe_partmap_hits(
    client: Any,
    code: str,
    cancel_checker: Optional[Callable[[], bool]] = None,
) -> Dict[str, Optional[int]]:
    try:
        rows = _query_with_deadlock_retry(
            client,
            _PARTMAP_PROBE_SQL,
            log_label=f"partmap_probe:{code}",
            cancel_checker=cancel_checker,
            params=(code, code),
        )
        first = rows[0] if rows else {}
        inv_hits_raw = first.get("InvCodeHits") if isinstance(first, dict) else None
        part_hits_raw = first.get("PartIdHits") if isinstance(first, dict) else None
        return {
            "inv_code_hits": int(inv_hits_raw) if inv_hits_raw is not None else None,
            "part_id_hits": int(part_hits_raw) if part_hits_raw is not None else None,
        }
    except Exception as exc:
        logger.warning("U8 PartMap 命中探针失败: code=%s, error=%s", code, exc)
        return {"inv_code_hits": None, "part_id_hits": None}


def _fetch_children_cached(
    client: Any,
    cache: "SharedChildrenCache",
    parent_inv_code: str,
    root_codes_set: set[str],
    cancel_checker: Optional[Callable[[], bool]] = None,
    diag: Optional["_DiagCounters"] = None,
) -> List[Dict[str, Any]]:
    """Fetch BOM children for one parent code, using a shared thread-safe cache.

    The SQL fetch runs OUTSIDE the cache lock: ``SharedChildrenCache.get_or_fetch``
    registers a per-key ``threading.Event`` in a short critical section, then the
    first fetcher runs the query on its own pooled connection while same-code
    waiters block on ``event.wait()`` (not on the lock). Distinct parent codes
    proceed in parallel. The result is shared globally without re-querying; on
    failure the exception is cached (short negative-TTL) so woken waiters and
    fresh callers re-raise instead of re-querying.
    """

    def _do_fetch() -> List[Dict[str, Any]]:
        raise_if_cancelled(cancel_checker)
        _sql_start = time.time()
        rows = _query_with_deadlock_retry(
            client,
            _BOM_FETCH_CHILDREN_SQL,
            log_label=f"fetch_children:{parent_inv_code}",
            cancel_checker=cancel_checker,
            params=(parent_inv_code,),
        )
        _sql_elapsed = time.time() - _sql_start
        if diag is not None:
            diag.record_sql(_sql_elapsed)
        if not rows and parent_inv_code in root_codes_set:
            probe = _probe_partmap_hits(client, parent_inv_code, cancel_checker)
            logger.warning(
                "U8 根节点无子件: parent_code=%s, inv_code_hits=%s, part_id_hits=%s",
                parent_inv_code,
                probe.get("inv_code_hits"),
                probe.get("part_id_hits"),
            )
        return rows

    _py_start = time.time()
    result = cache.get_or_fetch(parent_inv_code, _do_fetch, cancel_checker)
    if diag is not None:
        diag.record_python(time.time() - _py_start)
    return result


def _walk_bom_subtree(
    *,
    client: Any,
    cache: "SharedChildrenCache",
    root_code: str,
    root_name: str,
    parent_code: str,
    level: int,
    max_depth: int,
    cumulative_qty: float,
    root_codes_set: set[str],
    cancel_checker: Optional[Callable[[], bool]] = None,
    diag: Optional["_DiagCounters"] = None,
) -> List[Dict[str, Any]]:
    """Depth-first walk of one BOM subtree; returns rows for this subtree only.

    Uses the provided ``client`` (a pooled connection wrapper) and a shared
    thread-safe ``cache`` so that concurrent workers never share a pymssql
    connection but do share BOM children results across roots.
    """
    if level > max_depth:
        return []

    children = _fetch_children_cached(
        client,
        cache,
        parent_code,
        root_codes_set,
        cancel_checker,
        diag,
    )
    if not children:
        return []

    result_rows: List[Dict[str, Any]] = []

    for child in children:
        child_inv_code = str(child.get("ChildInvCode") or "").strip()

        qty_per = to_float_local(child.get("QtyPer"), 0.0)
        child_cumulative_qty = cumulative_qty * qty_per
        inv_cost = child.get("iInvNcost")
        inv_cost_num = to_float_local(inv_cost, 0.0) if inv_cost is not None else None
        total_price = child_cumulative_qty * inv_cost_num if inv_cost_num is not None else None

        result_rows.append(
            {
                "ROOT_INV_CODE": root_code,
                "ROOT_INV_NAME": root_name,
                "ParentInvCode": parent_code,
                "ChildInvCode": child_inv_code,
                "CHINANAME": child.get("cInvName"),
                "BomId": child.get("BomId"),
                "SortSeq": child.get("SortSeq"),
                "BaseQtyN": child.get("BaseQtyN"),
                "BaseQtyD": child.get("BaseQtyD"),
                "COUNTS": qty_per,
                "CUM_QTY": child_cumulative_qty,
                "LEVEL": level,
                "CompScrap": child.get("CompScrap"),
                "cInvCode": child_inv_code or None,
                "HAS_INVENTORY": "YES" if child.get("cInvName") else "NO",
                "iInvSprice": child.get("iInvSprice"),
                "iInvNcost": child.get("iInvNcost"),
                "cInvStd": child.get("cInvStd"),
                "cInvDepCode": child.get("cInvDepCode"),
                "cDefWareHouse": child.get("cDefWareHouse"),
                "bForeExpland": child.get("bForeExpland"),
                "iSupplyType": child.get("iSupplyType"),
                "TOTAL_PRICE": total_price,
            }
        )

        # 4/7 开头的编码不再继续向下展开
        if child_inv_code and child_inv_code.startswith(("4", "7")):
            continue

        if child_inv_code:
            result_rows.extend(
                _walk_bom_subtree(
                    client=client,
                    cache=cache,
                    root_code=root_code,
                    root_name=root_name,
                    parent_code=child_inv_code,
                    level=level + 1,
                    max_depth=max_depth,
                    cumulative_qty=child_cumulative_qty,
                    root_codes_set=root_codes_set,
                    cancel_checker=cancel_checker,
                    diag=diag,
                )
            )

    return result_rows


def _walk_one_root(
    root_code: str,
    root_name: str,
    max_depth: int,
    root_codes_set: set[str],
    cancel_checker: Optional[Callable[[], bool]],
    pool: "PymssqlConnectionPool",
    shared_cache: "SharedChildrenCache",
    diag: Optional["_DiagCounters"] = None,
) -> tuple[List[Dict[str, Any]], bool]:
    """Walk one root code's BOM subtree using a dedicated pooled connection.

    Shares ``shared_cache`` across all roots so common subassemblies are
    fetched only once. Returns ``(rows, has_no_bom_children)``.
    """
    raise_if_cancelled(cancel_checker)
    if diag is not None:
        diag.thread_enter()
    try:
        with pooled_client(pool, cancel_checker=cancel_checker) as client:
            rows = _walk_bom_subtree(
                client=client,
                cache=shared_cache,
                root_code=root_code,
                root_name=root_name,
                parent_code=root_code,
                level=1,
                max_depth=max_depth,
                cumulative_qty=1.0,
                root_codes_set=root_codes_set,
                cancel_checker=cancel_checker,
                diag=diag,
            )
        return rows, len(rows) == 0
    finally:
        if diag is not None:
            diag.thread_exit()


@dataclass
class U8BomQueryResult:
    """U8 BOM 查询结果 + 被跳过的失败根编码（部分失败可见性）。"""

    rows: List[Dict[str, Any]]
    failed_root_codes: List[str] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        return bool(self.failed_root_codes)


def _query_u8_bom_inventory(
    parent_codes: List[str],
    max_depth: int,
    cancel_checker: Optional[Callable[[], bool]] = None,
    user_key: Optional[str] = None,
) -> U8BomQueryResult:
    if not parent_codes:
        return U8BomQueryResult(rows=[], failed_root_codes=[])

    parallel_workers = max(1, min(settings.U8_BOM_PARALLEL_WORKERS, len(parent_codes)))

    slot_held = False
    per_user_held = False
    overall_start = time.time()

    try:
        # 先获取每用户槽位（快速拒绝刷爆的用户），再获取全局 BOM 任务槽位。
        # 二者均在 try 内获取、在 finally 释放；等待期间支持取消。
        per_user_held = _acquire_per_user_slot(user_key, cancel_checker)
        _acquire_bom_slot(cancel_checker)
        slot_held = True

        # 共享连接池（进程级单例）：所有 BOM 任务共用，ERP 连接数受池大小硬约束。
        pool = _get_shared_pool()
        root_codes_set = set(parent_codes)
        shared_cache = SharedChildrenCache()
        diag = _DiagCounters()

        # 故障隔离 + 熔断：单根失败时跳过该根继续其余根（任务带部分结果完成）；
        # 连续 _MAX_CONSECUTIVE_ROOT_FAILURES 个根失败则判定系统性故障并中止任务，
        # 避免 ERP 不可用时每个根各自熬满查询超时而放大 DB 负载。串行/并行路径共用。
        # 仅对可恢复的 DB 层故障（超时 20003 / 锁 20047 / 死锁 1205）做隔离，
        # 代码缺陷（KeyError/AttributeError 等）不属可恢复，原样上抛暴露。
        failed_root_codes: List[str] = []
        consecutive_failures = 0

        def _on_root_failure(root_code: str, exc: BaseException) -> bool:
            """记录一个失败的根；返回 True 表示判定系统性故障、应中止任务。"""
            nonlocal consecutive_failures
            consecutive_failures += 1
            failed_root_codes.append(root_code)
            logger.warning(
                "U8 根节点展开失败，跳过: root_code=%s, consecutive=%s/%s, error=%s",
                root_code, consecutive_failures, _MAX_CONSECUTIVE_ROOT_FAILURES, exc,
            )
            if consecutive_failures >= _MAX_CONSECUTIVE_ROOT_FAILURES:
                logger.error(
                    "U8 BOM 连续 %s 个根节点失败，判定系统性故障，中止任务。failed_sample=%s",
                    consecutive_failures, failed_root_codes[:10],
                )
                return True
            return False

        def _on_root_success() -> None:
            nonlocal consecutive_failures
            consecutive_failures = 0

        logger.info(
            "U8 BOM 并行查询开始: root_codes=%s, parallel_workers=%s, max_depth=%s, "
            "user=%s",
            len(parent_codes),
            parallel_workers,
            max_depth,
            user_key or "-",
        )

        # 1. 批量查询所有根父件名称（单连接）
        raise_if_cancelled(cancel_checker)
        with pooled_client(pool, cancel_checker=cancel_checker) as name_client:
            root_name_map = _fetch_root_inv_names(name_client, parent_codes, cancel_checker)

        # 2. 展开每个根编码的 BOM 子树（并行或串行回退）
        # 结果按 parent_codes 输入顺序归并，保证与原串行实现输出顺序一致。
        # 4/7 开头的编码是标准件/外购件，没有 BOM 子件结构，直接归入 no_bom
        # 走 Inventory 回退，避免对 ERP 发起无意义的查询。
        per_root_rows: Dict[str, List[Dict[str, Any]]] = {}
        per_root_no_bom: set[str] = set()

        _LEAF_PREFIXES = ("4", "7")

        if parallel_workers == 1:
            # 串行回退路径（显式禁用并行或仅单个根编码）
            for root_code in parent_codes:
                raise_if_cancelled(cancel_checker)
                root_name = root_name_map.get(root_code, root_code)
                if root_code.startswith(_LEAF_PREFIXES):
                    per_root_no_bom.add(root_code)
                    logger.info(
                        "U8 根节点跳过(4/7标准件/外购件): root_code=%s", root_code,
                    )
                    continue
                try:
                    rows, no_bom = _walk_one_root(
                        root_code, root_name, max_depth, root_codes_set,
                        cancel_checker, pool, shared_cache, diag,
                    )
                except QueryCancelledError:
                    raise
                except Exception as exc:
                    # 仅可恢复的 DB 层故障（超时/锁/死锁）才隔离跳过；
                    # 代码缺陷（KeyError 等）原样上抛，避免被静默掩盖。
                    if not _is_recoverable_root_failure(exc):
                        raise
                    if _on_root_failure(root_code, exc):
                        raise U8RootFailureBreakerError(
                            f"U8 BOM 连续 {consecutive_failures} 个根节点失败，"
                            f"判定系统性故障，中止任务。已跳过根: {failed_root_codes[:10]}",
                            failed_root_codes,
                        ) from exc
                    continue
                _on_root_success()
                per_root_rows[root_code] = rows
                if no_bom:
                    per_root_no_bom.add(root_code)
                logger.info("U8 根节点展开完成: root_code=%s, rows=%s", root_code, len(rows))
        else:
            # 4/7 开头的编码直接归入 no_bom，不提交到线程池
            bom_root_codes: List[str] = []
            for rc in parent_codes:
                if rc.startswith(_LEAF_PREFIXES):
                    per_root_no_bom.add(rc)
                    logger.info(
                        "U8 根节点跳过(4/7标准件/外购件): root_code=%s", rc,
                    )
                else:
                    bom_root_codes.append(rc)

            with ThreadPoolExecutor(
                max_workers=parallel_workers, thread_name_prefix="u8_bom_"
            ) as ex:
                future_map = {
                    ex.submit(
                        _walk_one_root,
                        root_code,
                        root_name_map.get(root_code, root_code),
                        max_depth,
                        root_codes_set,
                        cancel_checker,
                        pool,
                        shared_cache,
                        diag,
                    ): root_code
                    for root_code in bom_root_codes
                }
                for future in as_completed(future_map):
                    root_code = future_map[future]
                    try:
                        rows, no_bom = future.result()
                    except QueryCancelledError:
                        # 取消：取消尚未启动的 future 后向上抛出（已在运行的线程
                        # 会跑到各自的 raise_if_cancelled 才停）
                        for f in future_map:
                            f.cancel()
                        raise
                    except Exception as exc:
                        # 仅可恢复的 DB 层故障（超时/锁/死锁）才隔离跳过；
                        # 代码缺陷（KeyError 等）原样上抛，避免被静默掩盖。
                        if not _is_recoverable_root_failure(exc):
                            for f in future_map:
                                f.cancel()
                            raise
                        # 故障隔离：单根失败不致命，跳过该根继续其余根；
                        # 仅当判定系统性故障（连续失败达上限）时才取消兄弟并上抛。
                        if _on_root_failure(root_code, exc):
                            for f in future_map:
                                f.cancel()
                            raise U8RootFailureBreakerError(
                                f"U8 BOM 连续 {consecutive_failures} 个根节点失败，"
                                f"判定系统性故障，中止任务。已跳过根: {failed_root_codes[:10]}",
                                failed_root_codes,
                            ) from exc
                        continue
                    _on_root_success()
                    per_root_rows[root_code] = rows
                    if no_bom:
                        per_root_no_bom.add(root_code)
                    logger.info("U8 根节点展开完成: root_code=%s, rows=%s", root_code, len(rows))

        # 按输入顺序合并结果，保持与原串行实现一致的输出顺序
        result_rows: List[Dict[str, Any]] = []
        no_bom_root_codes: List[str] = []
        for root_code in parent_codes:
            result_rows.extend(per_root_rows.get(root_code, []))
            if root_code in per_root_no_bom:
                no_bom_root_codes.append(root_code)

        diag.log_summary("BOM 并行展开")

        if failed_root_codes:
            logger.warning(
                "U8 BOM 部分根节点查询失败已跳过: failed=%s/%s, sample=%s",
                len(failed_root_codes), len(parent_codes), failed_root_codes[:10],
            )

        # 3. 无 BOM 子件的根编码 → 直接查 Inventory（单连接批量）
        raise_if_cancelled(cancel_checker)
        with pooled_client(pool, cancel_checker=cancel_checker) as inv_client:
            _fill_inventory_only_rows(
                result_rows, inv_client, no_bom_root_codes, root_name_map,
                cancel_checker,
            )

        # 4. 补充缺失价格（单连接批量）
        raise_if_cancelled(cancel_checker)
        with pooled_client(pool, cancel_checker=cancel_checker) as price_client:
            _supplement_missing_prices(result_rows, price_client, cancel_checker)

        elapsed = time.time() - overall_start
        logger.info(
            "U8 BOM 并行查询完成: root_codes=%s, total_rows=%s, elapsed=%.2fs, "
            "parallel_workers=%s, pool_created=%s",
            len(parent_codes),
            len(result_rows),
            elapsed,
            parallel_workers,
            pool.created_count,
        )
        return U8BomQueryResult(rows=result_rows, failed_root_codes=failed_root_codes)
    finally:
        # 共享连接池不在请求结束时关闭（进程级单例）；连接由 pooled_client 归还。
        if slot_held:
            _release_bom_slot()
            slot_held = False
        if per_user_held:
            _release_per_user_slot(user_key, per_user_held)
            per_user_held = False


_SUPPLY_TYPE_MAP: Dict[int, str] = {
    0: "领用",
    1: "入库倒冲",
    2: "工序倒冲",
    3: "虚拟件",
    4: "直接供应",
}


def _determine_supply_type(row: Dict[str, Any]) -> str:
    """Determine supply type from iSupplyType, falling back to bForeExpland,
    then to iInvNcost-based heuristic.

    Priority:
    1. Inventory.iSupplyType (from U8 AA_Enum: 0=领用, 1=入库倒冲, 2=工序倒冲, 3=虚拟件, 4=直接供应)
    2. Inventory.bForeExpland (1=虚拟件)
    3. iInvNcost heuristic (non-zero → 领用, else → 虚拟件)
    """
    # 1. Try iSupplyType
    iSupplyType = row.get("iSupplyType")
    if iSupplyType is not None:
        try:
            return _SUPPLY_TYPE_MAP[int(iSupplyType)]
        except (ValueError, TypeError, KeyError):
            pass

    # 2. Try bForeExpland
    bForeExpland = row.get("bForeExpland")
    if bForeExpland is not None:
        try:
            return "虚拟件" if int(bForeExpland) == 1 else "领用"
        except (ValueError, TypeError):
            pass

    # 3. Fallback to cost-based heuristic
    return "领用" if _is_material_item_by_cost(row.get("iInvNcost")) else "虚拟件"


def format_u8_output_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map U8 raw rows to the slim fields used for export/save."""

    formatted_rows: List[Dict[str, Any]] = []
    for row in rows:
        supply_type = _determine_supply_type(row)
        formatted_rows.append(
            {
                "子件层级": row.get("LEVEL"),
                "子件名称": row.get("CHINANAME"),
                "根父件名称": row.get("ROOT_INV_NAME"),
                "材料编码（物料编码）": row.get("cInvCode"),
                "累计用量": row.get("CUM_QTY"),
                "供应类型": supply_type,
                "规格型号": row.get("cInvStd"),
                "单价": row.get("iInvNcost"),
                "总价": row.get("TOTAL_PRICE"),
                "__root_inv_code": row.get("ROOT_INV_CODE"),
                "__root_inv_name": row.get("ROOT_INV_NAME"),
                "__parent_inv_code": row.get("ParentInvCode"),
            }
        )

    return formatted_rows


def _is_material_item_by_cost(value: Any) -> bool:
    """Fallback check: item with a non-zero cost price is a real material item."""
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    try:
        return float(text) != 0
    except Exception:
        return False
