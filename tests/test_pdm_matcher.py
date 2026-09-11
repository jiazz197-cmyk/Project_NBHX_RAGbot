#!/usr/bin/env python3
"""
PDM Matcher 集成测试脚本

用法:
    cd project-yamato-shanghai
    python tests/test_pdm_matcher.py

环境要求:
    - .env 中配置正确的 PDM_SQLSERVER_* 连接信息
    - 能连通 PDM SQL Server 数据库
"""

import sys
import os
import json
from datetime import datetime

# 确保项目根目录在 path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


# =============================================================================
# 测试数据: ADW-A-0314S 完整 BOM 部件列表
# =============================================================================
SAMPLE_BOM_KEYWORDS = [
    {"type": "机架", "attr": {"model": "ADW-A-0314S", "material": "carbon_steel", "degree": "60", "surface": "flat", "end_user_country": "export", "detergent": "no"}},
    {"type": "中心柱天板密封罩", "attr": {"model": "ADW-A-0314S", "detergent": "no"}},
    {"type": "供料漏斗", "attr": {"model": "ADW-A-0314S", "surface": "flat", "degree": "60"}},
    {"type": "供料锥支架", "attr": {"model": "ADW-A-0314S"}},
    {"type": "顶锥", "attr": {"model": "ADW-A-0314S", "surface": "flat", "degree": "60", "lfp_type": "V"}},
    {"type": "振动盘", "attr": {"model": "ADW-A-0314S", "surface": "flat", "degree": "60", "lfp_lip": "lips", "lfp_type": "V", "leak_proof": "no", "detergent": "no"}},
    {"type": "供料斗", "attr": {"model": "ADW-A-0314S", "surface": "flat", "degree": "60", "fb_spring": "yes", "fb_gate": "single", "leak_proof": "no", "detergent": "no"}},
    {"type": "计量斗", "attr": {"model": "ADW-A-0314S", "surface": "flat", "degree": "60", "wb_spring": "yes", "wb_gate": "single", "leak_proof": "no", "detergent": "no"}},
    {"type": "溜槽", "attr": {"model": "ADW-A-0314S", "surface": "flat", "degree": "60", "collating_chute": "collection", "baffle": "no", "leak_proof": "no", "detergent": "no"}},
    {"type": "收集锥", "attr": {"model": "ADW-A-0314S", "surface": "flat", "degree": "60", "baffle": "no", "cf_baffles": "no", "detergent": "no"}},
    {"type": "集合斗", "attr": {"model": "ADW-A-0314S", "surface": "flat", "degree": "60", "capacity": "3L", "leak_proof": "no", "detergent": "no"}},
    {"type": "驱动单元", "attr": {"model": "ADW-A-0314S", "end_user_country": "export"}},
    {"type": "主振动器", "attr": {"model": "ADW-A-0314S"}},
    {"type": "线性振动器", "attr": {"model": "ADW-A-0314S", "end_user_country": "export"}},
    {"type": "配线单元", "attr": {"model": "ADW-A-0314S", "cable_length": "8m", "end_user_country": "export"}},
    {"type": "铭牌", "attr": {"model": "ADW-A-0314S", "name_plate": "SB09107G0028", "end_user_country": "export", "detergent": "no"}},
    {"type": "包装", "attr": {"model": "ADW-A-0314S", "end_user_country": "export", "detergent": "no"}},
    {"type": "电气", "attr": {"model": "ADW-A-0314S", "end_user_country": "export", "detergent": "no"}},
    {"type": "防碎", "attr": {"model": "ADW-A-0314S"}},
    {"type": "料层调整圈", "attr": {"model": "ADW-A-0314S"}},
    {"type": "记忆斗", "attr": {"model": "ADW-A-0314S", "surface": "flat"}},
    {"type": "光电料位计", "attr": {"model": "ADW-A-0314S", "detergent": "no"}},
]

# 适配后的 specs 格式 (model 已从 attr 提取到顶层)
SAMPLE_BOM_SPECS = [
    {"type": "机架", "model": "ADW-A-0314S", "attr": {"material": "carbon_steel", "degree": "60", "surface": "flat", "end_user_country": "export", "detergent": "no"}},
    {"type": "中心柱天板密封罩", "model": "ADW-A-0314S", "attr": {"detergent": "no"}},
    {"type": "供料漏斗", "model": "ADW-A-0314S", "attr": {"surface": "flat", "degree": "60"}},
    {"type": "供料锥支架", "model": "ADW-A-0314S", "attr": {}},
    {"type": "顶锥", "model": "ADW-A-0314S", "attr": {"surface": "flat", "degree": "60", "lfp_type": "V"}},
    {"type": "振动盘", "model": "ADW-A-0314S", "attr": {"surface": "flat", "degree": "60", "lfp_lip": "lips", "lfp_type": "V", "leak_proof": "no", "detergent": "no"}},
    {"type": "供料斗", "model": "ADW-A-0314S", "attr": {"surface": "flat", "degree": "60", "fb_spring": "yes", "fb_gate": "single", "leak_proof": "no", "detergent": "no"}},
    {"type": "计量斗", "model": "ADW-A-0314S", "attr": {"surface": "flat", "degree": "60", "wb_spring": "yes", "wb_gate": "single", "leak_proof": "no", "detergent": "no"}},
    {"type": "溜槽", "model": "ADW-A-0314S", "attr": {"surface": "flat", "degree": "60", "collating_chute": "collection", "baffle": "no", "leak_proof": "no", "detergent": "no"}},
    {"type": "收集锥", "model": "ADW-A-0314S", "attr": {"surface": "flat", "degree": "60", "baffle": "no", "cf_baffles": "no", "detergent": "no"}},
    {"type": "集合斗", "model": "ADW-A-0314S", "attr": {"surface": "flat", "degree": "60", "capacity": "3L", "leak_proof": "no", "detergent": "no"}},
    {"type": "驱动单元", "model": "ADW-A-0314S", "attr": {"end_user_country": "export"}},
    {"type": "主振动器", "model": "ADW-A-0314S", "attr": {}},
    {"type": "线性振动器", "model": "ADW-A-0314S", "attr": {"end_user_country": "export"}},
    {"type": "配线单元", "model": "ADW-A-0314S", "attr": {"cable_length": "8m", "end_user_country": "export"}},
    {"type": "铭牌", "model": "ADW-A-0314S", "attr": {"name_plate": "SB09107G0028", "end_user_country": "export", "detergent": "no"}},
    {"type": "包装", "model": "ADW-A-0314S", "attr": {"end_user_country": "export", "detergent": "no"}},
    {"type": "电气", "model": "ADW-A-0314S", "attr": {"end_user_country": "export", "detergent": "no"}},
    {"type": "防碎", "model": "ADW-A-0314S", "attr": {}},
    {"type": "料层调整圈", "model": "ADW-A-0314S", "attr": {}},
    {"type": "记忆斗", "model": "ADW-A-0314S", "attr": {"surface": "flat"}},
    {"type": "光电料位计", "model": "ADW-A-0314S", "attr": {"detergent": "no"}},
]


def test_imports():
    """测试 1: 导入链"""
    print("=" * 60)
    print("测试 1: 导入链")
    print("=" * 60)

    from app.core.config import settings
    print(f"  ✓ Settings: PDM_SQLSERVER_HOST={settings.PDM_SQLSERVER_HOST}")

    from app.adapters.pdm_matcher.type_config import TYPE_CONFIG
    print(f"  ✓ type_config: {len(TYPE_CONFIG)} 部件类型")

    from app.adapters.pdm_matcher.model_deriver import derive_models
    print("  ✓ model_deriver")

    from app.adapters.pdm_matcher.engine import query_candidate_parts, query_all_parallel
    print("  ✓ engine: query_candidate_parts, query_all_parallel")

    from app.adapters.sqlserver.pdm_matcher_adapter import adapt_input_to_matcher2
    print("  ✓ pdm_matcher_adapter")

    print("  ✅ 导入链测试通过\n")


def test_adapter_functions():
    """测试 2: 适配器函数"""
    print("=" * 60)
    print("测试 2: 适配器函数")
    print("=" * 60)

    from app.adapters.sqlserver.pdm_matcher_adapter import (
        adapt_input_to_matcher2,
        _truncate_component,
    )

    # 测试输入适配
    specs = adapt_input_to_matcher2(SAMPLE_BOM_KEYWORDS)

    assert len(specs) == 22, f"Expected 22 specs, got {len(specs)}"
    assert specs[0]["model"] == "ADW-A-0314S", f"Model not extracted: {specs[0]}"
    assert "model" not in specs[0]["attr"], "model should be removed from attr"
    assert specs[3]["model"] == "ADW-A-0314S", f"Empty attr model not extracted: {specs[3]}"
    assert specs[3]["attr"] == {}, "Empty attr should stay empty"
    assert specs[5]["attr"]["lfp_lip"] == "lips", "lfp_lip not preserved"
    print("  ✓ adapt_input_to_matcher2: 22 个 BOM 部件 model 提取正确")

    # 测试截断: HC=3 + MC=10 + LC=5 = 18, 不超 20 所以全部保留
    mock_result = {
        "normalized": {"type": "机架"},
        "high_confidence": [{"PARTID": f"HC{i}"} for i in range(3)],
        "medium_confidence": [{"PARTID": f"MC{i}"} for i in range(10)],
        "low_confidence": [{"PARTID": f"LC{i}"} for i in range(5)],
        "needs_review": [{"PARTID": f"RV{i}"} for i in range(15)],
        "stats": {"total_candidates": 33},
    }
    truncated = _truncate_component(mock_result)
    assert len(truncated["high_confidence"]) == 3
    assert len(truncated["medium_confidence"]) == 10
    assert len(truncated["low_confidence"]) == 5
    assert len(truncated["needs_review"]) == 0  # RV 被跳过
    assert truncated["stats"]["returned"] == 18
    print("  ✓ _truncate_component: 18 条全保留, RV 被跳过")

    # 测试截断: HC=3 + MC=10 + LC=10 = 23, 超 20 所以 LC 只取 7
    mock_result2 = {
        "normalized": {"type": "顶锥"},
        "high_confidence": [{"PARTID": f"HC{i}"} for i in range(3)],
        "medium_confidence": [{"PARTID": f"MC{i}"} for i in range(10)],
        "low_confidence": [{"PARTID": f"LC{i}"} for i in range(10)],
        "needs_review": [{"PARTID": f"RV{i}"} for i in range(15)],
        "stats": {"total_candidates": 38},
    }
    truncated2 = _truncate_component(mock_result2)
    assert len(truncated2["high_confidence"]) == 3
    assert len(truncated2["medium_confidence"]) == 10
    assert len(truncated2["low_confidence"]) == 7  # 20 - 3 - 10
    assert truncated2["stats"]["returned"] == 20
    print("  ✓ _truncate_component: 超 20 截断 LC, 返回 20 条")

    # 边界情况
    assert adapt_input_to_matcher2([]) == []
    assert adapt_input_to_matcher2({})[0]["model"] == ""
    assert _truncate_component({"error": "timeout"}) == {"error": "timeout"}
    print("  ✓ 边界情况处理正确")

    print("  ✅ 适配器函数测试通过\n")


def test_model_deriver():
    """测试 3: 型号派生"""
    print("=" * 60)
    print("测试 3: 型号派生")
    print("=" * 60)

    from app.adapters.pdm_matcher.model_deriver import derive_models

    # 标准型号
    models = derive_models("ADW-A-0314S")
    assert models["full"] == "ADW-A-0314S"
    assert models["short"] == "0314S"
    assert models["base"] == "0314"
    assert models["series"] == "03系列"
    print(f"  ✓ ADW-A-0314S → full={models['full']}, short={models['short']}, base={models['base']}")

    # 无前缀型号
    models2 = derive_models("0314S")
    assert models2["full"] == "0314S"
    assert models2["short"] == "0314S"
    print(f"  ✓ 0314S → full={models2['full']}, short={models2['short']}")

    # 空型号
    models3 = derive_models("")
    assert models3["full"] == ""
    print("  ✓ 空型号处理正确")

    print("  ✅ 型号派生测试通过\n")


def test_db_query():
    """测试 4: 数据库查询（需要真实连接）"""
    print("=" * 60)
    print("测试 4: 数据库查询")
    print("=" * 60)

    from app.core.config import settings
    from app.adapters.pdm_matcher.engine import query_candidate_parts

    print(f"  连接: {settings.PDM_SQLSERVER_HOST}:{settings.PDM_SQLSERVER_PORT}")
    print(f"  数据库: {settings.PDM_SQLSERVER_DATABASE}")

    # 取前 5 个部件做单条查询验证
    test_specs = SAMPLE_BOM_SPECS[:5]

    all_ok = True
    for i, spec in enumerate(test_specs, 1):
        print(f"\n  查询 {i}: type={spec['type']}, model={spec['model']}")
        try:
            result = query_candidate_parts(spec)
            if "error" in result:
                print(f"    ❌ 错误: {result['error']}")
                all_ok = False
                continue

            candidates = result.get("candidates", {})
            hc = len(candidates.get("high_confidence", []))
            mc = len(candidates.get("medium_confidence", []))
            lc = len(candidates.get("low_confidence", []))
            rv = len(candidates.get("needs_review", []))
            total = candidates.get("total_candidates", 0)
            dist = candidates.get("score_distribution", {})

            print(f"    ✓ HC={hc}  MC={mc}  LC={lc}  RV={rv}  total={total}")
            print(f"    ✓ 分数: max={dist.get('max', 0)} min={dist.get('min', 0)} avg={dist.get('avg', 0)}")

            if hc > 0:
                top = candidates["high_confidence"][0]
                print(f"    ✓ Top HC: {top['PARTID']} | score={top['_score']} | {top['CHINANAME'][:40]}")
            if mc > 0:
                top_mc = candidates["medium_confidence"][0]
                print(f"    ✓ Top MC: {top_mc['PARTID']} | score={top_mc['_score']} | {top_mc['CHINANAME'][:40]}")

        except Exception as e:
            print(f"    ❌ 连接失败: {type(e).__name__}: {e}")
            all_ok = False

    if all_ok:
        print("\n  ✅ 数据库查询测试通过\n")
    return all_ok


def test_parallel_query():
    """测试 5: 并行查询 + 保存结果"""
    print("=" * 60)
    print("测试 5: 并行查询")
    print("=" * 60)

    from app.adapters.pdm_matcher.engine import query_all_parallel
    from app.adapters.sqlserver.pdm_matcher_adapter import _truncate_component

    specs = SAMPLE_BOM_SPECS

    print(f"  并行查询 {len(specs)} 个部件...")
    try:
        results = query_all_parallel(specs, max_workers=3)

        truncated = []
        for i, (spec, result) in enumerate(zip(specs, results), 1):
            if result is None or "error" in (result or {}):
                err = result.get("error", "unknown") if result else "None result"
                print(f"    [{i}] {spec['type']}: ❌ {err}")
                continue

            tc = _truncate_component(result)
            hc = len(tc.get("high_confidence", []))
            mc = len(tc.get("medium_confidence", []))
            lc = len(tc.get("low_confidence", []))
            returned = tc.get("stats", {}).get("returned", 0)
            print(f"    [{i}] {spec['type']}: ✓ HC={hc} MC={mc} LC={lc} returned={returned}")
            truncated.append(tc)

        # 保存截断后的结果到 JSON 文件
        output_dir = os.path.join(_project_root, "tests", "output")
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(output_dir, f"pdm_match_result_{timestamp}.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({"components": truncated}, f, ensure_ascii=False, indent=2)
        print(f"\n  ✓ 结果已保存: {output_path}")

        print("\n  ✅ 并行查询测试通过\n")
        return True
    except Exception as e:
        print(f"    ❌ 失败: {type(e).__name__}: {e}\n")
        return False


def main():
    print("\n" + "=" * 60)
    print("PDM Matcher 集成测试")
    print("=" * 60 + "\n")

    # 测试 1-3: 不需要数据库
    test_imports()
    test_adapter_functions()
    test_model_deriver()

    # 测试 4-5: 需要数据库连接
    db_ok = test_db_query()
    if db_ok:
        test_parallel_query()
    else:
        print("⚠️  数据库连接失败，跳过测试 4-5")
        print("   请确保 .env 中 PDM_SQLSERVER_* 配置正确\n")

    print("=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
