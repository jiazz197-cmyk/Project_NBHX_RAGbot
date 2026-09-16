# 容器化开发环境（dev 容器）

> 相关 issue：#9（PaddleOCR 服务化）、#10（TagGenerator 服务化）、#11（本方案追踪 + 运维前置）、#12（前端 dev 网络）
> 一句话：**环境来自镜像，代码来自 bind mount。** 改代码不用碰镜像，升级依赖才需要重建镜像。

## 1. 目标架构

```
┌─ 开发者笔记本 ──────────────┐      ┌─ 共享 GPU 服务器 10.80.153.12 ──────────────────────┐
│ 编辑器 / 浏览器              │      │  GitLab :80  ← 代码（git pull）                      │
│ SSH 端口转发 8001→容器:8000  │◀────▶│  Registry :5050 ← 环境镜像（待启用，见 #11）          │
└─────────────────────────────┘      │                                                      │
                                     │  ┌─ 你的容器（nbhx-<你>-dev-1）──┐                 │
   「代码在宿主，环境在容器」          │  │ /workspace      ← bind mount     │                 │
   「镜像只存一份，容器共享只读层」     │  │ /opt/venv       ← 镜像层         │                 │
                                     │  │ /workspace/.cache ← 命名卷(NVMe) │                 │
                                     │  └───────────┬────────────────────┘                 │
                                     │              │ host.docker.internal                  │
                                     │  infra（已有，不动）：pgvector:5433 / redis:6379       │
                                     │                      minio:9000                       │
                                     │  外部推理 API：BGE-M3 / Reranker / Qwen               │
                                     │  （过渡期）PaddleOCR 仍在进程内；TagGenerator 已容器化  │
                                     └──────────────────────────────────────────────────────┘
```

## 2. 三条通道，别混

| 命令 | 从哪拉 | 拉到什么 | 影响什么 | 频率 |
|---|---|---|---|---|
| `git pull` | GitLab 代码仓库（:80） | **代码** | 容器里看到的文件 | 每天 |
| `docker compose pull` | GitLab Registry（:5050） | **环境镜像** | 容器用哪套依赖 | 依赖变化时 |
| `docker compose up -d` | — | 用新镜像**重建**容器 | 生效 | 同上 |

**拉镜像不会覆盖你的工作区**：venv 在 `/opt/venv`（镜像层），代码在 `/workspace`（bind mount），两者互不干涉。

## 3. 首次（Day 0）

```bash
# 0) 前置：Docker 可用 + 在 docker 组
docker info >/dev/null && echo ok

# 1) 起容器（首次会构建镜像：下依赖，几分钟到十几分钟）
bash scripts/dev.sh docker up

# 2) 验证环境
bash scripts/dev.sh docker py -c "import main; print('import ok')"
bash scripts/dev.sh docker test -q            # 期望 218 passed
bash scripts/dev.sh docker guard              # 期望通过

# 3) 需要前端时，一次性装依赖（写进命名卷，不污染宿主）
bash scripts/dev.sh docker fe-setup
```

之后**不需要再 source 任何环境、不需要装任何 Python/Node 依赖**。

## 4. 日常内循环（不碰镜像、不碰 registry）

```bash
# 终端 A：后端（前台，日志直出，uvicorn --reload 已开）
bash scripts/dev.sh docker backend

# 终端 B：前端
bash scripts/dev.sh docker frontend

# 终端 C：测试 / 守卫 / 任意命令
bash scripts/dev.sh docker test -q -k knowledge
bash scripts/dev.sh docker guard
bash scripts/dev.sh docker py -c "from app.core.config import settings; print(settings.POSTGRES_SERVER)"

# 进容器排查
bash scripts/dev.sh docker shell
```

改代码 → 宿主编辑器保存 → bind mount 立刻可见 → uvicorn/vite 自动重载。
**代码热重载 1–2 秒，全程零网络、零重建。**

## 5. 外循环：依赖变更（唯一需要重建镜像的路径）

```bash
# 1) 改 requirements*.txt（含 --overrides 那条规则，别漏）
# 2) 重建（只有变化的层会重跑；torch/paddle 层没动就命中缓存）
bash scripts/dev.sh docker build
# 3) 验证三连
bash scripts/dev.sh docker py -c "import main"
bash scripts/dev.sh docker test -q
bash scripts/dev.sh docker guard
# 4) 推镜像（Registry 启用后）+ 通知大家
#    tag 约定：与 requirements.txt 内容哈希绑定，可追溯、可回滚
```

## 5.5 配置改哪里？三个文件，各管一段

这是最容易搞混的地方，先记住一句话：**「容器怎么跑」看 compose，「应用怎么跑」看 `.env`，「只跟你有关」看 `.env.dev`。**

| 你想改的东西 | 改哪个文件 | 为什么 |
|---|---|---|
| 数据库/Redis/MinIO 地址、账号密码、密钥、JWT、限流、日志级别… (**应用配置**) | **仓库根 `.env`**（每人一份，gitignored） | 由 `app/core/config.py` 的 `settings` 读取；模板见 `.env.example` |
| 外部推理网关地址（BGE-M3 / Reranker / Qwen） | 先看下面「谁来覆盖谁」——**要覆盖成容器可达的地址就写 compose** | 容器里的 `localhost` 不是宿主 |
| 端口映射、挂载、网络别名、命令、`gpus`、容器内环境变量 (**容器运行时**) | **`docker/compose.dev.yaml`**（入库，改动会影响所有人） | 这是容器编排，不是应用配置 |
| **只跟你有关**的运行时参数（宿主端口、用哪个 registry 镜像） | **`.env.dev`**（`cp .env.dev.example .env.dev`，已 gitignore） | `dev.sh` 会 `set -a; source` 它，覆盖 compose 里的 `${...}` 默认值 |
| 前端 dev server 的 `VITE_*` | **`frontend/apps/chat/.env`** | vite 用 `loadEnv` 从那个目录读（模板见同目录 `env.example`；issue #12 已完成） |

### 谁来覆盖谁（优先级）

```
compose 的 environment:   ← 最高（容器内实际生效）
        ↑ 覆盖
shell / .env.dev 导出的变量  ← 只影响 compose 文件里的 ${...} 插值，不进容器
        ↑ 覆盖
仓库根 .env                ← 应用配置的兜底
```

实测验证过：`.env` 里写 `POSTGRES_SERVER=127.0.0.1`，但容器内 `settings.POSTGRES_SERVER` = `host.docker.internal`（compose 覆盖生效）。
另一条实测：`.env.dev` 里的 `API_PORT` **不会**出现在容器环境变量里（它只用于插值）——所以别把密钥写 `.env.dev`。

### 所以「以后改配置」的最短回答

- 改业务/凭据/第三方地址 → **`.env`**
- 改端口/挂载/容器内要覆盖的地址 → **`docker/compose.dev.yaml`**（共享，改前想一下别人）
- 只改我自己的端口/镜像 → **`.env.dev`**（或者一次性用命令行前缀）

## 5.6 宿主端口是怎么定的（`API_PORT=8001 ... dev.sh docker up` 那句）

`API_PORT=8001 WEB_PORT=8889 bash scripts/dev.sh docker up` 里的 `API_PORT=...` 是 **shell 的「一次性环境变量前缀」**：

- 它**只对这一条命令生效**，不写进任何文件，关掉终端就没了；
- compose 文件里写的是 `"${API_PORT:-8000}:8000"` —— `${VAR:-默认值}` 就是「有就用，没有就用 8000」；
- **容器内端口永远是 8000 / 8888**（与 `.env` 的 `PORT=8000`、vite 的 `VITE_PORT=8888` 一致），变的只是宿主侧。

三种写法，按需要选：

```bash
# ① 一次性（临时换一下）
API_PORT=8001 WEB_PORT=8889 bash scripts/dev.sh docker up

# ② 持久化（推荐）——写进 .env.dev，以后不用带前缀
cp .env.dev.example .env.dev
#   然后编辑 .env.dev：API_PORT=8001 / WEB_PORT=8889
bash scripts/dev.sh docker up

# ③ 当前 shell 有效（不推荐，容易忘）
export API_PORT=8001 WEB_PORT=8889
```

> 同一台服务器上多人各起一套时**必须错开**：你 8001/8889、别人 8002/8890……
> 前端那边还要对应改 `frontend/apps/chat/.env` 里的 `VITE_PORT`（见 issue #12）。

## 5.7 依赖变了怎么生效（重要：`git pull` 不会更新环境）

**venv 在镜像里**，所以 `git pull` 只更新代码 —— 拉到新的 `requirements*.txt` 后**必须重建镜像**，否则你跑的还是旧环境（这是容器化后最容易踩的一条）。

```bash
# 0) 随时自检：本地 requirements 与镜像里的依赖指纹是否一致
bash scripts/dev.sh docker check
#   ✅ 依赖哈希一致（本地 2340e5f5277e），环境是最新的
#   ⚠️ 依赖已变更，但镜像还是旧的 —— 环境不会自动更新！
#      本地 requirements 哈希 = bbea25f21150
#      镜像内依赖哈希         = 2340e5f5277e
#      执行： bash scripts/dev.sh docker build && bash scripts/dev.sh docker up

# 1) 重建（只有变化的层会重跑；uv 的 build cache 让重复下载最小化）
bash scripts/dev.sh docker build
# 2) 用新镜像重建容器
bash scripts/dev.sh docker up
```

`dev.sh docker up` 在启动前会**自动做一次这个检查并打印警告**（不阻塞），所以一般不会漏。

指纹来自镜像构建时写入的 `/opt/venv/.requirements-hash`（四个 requirements 文件的 sha256 前 12 位）。

**Registry 启用后**，分发流程是：改依赖的人 build → 验证 → `docker push` → 其他人 `dev.sh docker pull && up`（`up` 也会自动对齐远端）。

### 5.7.1 镜像怎么共享（Registry 是快照，不是同步盘）

**push 一次 = 一个不可变快照**；之后再改环境必须重新 build + push。所以：

| 你改了什么 | 要推 git 吗 | 要重新 build + push 镜像吗 |
|---|---|---|
| 业务代码 | ✅ 要（`git push`） | ❌ **不用**（代码是 bind mount，不在镜像里） |
| `requirements*.txt` / `docker/*` | ✅ 要 | ✅ **要**，且**要打新 tag** |
| 只改 `.env` / `.env.dev` | ❌ 不用（gitignored） | ❌ 不用 |

推送成本是**分层增量**的：只上传变化的层。只改业务代码 → 层没变 → 什么都不用推；改 `requirements.txt` → 那个 ~11.4 GB 的依赖层变了 → 要重传约 12 GB。**所以不要每 commit 打 tag，按 requirements 哈希打。**

```bash
# 改依赖的人：build → 验证 → 打哈希 tag → push
bash scripts/dev.sh docker build
bash scripts/dev.sh docker test -q && bash scripts/dev.sh docker guard
bash scripts/dev.sh docker push      # 自动打「依赖指纹 tag」+「移动 tag」并推送（需先 docker login）

# 其他人
bash scripts/dev.sh docker pull && bash scripts/dev.sh docker up
```

> `docker tag A B` 只是给同一个镜像加个别名（**不额外占盘**），所以本地同时保留 `nbhx-dev:local`
> 和 registry 名字是零成本的。

### 5.7.2 `NBHX_DEV_IMAGE` 是干什么的（一般不用写）

它决定「用哪个镜像」。**正常情况下不需要手写**：`dev.sh` 已默认用
`10.80.153.12:5050/carl_jia/ragchatbot/nbhx-dev:py312-cu130`，并在每次 `up` 前自动 `pull` 对齐
（registry 没启用时 pull 失败会自动回退本地 build，不会卡住）。

只有两种情况才写进 `.env.dev`：

1. **钉住版本**（可复现）：`NBHX_DEV_IMAGE=...:py312-cu130-<8位哈希>`
2. **临时用本地自建镜像**（完全不碰 registry）：`NBHX_DEV_IMAGE=nbhx-dev:local`

## 5.8 在 VS Code 里怎么看容器 / 怎么更新

**推荐工作方式：编辑在宿主，运行在容器。** 因为代码是 bind mount 的，你**不需要**在容器里编辑：

```
VS Code ──Remote-SSH──▶ 10.80.153.12（宿主）──打开仓库目录──┬─ 编辑 / Source Control：用宿主 git，和以前完全一样
                                                          └─ 终端：bash scripts/dev.sh docker backend|test|guard
```

两条路径，按省事程度排：

| 方式 | 做法 | 适用 |
|---|---|---|
| **A. Remote-SSH（推荐）** | 扩展 `Remote - SSH` → 连宿主 → 打开仓库；编辑与 `git commit/pull/push` 全在宿主，跟以前没区别 | 所有人，零配置、零属主风险 |
| **B. Attach 进容器** | 扩展 `Dev Containers` → 命令面板 `Dev Containers: Attach to Running Container` → 选 **`nbhx-<你的用户名>-dev-1`** | 想在容器内用 VS Code 的终端/调试器 |

> ⚠️ **不要用 `Dev Containers: Reopen in Container`**（原「方式 C」已废弃）：仓库里**不再提供** `.devcontainer/devcontainer.json`（2026-09-16 移除）。
> 原因：这份 compose 只给 `dev.sh` 用 —— `dev.sh` 会带 `-p nbhx-${USER}` 并导出 `NBHX_DEV_IMAGE`；VS Code 自己起 compose 时 project 名取 compose 文件所在目录名（**`docker`**），也拿不到 `NBHX_DEV_IMAGE`。于是每次「Reopen / Run in Container」都会：
> 1. 整份重建镜像（改依赖后确实该重建，但它把镜像打在**共享移动 tag** `.../nbhx-dev:py312-cu130` 上）；
> 2. 新建一个**平行的** `docker-dev-1` 容器 + 4 个全新空卷（`docker_nbhx-{cache,node-modules,pnpm-store}` + `vscode`）；
> 3. 因 8000/8888 已被你的 `nbhx-<你>-dev-1` 占用而**起不来**：`Bind for 0.0.0.0:8000 failed: port is already allocated`（ExitCode 128）。
>
> 误触后的清理（顺序要紧：先删容器，镜像才删得掉；全程不影响正在跑的容器）：
> ```bash
> docker rm docker-dev-1
> docker volume rm docker_nbhx-cache docker_nbhx-node-modules docker_nbhx-pnpm-store vscode
> docker rmi 10.80.153.12:5050/carl_jia/ragchatbot/nbhx-dev:py312-cu130   # 删掉这次构建的产物
> bash scripts/dev.sh docker pull                                        # 把移动 tag 拉回 registry 版本（可选）
> ```
> 2026-09-16 上午实测误触两次（第二次构建纯缓存命中，产出的镜像 digest 与第一次相同，包集合与线上镜像逐条一致 → 无实际收益）。

**怎么确认「我现在用的是哪个容器」**：

```bash
bash scripts/dev.sh docker ps                  # 只列你自己 project 的容器
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' | grep nbhx
# VS Code 里：左侧 Docker 面板 → Containers → 找 nbhx-<你>-dev-1
#             左下角绿色角标会显示 Remote / Container 名字
```

**容器命名规则**：`nbhx-${USER}-dev-1`（例如 `nbhx-jiazhenyu-dev-1`），数据卷是 `nbhx-${USER}_nbhx-cache` 等 —— 由 `dev.sh` 传的 `-p nbhx-${USER}` 决定，**保证同一台服务器上多人互不冲突**（docker 的容器名必须全局唯一，所以不能用固定名字）。

**更新环境后 VS Code 要做什么**：`dev.sh docker build && dev.sh docker up` 会**重建容器**，此时 attach 会话会断开 → 重新 Attach 一次即可（Remote-SSH 那种方式不用动）。

## 6. 排障常用
```bash
bash scripts/dev.sh docker ps                 # 容器状态
bash scripts/dev.sh docker logs               # 容器日志
bash scripts/dev.sh docker down               # 停容器（数据卷保留）
bash scripts/dev.sh docker check              # 依赖是否与镜像一致（见 §5.7）
bash scripts/dev.sh docker <cmd>              # 透传：如 `dev.sh docker nvidia-smi`
docker volume ls | grep nbhx                # 看数据卷（名字含你的用户名）
```

**每人一组端口**（避免同机冲突）：

```bash
API_PORT=8001 WEB_PORT=8889 bash scripts/dev.sh docker up
```

容器内端口固定为 8000（后端）与 8888（vite），与 `.env` 一致；变化的只是宿主侧端口。
注意：vite 的端口来自 `frontend/apps/chat/.env` 的 `VITE_PORT`，**要和上面的 WEB_PORT 对应**（见 #12）。

## 7. 设计决策与踩坑记录（本机实测）

| 决策 | 原因 |
|---|---|
| 代码**不进镜像**，`.dockerignore` 用**白名单** | 仓库里 `.venv`(11G)+`.cache`(12G)，黑名单漏一条就几 GB；白名单让上下文从 **13 GB → 几 KB** |
| venv 放 **`/opt/venv`** | 在 bind mount 之外，不会被宿主仓库的 `.venv` 遮蔽；`scripts/env.sh` 认 `VENV_DIR`，**脚本零改动** |
| 缓存卷挂在 **`/workspace/.cache`**（仓库内部） | `env.sh` 把 `UV_CACHE_DIR` / `HF_HOME` / `PADDLE_PDX_CACHE_HOME` / `COREPACK_HOME` 等十几个变量硬编码指向 `${PROJECT_ROOT}/.cache`；挂这里就全部自动落到 NVMe，**不用改脚本** |
| 缓存用**命名卷**而不是 bind mount | 命名卷落在 `/var/lib/docker/volumes/`，本机在 **NVMe**；bind mount 会跟着宿主 `/data` 落到 5400rpm 机械盘 |
| entrypoint **先 chown 再 `setpriv` 降权** | 实测命名卷挂进 bind mount 内部时是 `root:root 0755`，非 root 直接 Permission denied；降权后容器写进仓库的文件属主仍是宿主开发者本人 |
| **不递归** chown | `/opt/venv` 在镜像层，递归 chown 触发 copy-up（每容器多占 11G）；`.cache` 文件可能上百万，每次启动递归太慢 |
| 依赖**分三层** COPY+install | 改业务代码、改测试工具都不会重装 torch/paddle（几 GB 下载） |
| 基础镜像用 **`python:3.12-slim`**（Debian 13 trixie，本机已有） | CUDA 用户态库由 pip 的 `nvidia-*` wheel 提供；dev 容器**本来就不需要 GPU**（OCR/Tagger 将服务化） |
| Node 从 **`node:24-slim`** 阶段拷入 | 整份 `COPY /usr/local` 会覆盖 python 镜像的 `/usr/local`；只拷 `bin` + `lib/node_modules` |
| `uv` 用 **pip 安装**而不是 `COPY --from=ghcr.io` | 避免依赖 ghcr.io 可达性 |
| PyPI 默认走 **aliyun 镜像** | 本机实测 `pypi.org` **超时不可达**；aliyun 下载 5.2 MB/s、SJTU 4.4、tuna 1.2。可用 `--build-arg PYPI_INDEX=...` 覆盖 |
| compose 一律带 **`-p nbhx-${USER}`** | 多人同用一台 docker daemon，不加项目名会互相顶掉容器与数据卷（`dev.sh` 已内置） |

## 8. 已验证（2026-09，本机实测通过）

镜像 `nbhx-dev:local` 构建成功，容器 `nbhx-jiazhenyu-dev-1` 起得来，以下项逐条验过：

| 验证项 | 结果 |
|---|---|
| build 上下文 | `transferring context: 19.20kB`（白名单 `.dockerignore` 生效；原本 13 GB） |
| 依赖安装 | 主 179 + RAG 48 + dev 5 全部装入 `/opt/venv` |
| **`--overrides` 生效** | 装了 `nvidia-nccl-cu13==2.27.7`，**没有** `nvidia-nccl-cu12`（否则 `import torch` 崩） |
| 容器内身份 | `uid=1001 gid=1001`（= 宿主 `jiazhenyu`，entrypoint `setpriv` 降权成功） |
| **仓库写文件属主** | 容器内写的文件在宿主里是 `jiazhenyu:jiazhenyu` ✅（不是 root） |
| 命名卷属主 | `/workspace/.cache`、`.cache/home`、`.cache/uv` 均为 `1001:1001` 且可写 |
| 环境变量 | `VENV_DIR=/opt/venv`、`python=/opt/venv/bin/python` |
| `import main` | 通过（打印 `[info] PyTorch CUDA 不可用` —— dev 容器本就不需要 GPU） |
| 工具版本 | Python 3.12.14 / Node v24.21.0 / corepack 0.36.0 / uv 0.12.11 |
| **配置覆盖** | `.env` 里是 `127.0.0.1`，容器内 `settings.POSTGRES_SERVER` = `host.docker.internal` ✅ |
| **实际连库** | 容器内 psycopg2 连宿主 `host.docker.internal:5433` → `pgvector 0.8.6` ✅ |
| 分层架构守卫 | `[layer-check] PASSED` |
| **pytest** | 容器内 **150 passed / 9.08s**；宿主对照 **150 passed / 36.42s** —— 测试数完全一致（**容器快 4×**，因为 venv 在镜像层=NVMe，宿主 `.venv` 在 5400rpm HDD） |
| 历史测试数 | 早前记录的 218 / 150 等快照已过期；以当前分支 `pytest -q` 的实际输出为准 |

> 遗留观察：仓库里 `tests/__pycache__`、`tests/output/` 有 **Sep 14 就存在的 root 属主文件**（早于容器化）。
> 影响很小（Python 只是无法刷新字节码缓存），但容器里以 uid 1001 运行时会静默跳过写入。
> 要清掉的话用容器内 root 一把 chown 即可：
> `docker compose -p nbhx-$USER -f docker/compose.dev.yaml exec -u 0 dev chown -R "$(id -u):$(id -g)" /workspace/tests`

## 9. 与宿主环境的关系

宿主的 `.venv`（11G）与 `.cache`（12G）**容器不用**，容器跑通后可以删掉回收空间。

⚠️ 但必须**两个一起删**：实测 `.venv` 里 56995 个文件中有 **47535 个是硬链接**指向 `.cache/uv/archive-v0/...`（`uv pip install` 默认 hardlink 模式），所以

```bash
du -sh .venv    # 11G  ← 单独测会把同一批 inode 算进来
du -sh .cache   # 12G  ← 同上
du -sh .        # 13G  ← 真实占用（每个 inode 只算一次）
```

只删一个**不会回收空间**。两个都删才回收约 12–13 GB（每人）。

## 9. 待办（不在本方案内，见对应 issue）

- ~~**#11**：启用 GitLab Container Registry~~ ✅ **已完成（2026-09-15）**：`:5050` 已启用并实测推/拉可用，镜像 `10.80.153.12:5050/carl_jia/ragchatbot/nbhx-dev`。工作副本**决定不迁** NVMe（实测容器内 pytest 9s，源码在 HDD 无感）。
- **#9**：PaddleOCR 拆成独立 OCR 容器 → 主应用去掉 `paddle*`（-3.1 GB）。
- **#10**：TagGenerator 拆成独立 tagger 容器 → 主应用去掉 `torch` / `nvidia-*` / `sentence-transformers` / `keybert`（-5.4 GB）。
  → 两者完成后，dev 镜像可再瘦 ~8.5 GB（paddle 3.1G + torch/nvidia 5.4G）：
    实测当前镜像 **磁盘占 19 GB / 按层汇总（≈推拉传输量）12.3 GB**，
    拆掉后按层估算降到 **~4 GB 量级**，Dockerfile 里删掉对应层即可。
- ~~**#12**：前端 dev 网络（`VITE_WS_BASE_URL` 写死 8000、`.env` 拆分）~~ ✅ **已完成（2026-09-16）**：`VITE_WS_BASE_URL` 默认不再设置，`src/services/ws.ts` 空值回退 `window.location`（同源），WS 经 vite 代理（`ws: true`）到同容器后端——宿主端口实测 101 建连、多人端口互不串；前端 `.env` 只含 `VITE_*`（模板 `env.example`），缺变量报错自导航。结论与验收记录见 `docs/issues/004-frontend-dev-networking-hardcoded.md`。
- 外部推理 API 的真实地址确定后，加进 `docker/compose.dev.yaml` 的 `environment:`（覆盖 `.env`，容器里的 `localhost` 不是宿主）。
