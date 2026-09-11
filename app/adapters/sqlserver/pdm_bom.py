"""PDM BOM_027 query helpers.

All user-supplied text (keywords, model codes) is passed as pymssql parameters
(`%s` placeholders); LIKE wildcards (`%`, `_`) and SQL Server character classes
(`[a-zA-Z]`) are concatenated into the parameter *value*, not the SQL text.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.logging import get_logger

from app.adapters.sqlserver.client import get_sql_client

logger = get_logger("database.sqlserver.pdm_bom")

# 默认排除的关键词（停用、禁用等）
_DEFAULT_EXCLUDE_KEYWORDS = ["停用", "暂停使用", "禁用", "作废", "废弃"]


def build_pdm_exclude_clauses() -> Tuple[List[str], List[str]]:
    """Build default exclude clauses for CHINANAME."""
    clauses: List[str] = []
    params: List[str] = []
    for exclude_kw in _DEFAULT_EXCLUDE_KEYWORDS:
        clauses.append("a.CHINANAME NOT LIKE %s")
        params.append(f"%{exclude_kw}%")
    return clauses, params


def build_model_filter_clauses(model: Optional[str] = None) -> Tuple[List[str], List[str]]:
    """Build MODEL filter clauses with exact match logic.

    - a.MODEL LIKE '%{model}%'                 — 包含 model
    - a.MODEL NOT LIKE '%{model}[a-zA-Z]%'     — 排除 model 后紧跟字母的情况
      （SQL Server LIKE 字符类语法，防止 "ADW-A-0314S" 误匹配到 "ADW-A-0314SX"）

    Args:
        model: 机型型号，如 "ADW-A-0314S"

    Returns:
        (clauses, params): SQL fragments with `%s` placeholders and matching param values.
        LIKE wildcards/character classes live in the param value, not the SQL text.
    """
    if not model:
        return [], []

    clauses: List[str] = []
    params: List[str] = []
    clauses.append("a.MODEL LIKE %s")
    params.append(f"%{model}%")
    clauses.append("a.MODEL NOT LIKE %s")
    params.append(f"%{model}[a-zA-Z]%")
    return clauses, params


def build_pdm_where_clauses(condition: str | List[str]) -> Tuple[List[str], List[str]]:
    """Build LIKE clauses; user text is passed as parameters."""
    clauses, params = build_pdm_exclude_clauses()

    if isinstance(condition, str):
        text = condition.strip()
        if text:
            negated = text.startswith("!")
            payload = text[1:].strip() if negated else text
            if payload:
                if negated:
                    clauses.append("a.CHINANAME NOT LIKE %s")
                else:
                    clauses.append("a.CHINANAME LIKE %s")
                params.append(f"%{payload}%")
        return clauses, params

    for item in condition:
        text = str(item).strip()
        if not text:
            continue
        negated = text.startswith("!")
        payload = text[1:].strip() if negated else text
        if not payload:
            continue
        if negated:
            clauses.append("a.CHINANAME NOT LIKE %s")
        else:
            clauses.append("a.CHINANAME LIKE %s")
        params.append(f"%{payload}%")

    return clauses, params


def deduplicate_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate by (CHINANAME, PARTID), keep first."""
    unique_rows: List[Dict[str, Any]] = []
    seen: set = set()

    for row in rows:
        chinaname = str(row.get("CHINANAME") or "").strip()
        partid = str(row.get("PARTID") or "").strip()
        key = (chinaname, partid)
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(row)

    return unique_rows


def deduplicate_pdm_result_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate merged PDM query results."""
    return deduplicate_rows(rows)


def _pdm_client_config() -> dict:
    return {
        "backend": "pymssql",
        "server": settings.PDM_SQLSERVER_HOST,
        "port": settings.PDM_SQLSERVER_PORT,
        "database": settings.PDM_SQLSERVER_DATABASE,
        "username": settings.PDM_SQLSERVER_USER,
        "password": settings.PDM_SQLSERVER_PASSWORD,
        "encrypt": settings.PDM_SQLSERVER_ENCRYPT,
    }


def build_pdm_and_where_clause(
    alternatives_per_keyword: List[List[str]],
    model: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """Merged WHERE fragment: OR positive-only candidates, AND if any candidate is negated.

    Args:
        alternatives_per_keyword: 关键词列表，每组内的关键词用 OR 连接
        model: 机型型号，用于精确匹配 MODEL 字段

    Returns:
        (where_sql, params): WHERE fragment with `%s` placeholders and matching params.
    """
    # 先添加默认排除条件
    outer_parts, params = build_pdm_exclude_clauses()

    # 添加 MODEL 过滤条件
    model_clauses, model_params = build_model_filter_clauses(model)
    outer_parts.extend(model_clauses)
    params.extend(model_params)

    for alts in alternatives_per_keyword:
        inner: List[str] = []
        inner_params: List[str] = []
        has_negated = False
        seen_inner: set = set()
        for candidate in alts:
            text = str(candidate).strip()
            if not text:
                continue
            negated = text.startswith("!")
            has_negated = has_negated or negated
            payload = text[1:].strip() if negated else text
            if not payload:
                continue
            clause = "a.CHINANAME NOT LIKE %s" if negated else "a.CHINANAME LIKE %s"
            if clause in seen_inner:
                continue
            seen_inner.add(clause)
            inner.append(clause)
            inner_params.append(f"%{payload}%")
        if not inner:
            continue
        if len(inner) == 1:
            outer_parts.append(inner[0])
            params.append(inner_params[0])
        elif has_negated:
            outer_parts.append("(" + " AND ".join(inner) + ")")
            params.extend(inner_params)
        else:
            outer_parts.append("(" + " AND ".join(inner) + ")")
            params.extend(inner_params)
    return "\n            AND ".join(outer_parts), params


def build_pdm_or_where_clause(alternatives_per_keyword: List[List[str]]) -> Tuple[str, List[str]]:
    """Backward-compatible wrapper for merged PDM WHERE construction."""
    return build_pdm_and_where_clause(alternatives_per_keyword)


def query_pdm_bom_merged(
    alternatives_per_keyword: List[List[str]],
    model: Optional[str] = None,
    client: Any = None,
) -> List[Dict[str, Any]]:
    """Query BOM_027 with keywords and optional model filter.

    MODEL 查询策略（Python 两步，仅 miss 路径才发第二次 SQL）：
    - 有 MODEL：先用带 MODEL 的 WHERE 查询；命中即返回
    - 命中 0 行时回退到不带 MODEL 的查询（静默回退，业务侧故意行为）
    - 无 MODEL：直接单查询

    旧版用 CTE + UNION ALL 在一次 SQL 内表达"优先 + 回退"，但 SQL Server 不保证对
    sibling CTE 短路求值，常见命中路径会强制双扫描 BOM_027。改成 Python 两步后，
    命中路径只 1 次 SQL，仅 miss 路径才追加 1 次。

    Args:
        alternatives_per_keyword: 关键词列表
        model: 机型型号，如 "ADW-A-0314S"
        client: SQL client

    Returns:
        Deduplicated list of rows with PARTID and CHINANAME
    """
    if client is None:
        client = get_sql_client(_pdm_client_config())

    def _run(where_clause: str, params: List[str], tag: str) -> List[Dict[str, Any]]:
        query_sql = f"""
            SELECT DISTINCT
                a.PARTID AS PARTID,
                a.CHINANAME AS CHINANAME
            FROM BOM_027 a
            WHERE a.PARTVAR = (
                SELECT MAX(b.PARTVAR) FROM BOM_027 b WHERE b.PARTID = a.PARTID
            )
            AND {where_clause}
            ORDER BY a.PARTID
        """
        rows = client.query(query_sql, params)
        logger.debug("PDM BOM_027 查询 (%s) 命中 %d 条", tag, len(rows))
        return rows

    # 优先尝试带 MODEL（如有）的查询
    where_with_model, params_with_model = build_pdm_and_where_clause(
        alternatives_per_keyword, model=model
    )
    if not where_with_model:
        return []

    rows = _run(where_with_model, params_with_model, "带 MODEL" if model else "无 MODEL")

    # 命中 0 行且存在 MODEL 时，静默回退到无 MODEL 查询（业务侧故意行为）
    if not rows and model:
        where_no_model, params_no_model = build_pdm_and_where_clause(
            alternatives_per_keyword, model=None
        )
        if where_no_model:
            logger.debug("PDM BOM_027 MODEL 命中 0 行，回退到无 MODEL 查询")
            rows = _run(where_no_model, params_no_model, "MODEL 回退")

    return deduplicate_rows(rows)


def match_row_to_candidates(
    row: Dict[str, Any],
    alternatives_per_keyword: List[List[str]],
) -> List[str]:
    chinaname = str(row.get("CHINANAME") or "")
    result: List[str] = []
    for alts in alternatives_per_keyword:
        hits: List[str] = []
        has_negated = False
        for candidate in alts:
            text = str(candidate).strip()
            if not text:
                continue
            negated = text.startswith("!")
            has_negated = has_negated or negated
            payload = text[1:].strip() if negated else text
            if not payload:
                continue
            matched = (payload not in chinaname) if negated else (payload in chinaname)
            if matched:
                hits.append(text)
        result.append(" AND ".join(hits) if has_negated else (hits[0] if hits else ""))
    return result


def query_pdm_bom(
    condition: str | List[str],
    model: Optional[str] = None,
    client: Any = None,
) -> List[Dict[str, Any]]:
    """Query BOM_027 with condition and optional model filter.

    Args:
        condition: 单个关键词或关键词列表
        model: 机型型号，如 "ADW-A-0314S"
        client: SQL client

    Returns:
        Deduplicated list of rows with PARTID and CHINANAME
    """
    where_clauses, params = build_pdm_where_clauses(condition)

    # 添加 MODEL 过滤条件
    model_clauses, model_params = build_model_filter_clauses(model)
    where_clauses.extend(model_clauses)
    params.extend(model_params)

    if not where_clauses:
        return []

    and_conditions = "\n            AND ".join(where_clauses)

    if client is None:
        client = get_sql_client(_pdm_client_config())

    query_sql = f"""
        SELECT DISTINCT
            a.PARTID AS PARTID,
            a.CHINANAME AS CHINANAME
        FROM BOM_027 a
        WHERE a.PARTVAR = (
            SELECT MAX(b.PARTVAR) FROM BOM_027 b WHERE b.PARTID = a.PARTID
        )
        AND {and_conditions}
        ORDER BY a.PARTID
    """

    rows = client.query(query_sql, params)
    return deduplicate_rows(rows)
