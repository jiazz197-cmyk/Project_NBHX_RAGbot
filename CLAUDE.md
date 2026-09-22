# CLAUDE.md

本文件给 Claude Code 提供在本仓库工作时的项目上下文与约定。**先读这个，再读 [README.md](README.md)。**

## 项目概览

NBHX AI 助手平台——宁波华翔企业内部 AI 工作台。核心能力：自然语言知识库对话、文档与 Excel 数据库上传处理、RAG 检索、OCR。

技术栈：Python 3.12 + FastAPI 0.116 + SQLAlchemy(async) + LangChain；PostgreSQL 14(pgvector) + Redis + MinIO；前端 Vue 3 + pnpm workspace + Turbo。

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
  domain/                auth / file_manager / knowledge 纯领域
  ports/
    contracts/           driving 侧契约（identity / tasking / metrics / executor_async）
    dto/                 跨层 dataclass DTO + Command
    outbound/            driven 侧 Port（Protocol）
  usecases/              按业务域组织（auth / knowledge / document_processing / chat_summary / ...）
  adapters/
    {auth,ocr,doc_processing,knowledge,chat_archive}/  driven
    ragsystem/           driven（RetrieverPort / ChartAnalysisPort），经 adapters/retriever.py facade 暴露
    web/                 driving（FastAPI 请求/响应 schema）
    workers/             driving 异步入口（文档处理 / OCR 任务执行器）
    monitoring/          driven（health / metrics）
  models/orm/            SQLAlchemy ORM 模型
frontend/apps/chat/      主前端应用；frontend/packages/components = @nbhx/components 共享包
scripts/                 架构守卫 / 启动 / 环境（env.sh、setup_local_env.sh、dev.sh）/ nginx / 安全 smoke
tests/                   pytest 单元/回归（无根 conftest，直接 pytest 跑）
docker/                  开发容器：dev.Dockerfile / compose.dev.yaml / entrypoint.sh
docs/                    架构文档（di-and-layered-architecture.md、docker-dev-env.md 等）
requirements*.txt        主应用锁 / RAG 栈（requirements-rag.txt）/ 测试工具 / overrides
.venv/  .cache/          项目内自包含环境（仅本机，见下节；已被 .gitignore）
```

## 本地环境（项目内自包含）

依赖与缓存**全部落在仓库目录内，不写 `$HOME`**（本机系统 python 无 pip/无项目依赖，历史上前端 store 曾落在 `~/.local/share/pnpm/store`）：

| 路径 | 内容 | 量级 |
|---|---|---|
| `.venv/` | 后端唯一解释器（含 pip），`VENV_DIR` 默认即此 | GB 级（含 CUDA wheel） |
| `.cache/uv`、`.cache/pip` | Python 下载缓存 | — |
| `.cache/{huggingface,torch,modelscope}` | 模型 / 推理运行时缓存（paddle 系缓存已随 OCR 服务化移除） | — |
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
2. **自动激活**：本机 `~/.bashrc` 末尾已追加 `_nbhx_autoenv` 钩子（PROMPT_COMMAND）——`cd` 进仓库自动 `source scripts/env.sh`，离开自动还原 PATH 与全部缓存变量（含 `HF_HOME` 等）。改动 `~/.bashrc` 后开新终端生效；不需要了就删掉那段。

**用 uv 管理这个环境**（本机 uv 0.12.x，装在 `~/.local/bin/uv`；仓库没有 `pyproject.toml`，所以用的是 pip 兼容模式 `uv pip ...`，不是 `uv add/lock/sync` 项目模式）：

```bash
source scripts/env.sh          # 关键：uv 认 VIRTUAL_ENV，source 后所有 uv pip 命令都作用于 ./.venv
                               #（没 source 时加 --python .venv/bin/python 显式指定）

# 看状态
uv pip list / freeze / tree    # 已装包 / 锁格式输出 / 依赖树
uv pip check                   # 依赖一致性（应无 incompatibility；paddle 已移出主清单）

# 装依赖：**只增不减**，不会卸载清单外的包
uv pip install -r requirements.txt --overrides requirements-overrides.txt   # ⚠️ overrides 不能省
uv pip install -r requirements-rag.txt        # 过渡期需要 RAG 时
uv pip install -r requirements-dev.txt
uv pip uninstall <包名>

# 精确同步：等价 pip-sync，**会删除未列出的包**；且不做依赖展开，
# 传入的文件必须各自是完整闭包（三个文件都已满足）
uv pip sync requirements.txt requirements-rag.txt requirements-dev.txt

# 只验证能否整体求解（不安装，改依赖后必跑）
uv pip compile requirements.txt --index-url https://pypi.org/simple \
  --extra-index-url https://download.pytorch.org/whl/cu130 \
  --index-strategy unsafe-best-match

# 重建 / 另建一个干净环境（例如验证「RAG 拆分后的主镜像」而不动现有 .venv）
uv venv --python 3.12 --seed .venv
uv venv --python 3.12 .cache/lean-venv

# 缓存（env.sh 已把 UV_CACHE_DIR 指到 ./.cache/uv）
uv cache dir / size / prune
```

**四个 requirements 文件的分工**（实测锁定条数：主 155 / RAG 96 / dev 5 / overrides 1；OCR 服务化摘除 paddle 三件套 + 23 个 paddle 系孤儿依赖）：

| 文件 | 条数 | 角色 | 什么时候用 |
|---|---|---|---|
| `requirements.txt` | 155 | 主应用运行时唯一清单（唯一的**非 PyPI** 包只剩 `torch==2.9.1+cu130`；paddle 三件套已移除） | 本地后端环境、主应用镜像；**必须配 `--overrides`** |
| `requirements-rag.txt` | 96（其中 **48 个是 RAG 独有**） | LangChain/LlamaIndex 全套，随「RAG 独立容器」部署 | RAG 容器镜像；过渡期本地 `--with-rag` |
| `requirements-dev.txt` | 5 | `pytest` / `pytest-asyncio` / `iniconfig` / `pluggy` / `watchfiles`；**部署环境不要装** | 本地开发 + CI |
| `requirements-overrides.txt` | 1 条 | **不是清单**，是传给 `--overrides` 的冲突排除文件 | 凡装主清单就必须一起带 |

已核对过、不用重新推导的三条事实：
- **两文件共享的 48 个包（pydantic / sqlalchemy / numpy / httpx / asyncpg / psycopg2-binary …）版本锁定完全一致（逐条比对 0 处差异）** → 「先主清单后 RAG」顺序无关、可重复执行，不会互相翻版本。
- **`requirements-rag.txt` 里没有 `fastapi` / `uvicorn` / `starlette`，也没有 `torch`（paddle 全家已不在任何清单）** → ①RAG 容器要对外暴露 HTTP 得自补 `fastapi`+`uvicorn`；②RAG 镜像不含 GPU wheel，比主镜像小得多（推理走外部服务，不在容器内跑模型）。
- `requirements-dev.txt` 显式列 `iniconfig` / `pluggy`，是为了 `uv pip sync`（不展开依赖）时不误删；`packaging` / `Pygments` 已在主清单里。

⚠️ **`install` 与 `sync` 的差别是这里最大的坑**：`uv pip install -r requirements.txt` **不会**卸载「已装但不在清单里」的包——所以把 RAG 移出 `requirements.txt` 后，现有 `.venv` 里的 RAG 包**依然在**（应用照常能启动）；只有 `uv pip sync` 或重建 venv 才会得到精简环境，那时 RAG 代码未搬走的应用会起不来。

- **额外索引源**：`requirements.txt` 里唯一不在 PyPI 的包是 `torch==2.9.1+cu130`，必须带 torch 官方索引（paddle 索引已随 OCR 服务化移除）。torch 索引里也有 `fastapi` 等同名包，所以必须加 `--index-strategy unsafe-best-match`，否则 uv 的「命中即锁定首个索引」会把 `fastapi==0.116.1` 判成无解。`scripts/env.sh` 已定义 `TORCH_INDEX` 并导出 `PIP_EXTRA_INDEX_URL`。
- **`requirements.txt` 是可整体求解的完整锁（2026-09 修好）**：直接 `uv pip install -r requirements.txt` 即可，**不要再用 `--no-deps` 掩盖冲突**。改完务必验证：
  ```bash
  uv pip compile requirements.txt --index-url https://pypi.org/simple \
    --extra-index-url https://download.pytorch.org/whl/cu130 \
    --index-strategy unsafe-best-match        # 必须能通过，且不额外多出未锁定的包
  ```
  本次修掉的 3 处矛盾锁定：

  | 原锁定 | 冲突来源 | 现在 |
  |---|---|---|
  | `pycryptodome==3.21.0` | `miniopy-async==1.23.5` 要求 `>=3.22.0,<4` | `3.23.0` |
  | `argon2-cffi==21.3.0` | 同上要求 `>=23.1.0,<24` | `23.1.0` |
  | `langchain-openai==0.3.12` | 它要求 `openai<2`，与锁定的 `openai==2.8.1` 冲突 | `langchain-openai==0.3.34`（允许 `openai<3`，且兼容 `langchain-core==0.3.77`） |

  同时补锁了漏掉的传递依赖：`aiohttp-retry`、`nltk`、`aiosqlite`、`banks`、`xlsxwriter`、`requests-toolbelt`(langsmith 运行时就要)、`email-validator`+`dnspython`（pydantic `EmailStr` 用，**`uv pip check` 查不出来，只有 `import main` 时才会炸**）。
- **⚠️ 绝不能装 `nvidia-nccl-cu12`**（历史坑，防御性保留）：它与 torch 需要的 `nvidia-nccl-cu13` **装的是同一个文件** `nvidia/nccl/lib/libnccl.so.2`——cu12 覆盖 cu13 后 `import torch` 直接崩：`libtorch_cuda.so: undefined symbol: ncclCommWindowDeregister`。[`requirements-overrides.txt`](requirements-overrides.txt) 的排除规则**防御性保留**（当前闭包本就解析不出 cu12，防未来有人把 paddle 加回清单时静默复发）。paddle 系 CUDA 库（14 个 nvidia-*-cu12）与 paddle 传递依赖已随 OCR 服务化一并移出主清单。
- **RAG 栈已整体拆出主清单（2026-09）**：`langchain*`、`llama-index*`、`llama-cloud*`、`llama-parse`、`langsmith`、`openai`、`tiktoken`、`pgvector`、`nltk`、`banks`、`aiosqlite`、`requests-toolbelt` 等 **48 个包**移到 [`requirements-rag.txt`](requirements-rag.txt)（该栈的**完整独立闭包，96 个包**，可单独 `uv pip compile` 通过），随「RAG 独立容器」部署、对外只暴露 HTTP 接口；主清单从 228 → 179 → **155 包**（OCR 服务化再摘 26 条 = paddle 三件套 + 23 个孤儿；实测 `grep -cE '^[A-Za-z0-9._-]+==' requirements.txt`）。
  - ⚠️ **仓库里的 RAG 代码还没搬走**：`main.py`（第 33 行）与 `app/api/v1/registry.py` 仍会 `import langchain/llama_index`，所以在纯主清单环境下应用起不来。
  - 过渡期本地开发：`bash scripts/setup_local_env.sh --with-rag`（默认不装 RAG 栈）。等 RAG 调用改成 HTTP 客户端后即可去掉该开关。
  - 已用「导入拦截器」模拟验证过：**非 RAG 模块在无 RAG 栈时全部可正常 import**（`app.core.*`、`app.models.orm`、`ocr`、`knowledge`、`auth`、`monitoring`）。
  - 搬 RAG 时注意这几个**隐藏依赖**（元数据没声明、代码里才 import，容易被漏掉）：`psycopg2-binary`（`app/core/database.py` 的同步 engine 用，**留在主清单**）、`asyncpg`（`postgresql+asyncpg://` URL 用，主清单）、`greenlet`（SQLAlchemy async 需要）、`beautifulsoup4`/`soupsieve`（`readability`/`html_text` 运行时需要，主清单）。
- **测试工具在 [`requirements-dev.txt`](requirements-dev.txt)**（`pytest==9.1.1` + `pytest-asyncio==1.4.0`，与 `.gitlab-ci.yml` 的 pytest job 对齐），`setup_local_env.sh` 会自动装。pytest 结果以当前分支实际输出为准，历史数字不要当基线。
- **无 GPU 机器**：`TORCH_INDEX=https://download.pytorch.org/whl/cpu bash scripts/setup_local_env.sh`（省约 7GB CUDA wheel）。本机有 RTX 5090（驱动 580 / CUDA 13.0），装的是 cu130 版本，用 `python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"` 验证。
- **解释器只用 `.venv`**：`scripts/start_backend.sh` 按 `VENV_DIR` → `~/桌面/nbhxenv` → `~/nbhxenv` → `<repo>/.venv` → `<repo>/venv` 顺序解析，本仓库命中 `.venv`。

**本地 `.env`（development，已生成，gitignored）**：`ENVIRONMENT=development`、`DEBUG=True`；Postgres/Redis/MinIO 指向本机共享 infra（`/data/infra`），`SECRET_KEY`/`INTERNAL_API_KEY` 为随机值，种子超管用户名 `superuser`（口令由 `.env` 的 `BOOTSTRAP_SUPERUSER_PASSWORD` 提供，**不入库**；邮箱 `superuser@nbhx.com`；由 `BOOTSTRAP_SUPERUSER_*` 在启动时写入，**已存在同名用户则跳过**——改账号要先删库里的旧行再重启）。
- **PostgreSQL 走专用 pgvector 容器**（不是那个 `postgres:16-alpine`）：`/data/infra` 里的 `pgvector-rag` 服务 = `pgvector/pgvector:pg16`，**宿主端口 5433**，用户 `root`，库 `nbhx_dev`（已建 + 已 `CREATE EXTENSION vector`，扩展版本 0.8.6，向量运算实测可用）。`.env` 里 `POSTGRES_PORT=5433` / `POSTGRES_USER=root`。改动库/扩展后确认：`select extname from pg_extension where extname='vector'`。
- **AI 推理服务全部在外部**（BGE-M3 嵌入 / BGE-reranker-v2-m3 / 主 LLM `qwen3.8-27b` + 辅 LLM `Qwen3.6-27B`（Sophnet OpenAI 兼容网关，配置字段 `MAIN_LLM_*` / `SUB_LLM_*`，同地址同 key；辅 LLM 供 memory 压缩、用户画像生成，经 `SUB_LLM_ENABLE_THINKING=False` 默认关思考）：本机不跑这些模型，走 HTTP API。**OCR 已服务化并接入完成**（issue #9 主应用侧）：独立容器 `paddlex`（`paddlex-with-serving:gpu-cuda12.9`）暴露 `POST /ocr`（宿主 9002），主应用经 `PADDLE_OCR_ENDPOINT` 调用——`app/adapters/doc_processing/ocr_service_client.py`（HTTP 适配器），`doc_reader.PdfParser` 逐页调用（`fileType=1` + `visualize:false`，实测 300DPI A4 页 ~1.3s、响应 4.9KB）。**承重语义别顺手删**：pdfplumber 文本层短路（有文本层的 PDF 零 OCR 调用）、失败逐页降级（WARNING + 该页空文本，任务继续）、endpoint 未配置 = OCR 整体禁用。**不要整本 PDF 直传**（`fileType=0` 实测 12 页丢 2 页，且有文本层的 PDF 会被白吃整本 OCR）。宿主跑后端用 `localhost:9002`，dev 容器里由 `docker/compose.dev.yaml` 覆盖为 `host.docker.internal:9002`。**`TagGenerator` 已服务化并接入完成**（issue #10 主应用侧）：独立容器 `tagenerator:latest` 暴露 `POST /v1/tags`（宿主 8004），主应用经 `TAGGER_ENDPOINT` 调用——`app/ports/outbound/tag_generator.py`（Port）+ `app/adapters/doc_processing/tagger_client.py`（HTTP 适配器，含探活/重试/熔断），`text_splitter.TagGenerator` 只是薄壳，失败降级为本地 `_simple_tags`；进程内**不再** import torch/keybert，也无本地模型池。宿主跑后端用 `localhost:8004`，dev 容器里由 `docker/compose.dev.yaml` 覆盖为 `host.docker.internal:8004`（两个容器不在同一 docker 网络）。**BGE-M3 嵌入客户端已收敛到 `llama_index.embeddings.openai.OpenAIEmbedding`**（issue #35，含 #28）：`embedding_store.BGEM3EmbeddingWrapper` 是薄子类，传输/批量/响应解析走 openai SDK，入库按 `BGE_M3_BATCH_SIZE`（默认 64）一次 HTTP 带一批；空文本与 NaN→零向量、整批失败逐条回退、重试日志、`probe(timeout_sec)` 四条语义是承重的，别顺手删。⚠️ 依赖瘦身（从 `requirements.txt` 摘掉 torch/sentence-transformers/keybert）**尚未做**，但**进程内已无强制 torch 依赖**：issue #10 清掉 `text_splitter`、issue #35 清掉 `embedding_store` 的顶层 `import torch`，`main.py` 只在 try/except 里可选 import（打印 CUDA 状态）。实测拦截 torch 后 `import main` 正常。**paddle 已全部移出主应用**（issue #9，2026-09 完成）：`requirements.txt` 无 paddle 三件套与 23 个 paddle 系孤儿依赖（14 个 nvidia-*-cu12 + opencv-contrib/shapely/pyclipper/imagesize 等 9 个 Python 包），拦截 paddle 后 `import main` 同样正常；dev 镜像已无 paddle 层。

**MinIO 对账只扫保留功能的 `temp/`、`images/` 前缀**：历史功能曾用过的前缀不再纳入对账，也不再输出相关登记告警；已删历史对象不会被主动删除。

**pnpm 两个坑（已实测，别再踩）**：
1. **`.pnpmrc` 不生效**：pnpm 8 与 pnpm 12 都不读它，原先写在里面的 `shamefully-hoist=true` 从未起作用（改到 `.npmrc` 后 `node_modules/.modules.yaml` 才出现 `hoistPattern: '*'`）。该文件已删除，配置写在 [`frontend/.npmrc`](frontend/.npmrc)（pnpm ≤10）与 [`frontend/pnpm-workspace.yaml`](frontend/pnpm-workspace.yaml)（pnpm 11+，camelCase），两处保持一致。
2. **`package.json` 的 `packageManager` 已从 `pnpm@8.0.0` 升到 `pnpm@8.15.9`**：8.0.0 有 `ERR_INVALID_THIS` bug（node 20/24 都复现，8.6+ 才修）根本装不了包。直接用 `cd frontend && corepack pnpm install --frozen-lockfile` 即可（corepack 按 `packageManager` 自动选版本，缓存固定在 `.cache/corepack`）。**不要用本机全局 pnpm 10/12**：`pnpm-lock.yaml` 是 `lockfileVersion: '6.0'`，它们会报 `ERR_PNPM_LOCKFILE_BREAKING_CHANGE`，加 `--force` 则把锁文件升到 v9。

## 容器化开发（dev 容器）

**环境来自镜像，代码来自 bind mount** → 改代码不用碰镜像，只有 `requirements*.txt` 变化才需要重建。完整说明见 [docs/docker-dev-env.md](docs/docker-dev-env.md)。

```bash
bash scripts/dev.sh docker up          # 首次：构建镜像 + 起常驻容器
bash scripts/dev.sh docker backend     # 容器里起后端（前台，uvicorn --reload）
bash scripts/dev.sh docker frontend    # 容器里起 vite
bash scripts/dev.sh docker test -q     # 容器里跑 pytest
bash scripts/dev.sh docker guard       # 容器里跑分层架构守卫
bash scripts/dev.sh docker shell       # 进容器
bash scripts/dev.sh docker build       # 改依赖后重建
bash scripts/dev.sh docker fe-setup    # 一次性：容器内装前端依赖
API_PORT=8001 WEB_PORT=8889 bash scripts/dev.sh docker up   # 每人一组端口
```

要点（都实测过，别重新推导）：

- **必须经 `dev.sh`**：它带 `-p nbhx-${USER}`，让容器名与三个数据卷按人隔离（共享服务器上多人同用一台 docker daemon，直接 `docker compose` 会互相顶掉）。
- **代码不进镜像**：`.dockerignore` 是**白名单**，build 上下文 13 GB → 几 KB。`venv` 在 `/opt/venv`（bind mount 之外，不会被宿主 `.venv` 遮蔽），而 `scripts/env.sh` 认 `VENV_DIR` → **脚本零改动**。
- **缓存是命名卷**，挂在仓库内 `/workspace/.cache`：`env.sh` 那十几个缓存变量（`UV_CACHE_DIR`/`HF_HOME`/`COREPACK_HOME`…）硬编码指向 `${PROJECT_ROOT}/.cache`，挂这里就**全部自动落到 NVMe**（`/var/lib/docker` 在 NVMe，而宿主 `/data` 是 5400rpm HDD，实测顺序读 71 MB/s vs 9.2 GB/s）。
- **entrypoint 先 chown 缓存卷、再 `setpriv` 降权**到宿主 uid —— 命名卷默认 root 属主（实测挂在 bind mount 内部时 `root:root 0755`，非 root 直接 Permission denied），不降权则容器写进仓库的文件属主会变 root。
- **PyPI 必须走镜像**：本机 `pypi.org` 实测**超时不可达**；默认 `mirrors.aliyun.com`（`--build-arg PYPI_INDEX=` 可覆盖）。Docker Hub 也不通，但 daemon 已配 `registry-mirrors`。
- **dev 容器不需要 GPU**：OCR（issue #9）与 TagGenerator（issue #10）均已服务化，镜像已无 paddle 层（2026-09）；torch 尚未瘦身，瘦完估算还能再降。
- 容器跑通后宿主 `.venv` + `.cache` 可删，但**必须两个一起删**（硬链接关系，见 docker-dev-env.md §8），每人约回收 12–13 GB。
- **配置改哪里**：应用配置（DB/密钥/限流…）改仓库根 **`.env`**；容器编排（端口/挂载/容器内覆盖的地址）改 **`docker/compose.dev.yaml`**；只跟你有关的运行时参数（宿主端口、registry 镜像名）写 **`.env.dev`**（gitignored，示例 `.env.dev.example`）。优先级与实测见 [docs/docker-dev-env.md](docs/docker-dev-env.md) §5.5。
- **依赖变更后必须重建镜像**：`git pull` 只更新代码，**不会**更新环境（venv 在镜像里）。`bash scripts/dev.sh docker check` 自检指纹（镜像内 `/opt/venv/.requirements-hash`），`dev.sh docker up` 也会自动警告；修法是 `docker build && docker up`。
- **宿主端口**：容器内固定 8000/8888，宿主侧由 `${API_PORT:-8000}` / `${WEB_PORT:-8888}` 决定 —— 可一次性 `API_PORT=8001 WEB_PORT=8889 bash scripts/dev.sh docker up`，或持久化写进 `.env.dev`。
- **容器名 = `nbhx-${USER}-dev-1`**（如 `nbhx-jiazhenyu-dev-1`），数据卷 `nbhx-${USER}_nbhx-*`；由 `dev.sh` 的 `-p nbhx-${USER}` 决定，保证多人同机不冲突。看自己的：`bash scripts/dev.sh docker ps`。
- **VS Code**：推荐 Remote-SSH 连宿主编辑 + `dev.sh docker ...` 跑命令（编辑与 `git commit/pull/push` 全在宿主，和以前一样）；要看容器用 Docker 面板或 `Dev Containers: Attach to Running Container` 选 `nbhx-<你>-dev-1`。**⚠️ 不要用「Reopen in Container」**：仓库里已**不再**提供 `.devcontainer/devcontainer.json`（2026-09-16 移除）——VS Code 自己起 compose 会用另一个 project 名（`docker`）并重建镜像、新建一个抢不到端口的平行容器，实测误触两次。详见 [docs/docker-dev-env.md](docs/docker-dev-env.md) §5.8。
- **GitLab Container Registry 已启用（2026-09-15）**：`http://10.80.153.12:5050`，镜像 `.../carl_jia/ragchatbot/nbhx-dev`，tag `py312-cu130`（移动）+ `py312-cu130-<requirements 哈希>`（可复现）；已实测推/拉双向可用。每台要推拉的机器需 `sudo bash scripts/enable_insecure_registry.sh`（http registry）。踩过的坑见 [docs/docker-dev-guide-admin.md](docs/docker-dev-guide-admin.md) §3.1。
- **两份操作手册**：同事用 [docs/docker-dev-guide-colleague.md](docs/docker-dev-guide-colleague.md)（前置 / Day 0 / 日常命令 / 配置改哪里 / FAQ / 端口与禁忌）；管理员用 [docs/docker-dev-guide-admin.md](docs/docker-dev-guide-admin.md)（**改 requirements 后更新镜像的完整流程** / registry 运维 / 事故处置 / 回收）。

## 关键约定（非显而易见，务必遵守）

**1. 行尾只用 LF，不要提交 CRLF。** 仓库主体是 LF；混入 CRLF 会让 diff 整文件重写、`git blame` 报废、必然冲突。Windows 侧提交前先 `git config core.autocrlf input`，新文件一律 LF。（治本：加 `.gitattributes` 写 `* text=auto eol=lf`。）

**2. 业务异常用 `app.core.exceptions` 的 `APIException` 子类，不要抛 `ValueError` / 裸 `Exception`。** `main.py` 注册了 `@app.exception_handler(APIException)`，子类自带 `status_code` 自动映射 HTTP：`NotFoundError`=404、`PermissionDeniedError`=403、`AuthenticationError`=401、`ValidationError`=422、`ExternalServiceError`=502。抛 `ValueError` 会变成 500 且绕过统一错误格式。

**3. 日志命名必须落在 `app.*` 路由表里，否则静默丢失。** 用 `from app.core.logging import get_logger; logger = get_logger("auth.login")`（内部即 `logging.getLogger("app.auth.login")`）。若直接用 stdlib `logging.getLogger`，名字必须带 `app.` 前缀（如 `"app.knowledge.upload"`）。裸名（`"knowledge.upload"`）不在 dictConfig 路由表，日志会丢。

**4. Port / UseCase / Adapter / Router 角色**（详见 [docs/di-and-layered-architecture.md](docs/di-and-layered-architecture.md)）：
- Port = `Protocol`（`app/ports/outbound/`）或 DTO/Command（`app/ports/dto/`），只声明契约。
- UseCase 构造函数收 Port 类型，**不 import Adapter**。
- Adapter 实现 Port，是唯一碰 ORM/SDK 的地方。
- **组合根默认在 `app/api/v1/<域>.py` 的 router 模块**：在那里 `new` Adapter、注入给 UseCase、用 `Depends` 做请求级注入。UseCase 不要自己 `import` Adapter。

**5. SQLAlchemy Adapter 是「每方法一 session」模式**：`async with AsyncSessionLocal() as db:` 包在每个 repository 方法里，方法内 commit。连续两个 repository 调用会跨两个 session（这是既有模式，不是 bug）。

**6. 鉴权统一走 `get_current_user`（`require_roles` 也走它）**：JWT 带 `pv` claim（密码哈希指纹），重置密码后旧 token 立即失效。通用页面权限框架保留在 `app/api/v1/auth.py` / `app/usecases/auth/users.py` / `frontend/apps/chat/src/services/auth.ts`，但默认由 `PAGE_PERMISSION_MANAGEMENT_ENABLED=False` 关闭；改鉴权时别把该框架误删。

**7. API 路由前缀集中管理**：新前缀加在 [`app/api/v1/prefixes.py`](app/api/v1/prefixes.py)，路由挂载在 [`app/api/v1/registry.py`](app/api/v1/registry.py)。所有业务路由挂在 `settings.API_V1_STR`（`/api/v1`）下。

**8. Domain 层不要 import `app.core`**：用 stdlib（`logging`、`datetime`）替代 `app.core.logging` / `app.core.time_utils`。

**9. 前端用 `corepack pnpm`（`package.json` 已锁 `pnpm@8.15.9`，保持 lockfile v6），配置只写 `.npmrc` + `pnpm-workspace.yaml`，不要写 `.pnpmrc`（pnpm 8/12 都不读）。** 本机全局 pnpm 10/12 读不了 v6 lockfile，`--force` 会把它升到 v9。详见「本地环境」。

## 运行与验证

```bash
# 每个新 shell 先激活项目内环境（首次先跑 scripts/setup_local_env.sh）
source scripts/env.sh   # VIRTUAL_ENV=.venv，并导出 UV_CACHE_DIR / HF_HOME 等

# 后端（.env 已生成为 development；依赖 PostgreSQL+pgvector / Redis / MinIO）
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
- **MinIO**：对象存储（知识库文件、文档/OCR 产物、任务临时文件）
- **AI 推理**：BGE-M3 嵌入、Reranker、OCR（paddlex 容器 `PADDLE_OCR_ENDPOINT`）、TagGenerator（`TAGGER_ENDPOINT`）——全部 HTTP 外部服务

`main.py` 的 `lifespan` 负责启动初始化（DB 表、executor、任务观察者、MinIO reconcile 调度、MinIO bucket、RAG 系统）与有序关闭。改启动/关闭顺序在这里。

## Git 工作流

- 默认分支 `main`（PR 目标）；日常开发在 `develop`。
- PR 会触发架构守卫 CI。
- 提交前自检：行尾 LF、`bash scripts/check_layered_architecture.sh` 通过。
