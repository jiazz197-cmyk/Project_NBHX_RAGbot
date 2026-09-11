# 分层架构代码审查清单

> **适用项目**：FastAPI + LangChain + SQLAlchemy 等 Python AI 应用
> **审查目标**：确保核心业务逻辑与 IO 边界严格分离，保障长期可维护性
> **审查频率**：每次 PR 或每两周一次架构巡检

---

## 一、核心层审查（core/）

> **原则**：core/ 下的代码不应 import 任何外部框架（FastAPI、LangChain、SQLAlchemy、httpx 等）

### 1.1 import 禁令检查

```python
# ❌ 禁止出现在 core/ 目录下的 import
from fastapi import ...          # HTTP 框架
from sqlalchemy import ...       # ORM
from langchain_openai import ... # LLM 供应商
from langchain_core import ...   # LangChain 框架
from redis import ...            # 缓存中间件
from httpx import ...            # HTTP 客户端
from asyncio import ...          # 异步 IO（core 层可以是纯同步）
import os                        # 环境变量（属于配置，不应在 core 中硬编码）
```

**审查检查项**：

- [ ] core/ 目录下所有文件是否无以上 import
- [ ] 如果出现外部依赖，是否确实必要（极少情况允许，需明确标注 `# ALLOWED: 原因`）
- [ ] 所有数据模型是否使用 `dataclass`、`Pydantic` 或 `TypedDict`，而非 ORM 模型

### 1.2 纯函数判定

```python
# ✅ 合格的纯函数
def calculate_token_cost(text: str, rate: float = 0.00003) -> float:
    """入参定，出参定，无副作用"""
    return len(text) * rate

# ❌ 不合格
def calculate_and_save_cost(text: str, db_session) -> float:
    """计算的同时写数据库——违背单一职责"""
    cost = len(text) * 0.00003
    db_session.add(CostRecord(amount=cost))  # ❌ IO 操作混入核心层
    return cost
```

**审查检查项**：

- [ ] 每个函数是否接受输入 → 返回输出，不产生副作用
- [ ] 是否依赖外部状态（全局变量、模块级可变对象）
- [ ] 同样输入是否总能得到同样输出（确定性）

### 1.3 Protocol 接口定义

```python
# core/ports.py — 定义契约，不引入实现

from typing import Protocol

class LLMService(Protocol):
    """LLM 服务接口——核心层定义，适配器层实现"""
    async def chat(self, messages: list[Message]) -> str: ...

class ConversationRepository(Protocol):
    """持久化接口"""
    async def get_history(self, user_id: str, limit: int = 10) -> list[Message]: ...
    async def save(self, message: Message) -> None: ...
```

**审查检查项**：

- [ ] 核心层对外部的所有依赖是否都通过 Protocol 定义
- [ ] Protocol 的方法签名是否足够抽象（不泄露实现细节）
- [ ] Protocol 是否放在独立的 ports.py 中

### 1.4 UseCase 编排

```python
# core/usecases.py — 编排业务流程，不碰 IO

async def chat_with_agent(
    request: ChatRequest,
    llm: LLMService,              # 接口注入
    repo: ConversationRepository, # 接口注入
    config: ChatConfig,           # 纯配置对象
) -> ChatResponse:
    context = build_context(request, config.max_history)
    reply = await llm.chat(context)   # 通过接口调 IO
    cost = calculate_cost(reply)      # 纯函数
    await repo.save(Message("assistant", reply))
    return ChatResponse(reply=reply, cost=cost)
```

**审查检查项**：

- [ ] UseCase 是否只依赖接口（Protocol），不依赖具体实现
- [ ] 入参是否包括所有依赖（没有从全局、环境变量或 import 获取依赖）
- [ ] 测试时能否不启动数据库和外部服务就能运行

---

## 二、适配器层审查（adapters/）

> **原则**：适配器实现 core/ports.py 中定义的接口，将外部世界翻译为核心层能理解的模型

### 2.1 接口实现完整性

```python
# ✅ 正确实现
class PostgresConversationRepo:
    """实现 ConversationRepository 接口"""
    
    async def get_history(self, user_id: str, limit: int = 10) -> list[Message]:
        rows = await self.session.execute(
            select(ConversationRow)
            .where(ConversationRow.user_id == user_id)
            .order_by(ConversationRow.created_at.desc())
            .limit(limit)
        )
        return [
            Message(role=row.role, content=row.content)
            for row in rows.scalars().all()
        ]
    
    async def save(self, message: Message) -> None:
        self.session.add(ConversationRow(
            role=message.role,
            content=message.content,
        ))
        await self.session.commit()
```

**审查检查项**：

- [ ] 是否实现了 Protocol 中声明的所有方法
- [ ] 是否将外部数据（ORM 模型、API 响应）转换为核心层数据模型
- [ ] 异常是否做了封装（不将 SQLAlchemy 异常直接抛到核心层）

### 2.2 心智负担检查

```python
# ❌ 适配器层太复杂——说明核心层定义的接口粒度不对
class MegaAdapter:
    async def do_everything(self, ...) -> ...:  # 一个方法做太多事
    async def complex_query(self, ...) -> ...:  # 核心层不应知道查询细节
```

**审查检查项**：

- [ ] 适配器方法的复杂度是否合理（超过 50 行说明接口设计可能有问题）
- [ ] 适配器层是否在做业务逻辑的判断（应该上提到核心层）

---

## 三、边缘层审查（api/、handlers/）

> **原则**：边缘层是最薄的一层，只做三件事：解析输入、调用核心层、格式化输出

### 3.1 三层不应做的事

```python
# ✅ 正确的边缘层代码——薄
@router.post("/chat")
async def chat_endpoint(
    body: ChatRequest,
    llm: OpenAIAdapter = Depends(get_llm),
    repo: PostgresRepo = Depends(get_repo),
):
    request = ChatRequest(**body)
    response = await chat_usecase(request, llm, repo)  # 一行调用核心层
    return response.dict()

# ❌ 错误的边缘层——逻辑散落
@router.post("/chat")
async def chat_endpoint(body: dict):
    # 1. 查数据库（业务逻辑）
    history = await db.query(...)
    # 2. 拼 prompt（业务逻辑）
    messages = [format_msg(m) for m in history]
    # 3. 调 LLM（IO 混在路由中）
    reply = await openai_call(...)
    # 4. 计算成本（业务逻辑）
    cost = len(reply) * 0.0003
    # 5. 写数据库（IO）
    await db.save(...)
    # 6. 返回
    return {"reply": reply}
```

**审查检查项**：

- [ ] 路由函数是否超过 10-15 行
- [ ] 路由函数中是否存在 `if/else` 业务判断
- [ ] 路由函数是否直接调用了第三方 SDK（OpenAI SDK、外部 API）
- [ ] 依赖项是否通过 `Depends()` 注入，而不是在函数内部实例化

---

## 四、测试覆盖审查

### 4.1 核心层测试（无需 mock）

```python
# ✅ 测试纯函数——不必 mock 任何东西
def test_build_context():
    request = ChatRequest("hello", [Message("user", "hi")], "u1")
    result = build_context(request, max_history=5)
    assert len(result) == 2
    assert result[-1].content == "hello"

# ❌ 测试核心层时出现了 mock
@patch("openai.ChatCompletion.create")
def test_build_context(mock_openai):
    # ... 说明核心层混入了外部依赖
```

**审查检查项**：

- [ ] core/ 下的纯函数是否无需 mock 即可测试
- [ ] usecase 测试是否使用 Fake 实现而非 mock.patch
- [ ] 核心层测试覆盖率是否 > 90%

### 4.2 适配器层测试（适量 mock）

```python
# ✅ 适配器层——测试与外部服务的交互
async def test_openai_adapter_retries():
    with patch("openai.resources.chat.completions.AsyncCompletions.create",
               side_effect=[Exception("timeout"), fake_response]):
        adapter = OpenAIAdapter(api_key="test")
        result = await adapter.chat([Message("user", "hello")])
        assert result == "expected"
```

**审查检查项**：

- [ ] 适配器测试是否覆盖了超时、重试、异常场景
- [ ] 适配器测试是否只 mock 了边界点的外部调用
- [ ] 是否准备了内存实现（InMemoryRepo）用于集成测试

---

## 五、类型安全审查

### 5.1 mypy 检查项

- [ ] 项目是否启用 `mypy --strict`
- [ ] 核心层所有函数是否有完整类型注解
- [ ] Protocol 接口是否被实现方完整遵循
- [ ] 是否存在过多的 `type: ignore`（应少于总数的 0.5%）
- [ ] `Any` 类型使用是否限于适配器层与外部 SDK 的边界

```python
# ✅ 核心层：不用 Any
def calculate(text: str) -> float: ...

# ✅ 适配器边界：合理的 Any 使用（外部 SDK 返回）
def parse_response(data: dict[str, Any]) -> Message:
    return Message(role=data["role"], content=str(data["content"]))
```

### 5.2 Pydantic 检查项

- [ ] 所有外部输入是否经过 Pydantic 模型校验
- [ ] 是否使用了 `model_validator` 做交叉字段校验
- [ ] 错误消息是否对用户友好（不暴露内部实现）

---

## 六、依赖方向审查（架构守护）

> **依赖规则**：依赖只能从外指向内

```
适配器层 → 核心层   ✅ 允许
边缘层   → 核心层   ✅ 允许
边缘层   → 适配器层 ✅ 允许（通过 DI 容器）
核心层   → 适配器层 ❌ 禁止
核心层   → 边缘层   ❌ 禁止
核心层   → 任何外部包 ❌ 禁止（纯 Python 标准库除外）
```

**审查检查项**：

- [ ] 是否存在反向依赖（core/ import adapters/ 或 api/）
- [ ] 是否存在循环依赖
- [ ] 是否可以用 `pytest-arch` 或自定义脚本自动检查依赖方向

---

## 七、审查评分卡

| 维度 | 权重 | 评分标准 |
|------|------|---------|
| 核心层纯度 | 25% | core/ 无外部依赖 10 分，有违规扣分 |
| 接口定义 | 20% | Protocol 完善且粒度合理 |
| 边缘层厚度 | 15% | 路由函数 ≤ 15 行 |
| 测试分层 | 20% | 核心层零 mock 测试覆盖 |
| 类型安全 | 10% | mypy --strict 通过 |
| 依赖方向 | 10% | 无反向依赖 |

**评级**：
- ≥ 90 分：架构健康
- 70-89 分：需要局部重构
- < 70 分：建议架构整改后再迭代新功能

---

## 八、快速自查脚本

可在 CI 中添加以下检查：

```bash
# 检查核心层是否引入了外部依赖
! grep -r "^from fastapi\|^from sqlalchemy\|^from langchain\|^from openai\|^import requests\|^import httpx" core/ --include="*.py"

# 检查核心层测试是否使用了 mock
! grep -r "mock\|@patch\|MagicMock\|AsyncMock" tests/core/ --include="*.py"

# 检查路由文件行数是否合理（超 200 行预警）
find api/ -name "*.py" -exec wc -l {} \; | awk '$1 > 200 {print $0 "  ⚠️  过长"}'
```

---

> **最后提醒**：这份清单的价值不在于每一行都做到完美，而在于**团队形成共识**——知道什么代码好，什么代码需要改，为什么。
