#!/usr/bin/env bash
# 项目自包含环境变量（backend + frontend 的依赖与缓存全部落在仓库目录内，不写 $HOME）。
#
# 用法：
#   source scripts/env.sh          # 激活 .venv 并把所有缓存指向 <repo>/.cache
#   source scripts/env.sh -q       # 同上，但不打印提示
#
# 约定（见 CLAUDE.md「本地环境」一节）：
#   <repo>/.venv/        Python 虚拟环境（唯一后端解释器）
#   <repo>/.cache/       所有包管理器 / 模型 / 编译缓存
#   <repo>/frontend/node_modules/   前端依赖（pnpm workspace，天然在仓库内）
#
# 注意：本脚本只影响当前 shell；不会修改 ~/.bashrc、~/.config/pip/pip.conf 等全局配置。

# shellcheck shell=bash

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PROJECT_ROOT

# ---------------------------------------------------------------- Python 虚拟环境
export VENV_DIR="${VENV_DIR:-${PROJECT_ROOT}/.venv}"
if [[ -x "${VENV_DIR}/bin/python" ]]; then
  export VIRTUAL_ENV="${VENV_DIR}"
  case ":${PATH}:" in
    *":${VENV_DIR}/bin:"*) ;;
    *) export PATH="${VENV_DIR}/bin:${PATH}" ;;
  esac
  # 不读取 ~/.local/lib/python3.12/site-packages，保证依赖只来自 .venv
  export PYTHONNOUSERSITE=1
fi

# ---------------------------------------------------------------- 缓存根目录
# XDG_CACHE_HOME 兜住没有专门变量的工具；下面再逐个显式覆盖。
export XDG_CACHE_HOME="${PROJECT_ROOT}/.cache"

# Python 包管理器
export UV_CACHE_DIR="${PROJECT_ROOT}/.cache/uv"
export UV_PYTHON_INSTALL_DIR="${PROJECT_ROOT}/.cache/uv/python"
export PIP_CACHE_DIR="${PROJECT_ROOT}/.cache/pip"

# 包索引：torch==*+cu130 与 paddlepaddle-gpu 不在 PyPI 上，需额外索引源。
# 统一在这里定义，setup_local_env.sh 与手写 pip/uv 命令共用。
export PYPI_INDEX="${PYPI_INDEX:-https://pypi.org/simple}"
export TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu130}"
export PADDLE_INDEX="${PADDLE_INDEX:-https://www.paddlepaddle.org.cn/packages/stable/cu126/}"
export PIP_EXTRA_INDEX_URL="${TORCH_INDEX} ${PADDLE_INDEX}"

# Node / pnpm
# 注意：pnpm 的 store / cache 由 frontend/.npmrc（pnpm ≤10）与
# frontend/pnpm-workspace.yaml（pnpm 11+）固定在 frontend/.pnpm-store 与
# frontend/.pnpm-cache，这里只兜底 npm 自身与 corepack。
export npm_config_cache="${PROJECT_ROOT}/.cache/npm"
export npm_config_node_gyp_cache_dir="${PROJECT_ROOT}/.cache/node-gyp"
export COREPACK_HOME="${PROJECT_ROOT}/.cache/corepack"
export TURBO_CACHE_DIR="${PROJECT_ROOT}/.cache/turbo"

# 仓库锁定 pnpm@8（frontend/package.json 的 packageManager）。注意 pnpm 8.0.0 自身有
# ERR_INVALID_THIS bug（node 20/24 都复现，8.6+ 才修），前端统一用同属 8.x 的 8.15.9：
#   corepack pnpm@8.15.9 install --frozen-lockfile

# AI 模型与推理运行时缓存（嵌入 / Reranker / OCR / 本地 LLM）
export HF_HOME="${PROJECT_ROOT}/.cache/huggingface"
export HUGGINGFACE_HUB_CACHE="${HF_HOME}/hub"
export TRANSFORMERS_CACHE="${HF_HOME}/transformers"
export SENTENCE_TRANSFORMERS_HOME="${HF_HOME}/sentence-transformers"
export TORCH_HOME="${PROJECT_ROOT}/.cache/torch"
export MODELSCOPE_CACHE="${PROJECT_ROOT}/.cache/modelscope"
# PaddleX / PaddleOCR 3.x 的模型缓存（真正生效的是这个变量，默认 ~/.paddlex）
export PADDLE_PDX_CACHE_HOME="${PROJECT_ROOT}/.cache/paddlex"
# Paddle 编译扩展目录
export PADDLE_EXTENSION_DIR="${PROJECT_ROOT}/.cache/paddle-extension"
# 注意：paddle/dataset/common.py 在 import paddle 时无条件创建 ~/.cache/paddle/dataset，
# 路径只跟 HOME 走，没有环境变量能改到仓库内（好在只是个空目录，不含实际下载物）。
export PADDLE_HOME="${PROJECT_ROOT}/.cache/paddle"
export PADDLEOCR_HOME="${PROJECT_ROOT}/.cache/paddleocr"

# ---------------------------------------------------------------- 报告
if [[ "${1:-}" != "-q" ]]; then
  echo "[env] PROJECT_ROOT = ${PROJECT_ROOT}"
  echo "[env] VENV         = ${VENV_DIR}$([[ -x "${VENV_DIR}/bin/python" ]] || echo '  (缺失，请先跑 scripts/setup_local_env.sh)')"
  echo "[env] CACHE ROOT   = ${XDG_CACHE_HOME}"
  if [[ -x "${VENV_DIR}/bin/python" ]]; then
    echo "[env] python       = $(command -v python)"
  fi
fi
