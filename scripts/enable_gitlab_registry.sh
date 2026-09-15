#!/usr/bin/env bash
# =============================================================================
# 启用 GitLab Container Registry（在共享 GPU 服务器 10.80.153.12 上执行）
#
# 用法：
#   sudo bash scripts/enable_gitlab_registry.sh
#
# 它做四件事（可重复执行，幂等）：
#   1. 备份 /srv/infra/docker-compose.yml
#   2. 在 gitlab 服务的 GITLAB_OMNIBUS_CONFIG 里加 registry_external_url + registry['enable']
#      并在 ports 里加 "5050:5050"
#   3. 重建 gitlab 容器（**关键**：GITLAB_OMNIBUS_CONFIG 是容器 env，只有重建才会带上新值），
#      然后**等容器 entrypoint 自己那遍 reconfigure 跑完** —— 绝不再手动跑第二遍（见下方 ⚠️）
#   4. 等待并验证：registry 服务起来、:5050 返回 401（=活着，需认证）
#
# 背景：只跑 `docker exec gitlab gitlab-ctl reconfigure` 是**没用的** ——
#   如果 compose 文件没改 / 容器没重建，omnibus 根本不知道要开 registry。
#
# 实测过的事实（2026-09-15）：当时 /srv/infra/docker-compose.yml 里既没有
#   registry_external_url 也没有 5050 端口，且 gitlab 容器是 10:59 创建的 —— 
#   所以那次 reconfigure 只更新了 4/654 个资源，registry 服务压根没生成。
#
# ⚠️ 停机提醒：会重建 gitlab 容器并 reconfigure，**GitLab（网页 + CI）约 2–5 分钟不可用**。
#    避开大家用 GitLab / 跑 CI 的时段；正在跑的 CI job 可能失败，需要重跑。
# =============================================================================
set -Eeuo pipefail

COMPOSE="/srv/infra/docker-compose.yml"
INFRA_DIR="/srv/infra"
GITLAB_HOST="10.80.153.12"
REGISTRY_PORT="5050"

die() { echo "[registry] ❌ $*" >&2; exit 1; }
log() { echo "[registry] $*"; }

[[ "$(id -u)" == "0" ]] || die "请用 root 跑：sudo bash $0"
[[ -f "${COMPOSE}" ]] || die "找不到 ${COMPOSE}"
command -v python3 >/dev/null || die "缺 python3"

# ---- 1. 备份 ---------------------------------------------------------------
BACKUP="${COMPOSE}.bak-$(date +%Y%m%d-%H%M%S)"
cp -a "${COMPOSE}" "${BACKUP}"
log "已备份 → ${BACKUP}"

# ---- 2. 打补丁（python 精确替换 + 断言，避免 YAML 缩进改错）----------------
python3 - "${COMPOSE}" "${GITLAB_HOST}" "${REGISTRY_PORT}" <<'PY'
import sys, pathlib
path, host, port = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(path)
t = p.read_text(encoding="utf-8")
changed = []

# 2.1 registry 配置：挂在唯一的 external_url 行后面
anchor = f"        external_url 'http://{host}'\n"
if "registry_external_url" in t:
    print("  · registry_external_url 已存在，跳过")
else:
    assert t.count(anchor) == 1, f"锚点 external_url 不唯一（{t.count(anchor)} 处），请手工检查"
    t = t.replace(anchor, anchor
                  + f"        registry_external_url 'http://{host}:{port}'\n"
                  + "        registry['enable'] = true\n", 1)
    changed.append("registry_external_url + registry['enable']")

# 2.2 端口：挂在唯一的 "2222:22" 后面（属于 gitlab 服务）
port_anchor = '      - "2222:22"\n'
port_line = f'      - "{port}:{port}"\n'
if port_line in t:
    print(f"  · 端口 {port} 已存在，跳过")
else:
    assert t.count(port_anchor) == 1, f"锚点 2222:22 不唯一（{t.count(port_anchor)} 处），请手工检查"
    t = t.replace(port_anchor, port_anchor + port_line, 1)
    changed.append(f"ports {port}:{port}")

p.write_text(t, encoding="utf-8")
print("  已修改：" + ("、".join(changed) if changed else "（无变化）"))
PY

# ---- 3. 重建容器，然后**等它自己那遍 reconfigure 跑完** ---------------------
# ⚠️ 绝对不要在这里再执行一次 `gitlab-ctl reconfigure`！
#    官方镜像的 entrypoint 在容器启动时**已经**会 eval GITLAB_OMNIBUS_CONFIG 并 reconfigure。
#    再手动跑第二遍会与它并发（chef 会打印 "Cinc Client N is running, will wait"），
#    两个 chef 同时重建 /opt/gitlab/service/* → 互相把对方等待的 supervise socket 删掉 →
#    死锁在 ruby_block[wait for X service socket]，且 GitLab 服务全部下线。
#    （2026-09-15 实测发生过一次，只能靠 docker restart gitlab 恢复。）
log "重建 gitlab 容器（让 GITLAB_OMNIBUS_CONFIG 生效）…"
cd "${INFRA_DIR}"
docker compose up -d gitlab

log "等待容器自身的 reconfigure 完成（最多 10 分钟，别中断）…"
reconf_done=""
for i in $(seq 1 60); do
  sleep 10
  if docker logs gitlab 2>&1 | tail -200 | grep -q "gitlab Reconfigured!"; then
    reconf_done=1; break
  fi
  # 容器已经不在跑（异常）就立刻报错，别白等
  if ! docker ps --filter name=gitlab --filter status=running -q | grep -q .; then
    die "gitlab 容器没在运行！docker logs gitlab 最后 30 行：$(docker logs --tail 30 gitlab 2>&1)"
  fi
  printf "  [%02d] 还在 reconfigure…（服务目录 %s 个）\n" "$i" \
    "$(docker exec gitlab sh -c 'ls /opt/gitlab/service/ 2>/dev/null | wc -l' 2>/dev/null || echo '?')"
done
[[ -n "${reconf_done}" ]] || die "10 分钟内没看到 'gitlab Reconfigured!'，请查 docker logs gitlab"
log "reconfigure 完成 ✅"

# ---- 4. 验证 ---------------------------------------------------------------
log "等待 registry 就绪（最多 180s）…"
ok=""
for i in $(seq 1 60); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:${REGISTRY_PORT}/v2/" || true)"
  if [[ "${code}" == "401" || "${code}" == "200" ]]; then ok="${code}"; break; fi
  sleep 3
done

echo
if [[ -n "${ok}" ]]; then
  log "✅ registry 已就绪：http://${GITLAB_HOST}:${REGISTRY_PORT}/v2/ → HTTP ${ok}（401=需要认证，正常）"
  docker exec gitlab gitlab-ctl status registry || true
  cat <<EOF

下一步（在**你自己的**机器上，不要在 root 下）：
  docker login ${GITLAB_HOST}:${REGISTRY_PORT}
    用户名 = GitLab 用户名（如 jiazhenyu）
    密码   = Personal Access Token（GitLab → 头像 → Edit profile → Access tokens，
             scope 勾 read_registry + write_registry，或直接勾 api）

推送 dev 镜像：
  TAG=py312-cu130-\$(sha256sum requirements.txt | cut -c1-8)
  docker tag nbhx-dev:local ${GITLAB_HOST}:${REGISTRY_PORT}/carl_jia/ragchatbot/nbhx-dev:\$TAG
  docker push ${GITLAB_HOST}:${REGISTRY_PORT}/carl_jia/ragchatbot/nbhx-dev:\$TAG
  docker tag nbhx-dev:local ${GITLAB_HOST}:${REGISTRY_PORT}/carl_jia/ragchatbot/nbhx-dev:py312-cu130
  docker push ${GITLAB_HOST}:${REGISTRY_PORT}/carl_jia/ragchatbot/nbhx-dev:py312-cu130

存储位置（默认落在固态 /srv/infra，随 gitlab data 目录持久化）：
  /srv/infra/gitlab/data/gitlab-rails/shared/registry
定期回收（需要维护窗口，会切只读模式）：
  docker exec gitlab gitlab-ctl registry-garbage-collect -m
EOF
else
  echo "[registry] ❌ 180s 内 :${REGISTRY_PORT} 仍不通。排查顺序：" >&2
  echo "  1) 端口有没有发布：  docker ps --filter name=gitlab --format '{{.Ports}}' | tr ',' '\\n' | grep ${REGISTRY_PORT}" >&2
  echo "  2) 容器内 nginx 有没有监听： docker exec gitlab sh -c 'netstat -ltn | grep ${REGISTRY_PORT}'" >&2
  echo "  3) registry 服务状态：       docker exec gitlab gitlab-ctl status registry" >&2
  echo "  4) registry 日志：           docker exec gitlab tail -50 /var/log/gitlab/registry/current" >&2
  echo "  5) 防火墙：                  systemctl is-active ufw && ufw status | grep ${REGISTRY_PORT}" >&2
  echo "  6) reconfigure 日志：        tail -50 /tmp/gitlab-reconfigure.log" >&2
  exit 1
fi
