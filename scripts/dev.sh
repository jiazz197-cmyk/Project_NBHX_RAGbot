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
# 在开发容器里跑（环境来自镜像，代码 bind mount；每人一组容器/数据卷/端口）：
#   bash scripts/dev.sh docker up          # 首次：构建镜像 + 起常驻容器
#   bash scripts/dev.sh docker shell       # 进容器
#   bash scripts/dev.sh docker backend     # 容器里起后端（宿主端口 API_PORT）
#   bash scripts/dev.sh docker frontend    # 容器里起 vite（宿主端口 WEB_PORT）
#   bash scripts/dev.sh docker test [-q]   # 容器里跑 pytest
#   bash scripts/dev.sh docker guard       # 容器里跑分层架构守卫
#   bash scripts/dev.sh docker py <args>   # 容器里的 python
#   bash scripts/dev.sh docker fe-setup    # 一次性：容器内装前端依赖
#   bash scripts/dev.sh docker build|pull|down|logs|ps|check
#   每人一组端口示例：API_PORT=8001 WEB_PORT=8889 bash scripts/dev.sh docker up
#
# 说明：`backend` / `frontend` 是前台进程，Ctrl-C 结束。

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 关键：无论调用者在哪个 shell、有没有激活过环境，这里统一把 .venv 与缓存变量设好
# shellcheck source=scripts/env.sh
source "${ROOT}/scripts/env.sh" -q

usage() {
  sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

# 宿主 .venv 缺失时的友好提示：容器化之后宿主 .venv 可以删掉，
# 那时 `dev.sh backend` 这类宿主命令应当引导到 `dev.sh docker ...`，而不是报 python: not found。
require_host_venv() {
  if [[ ! -x "${VENV_DIR:-${ROOT}/.venv}/bin/python" ]]; then
    cat >&2 <<EOF
[dev] 宿主环境不存在：${VENV_DIR:-${ROOT}/.venv}/bin/python
      如果已改用容器，请用：
          bash scripts/dev.sh docker ${cmd}
      确实要在宿主上跑，先执行：
          bash scripts/setup_local_env.sh --with-rag
EOF
    exit 1
  fi
}

cmd="${1:-}"
shift || true

case "${cmd}" in
  backend)
    require_host_venv
    exec python "${ROOT}/main.py"
    ;;
  frontend)
    # 直接进 apps/chat 跑 vite：绕过 turbo（turbo 会把任务交给全局 pnpm 12）
    cd "${ROOT}/frontend/apps/chat"
    exec corepack pnpm dev
    ;;
  test)
    require_host_venv
    exec python -m pytest "$@"
    ;;
  guard)
    exec bash "${ROOT}/scripts/check_layered_architecture.sh" "$@"
    ;;
  py)
    [[ $# -gt 0 ]] || usage 2
    require_host_venv
    exec python "$@"
    ;;
  shell)
    echo "[dev] 已激活 ${VIRTUAL_ENV}；退出该 shell 后回到原环境"
    exec "${SHELL:-/bin/bash}" -i
    ;;
  docker)
    # 容器化开发：环境来自镜像，代码 bind mount。
    # 必须带 -p nbhx-${USER}：共享服务器上多人同用一台 docker daemon，
    # 不加项目名会互相顶掉容器与数据卷。
    CF="${ROOT}/docker/compose.dev.yaml"
    PROJ="nbhx-${USER:-dev}"
    # 受限环境（部分容器/沙箱）里 $HOME 不可写，docker CLI 会因无法写 ~/.docker 而报错
    #（如 buildx activity/log、config.json）。用「可写性探测」而不是「目录是否存在」判断，
    #  否则残留的空目录会让探测失效。退回仓库内的 .cache/docker（已 gitignore）。
    _dockercfg="${DOCKER_CONFIG:-${HOME}/.docker}"
    if ! { mkdir -p "${_dockercfg}" 2>/dev/null && touch "${_dockercfg}/.writable" 2>/dev/null; }; then
      export DOCKER_CONFIG="${ROOT}/.cache/docker"
      mkdir -p "${DOCKER_CONFIG}"
    fi
    # 每人的本地覆盖（端口 / 镜像名等）：仓库内 .env.dev，已被 .gitignore。
    # 用 `set -a` 导出，这样 dev.sh 自己（如 NBHX_DEV_IMAGE 的存在性检查）与
    # docker compose 都能看到。格式就是普通 shell 变量赋值：API_PORT=8001
    if [[ -f "${ROOT}/.env.dev" ]]; then
      set -a
      # shellcheck disable=SC1091
      source "${ROOT}/.env.dev"
      set +a
    fi
    dc() { docker compose -p "${PROJ}" -f "${CF}" "$@"; }
    # `docker compose exec` **不走 entrypoint**，默认用 compose 里的 user（0=root）执行。
    # 所以这里显式降权到宿主 uid/gid，并把 HOME 指回缓存卷（否则是 /root，不可写）——
    # 不然容器写进仓库的文件会变成 root 属主，宿主里就改不动了。
    dexec() {
      docker compose -p "${PROJ}" -f "${CF}" exec \
        -u "$(id -u):$(id -g)" -e "HOME=/workspace/.cache/home" dev "$@"
    }
    # ---- 镜像引用 -------------------------------------------------------------
    # 默认用 registry 里的共享镜像（需先启用 GitLab Registry，见 docs/docker-dev-env.md）。
    # ⚠️ 这个字符串与 docker/compose.dev.yaml 里的 ${NBHX_DEV_IMAGE:-...} 默认值保持一致；
    #    这里 export 之后 compose 会用我们导出的值，所以**以本文件为准**。
    #    想钉住某个具体版本时，在 .env.dev 里写 NBHX_DEV_IMAGE=...:<requirements 哈希>。
    DEV_IMAGE_DEFAULT="10.80.153.12:5050/carl_jia/ragchatbot/nbhx-dev:py312-cu130"
    export NBHX_DEV_IMAGE="${NBHX_DEV_IMAGE:-${DEV_IMAGE_DEFAULT}}"
    IMG="${NBHX_DEV_IMAGE}"

    # ---- 依赖新鲜度检查 -------------------------------------------------------
    # 镜像构建时把四个 requirements 的 sha256 前 12 位写进 /opt/venv/.requirements-hash。
    # 这样 `git pull` 拉到依赖变更后，能立刻知道「需不需要更新镜像」——
    # 因为 venv 在镜像里，光 git pull 是**不会**更新环境的。
    REQ_FILES=("requirements.txt" "requirements-rag.txt" "requirements-dev.txt" "requirements-overrides.txt")
    req_hash_local() { cat "${REQ_FILES[@]/#/${ROOT}/}" 2>/dev/null | sha256sum | cut -c1-12; }
    req_hash_image() {
      docker run --rm --entrypoint cat "$1" /opt/venv/.requirements-hash 2>/dev/null | tr -d '\n'
    }
    is_remote_ref() { case "$1" in *[.:]*/*) return 0 ;; *) return 1 ;; esac; }
    check_env() { # $1 = 1 只警告 / 0 失败退出（都用全局 ${IMG}）
      local local_h img_h
      docker image inspect "${IMG}" >/dev/null 2>&1 || return 2
      local_h="$(req_hash_local)"
      img_h="$(req_hash_image "${IMG}")"
      if [[ -z "${img_h}" ]]; then
        echo "[dev] ⚠️  镜像 ${IMG} 没有依赖哈希标记（旧镜像？），建议重建。" >&2
        return 1
      fi
      if [[ "${local_h}" != "${img_h}" ]]; then
        cat >&2 <<EOF
[dev] ⚠️  依赖已变更，但本地镜像还是旧的 —— 环境不会自动更新！
        本地 requirements 哈希 = ${local_h}
        镜像内依赖哈希         = ${img_h}
      执行其一：
        bash scripts/dev.sh docker pull && bash scripts/dev.sh docker up   # 别人已经推好了新镜像
        bash scripts/dev.sh docker build && bash scripts/dev.sh docker up  # 是你自己改了依赖
EOF
        return 1
      fi
      return 0
    }
    sub="${1:-}"
    shift || true
    case "${sub}" in
      build)     dc build "$@" ;;
      pull)
        is_remote_ref "${IMG}" || { echo "[dev] ${IMG} 不是 registry 镜像（本地标签），无需 pull" >&2; exit 2; }
        dc pull "$@"
        exit $?
        ;;
      up)
        # 来自 registry 的镜像：每次 up 都先对齐一次远端（层都在本地时只是查 manifest，很快）。
        # 拉不到（registry 未启用/没网）不阻塞，继续用本地镜像。
        if is_remote_ref "${IMG}"; then
          echo "[dev] 对齐 registry 镜像 ${IMG} …"
          dc pull --quiet || echo "[dev] 拉取失败（registry 未启用或网络不通），继续用本地镜像"
        fi
        if ! docker image inspect "${IMG}" >/dev/null 2>&1; then
          echo "[dev] 本地无 ${IMG}，本地构建（首次要下依赖，几分钟）…"
          dc build
        fi
        # 提醒依赖是否已过期（不阻塞）
        check_env 1 || true
        dc up -d "$@"
        ;;
      check)
        check_env 0
        case $? in
          0) echo "[dev] ✅ 依赖哈希一致（本地 $(req_hash_local)），环境是最新的" ;;
          2) echo "[dev] 镜像 ${IMG} 不存在，请先: bash scripts/dev.sh docker pull 或 build" >&2; exit 2 ;;
          *) exit 1 ;;
        esac
        ;;
      down|stop) dc down "$@" ;;
      logs)      dc logs -f "$@" ;;
      ps)        dc ps "$@" ;;
      shell)     dexec bash ;;
      backend)   dexec bash scripts/dev.sh backend ;;
      frontend)  dexec bash scripts/dev.sh frontend ;;
      test)      dexec python -m pytest "$@" ;;
      guard)     dexec bash scripts/check_layered_architecture.sh "$@" ;;
      py)
        [[ $# -gt 0 ]] || usage 2
        dexec python "$@"
        ;;
      fe-setup)
        echo "[dev] 容器内安装前端依赖（写入命名卷 nbhx-node-modules，不污染宿主）…"
        dexec bash -lc 'cd frontend && corepack pnpm install --frozen-lockfile'
        ;;
      ""|-h|--help|help) usage 0 ;;
      # 其余透传：`dev.sh docker id` / `dev.sh docker nvidia-smi`
      # 也容忍按 docker 习惯写成 `dev.sh docker exec dev <cmd>`
      *)
        if [[ "${sub}" == "exec" ]]; then
          sub="${1:-}"
          shift || true
          [[ "${sub}" == "dev" ]] && { sub="${1:-}"; shift || true; }
          [[ -n "${sub}" ]] || usage 2
        fi
        dexec "$sub" "$@"
        ;;
    esac
    ;;
  ""|-h|--help|help)
    usage 0
    ;;
  *)
    echo "未知子命令: ${cmd}" >&2
    usage 2
    ;;
esac
