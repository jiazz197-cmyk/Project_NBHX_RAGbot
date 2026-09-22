<div align="center">

# NBHX AI 助手平台

### 宁波华翔企业知识工作台 · 对话即检索，上传即建库

<p align="center">
  <a href="#项目简介">项目简介</a> •
  <a href="#核心能力">核心能力</a> •
  <a href="#系统架构">系统架构</a> •
  <a href="#技术栈">技术栈</a> •
  <a href="#快速开始">快速开始</a> •
  <a href="#api-一览">API</a> •
  <a href="#工程约定与质量门禁">工程约定</a> •
  <a href="#许可">许可</a>
</p>

![Python](https://img.shields.io/badge/Python-3.12-3776AB.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.116-009688.svg)
![Vue](https://img.shields.io/badge/Vue-3-4FC08D.svg)
![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)
![Sanitized](https://img.shields.io/badge/Edition-脱敏开源版-lightgrey.svg)

</div>

---

> [!IMPORTANT]
> ### 🔒 本仓库为「脱敏开源版本」（Sanitized Open-Source Edition）
>
> 本仓库面向开源分享与技术交流，已对企业敏感信息做脱敏处理：
>
> - **密钥与凭证**：真实 API Key / Token / 口令 / 证书均已移除，示例配置统一使用占位符（`change_me_*`、`<...>`）；
> - **配置与数据**：只保留配置字段结构与示例值，不含生产配置、业务数据、日志与数据库导出；
> - **模型与资产**：不含模型权重，AI 推理能力全部通过外部 HTTP 服务接入。
>
> ⚠️ 仓库内出现的任何账号、口令、API Key 均为**开发环境示例或占位符**，不对应任何生产系统，**请勿直接复用**；请勿将本仓库的示例配置用于生产环境。
>
> 企业名称与业务场景描述仅用于交代项目背景，不构成任何数据披露或授权。

---

## 项目简介

**NBHX AI 助手平台**是宁波华翔的企业级 AI 工作台：把散落在制度文件、Excel 台账与员工经验里的知识，收敛成一个能对话、能溯源的工作入口。员工用自然语言提问，平台从企业内部知识库检索依据后作答；文档与 Excel 类数据库上传后自动解析、切分、向量化建库，随即可被对话检索引用。

平台由三个可独立部署的部分组成，各司其职：

| 组成 | 位置 | 职责 |
|---|---|---|
| **主应用** | 本仓库根目录（[`main.py`](main.py)、[`app/`](app/)） | 认证鉴权、知识库与文件管理、文档处理任务、RAG 检索、会话记忆与用户画像、OCR 接口 |
| **RAG 核心链** | [`ragchain/`](ragchain/) | LangChain 编排的对话生成主链：改写 → 检索 → 重排 → 流式作答（SSE），独立容器部署 |
| **前端应用** | [`frontend/`](frontend/) | Vue 3 工作台：对话、知识库管理、用户管理；pnpm + Turbo monorepo |

**AI 推理能力全部是外部 HTTP 服务**（BGE-M3 嵌入、bge-reranker 重排、主/辅 LLM、OCR、标签生成），本仓库不含模型权重，也不在进程内加载大模型；业务数据全部落在企业自有的 PostgreSQL / Redis / MinIO 中。

---

## 核心能力

### 1. 知识库对话（SSE 流式）

自然语言提问，答案带企业内部依据。对话主链由 [`ragchain/`](ragchain/) 承接：查询改写与关键词抽取 → 多集合检索 → 重排 → 流式作答，全程 SSE 推送到前端，支持随时中止。主应用侧同时提供：

- **会话与消息持久化**：会话重命名、分页回看、删除（PostgreSQL 为真相源）
- **长上下文自动压缩**：超长对话自动压缩为摘要，保住检索与作答质量
- **用户画像摘要**：从历史对话提炼问询习惯与偏好，辅助个性化回答

> 主应用保留同名路由 `POST /api/v1/chat-messages`（`ChatOrchestratorPort` 预留位），Nginx 已把该路径分流到 ragchain 容器；单独部署主应用而未接 ragchain 时，这两个端点返回 `501 CHAT_ORCHESTRATOR_NOT_CONFIGURED`，前端给出明确未配置提示。

### 2. 智能文档处理

上传即处理，无需人工标注：

- **格式**：PDF、Word（doc/docx）、PowerPoint（ppt/pptx）、HTML、Markdown、纯文本（txt）、JSON；Excel（xlsx/xls）走「Excel 类数据库」入口单独建库
- **流水线**：解析 → 结构感知切分（保留标题层级与表头语义）→ 向量化 → 写入 PostgreSQL + pgvector
- **进度可观测**：WebSocket 实时推送处理进度（并保留轮询兜底），任务结果回传逐文件失败原因
- **PDF 混合解析**：有文本层的页面走本地文本提取短路，扫描页才调 OCR 服务——省时且不丢页

### 3. Excel 类数据库

Excel 台账按 sheet 建库，保留表头语义；检索接口与文档库分开（`/retriever/db`、`/retriever/excel`），命中后可定位源文件并按需下载解析，供图表与数据问答使用。

### 4. RAG 检索

- **向量检索**：BGE-M3 嵌入（OpenAI 兼容 `/v1/embeddings`，批量入库）+ pgvector
- **混合检索**：开启后并入 PostgreSQL 字面路（pg_trgm + ILIKE，对项目号、人名、科目名、文件编号这类精确 token 友好），两路按 **RRF 名次融合**，量纲不可比也不影响排序
- **重排**：外部 bge-reranker 服务，含探活 / 重试 / 熔断
- **多集合统一收池**：跨库召回后统一排序，不再对单个集合做固定配额截断
- **内容级去重**：写入端按落库文本的 md5 指纹判重（同 collection 内只落一份），预检与写入置于 advisory lock 临界区，重复上传不再挤占召回名额；存量数据可用 [`scripts/dedupe_chunks.py`](scripts/dedupe_chunks.py) 对账清理

### 5. OCR 与文档标签

- **OCR 服务化**：独立 paddlex 容器暴露 `POST /ocr`，主应用经 `PADDLE_OCR_ENDPOINT` 调用，支持中英文混排与表格结构；失败逐页降级、任务不中断
- **标签生成服务化**：独立 tagger 容器暴露 `POST /v1/tags`，含探活 / 重试 / 熔断，服务不可用时降级为本地简单标签
- **PDF 转图片**：配合 OCR 做预览与逐页识别

### 6. 任务基础设施

文档处理与 OCR 共享一套异步任务底座：Redis 任务状态 + 观察者模式 + 线程池执行器 + WebSocket 进度推送 + 按用户队列调度 + 取消语义 + MinIO 临时文件对账清理。

---

## 系统架构

### 运行时拓扑

```
        浏览器 / 移动端
              │ HTTPS
              ▼
           Nginx ──── 前端静态资源（构建产物）
              │
              ├─ /api/v1/chat-messages、…/{task_id}/stop ─► ragchain（LangChain 核心链 :8010）
              │                                                   │  回调主应用：/retriever、/conversations、/messages
              └─ 其余 /api/v1/* ──────────────────────────► 主应用 FastAPI（:8000）
                                                                    │
                        ┌───────────────────────────────────────────┼───────────────────────────┐
                        ▼                                           ▼                           ▼
              PostgreSQL + pgvector                              Redis                       MinIO
         （业务数据 / 向量集合 / 任务态）              （任务状态·限流·缓存）    （文档·OCR 产物·临时文件）
                        ▲
                        │ HTTP（全部外部服务，本仓库不含模型权重）
        AI 推理网关：BGE-M3 嵌入 · bge-reranker 重排 · 主/辅 LLM · OCR（paddlex） · TagGenerator
```

Nginx 按路径显式分流，未知 `/api/v1/*` 直接 404，不做兜底转发；模板见 [`nginx/nginx.conf.template`](nginx/nginx.conf.template)。

### 分层架构（Clean Architecture，强制）

依赖方向（向内为尊）：`domain ◄ ports ◄ usecases ◄ adapters ◄ api(组合根) + main.py`。

| 层 | 位置 | 职责 | 禁止 |
|---|---|---|---|
| **domain** | [`app/domain/`](app/domain/) | 纯领域实体 / 值对象 / 规则，无 IO | import 外层 |
| **ports** | [`app/ports/`](app/ports/) | `Protocol` 契约（`contracts/`、`outbound/`）+ 纯 DTO（`dto/`） | 做 IO、import 适配器 |
| **usecases** | [`app/usecases/`](app/usecases/) | 业务编排，构造函数注入 Port | import `app.adapters` / ORM / HTTP 客户端 |
| **adapters** | [`app/adapters/`](app/adapters/) | 实现 Port，桥接 ORM / SDK / 框架；driving（`web/`、`workers/`）与 driven（`doc_processing`、`knowledge`、`ragsystem`、`ocr`、`chat_archive`、`monitoring` 等）同层 | 容纳完整业务流程（应留在 UseCase） |
| **api / main.py** | [`app/api/v1/`](app/api/v1/)、[`main.py`](main.py) | 组合根：装配 Adapter + UseCase，HTTP 薄边界、生命周期管理 | — |

该约束由 [`scripts/check_layered_architecture.sh`](scripts/check_layered_architecture.sh)（8 条规则）在 CI 与本地强制校验，设计说明见 [`docs/di-and-layered-architecture.md`](docs/di-and-layered-architecture.md)。

---

## 技术栈

| 层 | 选型 | 用途 |
|---|---|---|
| **后端框架** | Python 3.12 + FastAPI 0.116 + Uvicorn + Pydantic 2 | 异步 API、配置校验、生命周期管理 |
| **对话编排** | LangChain（[`ragchain/`](ragchain/) 独立容器） | 查询改写、工具调用、流式作答、SSE |
| **持久化** | SQLAlchemy 2.0（async） | 用户 / 会话 / 消息 / 文件 / 任务等（启动时按 ORM 元数据建表） |
| **关系 + 向量库** | PostgreSQL 14+ + pgvector | 业务真相源与 RAG 向量集合（同一实例） |
| **缓存 / 任务态** | Redis | 任务状态、WS 进度镜像、限流 |
| **对象存储** | MinIO | 知识库文件、文档产物、OCR 产物、任务临时文件 |
| **检索** | BGE-M3 嵌入 + bge-reranker-v2-m3 + pg_trgm 字面路（RRF 融合） | 语义 + 关键词混合召回与重排 |
| **前端** | Vue 3 + Vite + TypeScript + Pinia + Vue Router + pnpm/Turbo | 对话、知识库管理、用户管理 |
| **并发** | ThreadPoolExecutor + 任务队列 + 观察者 + WebSocket | 文档处理、OCR 等异步任务与进度推送 |
| **开发环境** | Docker dev 容器（镜像供环境、bind mount 供代码） | 团队一致的本地开发；见 [`docs/docker-dev-guide-colleague.md`](docs/docker-dev-guide-colleague.md) |

---

## 快速开始

### 前置条件

| 依赖 | 说明 |
|---|---|
| Python 3.12+ | 主应用与 ragchain 均使用 |
| PostgreSQL 14+ 且含 **pgvector** 扩展 | 向量检索必需 |
| Redis 6+ | 任务状态与限流 |
| MinIO | 对象存储，需预先创建桶 |
| Node.js 18+ / **pnpm 8.15.9** | 前端；`package.json` 已锁 `packageManager`，用 `corepack pnpm` 即可 |
| Docker（可选但推荐） | 走开发容器路线时必需 |

AI 推理服务（嵌入 / 重排 / 主辅 LLM / OCR / 标签）需提前部署并拿到地址与密钥；本仓库不包含这些服务。

### 路线 A：开发容器（推荐）

环境来自镜像、代码来自 bind mount —— 改代码不用碰镜像，只有 `requirements*.txt` 变化才需要重建；容器名与数据卷按人隔离，多人共用一台机器互不干扰。

```bash
bash scripts/dev.sh docker up        # 首次：构建镜像 + 起常驻容器
bash scripts/dev.sh docker fe-setup  # 一次性：容器内装前端依赖
bash scripts/dev.sh docker backend   # 容器内起后端（宿主 http://localhost:8000）
bash scripts/dev.sh docker frontend  # 容器内起前端（宿主 http://localhost:8888）
bash scripts/dev.sh docker test -q   # 容器内跑 pytest
bash scripts/dev.sh docker shell     # 进容器
```

多人同机时给每人一组端口：`API_PORT=8001 WEB_PORT=8889 bash scripts/dev.sh docker up`。完整说明（配置改哪里、镜像更新流程、FAQ）见 [`docs/docker-dev-env.md`](docs/docker-dev-env.md) 与两份操作手册（[同事版](docs/docker-dev-guide-colleague.md) / [管理员版](docs/docker-dev-guide-admin.md)）。

### 路线 B：本机虚拟环境

```bash
# 1. 依赖与缓存全部落在仓库内（./.venv 与 ./.cache），不写 $HOME
bash scripts/setup_local_env.sh --with-rag

# 2. 每个新 shell 激活一次（或统一用 scripts/dev.sh 代跑）
source scripts/env.sh

# 3. 配置环境变量
cp .env.example .env      # 填入 PostgreSQL / Redis / MinIO / AI 推理服务地址

# 4. 首次准备数据库：CREATE EXTENSION IF NOT EXISTS vector;

# 5. 启动
python main.py            # http://localhost:8000 ，接口文档 /api/v1/docs
```

> 依赖清单分三份：[`requirements.txt`](requirements.txt)（主应用运行时）、[`requirements-rag.txt`](requirements-rag.txt)（LangChain / LlamaIndex 栈，随 ragchain 容器部署）、[`requirements-dev.txt`](requirements-dev.txt)（测试工具）。用 `uv pip install -r req…` 安装时**必须带** `--overrides requirements-overrides.txt`，细节见 [CLAUDE.md](CLAUDE.md)。

### 前端

```bash
cd frontend
corepack pnpm install --frozen-lockfile   # 不要用全局 pnpm 10/12：会把 lockfile 从 v6 升到 v9
cp apps/chat/env.example apps/chat/.env   # 配置后端与 ragchain 地址
corepack pnpm dev                         # http://localhost:8888
```

### RAG 核心链（ragchain）

```bash
bash scripts/dev.sh ragchain up        # 首次/换配置：缺镜像时构建并起容器
bash scripts/dev.sh ragchain restart   # 改完代码：重启进程即生效（代码是 bind mount）
bash scripts/dev.sh ragchain test      # 该服务自己的测试
```

运维细节（镜像、registry、健康检查、排障）见 [`ragchain/docs/operations.md`](ragchain/docs/operations.md)。

### 常用命令速查

```bash
bash scripts/dev.sh backend | frontend | test | guard | py <args> | shell
bash scripts/dev.sh docker up|backend|frontend|test|guard|build|push|pull|down|logs|ps|check
bash scripts/dev.sh ragchain up|restart|build|check|logs|ps|down|shell|test
```

### 开发端口

| 服务 | 默认宿主端口 |
|---|---|
| 主应用 FastAPI | 8000 |
| 前端 dev server | 8888 |
| RAG 核心链（ragchain） | 8010 |
| OCR 服务（paddlex） | 9002 |
| 标签生成服务（tagger） | 8004 |

---

## 配置

配置集中在仓库根 **`.env`**（模板 [`.env.example`](.env.example)，由 `app/core/config.py` 读取），容器编排相关覆盖在 [`docker/compose.dev.yaml`](docker/compose.dev.yaml)。

| 分组 | 关键项 | 说明 |
|---|---|---|
| 应用 | `ENVIRONMENT`、`DEBUG`、`HOST`、`PORT`、`ALLOWED_HOSTS` | 运行模式与监听地址 |
| 数据库 | `POSTGRES_*` | PostgreSQL + pgvector 连接 |
| 缓存 | `REDIS_*` | 任务状态、限流 |
| 对象存储 | `MINIO_*`、`MINIO_BUCKET_NAME` | 桶名与访问凭据 |
| 嵌入 / 重排 | `BGE_M3_*`、`RERANKER_*`、`AI_INFERENCE_API_KEY` | BGE-M3 与重排服务 |
| 大模型 | `MAIN_LLM_*`（作答/改写）、`SUB_LLM_*`（记忆压缩、用户画像） | OpenAI 兼容网关地址、模型名与密钥 |
| OCR | `PADDLE_OCR_ENDPOINT` | 不配置 = 整体禁用 OCR（走本地文本提取） |
| 标签 | `TAGGER_ENDPOINT`、`TAGGER_TIMEOUT_SEC`、`TAGGER_FAILURE_THRESHOLD` | 服务化标签生成，含熔断参数 |
| 检索 | `RETRIEVER_ALLOWED_*_COLLECTIONS`、`RETRIEVAL_HYBRID_ENABLED`、`RETRIEVAL_*` | 集合白名单与混合检索开关（默认关） |
| 安全 | `SECRET_KEY`、`ACCESS_TOKEN_EXPIRE_MINUTES`、`INTERNAL_API_KEY`、`RATE_LIMIT_*` | JWT、服务间调用、限流 |
| 上传限制 | `KNOWLEDGE_MAX_DOCUMENT_FILE_SIZE_MB`、`KNOWLEDGE_MAX_EXCEL_FILE_SIZE_MB` | 知识库文件大小上限 |

> 生产部署请替换所有默认密钥，并按 [`docs/SECURITY_PUBLIC_ENDPOINTS.md`](docs/SECURITY_PUBLIC_ENDPOINTS.md) 复核公开端点；仓库内 `.env` 为本地开发用，已 gitignore。

---

## API 一览

启动后：Swagger UI `http://localhost:8000/api/v1/docs`，ReDoc `http://localhost:8000/api/v1/redoc`，健康检查 `GET /api/v1/health`。

业务前缀集中管理于 [`app/api/v1/prefixes.py`](app/api/v1/prefixes.py)，装配于 [`app/api/v1/registry.py`](app/api/v1/registry.py)，全部挂在 `/api/v1` 下：

| 功能 | 前缀 | 提供方 |
|---|---|---|
| 认证与用户 | `/api/v1/auth` | 主应用（JWT 登录注册、superuser 用户管理） |
| 对话生成 | `/api/v1/chat-messages` | **ragchain**（SSE 流式；主应用侧为预留位） |
| 会话与消息 | `/api/v1/conversations`、`/api/v1/messages` | 主应用（本地记忆，持久化于 PostgreSQL） |
| 文件存储 | `/api/v1/files` | 主应用 |
| 文档任务 | `/api/v1/document-tasks` | 主应用（含 WebSocket 进度推送） |
| 知识库 | `/api/v1/knowledge` | 主应用（记录列表/删除、文档上传、Excel 数据库上传） |
| RAG 检索 | `/api/v1/retriever` | 主应用（文档库 / Excel 库分别检索） |
| 上下文压缩 | `/api/v1/context-compression` | 主应用 |
| 对话摘要 | `/api/v1/chat-summary` | 主应用（用户画像） |
| OCR | `/api/v1/ocr` | 主应用（图片识别、PDF 转图片） |

接口契约文档：[`docs/langchain-api-contract.md`](docs/langchain-api-contract.md)（对话与记忆）、[`docs/langchain-rag-container-api-contract.md`](docs/langchain-rag-container-api-contract.md)（主应用 ↔ ragchain 边界）。

### 前端页面

| 页面 | 路径 | 访问说明 |
|---|---|---|
| 登录 / 注册 | `/login`、`/register` | 公开；已登录访问登录页将跳转对话页 |
| AI 对话 | `/chat` | 需登录；知识库对话与文档上传 |
| 知识库管理 | `/knowledge` | 需登录；admin / superuser 可查看与删除记录，普通用户可上传并查看自己的进度 |
| 用户管理 | `/users` | 需登录且角色为 **superuser** |

> 通用页面权限框架（路由守卫 + 后端 PATCH + 前端 service）已保留但默认关闭（`PAGE_PERMISSION_MANAGEMENT_ENABLED=False`），新增业务页面时再启用；未登录访问受保护路由会跳转登录页，另有 6 小时无操作自动退出。

---

## 工程约定与质量门禁

| 项 | 约定 |
|---|---|
| **分层依赖** | `domain ◄ ports ◄ usecases ◄ adapters ◄ api`；改动后先跑 `bash scripts/check_layered_architecture.sh`（8 条规则） |
| **业务异常** | 统一用 `app.core.exceptions.APIException` 子类，自动映射 404/403/401/422/502；不要抛裸 `ValueError` |
| **日志** | `get_logger("auth.login")` → 实际 logger 名必须落在 `app.*` 路由表，否则静默丢失 |
| **SQLAlchemy** | Adapter 采用「每方法一 session」模式，方法内 commit |
| **行尾** | 全仓 LF；提交前可跑 `bash scripts/check-crlf.sh` |
| **测试** | `pytest`（无根 conftest，直接跑）；测试在 [`tests/`](tests/)，覆盖上传、检索、任务并发、鉴权与分层守卫等 |
| **CI** | GitLab CI 两个 job：`arch-guard`（分层守卫）与 `pytest`；另保留 GitHub Actions 的分层守卫 workflow |
| **安全自检** | `bash scripts/security_smoke_test.sh`、`bash scripts/infra_doctor.sh` |

运维与排障脚本（节选）：[`scripts/dedupe_chunks.py`](scripts/dedupe_chunks.py)（chunk 重复对账，默认 dry-run）、[`scripts/ensure_fulltext_index.py`](scripts/ensure_fulltext_index.py)（字面检索索引）、[`scripts/render_nginx_conf.sh`](scripts/render_nginx_conf.sh)、[`scripts/install_systemd.sh`](scripts/install_systemd.sh)（[`deploy/`](deploy/) 下的 systemd 模板）。

---

## 仓库结构

| 路径 | 说明 |
|---|---|
| [`main.py`](main.py) | FastAPI 入口与生命周期（DB/Redis/MinIO 初始化、任务基础设施装配、RAG 装配、MinIO 对账调度、有序关闭） |
| [`app/api/v1/`](app/api/v1/) | 路由层（组合根）：`registry.py` 装配、`prefixes.py` 前缀 |
| [`app/usecases/`](app/usecases/) | 业务用例（auth、knowledge、document_processing、chat_summary、context_compression 等） |
| [`app/ports/`](app/ports/) | `Protocol` 契约与纯 DTO |
| [`app/adapters/`](app/adapters/) | Port 实现：`doc_processing/`（解析/切分/嵌入客户端/任务执行器）、`knowledge/`（表与指纹对账、字面检索）、`ragsystem/`（检索与图表）、`ocr/`、`chat_archive/`、`monitoring/`、`web/`、`workers/` |
| [`app/domain/`](app/domain/) | 无 IO 领域规则（auth / file_manager / knowledge / retrieval 排序与融合） |
| [`app/models/orm/`](app/models/orm/) | SQLAlchemy ORM |
| [`app/core/`](app/core/) | 配置、数据库、任务管理、执行器、WebSocket、中间件、安全、日志、MinIO 对账（外层工具岛） |
| [`ragchain/`](ragchain/) | LangChain RAG 核心链独立服务（自带 Dockerfile / compose / 测试 / [运维手册](ragchain/docs/operations.md)） |
| [`frontend/`](frontend/) | pnpm + Turbo monorepo；应用 [`frontend/apps/chat`](frontend/apps/chat)，共享组件 `frontend/packages/components` |
| [`docker/`](docker/) | 开发容器：`dev.Dockerfile`、`compose.dev.yaml`、`entrypoint.sh` |
| [`nginx/`](nginx/) | 网关模板（按路径分流到主应用与 ragchain） |
| [`deploy/`](deploy/) | systemd 服务模板与运维定时器 |
| [`infra-migration/`](infra-migration/) | 共享基础设施迁移脚本（备份 / 迁移 / compose 模板） |
| [`scripts/`](scripts/) | 环境准备、命令入口 `dev.sh`、分层守卫、索引与去重运维脚本 |
| [`tests/`](tests/) | 单元与回归测试 |
| [`docs/`](docs/) | 架构、契约、容器化与检索设计文档 |
| [`CLAUDE.md`](CLAUDE.md) | 面向 AI 编码助手的项目上下文与约定（含本地环境、依赖治理细节） |

---

## 文档索引

| 文档 | 内容 |
|---|---|
| [`docs/di-and-layered-architecture.md`](docs/di-and-layered-architecture.md) | 依赖注入与分层架构落地方式 |
| [`docs/architecture-route-usecase-port-adapter.md`](docs/architecture-route-usecase-port-adapter.md) | 一次请求穿过四层的完整路径 |
| [`docs/retrieval-hybrid-fulltext.md`](docs/retrieval-hybrid-fulltext.md) | 混合检索（pg_trgm 字面路 + RRF）的设计与取舍 |
| [`docs/task-state-truth.md`](docs/task-state-truth.md) | 异步任务状态的真相源与留存策略 |
| [`docs/docker-dev-env.md`](docs/docker-dev-env.md) | 开发容器机制（镜像/挂载/卷/端口隔离） |
| [`docs/docker-dev-guide-colleague.md`](docs/docker-dev-guide-colleague.md) · [`admin`](docs/docker-dev-guide-admin.md) | 同事版 / 管理员版操作手册 |
| [`docs/langchain-api-contract.md`](docs/langchain-api-contract.md) · [`rag-container`](docs/langchain-rag-container-api-contract.md) | 对话与记忆契约、主应用 ↔ ragchain 边界 |
| [`docs/SECURITY_PUBLIC_ENDPOINTS.md`](docs/SECURITY_PUBLIC_ENDPOINTS.md) | 公开端点清单与安全复核 |

---

## 版本历程

**2026-09 · 平台工程化（当前）**

- 开发环境容器化：环境来自镜像、代码 bind mount，每人一组容器 / 数据卷 / 端口
- LangChain RAG 核心链拆为独立容器 [`ragchain/`](ragchain/)，Nginx 按路径分流
- OCR 与标签生成服务化，主应用进程内不再依赖本地模型
- 检索：混合检索（字面路 + RRF 融合）、多集合统一收池
- 写入：chunk 内容级去重（指纹预检 + advisory 写锁）与存量对账脚本
- 嵌入客户端收敛到 llama-index `OpenAIEmbedding`，支持批量入库

**v0.3.0（2026-09）**：历史业务模块下线（含报价 / SQLServer / PDM 查询与填报模块）；知识库域语义化重构；知识库管理页新增文档与 Excel 数据库上传、同名冲突处理；品牌全面切换为宁波华翔（NBHX）。

**v0.2.x（2026-03 ~ 2026-06）**：聊天记录归档与用户画像摘要；PDF 转图片与 OCR 提取；WebSocket 实时任务进度；JWT 用户认证；Vue 3 前端上线（对话 / 知识库 / 用户管理）。

**v0.1.0（2025-12）**：RAG 知识库检索系统上线；多格式文档处理管线；文件管理与对象存储集成。

---

## 许可

本项目采用 **Apache License 2.0** 开源许可，全文见 [LICENSE](LICENSE)。

- 允许自由使用、修改、分发（含商业用途），需保留版权声明与许可声明；
- 平台面向宁波华翔内部业务场景构建，代码以 Apache-2.0 开放，欢迎参考与复用；
- 第三方依赖（主应用 155 条 / RAG 栈 96 条 / 开发工具 5 条）各自遵循其原始许可。

### 脱敏开源版本声明

本仓库为**脱敏开源版本**：仓库中不含企业真实凭证与密钥，示例配置一律使用占位符（`change_me_*`、`<...>`），配置仅保留字段结构，不含生产配置、业务数据与模型权重。

- 仓库内出现的任何账号、口令、API Key 均为**开发环境示例或占位符**，不对应任何生产系统，**请勿直接复用**；
- 使用者应自行生成密钥与口令，并自行承担将示例配置用于生产环境所导致的风险；
- 如需基于本仓库二次开发，请在部署前完整复核 `README` 的「配置」章节与 `.env.example`，替换全部占位值。

```
Copyright 2026 宁波华翔电子股份有限公司（NBHX）
```

<div align="center">

**NBHX AI 助手平台** · 宁波华翔电子股份有限公司

</div>
