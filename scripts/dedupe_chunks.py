#!/usr/bin/env python3
"""chunk 内容级重复的存量对账与清理——issue #17 的运维入口。

**默认 dry-run，不改库**；只有显式 ``--apply`` 才删除。清理规则：每个「内容
完全相同」的组（``md5(text)`` 相同）保留 ``MIN(id)``（最早入库的那条），删除其余。

判重口径与写入端一致：PG 侧现算 ``md5(text)``（``app/domain/knowledge/chunk_identity``
里定义了同一算法），因此**不需要** history 数据里有 ``content_hash``。

用法：
    bash scripts/dev.sh docker shell            # 或 source scripts/env.sh（有 .venv 时）
    python scripts/dedupe_chunks.py --dry-run                     # 只报告（默认行为）
    python scripts/dedupe_chunks.py --dry-run --limit 10          # 明细只看前 10 组
    python scripts/dedupe_chunks.py --apply                       # 真删（删前仍会先预演一次）
    python scripts/dedupe_chunks.py --apply --ensure-index        # 顺带对齐指纹索引
    python scripts/dedupe_chunks.py --apply --purge-empty         # 顺带删空/纯空白 chunk
    python scripts/dedupe_chunks.py --collection knowledge_chunks # 只处理一个集合

退出码：0 正常；1 ``--apply`` 的实际删除量与预演不一致（需要人工复核）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.adapters.knowledge.chunk_fingerprint import (  # noqa: E402
    DuplicatePlan,
    count_empty_chunks,
    delete_duplicate_rows,
    ensure_fingerprint_indexes,
    plan_duplicate_groups,
    plan_fingerprint_index,
    purge_empty_chunks,
)
from app.core.database import engine  # noqa: E402
from app.domain.knowledge.collections import (  # noqa: E402
    EXCEL_DB_COLLECTION_NAME,
    KNOWLEDGE_COLLECTION_NAME,
)

DEFAULT_COLLECTIONS = (KNOWLEDGE_COLLECTION_NAME, EXCEL_DB_COLLECTION_NAME)


def _print_plan(plan: DuplicatePlan, limit: int) -> None:
    print(f"\n=== {plan.collection} ({plan.table}) ===")
    print(
        f"  总块数 {plan.total_rows}｜重复组 {plan.group_count}｜冗余块 {plan.redundant_rows}"
    )
    if not plan.has_duplicates:
        print("  [info] 无重复块")
        return
    print(f"  前 {min(limit, len(plan.groups))} 组明细（保留 MIN(id)，其余删除）：")
    for group in plan.groups:
        kept, dropped = group.ids[0], group.ids[1:]
        sources = "、".join(group.sources) or "(无 source)"
        print(
            f"    md5={group.digest} x{group.count}｜保留 id={kept}｜"
            f"删除 id={dropped}｜来源: {sources}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="chunk 内容级重复对账与清理（issue #17）")
    parser.add_argument(
        "--collection",
        action="append",
        dest="collections",
        help=f"集合名，可重复；默认 {' / '.join(DEFAULT_COLLECTIONS)}",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--apply", action="store_true", help="真正删除（默认只预演，不改库）"
    )
    mode_group.add_argument(
        "--dry-run", action="store_true", help="只预演不改库（默认行为，显式写法）"
    )
    parser.add_argument(
        "--ensure-index",
        action="store_true",
        help="确保 md5(text) 表达式索引存在（写入端预检不再全表扫描）",
    )
    parser.add_argument(
        "--purge-empty", action="store_true", help="顺带删除空 / 纯空白 chunk"
    )
    parser.add_argument("--limit", type=int, default=20, help="明细打印组数上限（默认 20）")
    parser.add_argument(
        "--concurrently",
        action="store_true",
        help="建索引走 CREATE INDEX CONCURRENTLY（生产大表用，不锁写）",
    )
    args = parser.parse_args()

    collections = tuple(dict.fromkeys(args.collections or DEFAULT_COLLECTIONS))
    mode = "APPLY（会改库）" if args.apply else "DRY-RUN（不改库）"
    print(f"[mode] {mode}｜集合: {', '.join(collections)}")

    exit_code = 0
    for collection in collections:
        plan = plan_duplicate_groups(collection, engine=engine, limit=args.limit)
        _print_plan(plan, args.limit)

        empty_count = count_empty_chunks(collection, engine=engine)
        if empty_count:
            print(f"  空/纯空白 chunk: {empty_count} 条")
            if args.purge_empty and args.apply:
                removed = purge_empty_chunks(collection, engine=engine)
                print(f"  [success] 已删除空 chunk {removed} 条")
            elif args.purge_empty:
                print("  [plan] --apply 后删除这些空 chunk")
            else:
                print("  [info] 加 --purge-empty --apply 可删除（检索端早已丢弃它们）")

        if args.ensure_index:
            if args.apply:
                ensured = ensure_fingerprint_indexes(
                    [collection], engine=engine, concurrently=args.concurrently
                )
                print(f"  [success] 指纹索引就绪: {', '.join(ensured) or '（集合表不存在）'}")
            else:
                print(f"  [plan] {plan_fingerprint_index(collection, concurrently=args.concurrently)}")

        if not plan.has_duplicates:
            continue

        if not args.apply:
            print(f"  [plan] --apply 后删除 {plan.redundant_rows} 条冗余块")
            continue

        deleted = delete_duplicate_rows(collection, engine=engine)
        print(f"  [success] 已删除冗余块 {deleted} 条（预演 {plan.redundant_rows} 条）")
        if deleted != plan.redundant_rows:
            print(
                "  [warning] 实际删除量与预演不一致——可能有并发写入，请重新跑一次 --dry-run 复核"
            )
            exit_code = 1

        after = plan_duplicate_groups(collection, engine=engine, limit=1)
        print(
            f"  [verify] 处理后：总块数 {after.total_rows}｜重复组 {after.group_count}"
            f"｜冗余块 {after.redundant_rows}"
        )
        if after.has_duplicates:
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
