# Route → UseCase → Port（Protocol）→ Adapter 调度方式说明

本文说明本仓库采用的一条**主干调用链**：HTTP 入站请求只负责边缘职责，业务编排集中在用例，对外部世界的能力以 **Port（`typing.Protocol`）** 抽象，**Adapter** 将 Port 与具体技术栈（数据库、消息队列、对象存储、LLM 等）对接。

**相关**：若需将 **Container / Provider / Inject / Wire** 等常见 DI 术语与 Port、UseCase、Adapter、Router 对应起来（避免“某一层=容器”的误读），见 [di-and-layered-architecture.md](di-and-layered-architecture.md)。

---

## 1. 这条链路在解决什么问题

在单体 Web 服务里，常见风险是“路由很厚”：一个 endpoint 里同时出现参数解析、鉴权、数据库查询、第三方 SDK 调用、任务调度与错误映射。结果是：

- 业务规则与框架、IO 细节缠在一起，难以单测、难以复用。
- 换存储或换集成方时改动面大，且容易漏改。

本调度方式把**依赖方向**固定为：越靠内越稳定（业务意图与抽象），越靠外越易变（具体实现与基础设施）。

---

## 2. 四层各做什么（职责边界）

| 层级 | 典型位置 | 职责 | 不做什么 |
|------|-----------|------|--------|
| **Route** | `app/api/v1/` | 解析/校验入参、注入依赖（`Depends`）、**组装 Adapter**、**构造并调用** `UseCase`、将领域结果映射为 API 响应。 | 不应承载长业务流程、不应直接 `import app.integrations`（整改后的路由，见 `scripts/check_layered_architecture.sh`）。 |
| **UseCase** | `app/usecases/` | **编排**业务步骤、组合规则、对调用方返回稳定的命令/查询结果。通过 **构造函数或方法参数** 接收 Port 依赖。 | 不应直接依赖 `app.integrations` 或 ORM/HTTP client 等具体实现。 |
| **Port** | `app/ports/dto`、`app/ports/contracts`、`app/ports/domains` | **DTO** 为纯数据类；**contracts** 为跨业务 `Protocol`；**domains** 为业务线出站 `Protocol`。示例 DTO：`ChatSummaryResult`、`QuotationTaskSnapshot`（见 `app/ports/dto/`）。 | Port 不实现 IO；**domains/contracts** 的方法签名不引用 ORM 实体；身份视图用 `CurrentUserPort` 等，而非 `User` 模型。 |
| **Adapter** | `app/adapters/`（等） | **实现** Port，把一次用例里的调用**翻译**为对现有代码（如 `app.integrations/*`、ORM、配置）的调用。 | 不把整条业务流程写进 Adapter；复杂编排仍放在 UseCase。 |

**依赖方向（示意）**：

```text
Route  ──creates──▶  Adapter  ──implements──▶  Port
   │                                            ▲
   └────────── invokes ─────────▶  UseCase  ──────┘
                    (UseCase 只认 Port，不知道具体 Adapter)
```

---

## 3. 一次请求的调度顺序（以数据流理解）

1. **Route** 收到 HTTP 请求，完成 Pydantic 校验与 `Depends`（如 `get_db`、`get_current_user`）。
2. **Route** 用当前请求上下文构造具体 **Adapter** 实例（例如把 `Session` 传进 `SqlAlchemyUserLookupAdapter`）。
3. **Route** 将 Port 型依赖注入 **UseCase**（`CreateChatSummaryUseCase(user_lookup=..., chat_archive=...)`）。
4. **UseCase** 的 `execute`（或类似入口）**只**通过 Port 调用：例如先 `UserLookupPort.resolve_effective_user_id`，再 `ChatArchivePort.update_user_profile`。
5. **Adapter** 在实现方法内部访问数据库、外部服务等，将结果填回 Port 中约定的返回类型。

控制流始终是：**Route → UseCase →（经 Port 接口）→ Adapter**；UseCase 不“向下”知道 Adapter 的类名，只依赖 **Protocol 契约**。

---

## 4. 与本仓库文件的对应关系

- **`app/ports/dto/`**：仅数据类（如 `TaskManagerTaskSnapshot`、`FileRecordDTO`、`QuotationTaskSnapshot`、`ChatSummaryResult`），无 `Protocol`。
- **`app/ports/contracts/`**：跨业务复用的 `Protocol`（如 `TaskStatePort`、`ExecutorAsyncTaskPort`、`CurrentUserPort`、`RequestMetricsPort`）。
- **`app/ports/domains/`**：按业务线的出站 `Protocol`（如 `app/ports/domains/chat_summary.py` 中的 `UserLookupPort` / `ChatArchivePort`，`domains/quotation.py` 中的 `FileStoragePort` / `QuotationTaskRepoPort`，`domains/ocr_async.py` 等）。**请从子包显式 import**；`app/ports/__init__.py` 不再做符号聚合。
- **UseCase 示例**：`app/usecases/chat_summary/create_chat_summary.py` 中 `CreateChatSummaryUseCase` 依赖 `domains.chat_summary` 的 Port 与 `contracts.identity.CurrentUserPort`。
- **Adapter 示例**：`app/adapters/chat_summary.py` 实现上述 Port，内部再调用 `app.integrations` 与 ORM。

架构守卫（CI/本地脚本）会约束：UseCase 不直接引用 `app.integrations`；已整改路由不直连 integrations。详见 `scripts/check_layered_architecture.sh` 与 `.github/workflows/layered-architecture-guard.yml`。

---

## 5. 这样调度的主要优势

1. **可测性**  
   UseCase 可针对 **Port 的 Fake/Stub 实现** 做单元测试，无需起数据库、不连外网。业务分支与错误语义在 UseCase 中集中验证。

2. **可替换性**  
   同一 `FileStoragePort` 可接 MinIO、本地文件系统或测试 double；**业务代码无需修改**，只换 Route 里注入的 Adapter。

3. **依赖方向清晰**  
   核心编排（UseCase）只依赖**抽象**（Port），避免 `core` / `usecases` 与 `integrations` 绞在一起，也便于做 grep/CI 级约束。

4. **路由变薄、意图集中**  
   Route 的读者能快速看到“入参 + 用哪几个 Adapter + 调哪个 UseCase”，复杂规则与状态机留在 UseCase，减少重复与分叉散落。

5. **演进友好**  
   新增能力时优先增加 **Port 方法** 或新 Port，再补 Adapter，最后调整 UseCase；对 API 契约的影响可分步、可审查。

6. **与“六边形/整洁架构”一致**  
   Port+Adapter 与业界常见的 **应用核心 + 端口适配器** 模型对齐，新成员能按图索骥，减少 ad-hoc 的跨层引用。

---

## 6. 使用时的注意点（简要）

- **Port 的粒度**：既要避免“上帝接口”，也要避免碎到每个 SQL 一条 Port；以**用例一次编排所需**为边界。
- **Route 与注入**：若 Adapter 变多，可逐步把“为一次请求选 Adapter 并组装 UseCase”抽成**工厂/依赖提供函数**（仍属于边缘层，UseCase 保持纯净）。
- **DTO 放哪**：与业务结果强相关的结构体放在 `app/ports/dto/`（或与 usecase 共享的纯模型中），**避免**在 `Protocol` 方法签名上暴露 ORM 实体或框架类型。

---

## 7. 本仓库中的真实案例

以下均来自当前代码：说明 **Route 如何组 Adapter、UseCase 依赖哪些 Port、Adapter 如何落到 `integrations` / 基础设施**。路径便于你在 IDE 中跳转。

### 7.1 聊天摘要：创建（`UserLookupPort` + `ChatArchivePort`）

**Route** 用当前请求的 `db` 与配置构造两个 Adapter，注入 `CreateChatSummaryUseCase`，再组 `CreateChatSummaryCommand` 并调用 `execute`：

```66:79:app/api/v1/chat_summary.py
    try:
        user_lookup = SqlAlchemyUserLookupAdapter(db)
        chat_archive = MessageExtractorChatArchiveAdapter(api_key=settings.CHAT_API_KEY)
        usecase = CreateChatSummaryUseCase(user_lookup=user_lookup, chat_archive=chat_archive)

        cmd = CreateChatSummaryCommand(
            user_id=request.user_id,
            conversation_id=request.conversation_id,
            limit=request.limit or 20,
            current_user=current_user,
        )

        logger.info(f"Creating chat summary for user {request.user_id}, conversation {request.conversation_id}")
        result = usecase.execute(cmd)
```

**UseCase** 只通过 Port 做两步：先解析有效用户，再调归档/摘要能力；**不**出现 `integrations` 或 ORM：

```23:36:app/usecases/chat_summary/create_chat_summary.py
class CreateChatSummaryUseCase:
    def __init__(self, user_lookup: UserLookupPort, chat_archive: ChatArchivePort):
        self._user_lookup = user_lookup
        self._chat_archive = chat_archive

    def execute(self, cmd: CreateChatSummaryCommand) -> ChatSummaryResult:
        effective_user_id = self._user_lookup.resolve_effective_user_id(
            requested_user_id=cmd.user_id,
            current_user=cmd.current_user,
        )
        return self._chat_archive.update_user_profile(
            user_id=effective_user_id,
            conversation_id=cmd.conversation_id,
            limit=cmd.limit,
        )
```

**Adapter** 一侧用 SQLAlchemy 做用户解析，另一侧把 `ChatArchivePort.update_user_profile` **委托**给 `app.integrations` 中已有方法，并把 dict 结果映射为 `ChatSummaryResult`（定义于 `app/ports/dto/chat_summary.py`）：

```9:12:app/adapters/chat_summary.py
from app.integrations.Chat_message_archive.message_extractor import (
    UserProfileDB,
    update_user_profile_with_new_queries,
)
```

```66:78:app/adapters/chat_summary.py
class MessageExtractorChatArchiveAdapter(ChatArchivePort):
    """Delegate summary generation workflow to existing integration service."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    def update_user_profile(self, user_id: str, conversation_id: str, limit: int) -> ChatSummaryResult:
        result = update_user_profile_with_new_queries(
            api_key=self._api_key,
            user_id=user_id,
            conversation_id=conversation_id,
            limit=limit,
        )
```

**要点**：执行「抽消息 + LLM + 写库」的仍是 **integration** 里的实现；Adapter 负责**对接到 Port 类型**，UseCase 只看见 `ChatArchivePort`。

---

### 7.2 聊天摘要：查询（`UserLookupPort` + `ChatSummaryRepoPort`）

**Route** 用 `UserProfileSummaryRepoAdapter` 作为只读存储边界，与 `UserLookup` 一起注入 `QueryUserSummaryUseCase`：

```126:134:app/api/v1/chat_summary.py
    try:
        user_lookup = SqlAlchemyUserLookupAdapter(db)
        summary_repo = UserProfileSummaryRepoAdapter()
        usecase = QueryUserSummaryUseCase(user_lookup=user_lookup, summary_repo=summary_repo)

        query = QueryUserSummaryQuery(user_id=user_id, current_user=current_user)

        logger.info(f"Querying summary for user {user_id}")
        result = usecase.execute(query)
```

**UseCase** 先解析 `effective_user_id`，再 `get_latest_summary`：

```24:38:app/usecases/chat_summary/query_user_summary.py
class QueryUserSummaryUseCase:
    def __init__(self, user_lookup: UserLookupPort, summary_repo: ChatSummaryRepoPort):
        self._user_lookup = user_lookup
        self._summary_repo = summary_repo

    def execute(self, query: QueryUserSummaryQuery) -> QueryUserSummaryResult:
        effective_user_id = self._user_lookup.resolve_effective_user_id(
            requested_user_id=query.user_id,
            current_user=query.current_user,
        )
        latest_summary = self._summary_repo.get_latest_summary(effective_user_id)
        return QueryUserSummaryResult(
            user_id=effective_user_id,
            latest_summary=latest_summary,
            exists=latest_summary is not None,
        )
```

**Adapter** `UserProfileSummaryRepoAdapter` 内部持有一个 `UserProfileDB()`，来自同一 integration 包：

```56:63:app/adapters/chat_summary.py
class UserProfileSummaryRepoAdapter(ChatSummaryRepoPort):
    """Read summary data from chat message archive profile storage."""

    def __init__(self):
        self._repo = UserProfileDB()

    def get_latest_summary(self, user_id: str):
        return self._repo.get_latest_summary(user_id)
```

---

### 7.3 报价任务：创建（多 Port 编排 + 执行器/调度在 Adapter 中）

**Route** 为一次「上传 PDF 建任务」注入 **五个** Port 实现：任务状态、任务与文件持久化、对象存储、执行器上的 owner 绑定、按 owner 触发队列：

```130:155:app/api/v1/quotation_generation.py
@router.post("/tasks", response_model=QuotationTaskSubmitResponse, summary="创建报价生成任务")
async def create_quotation_task(
    file: UploadFile = File(..., description="仅支持 PDF 文件"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> QuotationTaskSubmitResponse:
    file_data = await file.read()
    usecase = CreateQuotationTaskUseCase(
        task_state=TaskManagerStateAdapter(),
        task_repo=SqlAlchemyQuotationTaskRepoAdapter(db),
        file_storage=MinioFileStorageAdapter(),
        task_execution=ThreadPoolTaskExecutionAdapter(),
        task_dispatch=QuotationDispatchAdapter(),
    )
    result = await usecase.execute(
        CreateQuotationTaskCommand(
            file_name=file.filename,
            content_type=file.content_type,
            file_bytes=file_data,
            max_file_size=settings.MAX_FILE_SIZE,
            owner_id=str(current_user.id),
            owner_username=current_user.username,
            role_snapshot=current_user.role.value,
        )
    )
    return QuotationTaskSubmitResponse(**result.__dict__)
```

**UseCase** 在单条 `execute` 里按顺序做：校验类型/大小 → `FileStoragePort.upload_pdf` → `QuotationTaskRepoPort.create_file_record` → `TaskStatePort.create_task` → `QuotationTaskRepoPort.create_task`（领域任务行）→ `TaskExecutionPort.set_task_owner` → `TaskDispatchPort.dispatch_owner_queue` → 再算 `queue_position`。类型上全部是 Port，没有直接 `import app.integrations`：

```37:50:app/usecases/quotation/create_task.py
class CreateQuotationTaskUseCase:
    def __init__(
        self,
        task_state: TaskStatePort,
        task_repo: QuotationTaskRepoPort,
        file_storage: FileStoragePort,
        task_execution: TaskExecutionPort,
        task_dispatch: TaskDispatchPort,
    ):
        self._task_state = task_state
        self._task_repo = task_repo
        self._file_storage = file_storage
        self._task_execution = task_execution
        self._task_dispatch = task_dispatch
```

```65:104:app/usecases/quotation/create_task.py
        self._file_storage.upload_pdf(
            object_path=minio_path,
            file_bytes=cmd.file_bytes,
            content_type=content_type,
        )

        stored_file_id = self._task_repo.create_file_record(
            file_name=cmd.file_name or unique_name,
            unique_name=unique_name,
            minio_path=minio_path,
            content_type=content_type,
            file_size=len(cmd.file_bytes),
            uploader=cmd.owner_username,
        )

        task_id = await self._task_state.create_task(
            task_type="quotation_generation",
            metadata={
                "owner_id": cmd.owner_id,
                "owner_username": cmd.owner_username,
                "file_id": stored_file_id,
                "file_name": cmd.file_name or unique_name,
            },
        )

        task = self._task_repo.create_task(
            task_id=task_id,
            owner_id=cmd.owner_id,
            owner_username=cmd.owner_username,
            role_snapshot=cmd.role_snapshot,
            uploaded_file_id=stored_file_id,
            uploaded_file_name=cmd.file_name or unique_name,
            uploaded_file_minio_path=minio_path,
            uploaded_file_content_type=content_type,
            uploaded_file_size=len(cmd.file_bytes),
        )

        self._task_execution.set_task_owner(task_id, cmd.owner_id)
        self._task_dispatch.dispatch_owner_queue(cmd.owner_id)
```

**Adapter** 里才出现 `task_manager`、`executor_manager`、MinIO 上传、以及 `app.integrations.Quotation_Generation.quotation_task_workers` 的调度函数——即「执行器/队列」的**技术细节**被封装在 Port 实现中：

```11:19:app/adapters/quotation.py
from app.core.executor import executor_manager
from app.core.task_manager import task_manager
from app.core.storage import upload_stream_to_minio
from app.integrations.Quotation_Generation.quotation_task_workers import (
    dispatch_quotation_phase2,
    dispatch_quotation_queue_for_owner,
    safe_cleanup_quotation_task_files,
)
```

```161:184:app/adapters/quotation.py
class TaskManagerStateAdapter(TaskStatePort):
    async def create_task(self, task_type: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        return await task_manager.create_task(task_type=task_type, metadata=metadata)

    async def fail_task(self, task_id: str, error: str, message: str = "任务失败") -> bool:
        return await task_manager.fail_task(task_id, error, message)

    async def update_task_progress(self, task_id: str, progress: int, message: str = "") -> bool:
        return await task_manager.update_task_progress(task_id, progress, message)


class ThreadPoolTaskExecutionAdapter(TaskExecutionPort):
    def set_task_owner(self, task_id: str, owner_id: str) -> None:
        executor_manager.set_task_owner(task_id, owner_id)

    def cancel_task(self, task_id: str) -> bool:
        return executor_manager.cancel_task(task_id)


class QuotationDispatchAdapter(TaskDispatchPort):
    def dispatch_owner_queue(self, owner_id: str) -> None:
        dispatch_quotation_queue_for_owner(owner_id)

    def dispatch_phase2(self, task_id: str, owner_id: str) -> None:
        dispatch_quotation_phase2(task_id, owner_id)
```

**要点**：你问的「执行器」在这里对应 **`ThreadPoolTaskExecutionAdapter` → `executor_manager`** 与 **`TaskManagerStateAdapter` → `task_manager`** 等，它们**是 Adapter 的下游**，不是独立一层和 UseCase 平级；UseCase 只依赖 `TaskExecutionPort` / `TaskStatePort` 的抽象方法。

`cancel` / `approve` 两个 endpoint 同理：Route 里注入同一批 Adapter 类型，再调 `CancelQuotationTaskUseCase` / `ApproveQuotationTaskUseCase`（见 `app/api/v1/quotation_generation.py` 中 `cancel_quotation_task` 与 `approve_quotation_task`）。

---

### 7.4 非 HTTP 边缘：监控中间件（`RequestMetricsPort`）

不是 API Route，但同一模式：**边缘**（中间件）只依赖 `RequestMetricsPort` 的工厂，**实现**在 `PrometheusRequestMetricsAdapter`，再进入 `app.integrations.monitoring.prometheus`：

```7:7:app/core/middleware/monitoring.py
from app.adapters.monitoring import get_request_metrics
```

```12:12:app/core/middleware/monitoring.py
request_metrics = get_request_metrics()
```

```34:47:app/core/middleware/monitoring.py
            request_metrics.record_request(
                method=request.method,
                endpoint=_normalize_endpoint(request),
                status_code=500,
                duration=duration,
            )
            raise

        duration = time.perf_counter() - start_time
        request_metrics.record_request(
            method=request.method,
            endpoint=_normalize_endpoint(request),
            status_code=response.status_code,
            duration=duration,
        )
```

```9:16:app/adapters/monitoring.py
class PrometheusRequestMetricsAdapter(RequestMetricsPort):
    def record_request(self, method: str, endpoint: str, status_code: int, duration: float) -> None:
        metrics.record_request(
            method=method,
            endpoint=endpoint,
            status_code=status_code,
            duration=duration,
        )
```

---

## 8. 小结

**Route → UseCase → Port（Protocol）→ Adapter** 不是语法糖，而是把**调用方向**和**可变性**分层：HTTP 与框架在最外，**业务编排在内**，**技术细节在最外**但通过 Port 与内层对接。本仓库的 `app/ports`（`dto` / `contracts` / `domains`）是这条链路的**契约中心**；`app/usecases` 是**编排中心**；`app/adapters` 是**与现有 `integrations`、存储等打交道的边**。配合脚本与 workflow，可以在迭代中持续守住分层边界。
