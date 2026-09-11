interface FormattedApiResponse<T> {
  code: number
  message: string
  data: T
}

export interface ChatSummaryResult {
  user_id: string
  conversation_id: string
  query_count: number
  previous_summary: string | null
  new_summary: string | null
  is_first_time: boolean
  db_updated: boolean
}

export interface UserSummaryResult {
  user_id: string
  latest_summary: string | null
  exists: boolean
}

interface UseChatSummaryOptions {
  apiBaseUrl: string
  apiToken?: string
}

interface ArchiveConversationPayload {
  userId: string
  conversationId: string
  limit?: number
}

const normalizeBaseUrl = (apiBaseUrl: string): string => apiBaseUrl.replace(/\/$/, '')

const createHeaders = (apiToken?: string): HeadersInit => {
  return {
    'Content-Type': 'application/json',
    ...(apiToken ? { Authorization: `Bearer ${apiToken}` } : {}),
  }
}

const resolveErrorMessage = async (response: Response, fallback: string): Promise<string> => {
  try {
    const json = await response.json()
    if (json && typeof json.message === 'string' && json.message) {
      return json.message
    }
    if (json && typeof json.detail === 'string' && json.detail) {
      return json.detail
    }
  } catch {
    return fallback
  }

  return fallback
}

const requestFormatted = async <T>(url: string, options: RequestInit, fallbackMessage: string): Promise<T> => {
  const response = await fetch(url, options)

  if (!response.ok) {
    const message = await resolveErrorMessage(response, fallbackMessage)
    throw new Error(message)
  }

  const result = (await response.json()) as FormattedApiResponse<T>

  if (!result || typeof result !== 'object' || !('data' in result)) {
    throw new Error(fallbackMessage)
  }

  return result.data
}

export const useChatSummary = ({ apiBaseUrl, apiToken }: UseChatSummaryOptions) => {
  const baseUrl = normalizeBaseUrl(apiBaseUrl)

  const archiveConversation = async ({
    userId,
    conversationId,
    limit = 20,
  }: ArchiveConversationPayload): Promise<ChatSummaryResult> => {
    return requestFormatted<ChatSummaryResult>(
      `${baseUrl}/chat-summary/create`,
      {
        method: 'POST',
        headers: createHeaders(apiToken),
        body: JSON.stringify({
          user_id: userId,
          conversation_id: conversationId,
          limit,
        }),
      },
      '归档失败'
    )
  }

  const loadUserSummary = async (userId: string): Promise<UserSummaryResult> => {
    return requestFormatted<UserSummaryResult>(
      `${baseUrl}/chat-summary/query/${encodeURIComponent(userId)}`,
      {
        method: 'GET',
        headers: createHeaders(apiToken),
      },
      '读取归档失败'
    )
  }

  return {
    archiveConversation,
    loadUserSummary,
  }
}