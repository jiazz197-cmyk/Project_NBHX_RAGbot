/** LangChain 预留聊天协议类型。外部路径与 SSE 事件名兼容旧协议。 */

export type MessageRole = 'user' | 'assistant'

export interface Message {
  id?: string
  role: MessageRole
  content: string
  timestamp?: string
  conversationId?: string
}

export interface Conversation {
  id: string
  name: string
  inputs: Record<string, unknown>
  status: string
  introduction: string
  created_at: number
  updated_at: number
}

export type SearchMode = '联网搜索' | '本地检索' | '本地&网络'

export interface ChatMessageRequest {
  query: string
  search_mode: SearchMode
  inputs: Record<string, unknown>
  conversation_id?: string
  response_mode: 'streaming' | 'blocking'
}

export type SSEEventType =
  | 'message'
  | 'agent_message'
  | 'agent_thought'
  | 'message_file'
  | 'message_end'
  | 'message_replace'
  | 'workflow_finished'
  | 'error'
  | 'ping'

export interface ChatStreamUsage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export interface SSEMessageEvent {
  event: 'message' | 'agent_message' | 'message_replace'
  task_id: string
  conversation_id: string
  content: string
  created_at?: number
}

export interface SSEAgentThoughtEvent {
  event: 'agent_thought'
  task_id?: string
  conversation_id?: string
  thought?: string
  message?: string
  content?: string
  created_at?: number
}

export interface SSEMessageEndEvent {
  event: 'message_end' | 'workflow_finished'
  task_id: string
  conversation_id: string
  content?: string
  usage?: ChatStreamUsage
  metadata?: {
    usage?: ChatStreamUsage
    retriever_resources?: unknown[]
  }
}

export interface SSEErrorDetail {
  code?: string
  message?: string
  status?: number
}

export interface SSEErrorEvent {
  event: 'error'
  task_id?: string
  conversation_id?: string
  code?: string
  message?: string
  status?: number
  error?: SSEErrorDetail
}

export interface SSEPingEvent {
  event: 'ping'
}

export type SSEEvent =
  | SSEMessageEvent
  | SSEAgentThoughtEvent
  | SSEMessageEndEvent
  | SSEErrorEvent
  | SSEPingEvent

export interface ConversationsResponse {
  data: Conversation[]
  has_more: boolean
  limit: number
  page: number
}

export interface MessagesResponse {
  data: Message[]
  has_more: boolean
  limit: number
}

export interface RenameConversationRequest {
  name: string
  auto_generate?: boolean
}

export interface ApiError {
  code: string
  message: string
  status: number
  /** 后端 APIException 的 details 负载（如 409 冲突的既有文件摘要） */
  details?: unknown
}
