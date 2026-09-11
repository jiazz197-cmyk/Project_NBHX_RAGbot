#!/usr/bin/env bash
# 后端启动脚本（systemd ExecStart 入口）。
#
# 职责：
#   1. 取消全部代理环境变量（反代在 8080，出站请求不能被代理拦截）
#   2. 加载根目录 .env
#   3. 解析 Python 虚拟环境（yamatoenv）
#   4. 探测并等待依赖服务就绪（PostgreSQL / Redis / MinIO / SQL Server / Dify）
#   5. 启动 uvicorn main:app
#
# 依赖的 Docker 容器（nginx 反代、postgres、redis、minio、sqlserver、dify 等）
# 已设置 restart=always，开机自启；本脚本只负责等待它们就绪后拉起后端。
#
# 可用环境变量覆盖：
#   VENV_DIR            Python 虚拟环境目录（默认自动探测）
#   HOST / PORT         监听地址与端口（默认 0.0.0.0 / 8000）
#   UVICORN_EXTRA       附加 uvicorn 参数（如 --reload）
#   SKIP_WAIT=1         跳过依赖就绪探测
#   WAIT_TIMEOUT        单服务最长等待秒数（默认 120）
#   POLL_INTERVAL       探测间隔秒数（默认 3）
#   FAIL_ON_UNREADY=1   依赖未就绪则直接退出（默认仅告警并继续，因后端可降级运行）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ── 1. 取消代理 ──
unset ALL_PROXY all_proxy HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy

log()  { printf '\033[1;36m[start]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[ok]\033[0m   %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; }

# ── 2. 加载 .env（安全方式：仅导出 KEY=VALUE，不执行任意内容） ──
# 不能用 source：.env 里有带空格的值（如 PROJECT_NAME=Project Yamato Shanghai）
# 和注释文本，source 会把值当命令执行（status=127）。
load_env() {
  local file="$1" line key val
  [[ -f "$file" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    # 跳过空行与注释
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    # 仅处理 KEY=VALUE 形式
    [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    key="${BASH_REMATCH[1]}"; val="${BASH_REMATCH[2]}"
    # 去掉首尾成对引号
    if [[ "$val" =~ ^\"(.*)\"$ ]]; then val="${BASH_REMATCH[1]}"
    elif [[ "$val" =~ ^\'(.*)\'$ ]]; then val="${BASH_REMATCH[1]}"; fi
    export "$key=$val"
  done < "$file"
}
if load_env "${ROOT}/.env"; then
  log "已加载 .env"
else
  warn "未找到 ${ROOT}/.env，使用 config.py 内置默认值"
fi

# ── 3. 解析虚拟环境 ──
resolve_venv() {
  if [[ -n "${VENV_DIR:-}" && -x "${VENV_DIR}/bin/python" ]]; then
    echo "${VENV_DIR}"; return
  fi
  if command -v uvicorn >/dev/null 2>&1; then
    local dir
    dir="$(dirname "$(dirname "$(command -v uvicorn)")")"
    echo "${dir}"; return
  fi
  local cand
  for cand in "${HOME}/桌面/yamatoenv" "${HOME}/yamatoenv" "${ROOT}/.venv" "${ROOT}/venv"; do
    if [[ -x "${cand}/bin/python" ]]; then echo "${cand}"; return; fi
  done
  return 1
}

if ! VENV="$(resolve_venv)"; then
  fail "未找到 Python 虚拟环境（yamatoenv）。请设置 VENV_DIR 环境变量。"
  exit 1
fi
PY="${VENV}/bin/python"
if ! "${PY}" -c "import uvicorn" >/dev/null 2>&1; then
  fail "${VENV} 内未安装 uvicorn，请在该环境中 pip install -r requirements.txt"
  exit 1
fi
log "虚拟环境：${VENV}（uvicorn $("${PY}" -c 'import uvicorn;print(uvicorn.__version__)')）"

# ── 4. 依赖就绪探测 ──
# 从 URL 中解析 host:port（支持 http://host:port、host:port、redis://:pw@host:port/0）
parse_endpoint() {
  local raw="$1" default_port="$2"
  # 去掉 scheme
  local stripped="${raw#*://}"
  stripped="${stripped%%/*}"            # 去掉路径
  stripped="${stripped#*@}"             # 去掉 userinfo
  local host="${stripped%%:*}"
  local port="${stripped##*:}"
  [[ "$stripped" == *:* ]] || port="$default_port"
  [[ -n "$host" ]] || host="127.0.0.1"
  [[ -n "$port" ]] || port="$default_port"
  echo "${host} ${port}"
}

probe() {
  # $1 名称  $2 host  $3 port
  local name="$1" host="$2" port="$3"
  "${PY}" - "$name" "$host" "$port" <<'PY'
import socket, sys, time, os
name, host, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
interval = float(os.environ.get("POLL_INTERVAL", "3"))
deadline = time.time() + float(os.environ.get("WAIT_TIMEOUT", "120"))
last_err = ""
while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=3):
            print(f"[ok]   {name} {host}:{port} 就绪")
            sys.exit(0)
    except OSError as e:
        last_err = str(e)
        time.sleep(interval)
print(f"[fail] {name} {host}:{port} 未就绪：{last_err}", file=sys.stderr)
sys.exit(1)
PY
}

WAIT_TIMEOUT="${WAIT_TIMEOUT:-120}"
POLL_INTERVAL="${POLL_INTERVAL:-3}"
# 全局等待上限：所有探测并发执行，到点后杀死仍在等待的探测（防止总等待超过单元 TimeoutStartSec）
GLOBAL_WAIT="${GLOBAL_WAIT:-180}"
export WAIT_TIMEOUT POLL_INTERVAL

if [[ "${SKIP_WAIT:-0}" == "1" ]]; then
  warn "SKIP_WAIT=1，跳过依赖就绪探测"
else
  log "等待依赖服务就绪（单服务最长 ${WAIT_TIMEOUT}s）…"

  # 构造探测目标列表：名称 host port
  targets=()
  pg_host="${POSTGRES_SERVER:-127.0.0.1}"; pg_port="${POSTGRES_PORT:-5432}"
  targets+=("PostgreSQL" "$pg_host" "$pg_port")

  if [[ -n "${REDIS_HOST:-}" ]]; then
    targets+=("Redis" "${REDIS_HOST}" "${REDIS_PORT:-6379}")
  elif [[ -n "${REDIS_URL:-}" ]]; then
    read -r rh rp < <(parse_endpoint "${REDIS_URL}" 6379)
    targets+=("Redis" "$rh" "$rp")
  fi

  if [[ -n "${MINIO_ENDPOINT:-}" ]]; then
    read -r mh mp < <(parse_endpoint "${MINIO_ENDPOINT}" 9000)
    targets+=("MinIO" "$mh" "$mp")
  fi

  u8_host="${U8_SQLSERVER_HOST:-}"; u8_port="${U8_SQLSERVER_PORT:-1433}"
  [[ -n "$u8_host" ]] && targets+=("SQLServer-U8" "$u8_host" "$u8_port")

  pdm_host="${PDM_SQLSERVER_HOST:-}"; pdm_port="${PDM_SQLSERVER_PORT:-1433}"
  [[ -n "$pdm_host" ]] && targets+=("SQLServer-PDM" "$pdm_host" "$pdm_port")

  # Dify：DIFY_BASE_URL（默认 http://localhost:80）
  read -r dh dp < <(parse_endpoint "${DIFY_BASE_URL:-http://localhost:80}" 80)
  targets+=("Dify" "$dh" "$dp")

  unready=0
  # 并发探测所有依赖（避免串行累加等待时间超过单元 TimeoutStartSec）
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' EXIT
  declare -a pids=()
  i=0
  while (( i < ${#targets[@]} )); do
    name="${targets[$i]}"; host="${targets[$((i+1))]}"; port="${targets[$((i+2))]}"
    ( probe "$name" "$host" "$port" ) >"$tmpdir/p$((i/3)).log" 2>&1 &
    pids+=($!)
    i=$((i+3))
  done

  # 全局上限看门狗：到点后杀死仍在等待的探测
  ( sleep "$GLOBAL_WAIT"; for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done ) &
  watchdog=$!

  for p in "${pids[@]}"; do
    if ! wait "$p"; then unready=$((unready+1)); fi
  done
  kill "$watchdog" 2>/dev/null || true
  wait "$watchdog" 2>/dev/null || true

  # 按提交顺序输出各探测结果
  n=$(( ${#targets[@]} / 3 ))
  for (( k=0; k<n; k++ )); do cat "$tmpdir/p$k.log"; done
  rm -rf "$tmpdir"
  trap - EXIT

  if (( unready > 0 )); then
    warn "${unready} 个依赖未就绪"
    if [[ "${FAIL_ON_UNREADY:-0}" == "1" ]]; then
      fail "FAIL_ON_UNREADY=1，终止启动"
      exit 1
    fi
    warn "继续启动后端（后端对部分依赖支持降级运行，详见 main.py 启动日志）"
  else
    ok "全部依赖服务就绪"
  fi
fi

# ── 5. 启动 uvicorn ──
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
log "启动后端：uvicorn main:app --host ${HOST} --port ${PORT}（反代 8080 → ${PORT}）"
exec "${PY}" -m uvicorn main:app --host "${HOST}" --port "${PORT}" ${UVICORN_EXTRA:-}
