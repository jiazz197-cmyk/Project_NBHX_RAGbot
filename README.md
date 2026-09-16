<div align="center">

# NBHX AI 助手平台

### 宁波华翔企业内部 AI 工作台

<p align="center">
  <a href="#简介">简介</a> •
  <a href="#为宁波华翔带来的价值">为宁波华翔带来的价值</a> •
  <a href="#核心能力">核心能力</a> •
  <a href="#技术栈">技术栈</a> •
  <a href="#架构概览">架构概览</a> •
  <a href="#前端页面与权限">前端页面与权限</a> •
  <a href="#快速开始">快速开始</a>
</p>

![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.116.1-009688.svg)
![Vue](https://img.shields.io/badge/Vue-3-4FC08D.svg)
![Scope](https://img.shields.io/badge/用途-企业内部定制-orange.svg)

</div>

---

## 简介

NBHX AI 助手平台是为<strong>宁波华翔</strong>量身定制的企业内部 AI 工作台，仅服务于宁波华翔自身的业务运转，**不面向外部部署**。它把散落在企业文档、Excel 与员工经验里的知识收敛到一个对话式工作台：员工用自然语言提问，系统从企业知识库中检索依据并作答；文档与 Excel 类数据库上传后自动解析、建库，随后可被对话检索引用。

平台采用 FastAPI（Python 3.12）后端 + Vue 3（pnpm / Turbo monorepo）前端，遵循 **domain ◄ ports ◄ usecases ◄ adapters ◄ api(组合根)** 的 Clean Architecture。对话编排由本服务 FastAPI 承接，底层预留 **LangChain + ChatOrchestratorPort**；当前接口已注册且路径不变，但实现未完成前统一返回明确的 501 未配置错误。RAG 检索、文档建库、对话摘要与上下文压缩均由本服务提供；所有业务数据落在宁波华翔自有的 PostgreSQL / Redis / MinIO 之中。

---

## 为宁波华翔带来的价值

平台不是通用问答工具，而是围绕企业内部知识与文档资产做的沉淀与再利用。

| 场景 | 改造前 | 改造后 |
|------|--------|--------|
| **知识沉淀** | 企业文档散落各处，新人查资料靠问老人 | 文档 / Excel 数据库上传后自动建 RAG 知识库，对话直接引用依据 |
| **文档处理** | 手工整理 PDF / Word / Excel，费时且难追踪 | 自动解析、切分、向量化，WebSocket 实时推送处理进度 |
| **知识检索** | 搜关键词命中率低，跨文件查找困难 | BGE-M3 嵌入 + Reranker + pgvector 语义检索，支持文档库与 Excel 库分接口检索 |
| **权限分层** | 管理操作无差异化授权 | JWT + admin / superuser 角色控制，用户管理、知识库管理按角色开放 |

---

## 核心能力

### AI 知识库对话

员工直接用自然语言提问，AI 基于企业内部文档给出有依据的精准回答。对话编排由本服务 FastAPI 承接（`/chat-messages` SSE 流式端点），底层预留 `ChatOrchestratorPort`；未配置 LangChain 实现前，六个聊天接口返回 501 + `CHAT_ORCHESTRATOR_NOT_CONFIGURED`，前端显示明确未配置提示，不会误解为登录失效或网络错误。本服务同时提供检索、记忆与归档支撑：

- **本地知识库检索**：基于已上传企业文档的 RAG（BGE-M3 嵌入 + 重排序 + pgvector），由本服务 `/retriever` 提供，供对话引用依据
- **联网搜索**：由后续 LangChain 编排按需接入外部搜索
- **对话历史与会话/消息持久化**：支持重命名与分页回看
- **长上下文自动压缩**：LangChain + OpenAI 兼容 LLM，从本服务本地消息仓储拼接 recent/older 后压缩，超长对话不丢检索与作答质量
- **用户画像摘要**：自动分析历史对话，提炼问询习惯与偏好
- **协作式取消**：客户端可随时停止当前回答流

### 智能文档处理

上传企业文档后，系统自动解析内容并建立可检索的知识库，无需人工标注或整理。

- 支持格式：PDF、Word（.docx）、Excel（.xlsx / .xls）、PowerPoint（.pptx）、HTML、纯文本
- 文档知识库上传后自动解析、切分、向量化；Excel 类数据库按 sheet 建库，保留表头语义
- 上传即处理，**WebSocket** 实时推送处理进度（并保留轮询兜底）
- 处理完成后文档内容立即可被 AI 对话检索引用

### OCR 图像识别

将扫描件、拍照图片或 PDF 中的文字自动识别并提取为结构化文本。

- 支持中英文混合识别、表格结构识别
- 支持将 PDF 逐页转换为图片，便于预览和 OCR 处理

### 对话记录归档

平台自动分析用户历史对话，提炼问询习惯与偏好，生成**用户画像摘要**；并在长对话上下文超长时做自动压缩，保持检索与作答质量。

---

## 技术栈

| 层 | 选型 | 用途 |
|----|------|------|
| **后端框架** | FastAPI 0.116 + Uvicorn + Pydantic 2 | 异步 API、配置校验、生命周期管理 |
| **对话编排** | LangChain（预留） | 本服务 FastAPI 承接 SSE 流式对话路径；实现前统一返回 501 预留错误 |
| **LLM 编排** | LangChain | 上下文压缩、文档切分（非对话主链路） |
| **LLM 模型** | Qwen3-8B / Qwen3.6-35B（本地 vLLM） | 关键词/意图、流式作答、上下文压缩 |
| **OCR** | PaddleOCR | PDF 图纸文字识别 |
| **向量检索** | BGE-M3 嵌入 + 重排序模型 + pgvector | RAG 检索（企业文档知识库） |
| **关系/向量库** | PostgreSQL + pgvector | 对话、消息、RAG 集合、任务状态的真相源 |
| **缓存/任务态** | Redis | 任务状态、WS 进度缓存镜像、限流 |
| **对象存储** | MinIO | 文档、OCR 产物、任务临时文件与知识库导入文件 |
| **ORM** | SQLAlchemy 2.0（async） | 会话、消息、用户、任务/文件等持久化 |
| **前端** | Vue 3 + Vite + TypeScript + Vue Router + Pinia + pnpm/Turbo monorepo | 对话、知识库管理、用户管理 |
| **并发** | `ThreadPoolExecutor` + 任务队列 + 观察者 | 文档处理、OCR 等异步任务执行与进度推送 |

---

## 架构概览

### 分层架构（Clean Architecture）

依赖方向（向内为尊）：`domain ◄ ports ◄ usecases ◄ adapters ◄ api(组合根) + main.py`。内层（domain / ports / usecases）只依赖端口与 DTO，不直接 import 适配器实现；`api` 与 `main.py` 作为组合根负责装配 Adapter 并注入 UseCase。该约束由 [`scripts/check_layered_architecture.sh`](scripts/check_layered_architecture.sh) 强制校验（CI 门禁，见 `.github/workflows/layered-architecture-guard.yml`）。

| 层 | 位置 | 职责 | 禁止 |
|----|------|------|------|
| **domain** | `app/domain/` | 纯领域实体/值对象/规则，无 IO | import 外层 |
| **ports** | `app/ports/` | `Protocol` 契约（`contracts/`、`outbound/`）+ 纯 DTO（`dto/`） | 做 IO、import 适配器 |
| **usecases** | `app/usecases/` | 业务编排，构造函数注入 Port | import `app.adapters` / ORM / HTTP 客户端 |
| **adapters** | `app/adapters/` | 实现 Port，桥接 ORM / 集成 / 配置（driving 与 driven 同层） | 容纳完整业务流程（留在 UseCase） |
| **api / main.py** | `app/api/v1/`、`main.py` | 组合根：装配 Adapter + UseCase，HTTP 薄边界 | - |

> `app/core/`、`app/models/` 暂作外层工具岛原位保留，`core` 未纳入内层禁列。

### 对话编排（LangChain 预留 + 本服务）

流量在 Nginx 层按路径拆分（见 [`nginx/nginx.conf.template`](nginx/nginx.conf.template)）：业务前缀（`/auth`、`/chat-messages`、`/conversations`、`/messages`、`/knowledge`、`/document-tasks`、`/retriever`、`/context-compression`、`/chat-summary` 等）都显式反代到 FastAPI 后端；未知 `/api/v1/*` 返回 404，不再兜底外部聊天服务。

- **对话主链路**：本服务 API + `ChatOrchestratorPort` 预留，后续由 LangChain 实现 SSE 流式作答与工作流编排
- **RAG 检索**：本服务 `app/adapters/ragsystem/`（BGE-M3 嵌入 + 重排序 + pgvector），经 `app/adapters/retriever.py` facade 暴露，供对话引用
- **上下文压缩**：`app/adapters/context_compressor.py`，LangChain + LLM，从本地对话消息仓储拼接 recent/older 后压缩
- **对话归档**：`app/adapters/chat_archive/`，从本地消息仓储抽取历史问询文本，生成用户画像摘要

### 任务基础设施

所有异步任务共享：Redis 任务状态管理（观察者模式）+ 线程池执行器 + WebSocket 进度推送 + 按用户队列调度 + MinIO 临时文件对账清理。文档处理与 OCR 任务的 owner 校验、进度缓存与留存策略保持一致。

---

## 前端页面与权限

前端 Monorepo 主应用为 [`frontend/apps/chat`](frontend/apps/chat)。登录后可使用核心业务页面；侧边栏按角色控制入口。

| 页面 | 路径 | 访问说明 |
|------|------|----------|
| 登录页 | `/login` | 公开；已登录访问将跳转对话页 |
| 注册页 | `/register` | 公开；新用户默认注册为普通用户 |
| AI 对话页 | `/chat` | 需登录；可进行知识库对话与文档上传 |
| 知识库管理页 | `/knowledge` | 需登录；admin / superuser 可查看与删除记录，普通用户可上传并查看自己的上传进度 |
| 用户管理页 | `/users` | 需登录且角色为 **superuser**；可管理用户角色 |

> 通用页面权限框架（`requiresPermission` 路由守卫、后端 PATCH 接口与前端 service）已保留，但当前无页面 key、默认关闭（`PAGE_PERMISSION_MANAGEMENT_ENABLED=False`），后续新增业务页面时再启用。未登录用户访问除 `/login`、`/register` 外的路由将跳转至登录页。系统包含 10 分钟无操作自动退出登录机制。

---

## 快速开始

### 前置要求

- Python 3.12+
- PostgreSQL 14+（需安装 pgvector 扩展）
- Redis 6.0+
- **MinIO**（对象存储；知识库文件、文档处理临时文件与 OCR 产物依赖桶配置，见 `.env.example`）
- Node.js 18+；**pnpm 8.15.9**（[`frontend/package.json`](frontend/package.json) 的 `packageManager` 已锁定，直接用 `corepack pnpm` 即可）

### 后端启动

```bash
# 1. 克隆仓库
git clone <your-repo-url>
cd project-nbhx

# 2. 安装后端依赖（依赖与缓存全部落在仓库内：./.venv 与 ./.cache，见 CLAUDE.md「本地环境」）
bash scripts/setup_local_env.sh

# 3. 激活环境（每个新 shell 都要 source 一次：激活 .venv 并把缓存指向 ./.cache）
source scripts/env.sh

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 PostgreSQL、Redis、MinIO 及 AI 推理服务地址

# 5. 初始化数据库（首次运行）
# 在 PostgreSQL 中执行：CREATE EXTENSION IF NOT EXISTS vector;

# 6. 启动服务
python main.py          # http://localhost:8000，文档 /api/v1/docs
```

> `requirements.txt` 里的 `torch==2.9.1+cu130`、`paddlepaddle-gpu==3.2.0` 不在 PyPI 上，需带 torch/paddle 官方索引；`scripts/setup_local_env.sh` 已处理索引源、依赖冲突与 `nvidia-nccl` 互斥（详见 [CLAUDE.md](CLAUDE.md) 的「本地环境」）。手动装时请照抄脚本里的参数。
>
> **RAG 依赖已拆分**：LangChain / LlamaIndex 那一套在 [`requirements-rag.txt`](requirements-rag.txt)，随「RAG 独立容器」部署（对外只暴露 HTTP 接口），主清单不再包含。仓库里的 RAG 代码尚未搬走，过渡期本地跑完整应用请用 `bash scripts/setup_local_env.sh --with-rag`。RAG 容器与主应用的接口边界见 [`docs/langchain-rag-container-api-contract.md`](docs/langchain-rag-container-api-contract.md)。

### 前端启动

前端使用 **pnpm workspace** 与 **Turbo**（`pnpm dev` 等价于 `turbo run dev`）。仓库在 [`frontend/package.json`](frontend/package.json) 锁定 `pnpm@8.15.9`，用 `corepack pnpm` 即可自动匹配版本；不要用 pnpm 10/12（会把 `pnpm-lock.yaml` 从 v6 升到 v9）。

```bash
cd frontend

# 安装依赖（pnpm store / cache 落在仓库内：frontend/.pnpm-store、frontend/.pnpm-cache）
corepack pnpm install --frozen-lockfile

# 配置前端环境变量
cp apps/chat/env.example apps/chat/.env

# 启动开发服务器
corepack pnpm dev
```

---

## API 访问

后端服务启动后，可通过以下地址访问：

| 服务 | 地址 |
|------|------|
| 主服务 | http://localhost:8000 |
| Swagger UI（交互式 API 文档） | http://localhost:8000/api/v1/docs |
| ReDoc（API 参考文档） | http://localhost:8000/api/v1/redoc |
| 健康检查 | http://localhost:8000/api/v1/health |

常用业务接口前缀（均挂载在 `settings.API_V1_STR` = `/api/v1` 下，前缀集中管理于 [`app/api/v1/prefixes.py`](app/api/v1/prefixes.py)，装配于 [`app/api/v1/registry.py`](app/api/v1/registry.py)）：

| 功能 | 前缀 | 说明 |
|------|------|------|
| 认证与用户 | `/api/v1/auth` | 登录、注册、当前用户、superuser 用户管理；通用页面权限 PATCH 端点保留但默认 404 |
| 文件存储 | `/api/v1/files` | 文件上传、下载、列表、检索与删除 |
| 文档任务 | `/api/v1/document-tasks` | 知识库文档处理任务与 WebSocket 进度推送 |
| 知识库 | `/api/v1/knowledge` | 知识记录列表/删除、文档上传与 Excel 数据库上传 |
| RAG 检索 | `/api/v1/retriever` | 本地知识库检索（供对话引用） |
| 上下文压缩 | `/api/v1/context-compression` | 长对话上下文压缩 |
| 对话摘要 | `/api/v1/chat-summary` | 用户画像摘要存储与查询 |
| OCR | `/api/v1/ocr` | 图片文字识别与 PDF 转图片 |

> 对话类端点（`/chat-messages`、`/conversations`、`/messages`）现在挂载在本服务 `app/api/v1/chat.py`；LangChain 实现完成前统一返回 `501 CHAT_ORCHESTRATOR_NOT_CONFIGURED`，接入时只需替换 `ChatOrchestratorPort` 实现。

> 接口人工契约见 [`docs/langchain-api-contract.md`](docs/langchain-api-contract.md)；独立 RAG 容器 / LangChain 边界见 [`docs/langchain-rag-container-api-contract.md`](docs/langchain-rag-container-api-contract.md)。

---

## 仓库结构速览

| 路径 | 说明 |
|------|------|
| [`main.py`](main.py) | FastAPI 入口、生命周期（数据库/Redis/MinIO 初始化与依赖降级、任务基础设施装配） |
| [`app/api/v1/`](app/api/v1/) | 路由层（组合根）；`registry.py` 扁平装配各业务 router，`prefixes.py` 集中前缀 |
| [`app/usecases/`](app/usecases/) | 业务用例编排（auth、knowledge、chat_summary、context_compression、document_processing 等） |
| [`app/ports/`](app/ports/) | `Protocol` 契约（`contracts/`、`outbound/`）+ 纯 DTO（`dto/`） |
| [`app/adapters/`](app/adapters/) | Port 实现，桥接 ORM / 集成 / 配置；driving（`web/`、`workers/`）与 driven（`ocr`、`doc_processing`、`knowledge`、`auth`、`chat_archive`、`ragsystem`、`monitoring` 等）同层组织 |
| [`app/domain/`](app/domain/) | 无 IO 纯函数与领域规则（knowledge、file_manager、auth 页面权限命名等）及共享异常 |
| [`app/models/orm/`](app/models/orm/) | SQLAlchemy ORM（用户/角色/权限、对话、消息、文件、知识库等） |
| [`app/core/`](app/core/) | 配置、任务管理器、执行器、WS、中间件、安全、文件对账（外层工具岛） |
| [`nginx/`](nginx/) | Nginx 流量拆分模板（业务前缀显式 → FastAPI 后端；未知 `/api/v1/*` 返回 404） |
| [`frontend/`](frontend/) | pnpm + Turbo Monorepo；业务应用在 [`frontend/apps/chat`](frontend/apps/chat) |
| [`tests/`](tests/) | 单元/回归测试（知识库上传、RAG 接入、任务并发、鉴权与分层守卫等） |
| [`scripts/`](scripts/) | 启动脚本、Nginx 渲染、分层架构 guard |
| [`docs/`](docs/) | 架构模式与任务状态文档 |

---

## 更新日志

### v0.3.0（2026-09）

- **功能下线**：移除两个历史业务模块的前后端、测试、网关路由与文档，并移除其外部数据库依赖
- **知识库**：知识库管理页新增文档与 Excel 类数据库上传、同名冲突处理、上传进度轮询
- **RAG**：检索接口按文档库 / Excel 库分家，白名单配置化
- **权限**：通用页面权限框架保留但默认关闭；用户管理页不再展示已删页面的权限开关
- **基础设施**：任务基础设施去业务化，保留文档处理与 OCR 共用能力；MinIO 对账仅处理保留前缀

### v0.2.3（2026-06）

- **文档任务**：完善 WebSocket 实时进度推送与轮询兜底
- **权限控制**：补充 admin / superuser 角色分层说明
- **API 文档**：补充认证、文档任务与 OCR 的常用接口前缀

### v0.2.1（2026-04）

- 文档：同步前端路由（注册与用户/知识库管理页及权限说明）、Monorepo（pnpm + Turbo）与 `env.example` 说明

### v0.2.0（2026-03）

- 新增聊天记录归档与用户画像摘要
- 新增 PDF 转图片与 OCR 信息提取
- 新增 WebSocket 实时任务进度推送
- 新增用户认证系统（JWT）
- 完成 Vue 3 前端应用（对话、知识库管理、用户管理等页面）

### v0.1.0（2025-12）

- RAG 知识库检索系统上线
- 多格式文档处理管线
- 文件管理与对象存储集成
