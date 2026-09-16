# syntax=docker/dockerfile:1.7
# =============================================================================
# NBHX 开发环境镜像（dev）
#
# 设计要点
#   1. **不含代码**：代码运行时 bind mount 到 /workspace，改代码不需要重建镜像。
#      → 镜像只在 requirements*.txt 变化时重建。
#   2. **依赖分三层**（主清单 / RAG / 测试工具）：改业务代码或改测试工具都不会
#      重装 torch、paddle（那是几 GB 的下载）。
#   3. **venv 放 /opt/venv**（在 bind mount 之外，不会被宿主仓库的 .venv 遮蔽）。
#      scripts/env.sh 认 `VENV_DIR` 环境变量，所以容器内无需改任何脚本。
#   4. **不需要 GPU**：OCR / TagGenerator 将拆成独立容器（issue #9 / #10）。
#      过渡期这两个依赖仍在镜像里，等代码改完删掉对应层即可（见下方标注）。
#
# 构建（一般不用手敲，用 `bash scripts/dev.sh docker build`）：
#   docker compose -f docker/compose.dev.yaml build
#
# 本机实测：pypi.org 不可达（超时），所以默认走国内镜像；可用 --build-arg 覆盖：
#   docker compose -f docker/compose.dev.yaml build \
#     --build-arg PYPI_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
# =============================================================================
FROM node:24-slim AS node

FROM python:3.12-slim

ARG PYPI_INDEX=https://mirrors.aliyun.com/pypi/simple
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu130
ARG PADDLE_INDEX=https://www.paddlepaddle.org.cn/packages/stable/cu126/
ARG UV_VERSION=0.12.11

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VENV_DIR=/opt/venv \
    PATH=/opt/venv/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/root/.cache/uv

# ---- 系统依赖 ---------------------------------------------------------------
# procps   = ps / top / pgrep —— 排「端口被占」「进程没退干净」时必需；slim 镜像默认没有，
#            之前只能靠 /proc 扫（见 docs/docker-dev-guide-colleague.md §7.1）
# iproute2 = ss —— 看容器内监听端口
# ⚠️ 改动这一层会让后面所有层失效，**只在下一次因别的原因重建镜像时一起生效**（别为它单独重建）。
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git build-essential \
      procps iproute2 \
 && rm -rf /var/lib/apt/lists/*

# ---- Node 24 + corepack -----------------------------------------------------
# 只拷 bin 与 lib/node_modules：整份 COPY /usr/local 会覆盖 python 镜像自带的
# /usr/local（python 就跑在那儿）。
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && ln -sf /usr/local/lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack \
 && node -v && corepack -v

# ---- uv（用 pip 装，避免依赖 ghcr.io 的可达性）-------------------------------
RUN python -m pip install --no-cache-dir --index-url "${PYPI_INDEX}" "uv==${UV_VERSION}" \
 && uv --version

# ---- Python 依赖（三层，别合并）---------------------------------------------
RUN uv venv /opt/venv --python 3.12 --seed

# 层 1：主应用清单。⚠️ --overrides 不能省：否则 paddle 会拉进 nvidia-nccl-cu12，
# 覆盖 torch 需要的 nvidia-nccl-cu13 的同一个文件 libnccl.so.2 → import torch 崩。
COPY requirements.txt requirements-overrides.txt /tmp/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/venv/bin/python \
      -r /tmp/requirements.txt \
      --overrides /tmp/requirements-overrides.txt \
      --index-url "${PYPI_INDEX}" \
      --extra-index-url "${TORCH_INDEX}" \
      --extra-index-url "${PADDLE_INDEX}" \
      --index-strategy unsafe-best-match

# 层 2：RAG 栈。⚠️ 过渡期需要（仓库里 RAG 代码还没搬走：main.py / api registry 仍
# import langchain、llama_index）。等 RAG 改成 HTTP 客户端后，**删掉本层**即可。
COPY requirements-rag.txt /tmp/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/venv/bin/python -r /tmp/requirements-rag.txt \
      --index-url "${PYPI_INDEX}" \
      --extra-index-url "${TORCH_INDEX}" \
      --extra-index-url "${PADDLE_INDEX}" \
      --index-strategy unsafe-best-match

# 层 3：测试工具（watchfiles 是 uvicorn --reload 的 inotify 后端，缺了会单核空转）
COPY requirements-dev.txt /tmp/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/venv/bin/python -r /tmp/requirements-dev.txt \
      --index-url "${PYPI_INDEX}"

# 让任意 uid（宿主开发者）都能读用 venv
RUN chmod -R a+rX /opt/venv

# 记录依赖指纹：dev.sh 用它判断「git pull 拿到新的 requirements 后要不要重建镜像」。
# 四个文件的拼接顺序必须与 dev.sh 里 REQ_FILES 的顺序一致。
RUN cat /tmp/requirements.txt /tmp/requirements-rag.txt /tmp/requirements-dev.txt /tmp/requirements-overrides.txt \
      | sha256sum | cut -c1-12 > /opt/venv/.requirements-hash \
 && echo "requirements hash: $(cat /opt/venv/.requirements-hash)"

COPY docker/entrypoint.sh /usr/local/bin/nbhx-entrypoint
RUN chmod +x /usr/local/bin/nbhx-entrypoint

# 运行时的 uv 缓存指向缓存卷（构建期用的是 cache mount /root/.cache/uv，两者互不影响）。
# 放在安装之后，避免影响上面几层的构建缓存。
ENV UV_CACHE_DIR=/workspace/.cache/uv

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/nbhx-entrypoint"]
CMD ["bash"]
