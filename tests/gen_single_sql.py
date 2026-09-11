"""生成单个 type 的 PDM BOM 查询 SQL"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from app.domain.quotation.keyword_mapping import detect_product_type, expand_keyword_mapping
from app.domain.quotation.keyword_normalizer import normalize_pdm_keywords
from app.adapters.sqlserver.pdm_bom import build_pdm_and_where_clause


# 单个 type 数据
item = {
    "type": "中心柱天板密封罩",
    "attr": {
        "model": "ADW-A-0314S",
        "center_vibrator": "single",
        "detergent": False
    },
    "model": "ADW-A-0314S"
}

type_name = item.get("type")
attr = item.get("attr", {})
model = item.get("model")

print("=" * 80)
print(f"📌 Type: {type_name}")
print(f"   Attr: {json.dumps(attr, ensure_ascii=False)}")
print(f"   Model: {model}")
print("=" * 80)

# 1. 归一化关键词
keyword_groups = normalize_pdm_keywords(item)
print(f"\n🔹 normalize_pdm_keywords 结果:")
print(f"   {keyword_groups}")

if not keyword_groups:
    print("❌ 关键词归一化结果为空")
    sys.exit(1)

# 2. 检测产品类型
product_types = detect_product_type(keyword_groups[0]) or [""]
print(f"\n🔹 detect_product_type 结果:")
print(f"   {product_types}")

# 3. 扩展关键词映射
for product_type in product_types:
    print(f"\n{'─' * 80}")
    print(f"Product Type: {product_type or '(默认)'}")
    print(f"{'─' * 80}")

    alts_per_keyword = []
    for keyword in keyword_groups[0]:
        mapped = expand_keyword_mapping(keyword, product_type=product_type)
        print(f"   关键词: {keyword!r} -> {mapped}")
        if mapped:
            alts_per_keyword.append(mapped)

    print(f"\n🔹 alts_per_keyword:")
    for i, alts in enumerate(alts_per_keyword):
        print(f"   [{i}] {alts}")

    if not alts_per_keyword:
        print("❌ 无有效关键词")
        continue

    # 4. 构建 WHERE 子句
    where_clause, where_params = build_pdm_and_where_clause(alts_per_keyword, model=model)
    print(f"\n🔹 WHERE 子句:")
    print(f"   {where_clause}")
    print(f"   params={where_params}")

    if not where_clause:
        print("❌ WHERE 子句为空")
        continue

    # 5. 生成完整 SQL
    sql = f"""
SELECT DISTINCT
    a.PARTID AS PARTID,
    a.CHINANAME AS CHINANAME
FROM BOM_027 a
WHERE a.PARTVAR = (
    SELECT MAX(b.PARTVAR) FROM BOM_027 b WHERE b.PARTID = a.PARTID
)
AND {where_clause}
ORDER BY a.PARTID;"""

    print(f"\n✅ 完整 SQL:")
    print(sql)
