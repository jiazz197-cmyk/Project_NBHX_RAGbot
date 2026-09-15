# Issue #3 进度清单：知识库管理页新增上传能力（文档 + Excel 类数据库）

> GitLab Issue: #3（`Carl_Jia/ragchatbot`，id=3）
> 创建时间：2026-09-15
>
> 进度约定：完成一项勾选一项（`- [x]`），每项后标注提交 hash 与日期。

---

## 进度总览

| 块 | 内容 | 状态 |
|----|------|------|
| 0 | 侦察补齐（knowledge API / VectorStoreManager / 前端结构） | ✅ 完成 |
| 1 | 领域与端口（constants / DTO / Port） | ✅ 完成 |
| 2 | Adapter 能力（ExcelParser 多 sheet / metadata 查删） | ✅ 完成 |
| 3 | UseCase 编排（白名单 / 同名预检 / on_conflict） | ✅ 完成 |
| 4 | API 挂载（两个端点 + 前缀/tag） | ✅ 完成 |
| 5 | 前端（上传入口 / 进度 / 409 三选 / 普通用户视图） | ✅ 完成 |
| 6 | 测试与验收 | ⬜ 未开始 |

---

## 核心口径（执行前必读）

- **文档端点** `POST /api/v1/knowledge/documents`：txt/md/pdf/doc/docx/ppt/pptx/html/json；xlsx/xls 拒绝 422
- **Excel 端点** `POST /api/v1/knowledge/excel-db`：仅 xlsx/xls；遍历所有 sheet，sheet 名写入 chunk metadata
- **目标表**：文档 → `data_knowledge_chunks`（已定义）；Excel → 新建 `data_excel_db_chunks`
- **上传权限**：所有登录用户（Bearer JWT）；列表/删除维持 admin/superuser
- **同名冲突**：按 file_name 预检 → 409（`KNOWLEDGE_FILE_NAME_CONFLICT`）+ 摘要；`on_conflict=replace|append`
- **chunk 参数**：固定 chunk_size=500 / chunk_overlap=50，前端不暴露
- **复用资产**：`SubmitDocumentProcessingUseCase`（collection 参数化）、`ExcelHeaderPreservingSplitter`、任务进度设施
- **架构红线**：usecases 禁 import adapters；Excel 解析扩展放 adapter 层；业务异常走 `app.core.exceptions`

---

## 0. 侦察补齐

- [x] 确认 knowledge 域现有 API：`api/v1/knowledge.py` 已有 `GET/DELETE /knowledge/records`（admin/superuser），registry/prefixes/tags 已挂载
- [x] 摸清删除能力：`KnowledgePersistence` 原生 SQL（`metadata_->>'file_name'`）是既有模式；同名预检与 replace 删旧块照此实现
- [x] 确认表创建：llama_index PGVectorStore 懒建表，首次 upsert 自动建 `data_<collection>`；预检需容错表不存在
- [x] 前端现状：`KnowledgeManagePage.vue` 有列表+删除对话框；`services/knowledge.ts` 有 list/delete；缺上传/进度/409 弹窗
- [x] 确认 Command：`SubmitDocumentProcessingUseCase` 已完全 collection 参数化（chunk_size/chunk_overlap 可固定 500/50），零改动复用
- [x] Excel 多 sheet 扩展点定位：`doc_reader.py:328 ExcelParser`（`sheet_idx=0` 硬编码）；`process_document` 的 excel_splitter 链路现成
- [x] chunk metadata 字段确认：file_name / upload_time / uploader / collection 已在 JSONB

## 1. 领域与端口（纯增）

- [x] `app/adapters/knowledge/constants.py`：补 `EXCEL_DB_COLLECTION_NAME = "excel_db_chunks"`、`EXCEL_DB_CHUNKS_TABLE`
- [x] `app/domain/knowledge/upload_rules.py`：文件类型白名单（文档 9 类 / Excel 2 类）、冲突策略 replace|append、默认 chunk 500/50、大小上限、行数/chunk 上限（纯 stdlib）
- [x] `app/ports/dto/knowledge_upload.py`：`KnowledgeUploadCommand`、`ExcelDbUploadCommand`、`KnowledgeFileConflict`、`KnowledgeUploadResult`
- [x] `app/ports/outbound/knowledge_metadata.py`：`KnowledgeMetadataPort`（find_conflict / delete_chunks_by_file_name）

## 2. Adapter 能力

- [x] `ExcelParser` 多 sheet 扩展：`sheet_idx=None` 遍历所有 sheet（每个 sheet 独立 headers/rows + sheet_name），默认 `sheet_idx=0` 向后兼容
- [x] `process_document` 加 `excel_all_sheets` 开关；sheet 名写入 chunk metadata（`sheet_name` 键）；行数/chunk 上限保护（超限抛 DocumentProcessingError）
- [x] `pipeline.process`：`collection == excel_db_chunks` 时自动开启多 sheet 模式（usecase/worker 零改动）
- [x] `VectorMetadataAdapter`（`adapters/knowledge/metadata.py`）：实现 `KnowledgeMetadataPort`，原生 SQL `metadata_->>'file_name'`，懒建表容错 + collection 名校验防注入

## 3. UseCase 编排

- [x] `UploadKnowledgeDocumentUseCase`：白名单校验（xlsx/xls 提示走 Excel 入口）、大小上限、同名预检（409）、on_conflict 处理，复用 SubmitDocumentProcessingUseCase(collection=knowledge_chunks)
- [x] `UploadExcelDbUseCase`：仅 xlsx/xls、大小上限、同名预检，collection=excel_db_chunks（多 sheet 由 collection 驱动）
- [x] 新增 `KnowledgeFileNameConflictError`（409 + KNOWLEDGE_FILE_NAME_CONFLICT + details 摘要）
- [x] collection 逻辑名上移到 `domain/knowledge/collections.py`（usecase 不能 import adapters 常量）；adapter constants re-export
- [x] DTO 的 on_conflict 默认改为 None（未指定时有同名报 409，指定 replace/append 时按策略处理）
- [x] 日志 `get_logger("knowledge.upload")`；异常全走 `app.core.exceptions` 子类

## 4. API 挂载

- [x] `app/api/v1/knowledge.py`：`POST /knowledge/documents`、`POST /knowledge/excel-db`（`Depends(get_current_user)`，所有登录用户；on_conflict Query 参数 pattern 校验）
- [x] 组合根装配：`_submit_usecase(db)` 照 document_processing 端点模式装配流水线；`VectorMetadataAdapter` 注入 usecase
- [x] `KnowledgeUploadResponse` schema（web 层）；409/422 走统一异常处理器
- [x] 路由挂载：registry/prefixes 已由 Issue #2 挂好，零改动；tags 描述更新含 upload

## 5. 前端

- [x] `services/knowledge.ts`：上传 API（apiRequestFormData）、进度轮询、on_conflict 参数
- [x] `KnowledgeManagePage.vue`：「上传文档」「上传 Excel 数据库」入口 + accept 白名单 + 多文件 + 上传中状态
- [x] 任务进度轮询 → 完成自动刷新列表
- [x] 409 冲突弹窗三选（替换/追加/取消）
- [x] 普通用户视图：列表区隐藏 + 提示；上传与进度可用
- [x] `App.vue` 侧边栏知识库入口：`isAdminOrSuperuser` → 所有登录用户
- [x] `services/api.ts`：handleApiError 读 error_code/details（409 识别）；router `/knowledge` 守卫放开为登录即可

## 6. 测试与验收

- [ ] pytest 新增：白名单 422、xlsx 走错接口 422、同名 409、replace/append 语义、多 sheet metadata
- [ ] `bash scripts/check_layered_architecture.sh` 通过
- [ ] `pnpm --filter chat type-check` 通过
- [ ] 按 Issue 7 项验收标准逐条核对（含 retriever 检索验证、普通用户 403、聊天页上传不变）

---

## 操作记录

| 日期 | 完成项 | 提交 hash |
|------|--------|-----------|
| 2026-09-15 | 建分支 + chore（tsconfig baseUrl） | f69c8d6 |
| 2026-09-15 | 阶段5 前端 | （见本次提交） |
