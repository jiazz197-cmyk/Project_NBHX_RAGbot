#!/usr/bin/env bash
# infra 容器体检 / 自愈 —— 专治「宿主侧动了容器数据目录的属主或权限」这一类故障。
#
# 为什么需要它（2026-09-16 实测事故）：
#   有人对 /srv/infra 做了一次递归「权限归一化」（group → infra(1005)、mode → 2775），
#   容器内的服务用户随即全部失配（它们不在宿主的 infra 组里）：
#     · gitlab：nginx 连不上 workhorse socket（srwxrwsr-x root:1005）→ 全站 502
#     · redis ：RDB 落盘 Permission denied → stop-writes-on-bgsave-error → 写命令全被拒
#   而且这种故障「docker ps 看着是 Up」，只有功能探针才测得出来。
#
# 本脚本两层：
#   ① 功能探针：gitlab 出 HTTP / redis 能落盘 / PG 能写 / minio 活着 / dev 容器挂载源还在
#   ② 规范自愈：按各容器自己的官方动作修（redis & PG 改属主、gitlab 走 reconfigure）
#
# 用法：
#   bash scripts/infra_doctor.sh              # 只体检（只读，不改任何东西）
#   bash scripts/infra_doctor.sh --fix        # 发现故障就修
#   bash scripts/infra_doctor.sh --quiet      # 只在有故障时输出（给 systemd timer 用）
#   bash scripts/infra_doctor.sh --only Redis --fix   # 只体检/只修匹配的项（定点处置）
#
# 退出码：0 = 正常或已修复；1 = 存在故障（--fix 后仍失败）
#
# 注意：核心教训是「别在宿主侧递归 chown/chgrp/chmod 容器数据目录」。
#       要给 infra 组共享读写，请用 ACL（附加式，不动 owner/group/mode）：
#           setfacl -R -m g:infra:rwX -m d:g:infra:rwX /srv/infra/<dir>

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GITLAB_URL="${GITLAB_URL:-http://10.80.153.12/}"
MINIO_HEALTH="${MINIO_HEALTH:-http://127.0.0.1:9000/minio/health/live}"
PG_DEV_CONTAINER="${PG_DEV_CONTAINER:-pgvector-rag}"   # 本项目主库
GITLAB_CONTAINER="${GITLAB_CONTAINER:-gitlab}"
REDIS_CONTAINER="${REDIS_CONTAINER:-redis}"
PG_DEV_DB="${PG_DEV_DB:-nbhx_dev}"

FIX=0
QUIET=0
ONLY=""          # --only <正则>：只跑匹配的检查项（定点体检/修复用）
while [[ $# -gt 0 ]]; do
  case "$1" in
    --fix) FIX=1 ;;
    --quiet|-q) QUIET=1 ;;
    --only) ONLY="${2:-}"; shift ;;
    -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
  shift
done

declare -a STATUS=()      # ok|名称 / fixed|名称|原因 / fail|名称|原因

say()  { (( QUIET )) || printf '%s\n' "$*"; }
ok()   { (( QUIET )) || printf '  \033[32m✅\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m❌\033[0m %s\n' "$*"; }
warn() { (( QUIET )) || printf '  \033[33m⚠️\033[0m  %s\n' "$*"; }

have()  { docker inspect "$1" >/dev/null 2>&1; }
up()    { [[ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" == "true" ]]; }

# ---------------------------------------------------------------------------
# 探针
# ---------------------------------------------------------------------------

# GitLab：HTTP 出 200/301/302 才算活（502 = nginx 连不上 workhorse）
probe_gitlab() {
  have "${GITLAB_CONTAINER}" || { echo "容器不存在"; return 1; }
  up "${GITLAB_CONTAINER}"   || { echo "容器没在跑"; return 1; }
  local code
  code="$(curl -s -o /dev/null -m 10 -w '%{http_code}' "${GITLAB_URL}" 2>/dev/null || true)"
  case "${code}" in
    200|301|302) return 0 ;;
    *) echo "HTTP ${code}（期望 200/301/302）$(gitlab_socket_hint)"; return 1 ;;
  esac
}

# 502 时给出精确诊断：workhorse socket 的属主/权限
gitlab_socket_hint() {
  local s
  s="$(docker exec "${GITLAB_CONTAINER}" stat -c '%A %u:%g' /var/opt/gitlab/gitlab-workhorse/sockets/socket 2>/dev/null || true)"
  [[ -n "${s}" ]] && printf '；socket=%s（容器内 git=998、gitlab-www=999）' "${s}"
}

# Redis：① 服务用户能在数据目录建文件（决定性）② 服务响应 ③ RDB 落盘状态
redis_pw() {
  docker inspect "${REDIS_CONTAINER}" --format '{{range .Config.Cmd}}{{.}} {{end}}' 2>/dev/null \
    | sed -n 's/.*--requirepass[= ]\([^ ]*\).*/\1/p'
}
# 监听端口也从命令行解析（默认 6379），否则探针只对默认端口的实例有效
redis_port() {
  local p
  p="$(docker inspect "${REDIS_CONTAINER}" --format '{{range .Config.Cmd}}{{.}} {{end}}' 2>/dev/null \
    | sed -n 's/.*--port[= ]\([0-9]\+\).*/\1/p' | head -1)"
  echo "${p:-6379}"
}
redis_cli() {
  docker exec -e REDISCLI_AUTH="$(redis_pw)" "${REDIS_CONTAINER}" \
    redis-cli -p "$(redis_port)" "$@" 2>/dev/null
}
probe_redis() {
  have "${REDIS_CONTAINER}" || { echo "容器不存在"; return 1; }
  up "${REDIS_CONTAINER}"   || { echo "容器没在跑"; return 1; }
  docker exec -u redis:redis "${REDIS_CONTAINER}" sh -c 'touch /data/.doctor-probe 2>/dev/null && rm -f /data/.doctor-probe' \
    || { echo "redis 用户在 /data 里建不了文件（RDB 落盘会失败）"; return 1; }
  [[ "$(redis_cli ping)" == "PONG" ]] || { echo "redis-cli ping 无 PONG"; return 1; }
  redis_cli info persistence | grep -q 'rdb_last_bgsave_status:ok' \
    || { echo "rdb_last_bgsave_status 不是 ok（写命令会被 MISCONF 拒绝）"; return 1; }
  return 0
}

# PostgreSQL 系（pgvector-rag / postgres）：能连 + postgres 能在 PGDATA 建文件
probe_pg() {
  local c="$1"
  have "$c" || { echo "容器不存在"; return 1; }
  up "$c"   || { echo "容器没在跑"; return 1; }
  docker exec "$c" pg_isready -q 2>/dev/null || { echo "pg_isready 失败"; return 1; }
  docker exec -u postgres "$c" sh -c 'touch "$PGDATA/.doctor-probe" 2>/dev/null && rm -f "$PGDATA/.doctor-probe"' \
    || { echo "postgres 用户在 PGDATA 里建不了文件"; return 1; }
  return 0
}

probe_minio() {
  have minio || { echo "容器不存在"; return 1; }
  local code
  code="$(curl -s -o /dev/null -m 6 -w '%{http_code}' "${MINIO_HEALTH}" 2>/dev/null || true)"
  [[ "${code}" == "200" ]] || { echo "health/live HTTP ${code}"; return 1; }
  return 0
}

# dev 容器：每个 bind mount 的源在宿主上必须还在（目录改名后挂载会指向消失的路径，
# 而容器靠旧 inode 照跑 —— 就是这么骗过 `docker ps` 的）
# 注意：别人的 home 是 700，读不到 ≠ 不存在，必须区分 ENOENT 与 EACCES，否则误报。
probe_dev_mounts() {
  local c src out bad=0 skipped=0
  for c in $(docker ps --format '{{.Names}}' | grep -E '^nbhx-.*-dev-1$' || true); do
    while IFS= read -r src; do
      [[ -n "${src}" ]] || continue
      if out="$(stat -L -c %n "${src}" 2>&1)"; then
        continue                                    # 存在，正常
      fi
      case "${out}" in
        *"Permission denied"*|*"permission denied"*)
          skipped=$(( skipped + 1 ))                # 无权查看（别人的 home），跳过
          ;;
        *)
          echo "${c} 的挂载源不存在：${src}（容器看着 Up，其实靠旧 inode 在跑；重建：bash scripts/dev.sh docker up）"
          bad=1
          ;;
      esac
    done < <(docker inspect "$c" --format '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}}{{"\n"}}{{end}}{{end}}' 2>/dev/null)
  done
  (( skipped )) && warn "有 ${skipped} 个挂载源因权限（别人的 home 700）无法核验，已跳过"
  [[ ${bad} -eq 0 ]]
}

# 静态漂移指纹：容器数据目录里出现「带 setgid 位的普通文件」= 被宿主侧 chmod -R 刷过
# （正常数据文件不会有 setgid；这不会让服务立刻挂，但说明有人动过整棵树）
drift_scan() {
  local d n total=0
  for d in /srv/infra/gitlab /srv/infra/redis /srv/infra/pgvector_data /srv/infra/paddle; do
    [[ -d "${d}" ]] || continue
    n="$(timeout 90 find "${d}" -maxdepth 4 -type f -perm -2000 2>/dev/null | head -500 | wc -l)"
    if [[ "${n}" -gt 0 ]]; then
      warn "${d}：${n}${n:+$([ "${n}" -ge 500 ] && echo '+')} 个普通文件带 setgid 位（宿主侧 chmod -R 的指纹）"
      total=$(( total + n ))
    fi
  done
  [[ ${total} -eq 0 ]]
}

# ---------------------------------------------------------------------------
# 自愈（只做各容器自己的规范动作）
# ---------------------------------------------------------------------------
# GitLab 数据子树 → 期望属主（容器内数字 uid:gid，宿主侧同数字）。
# 依据：omnibus 服务用户（getent passwd）+ chef 自己 reconfigure 时的选择
#        （git-data/repositories→998:998、uploads→998:998、shared→998:999、registry→993:993），两处一致。
# 为什么需要它：reconfigure 只重刷它**声明过的目录**，数据文件（PG 的 global/pg_control、
# redis 的 dump.rdb、gitaly 仓库、prometheus 时序块…）不在 chef 管辖内 —— 2026-09-16 实测：
# 正是 PG 数据文件还是 root:1005 导致 postgres PANIC → chef 永远卡在「等 postgresql」→ 容器重启循环。
GITLAB_OWNER_MAP=(
  "postgresql:996:996"
  "redis:997:997"
  "prometheus:992:992"
  "registry:993:993"
  "gitaly:998:998"
  "git-data:998:998"
  "gitlab-shell:998:998"
  "gitlab-workhorse:998:998"
  "gitlab-ci:998:998"
  "gitlab-kas:998:998"
  "gitlab-rails/uploads:998:998"
  "gitlab-rails/shared:998:999"
  "nginx:999:999"
)

# 日志树同理：/var/log/gitlab/<服务> 下的**文件本体**也不归 chef 管（它只修目录），
# 2026-09-16 实测：rails 日志还是 root:1005 → puma 打不开 application_json.log → EACCES → 起不来。
GITLAB_LOG_MAP=(
  "gitlab-rails:998:998"
  "puma:998:998"
  "sidekiq:998:998"
  "gitlab-shell:998:998"
  "gitaly:998:998"
  "gitlab-workhorse:998:998"
  "gitlab-kas:998:998"
  "gitlab-ci:998:998"
)

fix_gitlab_ownership() {
  local spec sub uid gid n
  for spec in "${GITLAB_OWNER_MAP[@]}"; do
    IFS=: read -r sub uid gid <<<"${spec}"
    docker exec "${GITLAB_CONTAINER}" test -d "/var/opt/gitlab/${sub}" 2>/dev/null || continue
    n="$(docker exec "${GITLAB_CONTAINER}" find "/var/opt/gitlab/${sub}" \! -uid "${uid}" 2>/dev/null | head -50 | wc -l)"
    if [[ "${n}" -gt 0 ]]; then
      say "  → 属主漂移 /var/opt/gitlab/${sub}（抽样 ${n}$([ "${n}" -ge 50 ] && echo '+') 个不属于 uid ${uid}）→ chown -R ${uid}:${gid}"
      docker exec -u 0 "${GITLAB_CONTAINER}" chown -R "${uid}:${gid}" "/var/opt/gitlab/${sub}" >/dev/null 2>&1
    fi
  done
  for spec in "${GITLAB_LOG_MAP[@]}"; do
    IFS=: read -r sub uid gid <<<"${spec}"
    docker exec "${GITLAB_CONTAINER}" test -d "/var/log/gitlab/${sub}" 2>/dev/null || continue
    n="$(docker exec "${GITLAB_CONTAINER}" find "/var/log/gitlab/${sub}" \! -uid "${uid}" 2>/dev/null | head -50 | wc -l)"
    if [[ "${n}" -gt 0 ]]; then
      say "  → 日志属主漂移 /var/log/gitlab/${sub}（抽样 ${n}$([ "${n}" -ge 50 ] && echo '+') 个不属于 uid ${uid}）→ chown -R ${uid}:${gid}"
      docker exec -u 0 "${GITLAB_CONTAINER}" chown -R "${uid}:${gid}" "/var/log/gitlab/${sub}" >/dev/null 2>&1
    fi
  done
  # 事故遗留的 socket 文件：服务重建不了别人留下的 socket，只能删掉让它自己重开
  # （判据 gid=1005 且 uid=0，正好是那次 chown/chmod 的指纹；正常的 socket 都是服务自己属主）
  local stale
  stale="$(docker exec "${GITLAB_CONTAINER}" find /var/opt/gitlab -type s -uid 0 -gid 1005 -print 2>/dev/null)"
  if [[ -n "${stale}" ]]; then
    say "  → 事故遗留 socket（服务连不上/建不了，删掉让服务重建）："
    printf '%s\n' "${stale}" | sed 's/^/      /'
    docker exec -u 0 "${GITLAB_CONTAINER}" find /var/opt/gitlab -type s -uid 0 -gid 1005 -delete >/dev/null 2>&1
  fi
  return 0
}

fix_gitlab() {
  # ⚠️ 不能直接 `gitlab-ctl reconfigure`：官方镜像 entrypoint 启动时自己会跑一遍，
  #    手动再跑会两个 chef 并发死锁（手册 §0 不变量 #2，2026-09-15 踩过）。
  #    规范动作是重启容器 —— entrypoint 会干净地重跑一遍 reconfigure，重刷它管辖的属主/权限。
  #    但数据文件不归它管，所以先补一刀针对性 chown，再重启。
  fix_gitlab_ownership
  say "  → gitlab：docker restart -t 60（entrypoint 重跑 reconfigure，2–6 分钟，最多等 12 分钟）"
  docker restart -t 60 "${GITLAB_CONTAINER}" >/dev/null 2>&1
  local i
  for i in $(seq 1 72); do
    probe_gitlab >/dev/null 2>&1 && return 0
    (( i % 6 == 0 )) && say "     … 仍在等 GitLab 就绪（已 $(( i * 10 ))s）"
    sleep 10
  done
  # 超时：把最可能的卡点（chef 等 postgresql）原文捞出来，省得人工再翻日志
  echo "  重启后 12 分钟仍未恢复。最可能的卡点（chef 卡在等某个服务）：" >&2
  docker exec "${GITLAB_CONTAINER}" sh -c 'tail -3 /var/log/gitlab/postgresql/current 2>/dev/null' | sed 's/^/    PG: /' >&2
  return 1
}
fix_redis() {
  say "  → redis：chown -R redis:redis /data + bgsave"
  docker exec -u 0 "${REDIS_CONTAINER}" chown -R redis:redis /data >/dev/null 2>&1
  redis_cli bgsave >/dev/null 2>&1
  sleep 2
  probe_redis >/dev/null 2>&1
}
fix_pg() {
  local c="$1"
  say "  → ${c}：chown -R postgres:postgres \$PGDATA"
  docker exec -u 0 "$c" sh -c 'chown -R postgres:postgres "$PGDATA"' >/dev/null 2>&1
  probe_pg "$c" >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
say "infra 体检 $(date '+%F %T')"
(( FIX )) && say "（--fix 已开启：发现故障会自动修）"
say ""

run_check() {           # run_check <名字> <探针> [修复函数] [修复参数]
  local name="$1" probe="$2" fixfn="${3:-}" fixarg="${4:-}" reason
  if [[ -n "${ONLY}" ]] && ! grep -qiE "${ONLY}" <<<"${name}"; then
    return 0
  fi
  if reason="$("${probe}" ${fixarg} 2>&1)"; then
    STATUS+=("ok|${name}")
    ok "${name}"
    return 0
  fi
  bad "${name} —— ${reason}"
  if (( FIX )) && [[ -n "${fixfn}" ]] && "${fixfn}" ${fixarg}; then
    STATUS+=("fixed|${name}|${reason}")
    printf '  \033[32m↳ 已修复\033[0m\n'
    return 0
  fi
  STATUS+=("fail|${name}|${reason}")
  (( FIX )) && [[ -n "${fixfn}" ]] && bad "  ↳ 自动修复失败，需人工介入"
  return 1
}

run_check "GitLab（HTTP）"          probe_gitlab        fix_gitlab
run_check "Redis（落盘/写入）"       probe_redis         fix_redis
run_check "pgvector-rag（项目主库）" probe_pg            fix_pg  "${PG_DEV_CONTAINER}"
run_check "PostgreSQL（共享实例）"   probe_pg            fix_pg  postgres
run_check "MinIO（health）"          probe_minio
run_check "开发容器挂载源"            probe_dev_mounts

say ""
if [[ -z "${ONLY}" ]]; then
  drift_scan || true
fi

# 附：本项目主库的最小可用性抽查（能连就读一次版本）
if [[ -z "${ONLY}" ]] && have "${PG_DEV_CONTAINER}" && up "${PG_DEV_CONTAINER}"; then
  v="$(docker exec "${PG_DEV_CONTAINER}" psql -U root -d "${PG_DEV_DB}" -tAc "select extversion from pg_extension where extname='vector'" 2>/dev/null | tr -d '[:space:]')"
  [[ -n "${v}" ]] && ok "pgvector 扩展 ${v}（库 ${PG_DEV_DB}）"
fi

say ""
if printf '%s\n' "${STATUS[@]}" | grep -q '^fail|'; then
  echo "结论：有故障（上面 ❌ 的项需人工介入）"
  exit 1
fi
fixed_n="$(printf '%s\n' "${STATUS[@]}" | grep -c '^fixed|' || true)"
if (( fixed_n > 0 )); then
  echo "结论：全部正常（其中 ${fixed_n} 项由本次 --fix 修复）"
  exit 0
fi
echo "结论：全部正常"
exit 0
