# Issue #5 进度清单：删除报价生成与 SQLServer / U8 / PDM 查询，共享基础设施去报价化

> GitLab Issue: #5（`Carl_Jia/ragchatbot`）
> 对应计划文档：`docs/removal-plan-closing-form-and-quotation.md` §1.2 / §1.3 / §4 / §5
> 创建时间：2026-09-14
>
> 进度约定：完成一项勾选一项（`- [x]`），并在每项后标注提交 hash 与日期。

---

## 进度总览

| 块 | 内容 | 状态 |
|----|------|------|
| A | 删除报价生成（quotation 域） | ✅ 完成 |
| B | 删除 SQLServer / U8 / PDM 查询 | ✅ 完成 |
| C | main.py 生命周期 | ✅ 完成 |
| D | 共享基础设施去报价化 | ✅ 完成 |
| E | 配置 | ✅ 完成 |
| F | 前端 | ✅ 完成 |
| G | 测试 | ✅ 完成 |
| H | 网关 | ✅ 完成 |
| I | 补充项（Issue 清单未覆盖的残留点） | 🟡 部分（残留复查阶段4） |
| 验收 | 验收标准 | ⬜ 未开始 |

---

## 核心口径（执行前必读）

- **SQLServer / U8 / PDM 查询与报价生成一并删除**，不保留 `/sqlserver`；`keyword_mapping.py`、`keyword_normalizer.py` 直接删除，不迁移。
- 数据策略：**只改代码**。`quotation_tasks` 表不 DROP；MinIO 报价对象不清理。
- **保留**：`EXECUTOR_MAX_WORKERS`（共享 executor 服务 OCR / 文档处理）；后端 `/api/v1/files` API 保留（只删前端 `/files` 页面）。
- 依赖方向：报价生成（A）依赖 SQLServer（B），B 不依赖 A → **先删 A 再删 B**，每步均可保持项目可运行。

---

## A. 删除报价生成（quotation 域）

- [x] 删除 `app/api/v1/quotation_generation.py`
- [x] 删除整个 `app/adapters/quotation/`
- [x] 删除整个 `app/adapters/workers/quotation_generation/`
- [x] 删除 `app/adapters/workers/dispatch.py`（`QuotationDispatchAdapter` 专用）
- [x] 删除整个 `app/usecases/quotation/`
- [x] 删除 `app/models/orm/quotation_task.py`
- [x] 删除 `app/ports/dto/quotation.py`
- [x] 删除 `app/ports/dto/quotation_workbook.py`
- [x] 删除 `app/ports/outbound/quotation.py`
- [x] 删除 `app/ports/outbound/quotation_workbook.py`
- [x] 删除 `app/domain/quotation/` 的 entities、exceptions、partid_mapping、pdm_result、results、summary_selection、u8_grouping、value_objects、workbook（⚠️ `__init__.py`、`keyword_mapping.py`、`keyword_normalizer.py` 因被 `sqlserver_queries.py` 依赖，**延迟到阶段3删除**）

## B. 删除 SQLServer / U8 / PDM 查询

- [x] 删除 `app/api/v1/sqlserver_queries.py`
- [x] 删除 `app/adapters/sqlserver_queries.py`
- [x] 删除整个 `app/adapters/sqlserver/`（client、connectivity、exceptions、pdm_bom、pdm_matcher_adapter、u8_bom）
- [x] 删除整个 `app/adapters/pdm_matcher/`
- [x] 删除整个 `app/usecases/sqlserver_queries/`
- [x] 删除 `app/adapters/web/sqlserver.py`
- [x] 删除 `app/ports/dto/sqlserver_queries.py`
- [x] 删除 `app/ports/outbound/sqlserver_queries.py`
- [x] `app/core/security.py`：删除 `get_current_user_detached`（已确认唯一使用者是 sqlserver 端点）
- [x] `app/core/middleware/rate_limit.py`：`expensive_path_prefixes` 删除 `/sqlserver`
- [x] `app/adapters/doc_processing/model_pool.py`：更新「镜像 sqlserver 连接池语义」的注释
- [x] 删除 `app/domain/quotation/` 剩余的 `__init__.py`、`keyword_mapping.py`、`keyword_normalizer.py`（阶段2延迟项）
- [x] 删除 `app/core/circuit_breaker.py`（SQLServer 专属熔断器，唯一使用者 sqlserver/client.py 已删，且引用了已删的 SQLSERVER_CB_* 配置，属死代码）
- [x] `requirements.txt`：删除 `pymssql==2.3.0`（SQL Server 驱动，无使用者）

## C. main.py 生命周期

- [x] 删除 `_startup_check_sqlserver_connectivity()` 及调用（含 `app.state.sqlserver_connectivity`）
- [x] 删除关闭流程中的 SQL Server 连接池清理（`close_shared_u8_pool`）与 pool snapshot 的 `sqlserver_u8` 段
- [x] 删除 `shutdown_sqlserver_query_executor` 引用
- [x] 删除 `_startup_resume_quotation_services()` 及调用
- [x] 删除 `set_quotation_dispatch_loop(...)` 注册与清理
- [x] 更新报价任务 retention 调度相关启动 / 关闭日志（retention 已重命名为 MinIO reconcile 调度器）；`or_` import 已删除

## D. 共享基础设施去报价化（⚠️ 精准摘除，不伤及 OCR / 文档处理）

- [x] `app/core/task_owner_registry.py`：删除 `_QuotationOwnerLookup` 及 `QuotationTask` import，保留 doc / OCR 任务 owner 查找
- [x] `app/ports/contracts/tasking.py`：删除 `TaskDispatchPort`（仅报价使用），保留 `TaskStatePort`、`TaskExecutionPort`
- [x] `app/core/minio_reconcile.py`：删除 `QuotationTask` 及其字段注册，保留 `FileResource`、`temp/`、`images/`、`documents/`
- [x] `app/core/retention_scheduler.py`：删除报价任务留存逻辑，重命名为 MinIO orphan reconcile 调度器
- [x] 删除 `app/core/quotation_task_cleanup.py`
- [x] `app/core/circuit_breaker.py`：清理报价相关注释
- [x] `app/core/storage.py`：清理报价相关注释
- [x] `app/core/task_manager.py`：清理报价相关注释
- [x] `app/core/database.py`：删除 `QuotationTask` import、`quotation_tasks` 迁移逻辑、RBAC 种子 `view_quotation`、`page_quotation`

## E. 配置

- [x] `app/core/config.py` 删除：`QUOTATION_*`、`U8_SQLSERVER_*`、`PDM_SQLSERVER_*`、`SQLSERVER_QUERY_*`、`U8_BOM_*` 及 `_validate_u8_bom_concurrency` 校验器、secrets 列表中的 `U8_SQLSERVER_PASSWORD`/`PDM_SQLSERVER_PASSWORD`（保留 `EXECUTOR_MAX_WORKERS`）
- [x] `app/core/logging.py`：删除 quotation 日志文件与 logger route
- [x] `app/api/v1/registry.py`：移除 `quotation_generation`、`sqlserver_queries` import 与 mount
- [x] `app/api/v1/prefixes.py`：删除 `QUOTATION`、`SQLSERVER`
- [x] `app/api/v1/tags.py`：删除 `QUOTATION_GENERATION`、`SQLSERVER_QUERY` 常量与 tag 元数据
- [x] `.env.example`：删除报价运行时配置与 U8 / PDM / SQLSERVER 专属配置；保留 `MINIO_RECONCILE_*`

## F. 前端

- [x] 删除 `frontend/apps/chat/src/pages/FileManagerPage.vue`
- [x] 删除 `frontend/apps/chat/src/services/quotation.ts`
- [x] 删除 `frontend/apps/chat/src/types/quotation.ts`
- [x] `frontend/apps/chat/src/router/index.ts`：删除 `/files` 路由
- [x] `frontend/apps/chat/src/App.vue`：删除「报价生成」侧边栏入口、`showQuotation` 计算属性及权限判断
- [x] `frontend/apps/chat/src/services/auth.ts`：从 `UserPagePermissions` 删除 `view_quotation`
- [x] `frontend/apps/chat/src/pages/UserManagePage.vue`：删除「报价生成」权限开关及相关逻辑
- [x] `frontend/apps/chat/vite.config.ts`：删除 `/quotation` 代理
- [x] 检查并移除仅这两个页面使用的前端依赖（结论：无需移除，`js-tiktoken` 仍被 `utils/token_counter.ts` 使用）
- [x] 确认后端 `/api/v1/files` API 保留，仅前端 `/files` 页面路由删除（nginx `files` location 保留，后端未动）

## G. 测试

删除：

- [x] `tests/test_quotation_workbook_quantities.py`
- [x] `tests/test_quotation_workbook_adapter.py`
- [x] `tests/test_quotation_phase2.py`
- [x] `tests/test_create_direct_u8_task.py`
- [x] `tests/test_pdm_matcher.py`
- [x] `tests/test_u8_bom_deadlock_retry.py`
- [x] `tests/test_u8_bom_root_failure_isolation.py`
- [x] `tests/test_sqlserver_pool_keepalive.py`
- [x] `tests/test_pdm.py`
- [x] `tests/pdm_debug.py`
- [x] `tests/gen_single_sql.py`
- [x] `tests/test_pdm_sql.py`
- [x] `tests/fixtures/u8_result_by_type_*.json`（已确认仅报价 / sqlserver 测试使用）

改写：

- [x] `tests/test_dead_code_cleanup.py`：删除 quotation / sqlserver 域断言（含 `U8_BOM_POOL_ACQUIRE_TIMEOUT_SEC`）
- [x] `tests/test_ports_dead_code_cleanup.py`：删除 quotation / sqlserver port 断言
- [x] `tests/test_rate_limit_role_tier.py`：将 `/quotation/tasks` 样例路径换成 `/document-tasks`、`/sqlserver/query` 换成 `/retriever`

## H. 网关

- [x] `nginx/nginx.conf.template`：删除 `location ^~ /api/v1/quotation/`
- [x] `nginx/nginx.conf.template`：删除 `location ^~ /api/v1/sqlserver/` 及对应精确匹配 location
- [x] 更新 `/api/v1/sqlserver/` 上方「quotation pipeline 内部调用」过时注释

---

## I. 补充项（Issue 清单未显式覆盖的残留点，⭐ 扫描发现）

> 验收标准要求 `view_quotation`、`page_quotation` 业务代码零命中，但 Issue 清单 F/E 块未覆盖以下后端 auth 域引用，必须一并清理。

- [x] `app/ports/dto/auth.py`：删除 `view_quotation: bool` 字段
- [x] `app/ports/outbound/auth.py`：删除 Port 签名中的 `view_quotation`
- [x] `app/usecases/auth/users.py`：删除 `cmd.view_quotation` 透传
- [x] `app/adapters/auth/user_repository.py`：删除 `page_quotation` 角色管理逻辑（保留 `page_closing_form`）
- [x] `app/adapters/web/platform/user.py`：删除 `view_quotation` 字段/默认值
- [x] `app/api/v1/auth.py`：删除 `view_quotation=body.view_quotation` 等用户更新入参

> 阶段 0 扫描补充发现（2026-09-14）：

- [x] `scripts/check_layered_architecture.sh` 确认**无需修改**：8 条规则均为目录/import 正则，不 hardcode quotation / sqlserver 路径
- [x] `app/core/config.py` 额外点：`_validate_u8_bom_concurrency` 校验器、secrets 列表中的 `U8_SQLSERVER_PASSWORD`/`PDM_SQLSERVER_PASSWORD`、SQL Server 连接配置、U8_BOM 配置均已删除
- [x] `main.py` 引用点：connectivity check、连接池清理与 snapshot、shutdown_sqlserver_query_executor、报价队列恢复、dispatch loop 均已删除
- [x] `app/api/v1/prefixes.py` 的 `QUOTATION`/`SQLSERVER` 常量、`app/api/v1/tags.py` 的 `QUOTATION_GENERATION`/`SQLSERVER_QUERY` 常量、`.env.example` 的报价/U8/PDM/SQLSERVER 配置均已删除
- [x] `app/core/circuit_breaker.py`、`app/core/storage.py` 注释清理（circuit_breaker 为 SQLServer 专属死代码，已整体删除）
- [x] 与 Issue #4（closing_form）交叉点处理：`user_repository.py` 中删除 `page_quotation`，保留 `page_closing_form`
- [x] 全量 grep 复查 7 符号业务代码零命中（阶段4完成）

---

## 验收标准

- [ ] `/api/v1/quotation/*`、`/api/v1/sqlserver/*` 全部返回 404；OpenAPI 无 `Quotation Generation`、`SQLServer Query` tag
- [ ] 残留引用检查（业务代码零命中）：`quotation_generation`、`QuotationTask`、`view_quotation`、`page_quotation`、`sqlserver`、`pdm_matcher`、`U8_BOM`（计划文档与历史 issue 描述除外）
- [ ] 后端启动正常：无 SQL Server 连通性检查、无报价队列恢复、`init_db_tables` 不再依赖 `quotation_tasks`
- [ ] 共享任务基础设施不受影响：OCR / 文档处理 / 知识库上传任务正常提交与进度可见；`minio_reconcile` 不误删 `temp/`、`images/`、`documents/` 对象
- [ ] `pytest` 全绿
- [ ] `bash scripts/check_layered_architecture.sh` 通过
- [ ] `pnpm --filter chat type-check` 通过

---

## 依赖与顺序

- 建议在「删除 closing_form」（Issue #4）之后执行（避免删除交叉、便于回归定位）
- 后续：「移除页面权限框架 + 清理」（Issue #6）、「品牌替换」（Issue #7）、「Dify → LangChain」（Issue #8）
- 执行顺序（自顶向下 + 先断引用再删实体）：
  1. 阶段 1：外围件（测试删除 / nginx / 前端纯删除）
  2. 阶段 2：删报价 A + 修共享文件报价专属部分
  3. 阶段 3：删 SQLServer B + 修共享文件 sqlserver 专属部分
  4. 阶段 4：收尾改写测试 + 残留复查
  5. 阶段 5：验收

---

## 操作记录

| 日期 | 完成项 | 提交 hash |
|------|--------|-----------|
| 2026-09-14 | 阶段0：全量残留扫描锁定清单 | eb3f01f |
| 2026-09-14 | 阶段1：外围件删除（nginx/测试/前端） | 860bd33 |
| 2026-09-14 | 阶段2：删除报价域 A + 去报价化共享设施 | 191ac33 |
| 2026-09-14 | 阶段3：删除 SQLServer 域 B + 去 SQLServer 化共享设施 | b5909e4 |
