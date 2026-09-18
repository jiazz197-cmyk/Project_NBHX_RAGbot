# ragchain 运维手册

> 适用对象：`ragchain/` 独立容器（LangChain RAG 核心链）。
> 镜像 `nbhx-ragchain:py312`（仅本地构建）；宿主端口 `8010` → 容器 `8000`。
> 本文所有命令的起始目录默认为仓库根 `/data/jiazhenyu/RAG/project-nbhx`。

## 1. 约定与前置条件

| 项 | 值 |
|---|---|
| 代码目录 / build context | `ragchain/`（独立包，不 import 主应用） |
| 构建文件 | `ragchain/Dockerfile`、`ragchain/requirements.txt`、`ragchain/compose.yaml` |
| 运行时配置 | `ragchain/.env`（从 `.env.example` 复制，gitignored） |
| 镜像 | `nbhx-ragchain:py312`（不推 registry） |
| 容器端口 | 宿主 `8010` → 容器 `8000` |
| 健康检查 | `GET http://127.0.0.1:8010/healthz` → `{"status":"ok"}` |
| 本机 Docker 特殊项 | `$HOME/.docker` 不可写，docker CLI/compose 需 `export DOCKER_CONFIG=<repo>/.cache/docker` |

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

# 本机 HOME 下 .docker 不可写，统一指到仓库内
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

# 3) 构建并启动 ragchain
docker compose -f ragchain/compose.yaml up -d --build --force-recreate

# 4) 验证
curl -fsS http://127.0.0.1:8010/healthz && echo     # {"status":"ok"}
docker compose -f ragchain/compose.yaml ps          # STATUS 应含 (healthy)
docker compose -f ragchain/compose.yaml logs --tail=100
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

## 3. 更新 ragchain 镜像

> 关键：ragchain 的代码是 **COPY 进镜像**的，不是 bind mount。改完代码只 `restart` 不会生效，必须重建镜像并用新镜像重建容器。

### 3.1 只改代码（requirements 不变，依赖层走缓存，通常数秒）

```bash
cd /data/jiazhenyu/RAG/project-nbhx
export DOCKER_CONFIG=$PWD/.cache/docker

docker build -t nbhx-ragchain:py312 -f ragchain/Dockerfile ragchain \
  --build-arg PYPI_INDEX=https://mirrors.aliyun.com/pypi/simple

docker compose -f ragchain/compose.yaml up -d --force-recreate

curl -fsS http://127.0.0.1:8010/healthz && echo
```

等价的 compose 一条命令：

```bash
docker compose -f ragchain/compose.yaml build
docker compose -f ragchain/compose.yaml up -d --force-recreate
```

### 3.2 验证镜像内代码与依赖

```bash
docker run --rm --entrypoint python nbhx-ragchain:py312 \
  -c "from app.server import app; import langchain, pandas; print('image ok', langchain.__version__, pandas.__version__)"
```

### 3.3 更新依赖（requirements.in 变更后）

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

# 重建镜像（requirements.txt 变化会使 uv 安装层失效）
cd /data/jiazhenyu/RAG/project-nbhx
export DOCKER_CONFIG=$PWD/.cache/docker
docker compose -f ragchain/compose.yaml build
docker compose -f ragchain/compose.yaml up -d --force-recreate
```

### 3.4 本机跑 ragchain 单测（不依赖容器）

```bash
cd /data/jiazhenyu/RAG/project-nbhx
# 首次建独立 venv：
# uv venv .cache/ragchain-venv --python 3.12
# UV_CACHE_DIR=$PWD/.cache/uv uv pip install --python .cache/ragchain-venv/bin/python \
#   -r ragchain/requirements-dev.txt --index-url https://mirrors.aliyun.com/pypi/simple

cd ragchain && ../.cache/ragchain-venv/bin/python -m pytest tests -q   # 当前基线 115 passed
```

## 4. 只改配置（不重建镜像）

`ragchain/.env` 由 compose `env_file` 在**创建容器时**注入；改完只需重建容器：

```bash
cd /data/jiazhenyu/RAG/project-nbhx
export DOCKER_CONFIG=$PWD/.cache/docker

vi ragchain/.env
docker compose -f ragchain/compose.yaml up -d --force-recreate
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
export DOCKER_CONFIG=$PWD/.cache/docker

docker compose -f ragchain/compose.yaml stop          # 停止容器，保留
docker compose -f ragchain/compose.yaml start         # 启动已停止的容器
docker compose -f ragchain/compose.yaml restart       # 重启进程（不会更新代码）
docker compose -f ragchain/compose.yaml down          # 停止并删除容器/网络，保留镜像与 .env
docker compose -f ragchain/compose.yaml ps
docker compose -f ragchain/compose.yaml logs -f --tail=200
```

## 6. 排障速查

| 现象 | 原因 / 处理 |
|---|---|
| 8010 起不来 | `ss -ltnp | grep 8010`；修改 `ragchain/compose.yaml` ports 或停冲突进程 |
| 请求 401 / JWT 不认 | `ragchain/.env` 的 `SECRET_KEY` / `ALGORITHM` 与根 `.env` 不一致 |
| 容器调主应用 connection refused | 主应用没监听 `0.0.0.0`；见 §2 步骤 2；确认 `MAIN_APP_BASE_URL` 指向 `host.docker.internal:8000` |
| 检索 404 / 命中 0 | 主应用需在跑；超管可访问任意集合，普通用户受白名单限制；空集合属正常 |
| 联网搜索失败 | 本机填 `SEARCH_ENGINE_URL=http://host.docker.internal:8080/search`；确认宿主 8080 有 SearXNG |
| reranker / LLM 连接失败 | 检查 `RERANKER_API_URL` / `MAIN_LLM_API_URL` / key / 容器到网关网络；`RAGCHAIN_LOG_LEVEL=INFO` 看日志 |
| 改了代码但行为没变 | 代码在镜像内，必须按 §3 重建；`restart` 不会更新代码 |
| Docker build/CLI 权限报错 | `export DOCKER_CONFIG=$PWD/.cache/docker`（本机 `~/.docker` 不可写） |
| PyPI 超时 / 构建慢 | 构建加 `--build-arg PYPI_INDEX=https://mirrors.aliyun.com/pypi/simple` |
| 想确认容器实际配置 | `docker compose -f ragchain/compose.yaml images`；`docker inspect <容器> --format '{{json .Config.Env}}'` |
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

- 接口契约：`docs/langchain-rag-container-api-contract.md`
- 计划全文与架构决策：`.dsh/rag-core-chain-plan.md`（gitignored）
- RAG 流程图：`.dsh/rag-flow.md`（gitignored）
- 镜像/端口/环境变量明细：`ragchain/.env.example`、`ragchain/compose.yaml`
