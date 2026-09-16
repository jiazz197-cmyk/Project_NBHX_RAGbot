# LangChain RAG 容器接口文档（前端兼容版）

> 口径已按你的描述修订：
> - **RAG 容器只跑 RAG 核心链**：LangChain prompt、检索调用、LLM 调用、SSE 输出、停止/取消协作。
> - **Retriever 不进容器**：继续作为容器外部服务/接口提供。
> - **长短期 memory 处理不进容器**：容器只通过 Memory API 读取和写入，不直接管理记忆存储。
> - **前端路径保持旧协议**：六个聊天相关路径和 SSE 事件名不变，RAG 容器需要兼容。
> - **当前 Memory API 就是主应用**：会话/消息（短期）、画像摘要（长期）、上下文压缩（工作记忆）都已在主应用落地并可用，见第 9 节；主应用里只有两个生成接口（`POST /chat-messages` 与 `/stop`）仍是 501 预留。
>
> 本文分两部分：
> 1. **RAG 容器对前端暴露什么**（照着这部分写容器 HTTP 层）。
> 2. **RAG 容器需要调用什么外部接口**（retriever + memory + LLM）。

---

# 第一部分：RAG 容器对前端暴露的接口

## 0. 前端实际调用关系

当前前端 `frontend/apps/chat` 会在聊天过程中调用：

| 前端函数 | 请求 |
|---|---|
| `sendChatMessage()` | `POST /api/v1/chat-messages` |
| `stopChatMessage()` | `POST /api/v1/chat-messages/{task_id}/stop` |
| `getConversations()` | `GET /api/v1/conversations` |
| `getMessages()` | `GET /api/v1/messages` |
| `MessageList.vue#onRenameCommit()` | `POST /api/v1/conversations/{conversation_id}/name` |
| `MessageList.vue#deleteConversation()` | `DELETE /api/v1/conversations/{conversation_id}` |
| `compressContext()` | `POST /api/v1/context-compression/compress`（Memory API 外部） |
| `useChatSummary()` | `POST /api/v1/chat-summary/create`、`GET /api/v1/chat-summary/query/{user_id}`（Memory API 外部） |

从职责上建议两种部署方式：

### 模式 A（推荐，按你的口径）

RAG 容器只承接核心链的两个接口，Nginx 把记忆 CRUD 继续路由到 Memory API（当前即主应用）：

| Path | 归属 |
|---|---|
| `POST /api/v1/chat-messages` | RAG 容器 |
| `POST /api/v1/chat-messages/{task_id}/stop` | RAG 容器 |
| `POST /api/v1/conversations` | Memory API（主应用，已实现） |
| `POST /api/v1/conversations/{conversation_id}/messages` | Memory API（主应用，已实现） |
| `GET /api/v1/conversations` | Memory API（主应用，已实现） |
| `GET /api/v1/messages` | Memory API（主应用，已实现） |
| `POST /api/v1/conversations/{conversation_id}/name` | Memory API（主应用，已实现） |
| `DELETE /api/v1/conversations/{conversation_id}` | Memory API（主应用，已实现） |

RAG 容器在生成过程中调用外部 Retriever / Memory API，前端无感知。

### 模式 B（把 RAG 容器当作旧编排服务的替代）

RAG 容器对前端暴露全部六个路径。其中：

- `/chat-messages`、`/stop` 本地处理。
- `/conversations`、`/messages`、`/name`、`DELETE` 转发到外部 Memory API（转发时必须透传 `Authorization`）。

本文两种模式都兼容；接口契约以后端路径为标题。

## 1. 认证

- 前端统一发送：`Authorization: Bearer <JWT>`。
- JWT 由主应用签发，`sub` 是用户 UUID，可能还带 `role`、`pv` claim。
- RAG 容器必须拿到可信用户身份后才能调用 Retriever / Memory API。
- 推荐流程（待最终确认）：
  1. RAG 容器用共享的 `SECRET_KEY` / `ALGORITHM` 校验 JWT 签名、`exp`、`sub`；
  2. 需要确认用户仍有效时，用同一个 JWT 调用主应用 `GET /api/v1/auth/me`；
  3. 调 Retriever / Memory API 时原样透传 `Authorization`，避免容器自己伪造身份。
- 所有失败返回统一的 `401` / `403` 错误。

统一错误响应建议：

```json
{
  "message": "无权访问该会话",
  "error_code": "PERMISSION_DENIED"
}
```

参数校验失败可以是 FastAPI 默认的 `{"detail": [...]}`；业务错误建议统一成上面的 `message + error_code`。

---

## 2. `POST /api/v1/chat-messages`（SSE 流式）

### 2.1 功能

接收用户问题，执行 RAG 核心链，并按 SSE 流式返回回答。

### 2.2 请求

**Method + Path**

```http
POST /api/v1/chat-messages
```

**Headers**

| Header | 必填 | 说明 |
|---|---|---|
| `Authorization` | 是 | `Bearer <JWT>` |
| `Content-Type` | 是 | `application/json` |
| `Accept` | 建议 | `text/event-stream` |

**Body（前端当前实际发送）**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `query` | string | 是 | 用户问题，1–20000 字符 |
| `conversation_id` | string? | 否 | 本服务内部会话 ID；新建会话时不传 |
| `search_mode` | string | 否 | `联网搜索` / `本地检索` / `本地&网络`，默认 `本地&网络` |
| `inputs` | object | 否 | 额外输入，目前前端放 `background` 等非鉴权字段 |
| `response_mode` | string | 否 | 当前前端固定 `streaming` |

示例：

```json
{
  "query": "去年售后费用趋势如何？",
  "conversation_id": "8f14e45f-ea2a-4b1f-9c3a-1234567890ab",
  "search_mode": "本地&网络",
  "inputs": {
    "background": ""
  },
  "response_mode": "streaming"
}
```

**注意**

- 前端不再在 body 里传 `token` / `user` / `user_id` 作为可信身份；身份只来自 JWT。
- 如 body 仍出现旧字段，RAG 容器可以忽略，不要把它作为鉴权依据。
- `conversation_id` 由前端从历史事件中保存；新建会话时 RAG 容器应在 SSE 中返回新的 `conversation_id`。

### 2.3 Response：SSE 协议

HTTP 200，`Content-Type: text/event-stream`。

标准事件格式：

```text
event: <event_name>
data: <JSON>

```

`data` 公共字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `event` | string | 事件名，与 SSE 行一致 |
| `task_id` | string | 本次生成任务 ID，stop 使用 |
| `conversation_id` | string | 本服务内部会话 ID |
| `content` | string | 回答内容；流式事件中为增量片段，结束/错误可为空 |
| `usage` | object? | token usage，结束事件建议携带 |
| `error` | object? | 错误事件结构 `{code, message, status}` |

前端会从 SSE 的 `event:` 行和 data 里的 `event` 字段二选一解析，因此两者都建议带上。

**必须保持的事件名**

| event | 含义 | 备注 |
|---|---|---|
| `message` | 回答增量 | 最常用 |
| `agent_message` | agent 增量 | 兼容旧协议 |
| `message_replace` | 替换已输出内容 | 兼容旧协议 |
| `message_end` | 流正常结束 | 建议带 `usage` |
| `workflow_finished` | 工作流结束 | 兼容旧协议 |
| `error` | 生成失败 | data 里带 `error` |
| `ping` | 心跳 | 前端会忽略；建议空闲 15s 发一次 |

**推荐事件顺序**

```text
event: message
data: {"event":"message","task_id":"t-1","conversation_id":"c-1","content":"去年"}

event: message
data: {"event":"message","task_id":"t-1","conversation_id":"c-1","content":"售后费用"}

event: message_end
data: {"event":"message_end","task_id":"t-1","conversation_id":"c-1","content":"","usage":{"prompt_tokens":100,"completion_tokens":50,"total_tokens":150}}

```

**第一个 `message` 事件建议尽快带上 `task_id` 和 `conversation_id`，**前端在收到后才会保存并用于停止和后续多轮。

### 2.4 前端对内容的兼容解析

当前 `frontend/apps/chat/src/services/chat.ts` 会优先读：

```text
payload.content
```

同时兼容：

```text
payload.answer
payload.output_text
payload.text
payload.message
payload.outputs.{text,content,answer,output_text,result}
payload.data.{text,content,answer,output_text,result,message}
payload.data.outputs.*
```

所以新实现推荐统一发 `content`，旧字段作为容错即可。

前端对增量/累计混合也有容错：

- 若新片段以当前累计内容开头，则替换为累计内容；
- 若当前累计内容以新片段结尾，则忽略；
- 否则追加。

建议 RAG 容器统一发**增量片段**，最简单。

### 2.5 错误响应

如果请求在建立 SSE 之前失败（例如身份失败、参数错误），返回普通 JSON：

```json
{
  "message": "Could not validate credentials",
  "error_code": "AUTHENTICATION_ERROR"
}
```

如果流已经开始，错误用 SSE `error` 事件返回：

```text
event: error
data: {"event":"error","task_id":"t-1","conversation_id":"c-1","content":"","error":{"code":"LLM_UNAVAILABLE","message":"模型服务不可用","status":503}}
```

前端 `onError` 会读取 `data.error.message` 或 `data.message`。

---

## 3. `POST /api/v1/chat-messages/{task_id}/stop`

### 3.1 功能

协作取消正在生成的回答流。

### 3.2 请求

```http
POST /api/v1/chat-messages/t-1/stop
Authorization: Bearer <JWT>
```

无 body。

### 3.3 Response

成功：

```json
{"result": "success"}
```

任务不存在 / 已结束 / 不属于当前用户：

```json
{"message":"任务不存在、已结束或不属于当前用户","error_code":"NOT_FOUND"}
```

HTTP 404。

### 3.4 取消语义建议

- 收到 stop 后设置取消标记；
- LangChain/LLM 流应尽快停止读取并结束 SSE；
- 建议最后给前端发一个 `message_end`，或者由前端本地结束；
- 已生成的内容按完整消息落库，消息状态记为 `cancelled`，不要写 `failed`；
- stop 接口应尽量幂等。

---

## 4. `GET /api/v1/conversations`

### 4.1 功能

分页返回当前用户会话列表。

### 4.2 请求

```http
GET /api/v1/conversations?page=1&limit=20
Authorization: Bearer <JWT>
```

| Query | 类型 | 必填 | 说明 |
|---|---|---|---|
| `page` | int | 否 | 默认 1，从 1 开始 |
| `limit` | int | 否 | 默认 20，建议 1–100 |
| `user_id` | string | 否 | 仅 admin / superuser 查他人时使用 |

### 4.3 Response

```json
{
  "data": [
    {
      "id": "8f14e45f-ea2a-4b1f-9c3a-1234567890ab",
      "name": "售后费用分析",
      "user_id": "00000000-0000-0000-0000-000000000001",
      "inputs": {},
      "status": "normal",
      "introduction": "",
      "created_at": 1760000000,
      "updated_at": 1760000100
    }
  ],
  "page": 1,
  "limit": 20,
  "has_more": false
}
```

前端仅强依赖 `data[].id`、`data[].name`；其它字段兼容旧协议。

---

## 5. `GET /api/v1/messages`

### 5.1 功能

分页返回某个会话的消息。

### 5.2 请求

```http
GET /api/v1/messages?conversation_id=8f14...&page=1&limit=20
Authorization: Bearer <JWT>
```

| Query | 类型 | 必填 | 说明 |
|---|---|---|---|
| `conversation_id` | string | 是 | 本服务内部会话 ID，1–128 字符 |
| `page` | int | 否 | 默认 1 |
| `limit` | int | 否 | 默认 20，建议 1–100 |
| `user_id` | string | 否 | 仅 admin / superuser 查他人时使用 |

### 5.3 Response

```json
{
  "data": [
    {
      "id": "msg-1",
      "conversation_id": "8f14e45f-ea2a-4b1f-9c3a-1234567890ab",
      "role": "user",
      "content": "去年售后费用趋势如何？",
      "query": "去年售后费用趋势如何？",
      "answer": "",
      "created_at": 1760000000,
      "metadata": {}
    },
    {
      "id": "msg-2",
      "conversation_id": "8f14e45f-ea2a-4b1f-9c3a-1234567890ab",
      "role": "assistant",
      "content": "去年售后费用整体...",
      "query": "",
      "answer": "去年售后费用整体...",
      "created_at": 1760000010,
      "metadata": {"usage": {"total_tokens": 150}}
    }
  ],
  "page": 1,
  "limit": 20,
  "has_more": false
}
```

前端 `loadChat()` 会读取：

- `role`：只能是 `user` 或 `assistant`；
- 内容：优先 `content`，否则 assistant 读 `answer`、user 读 `query`；
- 时间：`created_at` 按 Unix 秒处理。

---

## 6. `POST /api/v1/conversations/{conversation_id}/name`

### 6.1 请求

```http
POST /api/v1/conversations/8f14.../name
Authorization: Bearer <JWT>
Content-Type: application/json

{"name":"售后费用分析","auto_generate":false}
```

### 6.2 Response

返回更新后的 Conversation：

```json
{
  "id": "8f14e45f-ea2a-4b1f-9c3a-1234567890ab",
  "name": "售后费用分析",
  "user_id": "00000000-0000-0000-0000-000000000001",
  "inputs": {},
  "status": "normal",
  "introduction": "",
  "created_at": 1760000000,
  "updated_at": 1760000200
}
```

前端 `MessageList.vue` 只检查 HTTP 2xx，然后用本地标题刷新侧边栏；但为了兼容和管理接口一致，建议返回完整 Conversation。

---

## 7. `DELETE /api/v1/conversations/{conversation_id}`

### 7.1 请求

```http
DELETE /api/v1/conversations/8f14...
Authorization: Bearer <JWT>
```

无 body。

### 7.2 Response

成功：`204 No Content`。

不存在/不属于当前用户：`404 NOT_FOUND`。

前端只检查 2xx，然后从侧边栏移除。

### 7.3 归属校验

- 普通用户只能删除/重命名自己的会话；
- `conversation_id` 若属于他人，建议返回 404 而不是 403，避免泄露存在性；
- admin 越权语义要和外部 Memory API 保持一致。

---

# 第二部分：RAG 容器需要调用的外部接口

## 8. Retriever（容器外，继续保留）

Retriever 不进容器。RAG 容器通过 HTTP 调用以下接口（当前实现于主应用 `app/api/v1/retriever.py`，后续可保持路径由独立检索服务实现）：

### 8.1 `POST /api/v1/retriever/db`

文档知识库检索：

```http
POST /api/v1/retriever/db?collection=knowledge_chunks
Authorization: Bearer <JWT>
Content-Type: application/json

{"question": "去年售后费用趋势如何？"}
```

响应：

```json
{
  "answer": "命中文档内容1\n命中文档内容2\n命中文档内容3",
  "sources": ["documents/2025_售后费用分析.pdf", "documents/售后流程说明.docx"]
}
```

| 字段 | 说明 |
|---|---|
| `answer` | 命中的文档块内容按换行拼接；RAG 容器可直接作为工具结果/Prompt 上下文 |
| `sources` | 来源标识列表，可用于前端展示或引用 |

约束：

- JWT 必填；RAG 容器应透传用户 JWT。
- `collection` 必填，模式 `^[a-z][a-z0-9_]{0,63}$`，文档库默认 `knowledge_chunks`。
- `superuser` 可访问任意集合；普通用户（含 admin）只能访问 `RETRIEVER_ALLOWED_DOCUMENT_COLLECTIONS` 白名单。
- 失败：401/403/422/429/500/503。

### 8.2 `POST /api/v1/retriever/excel`

Excel 库查询：

```http
POST /api/v1/retriever/excel?collection=excel_db_chunks
Authorization: Bearer <JWT>
Content-Type: application/json

{"question": "哪个供应商交付延期最多？"}
```

成功响应同 `/db`，但 `answer` 是 Excel 数据/分析结果的 JSON 字符串，`sources` 为空；底层会先检索 Excel 源文件，再从 MinIO 读取并转 JSON。

### 8.3 `POST /api/v1/retriever/charts`

图表分析：

```http
POST /api/v1/retriever/charts
Authorization: Bearer <JWT>
Content-Type: application/json

{"data_source": {...}, "requirements": {...}}
```

响应是图表信息数组。RAG 容器是否使用 `/charts` 取决于 LangChain chain 是否包含数据分析工具。

> 推荐：RAG 容器把 Retriever 封装成 LangChain Tool，工具输入里带上当前用户的 JWT 或内部服务身份。不要让前端直接调 Retriever，否则需要处理会话归属和 prompt 拼接。

---

## 9. Memory API（容器外的长短期记忆）

### 9.1 当前 Memory API 到底怎么运行的

**结论：当前没有一个独立的 Memory 服务。** 记忆相关逻辑都在主应用 FastAPI 里，情况如下：

#### A. 长期记忆（用户画像摘要）——已可用，数据源已接通

接口：

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/chat-summary/create` | 生成/更新某用户的长期画像摘要 |
| `GET` | `/api/v1/chat-summary/query/{user_id}` | 读取某用户最新摘要 |

当前实现：

- 代码：`app/api/v1/chat_summary.py` → `app/usecases/chat_summary/*` → `app/adapters/chat_summary.py` → `app/adapters/chat_archive/message_extractor.py`。
- 存储：PostgreSQL 表 `user_chat_profile(user_id VARCHAR(128) PRIMARY KEY, latest_summary TEXT, update_time TIMESTAMP)`，由 ORM 模型 `app/models/orm/chat.py::UserChatProfile` 管理，随启动时 `create_all` 建表。
- 访问方式：SQLAlchemy async（`SqlAlchemyUserProfileRepositoryAdapter`），不再用同步 psycopg2，也不再有"首次调用懒建表"。
- 生成逻辑：从 `ChatMessageRepositoryPort`（本地 `chat_message` 表）取该用户该会话的历史 query → 调 `settings.QWEN3_6_35B_API_URL` / `QWEN3_6_35B_MODEL` 生成 150 字以内摘要 → upsert。
- **`user_id` 主键口径**：内部用户 ID（`users.id` 字符串，即 JWT `sub`），不是 username；调用方传 UUID / username / 中文姓名都能解析到同一个键。
- LLM 不可达时返回 **502**（`EXTERNAL_SERVICE_ERROR`），且不会把错误文本写进画像；该会话没有 query 时返回 `query_count=0 / db_updated=false` 并保持画像不变。
- 鉴权：Bearer JWT；普通用户只能操作自己；admin/superuser 可指定其他 UUID/用户名。

#### B. 工作记忆（上下文压缩）——已可用，数据源已接通

接口：`POST /api/v1/context-compression/compress`

- 代码：`app/api/v1/context_compression.py` → `app/usecases/context_compression/compress.py` → `app/adapters/context_compression.py` → `ContextCompressor`。
- 逻辑：优先使用请求体里的 `recent_dialogues` / `older_dialogues`；否则从本地 `chat_message` 表取该用户该会话的历史，`recent` 取最近 `n_recent` 轮、`older` 取更早的 `n_recent*4` 轮（两段不重叠）；调用 `QWEN3_6_35B_API_URL` / `QWEN3_6_35B_MODEL` 输出三段式压缩结果。
- 当前不做持久化，只把压缩文本返回给调用方。
- LLM 不可达 / 返回网页 / 超窗且重试失败 → **502**。
- 长对话场景中，RAG 容器可以在构造 prompt 前调用它，把更早历史压成 `compressed_context`，然后作为 system/history 注入。

#### C. 短期记忆（会话/消息历史）——已实现，主应用自建本地存储

| 方法 | 路径 | 当前状态 |
|---|---|---|
| `POST` | `/api/v1/conversations` | 已实现：建会话（同 ID 重复创建幂等，他人占用返回 403） |
| `POST` | `/api/v1/conversations/{id}/messages` | 已实现：批量追加 user/assistant 消息 |
| `GET` | `/api/v1/conversations` | 已实现：分页会话列表 |
| `GET` | `/api/v1/messages` | 已实现：分页消息列表 |
| `POST` | `/api/v1/conversations/{id}/name` | 已实现：重命名 |
| `DELETE` | `/api/v1/conversations/{id}` | 已实现：删除会话（连带消息级联删除） |

关键事实：

- 表：`chat_conversation`（会话）与 `chat_message`（消息），ORM 见 `app/models/orm/chat.py`，随启动 `create_all` 建表。
- 归属：两张表都带 `user_id`（内部用户 ID = JWT `sub`），所有查询在 SQL 层按 `user_id` 过滤；跨用户读他人会话统一返回 **404**（不泄露存在性），跨用户指定 `user_id` 返回 **403**。
- 写消息的 `role` 只接受 `user` / `assistant` / `system`；`created_at` 为 epoch 秒，省略则用服务端时间。
- "读取历史消息"和"持久化新消息"现在都是真实数据层，RAG 容器可直接通过 Memory API 读写。

### 9.2 RAG 容器调用长期记忆的契约

**读取最新摘要**

```http
GET /api/v1/chat-summary/query/{user_id}
Authorization: Bearer <JWT>
```

`user_id` 可使用当前 JWT 的 `sub` UUID（推荐）。响应：

```json
{
  "code": 200,
  "message": "User summary found",
  "data": {
    "user_id": "00000000-0000-0000-0000-000000000001",
    "latest_summary": "用户关注售后费用、供应商交付...",
    "exists": true
  }
}
```

**写入/更新摘要**

```http
POST /api/v1/chat-summary/create
Authorization: Bearer <JWT>
Content-Type: application/json

{
  "user_id": "00000000-0000-0000-0000-000000000001",
  "conversation_id": "8f14e45f-ea2a-4b1f-9c3a-1234567890ab",
  "limit": 20
}
```

响应：

```json
{
  "code": 200,
  "message": "Chat summary created successfully",
  "data": {
    "user_id": "00000000-0000-0000-0000-000000000001",
    "conversation_id": "8f14e45f-ea2a-4b1f-9c3a-1234567890ab",
    "query_count": 0,
    "previous_summary": null,
    "new_summary": null,
    "is_first_time": true,
    "db_updated": false
  }
}
```

### 9.3 RAG 容器调用工作记忆（上下文压缩）的契约

```http
POST /api/v1/context-compression/compress
Authorization: Bearer <JWT>
Content-Type: application/json

{
  "user_id": "00000000-0000-0000-0000-000000000001",
  "conversation_id": "8f14e45f-ea2a-4b1f-9c3a-1234567890ab",
  "n_recent": 5
}
```

响应：

```json
{
  "code": 200,
  "message": "Context compressed successfully",
  "data": {
    "compressed_context": "**工作上下文（working context）**...\n**会话摘要（session summary）**...\n**长期记忆（durable memory）**..."
  }
}
```

RAG 容器建议：

- 历史消息较多时，先调这个接口生成压缩结果；
- 把压缩结果作为 system prompt 或 summary turn 注入 LangChain chain；
- 不要把完整长历史直接塞给 LLM。

### 9.4 短期记忆：写接口（已实现）

下面这些能力现已全部落地，RAG 容器可以直接按此调用；主应用即 Memory API。

| 能力 | 接口 | 状态 |
|---|---|---|
| 创建会话 | `POST /api/v1/conversations` | ✅ 已实现（同 ID 幂等） |
| 追加用户消息/助手消息 | `POST /api/v1/conversations/{id}/messages` | ✅ 已实现 |
| 查询会话列表 | `GET /api/v1/conversations?page&limit` | ✅ 已实现 |
| 查询消息列表 | `GET /api/v1/messages?conversation_id&page&limit` | ✅ 已实现 |
| 重命名会话 | `POST /api/v1/conversations/{id}/name` | ✅ 已实现 |
| 删除会话 | `DELETE /api/v1/conversations/{id}` | ✅ 已实现（消息级联删除） |

**创建会话**

```http
POST /api/v1/conversations
Authorization: Bearer <JWT>
Content-Type: application/json

{"conversation_id": "可选，省略则服务端生成 uuid4", "name": "会话名", "inputs": {"search_mode": "本地检索"}}
```

返回 201 + 会话对象。同一 `conversation_id` 重复创建是幂等的（返回已存在的那个）；该 ID 已被别人占用时返回 **403**。

**写入消息**

```http
POST /api/v1/conversations/{conversation_id}/messages
Authorization: Bearer <JWT>
Content-Type: application/json

{
  "messages": [
    {"role": "user", "content": "用户问题", "query": "用户问题", "created_at": 1760000000},
    {"role": "assistant", "content": "助手回答", "answer": "助手回答", "created_at": 1760000010,
     "metadata": {"task_id": "t-1", "usage": {"total_tokens": 150}}}
  ]
}
```

字段规则（均为实测行为）：

- `role` 必填，取值 `user` / `assistant` / `system`；其他值 **422**。
- `messages` 单次 **1–200 条**：空数组或超过 200 条都是 **422**。
- `content` 是主字段；只传 `query`（user）/ `answer`（assistant）时，落库的 `content` 会回退成该值（返回体里两个字段同值），所以 `content` 为空也能存。
- `created_at` 为 epoch 秒（前端按 `created_at * 1000` 渲染）；省略或传 0 用服务端时间。
- `conversation_id`（建会话时可选传）最长 64 字符，超出 **422**；省略则服务端生成 uuid4。

返回 201：

```json
{"data": [{"id": "1", "conversation_id": "…", "role": "user", "content": "用户问题",
           "query": "用户问题", "answer": "", "created_at": 1760000000, "metadata": {}}],
 "conversation_id": "…", "stored": 1}
```

错误：会话不存在或不属于当前用户 → **404**（不区分二者，避免泄露存在性）。

**分页语义**（`GET /messages`）

- `page=1` 返回**最新的** `limit` 条，数组内按时间**升序**（前端直接顺序渲染）。
- `page=2` 是更早的一窗，`has_more` 表示还有更早的消息。
- 返回字段：`{"data": [...], "page": n, "limit": n, "has_more": bool}`。
- 会话列表按 `updated_at` 倒序（最近有消息的排前面）。

> 不建议 RAG 容器直连业务表：写消息走上面的 HTTP 接口即可，归属校验与索引都由主应用负责。

---

## 10. RAG 容器出站 LLM 调用

RAG 容器内部用 LangChain 调 OpenAI 兼容 LLM：

- 配置：`LANGCHAIN_CHAT_BASE_URL`、`LANGCHAIN_CHAT_MODEL`、`LANGCHAIN_CHAT_TIMEOUT_SEC`、`LANGCHAIN_MAX_OUTPUT_TOKENS`。
- 默认复用 Qwen3.6-35B，后续可换独立网关。
- 请求按 OpenAI 兼容协议：`POST {base_url}/chat/completions`，支持 `stream: true`。
- LLM 失败建议映射为 SSE `error` 事件或 503，不要把模型错误当 500。

---

## 11. 前端需要的完整时序（推荐实现）

```text
前端                     RAG 容器                        外部 Retriever/Memory/LLM
 |  POST /chat-messages      |                                   |
 |-------------------------->|                                   |
 |                           | 校验 JWT / 获取 user_id           |
 |                           |---------------------------------->|
 |                           |  GET /api/v1/conversations        |
 |                           |  GET /api/v1/messages             |
 |                           |  GET /api/v1/chat-summary/query   |
 |                           |  调 Retriever：/retriever/db|excel |
 |                           |---------------------------------->|
 |                           |  必要时压缩上下文                  |
 |                           |---------------------------------->|
 |                           | LangChain + LLM（流式）            |
 |  SSE message / message_end |<---------------------------------|
 |<--------------------------|                                   |
 |                           |  持久化本次 user/assistant 消息    |
 |                           |---------------------------------->|
 |  POST /stop               |                                   |
 |-------------------------->| 设置取消标记，结束 SSE             |
```

---

## 12. 当前状态速查表

| 能力 | 当前在哪个服务 | 现状 | 你写 RAG 容器时需要做什么 |
|---|---|---|---|
| `POST /chat-messages` | 应迁入 RAG 容器 | 当前主应用返回 501 | 实现 SSE 核心链 |
| `POST /chat-messages/{id}/stop` | 应迁入 RAG 容器 | 当前主应用返回 501 | 实现取消标记 |
| `GET /conversations` | 主应用（本地记忆库） | ✅ 已实现 | 直接调用 |
| `POST /conversations` / `{id}/messages` | 主应用（本地记忆库） | ✅ 已实现 | 生成结束后落库 |
| `GET /messages` | 主应用（本地记忆库） | ✅ 已实现 | 直接调用 |
| `rename/delete conversation` | 主应用（本地记忆库） | ✅ 已实现 | 直接调用 |
| 长期画像摘要 | 主应用 Memory API | ✅ 可用，数据源已接通 | 读取/可选写入 |
| 上下文压缩 | 主应用 Memory API | ✅ 可用，数据源已接通 | 调用来压缩历史 |
| 短期消息持久化 | 主应用 Memory API | ✅ 已实现（`chat_conversation` / `chat_message`） | 走写消息接口 |
| Retriever | 容器外部服务 | `app/api/v1/retriever/*` 可用 | 封装成 LangChain Tool 调用 |
| BGE-M3 / Reranker | Retriever 所在服务内部 | 可用，HTTP 模型服务 | 不需要 RAG 容器直接调 |
| LLM | 外部推理网关 | 配置项已就绪，**真实网关地址待定** | RAG 容器内部直接调 OpenAI 兼容接口 |

---

## 13. 需要确认的问题

1. 前端六个路径是由 Nginx 拆给 RAG 容器 + Memory API（模式 A），还是全部由 RAG 容器承接（模式 B）？
2. RAG 容器收到 JWT 后，是本地验签，还是必须调 `GET /api/v1/auth/me` 做用户有效性校验？
3. 调 Retriever / Memory 时透传用户 JWT，还是用 `INTERNAL_API_KEY` + `X-User-Id`？
4. ~~短期记忆的"写消息"接口由哪边定义？~~ **已定**：主应用自建（见 §9.4），接口按本文字段形状实现，容器直接调用。
5. ~~长期摘要的消息源 `ChatMessageRepositoryPort` 现在返回空~~ **已解决**：`ChatMessageRepositoryPort` 现由 `SqlAlchemyChatMemoryRepositoryAdapter` 实现，直接读本地 `chat_message` 表；RAG 容器不需要把历史 query 塞给 `POST /chat-summary/create`。
6. `task_id` / `conversation_id` / 取消状态是否需要落 Redis 供跨容器协作？当前主应用的任务体系只覆盖文档/OCR。
7. `search_mode` 与 `inputs` 的最终字段名和取值，需要和前端再确认；目前前端发送的就是本文第 2.2 节的样子。
8. 新增：RAG 容器调 Retriever / Memory 时，是透传用户 JWT，还是走服务间身份？目前 `INTERNAL_API_KEY` 只在配置里定义、代码中**没有任何使用点**，所以现在只能透传用户 JWT（本文各接口都要求 Bearer JWT）。
