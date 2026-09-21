"""chunk 内容指纹的 SQL 侧：写入前预检 + 存量对账（issue #17）。

本模块是 ``md5(text)`` 查询与 DDL 的**单一出处**：

- 写入端 ``VectorStoreManager.existing_fingerprints`` → 入库前判重（跳过重复块）；
- 运维脚本 ``scripts/dedupe_chunks.py`` → 存量重复对账与清理、指纹索引保障。

为什么用 PG 侧现算的 ``md5(text)``，而不是读 ``metadata_->>'content_hash'``：
存量 chunk 没有该键，PG 现算能**一次性覆盖新旧数据**，无需回填、无需唯一索引。
``content_hash`` metadata 仍然写（便于对账与将来检索端兜底），但它不是判重依据。

索引备注：``{table}_text_md5_idx`` 是**普通（非唯一）**表达式索引。
不能建唯一索引——llama-index ``PGVectorStore.add()`` 是裸 ``session.add()``
（0.7.1，无 ``ON CONFLICT``/upsert），唯一冲突会让整批 insert 回滚，比重复更糟。
无索引时预检退化为顺序扫描，功能不受影响。

并发备注：唯一索引既然不能建，check-then-insert 就有竞态窗口，用
:func:`fingerprint_write_guard`（session 级 advisory lock）把「预检 + 写入」
串行化来消除——写入端必须把两者都放进该守卫内。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterable, Iterator, List, Sequence

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

from app.core.logging import get_logger
from app.adapters.knowledge.collection_tables import is_missing_table, physical_table
from app.domain.knowledge.chunk_identity import FINGERPRINT_PREFIX, fingerprint_digest

logger = get_logger("knowledge.chunk_fingerprint")

#: 单条 SQL 里 ``ANY(:digests)`` 的最大元素数（大文件一次几千块时避免超长参数）
_MAX_DIGESTS_PER_QUERY = 1000

#: 指纹表达式索引后缀（非唯一！理由见模块 docstring）
INDEX_SUFFIX = "_text_md5_idx"

#: 「预检 + 写入」临界区的最长等锁时间（秒）；超时降级为不持锁写入
LOCK_WAIT_TIMEOUT_SEC = 60.0

#: 等锁轮询间隔（advisory lock 用 try 版本，需自己轮询）
LOCK_POLL_INTERVAL_SEC = 0.2


@dataclass(frozen=True)
class DuplicateGroup:
    """一个「内容完全相同」的重复组（``digest`` 相同的一组行）。"""

    digest: str
    ids: List[int]
    sources: List[str]
    count: int


@dataclass(frozen=True)
class DuplicatePlan:
    """只读预演结果：整表重复盘子 + 前 ``limit`` 组明细。"""

    collection: str
    table: str
    total_rows: int
    group_count: int
    redundant_rows: int
    groups: List[DuplicateGroup] = field(default_factory=list)

    @property
    def has_duplicates(self) -> bool:
        return self.redundant_rows > 0


def _default_engine():
    from app.core.database import engine

    return engine


def fingerprint_index_name(collection: str) -> str:
    """集合的指纹索引名（``data_knowledge_chunks_text_md5_idx``，< 63 字符）。"""
    return f"{physical_table(collection)}{INDEX_SUFFIX}"


def plan_fingerprint_index(collection: str, *, concurrently: bool = False) -> str:
    """指纹索引 DDL（幂等，非唯一）。"""
    table = physical_table(collection)
    return (
        f"CREATE INDEX {'CONCURRENTLY ' if concurrently else ''}IF NOT EXISTS "
        f"{table}{INDEX_SUFFIX} ON {table} (md5(text))"
    )


# ---------------------------------------------------------------------------
# 写入端预检
# ---------------------------------------------------------------------------


def _lock_sql(function: str):
    """advisory lock 语句（``TextClause``；**必须**是 text() 而非裸字符串）。

    锁键用 ``hashtext(物理表名)``：同集合内互斥，不同集合互不阻塞。
    ``hashtext`` 是 32 位哈希，不同集合理论上可能撞键——后果只是多等一次锁，
    不会误判重复，故不做额外映射。
    """
    return text(f"SELECT {function}(hashtext(:key)::bigint) AS locked")


def _acquire_lock(conn, table: str, *, timeout_sec: float, poll_interval_sec: float) -> bool:
    """``pg_try_advisory_lock`` 轮询取锁；超时返回 False（不抛异常）。"""
    deadline = time.monotonic() + max(float(timeout_sec), 0.0)
    while True:
        row = conn.execute(_lock_sql("pg_try_advisory_lock"), {"key": table}).fetchone()
        if bool(getattr(row, "locked", False)):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(float(poll_interval_sec), 0.0))


@contextmanager
def fingerprint_write_guard(
    collection: str,
    *,
    engine=None,
    timeout_sec: float = LOCK_WAIT_TIMEOUT_SEC,
    poll_interval_sec: float = LOCK_POLL_INTERVAL_SEC,
) -> Iterator[bool]:
    """把「指纹预检 + 写入」变成同一集合内的临界区（issue #17 并发竞态）。

    并发上传同一内容时，两个任务的预检都可能早于对方插入，于是都判定「无重复」
    而双写——2026-09-21 E2E 实测复现（两任务间隔约 1s，日志：A 预检 45.506s 返回
    空 → B 插入 45.586s → A 插入 45.855s）。本守卫用 **session 级 advisory lock**
    覆盖「预检 + 写入」，从根上消除该窗口。

    承重语义（别顺手改）：

    - **拿不到锁不阻断写入**：超时或取锁报错 → WARNING + 不持锁继续（退化为
      「尽力去重」）。P0 修复不该给上传引入新的失败面；
    - **锁随连接释放**：进程崩溃/被杀时 PG 自动回收，不会留下死锁；正常路径在
      ``finally`` 里显式解锁，避免连接被连接池复用时带着锁；
    - **锁粒度为集合物理表**：knowledge / excel 两个集合互不阻塞；代价是同一集合
      的并发上传排队（嵌入 HTTP 也在临界区内）。

    产量 ``bool``：是否真正持锁（仅供调用方观测/日志，不影响写入）。
    """
    table = physical_table(collection)
    eng = engine or _default_engine()

    conn = None
    try:
        conn = eng.connect()
        acquired = _acquire_lock(
            conn, table, timeout_sec=timeout_sec, poll_interval_sec=poll_interval_sec
        )
    except SQLAlchemyError as exc:
        logger.warning("指纹写锁不可用，降级为不持锁写入: table=%s err=%s", table, exc)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - 关连接失败不影响主流程
                pass
        yield False
        return

    if not acquired:
        logger.warning(
            "等待指纹写锁超时（%.0fs），本次降级为不持锁写入: table=%s", timeout_sec, table
        )
    try:
        yield acquired
    finally:
        try:
            if acquired:
                conn.execute(_lock_sql("pg_advisory_unlock"), {"key": table})
        except SQLAlchemyError as exc:
            logger.warning(
                "释放指纹写锁失败（连接关闭时 PG 会自动释放）: table=%s err=%s", table, exc
            )
        finally:
            conn.close()


def existing_fingerprints(
    collection: str,
    fingerprints: Iterable[str],
    *,
    engine=None,
) -> set[str]:
    """返回入参中**已存在于该集合**的指纹（带 ``FINGERPRINT_PREFIX``）。

    懒建表容错：集合表尚未创建（首次 upsert 才建表）→ 空集。
    其他 SQL 异常**向上抛**，由调用方决定是否降级（写入端选择降级为仅批内去重）。
    """
    digests: List[str] = []
    seen: set[str] = set()
    for fingerprint in fingerprints:
        digest = fingerprint_digest(fingerprint)
        if digest is None or digest in seen:
            continue
        seen.add(digest)
        digests.append(digest)
    if not digests:
        return set()

    table = physical_table(collection)
    eng = engine or _default_engine()
    found: set[str] = set()
    try:
        with eng.connect() as conn:
            for start in range(0, len(digests), _MAX_DIGESTS_PER_QUERY):
                batch = digests[start : start + _MAX_DIGESTS_PER_QUERY]
                rows = conn.execute(
                    text(
                        f"SELECT DISTINCT md5(text) AS digest FROM {table}"
                        f" WHERE md5(text) = ANY(:digests)"
                    ),
                    {"digests": batch},
                ).fetchall()
                found.update(str(row.digest) for row in rows if row.digest)
    except ProgrammingError as exc:
        if not is_missing_table(exc):
            raise
        logger.debug(
            "指纹预检：集合表尚未创建（懒建表），视为无重复 collection=%s table=%s",
            collection,
            table,
        )
        return set()
    return {f"{FINGERPRINT_PREFIX}{digest}" for digest in found}


# ---------------------------------------------------------------------------
# 存量对账（dry-run 与清理）
# ---------------------------------------------------------------------------


def plan_duplicate_groups(
    collection: str,
    *,
    engine=None,
    limit: int = 200,
) -> DuplicatePlan:
    """只读预演：统计重复盘子并取前 ``limit`` 组明细（不写库）。

    - ``group_count`` / ``redundant_rows`` 是**全量**统计（不受 limit 影响）；
    - ``groups`` 仅前 ``limit`` 组，用于报告；实际删除按全量执行（见
      :func:`delete_duplicate_rows`）。
    """
    table = physical_table(collection)
    eng = engine or _default_engine()

    summary_sql = text(
        f"SELECT count(*) AS total_rows,"
        f" count(*) FILTER (WHERE n > 1) AS group_count,"
        f" COALESCE(sum(n - 1) FILTER (WHERE n > 1), 0) AS redundant_rows"
        f" FROM (SELECT count(*) AS n FROM {table} GROUP BY md5(text)) grouped"
    )
    detail_sql = text(
        f"SELECT md5(text) AS digest, array_agg(id ORDER BY id) AS ids,"
        f" count(*) AS n,"
        f" array_agg(DISTINCT COALESCE(metadata_->>'file_name', metadata_->>'source', ''))"
        f" AS sources"
        f" FROM {table} GROUP BY md5(text) HAVING count(*) > 1"
        f" ORDER BY n DESC, digest LIMIT :limit"
    )

    try:
        with eng.connect() as conn:
            summary = conn.execute(summary_sql).fetchone()
            rows = conn.execute(detail_sql, {"limit": int(limit)}).fetchall()
    except ProgrammingError as exc:
        if not is_missing_table(exc):
            raise
        logger.debug("重复对账：集合表尚未创建 collection=%s table=%s", collection, table)
        return DuplicatePlan(collection, table, 0, 0, 0, [])

    return DuplicatePlan(
        collection=collection,
        table=table,
        total_rows=int(getattr(summary, "total_rows", 0) or 0),
        group_count=int(getattr(summary, "group_count", 0) or 0),
        redundant_rows=int(getattr(summary, "redundant_rows", 0) or 0),
        groups=[
            DuplicateGroup(
                digest=str(row.digest),
                ids=[int(item) for item in (row.ids or [])],
                sources=[str(item) for item in (row.sources or []) if item],
                count=int(row.n),
            )
            for row in rows
        ],
    )


def delete_duplicate_rows(
    collection: str,
    *,
    engine=None,
    batch_size: int = 1000,
) -> int:
    """每个重复组保留 ``MIN(id)``（最早入库的那条），删除其余；返回删除行数。

    单事务执行，避免删一半留下「部分组已清、部分未清」的中间态。
    集合表不存在时返回 0（懒建表）。
    """
    table = physical_table(collection)
    eng = engine or _default_engine()
    deleted = 0
    try:
        with eng.begin() as conn:
            rows = conn.execute(
                text(
                    f"SELECT id FROM ("
                    f" SELECT id, row_number() OVER (PARTITION BY md5(text) ORDER BY id) AS rn"
                    f" FROM {table}"
                    f") ranked WHERE rn > 1"
                )
            ).fetchall()
            ids = [int(row.id) for row in rows]
            for start in range(0, len(ids), max(int(batch_size), 1)):
                batch = ids[start : start + max(int(batch_size), 1)]
                result = conn.execute(
                    text(f"DELETE FROM {table} WHERE id = ANY(:ids)"), {"ids": batch}
                )
                deleted += int(result.rowcount or 0)
    except ProgrammingError as exc:
        if not is_missing_table(exc):
            raise
        logger.debug("清理重复块：集合表尚未创建 collection=%s table=%s", collection, table)
        return 0
    return deleted


def count_empty_chunks(collection: str, *, engine=None) -> int:
    """空 / 纯空白 chunk 数（检索端早已丢弃它们，属可清理垃圾行）。"""
    table = physical_table(collection)
    eng = engine or _default_engine()
    try:
        with eng.connect() as conn:
            row = conn.execute(
                text(f"SELECT count(*) AS n FROM {table} WHERE btrim(text) = ''")
            ).fetchone()
    except ProgrammingError as exc:
        if not is_missing_table(exc):
            raise
        return 0
    return int(getattr(row, "n", 0) or 0)


def purge_empty_chunks(collection: str, *, engine=None) -> int:
    """删除空 / 纯空白 chunk；返回删除行数。"""
    table = physical_table(collection)
    eng = engine or _default_engine()
    try:
        with eng.begin() as conn:
            result = conn.execute(text(f"DELETE FROM {table} WHERE btrim(text) = ''"))
    except ProgrammingError as exc:
        if not is_missing_table(exc):
            raise
        return 0
    return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# 索引保障
# ---------------------------------------------------------------------------


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(text("SELECT to_regclass(:table) AS reg"), {"table": table}).fetchone()
    return bool(getattr(row, "reg", None))


def ensure_fingerprint_indexes(
    collections: Sequence[str],
    *,
    engine=None,
    concurrently: bool = False,
) -> List[str]:
    """确保各集合表的 ``md5(text)`` 表达式索引存在，返回已就绪的索引名。

    - 懒建表：集合表还不存在 → 跳过（INFO），不算失败；
    - 幂等：``CREATE INDEX IF NOT EXISTS``，重复调用无副作用；
    - ``concurrently=True`` 走 ``CREATE INDEX CONCURRENTLY``（生产大表用，不锁写），
      需要 AUTOCOMMIT 隔离级别；
    - 建索引失败（权限/锁）只告警并继续：预检退化为顺序扫描，功能不受影响。
    """
    eng = engine or _default_engine()
    ensured: List[str] = []
    for collection in collections:
        table = physical_table(collection)
        ddl = plan_fingerprint_index(collection, concurrently=concurrently)
        try:
            with eng.connect() as raw:
                # DDL 必须走 AUTOCOMMIT：SQLAlchemy 2.0 的 Connection.close() 会回滚
                # 未提交的事务，普通模式下建索引会静默不生效（CONCURRENTLY 更是
                # 不允许出现在事务块里）。与 lexical_search.ensure_fulltext_indexes 一致。
                conn = raw.execution_options(isolation_level="AUTOCOMMIT")
                if not _table_exists(conn, table):
                    logger.info("跳过指纹索引（集合表尚未创建）: %s", table)
                    continue
                conn.execute(text(ddl))
        except SQLAlchemyError as exc:
            logger.warning("建指纹索引失败 %s: %s", table, exc)
            continue
        ensured.append(fingerprint_index_name(collection))
        logger.info("指纹索引就绪: %s", fingerprint_index_name(collection))
    return ensured
