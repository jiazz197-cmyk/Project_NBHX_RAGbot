"""PDM BOM 调试工具：查询 keywords_payload 并生成 SQL"""
import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.orm.quotation_task import QuotationTask
from app.domain.quotation.keyword_mapping import expand_keyword_mapping
from app.domain.quotation.keyword_normalizer import normalize_pdm_keywords
from app.adapters.sqlserver.pdm_bom import build_pdm_and_where_clause


def _inline_params(sql: str, params) -> str:
    """Render a parameterized SQL fragment into a runnable display string (debug only)."""
    rendered = sql
    for value in params:
        literal = "N'" + str(value).replace("'", "''") + "'"
        rendered = rendered.replace("%s", literal, 1)
    return rendered


async def get_keywords_payload(task_id: int):
    """从数据库获取 keywords_payload"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(QuotationTask).where(QuotationTask.id == task_id))
        task = result.scalars().first()
        if not task:
            print(f"❌ Task id={task_id} not found")
            return None
        return task.result_payload.get("keywords_payload") if task.result_payload else None


def generate_sql(item: dict) -> str:
    """为单个 type 生成 SQL"""
    type_name = item.get("type")
    attr = item.get("attr", {})
    model = item.get("model")

    keyword_groups = normalize_pdm_keywords(item)
    if not keyword_groups:
        return None

    alts_per_keyword = []
    for kw in keyword_groups[0]:
        mapped = expand_keyword_mapping(kw, product_type=type_name)
        if mapped:
            alts_per_keyword.append(mapped)

    base_conditions_sql, base_conditions_params = build_pdm_and_where_clause(
        alts_per_keyword, model=None
    )
    if not base_conditions_sql:
        return None

    base_conditions = _inline_params(base_conditions_sql, base_conditions_params)

    if model:
        safe_model = model.replace("'", "''")
        return f"""-- {type_name} (model={model})
WITH model_matched AS (
    SELECT DISTINCT a.PARTID, a.CHINANAME
    FROM BOM_027 a
    WHERE a.PARTVAR = (SELECT MAX(b.PARTVAR) FROM BOM_027 b WHERE b.PARTID = a.PARTID)
    AND {base_conditions}
    AND a.MODEL LIKE '%{safe_model}%'
),
no_model AS (
    SELECT DISTINCT a.PARTID, a.CHINANAME
    FROM BOM_027 a
    WHERE a.PARTVAR = (SELECT MAX(b.PARTVAR) FROM BOM_027 b WHERE b.PARTID = a.PARTID)
    AND {base_conditions}
)
SELECT * FROM model_matched UNION ALL SELECT * FROM no_model WHERE NOT EXISTS (SELECT 1 FROM model_matched) ORDER BY PARTID;"""

    return f"""-- {type_name}
SELECT DISTINCT a.PARTID, a.CHINANAME FROM BOM_027 a
WHERE a.PARTVAR = (SELECT MAX(b.PARTVAR) FROM BOM_027 b WHERE b.PARTID = a.PARTID)
AND {base_conditions} ORDER BY a.PARTID;"""


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PDM BOM 调试工具")
    parser.add_argument("--task-id", type=int, default=214, help="quotation_tasks.id")
    parser.add_argument("--type", type=str, help="指定 type 名称，如 '中心柱天板密封罩'")
    args = parser.parse_args()

    payload = asyncio.run(get_keywords_payload(args.task_id))
    if not payload:
        sys.exit(1)

    keywords = payload.get("keywords", [])
    print(f"📋 共 {len(keywords)} 个 type")

    for item in keywords:
        type_name = item.get("type")
        if args.type and args.type != type_name:
            continue
        print(f"\n{'─' * 60}\n📌 {type_name}")
        sql = generate_sql(item)
        print(sql or "⚠️ 无法生成 SQL")
