"""Excel 表头结构探测（纯逻辑，无 IO）——issue #23 的唯一口径来源。

同一份 Excel 的「表头在哪、有几级、数据从哪行开始」在写入端（chunk 化）与
读取端（整表 JSON）**必须由同一段规则给出**，否则会出现「检索命中的 chunk 与
整表 JSON 的行列语义对不上」这种不抛异常、只产出静默错位数据的故障（财务域
不可接受）。

本模块只吃「二维单元格序列」（``rows[row][col]``，元素是 pandas / calamine
读出来后的原始对象），返回行号与列名，**不 import pandas、不碰文件与网络**：
两侧适配器各自负责读文件，读到的原始二维数组交给这里。

## 判定规则（常量集中，改规则先读这里）

1. **归一化** ``normalize_cell``：``None`` / ``NaN`` / ``NaT`` / 空串 → ``""``，
   其余 ``str(value).strip()``（与历史行为一致：数字与日期两端都 ``str()``）。
2. **空 sheet** ``detect_sheet_layout`` 返回 ``None``：去掉尾部整行空行后没有任何
   非空单元格。调用方按「空表」处理——不产 chunk，但**不**当解析失败。
3. **前导空行**：跳过整行全空的行（真实文件里 ``工作表1`` 就是这样：第 0 行全空、
   真表头在第 1 行），因此返回的 ``header_row`` / ``data_start_row`` 是**绝对行号**。
4. **标题行** ``has_title_row``：第 0 行只有 1 个非空格、该格不是数值/日期、
   且第 1 行「像表头」。典型：``模具系数表`` 首行是 ``IM注塑模具基础系数``。
5. **像表头** ``_is_header_like``：非空格 ≥ ``MIN_HEADER_CELLS`` 且
   「数值/日期样格数 ÷ 非空格数」 < ``HEADER_DATA_LIKE_MAX_RATIO``。
   首行整行都是数据（纯数字）时判为「没有表头」→ 拒绝。
   特例：**单列表格**（每一行最多 1 个非空格）没有歧义，首行即表头（首行是数值/日期
   则仍拒绝）；单列散文 sheet 里总有行带 2 个以上非空格，因此仍走拒绝路径。
6. **表头级数**：候选表头行里存在长度 ≥ ``GROUP_HOLE_MIN_RUN`` 的连续空格串
   （跨列合并单元格留下的痕迹）时，看下一行是否像表头、以及它填满这些空位的比例：
   - ``≥ TWO_LEVEL_FILL_MIN`` → **两级表头**（组行 + 叶子行，扁平化成 ``组_子``）；
   - ``≤ ONE_LEVEL_FILL_MAX`` → **一级表头**（那些空格是真空白列，列名走 ``列N`` 兜底）；
   - 介于两者之间 → **拒绝** ``ambiguous_header_levels``（判不准就不猜）。
   没有长空格串 → 一级表头。
7. **拒绝语义**：抛 :class:`ExcelLayoutError`（``no_header_row`` /
   ``ambiguous_header_levels``）。调用方必须把它变成用户可见的失败原因，
   **不得**退回旧口径继续产 chunk（本 issue 的核心就是「宁可拒绝，不要静默错位」）。
8. **列名** ``flatten_headers``：两级时组行向右填充（合并单元格只把组名写在左上角），
   空名 → ``列{i+1}``（1-based 列号），重名 → 追加 ``_2`` / ``_3``
   （真实文件 ``全新项目`` 的 ``IBP`` / ``内控`` / ``SOP`` 各出现两次；
   读取端 ``to_dict(orient='records')`` 会把重名列**静默丢掉一列**）。
9. **数据行** ``data_rows``：从 ``data_start_row`` 起，逐格归一化、列宽对齐到表头宽度、
   丢弃整行全空的行。写入端与读取端都走这个函数，保证逐项一致。

## 跨容器注意

RAG 侧若整体拆进独立容器（见 CLAUDE.md「RAG 栈已整体拆出主清单」），
本模块是**唯一口径来源**，复制过去时必须同步；不要在下游再写一份近似规则。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, Sequence

# ---------------------------------------------------------------------------
# 规则常量（调参改这里，并在 docstring 的规则说明里同步）
# ---------------------------------------------------------------------------

#: 「像表头」要求的最少非空单元格数（只有 1 格的行更可能是标题或散文行）
MIN_HEADER_CELLS = 2

#: 「像表头」允许的数值/日期样格占比上限（达到或超过即判为数据行）
HEADER_DATA_LIKE_MAX_RATIO = 0.5

#: 判为「标题行」时首行允许的最大非空格数
TITLE_MAX_NONEMPTY = 1

#: 跨列合并空位的最小连续长度（1 个孤立空格是普通空表头，不算合并痕迹）
GROUP_HOLE_MIN_RUN = 2

#: 下一行填满合并空位的比例 ≥ 此值 → 判为两级表头
TWO_LEVEL_FILL_MIN = 0.8

#: 下一行填满合并空位的比例 ≤ 此值 → 判为一级表头（空位是真空白列）
ONE_LEVEL_FILL_MAX = 0.2

#: 空表头列的兜底列名前缀（后接 1-based 列号）
FALLBACK_COLUMN_PREFIX = "列"

#: 两级表头扁平化时组名与叶子名的连接符
HEADER_JOINER = "_"

#: 数值样：整数 / 千分位 / 小数 / 百分比 / 科学计数
_NUMERIC_LIKE = re.compile(r"^[+-]?[0-9][0-9,]*(?:\.[0-9]+)?%?(?:[eE][+-]?[0-9]+)?$")

#: 货币样：¥ / ￥ / $ 前缀 + 数字
_CURRENCY_LIKE = re.compile(r"^[¥￥$]\s*[+-]?[0-9][0-9,]*(?:\.[0-9]+)?%?$")

#: 日期样：2026-06-04 / 2026/6/4 / 2026年6月4日 / 04-06-2026（可带时间尾巴）
_DATE_LIKE = re.compile(
    r"^(?:[0-9]{4}[-/年][0-9]{1,2}(?:[-/月][0-9]{1,2}日?)?"
    r"|[0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})(?:[ T].*)?$"
)

#: 布尔字面量（Excel 里的 TRUE/FALSE 是数据，不是列名）
_BOOLEAN_LITERAL = frozenset({"true", "false"})


class ExcelLayoutError(ValueError):
    """Excel 表头结构无法可靠判定（宁可拒绝，不做静默错位）。

    ``reason`` 是稳定的机器可读码，``detail`` 是给人看的补充（列数、填充率等）。
    调用方（写入端 / 读取端）负责把它翻成用户可见的失败原因。
    """

    _MESSAGES = {
        "no_header_row": "无法可靠探测表头：首行不符合表头特征（可能是没有表头的纯数据文件）",
        "ambiguous_header_levels": "无法可靠探测表头：疑似两级表头，但下一行未填满跨列合并位，一级/两级判不准",
    }

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        message = self._MESSAGES.get(reason, "Excel 表头结构无法可靠识别")
        if detail:
            message = f"{message}（{detail}）"
        super().__init__(message)


@dataclass(frozen=True)
class ExcelSheetLayout:
    """单个 sheet 的探测结果（行号均为**绝对行号**）。"""

    header_row: int
    header_levels: int
    data_start_row: int
    has_title_row: bool
    headers: tuple[str, ...]
    notes: tuple[str, ...] = ()

    @property
    def source(self) -> str:
        """表头来源摘要（写日志 / 报告用）。"""
        parts = [f"header_row={self.header_row}", f"levels={self.header_levels}"]
        if self.has_title_row:
            parts.append("title=1")
        return ", ".join(parts)


def normalize_cell(value: Any) -> str:
    """单元格归一化：``None``/``NaN``/``NaT``/纯空白 → ``""``，其余 ``str(v).strip()``。"""
    if value is None:
        return ""
    if isinstance(value, float) and value != value:  # NaN
        return ""
    text = str(value).strip()
    if text.lower() in ("nan", "none", "nat"):
        return ""
    return text


def normalize_row(row: Sequence[Any]) -> list[str]:
    """整行归一化。"""
    return [normalize_cell(cell) for cell in row]


def is_blank_row(row: Sequence[str]) -> bool:
    """整行无任何非空单元格。"""
    return all(not cell for cell in row)


def _looks_data_like(text: str) -> bool:
    """该单元格更像数据而不是列名（数值 / 货币 / 日期 / 布尔）。"""
    if not text:
        return False
    if text.lower() in _BOOLEAN_LITERAL:
        return True
    return bool(
        _NUMERIC_LIKE.match(text)
        or _CURRENCY_LIKE.match(text)
        or _DATE_LIKE.match(text)
    )


def _is_header_like(row: Sequence[str]) -> bool:
    """非空格足够多、且数值/日期样格占比低于阈值 → 像表头。"""
    cells = [cell for cell in row if cell]
    if len(cells) < MIN_HEADER_CELLS:
        return False
    data_like = sum(1 for cell in cells if _looks_data_like(cell))
    return data_like / len(cells) < HEADER_DATA_LIKE_MAX_RATIO


def _long_blank_runs(row: Sequence[str]) -> list[tuple[int, int]]:
    """返回长度 ≥ ``GROUP_HOLE_MIN_RUN`` 的连续空格串区间 ``[start, end)``。"""
    runs: list[tuple[int, int]] = []
    start: Optional[int] = None
    for index, cell in enumerate(row):
        if cell:
            if start is not None and index - start >= GROUP_HOLE_MIN_RUN:
                runs.append((start, index))
            start = None
        elif start is None:
            start = index
    if start is not None and len(row) - start >= GROUP_HOLE_MIN_RUN:
        runs.append((start, len(row)))
    return runs


def flatten_headers(header_rows: Sequence[Sequence[str]], width: int) -> tuple[str, ...]:
    """把 1~2 行表头扁平化成 ``width`` 个唯一、非空的列名。

    两级时组行向右填充（合并单元格），叶子名非空且与组名不同则拼成 ``组_子``。
    空名兜底 ``列{i+1}``；重名追加 ``_2`` / ``_3``（读取端 records 键必须唯一）。
    """
    if not header_rows:
        return tuple(f"{FALLBACK_COLUMN_PREFIX}{i + 1}" for i in range(width))

    if len(header_rows) == 1:
        source = list(header_rows[0])
        names = [source[i] if i < len(source) else "" for i in range(width)]
    else:
        top, leaf = list(header_rows[0]), list(header_rows[1])
        names = []
        last_top = ""
        for i in range(width):
            top_cell = top[i] if i < len(top) else ""
            leaf_cell = leaf[i] if i < len(leaf) else ""
            if top_cell:
                last_top = top_cell
            else:
                top_cell = last_top
            if top_cell and leaf_cell and leaf_cell != top_cell:
                names.append(f"{top_cell}{HEADER_JOINER}{leaf_cell}")
            elif top_cell:
                names.append(top_cell)
            elif leaf_cell:
                names.append(leaf_cell)
            else:
                names.append("")

    used: dict[str, int] = {}
    result: list[str] = []
    for index, name in enumerate(names):
        candidate = name.strip() or f"{FALLBACK_COLUMN_PREFIX}{index + 1}"
        if candidate in used:
            suffix = used[candidate] + 1
            unique = f"{candidate}_{suffix}"
            while unique in used:
                suffix += 1
                unique = f"{candidate}_{suffix}"
            used[unique] = 1
            candidate = unique
        used[candidate] = 1
        result.append(candidate)
    return tuple(result)


def detect_sheet_layout(rows: Sequence[Sequence[Any]]) -> Optional[ExcelSheetLayout]:
    """探测单个 sheet 的表头结构。

    - 返回 ``None``：空 sheet（去尾部空行后没有任何非空单元格）——调用方按空表处理；
    - 抛 :class:`ExcelLayoutError`：表头结构判不准（拒绝，不做静默错位）；
    - 其余：返回带**绝对行号**与唯一列名的 :class:`ExcelSheetLayout`。
    """
    grid = [normalize_row(row) for row in rows] if rows else []
    if not grid:
        return None

    end = len(grid)
    while end > 0 and is_blank_row(grid[end - 1]):
        end -= 1
    grid = grid[:end]
    if not grid:
        return None

    start = 0
    while start < len(grid) and is_blank_row(grid[start]):
        start += 1
    if start >= len(grid):
        return None

    body = grid[start:]
    width = max(len(row) for row in body)

    notes: list[str] = []
    if start:
        notes.append(f"跳过前导空行 {start} 行")

    # 单列表格（每一行最多 1 个非空格）：结构没有歧义——首行即表头、其余是数据。
    # 与「单列散文」的区分靠行宽：散文 sheet 里总有某些行有 2 个以上非空格
    # （如 操作指南 的「步骤1 / 说明」两列），因此不会走到这里，仍按拒绝处理。
    if max(len([cell for cell in row if cell]) for row in body) <= 1:
        first_cell = next((cell for cell in body[0] if cell), "")
        if not first_cell or _looks_data_like(first_cell):
            raise ExcelLayoutError(
                "no_header_row",
                f"第 {start + 1} 行唯一的非空值是数值/日期，单列数据无表头",
            )
        notes.append("单列表格（首行即表头）")
        return ExcelSheetLayout(
            header_row=start,
            header_levels=1,
            data_start_row=start + 1,
            has_title_row=False,
            headers=flatten_headers([body[0]], width),
            notes=tuple(notes),
        )

    header_index = 0
    has_title = False
    if (
        len([cell for cell in body[0] if cell]) == TITLE_MAX_NONEMPTY
        and not _looks_data_like(next((cell for cell in body[0] if cell), ""))
        and len(body) > 1
        and _is_header_like(body[1])
    ):
        has_title = True
        header_index = 1
        notes.append("首行为标题行")

    candidate = body[header_index]
    if not _is_header_like(candidate):
        preview = "、".join([cell for cell in candidate if cell][:3]) or "（整行空）"
        raise ExcelLayoutError(
            "no_header_row",
            f"第 {start + header_index + 1} 行前几个非空值为 {preview}",
        )

    levels = 1
    runs = _long_blank_runs(candidate)
    next_row = body[header_index + 1] if header_index + 1 < len(body) else None
    if runs and next_row is not None and _is_header_like(next_row):
        holes = [i for begin, finish in runs for i in range(begin, finish)]
        filled = sum(1 for i in holes if i < len(next_row) and next_row[i])
        fill_ratio = filled / len(holes)
        if fill_ratio >= TWO_LEVEL_FILL_MIN:
            levels = 2
            notes.append(f"两级表头（合并空位填充率 {fill_ratio:.2f}）")
        elif fill_ratio <= ONE_LEVEL_FILL_MAX:
            notes.append(f"一级表头，跨列空位未被子行填充（填充率 {fill_ratio:.2f}）")
        else:
            raise ExcelLayoutError(
                "ambiguous_header_levels",
                f"第 {start + header_index + 1} 行有 {len(holes)} 个跨列空位，"
                f"下一行只填充 {fill_ratio:.0%}",
            )

    header_rows = body[header_index : header_index + levels]
    headers = flatten_headers(header_rows, width)
    blank_columns = sum(
        1 for i, name in enumerate(headers) if name == f"{FALLBACK_COLUMN_PREFIX}{i + 1}"
    )
    if blank_columns:
        notes.append(f"{blank_columns} 个空表头列按「{FALLBACK_COLUMN_PREFIX}N」兜底")

    header_row = start + header_index
    layout = ExcelSheetLayout(
        header_row=header_row,
        header_levels=levels,
        data_start_row=header_row + levels,
        has_title_row=has_title,
        headers=headers,
        notes=tuple(notes),
    )
    return layout


def data_rows(rows: Sequence[Sequence[Any]], layout: ExcelSheetLayout) -> list[list[str]]:
    """按探测结果取数据行：逐格归一化、列宽对齐表头、丢弃整行全空的行。

    写入端与读取端都调这个函数，保证同一文件两侧得到逐项一致的行列语义。
    """
    width = len(layout.headers)
    result: list[list[str]] = []
    for raw in rows[layout.data_start_row :]:
        cells = [normalize_cell(cell) for cell in raw]
        if len(cells) < width:
            cells.extend([""] * (width - len(cells)))
        else:
            cells = cells[:width]
        if all(not cell for cell in cells):
            continue
        result.append(cells)
    return result


def format_layout(layout: ExcelSheetLayout) -> str:
    """把探测结果压成一行字符串，供 chunk metadata / 日志 / 对账报告使用。"""
    return (
        f"header_row={layout.header_row},levels={layout.header_levels},"
        f"title={int(layout.has_title_row)},data_start={layout.data_start_row}"
    )
