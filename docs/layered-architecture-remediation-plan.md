# 分层架构整改方案（详细版）

> 更新时间：2026-04-26  
> 审查基线：`docs/code-review-layered-architecture.md`  
> 审查范围：`app/api/v1`、`app/core`、`app/integrations`  
> 说明：本方案**不讨论目录命名**，仅聚焦分层逻辑、Port、UseCase、路由治理。

---

## 1. 二次复核结果（最新）

### 1.1 关键统计

- `Protocol` 命中：**0**（未发现 `typing.Protocol` / `typing_extensions.Protocol`）
- `usecase/use_case/orchestr` 相关命中：**0**
- API endpoint 总数：**54**
- API endpoint 超过 15 行：**35**（约 64.8%）
- 包级依赖方向：
  - `api -> core`: 46
  - `api -> integrations`: 10
  - `integrations -> core`: 56
  - `core -> integrations`: **1（反向依赖）**

### 1.2 明确的反向依赖证据

- `app/core/middleware/monitoring.py:9`
  - `from app.integrations.monitoring.prometheus import metrics`

### 1.3 最厚路由（TOP 10）

1. `app/api/v1/chat_summary.py:create_chat_summary` (92 行)
2. `app/api/v1/quotation_generation.py:create_quotation_task` (83 行)
3. `app/api/v1/chat_summary.py:query_user_summary` (74 行)
4. `app/api/v1/quotation_generation.py:approve_quotation_task` (59 行)
5. `app/api/v1/websocket_notifier.py:websocket_task_endpoint` (55 行)
6. `app/api/v1/document_processing.py:submit_document_processing` (54 行)
7. `app/api/v1/pdf2image.py:get_pdf_convert_task_status` (52 行)
8. `app/api/v1/image2url.py:get_image_upload_task_status` (52 行)
9. `app/api/v1/context_compression.py:compress_chat_context` (52 行)
10. `app/api/v1/quotation_generation.py:cancel_quotation_task` (50 行)

---

## 2. 当前架构问题（聚焦四项）

## 2.1 分层逻辑

当前主要是 **Route 直接编排 + Route 直接调具体实现**，缺少中间业务编排层。导致：

- 边缘层（route）承担了权限、分支、异常映射、状态流转等职责
- 业务规则散落在多个 endpoint，难复用、难回归
- 外部依赖切换成本高

## 2.2 Port（接口契约）缺失

缺少 Protocol 契约，导致：

- UseCase 无法只依赖抽象
- 只能直接依赖 integrations/core 的具体类与函数
- Fake 实现测试困难，单元测试边界模糊

## 2.3 UseCase（业务编排）缺失

未发现独立用例层，导致：

- 业务流程在 route 中“粘合式”存在
- 多业务线（chat summary / quotation / document task）有重复处理逻辑
- 无法形成“可组合、可测、可迁移”的编排中心

## 2.4 路由过厚

35/54 endpoint 超过 15 行，且多处出现：

- 复杂 `if/else`
- `db.query` 细节
- `task_manager/executor_manager` 调度细节
- 直接调用 `app.integrations.*`

这与目标“薄边缘层（解析输入 -> 调用核心 -> 格式化输出）”不一致。

---

## 3. 整改目标（验收可量化）

### 3.1 结构目标

形成稳定调用链：

```text
API Route -> UseCase -> Port(Protocol) -> Adapter(具体实现)
```

### 3.2 指标目标

1. Protocol 接口数 >= 10（首批）
2. 首批关键业务线全部落地 UseCase（chat_summary、quotation_generation）
3. 路由 >15 行占比从 64.8% 降至 <20%
4. 新增 endpoint 禁止直接 import `app.integrations.*`
5. 消除 `core -> integrations` 反向依赖（至少现有 1 处）

---

## 4. 目标分层职责（落地标准）

## 4.1 Route 层（api/v1）

**应该做**：
- 参数校验（Pydantic）
- 鉴权依赖（Depends）
- 调用 UseCase（单入口）
- 返回响应 DTO

**不应该做**：
- `db.query` 业务查询细节
- 任务编排（task_manager/executor_manager 细节）
- 外部服务调用（OCR/SQL/MinIO 等）
- 复杂分支业务规则

## 4.2 UseCase 层

**应该做**：
- 业务流程编排
- 规则组合
- 统一错误语义（领域异常）

**不应该做**：
- 直接调 SDK
- 依赖具体 ORM/第三方 client

## 4.3 Port 层（Protocol）

**应该做**：
- 定义抽象能力与入参/出参
- 与实现解耦

**不应该做**：
- 暴露实现细节（SQLAlchemy Query、OpenAI Response 等）

## 4.4 Adapter 层（现 integrations + 部分 core 实现）

**应该做**：
- 实现 Port
- 封装 IO 异常
- 数据模型转换

---

## 5. 详细改造清单（按优先级）

## P0（必须先做）

### P0-1 建立 Port 契约

新增（示例）：

- `app/ports/chat_summary.py`
  - `UserLookupPort`
  - `ChatSummaryRepoPort`
  - `ChatArchivePort`
- `app/ports/quotation.py`
  - `QuotationTaskRepoPort`
  - `FileStoragePort`
  - `TaskDispatchPort`
- `app/ports/tasking.py`
  - `TaskStatePort`

### P0-2 建立 UseCase 层

新增（示例）：

- `app/usecases/chat_summary/create_chat_summary.py`
- `app/usecases/chat_summary/query_user_summary.py`
- `app/usecases/quotation/create_task.py`
- `app/usecases/quotation/cancel_task.py`
- `app/usecases/quotation/approve_task.py`

### P0-3 路由瘦身改造

优先重构文件：

1. `app/api/v1/chat_summary.py`
2. `app/api/v1/quotation_generation.py`

将 route 内部业务逻辑迁移到 usecase，仅保留 DTO/Depends/调用/响应。

---

## P1（紧随其后）

### P1-1 任务类接口统一编排

文件：

- `app/api/v1/document_processing.py`
- `app/api/v1/pdf2image.py`
- `app/api/v1/image2url.py`

抽取：

- `SubmitTaskUseCase`
- `GetTaskStatusUseCase`
- `GetTaskResultUseCase`
- `CancelTaskUseCase`

### P1-2 解除 core -> integrations 反向依赖

目标点：

- `app/core/middleware/monitoring.py`

建议：通过依赖注入或中立的 metrics port 替代直接 import adapter 实现。

---

## P2（质量守护）

### P2-1 架构 CI 守卫

建议新增检查：

```bash
# 禁止 usecases 直连 integrations 具体实现
! grep -R "from app.integrations" app/usecases --include="*.py"

# 禁止 route 直连 integrations（迁移完成后强制）
! grep -R "from app.integrations" app/api/v1 --include="*.py"

# 必须存在 Protocol
grep -R "Protocol" app/ports --include="*.py"
```

### P2-2 用例级测试

为每个 usecase 配置 Fake Adapter 测试：

- 不依赖数据库
- 不依赖外部服务
- 可验证编排逻辑与异常分支

---

## 6. 分业务线实施方案

## 6.1 chat_summary 业务线

### 当前问题
- 两个 endpoint 合计 166 行左右
- 路由中含权限分支 + 用户标识归一化 + DB 查询 + 外部调用

### 拆分方案

- UseCase：
  - `CreateChatSummaryUseCase.execute(cmd)`
  - `QueryUserSummaryUseCase.execute(query)`
- Port：
  - `UserLookupPort`
  - `ChatSummaryRepoPort`
  - `ChatArchivePort`

### 目标结果
- 两个 endpoint 均 <= 15 行
- 用户身份规范化策略归口 usecase/domain service

---

## 6.2 quotation_generation 业务线

### 当前问题
- create/cancel/approve 路由承载完整状态机流程
- 文件存储、任务排队、权限与状态判断耦合在 route

### 拆分方案

- UseCase：
  - `CreateQuotationTaskUseCase`
  - `CancelQuotationTaskUseCase`
  - `ApproveQuotationTaskUseCase`
- Port：
  - `FileStoragePort`
  - `QuotationTaskRepoPort`
  - `TaskDispatchPort`

### 目标结果
- route 仅接收输入并调用 usecase
- 队列位置计算、审批状态转换下沉 usecase

---

## 6.3 task 型业务线（document/pdf/image）

### 当前问题
- 多文件重复“提交/状态/结果/取消”流程
- 异常映射风格不一致

### 拆分方案

- 通用 usecase：
  - `SubmitTaskUseCase`
  - `QueryTaskUseCase`
  - `CancelTaskUseCase`
- 业务特化通过 Port 实现注入

---

## 7. 4 周落地里程碑

## 第 1 周：基础搭建

- 建立 `ports`、`usecases` 基础骨架
- 先定义首批 Protocol（>=10）
- 建立路由长度统计脚本

交付：
- ports 初版
- usecases 初版
- 架构检查脚本 v1

## 第 2 周：chat_summary 完整迁移

- 两个 endpoint 迁移完成
- 新增 usecase 单测 + fake adapter

交付：
- 瘦路由
- usecase 测试报告

## 第 3 周：quotation_generation 主链路迁移

- create/cancel/approve 三条链路迁移
- 权限与状态机规则收敛到 usecase

交付：
- 三个 usecase
- adapter 实现

## 第 4 周：任务类统一 + CI 强化

- document/pdf/image 三模块统一编排
- CI 增加依赖方向守卫

交付：
- 通用任务 usecase
- 架构守护 v2

---

## 8. 完成定义（DoD）

某业务线整改完成，至少满足：

1. endpoint <= 15 行（复杂场景 <= 20 行并备注）
2. route 不直连 integrations 具体实现
3. usecase 仅依赖 port 抽象
4. adapter 完整实现 port
5. usecase 有 fake adapter 单测
6. 架构检查在 CI 中通过

---

## 9. 风险与应对

### 风险
- 权限与 user_id 规范化规则迁移时行为偏差
- 异常语义变化影响前端兼容

### 应对
- 每条链路采用“旧实现保留 + 新实现灰度切换”
- 补充 contract tests（尤其 chat_summary / quotation）
- 关键错误码保持兼容，先做映射层再逐步收敛

---

## 10. 第一批执行任务（可直接进入开发）

- [ ] 新增 `app/ports/chat_summary.py`
- [ ] 新增 `app/ports/quotation.py`
- [ ] 新增 `app/usecases/chat_summary/*`
- [ ] 新增 `app/usecases/quotation/*`
- [ ] 重构 `app/api/v1/chat_summary.py` 为薄路由
- [ ] 重构 `app/api/v1/quotation_generation.py` 为薄路由
- [ ] 修复 `app/core/middleware/monitoring.py` 反向依赖
- [ ] 在 CI 增加架构守卫脚本

---

## 附录：本方案核心原则

1. **路由薄**：只做输入输出
2. **用例厚**：只做业务编排
3. **端口稳**：抽象先行，屏蔽实现
4. **实现可换**：adapter 只做翻译
5. **规则可守**：CI 自动化约束架构方向
