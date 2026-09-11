# CLAUDE.md

本文件给 Claude Code 提供在本仓库工作时的项目上下文与约定。**先读这个，再读 [README.md](README.md)。**

## 项目概览

Yamato AI 助手平台——大和衡器（上海）企业内部 AI 工作台。核心能力：自然语言知识库对话、文档处理、营业订单填报、OCR、PDF 报价生成流水线。

技术栈：Python 3.12 + FastAPI 0.116 + SQLAlchemy(async) + LangChain；PostgreSQL 14(pgvector) + Redis + MinIO + SQL Server(U8/PDM)；前端 Vue 3 + pnpm workspace + Turbo。

## 架构：Clean Architecture（强制）

依赖方向（向内为尊）：`domain ◄ ports ◄ usecases ◄ adapters ◄ api(组合根) + main.py`

| 层 | 路径 | 职责 | 可依赖 |
|---|---|---|---|
| domain | `app/domain/` | 纯领域实体/值对象/规则，无 IO | 仅 stdlib + 自身 |
| ports | `app/ports/{contracts,dto,outbound}/` | `Protocol` 契约 + DTO/dataclass，无实现 | 仅 stdlib + 自身 |
| usecases | `app/usecases/` | 业务编排，构造函数注入 Port | ports + domain（+ `app.core` 工具，本轮豁免） |
| adapters | `app/adapters/` | 实现 Port，翻译外部世界 | 任意（含 ORM/SDK/框架） |
| api / main.py | `app/api/v1/`、`main.py` | 组合根：装配 Adapter+UseCase；HTTP 薄边界 | 任意 |

**架构守卫是事实来源**：[`scripts/check_layered_architecture.sh`](scripts/check_layered_architecture.sh)（8 条规则，CI 见 `.github/workflows/layered-architecture-guard.yml`，每个 PR 跑）。改分层相关代码前先读这个脚本，别凭记忆。要点：
- `usecases` 禁 import `app.{adapters,infrastructure,ragsystem,workers,api}`
- `ports` 禁 import `app.{adapters,usecases,infrastructure,ragsystem,workers,api}`（必须纯 Protocol/DTO）
- `domain` 禁 import 上述外层
- `app.core`、`app.models` **本轮 deferred**：作外层工具岛，未纳入内层禁列。所以 usecase `from app.core... import` 目前放行——但不要新增对 `app.core.security` 里涉及密钥/签发的直接依赖（如 `create_access_token`），走 Port 注入。

历史折叠：`ragsystem / infrastructure / workers / schemas` 已物理折叠进 `app/adapters/`，禁止再 `import app.ragsystem` 等裸顶层包。

## 目录布局

```
main.py                  FastAPI 入口 + lifespan（组合根 + 生命周期）
app/
  api/v1/                路由（薄边界）；registry.py 装配，prefixes.py 集中前缀
  core/                  配置/DB/缓存/安全/中间件/任务管理（工具岛，deferred）
  domain/                quotation / closing_form / file_manager 纯领域
  ports/
    contracts/           driving 侧契约（identity / tasking / metrics / executor_async）
    dto/                 跨层 dataclass DTO + Command
    outbound/            driven 侧 Port（Protocol）
  usecases/              按业务域组织（auth / quotation / closing_form / ...）
  adapters/
    {auth,quotation,ocr,sqlserver,doc_processing,closing_form,pdm_matcher,chat_archive}/  driven
    ragsystem/           driven（RetrieverPort / ChartAnalysisPort），经 adapters/retriever.py facade 暴露
    web/                 driving（FastAPI 请求/响应 schema）
    workers/             driving 异步入口（TaskDispatchPort，报价生成 worker）
    monitoring/          driven（health / metrics）
  models/orm/            SQLAlchemy ORM 模型
frontend/apps/chat/      主前端应用；frontend/packages/components = @yamato/components 共享包
scripts/                 架构守卫 / 启动 / nginx / 安全 smoke
tests/                   pytest 单元/回归（无根 conftest，直接 pytest 跑）
docs/                    架构文档（di-and-layered-architecture.md 等）
```

## 关键约定（非显而易见，务必遵守）

**1. 行尾只用 LF，不要提交 CRLF。** 仓库主体是 LF；混入 CRLF 会让 diff 整文件重写、`git blame` 报废、必然冲突。Windows 侧提交前先 `git config core.autocrlf input`，新文件一律 LF。（治本：加 `.gitattributes` 写 `* text=auto eol=lf`。）

**2. 业务异常用 `app.core.exceptions` 的 `APIException` 子类，不要抛 `ValueError` / 裸 `Exception`。** `main.py` 注册了 `@app.exception_handler(APIException)`，子类自带 `status_code` 自动映射 HTTP：`NotFoundError`=404、`PermissionDeniedError`=403、`AuthenticationError`=401、`ValidationError`=422、`ExternalServiceError`=502。抛 `ValueError` 会变成 500 且绕过统一错误格式。

**3. 日志命名必须落在 `app.*` 路由表里，否则静默丢失。** 用 `from app.core.logging import get_logger; logger = get_logger("auth.login")`（内部即 `logging.getLogger("app.auth.login")`）。若直接用 stdlib `logging.getLogger`，名字必须带 `app.` 前缀（如 `"app.u8_grouping"`）。裸名（`"u8_grouping"`）不在 dictConfig 路由表，日志会丢。

**4. Port / UseCase / Adapter / Router 角色**（详见 [docs/di-and-layered-architecture.md](docs/di-and-layered-architecture.md)）：
- Port = `Protocol`（`app/ports/outbound/`）或 DTO/Command（`app/ports/dto/`），只声明契约。
- UseCase 构造函数收 Port 类型，**不 import Adapter**。
- Adapter 实现 Port，是唯一碰 ORM/SDK 的地方。
- **组合根默认在 `app/api/v1/<域>.py` 的 router 模块**：在那里 `new` Adapter、注入给 UseCase、用 `Depends` 做请求级注入。UseCase 不要自己 `import` Adapter。

**5. SQLAlchemy Adapter 是「每方法一 session」模式**：`async with AsyncSessionLocal() as db:` 包在每个 repository 方法里，方法内 commit。连续两个 repository 调用会跨两个 session（这是既有模式，不是 bug）。

**6. 鉴权有两条验证路径，改密码/校验相关逻辑两条都要改**：`get_current_user`（`require_roles` 走它）和 `get_current_user_detached`（`sqlserver_queries` 端点用）。JWT 带 `pv` claim（密码哈希指纹），重置密码后旧 token 立即失效；动鉴权时两条路径的 pv 校验必须同步。

**7. API 路由前缀集中管理**：新前缀加在 [`app/api/v1/prefixes.py`](app/api/v1/prefixes.py)，路由挂载在 [`app/api/v1/registry.py`](app/api/v1/registry.py)。所有业务路由挂在 `settings.API_V1_STR`（`/api/v1`）下。

**8. Domain 层不要 import `app.core`**：用 stdlib（`logging`、`datetime`）替代 `app.core.logging` / `app.core.time_utils`。

## 运行与验证

```bash
# 后端（需先配 .env，见 .env.example；依赖 PostgreSQL+pgvector / Redis / MinIO / SQL Server）
python main.py          # http://localhost:8000，文档 /api/v1/docs

# 架构守卫（改分层后必跑）
bash scripts/check_layered_architecture.sh

# 测试（无特殊配置，直接 pytest）
pytest

# 前端
cd frontend
pnpm install
pnpm dev                          # 开发
pnpm build                        # = vue-tsc && vite build（类型检查 + 构建）
pnpm --filter chat type-check     # 仅类型检查
```

## 外部服务与配置

配置走 `.env`（模板见 [`.env.example`](.env.example)，由 `app/core/config.py` 的 `settings` 读取）。关键依赖：
- **PostgreSQL + pgvector**：主库 + 向量检索（RAG 集合）
- **Redis**：缓存 + 限流 + 任务状态
- **MinIO**：对象存储（报价 PDF / 临时图 / 结果 xlsx）
- **SQL Server**：U8 与 PDM 库（报价流水线；`main.py` 启动时连通性检查，失败不阻塞启动）
- **AI 推理**：BGE-M3 嵌入、Reranker、OCR（PaddleOCR/DOC），本地 LLM 走 GPU（`LOCAL_MODEL_GPU_DEVICE`）

`main.py` 的 `lifespan` 负责启动初始化（DB 表、executor、任务观察者、SQL Server 连通性、报价队列恢复、retention 调度、MinIO bucket、RAG 系统）与有序关闭。改启动/关闭顺序在这里。

## Git 工作流

- 默认分支 `main`（PR 目标）；日常开发在 `develop`。
- PR 会触发架构守卫 CI。
- 提交前自检：行尾 LF、`bash scripts/check_layered_architecture.sh` 通过。
