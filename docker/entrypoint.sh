#!/bin/sh
# =============================================================================
# 开发容器 entrypoint
#
# 解决一个 Docker 的固有坑：**命名卷（named volume）默认是 root 属主**，
# 而我们要以宿主开发者的 uid 运行进程（否则容器写进仓库的文件在宿主上是
# root 属主，改不动、git blame 也乱）。实测：把命名卷挂到 bind mount 内部时，
# 卷目录是 root:root 0755，非 root 用户直接 Permission denied。
#
# 做法：容器以 root 启动 → 只把「缓存卷的顶层目录」chown 成宿主 uid
#      （从 bind mount 的 /workspace 属主自动探测，无需任何配置）→ setpriv 降权。
#
# 为什么不 chown -R：
#   - /opt/venv 在镜像层里，递归 chown 会触发 copy-up，每个容器多占一份 11G；
#   - .cache 可能有几百万个文件，每次启动递归 chown 太慢。
#   → 只改顶层目录，子目录由降权后的进程自己创建，天然属主正确。
# =============================================================================
set -eu

WORKSPACE="${WORKSPACE:-/workspace}"

# 自动探测宿主 uid/gid（bind mount 的属主就是宿主里 clone 仓库的人）
detect() { stat -c "$1" "${WORKSPACE}" 2>/dev/null || echo "${2}"; }
DEV_UID="${DEV_UID:-$(detect %u 0)}"
DEV_GID="${DEV_GID:-$(detect %g 0)}"

# HOME 固定放进缓存卷（不依赖基础镜像的 HOME，那个通常是 /root，降权后不可写）。
# 必须与 scripts/dev.sh 里 dexec 传的 `-e HOME=...` 保持一致。
export HOME="${WORKSPACE}/.cache/home"

if [ "$(id -u)" = "0" ]; then
  # 1) 缓存卷顶层：先 chown，再建 HOME / uv 缓存目录（这样它们一出生就是正确属主）
  for d in "${WORKSPACE}/.cache" \
           "${WORKSPACE}/frontend/node_modules" \
           "${WORKSPACE}/frontend/.pnpm-store"; do
    [ -d "$d" ] && chown "${DEV_UID}:${DEV_GID}" "$d" 2>/dev/null || true
  done
  mkdir -p "${HOME}" "${WORKSPACE}/.cache/uv" 2>/dev/null || true
  chown "${DEV_UID}:${DEV_GID}" "${HOME}" "${WORKSPACE}/.cache/uv" 2>/dev/null || true
fi

# 2) 降权后执行真正的命令（保留 HOME / PATH 等环境变量）
if [ "$(id -u)" = "0" ] && [ "${DEV_UID}" != "0" ]; then
  exec setpriv --reuid="${DEV_UID}" --regid="${DEV_GID}" --clear-groups "$@"
fi
exec "$@"
