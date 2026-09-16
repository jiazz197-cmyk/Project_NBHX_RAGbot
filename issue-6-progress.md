# Issue #6 进度清单：页面权限框架保留（禁用态）与全局清理

> GitLab Work Item: #6（`Carl_Jia/ragchatbot`）
> 对应计划文档：原 `docs/removal-plan-closing-form-and-quotation.md` §6 / §9 / §10 / §11（随本 issue 完成已删除）
> 创建时间：2026-09-16
>
> 口径变更：原 issue 要求“移除页面权限框架整套删除”，本次按业务方要求调整为：
> **只移除两个已下线页面的权限管理；通用页面权限框架保留，但默认不启用**。
> 后续新业务页面需要时，配置 `PAGE_PERMISSION_MANAGEMENT_ENABLED=True` 并补 RBAC
> seed / 用户界面入口即可复用。

---

## 执行口径

- 两个已删页面的 permission key / role / 开关 / 自动授权不再存在。
- 通用页面权限框架保留为 data-driven：
  - 页面 key → `view_<key>` Permission → `page_<key>` Role；
  - `app/domain/auth/page_permissions.py` 负责命名与校验；
  - `PATCH /auth/users/{id}/page-permissions` 默认返回 404（feature flag 关闭）；
  - 前端 `UserPagePermissions` / `updateUserPagePermissions` service 保留，用户管理页暂不展示入口。
- 不写存量数据迁移脚本；仅改代码。
- 过时文档直接删除，不建 archive。

## A. 移除两个页面的权限管理（框架保留禁用）

后端：

- [x] `app/ports/dto/auth.py`：`UpdatePagePermissionsCommand` 改为通用 `page_permissions: dict[str, bool]`，无页面 key 耦合
- [x] `app/ports/outbound/auth.py`：保留通用 `update_page_permissions(...)` 契约
- [x] `app/usecases/auth/users.py`：保留 `UpdateUserPagePermissionsUseCase`，统一做 key 规范化
- [x] `app/api/v1/auth.py`：保留 `PATCH /auth/users/{id}/page-permissions`；默认由 `PAGE_PERMISSION_MANAGEMENT_ENABLED=False` 关闭并返回 404
- [x] `app/adapters/web/platform/user.py`：保留通用 `UserPagePermissionsUpdate`
- [x] `app/adapters/auth/user_repository.py`：新用户不再自动分配任何页面角色；保留通用 `update_page_permissions` 实现
- [x] `app/core/config.py` / `.env.example`：新增 `PAGE_PERMISSION_MANAGEMENT_ENABLED`，默认 `False`
- [x] `app/domain/auth/page_permissions.py`：页面 key 命名 / 校验 / RBAC 名称映射

前端：

- [x] `frontend/apps/chat/src/services/auth.ts`：保留通用 `UserPagePermissions` / `updateUserPagePermissions`
- [x] `frontend/apps/chat/src/pages/UserManagePage.vue`：无“页面权限”列与相关状态
- [x] `frontend/apps/chat/src/router/index.ts`：通用 `requiresPermission` 守卫保留，兜底路由注释去掉已删页面强引用

## B. 回归脚本收尾

- [x] `tests/scripts/fix_full_regression.sh`：已确认不含旧页面 / 报价链路变量与校验
- [x] `tests/scripts/full_acceptance_regression.sh`：已确认不含旧页面 / 报价链路变量与校验；并发测试仅依赖文件上传与文档任务
- [x] retention 检查仅保留 MinIO reconcile 语义
- [x] 两个脚本 `bash -n` 通过

## C. 冒烟与测试

- [x] 新增 `tests/test_page_permission_framework_disabled.py`：
  - 已删页面权限符号在业务代码 / 回归脚本零命中
  - 框架默认关闭
  - 通用 Command / payload / repo / usecase 行为
  - endpoint 默认返回 404
- [x] 新增 `tests/scripts/issue6_removed_features_smoke.sh`：
  - 已删 API 前缀返回 404
  - 前端已删路由未注册、兜底路由存在
  - 权限框架代码存在但默认关闭
  - knowledge 列表 / 删除 / 上传与 retriever 冒烟（后端可用时）
- [x] `.gitlab-ci.yml` 补齐测试实际需要的新增最小依赖（email-validator / dnspython）

## D. 文档更新

- [x] `README.md`：删除已删功能核心能力 / 页面表 / API 表 / 仓库结构 / 更新日志中的旧描述，改为当前知识库 + 文档 + OCR 口径
- [x] `CLAUDE.md`：同步删除旧业务描述，保留通用页面权限禁用态说明
- [x] `app/api/README.md`：确认无旧业务前缀
- [x] 直接删除：`quotation-task-and-data-flow.md`、`u8-plm-integration-design.md`、`sqlserver-query-parameter-passing.md`、`review-pdm-bom-model-query.md`、`u8_bom_tables.md`
- [x] `task-state-truth.md`：只保留 doc / OCR 任务状态真相
- [x] `SECURITY_PUBLIC_ENDPOINTS.md` 及分层架构文档：同步移除已删功能的请求流 / 端点描述
- [x] 原 `docs/removal-plan-closing-form-and-quotation.md` 全量完成后删除

## E. 全局残留验证

- [x] 业务代码 grep 零命中：两个页面权限 key、role、旧 domain / endpoint / ORM 符号
- [x] 回归脚本旧符号零命中
- [x] `pytest -q`：187 passed / 9 skipped（本机最小依赖环境）
- [x] `bash scripts/check_layered_architecture.sh`：8/8 PASSED
- [x] `corepack pnpm --filter chat type-check`：通过
- [x] `corepack pnpm --filter chat build`：通过

## 验收标准对照

- [x] `PATCH /auth/users/{id}/page-permissions` 默认 404；UserManagePage 无“页面权限”列
- [x] 角色体系（admin / superuser、`require_roles`）不受影响
- [x] E 节残留验证通过
- [x] 架构守卫、pytest、前端 type-check / build 全绿
