# 开发环境容器化 · 管理员手册

> 你是「镜像 + registry + 新同事接入」的负责人。同事视角的手册在 [docker-dev-guide-colleague.md](docker-dev-guide-colleague.md)；
> 完整设计说明与实测数据在 [docker-dev-env.md](docker-dev-env.md)。相关 issue：#9–#13。

---

## 0. 四个不变量（先记住，能避掉 90% 的坑）

1. **代码在宿主，环境在镜像** → 改代码不用重建；**改依赖必须重建**（`git pull` 不会更新环境）。
2. **绝不要手动跑 `gitlab-ctl reconfigure`**（除了极少数场景）—— 官方 gitlab 镜像的 entrypoint 启动时**自己会跑一遍**，你再跑第二遍会与它并发，两个 chef 互相删对方等待的 supervise socket → **死锁 + GitLab 全部服务下线**。2026-09-15 实测踩过，只能 `docker restart gitlab` 恢复。
3. **docker 危险命令黑名单**：`docker system prune -a --volumes`、`docker volume prune`、`docker image prune -a` —— 这台机器上跑着 GitLab / pgvector / Redis / MinIO / CI Runner 和其他同事的项目。
4. **绝不对容器数据目录做宿主侧递归 `chown` / `chgrp` / `chmod`**（`/srv/infra/**`，尤其 `gitlab/`、`redis/data`、`pgvector_data`）—— 里面的 owner/group/mode 是**容器内服务身份**（git 998、gitlab-www 999、redis 999:1000、postgres 999），宿主侧一刷就全部失配，服务「看着 Up、功能已废」。2026-09-16 实测踩过（详见 §4.2）。**要给 `infra` 组共享读写，用 ACL**（附加式，不动 owner/group/mode）：
   ```bash
   sudo setfacl -R -m g:infra:rwX -m d:g:infra:rwX /srv/infra/<dir>   # 只给权限，不改属主
   ```
   巡检用 `bash scripts/infra_doctor.sh`（见 §6），自愈定时器见 §4.2。

---

## 1. 改了 requirements 之后，怎么更新镜像（核心流程）

```bash
# ── ① 本地重建（只有变化的层重跑；uv 的 build cache 让重复下载最小）
bash scripts/dev.sh docker build

# ── ② 三连验证（缺一不可）
bash scripts/dev.sh docker py -c "import main"
bash scripts/dev.sh docker test -q
bash scripts/dev.sh docker guard
#     有前端改动再加：bash scripts/dev.sh docker fe-setup && bash scripts/dev.sh docker frontend（人工点一下）

# ── ③ 用新镜像重建容器，确认能用
bash scripts/dev.sh docker up

# ── ④⑤ 打 tag 并推送（一条命令搞定）
docker login 10.80.153.12:5050      # 首次/凭证过期时：用户名=GitLab 用户名，密码=PAT（scope 含 read_registry+write_registry 或 api）
bash scripts/dev.sh docker push
#   它会自动打两个 tag 并推送：
#     py312-cu130-<依赖指纹12位>   ← 可复现。指纹 = 四个 requirements 文件的 sha256 前 12 位，
#                                    与镜像内 /opt/venv/.requirements-hash、`docker check` 打印的一致
#     py312-cu130                 ← 「当前版本」移动 tag，同事 pull 拿到的就是它
#   ⚠️ 不要用 `sha256sum requirements.txt | cut -c1-8` 那种只哈希主清单的写法：
#      只改 rag/dev 清单时 tag 不变 → 同一个 tag 指向两个不同镜像。

# ── ⑥ 提交代码 + 通知
git add requirements*.txt docker/ && git commit -m "chore: 依赖变更" && git push
#    群里：「镜像已更新到 <TAG>，大家 docker pull && up」
```

**镜像层数说明**（决定推送成本）：`python:3.12-slim` 基座 → apt（Node/uv 等）→ **主依赖层 ~11.4GB** → RAG 层 → dev 层。
只改业务代码：镜像不变；改 `requirements.txt`：主依赖层变 → 重传 ~12GB。**所以不要每 commit 打 tag。**

**镜像大小现状**：磁盘 **19GB**／按层汇总（≈推拉传输量）**12.3GB**。
等 issue #9 / #10 完成（OCR / TagGenerator 服务化）后，可再瘦 ~8.5GB（paddle 3.1G + torch/nvidia 5.4G），
届时删掉 `docker/dev.Dockerfile` 里对应两层 + `requirements*.txt` 里的包即可 → 估算 **~4GB 量级**。

---

## 2. 命名与 tag 约定

| 东西 | 规则 | 例子 |
|---|---|---|
| 本地镜像 | `nbhx-dev:local` | — |
| registry 镜像 | `10.80.153.12:5050/carl_jia/ragchatbot/nbhx-dev:<tag>` | — |
| 「当前版本」tag | `py312-cu130`（**移动** tag，每次 push 都更新） | 同事 `pull` 拿到的就是它 |
| 可复现 tag | `py312-cu130-<依赖指纹 12 位>`（四个 requirements 文件，`dev.sh docker push` 自动算） | 回滚 / 排查用 |
| compose 项目名 | `nbhx-${USER}`（`dev.sh` 自动加 `-p`） | `nbhx-jiazhenyu` |
| 容器名 | `nbhx-<user>-dev-1` | `nbhx-jiazhenyu-dev-1` |
| 数据卷 | `nbhx-<user>_nbhx-{cache,node-modules,pnpm-store}` | 缓存落在 `/var/lib/docker`（NVMe） |

---

## 3. Registry 运维

| 项 | 值 |
|---|---|
| 地址 | `http://10.80.153.12:5050`（**http**，所以每台客户端要配 `insecure-registries`） |
| 启用方式 | `sudo bash scripts/enable_gitlab_registry.sh`（幂等：备份 compose → 打补丁 → 重建容器 → 等 entrypoint 的 reconfigure → 验证 401） |
| 客户端放行 | `sudo bash scripts/enable_insecure_registry.sh`（**每台要推拉的机器都要跑一次**；优先 `reload` 不重启容器，必要时加 `--restart`） |
| 存储 | `/var/opt/gitlab/gitlab-rails/shared/registry` → 宿主 `/srv/infra/gitlab/data/...`（**固态**），`delete.enabled=true` |
| 回收 | `docker exec gitlab gitlab-ctl registry-garbage-collect -m`（切只读模式，需维护窗口） |
| 配置源 | **两个，且 bind mount 优先**：compose 的 `GITLAB_OMNIBUS_CONFIG`（`/assets/gitlab.rb` 里 `eval`）→ 再 `from_file("/srv/infra/gitlab/config/gitlab.rb")` |
| 改配置 | 改 `/srv/infra/docker-compose.yml` 的 env → **重建容器**（`docker compose up -d gitlab`）；只 `reconfigure` 读不到新 env |

### 3.1 推送失败：`400 manifest blob unknown`（2026-09-15 实测踩过）

**现象**：`docker push` 失败，报 `manifest blob unknown`，detail 里常带 `sha256:4f4fb700ef54…`（OCI 空 blob）。
判据是 registry 请求状态码分布 —— **层全部成功、只有 manifest 400**：

```bash
docker exec gitlab sh -c 'grep -o "\"method\":\"[A-Z]*\".*\"status\":[0-9]*" /var/log/gitlab/registry/current \
  | sed "s/.*\"method\":\"\([A-Z]*\)\".*\"status\":\([0-9]*\)/\1 \2/" | sort | uniq -c | sort -rn'
# 故障时：23 POST 202 / 23 PUT 201（层都成功）+ 4 PUT 400（manifest 全失败）
```

**实测结论（重要，别误判）**：
- 报「unknown」的那 6 个 blob，**当时其实已经上传成功**（事后查 registry DB 全都在）；
- manifest PUT 与最后一批 blob 提交**发生在同一秒**（07:12:05）→ 更像是 **registry 侧的一致性竞态**（校验 manifest 时 blob 记录尚未可见）；
- **第 2 次推送就成功了**，而且只传了 2 个 manifest（`25 PUT 201` = 23 层 + 2 manifest），层 blob 一个都没重传。

**处理顺序**：
1. **先直接重推**（最便宜：层 blob 已在 registry，客户端 HEAD 到 200 会跳过上传）。
2. 若反复出现 → 用下面的查询确认 registry 里到底缺不缺 blob；确实缺就是上传中断，重推即可。
3. 顺手做的加固（已写入 `docker/compose.dev.yaml`）：`provenance: false` + `sbom: false` —— 内部开发镜像不需要 SLSA 证明清单，
   去掉它可以少推 attestation manifest 及其额外 blob（`44136fa3…` 是 sha256("{}")，就是 attestation 的 config）。**这不是已证实的主因，只是减少一类无关产物。**

**完整性校验（可复用诊断）**：把 manifest 引用的 blob 与 registry DB 里的 blob 对一遍。
⚠️ DB 里 bytea 存的是「算法字节 + sha256」（`01` 前缀），比对前要去掉。

```bash
REPO=carl_jia/ragchatbot/nbhx-dev
docker exec gitlab gitlab-psql -d registry -t -c "select encode(digest,'hex') from blobs;" | tr -d ' ' | grep -v '^$' | sed 's/^01//' | sort -u > /tmp/have.txt
# manifest 里的 layers/config digest（用带 Bearer token 的 GET /v2/<repo>/manifests/<tag> 拿到，见 gitlab.rb 的 /jwt/auth）
# 然后 diff 两者；实测结果：21 个引用 blob(1 config + 20 层) 全部在 → ✅ 完整可拉
```

---

## 4. 事故处置：GitLab 卡住 / 服务全掉

**症状**：`docker exec gitlab gitlab-ctl status` 只剩 `sshd`；网页 000；日志停在 `ruby_block[wait for XXX service socket]`；`ps` 里 chef 进程 CPU≈0。
**原因**（几乎都是）：**并发跑了两遍 reconfigure**（手动 + entrypoint）。
**处置**：

```bash
docker restart -t 60 gitlab          # 杀掉挂死的 chef，重启后 entrypoint 会干净地跑一遍
# 之后等 2–5 分钟，验证：
docker ps --filter name=gitlab                       # 期望 (healthy)
curl -s -o /dev/null -w '%{http_code}\n' http://10.80.153.12/     # 期望 302
docker exec gitlab gitlab-ctl status | head          # 期望十来个 run: 服务
```
**数据安全**：GitLab 所有状态都在 bind mount（`/srv/infra/gitlab/{config,logs,data}`），重启容器不丢数据。

### 4.2 容器数据目录被宿主侧「权限归一化」刷坏（2026-09-16 实测，GitLab + Redis 同时中招；registry 存储子树两天后才暴露，见 §4.3）

**症状**：`docker ps` 全是 Up，但功能已废 ——
- GitLab 全站 **502**（返回它自己的 `Waiting for GitLab to boot` 页），`git push` / `git ls-remote` 全部 HTTP 502；容器**内部**自测 `curl localhost:80` 也是 502（所以不是网络问题）。
- Redis 写命令被拒：`MISCONF Redis is configured to save RDB snapshots, but it's currently unable to persist to disk...`；`info persistence` 里 `rdb_last_bgsave_status:err`。**缓存/限流/任务状态全部写不进去**（三个开发容器共用这一个 Redis）。
- 两个都在 `docker logs` 里刷：gitlab nginx `connect() to unix:/var/opt/gitlab/gitlab-workhorse/sockets/socket failed (13: Permission denied)`；redis `Failed opening the temp RDB file temp-NNN.rdb ... Permission denied`。

**根因**：有人从**宿主侧**对 `/srv/infra` 做了一次递归「权限归一化」—— group 刷成 `infra(1005)`、普通文件 mode 刷成 `2775`。容器里的服务用户不在宿主 `infra` 组里（`git=998`、`gitlab-www=999`、`redis=999:1000`、`postgres=999`），目录/socket 的 group 位一夜失效。
**取证三步**（下次照抄）：
```bash
# ① ctime 指纹：mtime 没变、ctime 变了 = 有人只改了元数据（属主/权限），不是重建
docker exec gitlab stat -c 'mtime=%y ctime=%z %A %U:%G %n' /var/opt/gitlab/gitlab-workhorse/sockets/socket
# ② 破坏范围：全机找同一时刻被改的路径（含 /data、/opt、/home、docker 卷，确认没扩散）
find /srv /data /opt /home /var/lib/docker/volumes -maxdepth 4 -newerct "2026-09-16 11:26:50" ! -newerct "2026-09-16 11:27:20"
# ③ 破坏指纹：容器数据目录里出现「带 setgid 位的普通文件」= 被 chmod -R 刷过
find /srv/infra/gitlab -maxdepth 4 -type f -perm -2000 | head
```
**规范修复**：
```bash
bash scripts/infra_doctor.sh            # ① 先体检（只读）：谁坏了、坏在哪
bash scripts/infra_doctor.sh --fix      # ② 自愈：redis/pg 改属主 + bgsave；gitlab 走 docker restart
bash scripts/infra_doctor.sh --only Redis --fix   # 只处理某一项（定点）
# 或手动：
docker exec -u 0 redis chown -R redis:redis /data && docker exec -e REDISCLI_AUTH=… redis redis-cli bgsave
docker restart redis                    # redis 也可以直接重启：entrypoint 的 `find . ! -user redis -exec chown redis`
                                        #   连 /data 目录本身一起 chown（实测），落盘立刻恢复
docker restart -t 60 gitlab             # ⚠️ 不要 gitlab-ctl reconfigure（§0 不变量 #2）
```
**⚠️ 安全副作用（这次特有的）**：`chmod 2775` 把 `/srv/infra/gitlab/config/` 下的敏感文件也刷成了 **world-readable**（`gitlab.rb`、`gitlab-secrets.json`、**`ssh_host_rsa_key`**）。修完权限后要复核：
```bash
docker exec gitlab stat -c '%A %U:%G %n' /etc/gitlab/gitlab-secrets.json /etc/gitlab/gitlab.rb   # 期望 600 root:root
stat -c '%A %U:%G %n' /srv/infra/gitlab/config/ssh_host_rsa_key                                  # 期望 600 root:root
```
若 ssh host key 曾以 2775 暴露，建议重新生成（`rm` 掉 `ssh_host_*` 后重启容器会重建，客户端首次连接需重新确认指纹）。

**为什么"以后不再出现"要靠三道防线**（都已在仓库里）：
| 防线 | 内容 |
|---|---|
| ① 不再产生触发点 | §0 不变量 #4：容器数据目录禁止宿主侧递归 chown/chgrp/chmod；要共享用 `setfacl` |
| ② 坏了能自动发现并自愈 | `scripts/infra_doctor.sh`（功能探针，不只看 Up）+ `deploy/infra-doctor.{service,timer}.template`（10 分钟一轮） |
| ③ 备份/迁移别再带回来 | rsync 必须 `-aHAX --numeric-ids`（保留 uid/gid/ACL/xattr），否则恢复一次就把同一类问题带回来 |

**装自愈定时器**（一次性，root）：
```bash
sed "s|__ROOT__|/data/jiazhenyu/RAG/project-nbhx|g" deploy/infra-doctor.service.template \
  | sudo tee /etc/systemd/system/infra-doctor.service >/dev/null
sudo cp deploy/infra-doctor.timer.template /etc/systemd/system/infra-doctor.timer
sudo systemctl daemon-reload && sudo systemctl enable --now infra-doctor.timer
systemctl list-timers infra-doctor.timer          # 看下一轮
journalctl -u infra-doctor -n 50 --no-pager       # 看历史结果（正常时是空的，--quiet）
```

**为什么不能用"给容器加 `group_add`"来免疫**（实测过）：redis 官方 entrypoint 用 `setpriv --clear-groups`、postgres 用 `gosu`（initgroups）、gitlab 用 `chpst -P` / nginx `user gitlab-www` —— 服务进程的补充组一律由**容器内 `/etc/group`** 决定，docker 的 `group_add` 只作用于 PID 1，传不到服务进程。要真免疫只能派生镜像（把 gid 1005 加进容器内服务用户的组），代价是每次上游升版都要重做，性价比低。

### 4.3 Registry 推送 500 —— §4.2 的第三个受害者（2026-09-18 发现）

**症状**：`docker push` 新层一个都没传（全 `Waiting`），最终 `error from registry: unknown error`；
但已存在的层 HEAD 全 200（`Layer already exists`），pull 正常。registry 日志特征：
`POST /v2/<repo>/blobs/uploads/` **秒回 500**（duration_ms≈1）——与 §3.1 的 manifest 竞态
（层全传成功、只有 manifest PUT 400）**不是一回事**，别混淆。

**根因**：§4.2 的递归权限归一化把 `shared/registry/docker/` 整棵子树（13GB，blobs +
repositories 元数据）刷成了 `git:gitlab-www 2775`。registry 进程跑在 `registry` 用户
（993:993）下——既非属主也不在组，创建上传目录即失败。顶层 `shared/registry/` 当时
被修回 `registry:git`，但**子树没有任何进程会自动修复**；infra_doctor 只探读路径
（GET/HEAD，other 可读），探不出写路径损坏 → 拖了两天、到下一次 push（issue-15 依赖
更新）才暴露。

**修复**（§0 不变量 #4 允许的容器内模式，宿主侧禁止递归 chmod/chown）：

```bash
docker exec -u 0 gitlab chown -R registry:git /var/opt/gitlab/gitlab-rails/shared/registry/docker
# 验证（以 registry 用户身份写入）：
docker exec -u registry gitlab sh -c \
  'cd /var/opt/gitlab/gitlab-rails/shared/registry/docker/registry/v2/repositories/<repo路径> \
   && touch _uploads/t && rm _uploads/t && echo WRITE_OK'
```

修完**直接重推**即可。实测：重推一次成功，~20 层全部 `Pushed`，两个 tag 正常落库。

**教训**：这类「权限刷坏」事故的修复必须**整棵数据树核对属主**，别只看顶层——
受害者会随功能被使用陆续暴露（09-16 当天 GitLab 502 / Redis MISCONF，
09-18 registry push）。日后遇到「读都正常、写秒挂」的存储类服务，先查属主再查别的。

---

## 5. 磁盘与缓存回收

```bash
docker system df                    # 总览：Images / Containers / Local Volumes / Build Cache
docker builder prune --keep-storage 8GB    # ✅ 安全且收益最大（纯缓存，不动镜像/卷/数据）
```

| 类别 | 动不动 | 说明 |
|---|---|---|
| **Build Cache** | ✅ 可以清 | 只是构建缓存；代价是下次 build 重新下载 wheel（~8GB） |
| Local Volumes | ⚠️ 点名删 | 未使用的 `runner-*` 是 CI 缓存；**不要用 `volume prune`**（会删停掉的项目的卷） |
| Images | ❌ 基本别动 | 「可回收」的几乎都是**别人的**镜像（同事项目、CI、ubuntu 多版本） |

宿主的 `.venv`+`.cache` 已删（回收 12–13GB）。**注意**：这两个必须**一起删**才会真正回收（uv 用硬链接，单独删一个不释放空间）。

---

## 6. 日常巡检（30 秒）

```bash
bash scripts/infra_doctor.sh                                    # 全机容器体检（只读，30 秒内出结论）
bash scripts/dev.sh docker ps                                   # 自己的容器
bash scripts/dev.sh docker check                                # 本地依赖 vs 镜像指纹
curl -s -o /dev/null -w '%{http_code}\n' http://10.80.153.12:5050/v2/   # registry：期望 401
docker exec gitlab gitlab-ctl status registry                   # registry 服务：期望 run:
docker exec gitlab sh -c 'du -sh /var/opt/gitlab/gitlab-rails/shared/registry'  # registry 占用
docker system df                                                # 磁盘总览
```

---

## 7. 未完成事项（别忘）

| 事项 | issue | 影响 |
|---|---|---|
| PaddleOCR 拆成独立容器 | #9 | 本地 OCR 现在走 CPU（慢）；完成后 dev 镜像瘦 3.1GB |
| TagGenerator 拆成独立容器 | #10 | 完成后可去 torch/nvidia（再瘦 5.4GB），dev 容器彻底无 GPU 需求 |
| ~~前端 dev 网络可配置化~~ | #12 | ✅ 已完成（2026-09-16）：WS 默认走同源 vite 代理，多人端口实测互不串；`.env` 已拆分（模板 `env.example`），详见 `docs/issues/004` |
| `.env.production` 泄露密钥 | #13 | 已移出 git 跟踪并补 `.gitignore` 的 `.env.*` 规则（2026-09-16）；历史 key 轮换 / 历史清理仍待做，见 `docs/issues/005` |
| 工作副本是否迁 NVMe | #11 | 实测**不必**（venv/缓存已在 NVMe，源码 ~3MB 留在 HDD 无感） |
| /srv/infra/gitlab 74 个普通文件残留 setgid 位（09-16 事故指纹，infra_doctor 持续告警） | — | 不影响功能；维护窗口内容器内清理：`docker exec -u 0 gitlab sh -c 'find /var/opt/gitlab -type f -perm -2000 -exec chmod g-s {} +'`（**禁宿主侧**，§0 不变量 #4） |

---

## 8. 一页速查

```bash
# 改依赖后更新镜像（同事用）
bash scripts/dev.sh docker build && bash scripts/dev.sh docker up
TAG=py312-cu130-$(sha256sum requirements.txt | cut -c1-8)
docker tag nbhx-dev:local 10.80.153.12:5050/carl_jia/ragchatbot/nbhx-dev:$TAG
docker push  10.80.153.12:5050/carl_jia/ragchatbot/nbhx-dev:$TAG
docker tag nbhx-dev:local 10.80.153.12:5050/carl_jia/ragchatbot/nbhx-dev:py312-cu130
docker push  10.80.153.12:5050/carl_jia/ragchatbot/nbhx-dev:py312-cu130

# registry / infra
sudo bash scripts/enable_gitlab_registry.sh          # 启用 registry（幂等）
sudo bash scripts/enable_insecure_registry.sh        # 每台客户端放行 http registry
docker restart -t 60 gitlab                          # GitLab 卡死时的恢复手段

# 回收
docker builder prune --keep-storage 8GB              # 安全
```
