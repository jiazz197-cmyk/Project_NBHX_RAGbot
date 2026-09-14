#!/usr/bin/env bash
# 项目命令入口：脚本内部自己 source scripts/env.sh，所以**不用手动 source**。
#
# 用法：
#   bash scripts/dev.sh backend        # 起后端（http://localhost:8000）
#   bash scripts/dev.sh frontend       # 起前端 dev server（http://localhost:8888）
#   bash scripts/dev.sh test [args]    # pytest（默认全量；可跟 -k/-q 等参数）
#   bash scripts/dev.sh guard          # 分层架构守卫
#   bash scripts/dev.sh py <args>      # 用项目 venv 的 python 跑任意命令
#   bash scripts/dev.sh shell          # 开一个已激活环境的交互 shell（之后就不用手动 source）
#
# 说明：`backend` / `frontend` 是前台进程，Ctrl-C 结束。

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 关键：无论调用者在哪个 shell、有没有激活过环境，这里统一把 .venv 与缓存变量设好
# shellcheck source=scripts/env.sh
source "${ROOT}/scripts/env.sh" -q

usage() {
  sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

cmd="${1:-}"
shift || true

case "${cmd}" in
  backend)
    exec python "${ROOT}/main.py"
    ;;
  frontend)
    # 直接进 apps/chat 跑 vite：绕过 turbo（turbo 会把任务交给全局 pnpm 12）
    cd "${ROOT}/frontend/apps/chat"
    exec corepack pnpm dev
    ;;
  test)
    exec python -m pytest "$@"
    ;;
  guard)
    exec bash "${ROOT}/scripts/check_layered_architecture.sh" "$@"
    ;;
  py)
    [[ $# -gt 0 ]] || usage 2
    exec python "$@"
    ;;
  shell)
    echo "[dev] 已激活 ${VIRTUAL_ENV}；退出该 shell 后回到原环境"
    exec "${SHELL:-/bin/bash}" -i
    ;;
  ""|-h|--help|help)
    usage 0
    ;;
  *)
    echo "未知子命令: ${cmd}" >&2
    usage 2
    ;;
esac
