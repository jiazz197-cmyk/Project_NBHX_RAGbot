# 开发环境容器化 · 同事手册

> 一句话：**环境在 Docker 镜像里，代码在你自己的仓库目录里**（挂载进容器）。改代码不用碰镜像，只有依赖变化才重建。
> 详细原理见 [docker-dev-env.md](docker-dev-env.md)；镜像/registry 由管理员维护，见 [docker-dev-guide-admin.md](docker-dev-guide-admin.md)。

---

## 1. 前置（3 分钟）

```bash
# ① 能 SSH 到共享服务器
ssh <你的用户名>@10.80.153.12

# ② docker 权限（不需要装 Docker Desktop）
docker ps >/dev/null 2>&1 && echo "✅ docker 可用" || echo "❌ 找管理员: sudo usermod -aG docker $USER 后重新登录"

# ③ 一台要推/拉镜像的机器需要允许内网 http registry（管理员已在本机配好；
#    你自己的电脑若要 docker pull，需要自己配 insecure-registries）
```

**④ 一份 `.env`（必须找管理员要，git 里没有）** —— 里面有密钥，被 `.gitignore` 排除，新 clone 的目录里没有这个文件，应用起不来。
> 从模板起手也行：`cp .env.example .env`，但模板缺 `BOOTSTRAP_SUPERUSER_*` 和 `PDM_SQLSERVER_*`。

---

## 2. 首次上手（Day 0，约 5 分钟）

```bash
# ① clone
cd ~ && git clone http://10.80.153.12/Carl_Jia/ragchatbot.git && cd ragchatbot

# ② 放入 .env（见前置 ④）

# ③ 配置你自己的端口（同机多人必须错开，见文末端口表）
cp .env.dev.example .env.dev && vi .env.dev      # 改 API_PORT / WEB_PORT

# ④ 起容器（镜像从 registry 拉，约 12GB；拉不到会自动回退本地构建）
bash scripts/dev.sh docker up
#    期望：Container nbhx-<你的用户名>-dev-1  Started

# ⑤ 三连验证
bash scripts/dev.sh docker py -c "import main; print('✅ import ok')"
bash scripts/dev.sh docker test -q          # 期望 150 passed
bash scripts/dev.sh docker guard            # 期望 [layer-check] PASSED

# ⑥ 起后端
bash scripts/dev.sh docker backend          # 前台，Ctrl-C 结束
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:<你的API_PORT>/api/v1/docs   # 期望 200
```

需要前端时（一次性）：`bash scripts/dev.sh docker fe-setup`，之后 `bash scripts/dev.sh docker frontend`。

---

## 3. 日常命令（收藏这一页）

| 我要做什么 | 命令 |
|---|---|
| 起容器（开机/重启后） | `bash scripts/dev.sh docker up` |
| 起后端（前台，改代码自动重载） | `bash scripts/dev.sh docker backend` |
| 起前端 | `bash scripts/dev.sh docker frontend` |
| 跑测试 | `bash scripts/dev.sh docker test -q` |
| 只跑某个测试 | `bash scripts/dev.sh docker test -q -k knowledge` |
| 架构守卫（提交前必跑） | `bash scripts/dev.sh docker guard` |
| 进容器 | `bash scripts/dev.sh docker shell` |
| 用容器的 python 干活 | `bash scripts/dev.sh docker py -c "..."` |
| 看容器状态 / 日志 | `bash scripts/dev.sh docker ps` / `logs` |
| **拉到别人推的新镜像** | `bash scripts/dev.sh docker pull` |
| 看环境是否过期 | `bash scripts/dev.sh docker check` |
| 停容器（数据卷保留） | `bash scripts/dev.sh docker down` |
| 透传任意命令 | `bash scripts/dev.sh docker nvidia-smi` |

**日常内循环**（改代码 → 看效果，1~2 秒，不碰镜像）：

```
宿主编辑器改文件 ──bind mount──▶ 容器 /workspace 立即可见 ──▶ uvicorn --reload 自动重启
                                        │
                          满意 → dev.sh docker guard && git commit && git push
```

---

## 4. 配置改哪里（记住一句话）

> **「容器怎么跑」看 compose，「应用怎么跑」看 `.env`，「只跟你有关」看 `.env.dev`。**

| 改什么 | 改哪个文件 |
|---|---|
| 数据库/Redis/MinIO 地址、密钥、限流、日志级别 | **仓库根 `.env`**（你有，不进 git） |
| 端口映射、挂载、容器内要覆盖的地址 | **`docker/compose.dev.yaml`**（入库共享，改了影响所有人，先打招呼） |
| 你自己的宿主端口 | **`.env.dev`**（你有，不进 git） |
| 前端 `VITE_*` | `frontend/apps/chat/.env` |

优先级：`compose 的 environment:` > `.env.dev`/shell 变量 > 仓库根 `.env`。
所以 `.env` 里写 `127.0.0.1` 的地址在容器里会被自动换成 `host.docker.internal`，**`.env` 不用改**。

---

## 5. 依赖变了怎么办（同事视角）

⚠️ **`git pull` 只更新代码，不会更新环境**（venv 在镜像里）。

```bash
bash scripts/dev.sh docker check          # 先自检：本地依赖 vs 镜像里的指纹
# 情况 A（最常见）：别人已经推好了新镜像
bash scripts/dev.sh docker pull && bash scripts/dev.sh docker up
# 情况 B：是你自己改了 requirements
bash scripts/dev.sh docker build && bash scripts/dev.sh docker up
```

`dev.sh docker up` 每次也会自动检查并警告，一般不会漏。

---

## 6. VS Code

**`commit / pull / push` 和以前一模一样** —— git 在宿主上跑，容器不参与。

- **推荐**：`Remote - SSH` 扩展连宿主 → 打开仓库 → 编辑 / Source Control 全在宿主；要跑服务就在终端敲 `dev.sh docker ...`
- **想在容器里编辑**：`Dev Containers` 扩展 → `Attach to Running Container` → 选 **`nbhx-<你的用户名>-dev-1`**
- 环境更新后（`build && up` 会重建容器）→ 重新 Attach 一次

---

## 7. FAQ

| 症状 | 解决 |
|---|---|
| `permission denied ... Docker daemon` | 不在 docker 组：找管理员加，然后**重新登录** |
| `bind: address already in use` | 端口被占：`dev.sh docker ps` 看自己的旧容器，或 `.env.dev` 换端口 |
| 访问 `:8000` 打不开 | 容器起了吗？端口是不是你在 `.env.dev` 里设的那个？ |
| `ModuleNotFoundError` / 缺包 | 依赖过期：`check` → `pull`（或 `build`）→ `up` |
| `[info] PyTorch CUDA 不可用` | **正常**，开发容器不用 GPU（OCR/模型走独立服务，issue #9/#10） |
| 前端报 `Proxy target unavailable: http://127.0.0.1:8000` | 后端没起：另开终端 `dev.sh docker backend` |
| RAG/OCR/嵌入 探活失败 | **预期内**：这些模型服务在外部，`.env` 里的 `localhost:80` 是待替换的占位值 |
| `dev.sh backend` 报「宿主环境不存在」 | 宿主 `.venv` 已删（容器化后不需要）→ 用 `dev.sh docker backend` |
| 保存文件后宿主里属主变 root | attach 方式下 `.devcontainer/devcontainer.json` 的 `remoteUser` 要改成你的 `id -u` |
| 想重启环境 | `dev.sh docker down && dev.sh docker up`（数据卷保留） |

---

## 8. 团队约定

### 端口分配（同机必须错开）

| 成员 | API_PORT | WEB_PORT | 容器名 |
|---|---|---|---|
| jiazhenyu | 8000 | 8888 | `nbhx-jiazhenyu-dev-1` |
| （同事 A） | 8001 | 8889 | `nbhx-<A>-dev-1` |
| （同事 B） | 8002 | 8890 | `nbhx-<B>-dev-1` |

容器内端口固定 8000/8888；容器名 = `nbhx-<你的登录名>-dev-1`（自动生成，不用记）。

### ❌ 不要做的事

| 别做 | 为什么 |
|---|---|
| `docker system prune -a` / `docker volume prune` / `docker builder prune` | 这台机器跑着 **GitLab / pgvector / Redis / MinIO / CI Runner** 和其他同事的项目 |
| 不带 `-p` 直接 `docker compose up` | 会顶掉别人的容器（**始终用 `dev.sh`**，它带 `-p nbhx-$USER`） |
| `git add .env` / `.env.dev` | 有密钥，已被 gitignore |
| 在容器里用 root 写仓库文件 | 属主会变 root（用 `dev.sh` 的命令不会） |
| `docker compose down -v` | 会删数据卷 → 缓存和前端依赖重下 |
| 删别人的容器/卷/镜像 | 只有 `nbhx-<你的名字>-*` 是你的 |

---

## 9. 一页速查

```bash
bash scripts/dev.sh docker up                 # 起容器
bash scripts/dev.sh docker backend            # 起后端（Ctrl-C 停）
bash scripts/dev.sh docker frontend           # 起前端
bash scripts/dev.sh docker test -q            # 跑测试
bash scripts/dev.sh docker guard              # 架构守卫（提交前必跑）
bash scripts/dev.sh docker shell              # 进容器
bash scripts/dev.sh docker check              # 环境是否过期
bash scripts/dev.sh docker pull               # 拉最新镜像
bash scripts/dev.sh docker build && bash scripts/dev.sh docker up   # 自己改了依赖
git pull && git commit && git push             # 和以前一样，在宿主上跑
```
