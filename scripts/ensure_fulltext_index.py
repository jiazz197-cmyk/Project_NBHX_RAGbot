#!/usr/bin/env python3
"""确保字面检索（pg_trgm）扩展与索引存在——issue #16 的运维入口。

启动时（``RETRIEVAL_HYBRID_ENABLED=true``）主应用也会在后台跑一遍同样的
检查，但**大表上建索引会耗时**，生产建议先用本脚本离线执行（默认 CONCURRENTLY，
不阻塞写入），再打开开关。

用法：
    source scripts/env.sh
    python scripts/ensure_fulltext_index.py --dry-run      # 只打印将要执行的 DDL
    python scripts/ensure_fulltext_index.py                # CONCURRENTLY 建索引（推荐）
    python scripts/ensure_fulltext_index.py --no-concurrently   # 事务内建（开发库/小表）

幂等：``CREATE EXTENSION IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``；
重复执行无副作用。无 superuser 权限时扩展创建失败只会告警并跳过——
字面检索退化为顺序扫描，功能不受影响。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.adapters.knowledge.lexical_search import (  # noqa: E402
    ensure_fulltext_indexes,
    plan_fulltext_indexes,
)
from app.core.database import engine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="确保字面检索索引（issue #16）")
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印将要执行的 DDL，不改库"
    )
    parser.add_argument(
        "--no-concurrently",
        action="store_true",
        help="用事务内 CREATE INDEX（开发库/小表）；默认 CONCURRENTLY 不锁写",
    )
    args = parser.parse_args()

    if args.dry_run:
        plans = plan_fulltext_indexes(engine=engine)
        if not plans:
            print("[info] 未发现需要建索引的集合表（data_% 且含 text/node_id 列）")
            return 0
        for table, ddl in plans:
            print(f"[plan] {table}\n       {ddl}")
        print(f"[info] 共 {len(plans)} 张表；去掉 --dry-run 即执行（默认 CONCURRENTLY）")
        return 0

    names = ensure_fulltext_indexes(
        engine=engine, concurrently=not args.no_concurrently
    )
    if not names:
        print("[warning] 没有索引就绪（pg_trgm 不可用或没有集合表）；字面检索将走顺序扫描")
        return 0
    print(f"[success] 已就绪索引 {len(names)} 个：")
    for name in names:
        print(f"  - {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
