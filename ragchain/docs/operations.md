# ragchain 运维手册

> 适用对象：`ragchain/` 独立容器（LangChain RAG 核心链）。
> 镜像 `nbhx-ragchain:py312`（本地 tag；registry 见 §3.3.1）；宿主端口 `8010` → 容器 `8000`。
> 本文所有命令的起始目录默认为仓库根 `/data/jiazhenyu/RAG/project-nbhx`。
> **日常最短路径**：改代码 → `bash scripts/dev.sh ragchain restart`；改依赖 → `bash scripts/dev.sh ragchain build`。

## 1. 约定与前置条件

| 项 | 值 |
|---|---|
| 代码目录 / build context | `ragchain/`（独立包，不 import 主应用） |
| 构建文件 | `ragchain/Dockerfile`、`ragchain/requirements.txt`、`ragchain/compose.yaml` |
| 代码挂载 | `ragchain/app` → 容器 `/app/app:ro`（**镜像里不含代码**，改代码只需重启进程） |
| 运行时配置 | `ragchain/.env`（从 `.env.example` 复制，gitignored） |
| 镜像 | `nbhx-ragchain:py312`（改依赖后重建并推 registry，见 §3.3.1） |
| 容器端口 | 宿主 `8010` → 容器 `8000` |
| 健康检查 | `GET http://127.0.0.1:8010/healthz` → `{"status":"ok"}` |
| 统一入口 | `bash scripts/dev.sh ragchain up\|restart\|build\|check\|logs\|ps\|down\|shell\|test` |
| 本机 Docker 特殊项 | `$HOME/.docker` 不可写，docker CLI/compose 需 `export DOCKER_CONFIG=<repo>/.cache/docker`（`dev.sh ragchain` 已自动处理） |

> **命令执行位置**：本文所有命令默认在**仓库根** `/data/jiazhenyu/RAG/project-nbhx` 执行。
> - `scripts/dev.sh` 内部用脚本自身路径解析仓库根，所以 `bash /绝对路径/scripts/dev.sh ragchain …`
>   从**任意目录**都能用（在 `ragchain/` 里则要写 `bash ../scripts/dev.sh ragchain …`）。
> - 手工 `docker compose -f ragchain/compose.yaml …` 的 `-f` 是相对当前目录的；想从别处跑就用绝对路径。
>   compose 文件内部的相对路径（build context `.`、`env_file .env`、`./app` 挂载）相对**compose 文件所在目录**解析，
>   所以从仓库根写 `-f ragchain/compose.yaml` 与进 `ragchain/` 写 `-f compose.yaml` 是同一个项目、同一个容器。
> - `export DOCKER_CONFIG=$PWD/.cache/docker` 依赖当前目录，**只在仓库根成立**；在 `ragchain/` 里会指向
>   `ragchain/.cache/docker`（错的）。不想管这些就用 `bash scripts/dev.sh ragchain …`。

启动前必须满足：

1. 主应用已启动，且监听 `0.0.0.0:8000`。ragchain 容器通过 `host.docker.internal:8000/api/v1` 调主应用的检索与记忆接口；主应用若只监听 `127.0.0.1`，容器会 connection refused。
2. `ragchain/.env` 中的 `SECRET_KEY` / `ALGORITHM` 与主应用根 `.env` 一致（JWT 本地验签）。
3. `ragchain/.env` 中已填 `MAIN_LLM_*`、`SUB_LLM_*`、`AI_INFERENCE_API_KEY` 等凭据；本地 dev 推荐：
   - `MAIN_APP_BASE_URL=http://host.docker.internal:8000/api/v1`
   - `SEARCH_ENGINE_URL=http://host.docker.internal:8080/search`
   - `RERANKER_API_URL=http://172.28.16.50:8096/v1/rerank`

## 2. 首次 / 日常启动

```bash
cd /data/jiazhenyu/RAG/project-nbhx

# 本机 HOME 下 .docker 不可写时统一指到仓库内（用 dev.sh ragchain 则不必手动做）
export DOCKER_CONFIG=$PWD/.cache/docker
mkdir -p "$DOCKER_CONFIG"

# 1) 配置：仅首次或换环境需要
cp ragchain/.env.example ragchain/.env
# 编辑 ragchain/.env，至少填：
#   SECRET_KEY / ALGORITHM（与根 .env 一致）
#   MAIN_LLM_API_URL / MAIN_LLM_MODEL / MAIN_LLM_API_KEY
#   SUB_LLM_API_URL / SUB_LLM_MODEL / SUB_LLM_API_KEY
#   AI_INFERENCE_API_KEY
#   MAIN_APP_BASE_URL / SEARCH_ENGINE_URL / RERANKER_API_URL（按环境）

# 2) 启动主应用，必须监听 0.0.0.0:8000
#    A. 宿主 venv：
HOST=0.0.0.0 bash scripts/dev.sh backend

#    B. 当前 dev 容器（推荐，另开一个 shell）：
bash scripts/dev.sh docker backend
#       ↑ dev.sh 已显式注入 HOST=0.0.0.0（compose 的 environment 里另有一份兜底）；
#         手工 docker exec 起后端时记得自己带上 -e HOST=0.0.0.0：
#       docker exec -d -u "$(id -u):$(id -g)" -e HOME=/workspace/.cache/home -e HOST=0.0.0.0 \
#         nbhx-jiazhenyu-dev-1 bash -lc 'cd /workspace && /opt/venv/bin/python main.py'
#    注意：容器内主应用必须监听 0.0.0.0，否则 ragchain 经 host.docker.internal 访问会
#    connection refused（前端表现为发消息后报“外部服务不可达”）。

# 3) 启动 ragchain（首次自动构建镜像，之后直接起容器）
bash scripts/dev.sh ragchain up
#    等价手工命令：
#    docker compose -f ragchain/compose.yaml up -d --build --force-recreate

# 4) 验证
curl -fsS http://127.0.0.1:8010/healthz && echo     # {"status":"ok"}
bash scripts/dev.sh ragchain ps                     # STATUS 应含 (healthy)
bash scripts/dev.sh ragchain logs                   # 或 docker compose -f ragchain/compose.yaml logs --tail=100
```

前端单独启动（可选）：

```bash
# 宿主
bash scripts/dev.sh frontend

# dev 容器
docker exec -d -u "$(id -u):$(id -g)" -e HOME=/workspace/.cache/home nbhx-jiazhenyu-dev-1 \
  bash -lc 'cd /workspace/frontend/apps/chat && ./node_modules/.bin/vite --host 0.0.0.0 --port 8888'
```

Vite 分流由 `VITE_CHAT_ORCHESTRATOR_TARGET` 控制；`docker/compose.dev.yaml` 中已配置为 `http://host.docker.internal:8010`，宿主直接跑 vite 时用 `frontend/apps/chat/.env` 的 `127.0.0.1:8010`。

## 3. 日常开发：改代码不用重建镜像

> 关键：**依赖在镜像里，代码是 bind mount 进来的** —— `ragchain/compose.yaml` 把
> `ragchain/app` 挂到容器 `/app/app:ro`，镜像里根本没有 `app/`（对照根目录
> `docker/dev.Dockerfile` 那套 dev 容器，思路一致）。
>
> | 你改了什么 | 要做什么 |
> |---|---|
> | `ragchain/app/**` 代码 | `bash scripts/dev.sh ragchain restart`（秒级，**不重建镜像**） |
> | `ragchain/requirements.txt` / `requirements.in` | `ragchain build` + `ragchain up`（重建镜像） |
> | `ragchain/.env` | `ragchain up --force-recreate`（重建容器，不重建镜像） |
> | `ragchain/compose.yaml`（端口/挂载/环境变量） | `ragchain up`（重建容器，不重建镜像） |

```bash
cd /data/jiazhenyu/RAG/project-nbhx
bash scripts/dev.sh ragchain restart     # 改完代码：重启进程，代码即生效
bash scripts/dev.sh ragchain logs        # 看日志（-f）
bash scripts/dev.sh ragchain check       # 依赖指纹是否与镜像一致；不一致才需要 build
bash scripts/dev.sh ragchain build       # 只有 requirements.txt 变化时才需要
bash scripts/dev.sh ragchain up          # 起/重建容器（本地缺镜像时自动先 build）
```

### 3.1 只改代码（推荐路径，秒级）

```bash
cd /data/jiazhenyu/RAG/project-nbhx
bash scripts/dev.sh ragchain restart
curl -fsS http://127.0.0.1:8010/healthz && echo     # {"status":"ok"}
```

等价的手工命令（没有 `dev.sh` 时）：

```bash
export DOCKER_CONFIG=$PWD/.cache/docker
docker compose -f ragchain/compose.yaml restart     # 重启容器内进程：不 build、不 recreate
```

`restart` 只是把容器里的 uvicorn 重新拉起，**不碰镜像、也不重建容器**；挂载进来的
新代码在下次 import 时生效。改的模块多、或改了 import 结构时，同样一条 `restart` 就够。

### 3.2 确认容器里跑的就是宿主这份代码

```bash
cd /data/jiazhenyu/RAG/project-nbhx
export DOCKER_CONFIG=$PWD/.cache/docker

# 1) 挂载点存在（Source 应指向宿主 ragchain/app）
docker inspect ragchain-ragchain-1 --format '{{json .Mounts}}'

# 2) 宿主文件与容器内文件内容一致（两个哈希应相同）
md5sum ragchain/app/prompts.py
docker compose -f ragchain/compose.yaml exec ragchain md5sum /app/app/prompts.py

# 3) 容器实际 import 的路径
docker compose -f ragchain/compose.yaml exec ragchain \
  python -c "import app.server as s; print(s.__file__)"
```

### 3.3 更新依赖（`ragchain/requirements.in` 变更后）

只有这一步需要重建镜像。

```bash
cd /data/jiazhenyu/RAG/project-nbhx/ragchain

# 重新锁定运行时依赖
UV_CACHE_DIR=$PWD/../.cache/uv uv pip compile requirements.in \
  --index-url https://mirrors.aliyun.com/pypi/simple --python-version 3.12 \
  -o requirements.txt

# 测试依赖单独锁定（requirements-dev.in 已包含 -r requirements.in，不进镜像）
UV_CACHE_DIR=$PWD/../.cache/uv uv pip compile requirements-dev.in \
  --index-url https://mirrors.aliyun.com/pypi/simple --python-version 3.12 \
  -o requirements-dev.txt

# ⚠️ 锁定后必须跑 agent 栈 import 冒烟（tests/test_import_smoke.py，issue #29 教训）：
#    依赖解析器从不校验跨包兼容性——langgraph-prebuilt 1.0.13 与 langgraph 1.0.10
#    不兼容，compile 全绿但 langchain.agents 整包 import 不了，线上却在跑。
#    app/ 不 import agent 栈，回归不会自然暴露。同步本地测试 venv 后跑：
#      uv pip install --python ../.cache/ragchain-venv/bin/python \
#        -r requirements-dev.txt --index-url https://mirrors.aliyun.com/pypi/simple
#      ../.cache/ragchain-venv/bin/python -m pytest tests/test_import_smoke.py -q

# 重建镜像（requirements.txt 变化会使 uv 安装层失效）
cd /data/jiazhenyu/RAG/project-nbhx
bash scripts/dev.sh ragchain build
bash scripts/dev.sh ragchain up
bash scripts/dev.sh ragchain check      # 应打印 ✅ 依赖哈希一致
```

### 3.3.1 推送镜像到 GitLab Registry（改依赖后，管理员做）

镜像 tag 双份：`py312`（移动）+ `py312-<指纹>`（可复现，指纹 = `requirements.txt` sha256 前 12 位，
与镜像内 `/opt/venv/.requirements-hash`、`ragchain check` 判据一致）。

```bash
cd /data/jiazhenyu/RAG/project-nbhx

# 前置（每台要推拉的机器各一次）：http registry 放行 + 登录
sudo bash scripts/enable_insecure_registry.sh
docker login 10.80.153.12:5050          # 用户名=GitLab 用户名，密码=PAT（含 write_registry 或 api）

export DOCKER_CONFIG=$PWD/.cache/docker # 本机 ~/.docker 不可写时需要

HASH=$(sha256sum ragchain/requirements.txt | cut -c1-12)
docker tag nbhx-ragchain:py312 10.80.153.12:5050/carl_jia/ragchatbot/nbhx-ragchain:py312
docker tag nbhx-ragchain:py312 10.80.153.12:5050/carl_jia/ragchatbot/nbhx-ragchain:py312-$HASH
docker push 10.80.153.12:5050/carl_jia/ragchatbot/nbhx-ragchain:py312
docker push 10.80.153.12:5050/carl_jia/ragchatbot/nbhx-ragchain:py312-$HASH

# 同事侧拉取（retag 回本地名，compose 才认）：
# docker pull 10.80.153.12:5050/carl_jia/ragchatbot/nbhx-ragchain:py312
# docker tag  10.80.153.12:5050/carl_jia/ragchatbot/nbhx-ragchain:py312 nbhx-ragchain:py312
# 然后照常 bash scripts/dev.sh ragchain up
```

### 3.4 镜像里到底有什么

镜像 = Python 3.12 + `/opt/venv`（只装 `requirements.txt`）+ 空的 `/app/app` 挂载点，
**不含代码**。所以：

```bash
# 镜像里的依赖指纹（dev.sh ragchain check 就是拿它和宿主 requirements.txt 比）
docker run --rm --entrypoint cat nbhx-ragchain:py312 /opt/venv/.requirements-hash

# 免挂载直接跑镜像会 ImportError: app.server —— 这是预期行为（代码不在镜像里）；
# 想脱离 compose 独立验证镜像，必须把代码挂进去：
docker run --rm -v "$PWD/ragchain/app:/app/app:ro" --entrypoint python nbhx-ragchain:py312 \
  -c "from app.server import app; import langchain, pandas; print('image ok', langchain.__version__, pandas.__version__)"
```

### 3.5 本机跑 ragchain 单测（不依赖容器）

镜像只装运行时依赖（没有 pytest），单测用本机 venv 跑：

```bash
cd /data/jiazhenyu/RAG/project-nbhx
bash scripts/dev.sh ragchain test -q     # 内部用 .cache/ragchain-venv 跑 ragchain/tests

# 等价手工命令（首次需建 venv）：
# uv venv .cache/ragchain-venv --python 3.12
# UV_CACHE_DIR=$PWD/.cache/uv uv pip install --python .cache/ragchain-venv/bin/python \
#   -r ragchain/requirements-dev.txt --index-url https://mirrors.aliyun.com/pypi/simple

cd ragchain && ../.cache/ragchain-venv/bin/python -m pytest tests -q   # 2026-09-18 实测 136 passed
```

## 4. 只改配置（不重建镜像）

`ragchain/.env` 由 compose `env_file` 在**创建容器时**注入；改完只需重建容器（代码是挂载的，与配置无关）：

```bash
cd /data/jiazhenyu/RAG/project-nbhx
bash scripts/dev.sh ragchain up --force-recreate

# 等价手工命令：
# export DOCKER_CONFIG=$PWD/.cache/docker
# docker compose -f ragchain/compose.yaml up -d --force-recreate
```

常用可调项：

| 配置 | 作用 |
|---|---|
| `RAG_RETRIEVE_TOP_K` / `RAG_RERANK_TOP_N` | 本地召回 / 容器侧重排数量 |
| `EXCEL_CONTEXT_MAX_CHARS` | Excel JSON 进 prompt 的截断上限 |
| `TOOL_MAX_ITERATIONS` / `TOOL_EXEC_TIMEOUT_SEC` / `TOOL_OUTPUT_MAX_CHARS` | 沙箱工具循环与限制 |
| `MEMORY_RECENT_TURNS` / `MEMORY_COMPRESS_THRESHOLD` / `MEMORY_COMPRESS_N_RECENT` | 记忆与压缩策略 |
| `SSE_HEARTBEAT_SEC` / `TASK_REGISTRY_TTL_SEC` | SSE 心跳 / stop 幂等窗口 |
| `RAGCHAIN_GUARD_STRICT` / `RAGCHAIN_LOG_LEVEL` | 防注入严格模式 / 日志级别 |

## 5. 停止 / 重启 / 查看日志

```bash
cd /data/jiazhenyu/RAG/project-nbhx
export DOCKER_CONFIG=$PWD/.cache/docker    # 用 dev.sh ragchain 则不必手动 export

bash scripts/dev.sh ragchain stop          # 停止容器，保留（= docker compose stop）
bash scripts/dev.sh ragchain start         # 启动已停止的容器
bash scripts/dev.sh ragchain restart       # 重启容器内进程：**会加载挂载进来的最新代码**
bash scripts/dev.sh ragchain down          # 停止并删除容器/网络，保留镜像与 .env
bash scripts/dev.sh ragchain ps
bash scripts/dev.sh ragchain logs          # 跟随日志（-f）

# 手工等价：
# docker compose -f ragchain/compose.yaml stop|start|restart|down|ps|logs -f --tail=200
```

## 6. 排障速查

| 现象 | 原因 / 处理 |
|---|---|
| 8010 起不来 | `ss -ltnp \| grep 8010`；修改 `ragchain/compose.yaml` ports 或停冲突进程 |
| 请求 401 / JWT 不认 | `ragchain/.env` 的 `SECRET_KEY` / `ALGORITHM` 与根 `.env` 不一致 |
| 容器调主应用 connection refused | 主应用没监听 `0.0.0.0`；见 §2 步骤 2；确认 `MAIN_APP_BASE_URL` 指向 `host.docker.internal:8000` |
| 检索 404 / 命中 0 | 主应用需在跑；超管可访问任意集合，普通用户受白名单限制；空集合属正常 |
| 联网搜索失败 | 本机填 `SEARCH_ENGINE_URL=http://host.docker.internal:8080/search`；确认宿主 8080 有 SearXNG |
| reranker / LLM 连接失败 | 检查 `RERANKER_API_URL` / `MAIN_LLM_API_URL` / key / 容器到网关网络；`RAGCHAIN_LOG_LEVEL=INFO` 看日志 |
| 改了代码但行为没变 | 先 `bash scripts/dev.sh ragchain restart`（代码是挂载的，重启即生效）；仍不对则按 §3.2 核对挂载点与 `app.server.__file__` |
| `docker run nbhx-ragchain:py312` 报 `ImportError: app.server` | **预期行为**：镜像里没有代码，靠 bind mount 提供；见 §3.4 |
| `ragchain check` 报依赖哈希不一致 | `requirements.txt` 变了，按 §3.3 `ragchain build` + `ragchain up` |
| Docker build/CLI 权限报错 | `export DOCKER_CONFIG=$PWD/.cache/docker`（本机 `~/.docker` 不可写）；或统一用 `bash scripts/dev.sh ragchain ...` |
| PyPI 超时 / 构建慢 | 构建加 `--build-arg PYPI_INDEX=https://mirrors.aliyun.com/pypi/simple` |
| 想确认容器实际配置 | `bash scripts/dev.sh ragchain ps`；`docker inspect <容器> --format '{{json .Config.Env}}'` |
| 沙箱 / 工具异常 | 看 `python_exec` 日志；调 `TOOL_EXEC_TIMEOUT_SEC` / `TOOL_OUTPUT_MAX_CHARS` / `TOOL_MAX_ITERATIONS` |

## 7. 启动后冒烟（可选）

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"superuser","password":"<seed-superuser-password>"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -sS -N -X POST http://127.0.0.1:8010/api/v1/chat-messages \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"query":"用一句话介绍你自己","search_mode":"本地检索"}'
```

## 8. 相关文档

- 统一入口脚本：`scripts/dev.sh`（`bash scripts/dev.sh --help`）
- 接口契约：`docs/langchain-rag-container-api-contract.md`
- 计划全文与架构决策：`.dsh/rag-core-chain-plan.md`（gitignored）
- RAG 流程图：`.dsh/rag-flow.md`（gitignored）
- 镜像/端口/环境变量明细：`ragchain/.env.example`、`ragchain/compose.yaml`
