import json
from app.domain.quotation.keyword_normalizer import normalize_pdm_keywords
from app.domain.quotation.keyword_mapping import detect_product_type, expand_keyword_mapping
from app.adapters.sqlserver.pdm_bom import build_pdm_and_where_clause

def run_pdm_query_sql(test_payload):
    print("=" * 60)
    print("【输入负载】")
    print(json.dumps(test_payload, ensure_ascii=False, indent=2))
    print("-" * 60)

    # 第一步：把 JSON 转为展平的关键词组
    keyword_groups = normalize_pdm_keywords(test_payload)
    if not keyword_groups:
        print("未生成有效的查询关键词组！")
        return

    # 第二步：针对每组关键词开始转换与 SQL 组装
    for idx, group in enumerate(keyword_groups, start=1):
        product_types = detect_product_type(group) or [""]
        
        for product_type in product_types:
            alts_per_keyword = []
            print(f"\n[组 {idx}] 检测到产品类型: '{product_type}'")
            print(f" -> 原始分组: {group}")
            
            # 对单个关键词执行各类扩展和映射
            for keyword in group:
                mapped_alts = expand_keyword_mapping(keyword, product_type=product_type)
                if mapped_alts:
                    print(f"    - '{keyword}' 映射/扩展为 -> {mapped_alts}")
                    alts_per_keyword.append(mapped_alts)

            if not alts_per_keyword:
                continue

            # 第三步：拼装 WHERE 子句
            where_conditions, where_params = build_pdm_and_where_clause(alts_per_keyword)
            
            sql_query = f"""
SELECT DISTINCT
    CHINANAME,
    PARTID
FROM BOM_016
WHERE
    SEQNUM LIKE '1.[0-9]%'
    AND SEQNUM NOT LIKE '1.[0-9]%.%'
    AND {where_conditions}
ORDER BY CHINANAME, PARTID
"""
            print("\n【最终生成的 SQL】")
            print(sql_query)
            print(f"  params={where_params}")

if __name__ == "__main__":
    # 在这里放入你想测试的字典格式，完全模拟接口参数
    test_input = [
      {
        "type": "机架",
        "attr": {
          "model": "ADW-A-0314S",
          "surface": "flat",
          "commonbed": "painted on ss",
          "collating_chute": "50-degree",
          "end_user_country": "india",
          "weigh_bucket": "single",
          "detergent": False
        }
      },
      {
        "type": "供料漏斗",
        "attr": {
          "model": "ADW-A-0314S",
          "surface": "flat",
          "infeed_funnel": "single",
          "linear_feeder_pan": "sn"
        }
      },
      {
        "type": "顶锥",
        "attr": {
          "model": "ADW-A-0314S",
          "surface": "flat",
          "lfp_lip": "flat lip",
          "top_cone": "single",
          "detergent": False,
          "linear_feeder_pan": "sn"
        }
      },
      {
        "type": "振动盘",
        "attr": {
          "model": "ADW-A-0314S",
          "linear_feeder_pan": "sn",
          "detergent": False,
          "lfp_lip": "flat lip",
          "surface": "flat"
        }
      },
      {
        "type": "供料斗",
        "attr": {
          "model": "ADW-A-0314S",
          "fb_spring": True,
          "lfp_lip": "flat lip",
          "fb_gate": "single door",
          "surface": "flat",
          "detergent": False
        }
      },
      {
        "type": "计量斗",
        "attr": {
          "model": "ADW-A-0314S",
          "wb_spring": True,
          "surface": "flat",
          "wb_gate": "single door",
          "detergent": False
        }
      },
      {
        "type": "驱动单元",
        "attr": {
          "model": "ADW-A-0314S",
          "regulation": "india w&m"
        }
      },
      {
        "type": "溜槽",
        "attr": {
          "model": "ADW-A-0314S",
          "collating_chute": "50-degree",
          "surface": "flat",
          "detergent": False,
          "cc_baffles": False
        }
      },
      {
        "type": "收集锥",
        "attr": {
          "model": "ADW-A-0314S",
          "surface": "flat",
          "collating_chute": "50-degree",
          "cf_baffles": False,
          "cf_l_shaped_bracket": False
        }
      },
      {
        "type": "电气",
        "attr": {
          "model": "ADW-A-0314S"
        }
      },
      {
        "type": "配线单元",
        "attr": {
          "model": "ADW-A-0314S",
          "cable_length": "8m"
        }
      },
      {
        "type": "主振动器",
        "attr": {
          "model": "ADW-A-0314S",
          "center_vibrator": "single"
        }
      },
      {
        "type": "线性振动器",
        "attr": {
          "model": "ADW-A-0314S"
        }
      },
      {
        "type": "中心柱天板密封罩",
        "attr": {
          "model": "ADW-A-0314S",
          "center_vibrator": "single",
          "detergent": False
        }
      },
      {
        "type": "供料锥支架",
        "attr": {
          "model": "ADW-A-0314S",
          "top_cone": "single"
        }
      },
      {
        "type": "包装",
        "attr": {
          "model": "ADW-A-0314S",
          "end_user_country": "india",
          "detergent": False
        }
      },
      {
        "type": "集合斗",
        "attr": {
          "model": "ADW-A-0314S",
          "collection_bucket": "3l, 1-way, motor",
          "surface": "flat",
          "collecting_funnel": "single",
          "degree": "50-degree",
          "detergent": False
        }
      },
      {
        "type": "记忆斗",
        "attr": {
          "model": "ADW-A-0314S",
          "surface": "flat"
        }
      },
      {
        "type": "防碎",
        "attr": {
          "model": "ADW-A-0314S"
        }
      },
      {
        "type": "料层调整圈",
        "attr": {
          "model": "ADW-A-0314S"
        }
      }
    ]
    
    run_pdm_query_sql(test_input)