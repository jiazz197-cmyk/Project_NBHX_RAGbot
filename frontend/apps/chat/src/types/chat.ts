/** 与 Dify 聊天 API 对接用的类型 */

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
  user: string
  user_id: string
  search: SearchMode
  query: string
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
  | 'error'
  | 'ping'

export interface SSEMessageEvent {
  event: 'message' | 'agent_message' | 'message_replace'
  task_id: string
  id: string
  conversation_id: string
  answer: string
  created_at: number
}

export interface SSEAgentThoughtEvent {
  event: 'agent_thought'
  task_id: string
  id?: string
  conversation_id?: string
  thought?: string
  message?: string
  answer?: string
  created_at?: number
}

export interface SSEMessageEndEvent {
  event: 'message_end'
  task_id: string
  id: string
  conversation_id: string
  metadata: {
    usage: {
      prompt_tokens: number
      completion_tokens: number
      total_tokens: number
    }
    retriever_resources?: unknown[]
  }
}

export interface SSEErrorEvent {
  event: 'error'
  task_id: string
  code: string
  message: string
  status: number
}

export type SSEEvent = SSEMessageEvent | SSEAgentThoughtEvent | SSEMessageEndEvent | SSEErrorEvent

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
}
