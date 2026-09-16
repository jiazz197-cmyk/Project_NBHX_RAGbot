# LangChain 聊天接口契约（预留版）

> 状态：接口已注册、路径与 SSE 事件名保持历史兼容；LangChain 编排实现未完成。
> 独立 RAG 容器 / RAG 侧接口边界见 [`docs/langchain-rag-container-api-contract.md`](langchain-rag-container-api-contract.md)。
> 本文是人工契约，OpenAPI 自动生成结果不能替代本文的 SSE / 归属校验 / 预留状态说明。
> 相关代码：路由 `app/api/v1/chat.py`、Port `app/ports/outbound/chat.py`、DTO `app/ports/dto/chat.py`、占位实现 `app/adapters/langchain_chat/adapter.py`。

## 0. 通用约定

### 0.1 统一错误结构

所有 `APIException` 子类由 `main.py` 统一转换为：

```json
{
  "message": "人类可读的错误信息",
  "error_code": "MACHINE_READABLE_CODE",
  "details": {}
}
```

其中 `details` 可选。参数校验失败由 FastAPI 返回 `422`，结构为 `{"detail": [...]}`；本文只要求业务错误使用统一结构。

| HTTP | error_code 示例 | 语义 |
|------|-----------------|------|
| 400 / 422 | `VALIDATION_ERROR` / FastAPI detail | 参数缺失、格式错误、约束失败 |
| 401 | `AUTHENTICATION_ERROR` / `detail` | 未携带 Bearer JWT、JWT 无效或过期、用户停用 |
| 403 | `PERMISSION_DENIED` | 普通用户访问他人会话 / 消息 / 归档 |
| 404 | `NOT_FOUND` | 会话、消息或任务不存在，或不归当前用户 |
| 429 | 限流中间件统一响应 | 用户/IP 超过限流预算，按 `Retry-After` 提示重试 |
| 501 | `CHAT_ORCHESTRATOR_NOT_CONFIGURED` | LangChain 编排器预留未配置 |
| 503 | `RATE_LIMIT_REDIS_ERROR_STATUS` / `RAG service unavailable` | Redis 限流降级或 RAG 不可用 |

预留错误固定为：

```json
{
  "message": "LangChain chat orchestrator is reserved but not configured",
  "error_code": "CHAT_ORCHESTRATOR_NOT_CONFIGURED"
}
```

前端 `frontend/apps/chat/src/services/api.ts` 将 `CHAT_ORCHESTRATOR_NOT_CONFIGURED` 映射为中文提示「LangChain 聊天编排未配置，请稍后再试」，不会误判为登录失效或网络错误。

### 0.2 认证与身份

- 所有聊天接口必须携带 `Authorization: Bearer <JWT>`。
- JWT 的 `sub` 是当前用户 UUID；后端通过 `get_current_user` 查询数据库确认用户有效。
- `user_id` 不再从请求 body 读取后直接信任；普通用户的有效 `user_id` 始终是 JWT 当前用户。
- `admin` / `superuser` 可在查询类/压缩/摘要接口通过可选 `user_id` 查询指定用户；普通用户传他人标识会得到 `403`。
- 前端禁止在 body 中传 `token`、`user`、`user_id` 作为身份凭据；搜索模式与背景信息放在 `search_mode` / `inputs`。

### 0.3 分页

- Query：`page` 从 `1` 开始；`limit` 默认 `20`，上限 `100`。
- Response：保持旧前端兼容形状 `{ "data": [...], "page": 1, "limit": 20, "has_more": false }`。
- 超过末页返回空 `data`，`has_more=false`；`has_more=true` 表示可能存在下一页。
- 会话/消息落 PostgreSQL 后，分页必须稳定排序：会话按 `updated_at DESC, id DESC`；消息按**写入顺序**（自增 `id`）分窗——`page=1` 是 `id` 最大的 `limit` 条，页内按 `id` 升序返回，便于前端直接顺序渲染。调用方传入的 `created_at` 只用于展示，不参与排序。

### 0.4 SSE 协议

实现完成后，`POST /api/v1/chat-messages` 返回 `Content-Type: text/event-stream`，每个事件格式：

```text
event: <event_name>
data: <JSON>
```

`data` 的公共字段来自 `ChatStreamEvent`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `event` | string | 事件名，与 SSE 行一致 |
| `task_id` | string | 本次生成任务 ID，stop 接口使用 |
| `conversation_id` | string | 本服务内部会话 ID |
| `content` | string | 回答文本；流式事件中为增量片段，结束/错误可为空 |
| `usage` | object? | token usage，结束事件携带 |
| `error` | object? | 错误事件结构 `{code, message, status}` |

事件名保持兼容：`message`、`agent_message`、`message_replace`、`message_end`、`workflow_finished`、`error`、`ping`。顺序：

1. 首个 `message` 事件应携带 `task_id`、`conversation_id`（回复新会话时生成内部 ID）。
2. 中间按增量发送 `message` / `agent_message` / `message_replace`。
3. 正常结束发送 `message_end`，可附 `usage`。
4. 生成失败发送 `error`。
5. 空闲超过 15 秒由 API 层发送 `ping` 心跳；客户端应忽略心跳。

当前实现状态：只有两个生成接口（`POST /chat-messages` 与 `/{task_id}/stop`）仍在进入 SSE 逻辑前抛出 501，不会返回空流或 502；会话/消息的增删改查已在主应用本地记忆库落地（见第 0.6 节）。

### 0.5 超时 / 重试 / 取消 / 限流 / 观测

- **超时**：Nginx `proxy_read_timeout 3600s`；应用层 `LANGCHAIN_CHAT_TIMEOUT_SEC` 默认 300s。LLM 调用超时应转 `error` 事件或 503/502，不返回 501 以外的成功。
- **重试**：发送类接口建议只重试未开始生成且明确的网络错误；`stop` 幂等；重命名/删除为幂等语义。
- **取消**：`POST /api/v1/chat-messages/{task_id}/stop` 由编排器标记协作取消；取消后任务状态应为 `cancelled`，已生成内容按完整消息落库，不产生半截的“同步失败”状态。
- **心跳**：SSE 空闲 15s 发送 `ping`。
- **限流**：复用现有 `RateLimitMiddleware`；429 表示用户/IP 超预算，客户端提示稍后重试。
- **日志/观测**：日志至少带 `request_id`（监控中间件）、`task_id`、`conversation_id`、`user_id`、`event`；token usage 写入任务/Redis观察者体系与消息元数据。
- **依赖**：PostgreSQL（会话、消息、任务归属）、Redis（任务状态/取消标记/限流）、RAG（检索引用）、OpenAI 兼容 LLM（生成）、对象存储（可选附件）。
- **实现状态**：`预留未实现` 的接口当前返回 501；`实现中` 表示接口可返回业务响应但数据源/LLM 仍不完整；`已完成` 表示当前代码已有真实实现。

### 0.6 预留状态表

| Method + Path | 接口 | 当前实现状态 | 未配置/联调期间返回 |
|---------------|------|--------------|----------------------|
| `POST /api/v1/chat-messages` | 发送聊天消息（SSE） | 预留未实现 | `501 CHAT_ORCHESTRATOR_NOT_CONFIGURED` |
| `POST /api/v1/chat-messages/{task_id}/stop` | 停止生成 | 预留未实现 | `501 CHAT_ORCHESTRATOR_NOT_CONFIGURED` |
| `POST /api/v1/conversations` | 创建会话 | 已完成（本地记忆库，同 ID 幂等） | 201 / 403 / 422 |
| `POST /api/v1/conversations/{conversation_id}/messages` | 追加会话消息 | 已完成（本地记忆库） | 201 / 404 / 422 |
| `GET /api/v1/conversations` | 会话列表 | 已完成（按 user_id 过滤，`{data,page,limit,has_more}`） | 200 / 403 |
| `GET /api/v1/messages` | 消息列表 | 已完成（最新一窗、升序返回） | 200 / 403 / 404 |
| `POST /api/v1/conversations/{conversation_id}/name` | 重命名会话 | 已完成 | 200 / 404 |
| `DELETE /api/v1/conversations/{conversation_id}` | 删除会话 | 已完成（消息级联删除） | 204 / 404 |
| `POST /api/v1/context-compression/compress` | 上下文压缩 | 已完成（本地消息仓储已接通，需 LLM 可用） | 200 / 502 / 503 |
| `POST /api/v1/chat-summary/create` | 创建/更新用户摘要 | 已完成（本地消息仓储已接通） | 200 / 502 |
| `GET /api/v1/chat-summary/query/{user_id}` | 查询用户摘要 | 已完成 | 200 |

---

## 1. `POST /api/v1/chat-messages`

| 项目 | 内容 |
|------|------|
| 接口名称 / OpenAPI tag | 发送聊天消息（SSE 流式） / `LangChain Chat` |
| Method + Path | `POST /api/v1/chat-messages` |
| 功能说明 | 向编排器提交一条用户问题，返回 SSE 流式回答。 |
| 认证 | Bearer JWT，`Depends(get_current_user)`。 |
| 归属校验 | `user_id` 由 JWT `sub` 推导；`conversation_id` 若传入，后续实现必须校验属于当前用户，否则 403/404。admin 不以 body 伪造身份。 |
| Request Header | `Authorization: Bearer <JWT>` 必填；`Content-Type: application/json` 必填；`Accept: text/event-stream` 建议。 |
| Request Query | 无。 |
| Request Body | `query: string` 必填，1–20000 字符；`conversation_id?: string` 可选，1–128 字符，本服务内部会话 ID；`search_mode?: string` 默认 `"本地&网络"`，示例 `本地检索` / `联网搜索` / `本地&网络`；`inputs?: object` 默认 `{}`，可放 `background` 等非鉴权字段；`response_mode?: "streaming" \| "blocking"` 默认 `"streaming"`。 |
| Body 示例 | `{"query":"去年售后费用趋势","search_mode":"本地&网络","inputs":{"background":""},"response_mode":"streaming"}` |
| Response Body | 当前未配置时：501 统一错误结构。实现后：`text/event-stream`，事件见第 0.4 节。 |
| SSE 协议 | 事件：`message` / `agent_message` / `message_replace` / `message_end` / `workflow_finished` / `error` / `ping`；data 字段见 0.4；结束以 `message_end` 或 `workflow_finished` 表示；取消后以 `message_end` 结束或 `error` 说明。 |
| 错误码 | 400/422 参数错误；401 未登录；403 会话归属不符；404 会话不存在；429 限流；501 未配置；503 RAG/依赖不可用或限流 Redis 降级。 |
| 分页 | 不适用。 |
| 超时 / 重试 / 心跳 | 见 0.5；SSE 空闲 15s 发 `ping`；客户端可对首包前的连接错误重试，生成开始后不自动重试。 |
| 取消 | 通过 `POST /api/v1/chat-messages/{task_id}/stop` 协作取消；当前任务 ID 在 `message` 事件中返回。 |
| 限流 | `RateLimitMiddleware`；429 统一提示后重试。 |
| 依赖 | PostgreSQL（会话/消息/任务）、Redis（任务状态/取消/限流）、RAG、LLM、可选对象存储。 |
| 日志与观测 | `request_id`、`task_id`、`conversation_id`、`user_id`、`event`；usage 写入消息元数据。 |
| 实现状态 | 预留未实现（占位适配器直接抛出 501）。 |
| 前端调用方 | `frontend/apps/chat/src/pages/ChatPage.vue` 的 `sendMessage()`；HTTP/SSE 客户端在 `frontend/apps/chat/src/services/chat.ts` 的 `sendChatMessage()`。 |

## 2. `POST /api/v1/chat-messages/{task_id}/stop`

| 项目 | 内容 |
|------|------|
| 接口名称 / OpenAPI tag | 停止聊天生成 / `LangChain Chat` |
| Method + Path | `POST /api/v1/chat-messages/{task_id}/stop` |
| 功能说明 | 请求协作取消指定 `task_id` 的回答流。 |
| 认证 | Bearer JWT。 |
| 归属校验 | 只能取消 JWT 当前用户发起的任务；admin 不代他人取消，除非后续显式扩展。 |
| Request Header | `Authorization: Bearer <JWT>` 必填。 |
| Request Query | 无。 |
| Request Body | 无。 |
| Response Body | 成功：`{"result":"success"}`；当前未配置：501 统一错误；任务不存在/已结束：404。 |
| SSE 协议 | 不适用（普通 JSON）。 |
| 错误码 | 401/403/404/429/501/503。 |
| 分页 | 不适用。 |
| 超时 / 重试 | 幂等；可安全重试，已结束任务仍返回 404 或成功幂等结果由实现者统一。 |
| 取消 | stop 后编排器标记取消；消息落库状态为 `cancelled`，保留已生成内容，不写“失败”终态。 |
| 限流 | 同聊天接口限流。 |
| 依赖 | Redis（取消标记/任务状态）、编排器。 |
| 日志与观测 | `request_id`、`task_id`、`user_id`、取消结果。 |
| 实现状态 | 预留未实现。 |
| 前端调用方 | `frontend/apps/chat/src/services/chat.ts` 的 `stopChatMessage()`；`ChatPage.vue` 的 `stopGeneration()`。 |

## 3. `GET /api/v1/conversations`

| 项目 | 内容 |
|------|------|
| 接口名称 / OpenAPI tag | 获取会话列表 / `LangChain Chat` |
| Method + Path | `GET /api/v1/conversations` |
| 功能说明 | 分页返回当前用户的会话列表。 |
| 认证 | Bearer JWT。 |
| 归属校验 | 普通用户只能看到 JWT 自己的会话；`admin`/`superuser` 可传 `user_id` 查询指定用户。 |
| Request Header | `Authorization: Bearer <JWT>` 必填；`Accept: application/json`。 |
| Request Query | `page?: int` 默认 1，>=1；`limit?: int` 默认 20，1–100；`user_id?: string` 仅 admin/superuser，1–128 字符。 |
| Request Body | 无。 |
| Response Body | `{"data":[{"id":"...","name":"...","user_id":"...","inputs":{},"status":"normal","introduction":"","created_at":0,"updated_at":0}],"page":1,"limit":20,"has_more":false}` |
| SSE 协议 | 不适用。 |
| 错误码 | 401/403/429/503。 |
| 分页 | `page`/`limit`/`has_more`，见 0.3；`limit` 上限 100（超出 422）。排序为 `updated_at` 倒序，同秒按 `id` 倒序兜底。 |
| 超时 / 重试 | 只读，可安全重试；超时按部署默认 HTTP 超时。 |
| 取消 | 不适用。 |
| 限流 | 普通限流。 |
| 依赖 | PostgreSQL（`chat_conversation`，按 `user_id` 过滤，`updated_at` 倒序）。 |
| 日志与观测 | `request_id`、`user_id`、分页参数、返回数量。 |
| 实现状态 | 已完成（`ListConversationsUseCase` + `SqlAlchemyChatMemoryRepositoryAdapter`）。无会话时返回 200 + `data: []`（正常响应，前端渲染空侧边栏）。 |
| 前端行为 | `ChatPage.vue` 的 `onMounted` 只调一次（`page=1&limit=20`），**当前没有分页/"加载更多" UI**，所以侧边栏最多显示最近 20 个会话。 |
| 前端调用方 | `frontend/apps/chat/src/services/chat.ts` 的 `getConversations()`；`ChatPage.vue` `onMounted()`。 |

## 4. `GET /api/v1/messages`

| 项目 | 内容 |
|------|------|
| 接口名称 / OpenAPI tag | 获取会话消息列表 / `LangChain Chat` |
| Method + Path | `GET /api/v1/messages` |
| 功能说明 | 分页返回指定会话的消息。 |
| 认证 | Bearer JWT。 |
| 归属校验 | `conversation_id` 必须属于当前用户；否则 403/404。admin 可加 `user_id` 查询他人会话，但后端仍须校验 `conversation_id` 归属。 |
| Request Header | `Authorization: Bearer <JWT>`；`Accept: application/json`。 |
| Request Query | `conversation_id: string` 必填，1–128 字符；`page?: int` 默认 1；`limit?: int` 默认 20，1–100；`user_id?: string` 仅 admin/superuser。 |
| Request Body | 无。 |
| Response Body | `{"data":[{"id":"...","conversation_id":"...","role":"user","content":"...","query":"...","answer":"...","created_at":0,"metadata":{}}],"page":1,"limit":20,"has_more":false}` |
| SSE 协议 | 不适用。 |
| 错误码 | 401/403/404/422/429/503。 |
| 分页 | 见 0.3；`page=1` 是最新一窗，数组内按时间升序；`limit` 上限 100（超出 422）。 |
| 超时 / 重试 | 只读，可安全重试。 |
| 取消 | 不适用。 |
| 限流 | 普通限流。 |
| 依赖 | PostgreSQL（`chat_message`，同时按 `user_id` 与 `conversation_id` 过滤）。 |
| 日志与观测 | `request_id`、`user_id`、`conversation_id`、返回数量。 |
| 实现状态 | 已完成；会话不存在或不属于当前用户统一 404。 |
| 前端调用方 | `frontend/apps/chat/src/services/chat.ts` 的 `getMessages()`；`ChatPage.vue` `loadChat()`。 |

## 5. `POST /api/v1/conversations/{conversation_id}/name`

| 项目 | 内容 |
|------|------|
| 接口名称 / OpenAPI tag | 重命名会话 / `LangChain Chat` |
| Method + Path | `POST /api/v1/conversations/{conversation_id}/name` |
| 功能说明 | 修改指定会话名称，可请求自动生成名称。 |
| 认证 | Bearer JWT。 |
| 归属校验 | `conversation_id` 必须属于 JWT 当前用户；普通用户不可操作他人会话；admin 查询可加 `user_id`，但写操作默认仍以 JWT 身份为归属。 |
| Request Header | `Authorization: Bearer <JWT>`；`Content-Type: application/json`。 |
| Request Query | 无（`user_id` 如后续扩展路由可加，当前实现由 JWT 推导）。 |
| Request Body | `name: string` 必填，1–255 字符；`auto_generate?: boolean` 默认 false。示例 `{"name":"售后费用分析","auto_generate":false}`。 |
| Response Body | 返回更新后 Conversation：`{"id":"...","name":"...","user_id":"...","inputs":{},"status":"normal","introduction":"","created_at":0,"updated_at":0}`。 |
| SSE 协议 | 不适用。 |
| 错误码 | 400/401/403/404/422/429/503。 |
| 分页 | 不适用。 |
| 超时 / 重试 | 幂等：相同名称重复提交结果一致。 |
| 取消 | 不适用。 |
| 限流 | 普通限流。 |
| 依赖 | PostgreSQL（`chat_conversation.name`）。 |
| 日志与观测 | `request_id`、`user_id`、`conversation_id`、新旧名称。 |
| 实现状态 | 已完成；`auto_generate=true` 且 `name` 为空时用该会话首个用户提问前 50 字生成标题。 |
| 前端调用方 | `frontend/packages/components/src/MessageList/MessageList.vue` 的 `onRenameCommit()`；包装方为 `ChatPage.vue` 的 `MessageList` props 和 `saveRename()`。 |

## 5.1 `POST /api/v1/conversations` 与 `POST /api/v1/conversations/{conversation_id}/messages`（记忆写入）

这两个写接口是短期记忆落库入口，RAG 容器生成结束后调用；字段级契约与示例见
[`docs/langchain-rag-container-api-contract.md`](langchain-rag-container-api-contract.md) §9.4。

| 项目 | 内容 |
|------|------|
| 接口名称 / OpenAPI tag | 创建会话 / 追加会话消息 · `LangChain Chat` |
| Method + Path | `POST /api/v1/conversations`；`POST /api/v1/conversations/{conversation_id}/messages` |
| 功能说明 | 新建会话；向已有会话批量追加 `user`/`assistant`/`system` 消息并返回落库结果。 |
| 认证 | Bearer JWT。 |
| 归属校验 | 会话必须属于 JWT 当前用户；`user_id` 仅 admin/superuser 可指定他人（普通用户传他人身份 403）。 |
| Request Body | 建会话：`conversation_id?`（≤64，省略则生成 uuid4）、`name?`（≤255）、`inputs?`、`user_id?`。写消息：`messages: [{role, content?, query?, answer?, created_at?, metadata?}]`（1–200 条）、`user_id?`。 |
| Response Body | 建会话：201 + Conversation 对象（同 ID 重复创建返回已存在的那个）。写消息：201 + `{"data":[Message...],"conversation_id":"...","stored":n}`。 |
| 错误码 | 401/403/404/422/429/503。 |
| 分页 | 不适用。 |
| 依赖 | PostgreSQL（`chat_conversation` / `chat_message`）。 |
| 实现状态 | 已完成。 |

## 6. `DELETE /api/v1/conversations/{conversation_id}`

| 项目 | 内容 |
|------|------|
| 接口名称 / OpenAPI tag | 删除会话 / `LangChain Chat` |
| Method + Path | `DELETE /api/v1/conversations/{conversation_id}` |
| 功能说明 | 删除会话及其归属消息（是否物理删除由实现约定，须幂等且不可越权）。 |
| 认证 | Bearer JWT。 |
| 归属校验 | 必须属于 JWT 当前用户；不可删除他人会话。 |
| Request Header | `Authorization: Bearer <JWT>`。 |
| Request Query | 无。 |
| Request Body | 无。 |
| Response Body | 成功：`204 No Content`；会话不存在或不属于当前用户：404。 |
| SSE 协议 | 不适用。 |
| 错误码 | 401/403/404/429/503。 |
| 分页 | 不适用。 |
| 超时 / 重试 | 幂等：不存在时返回 404 或成功由实现统一；建议 404 以避免猜测他人会话是否存在。 |
| 取消 | 不适用。 |
| 限流 | 普通限流。 |
| 依赖 | PostgreSQL；如附件/对象存储需级联清理。 |
| 日志与观测 | `request_id`、`user_id`、`conversation_id`、删除结果。 |
| 实现状态 | 已完成（`SqlAlchemyChatMemoryRepositoryAdapter.delete_conversation`，同事务删除会话与其消息）。 |
| 前端调用方 | `frontend/packages/components/src/MessageList/MessageList.vue` 的 `deleteConversation()`；`ChatPage.vue` 的 `deleteChat()` / `confirmDelete()`。 |

## 7. `POST /api/v1/context-compression/compress`

| 项目 | 内容 |
|------|------|
| 接口名称 / OpenAPI tag | 压缩聊天上下文 / `Context Compression` |
| Method + Path | `POST /api/v1/context-compression/compress` |
| 功能说明 | 将 recent/older 对话与背景信息交给 LangChain + OpenAI 兼容 LLM 压缩为高密度上下文。 |
| 认证 | Bearer JWT。 |
| 归属校验 | 普通用户只能传自己的 `user_id` 别名；`admin`/`superuser` 可传任意业务 `user_id`。`conversation_id` 是本服务内部会话 ID。 |
| Request Header | `Authorization: Bearer <JWT>`；`Content-Type: application/json`。 |
| Request Query | 无。 |
| Request Body | `user_id: string` 必填，1–512；`conversation_id: string` 必填，1–128 字符；`n_recent: int` 默认 5，1–100。 |
| Response Body | `{"code":200,"message":"Context compressed successfully","data":{"compressed_context":"..."}}`（`FormatJSONResponse`）。 |
| SSE 协议 | 不适用。 |
| 错误码 | 400/401/403/422/429/502 LLM/上游配置错误/503 LLM 或 Redis 不可用。 |
| 分页 | 不适用。 |
| 超时 / 重试 | LLM 调用超时/上下文窗口错误可由适配器内部降级 `max_tokens`；客户端可重试幂等压缩。 |
| 取消 | 不适用。 |
| 限流 | 普通限流。 |
| 依赖 | PostgreSQL（`chat_message`，按 `user_id` + `conversation_id` 读取；请求体自带 `recent_dialogues`/`older_dialogues` 时优先用请求体）、外部 OpenAI 兼容 LLM。 |
| 日志与观测 | `request_id`、`user_id`、`conversation_id`、压缩前后长度。 |
| 实现状态 | 已完成：本地消息仓储已接通；LLM 不可达/返回网页/超窗重试失败统一 502。 |
| 前端调用方 | `frontend/apps/chat/src/services/chat.ts` 的 `compressContext()`；`ChatPage.vue` 的 `handleCompressContext()`。 |

## 8. `POST /api/v1/chat-summary/create`

| 项目 | 内容 |
|------|------|
| 接口名称 / OpenAPI tag | 创建/更新用户聊天摘要 / `Chat Summary` |
| Method + Path | `POST /api/v1/chat-summary/create` |
| 功能说明 | 从本地消息仓储提取用户问询文本，结合旧摘要调用 LLM 生成新画像摘要并写入 `user_chat_profile`。 |
| 认证 | Bearer JWT。 |
| 归属校验 | 普通用户只能归档自己的 `user_id` 别名；`admin`/`superuser` 可指定他人 `user_id`（UUID 会查询用户表）。 |
| Request Header | `Authorization: Bearer <JWT>`；`Content-Type: application/json`。 |
| Request Query | 无。 |
| Request Body | `user_id: string` 必填；`conversation_id: string` 必填，本服务内部会话 ID；`limit?: int` 默认 20。 |
| Response Body | `{"code":200,"message":"Chat summary created successfully","data":{"user_id":"...","conversation_id":"...","query_count":0,"previous_summary":null,"new_summary":null,"is_first_time":true,"db_updated":false}}` |
| SSE 协议 | 不适用。 |
| 错误码 | 400/401/403/422/429/500 未预期异常/502 LLM 不可达或返回非 API 响应/503。 |
| 分页 | 不适用。 |
| 超时 / 重试 | 摘要生成带 LLM 调用，可重试；若消息仓储为空则直接返回，不调用 LLM。 |
| 取消 | 不适用。 |
| 限流 | 普通限流。 |
| 依赖 | PostgreSQL（`user_chat_profile` 主键为内部用户 ID=JWT `sub`；消息源为 `chat_message`）、OpenAI 兼容 LLM（`SUB_LLM_*`，留空回退 `MAIN_LLM_*`；key 用 `SUB_LLM_API_KEY`/`MAIN_LLM_API_KEY`，默认关思考）。 |
| 日志与观测 | `request_id`、`user_id`、`conversation_id`、query_count、LLM 结果长度。 |
| 实现状态 | 已完成：本地消息仓储已接通，摘要真实生成并 upsert；该会话无 query 时返回 `query_count=0/db_updated=false` 且不改动画像。 |
| 前端调用方 | `frontend/packages/components/src/ChatSummary/useChatSummary.ts` 的 `archiveConversation()`；`ChatPage.vue` 的 `archiveChat()`。 |

## 9. `GET /api/v1/chat-summary/query/{user_id}`

| 项目 | 内容 |
|------|------|
| 接口名称 / OpenAPI tag | 查询用户聊天摘要 / `Chat Summary` |
| Method + Path | `GET /api/v1/chat-summary/query/{user_id}` |
| 功能说明 | 查询指定用户最新画像摘要。 |
| 认证 | Bearer JWT。 |
| 归属校验 | 普通用户只能查自己；`admin`/`superuser` 可查任意 user_id（UUID 或用户名都会解析为内部用户 ID）。 |
| Request Header | `Authorization: Bearer <JWT>`。 |
| Request Query | 无；`user_id` 在 path 中。 |
| Request Body | 无。 |
| Response Body | `{"code":200,"message":"User summary found","data":{"user_id":"...","latest_summary":"...","exists":true}}` |
| SSE 协议 | 不适用。 |
| 错误码 | 401/403/429/500/503。 |
| 分页 | 不适用。 |
| 超时 / 重试 | 只读，可安全重试。 |
| 取消 | 不适用。 |
| 限流 | 普通限流。 |
| 依赖 | PostgreSQL（`user_chat_profile`）。 |
| 日志与观测 | `request_id`、目标 `user_id`、exists。 |
| 实现状态 | 已完成。 |
| 前端调用方 | `frontend/packages/components/src/ChatSummary/useChatSummary.ts` 的 `loadUserSummary()`；`ChatSummaryDialog` 组件。 |

## 10. 后续接入 LangChain 的施工边界

1. 只实现 `app/ports/outbound/chat.py` 的 `ChatOrchestratorPort`（**只有** `stream_message` 与 `stop` 两个方法）。
2. 替换 `app/adapters/langchain_chat/adapter.py` 中的占位方法；路由、前端路径、Nginx 与 `prefixes.py` 不再修改。
3. 会话/消息存储**已完成**：`chat_conversation` / `chat_message` 两张表 + `SqlAlchemyChatMemoryRepositoryAdapter` 同时实现 `ConversationStorePort` 与 `ChatMessageRepositoryPort`，`context_compression` / `chat_summary` 已直接读它，容器侧无需再补数据层。
4. 仍需补齐的是生成侧：任务状态观察者与 token usage 记录；SSE 事件名和 `{data,page,limit,has_more}` 响应形状不得改变。
5. 接入后把本文件「预留状态表」中剩下两条 501 说明改为已完成，保留其他契约要求。
