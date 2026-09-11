# models + core 整合（Clean Architecture 收尾 · 后续 issue）

> 本轮 CA 折叠（`ragsystem / infrastructure / workers / schemas` → `adapters/`）已完成。
> `app/models/` 与 `app/core/` 原位保留为「外层工具岛」，本 issue 处理二者与分层终态的最终对齐。

## 现状（2026-07 核实）

- `app/models/orm` 被 **22 个外部文件**引用（`core/` 7、`adapters/quotation/` 5、`adapters/` 3、`main.py` 1、`api/v1/` 1、`adapters/{workers,web,ocr,doc_processing,auth}/` 各 1；含包内互引共 33）。
- `app/core/` 中 **6 个助手直接依赖 `app.models`**：`rbac_queries`、`security`、`task_owner_registry`、`quotation_task_cleanup`、`minio_reconcile`、`websocket_task_manager`。
- **第 4 处泄漏（本轮未修）**：`app/api/v1/quotation_generation.py:38` 与 `main.py:109` 直接 `from app.models.orm.quotation_task import QuotationTask`，绕过 `QuotationTaskRepoPort`。
- **有利条件**：`domain / ports / usecases` 对 `app.core`、`app.models` 的引用已为 **0**（先前 CA 清理已剥离），故「把 core/models 纳入内层禁列」对内层零破坏。

## 任务

1. **迁移 ORM**：`app/models/orm` → `app/adapters/persistence/orm`；批量改写 22 处外部引用（`app.models.orm` → `app.adapters.persistence.orm`）。
2. **下沉 core 助手**：将上述 6 个依赖 model 的助手按职责迁入 adapter/usecase：
   - `rbac_queries` / `security` → auth adapter；
   - `task_owner_registry` / `quotation_task_cleanup` / `minio_reconcile` → quotation adapter；
   - `websocket_task_manager` → 评估归属 driving adapter 或保留为组合根设施。
3. **修第 4 处泄漏**：`api/v1/quotation_generation.py:38` 与 `main.py:109` 改走 `QuotationTaskRepoPort`（组合根经 repo 查询），不再直连 ORM。
4. **收紧检测脚本**：在 `scripts/check_layered_architecture.sh` 规则 1–3 的禁列中加入 `core|models`（内层已 0 引用，可直接启用）；并补一条「`app.adapters.persistence.orm` 只能被 `app/adapters/` 与组合根 import」。
5. 清理空目录 `app/models/`；`app/core/` 仅留真正跨层且无 model 依赖的工具（`logging` / `time_utils` / `exceptions` / `config` / `storage` 等按需保留或归位）。

## 验收

- `pytest` 全绿；
- 新规则脚本通过（含 core/models 内层禁列）；
- `grep -rE '^[[:space:]]*(from|import) app\.(core|models)' app/domain app/ports app/usecases` 为空；
- `app/api`、`main.py` 无 `app.models.orm` 直连。

## 风险

- ORM 迁移扇入 22，**高风险**，单独成 PR，重点跑 quotation / auth / chat_summary / sqlserver / doc_processing 相关测试与接口冒烟。
- core 助手下沉需逐一判断 driving / driven / 组合根设施归属，避免把组合根设施误降为内层。
