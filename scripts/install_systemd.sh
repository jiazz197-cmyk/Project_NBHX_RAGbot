#!/usr/bin/env bash
# 安装并启用 yamato-backend systemd 服务。
#
# 用法：
#   sudo ./scripts/install_systemd.sh            # 安装 + 启用 + 启动
#   sudo ./scripts/install_systemd.sh --uninstall  # 卸载（停止 + 禁用 + 删除单元）
#
# 安装后效果：服务器重启后后端自动拉起；进程崩溃 5s 后自动重启。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TPL="${ROOT}/deploy/yamato-backend.service.template"
UNIT_NAME="yamato-backend.service"
UNIT_DST="/etc/systemd/system/${UNIT_NAME}"

c() { printf '\033[1;36m●\033[0m %s\n' "$*"; }
g() { printf '\033[1;32m✔\033[0m %s\n' "$*"; }
r() { printf '\033[1;31m✖\033[0m %s\n' "$*" >&2; }

if [[ $EUID -ne 0 ]]; then
  r "需要 root 权限（写 /etc/systemd/system）。请用 sudo 运行。"
  exit 1
fi

if [[ ! -f "$TPL" ]]; then
  r "缺少模板：${TPL}"
  exit 1
fi

# 探测运行用户：优先 SUDO_USER，否则当前用户
RUN_USER="${SUDO_USER:-${USER:-}}"
if [[ -z "$RUN_USER" || "$RUN_USER" == "root" ]]; then
  r "无法确定运行用户（请以非 root 用户通过 sudo 执行）。"
  exit 1
fi
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
if [[ -z "$RUN_HOME" ]]; then
  r "找不到用户 ${RUN_USER} 的家目录。"
  exit 1
fi

# 探测虚拟环境：yamatoenv
detect_venv() {
  for cand in "${RUN_HOME}/桌面/yamatoenv" "${RUN_HOME}/yamatoenv" "${ROOT}/.venv" "${ROOT}/venv"; do
    if [[ -x "${cand}/bin/python" ]] && "${cand}/bin/python" -c "import uvicorn" >/dev/null 2>&1; then
      echo "$cand"; return 0
    fi
  done
  return 1
}
if ! VENV_DIR="$(detect_venv)"; then
  r "未在常见位置找到含 uvicorn 的 yamatoenv。请先在该环境内 pip install -r requirements.txt。"
  exit 1
fi

ACTION="install"
[[ "${1:-}" == "--uninstall" ]] && ACTION="uninstall"

uninstall() {
  c "卸载 ${UNIT_NAME} …"
  systemctl disable --now "$UNIT_NAME" 2>/dev/null || true
  rm -f "$UNIT_DST"
  systemctl daemon-reload
  g "已卸载 ${UNIT_NAME}"
}

install_unit() {
  c "渲染服务单元（ROOT=${ROOT}  USER=${RUN_USER}  VENV=${VENV_DIR}）"
  tmp="$(mktemp)"
  sed \
    -e "s#__ROOT__#${ROOT}#g" \
    -e "s#__USER__#${RUN_USER}#g" \
    -e "s#__VENV_DIR__#${VENV_DIR}#g" \
    "$TPL" > "$tmp"
  install -m 0644 "$tmp" "$UNIT_DST"
  rm -f "$tmp"
  systemctl daemon-reload
  g "已安装 ${UNIT_DST}"
}

if [[ "$ACTION" == "uninstall" ]]; then
  uninstall
  exit 0
fi

install_unit
c "启用并启动服务 …"
systemctl enable "$UNIT_NAME" >/dev/null
# --no-block：启动可能因依赖探测耗时较长，不阻塞安装脚本；状态由下方轮询确认
systemctl restart --no-block "$UNIT_NAME"

# 轮询启动状态（最多 ~30s），不因启动慢而误判失败
state=""
for _ in $(seq 1 15); do
  sleep 2
  state="$(systemctl is-active "$UNIT_NAME" 2>/dev/null || true)"
  case "$state" in
    active) break ;;
    failed) break ;;
  esac
done

c "当前状态：${state}"
systemctl --no-pager --full status "$UNIT_NAME" || true

cat <<EOF

$(printf '\033[1;32m━━━ 安装完成 ━━━\033[0m')

服务名：${UNIT_NAME}
运行用户：${RUN_USER}
工作目录：${ROOT}
虚拟环境：${VENV_DIR}
监听：0.0.0.0:8000（nginx 反代 8080 → 8000）

开机自启：已启用（服务器重启后自动拉起后端；Docker 依赖容器靠 restart=always 自启）
崩溃自愈：进程异常退出 5s 后自动重启

常用命令：
  查看状态      systemctl status ${UNIT_NAME}
  实时日志      journalctl -u ${UNIT_NAME} -f
  启动/停止     systemctl start ${UNIT_NAME}   |  systemctl stop ${UNIT_NAME}
  重启          systemctl restart ${UNIT_NAME}
  重新加载配置  systemctl reload ${UNIT_NAME}
  卸载          sudo ./scripts/install_systemd.sh --uninstall

访问地址（nginx 反代）：
  应用          http://<服务器IP>:8080/
  API 文档      http://<服务器IP>:8080/api/v1/docs
  健康检查      http://<服务器IP>:8080/api/v1/health
EOF
