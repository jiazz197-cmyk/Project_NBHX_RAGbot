import { config } from '../config'
import { createChatHeaders } from './api'
import { getAuthTokenFromStorage } from './token_storage'
import type {
  SearchMode,
  SSEEvent,
  Conversation,
  ConversationsResponse,
  MessagesResponse,
  RenameConversationRequest,
} from '../types/chat'

/** SSE 流回调 */
export interface SSECallbacks {
  onMessage?: (content: string, data: SSEEvent) => void
  onEnd?: (data: SSEEvent) => void
  onError?: (error: Error) => void
}

export interface CompressContextResponse {
  data: {
    compressed_context: string
  }
  message: string
}

interface ParsedSSEBlock {
  event?: string
  data?: string
}

type RawSSEEvent = Partial<SSEEvent> & {
  event?: string
  answer?: string
  message?: string
  task_id?: string
  conversation_id?: string
  output_text?: string
  text?: string
  content?: string
  data?: unknown
  outputs?: unknown
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null

const getStringValue = (value: unknown): string | undefined =>
  typeof value === 'string' && value.length > 0 ? value : undefined

const pickFirstString = (values: unknown[]): string | undefined => {
  for (const value of values) {
    const text = getStringValue(value)
    if (text) {
      return text
    }
  }
  return undefined
}

const extractEventContent = (payload: RawSSEEvent): string | undefined => {
  const direct = pickFirstString([
    payload.answer,
    payload.output_text,
    payload.text,
    payload.content,
    payload.message,
  ])

  if (direct) {
    return direct
  }

  if (isRecord(payload.outputs)) {
    const fromOutputs = pickFirstString([
      payload.outputs.text,
      payload.outputs.content,
      payload.outputs.answer,
      payload.outputs.output_text,
      payload.outputs.result,
    ])
    if (fromOutputs) {
      return fromOutputs
    }
  }

  if (isRecord(payload.data)) {
    const fromData = pickFirstString([
      payload.data.text,
      payload.data.content,
      payload.data.answer,
      payload.data.output_text,
      payload.data.result,
      payload.data.message,
    ])
    if (fromData) {
      return fromData
    }

    if (isRecord(payload.data.outputs)) {
      return pickFirstString([
        payload.data.outputs.text,
        payload.data.outputs.content,
        payload.data.outputs.answer,
        payload.data.outputs.output_text,
        payload.data.outputs.result,
      ])
    }
  }

  return undefined
}

const extractStreamChunkContent = (payload: RawSSEEvent): string | undefined =>
  pickFirstString([
    payload.answer,
    payload.output_text,
    payload.text,
    payload.content,
  ])

const mergeStreamContent = (current: string, incoming: string): string => {
  if (!current) {
    return incoming
  }

  if (incoming.startsWith(current)) {
    return incoming
  }

  if (current.endsWith(incoming)) {
    return current
  }

  return `${current}${incoming}`
}

const parseSSEBlock = (block: string): ParsedSSEBlock => {
  const normalizedBlock = block.replace(/\r/g, '')
  const lines = normalizedBlock.split('\n')
  const dataLines: string[] = []
  let eventName: string | undefined

  for (const line of lines) {
    if (!line || line.startsWith(':')) {
      continue
    }

    if (line.startsWith('event:')) {
      eventName = line.substring(6).trim()
      continue
    }

    if (line.startsWith('data:')) {
      dataLines.push(line.substring(5).trimStart())
    }
  }

  return {
    event: eventName,
    data: dataLines.length > 0 ? dataLines.join('\n') : undefined,
  }
}

const isUUID = (value: string | undefined): value is string => {
  if (!value) {
    return false
  }
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
}

const getAuthToken = (): string => {
  return getAuthTokenFromStorage() || ''
}

const parseErrorResponseMessage = async (response: Response): Promise<string | undefined> => {
  const contentType = response.headers.get('content-type') || ''
  const isJsonResponse = contentType.toLowerCase().includes('application/json')

  if (isJsonResponse) {
    const payload = (await response.json().catch(() => null)) as
      | { message?: unknown; detail?: unknown; error?: unknown }
      | null
    return pickFirstString([payload?.message, payload?.detail, payload?.error])
  }

  const rawText = await response.text().catch(() => '')
  return rawText.trim() || undefined
}

/** POST /chat-messages，解析 SSE。 */
export const sendChatMessage = async (
  query: string,
  conversationId: string | undefined,
  settings: { user: string; search: SearchMode; background?: string },
  callbacks: SSECallbacks,
  signal?: AbortSignal
): Promise<{ taskId: string; conversationId: string }> => {
  const url = `${config.apiBaseUrl}/chat-messages`
  const user = settings.user.trim()

  if (!user) {
    throw new Error('Arg user must be provided.')
  }

  const normalizedConversationId = isUUID(conversationId) ? conversationId : undefined

  const token = getAuthToken()

  const requestBody = {
    user,
    user_id: user,
    token,
    search: settings.search,
    query,
    inputs: {
      search: settings.search,
      user_id: user,
      token,
      background: settings.background || "",
    },
    ...(normalizedConversationId ? { conversation_id: normalizedConversationId } : {}),
    response_mode: 'streaming',
  }

  const response = await fetch(url, {
    method: 'POST',
    headers: createChatHeaders(),
    body: JSON.stringify(requestBody),
    signal,
  })
  
  if (!response.ok) {
    const backendMessage = await parseErrorResponseMessage(response)
    if (response.status === 429) {
      const rateLimitHint = backendMessage ? `429 ${backendMessage}` : '429 Too Many Requests'
      throw new Error(rateLimitHint)
    }
    throw new Error(backendMessage || `HTTP ${response.status} 发送消息失败`)
  }
  
  const reader = response.body?.getReader()
  const decoder = new TextDecoder()
  
  if (!reader) {
    throw new Error('无法读取响应流')
  }
  
  let taskId = ''
  let responseConversationId = ''
  let buffer = ''
  let rawAccumulatedContent = ''
  let accumulatedContent = ''
  let hasStreamedContent = false
  let fallbackContent = ''
  let endEventEmitted = false

  const emitEndEvent = (event: RawSSEEvent) => {
    if (endEventEmitted) {
      return
    }
    endEventEmitted = true
    callbacks.onEnd?.(event as SSEEvent)
  }

  const processSSEBlock = (block: string) => {
    const parsed = parseSSEBlock(block)

    if (!parsed.data || parsed.data === '[DONE]') {
      return
    }

    let data: RawSSEEvent
    try {
      data = JSON.parse(parsed.data) as RawSSEEvent
    } catch (error) {
      if (import.meta.env.DEV) {
        console.warn('Failed to parse SSE data block:', parsed.data, error)
      }
      return
    }

    if (typeof data.task_id === 'string') {
      taskId = data.task_id
    }
    if (typeof data.conversation_id === 'string') {
      responseConversationId = data.conversation_id
    }

    const eventType = data.event ?? parsed.event ?? ''
    const isEndEvent = eventType === 'message_end' || eventType === 'workflow_finished'
    const isStreamChunkEvent =
      eventType === 'message' ||
      eventType === 'agent_message' ||
      eventType === 'message_replace'
    const isFallbackCandidateEvent = eventType === 'node_finished' || eventType === 'workflow_finished'

    if (import.meta.env.DEV) {
      console.debug('[chat-sse-event]', {
        eventType,
        isStreamChunkEvent,
        isEndEvent,
      })
    }

    if (isStreamChunkEvent) {
      const chunkContent = extractStreamChunkContent(data)
      if (typeof chunkContent === 'string' && chunkContent.length > 0) {
        rawAccumulatedContent = mergeStreamContent(rawAccumulatedContent, chunkContent)
        if (rawAccumulatedContent && rawAccumulatedContent !== accumulatedContent) {
          accumulatedContent = rawAccumulatedContent
          hasStreamedContent = true
          callbacks.onMessage?.(accumulatedContent, data as SSEEvent)
        }
      }
    }

    if (isFallbackCandidateEvent) {
      const candidate = extractEventContent(data)
      if (typeof candidate === 'string' && candidate.length > 0) {
        fallbackContent = candidate
      }
    }

    if (isEndEvent) {
      if (!hasStreamedContent && fallbackContent) {
        accumulatedContent = fallbackContent
        callbacks.onMessage?.(accumulatedContent, data as SSEEvent)
        hasStreamedContent = true
      }
      emitEndEvent(data)
    } else if (eventType === 'error') {
      callbacks.onError?.(new Error(typeof data.message === 'string' ? data.message : '请求失败'))
    }
  }
  
  try {
    for (;;) {
      const { done, value } = await reader.read()

      if (done) {
        buffer += decoder.decode()
        break
      }

      buffer += decoder.decode(value, { stream: true })

      let boundaryMatch = /\r?\n\r?\n/.exec(buffer)
      while (boundaryMatch && typeof boundaryMatch.index === 'number') {
        const eventBoundaryIndex = boundaryMatch.index
        const separatorLength = boundaryMatch[0].length
        const rawBlock = buffer.slice(0, eventBoundaryIndex)
        buffer = buffer.slice(eventBoundaryIndex + separatorLength)
        processSSEBlock(rawBlock)
        boundaryMatch = /\r?\n\r?\n/.exec(buffer)
      }
    }

    if (buffer.trim()) {
      processSSEBlock(buffer)
    }

    if (!hasStreamedContent && fallbackContent) {
      accumulatedContent = fallbackContent
      callbacks.onMessage?.(accumulatedContent, {
        event: 'message_end',
      } as SSEEvent)
    }

    if (!endEventEmitted) {
      emitEndEvent({
        event: 'message_end',
        task_id: taskId,
        conversation_id: responseConversationId,
      })
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error
    }
    callbacks.onError?.(error as Error)
    throw error
  } finally {
    reader.releaseLock()
  }
  
  return {
    taskId,
    conversationId: responseConversationId || '',
  }
}

export const compressContext = async (
  userId: string,
  conversationId: string,
  nRecent: number = 5
): Promise<CompressContextResponse> => {
  const url = `${config.apiBaseUrl}/context-compression/compress`
  const token = getAuthToken()

  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      user_id: userId,
      conversation_id: conversationId,
      n_recent: nRecent,
    }),
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.message || error.detail || '压缩上下文失败')
  }

  return response.json()
}

export const stopChatMessage = async (taskId: string): Promise<void> => {
  const url = `${config.apiBaseUrl}/chat-messages/${taskId}/stop`
  
  const response = await fetch(url, {
    method: 'POST',
    headers: createChatHeaders(),
  })
  
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.message || '停止消息生成失败')
  }
}

export const getConversations = async (
  user: string,
  page = 1,
  limit = 20
): Promise<ConversationsResponse> => {
  const normalizedUser = user.trim() || 'user'
  const url = `${config.apiBaseUrl}/conversations?user=${encodeURIComponent(normalizedUser)}&page=${page}&limit=${limit}`
  
  const response = await fetch(url, {
    method: 'GET',
    headers: createChatHeaders(),
  })
  
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.message || '获取会话列表失败')
  }
  
  return response.json()
}

export const getMessages = async (
  user: string,
  conversationId: string,
  page = 1,
  limit = 20
): Promise<MessagesResponse> => {
  const normalizedUser = user.trim() || 'user'
  const url = `${config.apiBaseUrl}/messages?user=${encodeURIComponent(normalizedUser)}&conversation_id=${conversationId}&page=${page}&limit=${limit}`
  
  const response = await fetch(url, {
    method: 'GET',
    headers: createChatHeaders(),
  })
  
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.message || '获取消息列表失败')
  }
  
  return response.json()
}

export const renameConversation = async (
  conversationId: string,
  name: string,
  autoGenerate = false
): Promise<Conversation> => {
  const url = `${config.apiBaseUrl}/conversations/${conversationId}/name`
  
  const requestBody: RenameConversationRequest = {
    name,
    auto_generate: autoGenerate,
  }
  
  const response = await fetch(url, {
    method: 'POST',
    headers: createChatHeaders(),
    body: JSON.stringify(requestBody),
  })
  
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.message || '重命名会话失败')
  }
  
  return response.json()
}
