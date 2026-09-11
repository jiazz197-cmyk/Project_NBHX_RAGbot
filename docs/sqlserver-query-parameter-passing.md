# SQL Server 查询：参数传递流程详解

本文以 `POST /api/v1/sqlserver/u8/bom-inventory` 为例，详细说明参数如何从 HTTP 请求一路传递到数据库查询。

---

## 1. 完整调用链概览

```
HTTP POST /u8/bom-inventory
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  Route: app/api/v1/sqlserver_queries.py                     │
│  ─────────────────────────────────────────────────────────  │
│  1. FastAPI 解析 JSON → U8BomInventoryRequest (Pydantic)    │
│  2. 依赖注入: _current_user = Depends(get_current_user)     │
│  3. 调用 _run_sqlserver_query(...)                          │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  UseCase: app/usecases/sqlserver_queries/run_queries.py     │
│  ─────────────────────────────────────────────────────────  │
│  RunU8BomInventoryQueryUseCase.execute(payload)             │
│      └── self._port.run(payload)                            │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  Adapter: app/adapters/sqlserver_queries.py                 │
│  ─────────────────────────────────────────────────────────  │
│  U8BomInventoryQueryAdapter.run(payload)                    │
│      └── run_u8_bom_inventory_query(payload)                │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  Integration: app/integrations/sqlserver/queries.py         │
│  ─────────────────────────────────────────────────────────  │
│  run_u8_bom_inventory_query(payload)                        │
│      └── 解析参数、执行 SQL、返回 QueryResponse              │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 各层参数传递详解

### 2.1 Route 层：HTTP → Pydantic 模型

**文件**：`app/api/v1/sqlserver_queries.py`

```python
@router.post("/u8/bom-inventory", response_model=QueryResponse)
async def query_u8_bom_inventory(
    payload: U8BomInventoryRequest,                      # ← 请求体参数
    _current_user: User = Depends(get_current_user_detached),  # ← 依赖注入
) -> QueryResponse:
    return await _run_sqlserver_query(
        RunU8BomInventoryQueryUseCase(_u8).execute,      # ← 函数引用 + adapter 注入
        payload                                          # ← payload 作为第二个参数
    )
```

**参数来源**：

| 参数 | 来源 | 说明 |
|------|------|------|
| `payload` | HTTP 请求体 JSON | FastAPI 自动解析并验证 |
| `_current_user` | `Depends(get_current_user_detached)` | FastAPI 依赖注入，从 JWT 获取当前用户 |
| `_u8` | 模块级单例 | `U8BomInventoryQueryAdapter()` 在文件顶部实例化 |

**Pydantic 模型定义**（`app/adapters/web/sqlserver.py`）：

```python
class U8BomInventoryRequest(BaseModel):
    parent_inv_codes: str | List[str] = Field(
        ...,
        description="父件编码，支持字符串（逗号/空格分隔）或数组",
    )
    max_depth: int = Field(3, ge=1, le=50, description="递归最大深度")
```

**请求示例**：

```json
POST /api/v1/sqlserver/u8/bom-inventory
{
    "parent_inv_codes": "A001, A002",
    "max_depth": 5
}
```

---

### 2.2 异步执行层：线程池调度

**文件**：`app/api/v1/sqlserver_queries.py`

```python
async def _run_sqlserver_query(func, *args) -> QueryResponse:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _sqlserver_query_executor,           # ← 线程池
        partial(func, *args)                 # ← 将参数绑定到函数
    )
```

**参数绑定过程**：

```python
# 调用时：
_run_sqlserver_query(RunU8BomInventoryQueryUseCase(_u8).execute, payload)

# 等价于：
func = RunU8BomInventoryQueryUseCase(_u8).execute  # 方法的引用（未调用）
args = (payload,)                                   # 参数元组

# partial(func, *args) 创建一个新函数：
bound_func = lambda: func(payload)

# 然后在线程池中执行：
loop.run_in_executor(_sqlserver_query_executor, bound_func)
```

**为什么要用线程池？**

SQL Server 查询是**同步阻塞**操作（`pymssql`），但 FastAPI 是异步框架。通过 `run_in_executor` 将同步查询放到线程池，避免阻塞事件循环。

---

### 2.3 UseCase 层：业务编排

**文件**：`app/usecases/sqlserver_queries/run_queries.py`

```python
class RunU8BomInventoryQueryUseCase:
    def __init__(self, port: U8BomInventoryQueryPort):
        self._port = port                    # ← 构造时注入 Port

    def execute(self, payload: U8BomInventoryRequest) -> QueryResponse:
        return self._port.run(payload)       # ← 调用时传递 payload
```

**参数流向**：

```
构造阶段：
    RunU8BomInventoryQueryUseCase(_u8)
    └── self._port = _u8  （U8BomInventoryQueryAdapter 实例）

执行阶段：
    usecase.execute(payload)
    └── self._port.run(payload)
        └── _u8.run(payload)
```

**Port 定义**（`app/ports/domains/sqlserver_queries.py`）：

```python
class U8BomInventoryQueryPort(Protocol):
    def run(self, payload: Any, *, cancel_checker: CancelChecker = None) -> Any:
        ...
```

UseCase 只依赖 **Protocol（抽象）**，不知道具体实现是哪个 Adapter。

---

### 2.4 Adapter 层：对接 Integration

**文件**：`app/adapters/sqlserver_queries.py`

```python
class U8BomInventoryQueryAdapter(U8BomInventoryQueryPort):
    def run(
        self,
        payload: Any,
        *,
        cancel_checker: Optional[Callable[[], bool]] = None,
    ) -> Any:
        return run_u8_bom_inventory_query(payload, cancel_checker=cancel_checker)
```

**参数传递**：

| 参数 | 来源 | 说明 |
|------|------|------|
| `payload` | UseCase 传入 | `U8BomInventoryRequest` 实例 |
| `cancel_checker` | 可选，用于取消 | 当前路由未使用，默认 `None` |

Adapter 的职责是**翻译**：将 Port 的方法调用转发到具体的 integration 函数。

---

### 2.5 Integration 层：实际 SQL 执行

**文件**：`app/integrations/sqlserver/queries.py`

```python
def run_u8_bom_inventory_query(
    payload: U8BomInventoryRequest,
    cancel_checker: Optional[Callable[[], bool]] = None,
) -> QueryResponse:
    # 1. 解析参数
    parent_codes = split_parent_inv_codes(payload.parent_inv_codes)
    
    # 2. 执行 SQL 查询（递归展开 BOM）
    raw_rows = _query_u8_bom_inventory(
        parent_codes, 
        payload.max_depth, 
        cancel_checker=cancel_checker
    )
    
    # 3. 格式化输出
    rows = format_u8_output_rows(raw_rows)
    
    # 4. 返回结果
    return QueryResponse(total=len(rows), items=rows)
```

**参数使用**：

```python
# payload.parent_inv_codes 可以是字符串或列表
"parent_inv_codes": "A001, A002"     # → ["A001", "A002"]
"parent_inv_codes": ["A001", "A002"] # → ["A001", "A002"]

# payload.max_depth 控制递归深度
"max_depth": 5  # → 递归展开 5 层 BOM
```

---

## 3. 参数传递图解

### 3.1 构造阶段（依赖注入）

```
模块加载时：
─────────────────────────────────────────────────────────────
_u8 = U8BomInventoryQueryAdapter()    # 创建 Adapter 单例
_pdm = PdmBomInventoryQueryAdapter()  # 创建 Adapter 单例
─────────────────────────────────────────────────────────────

请求到达时：
─────────────────────────────────────────────────────────────
RunU8BomInventoryQueryUseCase(_u8)
        │
        ▼
usecase._port = _u8    # 注入 Adapter
─────────────────────────────────────────────────────────────
```

### 3.2 执行阶段（参数传递）

```
HTTP Request Body
        │
        ▼
┌─────────────────────────────────────┐
│  payload: U8BomInventoryRequest     │
│  ├── parent_inv_codes: str | List   │
│  └── max_depth: int = 3             │
└─────────────────────────────────────┘
        │
        ▼  Route 层
┌─────────────────────────────────────┐
│  _run_sqlserver_query(              │
│      RunU8BomInventoryQueryUseCase(_u8).execute,  │
│      payload                        │
│  )                                  │
└─────────────────────────────────────┘
        │
        ▼  UseCase 层
┌─────────────────────────────────────┐
│  usecase.execute(payload)           │
│      └── self._port.run(payload)    │
└─────────────────────────────────────┘
        │
        ▼  Adapter 层
┌─────────────────────────────────────┐
│  adapter.run(payload)               │
│      └── run_u8_bom_inventory_query(payload)  │
└─────────────────────────────────────┘
        │
        ▼  Integration 层
┌─────────────────────────────────────┐
│  split_parent_inv_codes(payload.parent_inv_codes)  │
│  _query_u8_bom_inventory(parent_codes, payload.max_depth)  │
│  return QueryResponse(total, items) │
└─────────────────────────────────────┘
```

---

## 4. 与其他案例对比

### 4.1 本案例（SQL Server 查询）

**特点**：Adapter 在模块顶部实例化为单例，UseCase 在每次请求时创建。

```python
# 模块级单例
_u8 = U8BomInventoryQueryAdapter()

# 每次请求创建 UseCase
usecase = RunU8BomInventoryQueryUseCase(_u8)
```

### 4.2 聊天摘要案例（需要 db session）

**特点**：Adapter 需要请求级别的 `db` session，因此每次请求都创建新 Adapter。

```python
# 每次请求创建 Adapter（因为需要 db）
user_lookup = SqlAlchemyUserLookupAdapter(db)
chat_archive = MessageExtractorChatArchiveAdapter(api_key=settings.CHAT_API_KEY)

# 创建 UseCase 并注入
usecase = CreateChatSummaryUseCase(user_lookup=user_lookup, chat_archive=chat_archive)
```

### 4.3 报价任务案例（多 Port 编排）

**特点**：一次请求注入多个 Port 实现。

```python
usecase = CreateQuotationTaskUseCase(
    task_state=TaskManagerStateAdapter(),
    task_repo=SqlAlchemyQuotationTaskRepoAdapter(db),
    file_storage=MinioFileStorageAdapter(),
    task_execution=ThreadPoolTaskExecutionAdapter(),
    task_dispatch=QuotationDispatchAdapter(),
)
```

---

## 5. 关键设计原则

| 原则 | 说明 |
|------|------|
| **依赖注入** | UseCase 通过构造函数接收 Port，不直接创建 Adapter |
| **依赖倒置** | UseCase 依赖抽象（Protocol），不依赖具体实现 |
| **单一职责** | Route 负责解析，UseCase 负责编排，Adapter 负责对接 |
| **参数透传** | `payload` 从 Route → UseCase → Adapter → Integration 逐层透传 |

---

## 6. 调试技巧

### 6.1 查看请求参数

在 Route 层添加日志：

```python
@router.post("/u8/bom-inventory")
async def query_u8_bom_inventory(payload: U8BomInventoryRequest, ...):
    logger.info(f"收到请求: parent_inv_codes={payload.parent_inv_codes}, max_depth={payload.max_depth}")
    ...
```

### 6.2 查看 SQL 执行

在 Integration 层已有日志：

```python
logger.info(
    "U8 查询完成: parent_inv_codes=%s, raw_rows=%s, output_rows=%s",
    parent_codes,
    len(raw_rows),
    len(rows),
)
```

### 6.3 单元测试示例

由于 UseCase 只依赖 Port，可以轻松 mock：

```python
from unittest.mock import Mock
from app.adapters.web.sqlserver import U8BomInventoryRequest, QueryResponse

def test_u8_query_usecase():
    # 创建 mock Port
    mock_port = Mock()
    mock_port.run.return_value = QueryResponse(total=1, items=[{"code": "A001"}])
    
    # 注入 mock
    usecase = RunU8BomInventoryQueryUseCase(mock_port)
    
    # 执行
    payload = U8BomInventoryRequest(parent_inv_codes="A001", max_depth=3)
    result = usecase.execute(payload)
    
    # 验证
    assert result.total == 1
    mock_port.run.assert_called_once_with(payload)
```
