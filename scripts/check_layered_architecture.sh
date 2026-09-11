#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Clean Architecture 分层检测（8 规则，锁折叠终态）。
#
# 依赖方向（向内为尊）：domain ◄ ports ◄ usecases ◄ adapters ◄ api(组合根) + main.py
#   - ragsystem / infrastructure / workers / schemas 已物理折叠进 app/adapters/
#   - core / models 本轮 deferred：暂作外层工具岛，不纳入内层禁列（后续 issue 处理）
#
# 内层（domain/ports/usecases）不得直接 import 外层实现；driven 适配器不得
# 反向依赖 driving 侧（workers）。api/main 作为组合根可装配任意 adapter。

fail() { echo "[layer-check] FAILED: $1"; exit 1; }

echo "[layer-check] 1/8 usecases must not import adapters/infrastructure/ragsystem/workers/api"
if grep -Rq --include="*.py" -E '^[[:space:]]*(from|import) app\.(adapters|infrastructure|ragsystem|workers|api)([.[:space:]]|$)' app/usecases; then
  fail "app/usecases must depend on ports only (no concrete adapters)."
fi

echo "[layer-check] 2/8 ports must not import outer implementations"
if grep -Rq --include="*.py" -E '^[[:space:]]*(from|import) app\.(adapters|usecases|infrastructure|ragsystem|workers|api)([.[:space:]]|$)' app/ports; then
  fail "app/ports must stay pure (Protocol/DTO only)."
fi

echo "[layer-check] 3/8 domain must not import outer layers"
if grep -Rq --include="*.py" -E '^[[:space:]]*(from|import) app\.(adapters|usecases|infrastructure|ragsystem|workers|api)([.[:space:]]|$)' app/domain; then
  fail "app/domain must not depend on outer layers."
fi

echo "[layer-check] 4/8 ports must contain Protocol definitions"
if ! grep -Rq --include="*.py" "Protocol" app/ports; then
  fail "Protocol not found under app/ports."
fi

echo "[layer-check] 5/8 no imports of folded top-level packages (ragsystem|infrastructure|workers|schemas)"
if grep -Rq --include="*.py" -E '^[[:space:]]*(from|import) app\.(ragsystem|infrastructure|workers|schemas)([.[:space:]]|$)' app/ main.py; then
  fail "folded packages must be reached via app.adapters.{ragsystem,workers,web}; a bare top-level import remains."
fi

echo "[layer-check] 6/8 api must not import app.adapters.ragsystem directly (use facade app.adapters.retriever)"
if grep -Rq --include="*.py" -E '^[[:space:]]*(from|import) app\.adapters\.ragsystem([.[:space:]]|$)' app/api; then
  fail "app/api must go through the app.adapters.retriever facade, not app.adapters.ragsystem."
fi

echo "[layer-check] 7/8 app.adapters.web only importable by app/api and itself"
if web_violators=$(grep -Rl --include="*.py" -E '^[[:space:]]*(from|import) app\.adapters\.web([.[:space:]]|$)' app/ main.py \
    | grep -v '^app/api/' | grep -v '^app/adapters/web/'); then
  fail "app.adapters.web imported outside app/api and app/adapters/web: $web_violators"
fi

echo "[layer-check] 8/8 app.adapters.workers only importable by app/api, main.py and itself"
if workers_violators=$(grep -Rl --include="*.py" -E '^[[:space:]]*(from|import) app\.adapters\.workers([.[:space:]]|$)' app/ main.py \
    | grep -v '^app/api/' | grep -v '^app/adapters/workers/' | grep -v '^main\.py$'); then
  fail "app.adapters.workers imported outside app/api, main.py and app/adapters/workers: $workers_violators"
fi

echo "[layer-check] PASSED"
