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
  domain/                quotation / file_manager / knowledge 纯领域
  ports/
    contracts/           driving 侧契约（identity / tasking / metrics / executor_async）
    dto/                 跨层 dataclass DTO + Command
    outbound/            driven 侧 Port（Protocol）
  usecases/              按业务域组织（auth / quotation / knowledge / ...）
  adapters/
    {auth,quotation,ocr,sqlserver,doc_processing,knowledge,pdm_matcher,chat_archive}/  driven
    ragsystem/           driven（RetrieverPort / ChartAnalysisPort），经 adapters/retriever.py facade 暴露
    web/                 driving（FastAPI 请求/响应 schema）
    workers/             driving 异步入口（TaskDispatchPort，报价生成 worker）
    monitoring/          driven（health / metrics）
  models/orm/            SQLAlchemy ORM 模型
frontend/apps/chat/      主前端应用；frontend/packages/components = @yamato/components 共享包
scripts/                 架构守卫 / 启动 / 环境（env.sh、setup_local_env.sh、dev.sh）/ nginx / 安全 smoke
tests/                   pytest 单元/回归（无根 conftest，直接 pytest 跑）
docs/                    架构文档（di-and-layered-architecture.md 等）
requirements*.txt        主应用锁 / RAG 栈（requirements-rag.txt）/ 测试工具 / overrides
.venv/  .cache/          项目内自包含环境（仅本机，见下节；已被 .gitignore）
```

## 本地环境（项目内自包含）

依赖与缓存**全部落在仓库目录内，不写 `$HOME`**（本机系统 python 无 pip/无项目依赖，历史上前端 store 曾落在 `~/.local/share/pnpm/store`）：

| 路径 | 内容 | 量级 |
|---|---|---|
| `.venv/` | 后端唯一解释器（含 pip），`VENV_DIR` 默认即此 | GB 级（含 CUDA wheel） |
| `.cache/uv`、`.cache/pip` | Python 下载缓存 | — |
| `.cache/{huggingface,torch,paddlex,paddle-extension,modelscope}` | 模型 / 推理运行时缓存（PaddleX 走 `PADDLE_PDX_CACHE_HOME`） | — |
| `.cache/{npm,node-gyp,corepack,turbo}` | Node 侧缓存 | — |
| `frontend/node_modules/` | 前端依赖（pnpm workspace 默认位置） | ~220M |
| `frontend/.pnpm-store`、`frontend/.pnpm-cache` | pnpm 内容寻址存储 / 元数据缓存 | — |

以上路径**全部已进 [`.gitignore`](.gitignore)（含 `.venv/`、`.cache/`、`node_modules/`、`.pnpm-store/`、`.pnpm-cache/`），不要提交**。

```bash
# 首次 / 补齐依赖（幂等）
bash scripts/setup_local_env.sh                 # 建 .venv + 装主应用依赖（不含 RAG 栈）
bash scripts/setup_local_env.sh --with-rag      # 过渡期：额外装 RAG 栈（仓库里 RAG 代码还没搬走）
bash scripts/setup_local_env.sh --with-frontend # 顺带装前端依赖
bash scripts/setup_local_env.sh --frontend-only # 只装前端

# 每个新 shell：激活 venv + 把所有缓存指向 .cache（不 source 则回落 ~/.cache）
source scripts/env.sh
```

**不想每次 source？两种方式（都不需要再手动 source）**：
1. **用 `scripts/dev.sh` 跑命令**（脚本内部自己 source，任意 shell 都能用）：
   ```bash
   bash scripts/dev.sh backend     # 起后端（8000）
   bash scripts/dev.sh frontend    # 起前端 dev server（8888）
   bash scripts/dev.sh test -q     # pytest
   bash scripts/dev.sh guard       # 分层架构守卫
   bash scripts/dev.sh py <args>   # 用项目 venv 的 python 跑任意命令
   bash scripts/dev.sh shell       # 开一个已激活环境的交互 shell
   ```
2. **自动激活**：本机 `~/.bashrc` 末尾已追加 `_yamato_autoenv` 钩子（PROMPT_COMMAND）——`cd` 进仓库自动 `source scripts/env.sh`，离开自动还原 PATH 与全部缓存变量（含 `HF_HOME` 等）。改动 `~/.bashrc` 后开新终端生效；不需要了就删掉那段。

**用 uv 管理这个环境**（本机 uv 0.12.x，装在 `~/.local/bin/uv`；仓库没有 `pyproject.toml`，所以用的是 pip 兼容模式 `uv pip ...`，不是 `uv add/lock/sync` 项目模式）：

```bash
source scripts/env.sh          # 关键：uv 认 VIRTUAL_ENV，source 后所有 uv pip 命令都作用于 ./.venv
                               #（没 source 时加 --python .venv/bin/python 显式指定）

# 看状态
uv pip list / freeze / tree    # 已装包 / 锁格式输出 / 依赖树
uv pip check                   # 依赖一致性（预期留 1 条 paddle 的 nccl 提示，见上）

# 装依赖：**只增不减**，不会卸载清单外的包
uv pip install -r requirements.txt
uv pip install -r requirements-rag.txt        # 过渡期需要 RAG 时
uv pip install -r requirements-dev.txt
uv pip uninstall <包名>

# 精确同步：等价 pip-sync，**会删除未列出的包**；且不做依赖展开，
# 传入的文件必须各自是完整闭包（三个文件都已满足）
uv pip sync requirements.txt requirements-rag.txt requirements-dev.txt

# 只验证能否整体求解（不安装，改依赖后必跑）
uv pip compile requirements.txt --index-url https://pypi.org/simple \
  --extra-index-url https://download.pytorch.org/whl/cu130 \
  --extra-index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/ \
  --index-strategy unsafe-best-match

# 重建 / 另建一个干净环境（例如验证「RAG 拆分后的主镜像」而不动现有 .venv）
uv venv --python 3.12 --seed .venv
uv venv --python 3.12 .cache/lean-venv

# 缓存（env.sh 已把 UV_CACHE_DIR 指到 ./.cache/uv）
uv cache dir / size / prune
```

⚠️ **`install` 与 `sync` 的差别是这里最大的坑**：`uv pip install -r requirements.txt` **不会**卸载「已装但不在清单里」的包——所以把 RAG 移出 `requirements.txt` 后，现有 `.venv` 里的 RAG 包**依然在**（应用照常能启动）；只有 `uv pip sync` 或重建 venv 才会得到精简环境，那时 RAG 代码未搬走的应用会起不来。

- **额外索引源**：`requirements.txt` 里 `torch==2.9.1+cu130`、`paddlepaddle-gpu==3.2.0` **不在 PyPI**，必须带 torch/paddle 官方索引。另外这两个索引里也有 `fastapi` 等同名包（paddle 索引尤其杂），所以必须加 `--index-strategy unsafe-best-match`，否则 uv 的「命中即锁定首个索引」会把 `fastapi==0.116.1` 判成无解。`scripts/env.sh` 已定义 `TORCH_INDEX` / `PADDLE_INDEX` 并导出 `PIP_EXTRA_INDEX_URL`。
- **`requirements.txt` 是可整体求解的完整锁（2026-09 修好）**：直接 `uv pip install -r requirements.txt` 即可，**不要再用 `--no-deps` 掩盖冲突**。改完务必验证：
  ```bash
  uv pip compile requirements.txt --index-url https://pypi.org/simple \
    --extra-index-url https://download.pytorch.org/whl/cu130 \
    --extra-index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/ \
    --index-strategy unsafe-best-match        # 必须能通过，且不额外多出未锁定的包
  ```
  本次修掉的 3 处矛盾锁定：

  | 原锁定 | 冲突来源 | 现在 |
  |---|---|---|
  | `pycryptodome==3.21.0` | `miniopy-async==1.23.5` 要求 `>=3.22.0,<4` | `3.23.0` |
  | `argon2-cffi==21.3.0` | 同上要求 `>=23.1.0,<24` | `23.1.0` |
  | `langchain-openai==0.3.12` | 它要求 `openai<2`，与锁定的 `openai==2.8.1` 冲突 | `langchain-openai==0.3.34`（允许 `openai<3`，且兼容 `langchain-core==0.3.77`） |

  同时补锁了漏掉的传递依赖：`aiohttp-retry`、`nltk`、`aiosqlite`、`banks`、`xlsxwriter`、`requests-toolbelt`(langsmith 运行时就要)、`email-validator`+`dnspython`（pydantic `EmailStr` 用，**`uv pip check` 查不出来，只有 `import main` 时才会炸**）。
- **⚠️ 绝不能装 `nvidia-nccl-cu12`**：`paddlepaddle-gpu` 的元数据要求它，但它与 torch 需要的 `nvidia-nccl-cu13` **装的是同一个文件** `nvidia/nccl/lib/libnccl.so.2`——cu12 覆盖 cu13 后 `import torch` 直接崩：`libtorch_cuda.so: undefined symbol: ncclCommWindowDeregister`。已用 [`requirements-overrides.txt`](requirements-overrides.txt) 将其排除（`setup_local_env.sh` 带 `--overrides`）。代价：`uv pip check` 会留 1 条 paddle 的 incompatibility，属**预期**。paddle 只在多卡分布式才用 NCCL，单卡 OCR 推理不受影响。
- **RAG 栈已整体拆出主清单（2026-09）**：`langchain*`、`llama-index*`、`llama-cloud*`、`llama-parse`、`langsmith`、`openai`、`tiktoken`、`pgvector`、`nltk`、`banks`、`aiosqlite`、`requests-toolbelt` 等 **48 个包**移到 [`requirements-rag.txt`](requirements-rag.txt)（该栈的**完整独立闭包，96 个包**，可单独 `uv pip compile` 通过），随「RAG 独立容器」部署、对外只暴露 HTTP 接口；主清单从 228 → **180 包**。
  - ⚠️ **仓库里的 RAG 代码还没搬走**：`main.py`（第 33 行）与 `app/api/v1/registry.py` 仍会 `import langchain/llama_index`，所以在纯主清单环境下应用起不来。
  - 过渡期本地开发：`bash scripts/setup_local_env.sh --with-rag`（默认不装 RAG 栈）。等 RAG 调用改成 HTTP 客户端后即可去掉该开关。
  - 已用「导入拦截器」模拟验证过：**非 RAG 模块在无 RAG 栈时全部可正常 import**（`app.core.*`、`app.models.orm`、`ocr`、`sqlserver`、`quotation`、`knowledge`、`auth`、`monitoring`）。
  - 搬 RAG 时注意这几个**隐藏依赖**（元数据没声明、代码里才 import，容易被漏掉）：`psycopg2-binary`（`app/core/database.py` 的同步 engine 用，**留在主清单**）、`asyncpg`（`postgresql+asyncpg://` URL 用，主清单）、`greenlet`（SQLAlchemy async 需要）、`beautifulsoup4`/`soupsieve`（`readability`/`html_text` 运行时需要，主清单）。
- **测试工具在 [`requirements-dev.txt`](requirements-dev.txt)**（`pytest==9.1.1` + `pytest-asyncio==1.4.0`，与 `.gitlab-ci.yml` 的 pytest job 对齐），`setup_local_env.sh` 会自动装。当前 `pytest -q` = **218 passed**。
- **无 GPU 机器**：`TORCH_INDEX=https://download.pytorch.org/whl/cpu bash scripts/setup_local_env.sh`（省约 7GB CUDA wheel）。本机有 RTX 5090（驱动 580 / CUDA 13.0），装的是 cu130 版本，用 `python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"` 验证。
- **解释器只用 `.venv`**：`scripts/start_backend.sh` 按 `VENV_DIR` → `~/桌面/yamatoenv` → `~/yamatoenv` → `<repo>/.venv` → `<repo>/venv` 顺序解析，本仓库命中 `.venv`。

**本地 `.env`（development，已生成，gitignored）**：`ENVIRONMENT=development`、`DEBUG=True`；Postgres/Redis/MinIO 指向本机共享 infra（`/data/infra`），`SECRET_KEY`/`INTERNAL_API_KEY`/`CHAT_API_KEY` 为随机值，种子超管 `superuser` / `<seed-superuser-password>`（邮箱 `superuser@nbhx.com`；由 `BOOTSTRAP_SUPERUSER_*` 在启动时写入，**已存在同名用户则跳过**——改账号要先删库里的旧行再重启）。
- **PostgreSQL 走专用 pgvector 容器**（不是那个 `postgres:16-alpine`）：`/data/infra` 里的 `pgvector-rag` 服务 = `pgvector/pgvector:pg16`，**宿主端口 5433**，用户 `root`，库 `yamato_dev`（已建 + 已 `CREATE EXTENSION vector`，扩展版本 0.8.6，向量运算实测可用）。`.env` 里 `POSTGRES_PORT=5433` / `POSTGRES_USER=root`。改动库/扩展后确认：`select extname from pg_extension where extname='vector'`。
- **SQL Server（U8/PDM）**：启动时的连通性检查**已删除**（连同 `app/adapters/sqlserver/connectivity.py`），不再打 `[warning] U8/PDM SQLServer 连接失败`；报价/PDM 相关接口被调用时才会真正连库。
- **AI 推理服务**（BGE-M3 / Reranker / OCR / LLM，`.env` 里指向 `localhost:80`）：本机没有，RAG/OCR 调用会失败。

**MinIO 对账只扫 `temp/` 与 `images/`**：`form_pic/` 前缀与 closing_form 遗留登记逻辑（`_LEGACY_*`）已随 closing_form 下线一并删除，不再输出 `跳过 data_doc_collection_1 图片登记` 告警；`form_pic/` 下历史对象现在**不被扫描**（不会被删，也不再纳入对账）。

**pnpm 两个坑（已实测，别再踩）**：
1. **`.pnpmrc` 不生效**：pnpm 8 与 pnpm 12 都不读它，原先写在里面的 `shamefully-hoist=true` 从未起作用（改到 `.npmrc` 后 `node_modules/.modules.yaml` 才出现 `hoistPattern: '*'`）。该文件已删除，配置写在 [`frontend/.npmrc`](frontend/.npmrc)（pnpm ≤10）与 [`frontend/pnpm-workspace.yaml`](frontend/pnpm-workspace.yaml)（pnpm 11+，camelCase），两处保持一致。
2. **`package.json` 的 `packageManager` 已从 `pnpm@8.0.0` 升到 `pnpm@8.15.9`**：8.0.0 有 `ERR_INVALID_THIS` bug（node 20/24 都复现，8.6+ 才修）根本装不了包。直接用 `cd frontend && corepack pnpm install --frozen-lockfile` 即可（corepack 按 `packageManager` 自动选版本，缓存固定在 `.cache/corepack`）。**不要用本机全局 pnpm 10/12**：`pnpm-lock.yaml` 是 `lockfileVersion: '6.0'`，它们会报 `ERR_PNPM_LOCKFILE_BREAKING_CHANGE`，加 `--force` 则把锁文件升到 v9。

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

**9. 前端用 `corepack pnpm`（`package.json` 已锁 `pnpm@8.15.9`，保持 lockfile v6），配置只写 `.npmrc` + `pnpm-workspace.yaml`，不要写 `.pnpmrc`（pnpm 8/12 都不读）。** 本机全局 pnpm 10/12 读不了 v6 lockfile，`--force` 会把它升到 v9。详见「本地环境」。

## 运行与验证

```bash
# 每个新 shell 先激活项目内环境（首次先跑 scripts/setup_local_env.sh）
source scripts/env.sh   # VIRTUAL_ENV=.venv，并导出 UV_CACHE_DIR / HF_HOME 等

# 后端（.env 已生成为 development；依赖 PostgreSQL+pgvector / Redis / MinIO / SQL Server）
python main.py          # http://localhost:8000，文档 /api/v1/docs

# 架构守卫（改分层后必跑）
bash scripts/check_layered_architecture.sh

# 测试（无特殊配置，直接 pytest；解释器须是 .venv 里的）
pytest

# 前端（corepack 按 package.json 的 packageManager 自动选 8.15.9）
cd frontend
corepack pnpm install --frozen-lockfile
corepack pnpm dev                       # 开发
corepack pnpm build                     # = vue-tsc && vite build（类型检查 + 构建）
corepack pnpm --filter chat type-check  # 仅类型检查
```

## 外部服务与配置

配置走 `.env`（模板见 [`.env.example`](.env.example)，由 `app/core/config.py` 的 `settings` 读取）。关键依赖：
- **PostgreSQL + pgvector**：主库 + 向量检索（RAG 集合）
- **Redis**：缓存 + 限流 + 任务状态
- **MinIO**：对象存储（报价 PDF / 临时图 / 结果 xlsx）
- **SQL Server**：U8 与 PDM 库（报价/PDM 接口被调用时才连库；启动时**不再**做连通性检查）
- **AI 推理**：BGE-M3 嵌入、Reranker、OCR（PaddleOCR/DOC），本地 LLM 走 GPU（`LOCAL_MODEL_GPU_DEVICE`）

`main.py` 的 `lifespan` 负责启动初始化（DB 表、executor、任务观察者、报价队列恢复、retention 调度、MinIO bucket、RAG 系统）与有序关闭。改启动/关闭顺序在这里。

## Git 工作流

- 默认分支 `main`（PR 目标）；日常开发在 `develop`。
- PR 会触发架构守卫 CI。
- 提交前自检：行尾 LF、`bash scripts/check_layered_architecture.sh` 通过。
