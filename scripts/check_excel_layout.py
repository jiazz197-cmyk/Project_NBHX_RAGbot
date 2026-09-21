#!/usr/bin/env python3
"""Excel 读写口径对账（issue #23 的运维入口）——**只读**，不改库、不改文件。

比对「库里已经落下的 chunk」与「用当前代码重新解析源文件得到的口径」：

- 旧口径：``ExcelParser`` 当年把第 0 行当表头、``df.iloc[1:]`` 当数据行；
- 新口径：``app.domain.knowledge.excel_layout`` 的结构探测（标题行 / 一级 / 两级表头）。

对每个 ``(文件, sheet)`` 输出：库内块数、空标签（``：值``）证据块数、旧→新表头差异，
并汇总「需要重灌的文件与候选块数」。写入口径变化 = 该 sheet 的 chunk 文本会变
（表头名变、数据起始行变、或该 sheet 现在被拒绝），重灌后才会一致。

用法（在 dev 容器或有 .env 的宿主环境里跑）：

    bash scripts/dev.sh docker shell
    python scripts/check_excel_layout.py                      # 全部文件，打印报告
    python scripts/check_excel_layout.py --out /tmp/report.md # 同时落盘 markdown
    python scripts/check_excel_layout.py --file documents/xxx.xlsx
    python scripts/check_excel_layout.py --json               # 机器可读

退出码：0 = 无口径变化；1 = 存在需要重灌的文件（供人工/流水线判定）。
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.adapters.doc_processing.doc_reader import _read_excel_frames  # noqa: E402
from app.adapters.knowledge.collection_tables import physical_table  # noqa: E402
from app.core.database import engine  # noqa: E402
from app.core.storage import save_file_from_minio  # noqa: E402
from app.domain.knowledge.collections import EXCEL_DB_COLLECTION_NAME  # noqa: E402
from app.domain.knowledge.excel_layout import (  # noqa: E402
    ExcelLayoutError,
    data_rows,
    detect_sheet_layout,
    format_layout,
    normalize_row,
)

#: 旧口径的空标签痕迹：``：值``（表头为空时 ``f"{header}：{value}"`` 的结果）
_EMPTY_LABEL = re.compile(r"(?:^|, )：")

#: 差异示例的截断长度（字符）
_DIFF_PREVIEW = 70

_STATUS_LABEL = {
    "ok": "✓ 口径未变",
    "changed": "⚠ 口径变化",
    "refused": "✗ 现在被拒绝",
    "empty": "· 空 sheet",
    "missing": "? 源文件里没有该 sheet",
}


def _render_row(headers: List[str], row: List[str]) -> str:
    """复刻 ``ExcelHeaderPreservingSplitter._format_row_with_headers`` 的渲染。"""
    parts = []
    for index, header in enumerate(headers):
        value = row[index].strip() if index < len(row) and row[index] is not None else ""
        if value:
            parts.append(f"{header}：{value}")
    return ", ".join(parts)


def _legacy_headers(raw_rows: List[List[Any]]) -> List[str]:
    """旧口径表头：第 0 行原样（空单元格保持空串）。"""
    return normalize_row(raw_rows[0]) if raw_rows else []


def _legacy_collapsed_columns(legacy_headers: List[str]) -> int:
    """旧口径下读取端会**静默丢列**的数量。

    读取端把列名当 ``dict`` 的 key（``to_dict(orient='records')``）：空列名与重名列
    会互相覆盖。这里量的是「表头格数 − 去重后的非空列名数」，即最坏情况下的丢列数
    （真实文件：Startup 38 列里 14 列被吞、全新项目 IBP/内控/SOP 各吞 1 列）。
    """
    if not legacy_headers:
        return 0
    distinct = len({header for header in legacy_headers if header})
    return len(legacy_headers) - distinct


def _legacy_rendered_rows(raw_rows: List[List[Any]], headers: List[str]) -> List[str]:
    """旧口径的数据行渲染（``df.iloc[1:]`` → ``fillna('')`` → 逐行格式化）。

    与切分器一致地丢掉渲染为空的行（``split_excel_data`` 里 ``if not row_text.strip(): continue``），
    否则「丢空行」本身会被误报成口径变化。
    """
    rendered = []
    for raw in raw_rows[1:]:
        row = normalize_row(raw)
        if len(row) < len(headers):
            row.extend([""] * (len(headers) - len(row)))
        text = _render_row(headers, row)
        if text:
            rendered.append(text)
    return rendered


def _describe_diff(
    legacy_headers: List[str],
    headers: List[str],
    legacy_rows: List[str],
    new_rows: List[str],
) -> str:
    """把「为什么口径变了」压成一句话（行数 + 首个差异行示例）。"""
    parts = []
    if len(legacy_headers) != len(headers):
        parts.append(f"表头列 {len(legacy_headers)}→{len(headers)}")
    elif legacy_headers != headers:
        parts.append("表头名变化")
    if len(legacy_rows) != len(new_rows):
        parts.append(f"数据行 {len(legacy_rows)}→{len(new_rows)}")
    for old, new in zip(legacy_rows, new_rows):
        if old != new:
            parts.append(
                f"首个差异行「{old[:_DIFF_PREVIEW]}」→「{new[:_DIFF_PREVIEW]}」"
            )
            break
    else:
        if len(legacy_rows) != len(new_rows):
            tail = new_rows[len(legacy_rows) :] or legacy_rows[len(new_rows) :]
            if tail:
                parts.append(f"尾部行差异「{tail[0][:_DIFF_PREVIEW]}」")
    return "；".join(parts)


def _load_stored_rows() -> List[Dict[str, Any]]:
    """库内 excel chunk 的定位信息与文本（含空标签证据）。"""
    table = physical_table(EXCEL_DB_COLLECTION_NAME)
    query = text(
        "SELECT COALESCE(metadata_->>'file_name', metadata_->>'source', '') AS file_name,"
        "       COALESCE(metadata_->>'minio_object_path', '') AS object_path,"
        "       COALESCE(metadata_->>'sheet_name', '') AS sheet_name,"
        "       text AS chunk_text"
        f" FROM {table}"
    )
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(query)]


def _group_stored(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    files: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        entry = files.setdefault(
            row["object_path"],
            {"file_name": row["file_name"], "sheets": {}},
        )
        sheet = entry["sheets"].setdefault(
            row["sheet_name"], {"chunks": 0, "empty_label_chunks": 0}
        )
        sheet["chunks"] += 1
        if _EMPTY_LABEL.search(row["chunk_text"] or ""):
            sheet["empty_label_chunks"] += 1
    return files


def _audit_file(
    object_path: str,
    stored: Dict[str, Any],
    temp_path: Path,
) -> Dict[str, Any]:
    frames = _read_excel_frames(str(temp_path), sheet_name=None)
    sheet_names = list(dict.fromkeys(list(frames.keys()) + list(stored["sheets"].keys())))

    sheets: List[Dict[str, Any]] = []
    for sheet_name in sheet_names:
        db_sheet = stored["sheets"].get(sheet_name, {"chunks": 0, "empty_label_chunks": 0})
        frame = frames.get(sheet_name)
        if frame is None:
            sheets.append(
                {
                    "sheet_name": sheet_name,
                    "status": "missing",
                    "chunks": db_sheet["chunks"],
                    "empty_label_chunks": db_sheet["empty_label_chunks"],
                    "headers": [],
                    "legacy_headers": [],
                    "legacy_collapsed_columns": 0,
                    "layout": "",
                    "reason": "源文件里已无该 sheet（库内块将随重灌消失）",
                    "diff": "",
                }
            )
            continue

        raw_rows = frame.values.tolist()
        legacy_headers = _legacy_headers(raw_rows)
        try:
            layout = detect_sheet_layout(raw_rows)
            refused_reason = ""
        except ExcelLayoutError as exc:
            layout = None
            refused_reason = str(exc)

        diff = ""
        if refused_reason:
            status, headers, layout_text = "refused", [], ""
            diff = "该 sheet 现在不产 chunk（原先有块）"
        elif layout is None:
            status, headers, layout_text = "empty", [], ""
            diff = "空 sheet"
        else:
            headers = list(layout.headers)
            layout_text = format_layout(layout)
            legacy_rows = _legacy_rendered_rows(raw_rows, legacy_headers)
            new_rows = [
                text
                for text in (_render_row(headers, row) for row in data_rows(raw_rows, layout))
                if text
            ]
            status = "ok" if legacy_rows == new_rows else "changed"
            if status == "changed":
                diff = _describe_diff(legacy_headers, headers, legacy_rows, new_rows)

        sheets.append(
            {
                "sheet_name": sheet_name,
                "status": status,
                "chunks": db_sheet["chunks"],
                "empty_label_chunks": db_sheet["empty_label_chunks"],
                "headers": headers,
                "legacy_headers": legacy_headers,
                "legacy_collapsed_columns": _legacy_collapsed_columns(legacy_headers),
                "layout": layout_text,
                "reason": refused_reason,
                "diff": diff,
            }
        )

    affected = sum(
        sheet["chunks"] for sheet in sheets if sheet["status"] not in ("ok", "empty")
    )
    return {
        "file_name": stored["file_name"] or object_path,
        "object_path": object_path,
        "sheets": sheets,
        "affected_chunks": affected,
        "needs_reingest": affected > 0,
    }


def _audit(
    object_paths: Optional[List[str]] = None,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    files = _group_stored(_load_stored_rows())
    targets = object_paths or sorted(files.keys())
    reports: List[Dict[str, Any]] = []
    for object_path in targets:
        stored = files.get(object_path)
        if stored is None:
            reports.append(
                {
                    "file_name": object_path,
                    "object_path": object_path,
                    "sheets": [],
                    "affected_chunks": 0,
                    "needs_reingest": False,
                    "error": "库内没有该对象的 chunk",
                }
            )
            continue
        temp_path = None
        try:
            temp_path = save_file_from_minio(object_path)
            reports.append(_audit_file(object_path, stored, temp_path))
        except Exception as exc:  # noqa: BLE001 - 对账脚本：单文件失败不中断整体
            reports.append(
                {
                    "file_name": stored["file_name"] or object_path,
                    "object_path": object_path,
                    "sheets": [],
                    "affected_chunks": 0,
                    "needs_reingest": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
    return reports


def _render_markdown(reports: List[Dict[str, Any]], limit: int) -> str:
    lines: List[str] = []
    needs = [r for r in reports if r["needs_reingest"]]
    total_chunks = sum(
        sheet["chunks"] for report in reports for sheet in report["sheets"]
    )
    total_empty_labels = sum(
        sheet["empty_label_chunks"] for report in reports for sheet in report["sheets"]
    )
    total_collapsed = sum(
        sheet.get("legacy_collapsed_columns", 0)
        for report in reports
        for sheet in report["sheets"]
    )
    affected = sum(report["affected_chunks"] for report in reports)

    lines.append("## Excel 读写口径对账（issue #23）")
    lines.append("")
    lines.append(
        f"- 源文件 {len(reports)} 个｜库内块 {total_chunks} 条"
        f"｜需重灌文件 {len(needs)} 个 / 候选块 {affected} 条"
    )
    lines.append(
        f"- 旧口径证据：空标签（`：值`）块 {total_empty_labels} 条"
        f"｜读取端会因空/重复列名静默丢列 {total_collapsed} 列（最坏情况）"
    )
    if needs:
        lines.append("- 需重灌：" + "、".join(r["file_name"] for r in needs))
    else:
        lines.append("- 全部文件口径与库内一致，无需重灌")
    lines.append("")

    for report in reports:
        lines.append(f"### {report['file_name']}")
        if report.get("error"):
            lines.append(f"- 读取失败：{report['error']}")
            lines.append("")
            continue
        lines.append(
            f"- 对象：`{report['object_path']}`｜候选受影响块：{report['affected_chunks']}"
        )
        lines.append("")
        lines.append("| sheet | 库内块 | 空标签块 | 旧口径丢列 | 判定 | 差异摘要 |")
        lines.append("|---|---:|---:|---:|---|---|")
        for sheet in report["sheets"][:limit]:
            lines.append(
                f"| {sheet['sheet_name'] or '(默认)'} | {sheet['chunks']} |"
                f" {sheet['empty_label_chunks']} | {sheet.get('legacy_collapsed_columns', 0)} |"
                f" {_STATUS_LABEL.get(sheet['status'], sheet['status'])} |"
                f" {sheet.get('diff') or '-'} |"
            )
        if len(report["sheets"]) > limit:
            lines.append(f"| … | | | | | 共 {len(report['sheets'])} 个 sheet |")
        for sheet in report["sheets"][:limit]:
            if sheet["status"] in ("changed", "refused") and sheet["headers"]:
                lines.append(
                    f"- 新表头（`{sheet['sheet_name']}`）："
                    + "、".join(sheet["headers"][:8])
                )
            if sheet["status"] == "refused":
                lines.append(f"- ✗ `{sheet['sheet_name']}`：{sheet['reason']}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    # 对账报告要和 SQL echo 日志（dev 环境 engine.echo=True）分开：直接关掉 echo，
    # 否则 stdout 里会混进每次查询的 SQL 明细。
    try:
        engine.echo = False
    except Exception:  # noqa: BLE001 - 关不掉也不影响对账结果
        pass
    for name in ("sqlalchemy.engine", "sqlalchemy.engine.Engine"):
        logging.getLogger(name).setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(description="Excel 读写口径对账（issue #23，只读）")
    parser.add_argument("--file", action="append", dest="files", help="只对账指定 MinIO 对象，可重复")
    parser.add_argument("--out", help="把 markdown 报告写到该路径")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--limit", type=int, default=8, help="每文件打印的 sheet 行数上限（默认 8）")
    args = parser.parse_args()

    reports = _audit(args.files, args.limit)

    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        markdown = _render_markdown(reports, args.limit)
        print(markdown)
        if args.out:
            Path(args.out).write_text(markdown + "\n", encoding="utf-8")
            print(f"\n[report] 已写入 {args.out}")

    return 1 if any(report["needs_reingest"] for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
