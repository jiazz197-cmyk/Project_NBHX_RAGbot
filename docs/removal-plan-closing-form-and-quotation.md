# 功能裁剪与品牌替换行动清单：删除 closing_form、报价生成并切换宁波华翔（NBHX）

> 文档状态：待确认口径后执行
> 品牌素材源：项目根目录 `NBHX.png`
> 范围：下个项目不再需要的 `closing_form`（营业订单信息填报）与「报单生成」功能代码、页面、测试、文档及相关数据；同时清除“上海大和 / Yamato”相关品牌素材，统一替换为宁波华翔（NBHX）。

## 0. 口径与前提

### 0.1 “报单生成”的代码对应关系

仓库中没有字面名为“报单生成”的模块或页面。结合现有代码，本文档按以下口径整理：

- “填写 closing_form” = 前端 `/closing-form` 页面 `frontend/apps/chat/src/pages/PolicyGeneratePage.vue` 及其后端 `closing_form` 域。
- “报单生成” = 侧边栏「报价生成」页面 `/files`，
  - 前端：`frontend/apps/chat/src/pages/FileManagerPage.vue`
  - 后端：`app/api/v1/quotation_generation.py` 及 `quotation` 相关 domain / usecases / adapters / workers / ports / ORM。

如果“报单生成”不是指报价生成，需要先纠正范围，否则可能误删保留功能。

### 0.2 开始前必须确认的事项

- [ ] “报单生成”是否确认指「报价生成 / Quotation Generation」。
- [ ] 是否保留 `/api/v1/sqlserver` 的 U8 / PDM 查询能力。该能力与报价生成共享底层 SQLServer adapter，但可以作为独立查询 API 保留。
- [ ] `view_closing_form`、`view_quotation` 两个页面权限是否整套删除；下个项目是否仍需通用页面权限框架。
- [ ] 生产数据策略：只删代码，还是同时归档并删除 `data_pending`、`quotation_tasks` 等表及 MinIO 对象。
- [ ] `collection2`（知识库管理）迁移后的新 API 前缀，例如继续叫 `/collection2` 还是改为 `/knowledge/collection2`。
- [ ] 品牌替换范围：是否包括内部代码标识、CSS 变量、localStorage key、MinIO bucket、systemd 服务名等；还是只替换 UI/文档中可见素材。
- [ ] 新品牌展示名称确认：中文名“宁波华翔”、简称“NBHX”；根目录 `NBHX.png` 是否作为唯一 Logo 源。

### 0.3 当前目录说明

当前项目目录不是 Git 仓库（没有 `.git`），动手前应先整包备份或初始化版本管理，避免不可逆修改。

## 1. 先拆共享能力（必须“先拆后删”）

以下模块同时服务待删功能与保留功能，必须先解耦，不能随目录直接删除。

### 1.1 `closing_form` 与知识库管理 `collection2` 耦合

现状：

- `app/api/v1/closing_form.py` 内包含 `/collection2/list`、`/collection2/{record_id}`。
- `app/adapters/web/endpoints/closing_form.py` 内包含 `Collection2Record`、`Collection2ListResponse`。
- `app/usecases/closing_form/operations.py` 内包含 `ListCollection2UseCase`、`DeleteCollection2RecordUseCase`。
- `app/adapters/closing_form/persistence.py` 内包含 collection2 的查询/存在性检查/删除方法。
- `app/adapters/closing_form/constants.py` 内包含 `COLLECTION2_TABLE = "data_doc_collection_2"`。
- `frontend/apps/chat/src/pages/Collection2ManagePage.vue` 通过 `services/closing_form.ts` 调用 collection2 接口。

行动项：

- [ ] 后端拆出独立 collection2 / knowledge 模块，例如 `app/api/v1/collection2.py`。
- [ ] 将 collection2 的 Pydantic schema、usecase、port、persistence 方法迁移到独立模块。
- [ ] `COLLECTION2_TABLE` 常量迁移到新模块，不再依赖 `app/adapters/closing_form/constants.py`。
- [ ] 前端新增 `frontend/apps/chat/src/services/collection2.ts`。
- [ ] 更新 `Collection2ManagePage.vue` 的 import，类型名从 `ClosingFormRecord` 改为 `Collection2Record` 等中性命名。
- [ ] 更新 `frontend/apps/chat/vite.config.ts` 与 `nginx/nginx.conf.template` 中 collection2 的代理路径（如前缀变化）。
- [ ] 保留 `data_doc_collection_2` 表，不随 closing_form 删除。

### 1.2 `/sqlserver` 查询与 quotation 域耦合

现状：

- `app/adapters/sqlserver_queries.py` 依赖：
  - `app/domain/quotation/keyword_mapping.py`
  - `app/domain/quotation/keyword_normalizer.py`
- 这两个文件是保留的 `/api/v1/sqlserver` 查询能力所需，不能跟随 quotation 域删除。

行动项：

- [ ] 将 `keyword_mapping.py`、`keyword_normalizer.py` 迁移到中性目录，例如 `app/domain/pdm_query/` 或 `app/domain/sqlserver/`。
- [ ] 更新 `app/adapters/sqlserver_queries.py` 的 import。
- [ ] 更新相关测试的 import：`tests/gen_single_sql.py`、`tests/test_pdm_sql.py` 等。
- [ ] 后续删除 `app/domain/quotation/` 中除这两个文件外的所有文件。
- [ ] 保留所有 `/sqlserver` 相关实现：
  - `app/api/v1/sqlserver_queries.py`
  - `app/adapters/sqlserver_queries.py`
  - `app/adapters/sqlserver/*`
  - `app/adapters/pdm_matcher/*`
  - `app/ports/dto/sqlserver_queries.py`
  - `app/ports/outbound/sqlserver_queries.py`
  - `app/usecases/sqlserver_queries/*`
  - `app/adapters/web/sqlserver.py`
  - 配置项：`U8_SQLSERVER_*`、`PDM_SQLSERVER_*`、`SQLSERVER_QUERY_*`、`U8_BOM_*`、`EXECUTOR_MAX_WORKERS`

### 1.3 任务基础设施与报价流水线耦合

以下为共享模块，不能整个删除：

- `app/core/task_manager.py`
- `app/core/websocket_task_manager.py`
- `app/core/observer.py`
- `app/core/observers.py`
- `app/adapters/tasking.py`
- `app/core/minio_reconcile.py`
- `app/adapters/workers/__init__.py`

行动项：

- [ ] `app/core/task_owner_registry.py`：删除 `_QuotationOwnerLookup` 及 `QuotationTask` import，保留 doc / OCR 任务的 owner 查找。
- [ ] `app/ports/contracts/tasking.py`：删除 `TaskDispatchPort`（仅报价使用），保留 `TaskStatePort`、`TaskExecutionPort`。
- [ ] `app/core/minio_reconcile.py`：删除 `QuotationTask`、`data_pending`、`form_pic/` 相关注册与前缀；保留 `FileResource`、`temp/`、`images/`。
- [ ] `app/core/retention_scheduler.py`：删除报价任务留存逻辑，保留/重命名为 MinIO orphan reconcile 调度器。
- [ ] `main.py`：删除报价队列恢复、报价 dispatch loop 注册与清理；保留任务管理器、观察者、执行器、MinIO reconcile 调度。
- [ ] 注意共享状态：`data_doc_collection_1` 是 RAG 知识库向量表，closing_form 审批写入的是这张表，**禁止 drop/truncate**。

## 2. 删除 closing_form：后端

- [ ] 删除 `app/api/v1/closing_form.py`。
- [ ] 删除 `app/adapters/web/endpoints/closing_form.py`。
- [ ] 删除整个 `app/adapters/closing_form/`。
- [ ] 删除整个 `app/usecases/closing_form/`。
- [ ] 删除整个 `app/domain/closing_form/`。
- [ ] 删除 `app/models/orm/closing_form.py`（`PendingForm`）。
- [ ] 删除 `app/ports/dto/closing_form.py`。
- [ ] 删除 `app/ports/outbound/closing_form.py`。
- [ ] 更新 `app/api/v1/registry.py`：移除 `closing_form` import 与 mount。
- [ ] 更新 `app/api/v1/prefixes.py`：删除 `CLOSING_FORM`。
- [ ] 更新 `app/api/v1/tags.py`：删除 `CLOSING_FORM` 常量与 OpenAPI tag 元数据。
- [ ] 更新 `app/core/config.py`：删除 `CLOSING_FORM_IMAGE_PREFIX`。
- [ ] 更新 `app/core/logging.py`：删除 `closing_form` 日志文件与 logger route。
- [ ] 更新 `app/core/database.py`：
  - [ ] 删除 `PendingForm` 的 import / create_all。
  - [ ] 删除 `data_pending` 的 `status`、`image_url_1`、`image_url_2`、`rejected -> pending_revision` 迁移逻辑。
- [ ] 更新 `app/adapters/doc_processing/__init__.py`、`app/adapters/ocr/__init__.py` 中提及 closing-form adapter 的注释。

## 3. 删除 closing_form：前端

- [ ] 删除 `frontend/apps/chat/src/pages/PolicyGeneratePage.vue`。
- [ ] 删除 `frontend/apps/chat/src/services/closing_form.ts`（collection2 函数先迁出）。
- [ ] 更新 `frontend/apps/chat/src/router/index.ts`：删除 `/closing-form` 路由。
- [ ] 更新 `frontend/apps/chat/src/App.vue`：
  - [ ] 删除“营业订单信息”侧边栏入口。
  - [ ] 删除 `showClosingForm` 计算属性及权限判断。
- [ ] 更新 `frontend/apps/chat/src/services/auth.ts`：从 `UserPagePermissions` 删除 `view_closing_form`。
- [ ] 更新 `frontend/apps/chat/src/pages/UserManagePage.vue`：删除“营业订单”权限开关及相关逻辑。
- [ ] 更新 `frontend/apps/chat/vite.config.ts`：删除 `/closing-form` 代理。
- [ ] MinIO 中 `form_pic/` 旧图片按数据策略清理；清理完成后再把 `form_pic/` 从 reconcile 前缀移除。

## 4. 删除报价生成：后端

- [ ] 删除 `app/api/v1/quotation_generation.py`。
- [ ] 删除整个 `app/adapters/quotation/`。
- [ ] 删除整个 `app/adapters/workers/quotation_generation/`。
- [ ] 删除 `app/adapters/workers/dispatch.py`（`QuotationDispatchAdapter` 专用）。
- [ ] 删除整个 `app/usecases/quotation/`。
- [ ] 删除 `app/models/orm/quotation_task.py`。
- [ ] 删除 `app/ports/dto/quotation.py`。
- [ ] 删除 `app/ports/dto/quotation_workbook.py`。
- [ ] 删除 `app/ports/outbound/quotation.py`。
- [ ] 删除 `app/ports/outbound/quotation_workbook.py`。
- [ ] 删除 `app/domain/quotation/` 中除迁移走的 `keyword_mapping.py`、`keyword_normalizer.py` 外的文件：
  - [ ] `entities.py`
  - [ ] `exceptions.py`
  - [ ] `partid_mapping.py`
  - [ ] `pdm_result.py`
  - [ ] `results.py`
  - [ ] `summary_selection.py`
  - [ ] `u8_grouping.py`
  - [ ] `value_objects.py`
  - [ ] `workbook.py`
  - [ ] `__init__.py`
- [ ] 删除 `app/core/quotation_task_cleanup.py`。
- [ ] 更新 `app/core/task_owner_registry.py`。
- [ ] 更新 `app/core/minio_reconcile.py`。
- [ ] 更新 `app/core/retention_scheduler.py`。
- [ ] 更新 `app/core/database.py`：
  - [ ] 删除 `QuotationTask` import。
  - [ ] 删除 `quotation_tasks` 的 `owner_ip`、`display_name`、`awaiting_approval_at` 等迁移逻辑。
- [ ] 更新 `app/core/config.py`：删除：
  - [ ] `QUOTATION_MAX_RUNNING_PER_OWNER`
  - [ ] `QUOTATION_MAX_RUNNING_PER_IP`
  - [ ] `QUOTATION_RETENTION_MAX_TOTAL`
  - [ ] `QUOTATION_RETENTION_TARGET`
  - [ ] `QUOTATION_RETENTION_INTERVAL_SEC`
  - [ ] `QUOTATION_AWAITING_APPROVAL_TTL_HOURS`
  - [ ] `QUOTATION_RUNNING_TIMEOUT_SEC`
- [ ] 更新 `app/core/logging.py`：删除 quotation 相关日志文件与 logger route。
- [ ] 更新 `app/core/circuit_breaker.py`、`app/core/storage.py`、`app/core/task_manager.py` 中报价相关注释。
- [ ] 更新 `main.py`：
  - [ ] 删除 `_startup_resume_quotation_services()` 及调用。
  - [ ] 删除 `set_quotation_dispatch_loop(...)` 注册与清理。
  - [ ] 更新“报价任务 retention 调度”相关启动/关闭日志。
  - [ ] 如 `or_` import 不再使用，同步删除。
- [ ] 更新 `app/api/v1/registry.py`：移除 `quotation_generation` import 与 mount。
- [ ] 更新 `app/api/v1/prefixes.py`：删除 `QUOTATION`。
- [ ] 更新 `app/api/v1/tags.py`：删除 `QUOTATION_GENERATION` 与 OpenAPI tag 元数据。

## 5. 删除报价生成：前端

- [ ] 删除 `frontend/apps/chat/src/pages/FileManagerPage.vue`。
- [ ] 删除 `frontend/apps/chat/src/services/quotation.ts`。
- [ ] 删除 `frontend/apps/chat/src/types/quotation.ts`。
- [ ] 更新 `frontend/apps/chat/src/router/index.ts`：删除 `/files` 路由。
- [ ] 更新 `frontend/apps/chat/src/App.vue`：
  - [ ] 删除“报价生成”侧边栏入口。
  - [ ] 删除 `showQuotation` 计算属性及权限判断。
- [ ] 更新 `frontend/apps/chat/src/services/auth.ts`：从 `UserPagePermissions` 删除 `view_quotation`。
- [ ] 更新 `frontend/apps/chat/src/pages/UserManagePage.vue`：删除“报价生成”权限开关及相关逻辑。
- [ ] 更新 `frontend/apps/chat/vite.config.ts`：删除 `/quotation` 代理。
- [ ] 检查并移除仅这两个页面使用的前端依赖。

## 6. 页面权限 / RBAC 清理

两个权限 key 都只服务这两个页面，建议整套清掉；若下个项目仍需通用页面权限框架，则保留框架、只删 key。

后端：

- [ ] `app/ports/dto/auth.py`：删除 `UpdatePagePermissionsCommand` 中的 `view_closing_form`、`view_quotation`。
- [ ] `app/ports/outbound/auth.py`：删除 `update_page_permissions(...)`，或改为新页面权限契约。
- [ ] `app/usecases/auth/users.py`：删除 `UpdateUserPagePermissionsUseCase`。
- [ ] `app/api/v1/auth.py`：删除 `PATCH /auth/users/{id}/page-permissions`。
- [ ] `app/adapters/web/platform/user.py`：删除 `UserPagePermissionsUpdate`。
- [ ] `app/adapters/auth/user_repository.py`：
  - [ ] 新建用户时不再自动分配 `page_closing_form` / `page_quotation`。
  - [ ] 删除 `update_page_permissions` 实现。
- [ ] `app/core/database.py`：删除以下 RBAC 种子：
  - [ ] `view_closing_form`
  - [ ] `view_quotation`
  - [ ] `page_closing_form`
  - [ ] `page_quotation`

前端：

- [ ] `frontend/apps/chat/src/services/auth.ts`：删除 `UserPagePermissions`、`updateUserPagePermissions`。
- [ ] `frontend/apps/chat/src/pages/UserManagePage.vue`：删除“页面权限”整列及相关状态与逻辑。

数据库存量数据：

- [ ] 编写迁移脚本，删除 `role_permission` 中这两个 role 的关联。
- [ ] 删除 `user_role` 中这两个 role 的关联。
- [ ] 删除 `role` 表中两个角色。
- [ ] 删除 `permission` 表中两个权限。

## 7. 数据库与对象存储

- [ ] `quotation_tasks`：
  - [ ] 先导出/归档数据。
  - [ ] 清理任务关联的 MinIO 文件与 `file_resource` 记录。
  - [ ] 确认后再 `DROP TABLE`。
- [ ] `data_pending`：closing_form 专用表，归档后可 drop。
- [ ] `data_doc_collection_2`：保留，知识库管理页面继续使用。
- [ ] `data_doc_collection_1`：禁止 drop/truncate，它是共享 RAG 向量表。
  - [ ] 如确需清理 closing_form 审批写入的向量记录，先确认 doc_processing 写入的 metadata 结构，再按 metadata 谨慎过滤。
- [ ] MinIO：
  - [ ] `quotation-results/`、报价上传 PDF、报价临时图按归档策略处理。
  - [ ] `form_pic/` closing_form 旧图片可先保留在 reconcile 前缀里过渡，清理完成后移除。
- [ ] 验证 `minio_reconcile` 调整后不会误删保留功能的 `temp/`、`images/` 对象。

## 8. 配置与网关

- [ ] `.env.example`：
  - [ ] 删除第 176-183 行报价运行时配置。
  - [ ] 删除其他报价专属配置说明。
  - [ ] 保留 `U8_SQLSERVER_*`、`PDM_SQLSERVER_*`、`SQLSERVER_QUERY_*`、`U8_BOM_*`。
  - [ ] 保留 `MINIO_RECONCILE_*`。
- [ ] `nginx/nginx.conf.template`：
  - [ ] 删除 `location ^~ /api/v1/closing-form/`。
  - [ ] 删除 `location ^~ /api/v1/quotation/`。
  - [ ] 删除精确匹配 `location = /api/v1/closing-form`、`location = /api/v1/quotation`。
  - [ ] 更新 `/api/v1/sqlserver/` 上方“quotation pipeline 内部调用”的过时注释。
- [ ] 如 collection2 迁到新前缀，同步更新 nginx 与 Vite 代理配置。

## 9. 测试与脚本

### 9.1 单元测试

删除：

- [ ] `tests/test_quotation_workbook_quantities.py`
- [ ] `tests/test_quotation_workbook_adapter.py`
- [ ] `tests/test_quotation_phase2.py`
- [ ] `tests/test_create_direct_u8_task.py`

改写/裁剪：

- [ ] `tests/test_dead_code_cleanup.py`：删除 quotation 域断言。
- [ ] `tests/test_ports_dead_code_cleanup.py`：删除 quotation port 断言。
- [ ] `tests/test_pdm.py`、`tests/pdm_debug.py`：拆除 `QuotationTask` 依赖或删除。
- [ ] `tests/test_rate_limit_role_tier.py`：将 `/quotation/tasks` 样例路径换成保留端点。
- [ ] `tests/gen_single_sql.py`、`tests/test_pdm_sql.py`：改为引用迁移后的 keyword 模块。
- [ ] 检查 `tests/fixtures/u8_result_by_type_*.json` 是否仅报价测试使用；若是则删除。

保留并确认仍通过：

- [ ] `tests/test_pdm_matcher.py`
- [ ] `tests/test_u8_bom_deadlock_retry.py`
- [ ] `tests/test_u8_bom_root_failure_isolation.py`
- [ ] `tests/test_sqlserver_pool_keepalive.py`

### 9.2 回归脚本

- [ ] `tests/scripts/fix_full_regression.sh`：
  - [ ] 删除 `test_closing_form`。
  - [ ] 删除 `test_quotation`。
  - [ ] 删除 `test_retention_pg_redis` 中报价留存校验。
  - [ ] 删除相关变量与 cleanup。
- [ ] `tests/scripts/full_acceptance_regression.sh`：
  - [ ] 删除 `submit_closing_form`、`test_closing_form`。
  - [ ] 删除 `try_quotation_approve`、`delete_quotation_task`、`test_quotation`。
  - [ ] 改写 `test_multi_user_concurrent` 中依赖 closing_form 的并发数据构造逻辑。
  - [ ] 改写 `test_retention_pg_redis`。
  - [ ] 保留 `test_sqlserver_and_pdm_optional`。

### 9.3 新增冒烟

- [ ] `/api/v1/closing-form/*`、`/api/v1/quotation/*` 返回 404。
- [ ] 前端 `/files`、`/closing-form` 直接访问被重定向。
- [ ] `/api/v1/sqlserver/*` 仍可用。
- [ ] `/collection2` 迁移后列表/删除可用。

## 10. 文档更新

- [ ] `README.md`：
  - [ ] 删除“报价生成”“营业订单信息填报”核心能力段落。
  - [ ] 删除前端页面表中 `/files`、`/closing-form` 两行。
  - [ ] 删除 API 前缀表中 `/quotation`、`/closing-form` 两行。
  - [ ] 更新项目结构速览、MinIO / SQL Server 说明、更新日志。
- [ ] `CLAUDE.md`：
  - [ ] 删除 domain / usecases / adapters 中 closing_form、quotation 的描述。
  - [ ] 更新“已知业务前缀”说明。
- [ ] `app/api/README.md`：删除 Quotation、Closing form 两行。
- [ ] `docs/`：
  - [ ] `quotation-task-and-data-flow.md` 删除或移到 `docs/archive/`。
  - [ ] `task-state-truth.md` 删除 quotation 段落，保留 doc task 状态。
  - [ ] `u8-plm-integration-design.md`、`sqlserver-query-parameter-passing.md`、`review-pdm-bom-model-query.md` 保留 `/sqlserver` 内容，移除报价流水线内容。
  - [ ] `SECURITY_PUBLIC_ENDPOINTS.md`、分层架构相关文档同步更新。

## 11. 验证清单

每步完成后必须通过：

- [ ] 删除前基线：`pytest`、`bash scripts/check_layered_architecture.sh`、`pnpm --filter chat type-check` 全绿。
- [ ] 残留引用检查：
  - [ ] `closing_form`
  - [ ] `closing-form`
  - [ ] `PendingForm`
  - [ ] `data_pending`
  - [ ] `view_closing_form`
  - [ ] `page_closing_form`
  - [ ] `quotation_generation`
  - [ ] `QuotationTask`
  - [ ] `view_quotation`
  - [ ] `page_quotation`
  - 说明：迁移后的 keyword 模块与 collection2 新模块不应命中上述删除目标。
- [ ] 后端启动正常，`init_db_tables` 不再报 `data_pending`、`quotation_tasks` 缺失。
- [ ] OpenAPI 中不再出现 `Closing Form`、`Quotation Generation` tag。
- [ ] 前端构建通过，侧边栏只剩：AI 聊天、知识库管理、用户管理。
- [ ] 保留功能冒烟：登录、用户管理、知识库管理、AI 对话、文档处理、OCR、RAG 检索、`/sqlserver` 查询。
- [ ] 品牌冒烟：页面标题/Logo/favicon/文案、后端示例接口、Nginx/systemd 名称均为宁波华翔 / NBHX；无 Yamato / 大和 / 衡器 / 上海大和残留。

## 12. 品牌与素材替换：消除上海大和，切换宁波华翔（NBHX）

### 12.1 替换源与命名约定

- [ ] 根目录 `NBHX.png` 作为宁波华翔 Logo 源文件；不要直接让运行时代码引用根目录文件。
- [ ] 将 `NBHX.png` 复制到前端 public 目录，建议命名：
  - [ ] `frontend/apps/chat/public/nbhx_icon.png`（侧边栏、登录/注册页）
  - [ ] `frontend/apps/chat/public/favicon.ico` 或 `frontend/apps/chat/public/nbhx_favicon.png`（浏览器标签页）
- [ ] 确认统一文案映射，建议如下：
  - [ ] `Yamato` / `Yamato AI` -> `NBHX` / `NBHX AI`
  - [ ] `大和衡器（上海）` / `上海大和` / `大和` -> `宁波华翔`
  - [ ] 代码命名 `yamato` -> `nbhx`（包名、变量、路径、服务名等）
  - [ ] 注意保留 `Asia/Shanghai` 这类业务时区标识，它不是品牌素材。

### 12.2 前端素材与可见文案

- [ ] 删除 `frontend/apps/chat/public/yamato_icon.png`，替换为 NBHX Logo。
- [ ] 保留 `frontend/apps/chat/public/ai_icon.png`（若确认它不是大和专属素材）。
- [ ] `frontend/apps/chat/index.html`：
  - [ ] 标题 `Yamato AI Chat` -> `宁波华翔 AI 助手` 或 `NBHX AI`
  - [ ] favicon 从 `vite.svg` 改为 NBHX favicon
- [ ] `frontend/apps/chat/src/App.vue`：
  - [ ] `title="yamato"` -> `title="宁波华翔"` 或 `NBHX`
  - [ ] `logo-url="/yamato_icon.png"` -> `logo-url="/nbhx_icon.png"`
- [ ] `frontend/apps/chat/src/router/index.ts`：
  - [ ] `document.title ... - yamato` -> `... - 宁波华翔` 或 `... - NBHX`
- [ ] 检查登录页、注册页、侧边栏是否展示品牌名/Logo；如需展示，使用 NBHX Logo 与中文名“宁波华翔”。
- [ ] 全局搜索并替换前端可见文案中的：
  - [ ] `Yamato`
  - [ ] `yamato`
  - [ ] `大和`
  - [ ] `上海`
  - [ ] `衡器`

### 12.3 前端包名、路径与内部标识

- [ ] `frontend/apps/chat/package.json`：
  - [ ] `@yamato/chat` -> `@nbhx/chat`
  - [ ] `@yamato/components` -> `@nbhx/components`
- [ ] `frontend/package.json`：`yamato-frontend` -> `nbhx-frontend`
- [ ] `frontend/packages/components/package.json`：`@yamato/components` -> `@nbhx/components`
- [ ] 全局替换所有 `@yamato/components` import（Vue 页面、`vite.config.ts` alias 等）。
- [ ] 重新执行 `pnpm install`，让 `pnpm-lock.yaml` 与新包名同步。
- [ ] `frontend/apps/chat/vite.config.ts`：检查 `@yamato/components` alias、品牌相关代理/路径。
- [ ] `frontend/apps/chat/src/style.scss`：统一替换 CSS 变量前缀 `--yamato-*` -> `--nbhx-*`，并全局更新所有页面文件中的引用。
- [ ] 前端 localStorage key 替换需要评估存量影响：
  - [ ] `VITE_AUTH_TOKEN_KEY=yamato_chat_token` -> `nbhx_chat_token`
  - [ ] `VITE_SETTINGS_STORAGE_KEY=yamato_chat_settings` -> `nbhx_chat_settings`
  - [ ] `ChatPage.vue` 中 `yamato_chat_background_` 等前缀
  - [ ] 更换后旧登录态/设置会丢失，可加一次性兼容迁移：读旧 key 后写入新 key，再删除旧 key；否则接受用户重新登录。
- [ ] 全局检查 `frontend/apps/chat/src` 下所有 `yamato` 字符串。

### 12.4 后端与配置命名

- [ ] `app/api/v1/example.py`：`Hello Yamato` -> `Hello NBHX`，路由函数名与注释同步。
- [ ] `app/adapters/ragsystem/retriever_for_yamato.py`：
  - [ ] 重命名为 `retriever_for_nbhx.py`
  - [ ] 更新 logger 名 `ragsystem.retriever_for_nbhx`
  - [ ] 更新 `app/adapters/retriever.py`、`app/adapters/ragsystem/RAGretriever.py`、`main.py` 中 import
- [ ] `app/core/config.py`：`MINIO_BUCKET_NAME` 默认值 `yamatodev` -> `nbhxdev`（或保留环境变量覆盖，同时评估旧桶数据迁移）。
- [ ] `.env.example`：
  - [ ] `PROJECT_NAME=Project Yamato Shanghai` -> `NBHX AI Assistant` / `宁波华翔 AI 助手`
  - [ ] `DESCRIPTION=...` -> NBHX 描述
  - [ ] `MINIO_BUCKET_NAME=yamatodev` -> `nbhxdev`（如沿用旧桶，可暂不替换但需在文档说明）
- [ ] `frontend/apps/chat/env.example`、`frontend/apps/chat/.env.production`：
  - [ ] 同步 `PROJECT_NAME` / `DESCRIPTION`
  - [ ] 同步 `VITE_AUTH_TOKEN_KEY` / `VITE_SETTINGS_STORAGE_KEY`
  - [ ] 同步 `MINIO_BUCKET_NAME`
- [ ] 全局检查 `main.py`、`app/`、`requirements.txt`、`langchain_compat.py` 中的 `yamato` / `Yamato` 残留。

### 12.5 部署、脚本与运维命名

- [ ] 重命名 systemd 单元模板：`deploy/yamato-backend.service.template` -> `deploy/nbhx-backend.service.template`。
- [ ] `deploy/yamato-backend.service.template` 内：
  - [ ] 描述 `Yamato AI Backend` -> `NBHX AI Backend`
  - [ ] 注释中的 `Yamato`、`yamato-backend` 同步替换
- [ ] `scripts/install_systemd.sh`：
  - [ ] `UNIT_NAME="yamato-backend.service"` -> `nbhx-backend.service`
  - [ ] 模板路径同步
  - [ ] 注释、日志、文档同步
  - [ ] 虚拟环境探测名 `yamatoenv` -> `nbhxenv`（如需兼容旧环境，可同时探测 `nbhxenv` 与 `yamatoenv`，并在文档中注明过渡策略）
- [ ] `scripts/start_backend.sh`：
  - [ ] 注释、错误提示、`yamatoenv` 探测路径同步
  - [ ] `.env` 示例 `Project Yamato Shanghai` 改为新名称
- [ ] `nginx/nginx.conf.template`：
  - [ ] upstream 名 `yamato_backend` / `yamato_dify` / `yamato_vite` -> `nbhx_backend` / `nbhx_dify` / `nbhx_vite`
  - [ ] 限流 zone `yamato_dify` -> `nbhx_dify`
  - [ ] 所有 `proxy_pass` 与注释同步
  - [ ] 与待删除路由同步：`closing-form`、`quotation` 相关 location 一并删除
- [ ] `nginx/README.md` 中 upstream、zone 名称同步。
- [ ] 如项目根目录 / 仓库名也重命名（例如 `project-yamato-shanghai` -> `project-nbhx`），同步 README、部署脚本、systemd WorkingDirectory、测试脚本中的路径。
- [ ] 测试与回归脚本中的临时目录、虚拟环境路径：
  - [ ] `tests/scripts/*.sh` 中 `yamato_fix_regression` / `yamato_full_acceptance` -> `nbhx_*`
  - [ ] 硬编码 `/home/shmtu/桌面/yamatoenv/bin/python` 改为可配置的 `${VENV_DIR:-python}` 或新的 `nbhxenv` 路径，避免保留旧品牌路径。

### 12.6 文档素材

- [ ] `README.md`：
  - [ ] 标题 `Yamato AI 助手平台` -> `宁波华翔 AI 助手平台` / `NBHX AI Assistant`
  - [ ] `大和衡器（上海）`、`大和`、`上海` 等文案统一替换
  - [ ] 页脚 `仅供大和衡器（上海）内部使用 ... Shanghai Marinetime 331 Team` 替换为 NBHX 版本
  - [ ] 快速开始里的仓库名、conda 环境名 `yamato` 同步
- [ ] `CLAUDE.md`：标题、项目概览、目录说明中的 `Yamato` / `大和` 同步替换。
- [ ] `frontend/README.md`：标题 `Yamato Frontend` -> `NBHX Frontend`，目录说明同步。
- [ ] `frontend/rules/frontend.mdc`、`frontend/QUICKSTART.md` 检查品牌残留。
- [ ] `nginx/README.md`：品牌与 upstream 名称同步。
- [ ] `docs/u8-plm-integration-design.md`：`Yamato`、`大和衡器（上海）`、`project-yamato-shanghai` 同步替换。
- [ ] 本文档自身及其他 docs 中的品牌残留检查。
- [ ] 若旧品牌素材需要留档，统一放入 `docs/archive/` 或单独的移交目录，仓库运行时不再引用。

### 12.7 品牌替换验证

- [ ] `grep -Rni "yamato" .` 结果只剩：
  - [ ] `docs/archive/` 下的历史留档（如决定留档）
  - [ ] 迁移兼容代码（如明确保留旧 localStorage / 旧 venv 探测）
- [ ] `grep -Rni "大和\|衡器\|上海" .` 不再命中业务代码与展示文案。
- [ ] `grep -Rni "shanghai" .` 只允许业务时区 `Asia/Shanghai` 或历史留档命中。
- [ ] 前端启动后确认：浏览器标题、侧边栏 Logo、侧边栏名称、登录/注册页、favicon 全部为 NBHX。
- [ ] 检查浏览器 localStorage：新用户写入新 key；老用户按迁移策略处理。
- [ ] 后端启动打印、Swagger 标题、示例接口文案为 NBHX。
- [ ] `pnpm install`、`pnpm --filter chat type-check`、`pnpm build` 通过。
- [ ] systemd/Nginx 部署名称替换后启动正常，健康检查通过。

## 13. 默认保留清单

以下功能按“其他功能保留”原则继续保留：

- AI 对话、Dify 接入、对话摘要、上下文压缩。
- 文档上传 / 文档处理 / 文档任务 WebSocket。
- OCR：图片识别、PDF 转图片，含 `/ocr` 与 legacy 别名。
- RAG 检索：`/retriever`、`app/adapters/ragsystem/*`、`data_doc_collection_1`。
- 文件管理：`/api/v1/files`、`app/usecases/file_manager/*`、`app/adapters/file_manager.py`。
  - 注意：前端 `/files` 路由是报价页，要删；后端 `/api/v1/files` 是文件管理 API，要保留。
- 知识库管理：`/collection2`、`Collection2ManagePage.vue`（需先完成接口迁出）。
- 用户与认证：登录、注册、用户管理、角色、密码重置。
- SQLServer 查询：`/api/v1/sqlserver`、U8/PDM adapter、PDM matcher、keyword 迁移模块。
- 任务基础设施：TaskManager、Observer、WebSocket 任务进度、Executor、MinIO orphan reconcile。

## 14. 建议 PR 拆分

1. **PR1 解耦**：collection2 迁出 closing_form；keyword 模块迁出 quotation；共享 core / 任务基础设施去报价化。
2. **PR2**：删除 closing_form 后端 + 前端 + 权限 + `data_pending` 数据。
3. **PR3**：删除报价生成后端 + 前端 + 权限 + `quotation_tasks` 数据。
4. **PR4**：测试脚本、文档、存量 RBAC 与 MinIO 清理。
5. **PR5**：品牌与素材替换（NBHX），删除 Yamato / 大和素材，统一前端、后端、部署与文档品牌。

每个 PR 独立回归，避免一次删除过多导致保留功能被误伤。
