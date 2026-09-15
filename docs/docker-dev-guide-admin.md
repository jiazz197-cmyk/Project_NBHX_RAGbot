# 开发环境容器化 · 管理员手册

> 你是「镜像 + registry + 新同事接入」的负责人。同事视角的手册在 [docker-dev-guide-colleague.md](docker-dev-guide-colleague.md)；
> 完整设计说明与实测数据在 [docker-dev-env.md](docker-dev-env.md)。相关 issue：#9–#13。

---

## 0. 三个不变量（先记住，能避掉 90% 的坑）

1. **代码在宿主，环境在镜像** → 改代码不用重建；**改依赖必须重建**（`git pull` 不会更新环境）。
2. **绝不要手动跑 `gitlab-ctl reconfigure`**（除了极少数场景）—— 官方 gitlab 镜像的 entrypoint 启动时**自己会跑一遍**，你再跑第二遍会与它并发，两个 chef 互相删对方等待的 supervise socket → **死锁 + GitLab 全部服务下线**。2026-09-15 实测踩过，只能 `docker restart gitlab` 恢复。
3. **docker 危险命令黑名单**：`docker system prune -a --volumes`、`docker volume prune`、`docker image prune -a` —— 这台机器上跑着 GitLab / pgvector / Redis / MinIO / CI Runner 和其他同事的项目。

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

# ── ④ 打标签（本地别名 + registry 两个 tag）
TAG=py312-cu130-$(sha256sum requirements.txt | cut -c1-8)     # 可追溯、可回滚
docker tag nbhx-dev:local 10.80.153.12:5050/carl_jia/ragchatbot/nbhx-dev:$TAG
docker tag nbhx-dev:local 10.80.153.12:5050/carl_jia/ragchatbot/nbhx-dev:py312-cu130   # 「当前版本」移动 tag

# ── ⑤ 推送（约 12GB 传输；分层增量，只改业务代码时镜像层没变，**完全不用推**）
docker login 10.80.153.12:5050      # 用户名=GitLab 用户名，密码=PAT（scope 含 read_registry+write_registry 或 api）
docker push 10.80.153.12:5050/carl_jia/ragchatbot/nbhx-dev:$TAG
docker push 10.80.153.12:5050/carl_jia/ragchatbot/nbhx-dev:py312-cu130

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
| 「当前版本」tag | `py312-cu130`（**移动** tag，每次都推） | 同事 `pull` 拿到的就是它 |
| 可复现 tag | `py312-cu130-<requirements.txt 前 8 位 sha>` | 回滚 / 排查用 |
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
| 前端 dev 网络可配置化 | #12 | `VITE_WS_BASE_URL=ws://localhost:8000` 写死，多人多端口时 WS 可能连错 |
| `.env.production` 泄露密钥 | #13 | 已提交的真实 Dify key，需轮换 + 移出 git |
| 工作副本是否迁 NVMe | #11 | 实测**不必**（venv/缓存已在 NVMe，源码 ~3MB 留在 HDD 无感） |

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
