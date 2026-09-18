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
#   bash scripts/dev.sh docker build|push|pull|down|logs|ps|check
#   每人一组端口示例：API_PORT=8001 WEB_PORT=8889 bash scripts/dev.sh docker up
#
# ragchain 独立容器（依赖在镜像里、代码 bind mount；改代码不用重建镜像）：
#   bash scripts/dev.sh ragchain up          # 首次/换配置：构建（缺镜像时）+ 起容器
#   bash scripts/dev.sh ragchain restart     # 改完代码：重启进程即生效
#   bash scripts/dev.sh ragchain build       # 只有 requirements.txt 变化才需要
#   bash scripts/dev.sh ragchain check|logs|ps|down|shell|test
#
# 说明：`backend` / `frontend` 是前台进程，Ctrl-C 结束。

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 关键：无论调用者在哪个 shell、有没有激活过环境，这里统一把 .venv 与缓存变量设好
# shellcheck source=scripts/env.sh
source "${ROOT}/scripts/env.sh" -q

usage() {
  sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
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
    # ⚠️ 必须探到 buildx 真正要写的子目录：只探顶层会误判 —— 2026-09-18 实测
    #    $HOME/.docker 顶层可写、但 buildx/activity 写入被拒，build 直接报
    #    "failed to update builder last activity time … permission denied"。
    _dockercfg="${DOCKER_CONFIG:-${HOME}/.docker}"
    if ! { mkdir -p "${_dockercfg}/buildx/activity" 2>/dev/null \
           && touch "${_dockercfg}/buildx/activity/.writable" 2>/dev/null; }; then
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
    # 同 dexec，但额外注入环境变量：宿主的 shell 变量**不会**自动穿透 exec 边界，
    # 必须显式 -e 传进去。用法：dexec_env KEY=VAL [KEY=VAL ...] -- <cmd...>
    dexec_env() {
      local -a extra=()
      while [[ $# -gt 0 && "$1" != "--" ]]; do extra+=(-e "$1"); shift; done
      shift || true
      docker compose -p "${PROJ}" -f "${CF}" exec \
        -u "$(id -u):$(id -g)" -e "HOME=/workspace/.cache/home" "${extra[@]}" dev "$@"
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
      build)
        dc build "$@"
        # 记下「这个 tag 现在指向我本地刚构建的镜像」，供 up 判断要不要跳过自动 pull。
        # 血泪教训（2026-09-15）：build 完再 pull（up 里的自动 pull 也算），registry 的旧版本
        # 会把 tag 抢走，刚构建的镜像变悬空 —— 用户以为推的是新的，其实推的还是旧的。
        mkdir -p "${ROOT}/.cache"
        docker image inspect "${IMG}" --format '{{.Id}}' > "${ROOT}/.cache/last-local-build" 2>/dev/null || true
        echo "[dev] 已构建 ${IMG}（已记录本地构建标记；up 不会用 registry 的旧版本覆盖它）"
        echo "      改完记得推： bash scripts/dev.sh docker push（见 docs/docker-dev-guide-admin.md §1）"
        ;;
      pull)
        is_remote_ref "${IMG}" || { echo "[dev] ${IMG} 不是 registry 镜像（本地标签），无需 pull" >&2; exit 2; }
        dc pull "$@"
        exit $?
        ;;
      push)
        # 推镜像给同事用：打「requirements 哈希 tag」+ 更新「当前版本」移动 tag，再一起推。
        # 用法： bash scripts/dev.sh docker push        （需要先 docker login 10.80.153.12:5050）
        is_remote_ref "${IMG}" || { echo "[dev] ${IMG} 不是 registry 镜像，无法 push" >&2; exit 2; }
        REG="${IMG%:*}"                      # 去掉 tag，留下 .../nbhx-dev
        # 用**镜像依赖指纹**（四个 requirements 文件的 sha256 前 12 位，也就是烤进镜像的
        # /opt/venv/.requirements-hash、dev.sh docker check 打印的那个值）作为可复现 tag。
        # 不用「只哈希 requirements.txt」——那样只改 rag/dev 清单时 tag 不变，同名不同镜像。
        HASH_TAG="py312-cu130-$(req_hash_local)"
        echo "[dev] 推送镜像："
        echo "        ${REG}:${HASH_TAG}  （依赖指纹，可复现）"
        echo "        ${IMG}  （移动 tag，同事 pull 拿到的就是它）"
        docker tag "${IMG}" "${REG}:${HASH_TAG}"
        docker push "${REG}:${HASH_TAG}" || exit $?
        docker push "${IMG}" || exit $?
        # 本地也留个别名，方便以后 build 出来直接对应
        docker tag "${IMG}" nbhx-dev:local 2>/dev/null || true
        echo "[dev] ✅ 已推送。同事执行： bash scripts/dev.sh docker pull && bash scripts/dev.sh docker up"
        ;;
      up)
        # 来自 registry 的镜像：先对齐一次远端（层都在本地时只是查 manifest，很快）。
        # ⚠️ 但如果 tag 指向的是**你本地刚构建**的镜像就跳过 —— 否则 registry 的旧版本会把
        #    tag 抢走、你的构建变成悬空（2026-09-15 实测踩过：以为推的是新的，其实推的旧的）。
        if is_remote_ref "${IMG}"; then
          local_id="$(docker image inspect "${IMG}" --format '{{.Id}}' 2>/dev/null || true)"
          stamped_id="$(cat "${ROOT}/.cache/last-local-build" 2>/dev/null || true)"
          if [[ -n "${local_id}" && "${local_id}" == "${stamped_id}" ]]; then
            echo "[dev] ${IMG} 是你本地刚构建的 → 跳过自动 pull（避免被 registry 旧版本覆盖）"
            echo "      想强制对齐远端： bash scripts/dev.sh docker pull"
          else
            echo "[dev] 对齐 registry 镜像 ${IMG} …"
            dc pull --quiet || echo "[dev] 拉取失败（registry 未启用或网络不通），继续用本地镜像"
          fi
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
      backend)
        # 容器内的主应用必须监听 0.0.0.0：宿主端口发布（0.0.0.0:8000→容器）和
        # ragchain 容器（经 host.docker.internal:8000 回调）都要能连进来；本地 .env
        # 常写 HOST=127.0.0.1，只绑回环会让两者都 connection refused（前端表现为
        # ragchain 报“外部服务不可达”）。这里显式覆盖，compose 里另有一份兜底。
        dexec_env HOST=0.0.0.0 -- bash scripts/dev.sh backend
        ;;
      frontend)
        # 前端依赖装在**命名卷** frontend/node_modules 里（刻意遮蔽宿主那份），
        # 首次为空 → 里面没有 .pnpm 存储，apps/chat/node_modules 的软链会悬空，
        # vite 会报一个很难懂的 "Cannot find module .../vite/bin/vite.js"。
        # 这里提前给明确指引。
        if ! dexec sh -c 'test -e /workspace/frontend/node_modules/.pnpm' 2>/dev/null; then
          cat >&2 <<'EOF'
[dev] ❌ 容器内前端依赖还没装（node_modules 是命名卷，首次是空的）
      先跑一次（约 1–3 分钟）：
          bash scripts/dev.sh docker fe-setup
      然后再：
          bash scripts/dev.sh docker frontend
EOF
          exit 1
        fi
        dexec bash scripts/dev.sh frontend
        ;;
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
  ragchain)
    # ragchain 独立容器（ragchain/compose.yaml，独立 compose project = ragchain）。
    # 与根目录 dev 容器同一思路：**依赖在镜像里、代码 bind mount 到 /app/app**，
    # 所以改代码 → restart（重启进程，不重建镜像）；只有 requirements.txt 变化才 build。
    RC_CF="${ROOT}/ragchain/compose.yaml"
    RC_IMG="nbhx-ragchain:py312"
    # 同 docker 分支：受限环境里 $HOME/.docker 不可写 → 退回仓库内 .cache/docker。
    # 探测必须包含 buildx 真正写文件的子目录（只探顶层会误判，见 docker 分支注释）。
    _dockercfg="${DOCKER_CONFIG:-${HOME}/.docker}"
    if ! { mkdir -p "${_dockercfg}/buildx/activity" 2>/dev/null \
           && touch "${_dockercfg}/buildx/activity/.writable" 2>/dev/null; }; then
      export DOCKER_CONFIG="${ROOT}/.cache/docker"
      mkdir -p "${DOCKER_CONFIG}"
    fi
    rc() { docker compose -f "${RC_CF}" "$@"; }
    # 镜像构建时把 requirements.txt 的 sha256 前 12 位写进 /opt/venv/.requirements-hash。
    # 代码是挂载的，所以代码改动不会影响这个指纹 —— 指纹不一致 = 该 build 了。
    rc_hash_local() { sha256sum "${ROOT}/ragchain/requirements.txt" | cut -c1-12; }
    rc_hash_image() {
      docker run --rm --entrypoint cat "${RC_IMG}" /opt/venv/.requirements-hash 2>/dev/null | tr -d '\n'
    }
    rc_check() { # $1 = 1 只警告 / 0 失败退出
      local local_h img_h
      docker image inspect "${RC_IMG}" >/dev/null 2>&1 || return 2
      local_h="$(rc_hash_local)"
      img_h="$(rc_hash_image)"
      if [[ -z "${img_h}" ]]; then
        echo "[ragchain] ⚠️  镜像 ${RC_IMG} 没有依赖哈希标记（旧镜像？），建议：bash scripts/dev.sh ragchain build" >&2
        return 1
      fi
      if [[ "${local_h}" != "${img_h}" ]]; then
        cat >&2 <<EOF
[ragchain] ⚠️  ragchain/requirements.txt 已变更，镜像里的依赖还是旧的
           （代码是 bind mount 的，改代码不需要重建；改依赖才需要。）
           本地哈希 = ${local_h}
           镜像哈希 = ${img_h}
         重建并重启：
           bash scripts/dev.sh ragchain build && bash scripts/dev.sh ragchain up
EOF
        return 1
      fi
      return 0
    }
    rc_sub="${1:-}"
    shift || true
    case "${rc_sub}" in
      up)
        if ! docker image inspect "${RC_IMG}" >/dev/null 2>&1; then
          echo "[ragchain] 本地无 ${RC_IMG}，先构建（首次要装依赖）…"
          rc build
        fi
        rc_check 1 || true
        rc up -d "$@"
        ;;
      build)     rc build "$@" ;;
      restart|reload)
        # 改完代码走这条：重启容器内进程 → 重新 import 挂载进来的代码。
        if [[ -z "$(rc ps -q ragchain 2>/dev/null)" ]]; then
          echo "[ragchain] 容器不存在，先起： bash scripts/dev.sh ragchain up" >&2
          exit 2
        fi
        rc restart "$@"
        ;;
      logs)      rc logs -f "$@" ;;
      ps)        rc ps "$@" ;;
      down|stop) rc "${rc_sub}" "$@" ;;
      start)     rc start "$@" ;;
      check)
        # 注意：脚本开着 set -e，`rc_check 0` 不能用裸调用 + `case $?`，
        # 否则非 0 状态会先被 set -e 吃掉、下面的提示永远不打印。放进 if 条件里。
        if rc_check 0; then
          echo "[ragchain] ✅ 依赖哈希一致（本地 $(rc_hash_local)）：改代码只需 ragchain restart，不用重建镜像"
        else
          rc_rc=$?
          case "${rc_rc}" in
            2) echo "[ragchain] 镜像 ${RC_IMG} 不存在，请先: bash scripts/dev.sh ragchain up" >&2; exit 2 ;;
            *) exit 1 ;;
          esac
        fi
        ;;
      shell)     rc exec ragchain bash ;;
      exec)
        # 透传：bash scripts/dev.sh ragchain exec ragchain python -c '...'
        rc exec "$@"
        ;;
      test)
        # 单测不在镜像里（镜像只装运行时依赖），用本机 ragchain venv 跑；见 docs/operations.md §3.5
        rc_venv="${ROOT}/.cache/ragchain-venv"
        if [[ ! -x "${rc_venv}/bin/python" ]]; then
          cat >&2 <<EOF
[ragchain] 本机测试环境不存在：${rc_venv}/bin/python
          首次创建（在仓库根执行）：
            uv venv .cache/ragchain-venv --python 3.12
            UV_CACHE_DIR=\$PWD/.cache/uv uv pip install --python .cache/ragchain-venv/bin/python \\
              -r ragchain/requirements-dev.txt --index-url https://mirrors.aliyun.com/pypi/simple
EOF
          exit 1
        fi
        ( cd "${ROOT}/ragchain" && "${rc_venv}/bin/python" -m pytest tests "$@" )
        ;;
      ""|-h|--help|help) usage 0 ;;
      *)
        echo "[ragchain] 未知子命令: ${rc_sub}" >&2
        usage 2
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
