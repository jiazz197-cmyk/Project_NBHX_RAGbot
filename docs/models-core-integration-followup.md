# models + core 整合（Clean Architecture 收尾 · 后续 issue）

> 本轮 CA 折叠（`ragsystem / infrastructure / workers / schemas` → `adapters/`）已完成。
> `app/models/` 与 `app/core/` 原位保留为「外层工具岛」，本 issue 处理二者与分层终态的最终对齐。

## 现状（2026-09 核实）

- 业务内层（`domain / ports / usecases`）对 `app.core`、`app.models` 的引用已收敛，仅保留少量工具依赖。
- `app/core/` 中直接依赖 ORM 的助手主要是 `rbac_queries`、`security`、`task_owner_registry`、`minio_reconcile`、`websocket_task_manager` 等。
- `main.py` 与组合根仍需按生命周期访问 ORM 与基础设施；这是当前接受的「外层工具岛」边界。

## 任务

1. **迁移 ORM**：评估 `app/models/orm` → `app/adapters/persistence/orm`，批量改写外部引用。
2. **下沉 core 助手**：将依赖 model 的助手按职责迁入对应 adapter / usecase：
   - `rbac_queries` / `security` → auth adapter；
   - `task_owner_registry` / `minio_reconcile` / `websocket_task_manager` → 任务基础设施 adapter。
3. **收口组合根**：让 route / main 只经 Port + UseCase 使用 ORM，不再新增 `from app.models...` 直连。
4. **补测试**：为迁移后的 Port 边界补 fake adapter 单测，重点覆盖鉴权与任务所有权。
