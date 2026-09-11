<div align="center">

# Yamato AI 助手平台

### 大和衡器（上海）企业内部 AI 工作台

<p align="center">
  <a href="#简介">简介</a> •
  <a href="#为大和带来的价值">为大和带来的价值</a> •
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

Yamato AI 助手平台是为<strong>大和衡器（上海）</strong>量身定制的企业内部 AI 工作台，仅服务于大和自身的业务运转，**不面向外部部署**。它把原本散落在图纸、老 ERP 系统、Excel 与员工经验里的报价与订单流程，收敛到一个对话式工作台：员工用自然语言提问、上传 PDF 图纸即可自动生成报价、填写报单，AI 在背后对接大和既有的 PDM 与 U8（用友）ERP 系统完成数据回填与校核。

平台采用 FastAPI（Python 3.12）后端 + Vue 3（pnpm / Turbo monorepo）前端，遵循 **domain ◄ ports ◄ usecases ◄ adapters ◄ api(组合根)** 的 Clean Architecture。对话编排由 **Dify** 承担（SSE 流式），本服务负责其周围的 RAG 检索、文档建库、对话摘要、上下文压缩与报价 / 报单流水线；所有业务数据落在大和自有的 PostgreSQL / Redis / MinIO 与 SQL Server 之中。

---

## 为大和带来的价值

平台不是通用工具，而是针对大和报价与订单环节的瓶颈做的流程再造。上线后的核心成效：

| 场景 | 改造前 | 改造后 |
|------|--------|--------|
| **报价生成** | 人工读图 → 查 PDM → 查 U8 BOM 与库存 → 手工拼 Excel，单份报价约 **3 天** | PDF 图纸上传后自动走 OCR → PDM → U8 全链路，**约 30 分钟**出含分类型多 Sheet 的报价 Excel |
| **老 ERP 对接** | 报价需人工反复登录 U8/PDM 逐条查询，大 BOM 展开极耗工时 | 智能工作流自动对接 U8 + PDM（SQL Server），单任务最多展开 **1500 个根件**，BOM 多层展开后子件查询规模可达 **10000+ 条** |
| **并行查询优化** | 串行逐条查 ERP，大 BOM 动辄卡死或超时 | 单任务内 `ThreadPoolExecutor` 并行展开 BOM 根节点（默认 16 路、最高 128 路），IN 列表按 1000 分批规避 SQL Server 2100 参数上限；故障隔离 + 熔断，单根失败不拖垮整单 |
| **营业订单归档** | 订单靠手工录表、散落各处，历史难查、新人无从下手 | 结构化填报 + 审批流（提交 / 审批通过 / 退回待修改），审批通过即写入知识库可语义检索，规格书图片归档 MinIO |
| **知识沉淀** | 企业文档散落各处，新人查资料靠问老人 | 文档上传即建 RAG 知识库，对话直接引用依据，员工经验不再随人流失 |

> 报价链路效果数字基于大和实际业务场景测算；机制实现见下文「报价生成」与「架构概览」。

---

## 核心能力

### AI 知识库对话

员工直接用自然语言提问，AI 基于企业内部文档给出有依据的精准回答。对话编排由 **Dify** 承担（`/chat-messages` SSE 流式端点），本服务在其周围提供检索、记忆与归档支撑：

- **本地知识库检索**：基于已上传企业文档的 RAG（BGE-M3 嵌入 + 重排序 + pgvector），由本服务 `/retriever` 提供，供对话引用依据
- **联网搜索**：在 Dify 工作流内补充外部信息
- **对话历史与会话/消息持久化**：支持重命名与分页回看
- **长上下文自动压缩**：LangChain + OpenAI 兼容 LLM，从 Dify 拉取对话变量后压缩，超长对话不丢检索与作答质量
- **用户画像摘要**：自动分析历史对话，提炼问询习惯与偏好
- **协作式取消**：客户端可随时停止当前回答流

### 智能文档处理

上传企业文档后，系统自动解析内容并建立可检索的知识库，无需人工标注或整理。

- 支持格式：PDF、Word（.docx）、Excel（.xlsx）、PowerPoint（.pptx）、HTML、纯文本
- 上传即处理，**WebSocket** 实时推送处理进度（并保留轮询兜底）
- 处理完成后文档内容立即可被 AI 对话检索引用

### 报价生成（PDF 图纸 → PDM → U8 → Excel）

侧边栏 **「报价生成」**（路由 `/files`）上传 **PDF 图纸**，走两阶段异步流水线。这是平台价值最集中的环节——把人工读图 + 逐条查 ERP 的三天流程压缩到半小时。

- **Phase1**：PDF 首页栅格化 → OCR → 关键词映射 → PDM BOM 查询，进入**等待审核**；用户勾选保留的 PARTID 后继续
- **Phase2**：U8 BOM + 库存并行查询，按类型汇总
- **直接 U8 查询**：跳过 Phase1，直接以 PARTID 列表（上限 1500）发起 U8 BOM 展开，适用于已确知零件号的场景
- 阶段产物存入 MinIO；Phase2 结束后生成 **按类型多 Sheet 的 xlsx**（`quotation-results/{task_id}/u8_by_type.xlsx`）
- 完成后可 **鉴权下载** 原始处理 PDF 与 **U8 分组 Excel**
- 全程支持协作式取消；服务重启后中断任务自动重排队

> **说明**：知识库文档的上传与处理进度在 **AI 对话页** 内完成；集合与文档管理在 **知识库管理页**（`/collection2`）。请勿与报价任务的 PDF 混淆。

### OCR 图像识别

将扫描件、拍照图片或 PDF 中的文字自动识别并提取为结构化文本。

- 支持中英文混合识别、表格结构识别
- 支持将 PDF 逐页转换为图片，便于预览和 OCR 处理

### 营业订单信息填报

专为**大和智能组合秤产品**设计的营业订单信息页面，结构化收集订单参数、规格书与原价书图片，替代手工录表。

支持填报的字段包括：成交时间、客户名称、产品类型与型号、数量、不含税单价、制造编号、合同编号、物料与称重规格（物料名称、称重规格、速度、精度、包装机类型）、机械结构参数（顶锥形式、线振形式、料层圈、供料斗、计量斗、存储斗、溜槽角度、集合斗形式等）与秤体类型。

- 普通用户提交后可查看自己的表单；管理员与超级用户可查看全部表单
- 管理员与超级用户可审批通过、退回待修改，并删除已通过或不通过记录
- 被退回的表单会进入 **待修改** 标签，原提交者修改后可重新提交
- 审批通过后自动写入知识库集合，支持后续语义检索
- 报单图片存储于 MinIO，并支持预览与归属校验删除

### 对话记录归档

平台自动分析用户历史对话，提炼问询习惯与偏好，生成**用户画像摘要**；并在长对话上下文超长时做自动压缩，保持检索与作答质量。

---

## 技术栈

| 层 | 选型 | 用途 |
|----|------|------|
| **后端框架** | FastAPI 0.116 + Uvicorn + Pydantic 2 | 异步 API、配置校验、生命周期管理 |
| **对话编排** | Dify（外部） | SSE 流式对话工作流（`/chat-messages`），由 Nginx 拆分流量 |
| **LLM 编排** | LangChain | 上下文压缩、文档切分（非对话主链路） |
| **LLM 模型** | Qwen3-8B / Qwen3.6-35B（本地 vLLM） | 关键词/意图、流式作答、上下文压缩 |
| **OCR** | PaddleOCR | PDF 图纸文字识别 |
| **向量检索** | BGE-M3 嵌入 + 重排序模型 + pgvector | RAG 检索（企业文档知识库） |
| **关系/向量库** | PostgreSQL + pgvector | 对话、消息、报价任务、RAG 集合的真相源 |
| **缓存/任务态** | Redis | 任务状态、WS 进度缓存镜像、限流 |
| **对象存储** | MinIO | PDF、OCR 产物、报价 Excel、报单图片 |
| **老 ERP 对接** | SQL Server（pymssql）+ U8/PDM | BOM 展开、库存与价格查询、零件匹配 |
| **ORM** | SQLAlchemy 2.0（async） | 会话/消息/报价任务持久化 |
| **前端** | Vue 3 + Vite + TypeScript + Vue Router + Pinia + pnpm/Turbo monorepo | 对话、报价、报单、管理页 |
| **并发** | `ThreadPoolExecutor` + 连接池 + 信号量 | U8 BOM 并行展开、跨任务/跨用户连接配额 |

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

### 对话编排（Dify + 本服务分工）

流量在 Nginx 层按路径拆分（见 [`nginx/nginx.conf.template`](nginx/nginx.conf.template)）：已知的业务前缀（`/auth`、`/closing-form`、`/quotation`、`/document-tasks`、`/retriever`、`/context-compression`、`/chat-summary` 等）反代到 FastAPI 后端；其余 `/api/v1/*` 兜底反代到 Dify（重写为 `/v1/*` 并注入 Dify App API Key），其中即包含 `/chat-messages`、`/conversations`、`/messages` 等对话端点。

- **对话主链路**：Dify 负责 SSE 流式作答与工作流编排
- **RAG 检索**：本服务 `app/adapters/ragsystem/`（BGE-M3 嵌入 + 重排序 + pgvector），经 `app/adapters/retriever.py` facade 暴露，供对话引用
- **上下文压缩**：`app/adapters/context_compressor.py`，LangChain + LLM，从 Dify 拉取对话变量后压缩
- **对话归档**：`app/adapters/chat_archive/`，经 Dify API 抽取历史问询文本，生成用户画像摘要

### 报价生成流水线

两阶段状态机：`queued` → `running`(Phase1) → `awaiting_approval` → `running`(Phase2) → `completed`（另有 `failed` / `cancelled`）。PostgreSQL 为状态真相源，Redis 为 WS 进度缓存镜像；协程式取消贯穿 Port 调用。详见 [`docs/quotation-task-and-data-flow.md`](docs/quotation-task-and-data-flow.md)、[`docs/task-state-truth.md`](docs/task-state-truth.md)。

**U8 并行查询优化**（`app/adapters/sqlserver/u8_bom.py`）：

- 单任务内 `ThreadPoolExecutor` 并行展开多个 BOM 根节点，并行度 `U8_BOM_PARALLEL_WORKERS`（默认 16、最高 128）
- IN 列表按 `_IN_CLAUSE_BATCH_SIZE = 1000` 分批，规避 SQL Server 单次 2100 参数硬上限，大 BOM 不会触发 8003 静默丢数据
- **故障隔离 + 熔断**：单根失败仅跳过该根继续其余根；连续根节点失败达上限才判定系统性故障（ERP 宕机 / 连接饱和）并抛 `U8RootFailureBreakerError` 中止任务，避免无效轮询拖垮整单
- **连接配额**：跨任务 `U8_BOM_MAX_CONCURRENT_TASKS`（30）、单用户 `U8_BOM_MAX_CONCURRENT_TASKS_PER_USER`（2）、总连接 `U8_BOM_MAX_TOTAL_CONNECTIONS`（64）三层限流
- **协程式取消**贯穿每个 Port 调用，取消即时生效

### 任务基础设施

所有异步任务共享：Redis 任务状态管理（观察者模式）+ 线程池执行器 + WebSocket 进度推送 + 按用户队列调度 + 留存策略（总量 > 100 裁剪至 ≤ 50；等待审核超 24h 清理）。

---

## 前端页面与权限

前端 Monorepo 主应用为 [`frontend/apps/chat`](frontend/apps/chat)。登录后可使用核心业务页面；侧边栏会按角色与页面权限自动隐藏无权限入口：

| 页面 | 路径 | 访问说明 |
|------|------|----------|
| 登录页 | `/login` | 公开；已登录访问将跳转对话页 |
| 注册页 | `/register` | 公开；新用户默认注册为普通用户 |
| AI 对话页 | `/chat` | 需登录；侧边栏含知识库文档上传入口 |
| 报价生成页 | `/files` | 需登录且拥有 `view_quotation` 页面权限；admin / superuser 默认可访问 |
| 营业订单信息页 | `/closing-form` | 需登录且拥有 `view_closing_form` 页面权限；admin / superuser 默认可访问 |
| 知识库管理页 | `/collection2` | 需登录且角色为 **admin** 或 **superuser** |
| 用户管理页 | `/users` | 需登录且角色为 **superuser**；可管理用户角色与页面权限 |

> 未登录用户访问除 `/login`、`/register` 外的路由将跳转至登录页。无相应角色或页面权限时将被重定向至对话页。系统包含 10 分钟无操作自动退出登录机制。

---

## 快速开始

### 前置要求

- Python 3.12+
- PostgreSQL 14+（需安装 pgvector 扩展）
- Redis 6.0+
- **MinIO**（对象存储；报价任务 PDF/临时图/结果 xlsx 等依赖桶配置，见 `.env.example`）
- **SQL Server**（**U8** 与 **PDM** 库；报价流水线与启动时的连通性检查，见 `.env.example`）
- **Dify**（对话编排；Nginx 兜底反代目标，需配置 Dify App API Key，见 [`nginx/README.md`](nginx/README.md)）
- Node.js 18+；**pnpm 8.x**（与 [`frontend/package.json`](frontend/package.json) 中 `packageManager` 一致）

### 后端启动

```bash
# 1. 克隆仓库
git clone <your-repo-url>
cd project-yamato-shanghai

# 2. 创建 Python 环境
conda create -n yamato python=3.12
conda activate yamato

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 PostgreSQL、Redis、MinIO、SQL Server（U8/PDM）、Dify 及 AI 推理服务地址

# 5. 初始化数据库（首次运行）
# 在 PostgreSQL 中执行：CREATE EXTENSION IF NOT EXISTS vector;

# 6. 启动服务
python main.py          # http://localhost:8000，文档 /api/v1/docs
```

### 前端启动

前端使用 **pnpm workspace** 与 **Turbo**（`pnpm dev` 等价于 `turbo run dev`）。建议使用与仓库一致的 **pnpm 8.x**（见 [`frontend/package.json`](frontend/package.json) 中 `packageManager`），以减少安装与脚本行为差异。

```bash
cd frontend

# 安装依赖
pnpm install

# 配置前端环境变量
cp apps/chat/env.example apps/chat/.env

# 启动开发服务器
pnpm dev
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
| 认证与用户 | `/api/v1/auth` | 登录、注册、当前用户、superuser 用户/权限管理 |
| 报价生成 | `/api/v1/quotation` | PDF 报价任务、PDM 审核、U8 查询、直接 U8 查询、结果下载 |
| 营业订单信息 | `/api/v1/closing-form` | 表单提交、列表、审批、退回修改、图片上传与知识库记录管理 |
| 文档任务 | `/api/v1/document-tasks` | 知识库文档处理任务与 WebSocket 进度推送 |
| RAG 检索 | `/api/v1/retriever` | 本地知识库检索（供对话引用） |
| 上下文压缩 | `/api/v1/context-compression` | 长对话上下文压缩 |
| 对话摘要 | `/api/v1/chat-summary` | 用户画像摘要存储与查询 |
| OCR | `/api/v1/ocr` | 图片文字识别与 PDF 转图片 |

> 对话类端点（`/chat-messages`、`/conversations`、`/messages`）由 Dify 提供，经 Nginx 兜底反代，不在本服务路由表中。

报价相关 OpenAPI 标签为 **Quotation Generation**（实现见 `app/api/v1/quotation_generation.py`）。营业订单信息接口实现见 `app/api/v1/closing_form.py`。

---

## 仓库结构速览

| 路径 | 说明 |
|------|------|
| [`main.py`](main.py) | FastAPI 入口、生命周期（报价队列恢复、SQL Server 连通性检查、依赖降级初始化） |
| [`app/api/v1/`](app/api/v1/) | 路由层（组合根）；`registry.py` 扁平装配各业务 router，`prefixes.py` 集中前缀 |
| [`app/usecases/`](app/usecases/) | 业务用例编排（auth、quotation、closing_form、chat_summary、context_compression、document_processing 等） |
| [`app/ports/`](app/ports/) | `Protocol` 契约（`contracts/`、`outbound/`）+ 纯 DTO（`dto/`） |
| [`app/adapters/`](app/adapters/) | Port 实现，桥接 ORM / 集成 / 配置；driving（`web/`、`workers/`）与 driven（`quotation`、`ocr`、`sqlserver`、`doc_processing`、`closing_form`、`auth`、`pdm_matcher`、`chat_archive`、`ragsystem`、`monitoring`）同层组织 |
| [`app/domain/`](app/domain/) | 无 IO 纯函数（quotation 关键词映射 / PDM 结果 / U8 分组 / workbook、closing_form 格式化、file_manager 命名等）与共享异常 |
| [`app/models/orm/`](app/models/orm/) | SQLAlchemy ORM（对话、消息、报价任务等） |
| [`app/core/`](app/core/) | 配置、任务管理器、执行器、WS、中间件、安全、仓储（外层工具岛） |
| [`nginx/`](nginx/) | Nginx 流量拆分模板（业务前缀 → 后端，兜底 → Dify） |
| [`frontend/`](frontend/) | pnpm + Turbo Monorepo；业务应用在 [`frontend/apps/chat`](frontend/apps/chat) |
| [`tests/`](tests/) | 单元/回归测试（含 U8 BOM 死锁重试、根失败隔离、报价 workbook、规格映射、PDM 匹配等） |
| [`scripts/`](scripts/) | 启动脚本、Nginx 渲染、分层架构 guard |
| [`docs/`](docs/) | 架构与子系统文档（分层模式、报价流水线、任务状态真相、U8 BOM 表结构等） |

---

## 更新日志

### v0.2.3（2026-06）

- **营业订单信息**：同步当前页面名称、普通用户页面权限、admin / superuser 审批流程与待修改重新提交机制
- **权限控制**：补充 `view_quotation`、`view_closing_form` 页面权限与侧边栏可见性说明
- **API 文档**：补充认证、报价、营业订单、文档任务与 OCR 的常用接口前缀

### v0.2.2（2026-04）

- **报价生成**：Phase2 完成后生成 U8 按类型多 Sheet **Excel**，上传 MinIO，并在 `result_payload` 中记录路径与建议文件名
- **API**：`GET /api/v1/quotation/tasks/{task_id}/u8-by-type-workbook` 鉴权流式下载 xlsx
- **前端**：报价页任务完成后提供 **下载 Excel**（与下载 PDF 相同鉴权与 blob 行为）

### v0.2.1（2026-04）

- 文档：同步前端路由（含 `/closing-form`、注册与用户/知识库管理页及权限说明）、Monorepo（pnpm + Turbo）与 `env.example` 说明

### v0.2.0（2026-03）

- 新增智能组合秤报单填写功能
- 新增聊天记录归档与用户画像摘要
- 新增 PDF 转图片与 OCR 信息提取
- 新增 WebSocket 实时任务进度推送
- 新增用户认证系统（JWT）
- 完成 Vue 3 前端应用（对话、报价/文件相关页、报单等）

### v0.1.0（2025-12）

- RAG 知识库检索系统上线
- 多格式文档处理管线
- 文件管理与对象存储集成

---

<div align="center">

仅供大和衡器（上海）内部使用 · Made with ❤️ by Shanghai Marinetime 331 Team

</div>
