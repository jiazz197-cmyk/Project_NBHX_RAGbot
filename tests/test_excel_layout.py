"""issue #23：Excel 表头结构探测（domain 纯逻辑）单测。

用例覆盖真实语料里的五类布局（标题行 / 两级表头 / 一级表头 / 前导空行 / 空 sheet）
与两类拒绝场景（首行非表头、两级/一级判不准），以及列名兜底与读侧无法容忍的
重名/空名处理。

本文件**不依赖 pandas**：探测吃的是「二维单元格序列」，CI 的 pytest job 不装
pandas 也能跑（真实 xlsx 的端到端用例在 test_excel_parser_engine.py /
test_excel_retrieval_hardening.py，那里按 pandas 存在与否 importorskip）。
"""

from __future__ import annotations

import pytest

from app.domain.knowledge.excel_layout import (
    FALLBACK_COLUMN_PREFIX,
    ExcelLayoutError,
    data_rows,
    detect_sheet_layout,
    flatten_headers,
    format_layout,
    normalize_cell,
)


# ---------------------------------------------------------------------------
# 1. 三类布局：一级表头 / 标题行 + 一级表头 / 两级表头
# ---------------------------------------------------------------------------


def test_plain_single_level_header():
    """一级表头：第 0 行即表头，数据从第 1 行开始。"""
    rows = [
        ["项目名称", "负责人"],
        ["V254 (GLC)", "洪鑫浩"],
        ["X91", "张三"],
    ]
    layout = detect_sheet_layout(rows)

    assert layout is not None
    assert layout.header_row == 0
    assert layout.header_levels == 1
    assert layout.data_start_row == 1
    assert layout.has_title_row is False
    assert layout.headers == ("项目名称", "负责人")
    assert data_rows(rows, layout) == [["V254 (GLC)", "洪鑫浩"], ["X91", "张三"]]


def test_title_row_then_single_level_header():
    """标题行 + 一级表头（真实文件：模具系数表首行是 IM注塑模具基础系数）。"""
    rows = [
        ["IM注塑模具基础系数", None, None],
        ["#", "长度范围(mm)", "系数_L"],
        [1, "0~100", "1.0"],
        [2, "100~300", "1.15"],
    ]
    layout = detect_sheet_layout(rows)

    assert layout is not None
    assert layout.has_title_row is True
    assert layout.header_row == 1
    assert layout.data_start_row == 2
    assert layout.headers == ("#", "长度范围(mm)", "系数_L")
    assert data_rows(rows, layout) == [["1", "0~100", "1.0"], ["2", "100~300", "1.15"]]


def test_two_level_header_flattened_with_group_fill():
    """两级表头：组行合并空位向右填充，叶子非空则拼成 组_子。"""
    rows = [
        ["项目信息", None, None, None, "指标", "FRQ版", None, None],
        ["项目编号", "客户", "大区", "项目名称", "指标名称", "Total", "%", "SOP年单套"],
        [None, None, None, "ABCT项目", "产品收入", "100", "1", "50"],
    ]
    layout = detect_sheet_layout(rows)

    assert layout is not None
    assert layout.header_levels == 2
    assert layout.data_start_row == 2
    assert layout.headers[:5] == (
        "项目信息_项目编号",
        "项目信息_客户",
        "项目信息_大区",
        "项目信息_项目名称",
        "指标_指标名称",
    )
    # FRQ版 组的三个叶子列：Total / % / SOP年单套
    assert layout.headers[5:] == ("FRQ版_Total", "FRQ版_%", "FRQ版_SOP年单套")
    assert data_rows(rows, layout) == [
        ["", "", "", "ABCT项目", "产品收入", "100", "1", "50"]
    ]


def test_title_row_before_two_level_header():
    """标题行 + 两级表头：先跳标题行，再判级数。"""
    rows = [
        ["2026 年项目利润表", None, None, None, None],
        ["项目信息", None, None, "指标", None],
        ["项目编号", "客户", "项目名称", "指标名称", "版本"],
        ["P1", "奇瑞", "E03", "产品收入", "FRQ"],
    ]
    layout = detect_sheet_layout(rows)

    assert layout is not None
    assert layout.has_title_row is True
    assert layout.header_levels == 2
    assert layout.header_row == 1
    assert layout.data_start_row == 3
    assert layout.headers == (
        "项目信息_项目编号",
        "项目信息_客户",
        "项目信息_项目名称",
        "指标_指标名称",
        "指标_版本",
    )
    assert data_rows(rows, layout) == [["P1", "奇瑞", "E03", "产品收入", "FRQ"]]


def test_title_plus_single_group_header_is_refused():
    """标题 + 只有一格的组行：判不准（第 1 行不像表头）→ 拒绝，不猜。

    这是刻意的保守：单格组行与「单列表头」无法区分，宁可让用户看到失败原因。
    """
    rows = [
        ["2026 年项目利润表", None, None],
        ["项目信息", None, None],
        ["项目编号", "客户", "项目名称"],
        ["P1", "奇瑞", "E03"],
    ]
    with pytest.raises(ExcelLayoutError) as excinfo:
        detect_sheet_layout(rows)

    assert excinfo.value.reason == "no_header_row"


# ---------------------------------------------------------------------------
# 2. 边界：前导空行 / 空 sheet / 尾部空行 / 短行
# ---------------------------------------------------------------------------


def test_leading_blank_rows_are_skipped_with_absolute_row_numbers():
    """真实文件 工作表1：第 0 行全空，真表头在第 1 行（列名按位置兜底 列N）。"""
    rows = [
        [None, None, None, None, None],
        ["", "内控", "SOP", "", "结案"],
        ["", "427CNB CNSL", "A3PA （A3）", "IS4GR", "A SUVe"],
    ]
    layout = detect_sheet_layout(rows)

    assert layout is not None
    assert layout.header_row == 1
    assert layout.data_start_row == 2
    assert layout.headers == ("列1", "内控", "SOP", "列4", "结案")
    assert data_rows(rows, layout) == [["", "427CNB CNSL", "A3PA （A3）", "IS4GR", "A SUVe"]]


def test_empty_sheet_returns_none():
    assert detect_sheet_layout([]) is None
    assert detect_sheet_layout([[None, None], ["", "   "]]) is None


def test_blank_rows_in_data_are_dropped_and_width_padded():
    rows = [
        ["名称", "编码", "备注"],
        ["华翔内饰", "2600"],
        ["", "", ""],
        [None, "2609", "合肥"],
    ]
    layout = detect_sheet_layout(rows)

    assert layout is not None
    assert data_rows(rows, layout) == [["华翔内饰", "2600", ""], ["", "2609", "合肥"]]


# ---------------------------------------------------------------------------
# 3. 拒绝：宁可失败，不做静默错位
# ---------------------------------------------------------------------------


def test_all_numeric_first_row_is_refused():
    rows = [
        [1, 2, 3.5, 4],
        [5, 6, 7, 8],
    ]
    with pytest.raises(ExcelLayoutError) as excinfo:
        detect_sheet_layout(rows)

    assert excinfo.value.reason == "no_header_row"
    assert "表头" in str(excinfo.value)


def test_prose_sheet_is_refused():
    """散文 sheet（真实文件 操作指南：有「步骤1 / 说明」这类两列行）→ 拒绝，不冒充表头。"""
    rows = [
        ["项目利润表总览 — 操作指南", None],
        [None, None],
        ["一、粘贴新项目数据", None],
        ["步骤1", "在总览表末尾找到空行位置"],
    ]
    with pytest.raises(ExcelLayoutError) as excinfo:
        detect_sheet_layout(rows)

    assert excinfo.value.reason == "no_header_row"


def test_single_column_table_keeps_first_row_as_header():
    """单列表格（元数据 sheet：编码 / X / Y）没有歧义——首行即表头。"""
    rows = [["编码"], ["X"], ["Y"]]
    layout = detect_sheet_layout(rows)

    assert layout is not None
    assert layout.headers == ("编码",)
    assert layout.data_start_row == 1
    assert data_rows(rows, layout) == [["X"], ["Y"]]


def test_single_column_all_numeric_is_refused():
    """单列但首行是数字：无表头可判 → 拒绝（不产「列1：123」这种假标签）。"""
    with pytest.raises(ExcelLayoutError) as excinfo:
        detect_sheet_layout([[1001], [1002], [1003]])

    assert excinfo.value.reason == "no_header_row"


def test_ambiguous_two_level_fill_ratio_is_refused():
    """跨列空位只被下一行填一半 → 判不准，拒绝（不猜）。"""
    rows = [
        ["项目信息", None, None, "指标", None],
        ["项目编号", None, "大区", "指标名称", "版本"],
        ["P1", None, "华东", "产品收入", "FRQ"],
    ]
    with pytest.raises(ExcelLayoutError) as excinfo:
        detect_sheet_layout(rows)

    assert excinfo.value.reason == "ambiguous_header_levels"


def test_long_group_run_without_sub_header_stays_single_level():
    """长空串存在但下一行没填（真实文件 FONE 基础表）：仍是一级表头 + 列N 兜底。"""
    rows = [
        ["人员性质", "执行人员情况", None, "产品大类", None, None, None],
        ["高层管理人员", "新增", None, None, None, None, None],
    ]
    layout = detect_sheet_layout(rows)

    assert layout is not None
    assert layout.header_levels == 1
    assert layout.headers == ("人员性质", "执行人员情况", "列3", "产品大类", "列5", "列6", "列7")


# ---------------------------------------------------------------------------
# 4. 列名：空名兜底 / 重名去重 / 归一化
# ---------------------------------------------------------------------------


def test_flatten_headers_dedupes_repeated_names():
    """真实文件 全新项目：IBP / 内控 / SOP 各出现两次——读取端 records 键必须唯一。"""
    headers = flatten_headers(
        [["序", "IBP", "内控", "IBP", "IBP", "内控", "IBP_2"]], width=7
    )

    assert headers == ("序", "IBP", "内控", "IBP_2", "IBP_3", "内控_2", "IBP_2_2")
    assert len(set(headers)) == len(headers)


def test_blanks_and_short_rows_get_positional_fallback_names():
    headers = flatten_headers([["名称", "", "编码"]], width=5)

    assert headers == ("名称", f"{FALLBACK_COLUMN_PREFIX}2", "编码", "列4", "列5")


def test_normalize_cell_covers_nan_none_and_whitespace():
    assert normalize_cell(None) == ""
    assert normalize_cell(float("nan")) == ""
    assert normalize_cell("  V254 (GLC)  ") == "V254 (GLC)"
    assert normalize_cell(1504.0) == "1504.0"
    assert normalize_cell("nan") == ""


def test_format_layout_is_stable_metadata_string():
    layout = detect_sheet_layout([["名称", "编码"], ["A", "1"]])

    assert layout is not None
    assert format_layout(layout) == "header_row=0,levels=1,title=0,data_start=1"
