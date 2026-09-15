#!/usr/bin/env bash
# =============================================================================
# 让 docker 允许访问内网 http registry（10.80.153.12:5050）
#
# 为什么需要：registry_external_url 用的是 **http**，而 docker 客户端默认只走 https
#（localhost 例外）。不配这个，`docker login/push/pull` 会报：
#     http: server gave HTTP response to HTTPS client
#
# 用法：
#   sudo bash scripts/enable_insecure_registry.sh            # 只改配置 + reload（尽量不重启 docker）
#   sudo bash scripts/enable_insecure_registry.sh --restart   # 若 reload 不生效，再重启 docker
#
# ⚠️ --restart 会重启**所有**容器（GitLab / pgvector / Redis / MinIO / 别人的项目），
#    GitLab 约 2–4 分钟不可用。请挑没人用 GitLab / 跑 CI 的时候。
#
# 注意：**每个要推/拉镜像的机器都要配一次**（包括同事自己的电脑）。
# =============================================================================
set -Eeuo pipefail

DAEMON_JSON="/etc/docker/daemon.json"
REGISTRY_HOST="10.80.153.12:5050"
DO_RESTART=0
[[ "${1:-}" == "--restart" ]] && DO_RESTART=1

die() { echo "[insecure-registry] ❌ $*" >&2; exit 1; }
log() { echo "[insecure-registry] $*"; }

[[ "$(id -u)" == "0" ]] || die "请用 root 跑：sudo bash $0"
command -v python3 >/dev/null || die "缺 python3"

# ---- 1. 备份 + 打补丁（python 保证 JSON 合法、幂等）-------------------------
[[ -f "${DAEMON_JSON}" ]] || { log "${DAEMON_JSON} 不存在，创建之"; echo '{}' > "${DAEMON_JSON}"; }
cp -a "${DAEMON_JSON}" "${DAEMON_JSON}.bak-$(date +%Y%m%d-%H%M%S)"
log "已备份 → ${DAEMON_JSON}.bak-$(date +%Y%m%d-%H%M%S)"

python3 - "${DAEMON_JSON}" "${REGISTRY_HOST}" <<'PY'
import json, sys, pathlib
path, host = sys.argv[1], sys.argv[2]
p = pathlib.Path(path)
cfg = json.loads(p.read_text(encoding="utf-8") or "{}")
lst = cfg.setdefault("insecure-registries", [])
if host in lst:
    print(f"  · {host} 已在 insecure-registries 里，跳过")
else:
    lst.append(host)
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  · 已加入 insecure-registries: {host}")
PY

python3 -c "import json,sys; json.load(open('${DAEMON_JSON}')); print('  · daemon.json JSON 合法 ✅')"

# ---- 2. 优先用 reload（不重启容器），不行再 restart ------------------------
log "尝试 reload docker（不会重启容器）…"
if systemctl reload docker 2>/dev/null; then
  log "reload 完成，等 3 秒后测试"
  sleep 3
else
  log "⚠️ systemctl reload docker 不支持/失败"
fi

# 用 docker login 的报错内容判断 insecure 是否生效（不真的登录：
# 只要不再报 "server gave HTTP response to HTTPS client" 就算成功）
out="$(docker login "${REGISTRY_HOST}" -u __probe__ --password-stdin <<< "x" 2>&1 || true)"
if grep -qi "server gave HTTP response to HTTPS client" <<< "${out}"; then
  log "❌ reload 不足以生效 insecure-registries"
  if [[ "${DO_RESTART}" == "1" ]]; then
    log "按 --restart 执行：重启 docker（所有容器会重启，GitLab 约 2–4 分钟不可用）…"
    systemctl restart docker
    log "已重启，等待 10 秒"
    sleep 10
    out="$(docker login "${REGISTRY_HOST}" -u __probe__ --password-stdin <<< "x" 2>&1 || true)"
  else
    die "请挑空闲时间跑： sudo bash $0 --restart"
  fi
fi

if grep -qi "server gave HTTP response to HTTPS client" <<< "${out}"; then
  die "重启后仍然报 HTTPS 错误，请检查 ${DAEMON_JSON} 并确认 dockerd 已重启"
fi

log "✅ docker 现在允许访问 ${REGISTRY_HOST}（探针返回的是认证错误而非 HTTPS 错误，符合预期）"
echo
echo "下一步（**用你自己的普通账号**，不要 root，否则凭据会落在 /root/.docker）："
echo "  docker login ${REGISTRY_HOST}"
echo "     用户名 = GitLab 用户名；密码 = Personal Access Token（scope 勾 read_registry+write_registry 或 api）"
echo
echo "提醒：其他要推拉镜像的机器（同事的电脑）也要各自跑一次本脚本。"
