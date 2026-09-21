"""关键词 / 字面检索适配器（issue #16 路线 1：pg_trgm + ILIKE）。

为什么不是 tsvector / zhparser（决策记录见 docs/retrieval-hybrid-fulltext.md）：
``zhparser`` 不在 ``pgvector/pgvector:pg16`` 镜像的可用扩展里，且 PG 默认
parser 会把整串 CJK 当成一个 token，``to_tsvector('simple', ...)`` 对中文
等于失效；``pg_trgm`` 只依赖 contrib（现成可用），按字符切 trigram，
恰好覆盖「项目号 V254 / 人名 / 科目名 / 文件编号」这类精确 token。

已知特性：关键词短于 3 个字符时 pg_trgm 无法提取 trigram，索引用不上会退化为
顺序扫描——结果正确、只是慢；当前集合量级（百行）无影响（见文档「后续」）。

失败语义：表不存在（懒建表中间态）返回空列表；其余 DB 异常记 warning 后同样
返回空列表——字面检索路永不阻断 dense 主路（融合层另有兜底）。
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, List, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.adapters.knowledge.collection_tables import is_missing_table, physical_table
from app.domain.retrieval.ranking import (
    DEFAULT_KEYWORD_MAX_LEN,
    DEFAULT_MAX_KEYWORDS,
    normalize_keywords,
)
from app.ports.outbound.retriever import LexicalHit, LexicalSearchPort

logger = get_logger("knowledge.lexical_search")

# ILIKE 模式里的元字符必须转义，否则关键词里的 % / _ 会变成通配符
_LIKE_ESCAPE = "\\"
_INDEX_NAME_SUFFIX = "_text_trgm"
_INDEX_PREFIX = "idx_"

# 集合表判定：public 下 data_% 且同时含 text 与 node_id 两列
# （data_shares 等平台表没有 text 列，天然被排除）
_COLLECTION_TABLES_SQL = text(
    "SELECT t.table_name"
    " FROM information_schema.tables t"
    " WHERE t.table_schema = 'public' AND t.table_name LIKE 'data\\_%'"
    " AND EXISTS (SELECT 1 FROM information_schema.columns c"
    "             WHERE c.table_schema = t.table_schema AND c.table_name = t.table_name"
    "               AND c.column_name = 'text')"
    " AND EXISTS (SELECT 1 FROM information_schema.columns c"
    "             WHERE c.table_schema = t.table_schema AND c.table_name = t.table_name"
    "               AND c.column_name = 'node_id')"
    " ORDER BY t.table_name"
)


def like_pattern(keyword: str) -> str:
    """关键词 → ``%kw%`` 的 ILIKE 模式（转义 ``\\`` ``%`` ``_``）。"""
    escaped = (
        str(keyword)
        .replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


def _parse_metadata(raw: Any) -> dict:
    """``metadata_`` 列（json）→ dict；形状异常一律退回空 dict。"""
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class PostgresLexicalSearcher(LexicalSearchPort):
    """在 ``data_<collection>`` 上按关键词做字面检索（实现 LexicalSearchPort）。

    ``session_factory`` 可注入（测试用假件）；默认 ``AsyncSessionLocal``，
    沿用「每方法一 session」的既有仓储模式。
    """

    def __init__(
        self,
        session_factory: Optional[Callable[[], Any]] = None,
        *,
        max_keywords: int = DEFAULT_MAX_KEYWORDS,
        max_keyword_len: int = DEFAULT_KEYWORD_MAX_LEN,
    ) -> None:
        self._session_factory = session_factory or AsyncSessionLocal
        self._max_keywords = max_keywords
        self._max_keyword_len = max_keyword_len

    async def search(
        self, collection: str, keywords: Sequence[str], top_k: int
    ) -> List[LexicalHit]:
        normalized = normalize_keywords(
            keywords, max_count=self._max_keywords, max_len=self._max_keyword_len
        )
        if not normalized or not top_k or top_k <= 0:
            return []

        table = physical_table(collection)
        params: dict = {f"kw_{i}": like_pattern(kw) for i, kw in enumerate(normalized)}
        params["limit"] = int(top_k)

        hit_expr = " + ".join(
            f"(CASE WHEN text ILIKE :kw_{i} ESCAPE '{_LIKE_ESCAPE}' THEN 1 ELSE 0 END)"
            for i in range(len(normalized))
        )
        where_expr = " OR ".join(
            f"text ILIKE :kw_{i} ESCAPE '{_LIKE_ESCAPE}'" for i in range(len(normalized))
        )
        sql = (
            f"SELECT node_id, text AS content,"
            f" COALESCE(metadata_->>'source', 'Unknown') AS source,"
            f" metadata_::text AS metadata_json,"
            f" ({hit_expr}) AS hits"
            f" FROM {table}"
            f" WHERE ({where_expr}) AND COALESCE(text, '') <> ''"
            f" ORDER BY hits DESC, length(text) ASC, node_id ASC"
            f" LIMIT :limit"
        )

        try:
            async with self._session_factory() as db:
                result = await db.execute(text(sql), params)
                rows = result.fetchall()
        except ProgrammingError as exc:
            if not is_missing_table(exc):
                logger.warning("字面检索失败（集合 %s）: %s", collection, exc)
                return []
            logger.debug("字面检索：集合表尚未创建（懒建表） table=%s", table)
            return []
        except SQLAlchemyError as exc:
            logger.warning("字面检索失败（集合 %s）: %s", collection, exc)
            return []

        hits: List[LexicalHit] = []
        for row in rows:
            content = (row.content or "").strip()
            if not content:
                continue
            hits.append(
                LexicalHit(
                    node_id=str(row.node_id or ""),
                    content=content,
                    source=row.source or "Unknown",
                    metadata=_parse_metadata(getattr(row, "metadata_json", None)),
                    hits=int(row.hits or 0),
                )
            )
        return hits


def _default_engine():
    from app.core.database import engine

    return engine


def _index_name(table: str) -> str:
    return f"{_INDEX_PREFIX}{table}{_INDEX_NAME_SUFFIX}"


def index_ddl(table: str, *, concurrently: bool = False) -> str:
    """集合表的字面检索索引 DDL（幂等）。"""
    return (
        f"CREATE INDEX {'CONCURRENTLY ' if concurrently else ''}IF NOT EXISTS "
        f"{_index_name(table)} ON {table} USING gin (text gin_trgm_ops)"
    )


def _collection_tables(conn) -> List[str]:
    return [row[0] for row in conn.execute(_COLLECTION_TABLES_SQL).fetchall()]


def plan_fulltext_indexes(*, engine=None) -> List[tuple]:
    """只读预演：返回 ``[(table, ddl), ...]``（``--dry-run`` 用）。"""
    eng = engine or _default_engine()
    with eng.connect() as conn:
        return [(table, index_ddl(table)) for table in _collection_tables(conn)]


def ensure_fulltext_indexes(*, engine=None, concurrently: bool = False) -> List[str]:
    """确保 pg_trgm 扩展与各集合表的 GIN 索引存在，返回已就绪的索引名。

    - 仅对 ``data_%`` 且含 text / node_id 列的表建索引（跳过平台表）；
    - 无 superuser / 无 contrib 时只告警并返回空列表：ILIKE 仍能工作，
      只是退化为顺序扫描（功能不降级）；
    - 幂等：``IF NOT EXISTS``，重复调用无副作用；
    - ``concurrently=True`` 走 ``CREATE INDEX CONCURRENTLY``（生产大表用，
      不锁写），需要 AUTOCOMMIT 隔离级别。
    """
    eng = engine or _default_engine()
    ensured: List[str] = []
    try:
        with eng.connect() as raw:
            conn = raw.execution_options(isolation_level="AUTOCOMMIT")
            try:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            except SQLAlchemyError as exc:
                logger.warning(
                    "pg_trgm 扩展不可用（%s）；字面检索退化为顺序扫描，功能不受影响", exc
                )
                return []

            tables = _collection_tables(conn)
            if not tables:
                logger.info("未发现需要建字面检索索引的集合表（data_% 且含 text/node_id）")
                return []

            for table in tables:
                name = _index_name(table)
                started = time.monotonic()
                try:
                    conn.execute(text(index_ddl(table, concurrently=concurrently)))
                except SQLAlchemyError as exc:
                    logger.warning("建字面检索索引失败 %s: %s", name, exc)
                    continue
                ensured.append(name)
                logger.info(
                    "字面检索索引就绪: %s（%.2fs，concurrently=%s）",
                    name,
                    time.monotonic() - started,
                    concurrently,
                )
    except SQLAlchemyError as exc:
        logger.warning("字面检索索引检查失败（不影响启动）: %s", exc)
        return ensured
    return ensured
