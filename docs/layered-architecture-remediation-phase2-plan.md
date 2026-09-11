# 分层架构整改方案（二期：彻底改造）

> 更新时间：2026-04-26  
> 审查基线：基于首批 P0 改造后的代码库状态（`docs/architecture-route-usecase-port-adapter.md`）  
> 目标：将剩余重型路由与反向依赖全部迁移至 `Route -> UseCase -> Port -> Adapter` 标准调用链。

---

## 1. 当前基线评估与待整改清单

经过第一批整改，我们确立了明确的 Port 抽象与 UseCase 编排模式。但通过静态分析，当前库中仍有以下不合规项：

### 1.1 核心数据
- API endpoint 总数：**53**
- 超过 15 行的路由数：**33**（占比约 62.2%，目标为降至 20% 以下）
- 仍直接 `import app.integrations` 的业务 API 文件数：**7** 个

### 1.2 待整改的厚路由（按长度 Top 排序）
这些路由内部混杂了权限判断（owner 校验）、执行器提交（executor_manager）、复杂状态翻译等逻辑：

1. `document_processing.py`: `submit_document_processing` (54 行), `list_tasks` (37 行)
2. `pdf2image.py`: `get_pdf_convert_task_status` (52 行), `convert_pdf_to_images` (49 行), `get_pdf_convert_task_result` (49 行)
3. `image2url.py`: `get_image_upload_task_status` (52 行), `get_image_upload_task_result` (49 行)
4. `context_compression.py`: `compress_chat_context` (52 行)
5. `file_manager.py`: `upload_file` (46 行), `search_files` (36 行), `list_files` (35 行)

### 1.3 直接引用 `app.integrations` 的路由
根据 `scripts/check_layered_architecture.sh` 扫描，以下路由违反依赖方向原则：
- `app/api/v1/document_processing.py`
- `app/api/v1/pdf2image.py`
- `app/api/v1/image2url.py`
- `app/api/v1/file_manager.py`
- `app/api/v1/context_compression.py`
- `app/api/v1/closing_form.py`
- `app/api/v1/sqlserver_queries.py`

---

## 2. 详细改造蓝图（分模块）

二期改造分为三个优先级队列，遵循“先核心后边缘”的原则。

### P0：任务型三兄弟（Doc / PDF / Image）
**现状**：这三个模块处理流高度同构：验证 -> 创建异步任务 -> 提交给线程池 -> 查询状态 -> 查询结果 -> 取消。但目前在各自 route 里重复实现 `executor_manager.get_task_future` 轮询、权限越权校验（owner_id 与 current_user 对比）、以及错误状态的 dict 转换。

**目标**：
抽取统一的 `app/ports/tasking.py`（已部分存在），并建立特化的用例。

- **Ports**: 
  - `TaskExecutionPort`（复用）
  - `TaskStatePort`（复用）
  - `TaskWorkerPort`（定义 submit 参数，不同业务实现各自的 worker dispatch）
- **UseCase**（如 `app/usecases/tasks/`）:
  - `SubmitAsyncTaskUseCase`: 处理上传验证、任务注册、提交。
  - `QueryTaskUseCase`: 处理 owner 鉴权、future 阻塞/获取与统一的状态 DTO 转换（Running / Completed / Failed / Cancelled）。
  - `CancelTaskUseCase`: 处理 owner 鉴权、取消调度队列。
- **Adapter**: 将 `background_pdf_convert_task`、`process_documents_background` 封装入各自的 Adapter 中。

### P0：文件管理器（file_manager）
**现状**：`file_manager.py` 内部直接调 `file_service.upload_stream_persist`，抛出的 RuntimeError 与 PermissionError 在路由侧做 HTTPException 的映射。搜索与列表接口中混杂了分页逻辑。

**目标**：
- **Ports**: `FileStoragePort` (S3/MinIO), `FileMetadataRepoPort` (DB)
- **UseCase**: `app/usecases/file_manager/`
  - `UploadFileUseCase`
  - `DownloadFileUseCase`（处理鉴权，返回流生成器或签发 URL）
  - `ListFilesUseCase`
  - `DeleteFileUseCase`
- **重构后**：路由只负责把 `UploadFile` 转为字节流/句柄传给 UseCase，并翻译 UseCase 抛出的统一领域异常（如 `DomainPermissionError` -> `403`）。

---

### P1：上下文压缩（context_compression）
**现状**：路由里包含复杂的 user_id 解析和对 `UserRole` 的特判逻辑。
**目标**：
- **Ports**: `ContextCompressorPort`
- **UseCase**: `CompressContextUseCase`
- 将 `normalize_self_user_identifier` 以及角色的 admin 特判下沉到 UseCase 中，外部仅传入 `auth_user` 和 `requested_user_id`。

### P1：表单与 SQL 查询（closing_form & sqlserver）
**现状**：这俩模块已经是 Thin Controller（约 20 行以内），但直接 import 了 `app.integrations.*`。
**目标**：
- 为它们声明 Protocol (如 `ClosingFormRepoPort`, `U8BomQueryPort`)。
- 虽然它们只有单纯的透传或单步调用，为了保持全局依赖方向一致，补充极为简单的 UseCase 壳子，或者至少让 Route 调用 Adapter，Adapter 实现 Port，阻断从 api 到 integrations 的静态依赖。

---

## 3. 落地里程碑

### Week 1：重型任务路由瘦身
- [ ] 补齐 `tasking` 相关的 UseCase（QueryStatus, Cancel 等）。
- [ ] 将 `pdf2image.py`, `image2url.py`, `document_processing.py` 路由缩小至 <= 15 行。
- [ ] 将这三个模块从直接调用 `executor_manager` 和 `app.integrations` 迁移为调用 UseCase。

### Week 2：文件管理与核心领域重构
- [ ] 建立 `file_manager` 的 Port 与 UseCase。
- [ ] 重构 `file_manager.py` 的 7 个 endpoint。
- [ ] 建立 `context_compression` 的 UseCase。

### Week 3：长尾 API 收尾与 CI 升级
- [ ] 重构 `closing_form` 与 `sqlserver_queries` 以切断直接依赖。
- [ ] 升级 `scripts/check_layered_architecture.sh`，将上述 7 个文件加入严格阻断名单。
- [ ] 为新增的核心 UseCase 补充 Fake Adapter 测试（尤其聚焦越权校验分支）。

---

## 4. 完成定义（DoD）

二期整改完成后，系统必须达到：
1. `check_layered_architecture.sh` 无任何警告通过（禁止 route/usecase 直连 integrations 的规则扩展至全局）。
2. 全局 >15 行的 Route 比例下降至 20% 以下。
3. 任何权限校验（如判断任务的 `owner_id` 是否等于当前请求用户，或当前用户是否为 superuser 从而能越权操作）**全部从路由消失，收敛到 UseCase 中**。
4. 任何底层报错（如 timeout, cancelled, DB 唯一键冲突）在 Route 层只能看到统一的领域异常（如 `TaskNotFoundError`, `DomainPermissionError`），Route 只做 HTTP Status Code 的机械映射。