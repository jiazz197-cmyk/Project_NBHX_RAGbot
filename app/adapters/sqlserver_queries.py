"""SQL Server query adapters.

These adapters implement the outbound query ports and run the SQL
orchestration directly; low-level helpers (client, pdm_bom, u8_bom,
exceptions) live in the :mod:`app.adapters.sqlserver` subpackage.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from app.adapters.sqlserver.client import close_sql_client, get_sql_client
from app.adapters.sqlserver.exceptions import (
    QueryCancelledError,
    U8RootFailureBreakerError,
    raise_if_cancelled,
)
from app.adapters.sqlserver.pdm_bom import (
    deduplicate_pdm_result_rows,
    match_row_to_candidates,
    query_pdm_bom_merged,
)
from app.adapters.sqlserver.u8_bom import (
    _query_u8_bom_inventory,
    format_u8_output_rows,
    split_parent_inv_codes,
)
from app.core.config import settings
from app.core.exceptions import ExternalServiceError, ValidationError
from app.core.logging import get_logger
from app.domain.quotation.keyword_mapping import (
    detect_product_type,
    expand_keyword_mapping,
)
from app.domain.quotation.keyword_normalizer import normalize_pdm_keywords
from app.ports.outbound.sqlserver_queries import (
    PdmBomQueryPort,
    PdmMatchQueryPort,
    U8BomInventoryQueryPort,
)
from app.ports.dto.sqlserver_queries import SqlserverQueryResultDTO

logger = get_logger("database.sqlserver")


class U8BomInventoryQueryAdapter(U8BomInventoryQueryPort):
    def run(
        self,
        payload: Any,
        *,
        cancel_checker: Optional[Callable[[], bool]] = None,
        user_key: Optional[str] = None,
    ) -> Any:
        """U8 BOM + Inventory. Cooperative cancellation between root iterations."""
        parent_codes = split_parent_inv_codes(payload.parent_inv_codes)
        if not parent_codes:
            raise ValidationError("parent_inv_codes 不能为空")

        try:
            result = _query_u8_bom_inventory(
                parent_codes,
                payload.max_depth,
                cancel_checker=cancel_checker,
                user_key=user_key,
            )
            rows = format_u8_output_rows(result.rows)
            logger.debug(
                "U8 查询完成: parent_codes=%s, raw_rows=%s, output_rows=%s, failed_roots=%s",
                len(parent_codes),
                len(result.rows),
                len(rows),
                len(result.failed_root_codes),
            )
            return SqlserverQueryResultDTO(
                total=len(rows),
                items=rows,
                failed_root_codes=result.failed_root_codes,
                partial=result.partial,
            )
        except (ValidationError, QueryCancelledError, U8RootFailureBreakerError):
            raise
        except Exception as exc:
            logger.error("U8 查询失败: %s", exc, exc_info=True)
            raise ExternalServiceError("U8 SQLServer", f"查询失败: {exc}") from exc


class PdmBomQueryAdapter(PdmBomQueryPort):
    def run(
        self,
        payload: Any,
        *,
        cancel_checker: Optional[Callable[[], bool]] = None,
    ) -> Any:
        """PDM BOM_027. Cooperative cancellation between SQL calls."""
        keyword_groups = normalize_pdm_keywords(payload.keywords)
        if not keyword_groups:
            raise ValidationError(
                "keywords 不能为空，且仅支持 {type, attr} 或 [{type, attr}, ...]"
            )

        shared_client = get_sql_client(
            {
                "backend": "pymssql",
                "server": settings.PDM_SQLSERVER_HOST,
                "port": settings.PDM_SQLSERVER_PORT,
                "database": settings.PDM_SQLSERVER_DATABASE,
                "username": settings.PDM_SQLSERVER_USER,
                "password": settings.PDM_SQLSERVER_PASSWORD,
                "encrypt": settings.PDM_SQLSERVER_ENCRYPT,
            }
        )

        def extract_model_from_raw_keywords(raw_keywords: Any) -> Optional[str]:
            """从原始 keywords 中提取 model 参数。"""
            if isinstance(raw_keywords, dict):
                # 单个对象: {"type": "机架", "model": "ADW-A-0314S", "attr": {...}}
                model = raw_keywords.get("model")
                if model:
                    return str(model).strip()
            elif isinstance(raw_keywords, list):
                # 列表: [{"type": "机架", "model": "ADW-A-0314S"}, ...]
                for item in raw_keywords:
                    if isinstance(item, dict):
                        model = item.get("model")
                        if model:
                            return str(model).strip()
            return None

        try:
            rows: List[Dict[str, Any]] = []
            executed_query_count = 0

            # 从原始 payload.keywords 提取 model
            model = extract_model_from_raw_keywords(payload.keywords)

            for idx, group in enumerate(keyword_groups, start=1):
                raise_if_cancelled(cancel_checker)
                product_types = detect_product_type(group) or [""]
                for product_type in product_types:
                    raise_if_cancelled(cancel_checker)
                    alts_per_keyword: List[List[str]] = []
                    mapping_debug: List[Dict[str, Any]] = []
                    for keyword in group:
                        mapped = expand_keyword_mapping(keyword, product_type=product_type)
                        mapping_debug.append({"raw": keyword, "expanded": mapped})
                        if mapped:
                            alts_per_keyword.append(mapped)
                    if not alts_per_keyword:
                        continue

                    logger.debug(
                        "PDM 关键词转换: group_index=%s, product_type=%r, model=%r, expanded_count=%s",
                        idx,
                        product_type,
                        model,
                        len(mapping_debug),
                    )

                    executed_query_count += 1
                    group_rows = query_pdm_bom_merged(alts_per_keyword, model=model, client=shared_client)
                    for row in group_rows:
                        item = dict(row)
                        item["QUERY_INDEX"] = idx
                        item["QUERY_KEYWORDS"] = group
                        item["QUERY_EXPANDED_KEYWORDS"] = match_row_to_candidates(
                            row, alts_per_keyword
                        )
                        rows.append(item)

            deduplicated_rows = deduplicate_pdm_result_rows(rows)
            logger.debug(
                "PDM 查询完成: input_groups=%s, executed_queries=%s, rows=%s, deduplicated_rows=%s",
                len(keyword_groups),
                executed_query_count,
                len(rows),
                len(deduplicated_rows),
            )
            return SqlserverQueryResultDTO(total=len(deduplicated_rows), items=deduplicated_rows)
        except (ValidationError, QueryCancelledError):
            raise
        except Exception as exc:
            logger.error("PDM 查询失败: %s", exc, exc_info=True)
            raise ExternalServiceError("PDM SQLServer", f"查询失败: {exc}") from exc
        finally:
            close_sql_client(shared_client)


class PdmMatchQueryAdapter(PdmMatchQueryPort):
    def run(
        self,
        payload: Any,
        *,
        cancel_checker: Optional[Callable[[], bool]] = None,
    ) -> Any:
        """PDM 部件匹配查询 - 四路召回 + 多维打分。

        通过 matcher2 引擎执行多路召回、打分排序，
        并将分层结果扁平化返回。
        """
        from app.adapters.sqlserver.pdm_matcher_adapter import (
            run_pdm_match_query as _run_match,
        )

        try:
            result = _run_match(payload.keywords)
            total = result.get("total", 0)
            items = result.get("items", [])
            components = result.get("components", [])
            logger.debug(
                "PDM 匹配查询完成: total=%s",
                total,
            )
            return SqlserverQueryResultDTO(total=total, items=items, components=components)
        except Exception as exc:
            logger.error("PDM 匹配查询失败: %s", exc, exc_info=True)
            raise ExternalServiceError("PDM SQLServer", f"匹配查询失败: {exc}") from exc
