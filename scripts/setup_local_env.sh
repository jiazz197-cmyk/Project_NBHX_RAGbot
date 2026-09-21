#!/usr/bin/env bash
# 一键搭建「项目内自包含」开发环境：
#   <repo>/.venv          后端 Python 虚拟环境（含 pip）
#   <repo>/.cache/**      所有下载 / 编译 / 模型缓存
#   <repo>/frontend/node_modules  前端依赖（pnpm workspace 默认位置）
#
# 用法：
#   bash scripts/setup_local_env.sh                 # 主应用依赖（默认，不含 RAG 栈）
#   bash scripts/setup_local_env.sh --with-rag      # 主应用 + RAG 栈（过渡期本地开发用）
#   bash scripts/setup_local_env.sh --with-frontend # 顺带装前端依赖
#   bash scripts/setup_local_env.sh --frontend-only # 仅前端依赖
#   bash scripts/setup_local_env.sh --upgrade       # 按 requirements.txt 重装/升级
#
# 关于 RAG：LangChain/LlamaIndex 那一套已移出 requirements.txt，集中到
# requirements-rag.txt，随「RAG 独立容器」部署。但仓库里的 RAG 代码尚未搬走
# （main.py 与 app/api/v1/registry.py 仍会 import langchain/llama_index），
# 所以**过渡期本地要跑起完整应用，需要加 --with-rag**。
#
# 可用环境变量覆盖：
#   PYTHON_BIN   构建 venv 的解释器（默认 3.12，交给 uv 解析）
#   TORCH_INDEX  torch/torchvision 的 wheel 源（默认 cu130）
#
# 幂等：重复执行只会补齐缺失依赖。

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# shellcheck source=scripts/env.sh
source "${SCRIPT_DIR}/env.sh" -q

# 索引源与缓存目录均来自 scripts/env.sh（见上面的 source）
PYTHON_BIN="${PYTHON_BIN:-3.12}"

WITH_FRONTEND=0
FRONTEND_ONLY=0
UPGRADE=0
WITH_RAG=0
for arg in "$@"; do
  case "${arg}" in
    --with-frontend) WITH_FRONTEND=1 ;;
    --with-rag)      WITH_RAG=1 ;;
    --frontend-only) FRONTEND_ONLY=1 ;;
    --upgrade)       UPGRADE=1 ;;
    -h|--help)       sed -n '2,24p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "未知参数: ${arg}（-h 查看用法）" >&2; exit 2 ;;
  esac
done

log() { printf '\033[1;34m[setup]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[setup]\033[0m %s\n' "$*" >&2; exit 1; }

mkdir -p "${PROJECT_ROOT}/.cache"

# ---------------------------------------------------------------- 后端
setup_backend() {
  local uv_bin
  if uv_bin="$(command -v uv 2>/dev/null)"; then
    :
  elif [[ -x "${HOME}/.local/bin/uv" ]]; then
    uv_bin="${HOME}/.local/bin/uv"
  else
    die "未找到 uv。安装： curl -LsSf https://astral.sh/uv/install.sh | sh   （或用 --frontend-only 只装前端）"
  fi

  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    log "创建虚拟环境 ${VENV_DIR}（python ${PYTHON_BIN}）"
    "${uv_bin}" venv --python "${PYTHON_BIN}" --seed "${VENV_DIR}"
  else
    log "复用已有虚拟环境 ${VENV_DIR}"
  fi

  local -a uv_args=(pip install --python "${VENV_DIR}/bin/python" -r "${PROJECT_ROOT}/requirements.txt")
  # torch==*+cu130 不在 PyPI 上，需额外索引
  uv_args+=(--index-url "${PYPI_INDEX}")
  uv_args+=(--extra-index-url "${TORCH_INDEX}")
  # torch 索引里也有 fastapi 等同名包，必须允许跨索引择优；
  # 否则 uv 默认的「命中即锁定首个索引」会把 fastapi==0.116.1 判成无解。
  uv_args+=(--index-strategy unsafe-best-match)
  # 排除 nvidia-nccl-cu12（会把 torch 的 cu13 NCCL 覆盖掉导致 import torch 崩，详见该文件）
  uv_args+=(--overrides "${PROJECT_ROOT}/requirements-overrides.txt")
  if [[ "${UPGRADE}" == "1" ]]; then
    uv_args+=(--upgrade)
  fi

  log "安装后端依赖（requirements.txt，缓存 ${UV_CACHE_DIR}）"
  "${uv_bin}" "${uv_args[@]}"

  if [[ "${WITH_RAG}" == "1" ]]; then
    # 过渡期：仓库里的 RAG 代码还没搬进独立容器，本地要跑完整应用就得装上这套
    log "安装 RAG 栈（requirements-rag.txt，过渡期）"
    "${uv_bin}" pip install --python "${VENV_DIR}/bin/python" -r "${PROJECT_ROOT}/requirements-rag.txt" \
      --index-url "${PYPI_INDEX}" --index-strategy unsafe-best-match
  else
    log "跳过 RAG 栈（--with-rag 可安装；不装则 main.py / api registry 会因 RAG 代码 import 失败）"
  fi

  # 测试工具单独一份（部署环境不需要），版本与 .gitlab-ci.yml 的 pytest job 对齐
  log "安装测试工具（requirements-dev.txt）"
  "${uv_bin}" pip install --python "${VENV_DIR}/bin/python" --index-url "${PYPI_INDEX}" \
    -r "${PROJECT_ROOT}/requirements-dev.txt"

  log "后端自检"
  # 用 importlib.metadata 查版本，避免 import 副作用：import paddle 会去创建
  # ~/.cache/paddle（路径只跟 HOME 走），在只读 HOME / 受限环境下会直接抛 PermissionError，
  # 但那与「装没装上」无关。
  "${VENV_DIR}/bin/python" - <<'PY'
from importlib.metadata import PackageNotFoundError, version

for dist in ("fastapi", "sqlalchemy", "torch", "paddlepaddle-gpu", "paddleocr", "uvicorn"):
    try:
        print(f"  ok      {dist:18s} {version(dist)}")
    except PackageNotFoundError:
        print(f"  MISSING {dist:18s} (requirements.txt 里应已锁定)")
PY
}

# ---------------------------------------------------------------- 前端
# 仓库在 frontend/package.json 锁定 pnpm@8，但 8.0.0 自身有 ERR_INVALID_THIS bug
# （node 20/24 均复现，8.6+ 才修），所以用同属 8.x 的 8.15.9。
# 不要用本机全局 pnpm 10/12：它们读不了 lockfile v6（ERR_PNPM_LOCKFILE_BREAKING_CHANGE），
# 加 --force 会把 pnpm-lock.yaml 从 v6 升到 v9。
PNPM_VERSION="${PNPM_VERSION:-8.15.9}"

setup_frontend() {
  command -v corepack >/dev/null || die "未找到 corepack（随 node 一起发布，请检查 node 安装）"
  log "安装前端依赖（pnpm@${PNPM_VERSION} + node $(node -v)，store: frontend/.pnpm-store）"
  ( cd "${PROJECT_ROOT}/frontend" \
    && COREPACK_HOME="${COREPACK_HOME}" corepack "pnpm@${PNPM_VERSION}" install --frozen-lockfile )
}

if [[ "${FRONTEND_ONLY}" == "1" ]]; then
  setup_frontend
else
  setup_backend
  if [[ "${WITH_FRONTEND}" == "1" ]]; then
    setup_frontend
  fi
fi

log "完成。后续在任意 shell 中执行： source scripts/env.sh"
