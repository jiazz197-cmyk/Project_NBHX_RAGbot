import { config } from '../config'
import type { ApiError } from '../types/chat'
import { clearAuthTokenFromStorage, getAuthTokenFromStorage } from './token_storage'

const DEBUG_API_DIAGNOSTICS = import.meta.env.DEV
const FETCH_PENDING_WARN_MS = 20000
let authorizedFetchSeq = 0

const logApiDiag = (event: string, details?: Record<string, unknown>): void => {
  if (!DEBUG_API_DIAGNOSTICS) return
  try {
    console.info('[ApiDiag]', {
      event,
      ts: new Date().toISOString(),
      ...details,
    })
  } catch {
    // ignore logging failures
  }
}

const logApiDiagError = (event: string, error: unknown, details?: Record<string, unknown>): void => {
  if (!DEBUG_API_DIAGNOSTICS) return
  try {
    const cast = error as { message?: string; stack?: string; name?: string }
    console.error('[ApiDiagError]', {
      event,
      ts: new Date().toISOString(),
      errorName: cast?.name,
      errorMessage: cast?.message,
      errorStack: cast?.stack,
      ...details,
    })
  } catch {
    // ignore logging failures
  }
}

const handleUnauthorized = (): void => {
  clearAuthTokenFromStorage()
  try {
    localStorage.removeItem(config.settingsStorageKey)
  } catch {
    // 本地存储不可用则忽略
  }

  if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
    window.location.href = '/login'
  }
}

const getAuthToken = (): string | null => {
  return getAuthTokenFromStorage()
}

/** 聊天走代理时只带 JSON，鉴权在网关处理。 */
export const createChatHeaders = (): HeadersInit => {
  return {
    'Content-Type': 'application/json',
  }
}

/** 业务 API：Bearer 来自登录，不用 Chat API Key。 */
export const createHeaders = (): HeadersInit => {
  return createAuthHeaders({ jsonContentType: true })
}

export const createAuthHeaders = (options?: { jsonContentType?: boolean }): HeadersInit => {
  const token = getAuthToken()
  if (!token) {
    throw new Error('未登录或登录态已失效，请重新登录')
  }

  const headers: HeadersInit = {
    Authorization: `Bearer ${token}`,
  }

  if (options?.jsonContentType !== false) {
    (headers as Record<string, string>)['Content-Type'] = 'application/json'
  }

  return headers
}

export const handleApiError = async (response: Response): Promise<never> => {
  let error: ApiError

  try {
    const payload = await response.json()
    error = {
      code: String((payload as { code?: unknown })?.code ?? 'api_error'),
      message: String((payload as { message?: unknown; detail?: unknown })?.message ?? (payload as { detail?: unknown })?.detail ?? '请求失败'),
      status: response.status,
    }
  } catch {
    error = {
      code: 'unknown_error',
      message: '网络请求失败',
      status: response.status,
    }
  }

  const isLoginRequest = response.url.includes(config.loginEndpoint)

  if (response.status === 401) {
    if (!isLoginRequest) {
      handleUnauthorized()
      throw {
        ...error,
        message: '登录已过期，请重新登录',
      } as ApiError
    }
  }

  throw error
}

export const apiRequest = async <T>(
  endpoint: string,
  options?: RequestInit
): Promise<T> => {
  const response = await authorizedFetch(endpoint, options, { jsonContentType: true })
  
  if (!response.ok) {
    await handleApiError(response)
  }

  if (response.status === 204 || response.status === 205) {
    return undefined as T
  }

  const contentLength = response.headers.get('content-length')
  if (contentLength === '0') {
    return undefined as T
  }

  const contentType = response.headers.get('content-type') || ''
  if (!contentType.includes('application/json')) {
    return undefined as T
  }
  
  return response.json()
}

export const apiRequestFormData = async <T>(
  endpoint: string,
  formData: FormData
): Promise<T> => {
  const response = await authorizedFetch(endpoint, {
    method: 'POST',
    body: formData,
  }, { jsonContentType: false })

  if (!response.ok) {
    await handleApiError(response)
  }
  return response.json()
}

export const authorizedFetch = async (
  endpoint: string,
  options?: RequestInit,
  authOptions?: { jsonContentType?: boolean }
): Promise<Response> => {
  const url = `${config.apiBaseUrl}${endpoint}`
  const method = String(options?.method ?? 'GET').toUpperCase()
  const requestId = ++authorizedFetchSeq
  const startedAt = Date.now()
  const pendingTimer = window.setTimeout(() => {
    logApiDiag('authorized_fetch_pending_too_long', {
      requestId,
      endpoint,
      url,
      method,
      elapsedMs: Date.now() - startedAt,
    })
  }, FETCH_PENDING_WARN_MS)

  logApiDiag('authorized_fetch_start', {
    requestId,
    endpoint,
    url,
    method,
  })

  try {
    const response = await fetch(url, {
      ...options,
      headers: {
        ...createAuthHeaders(authOptions),
        ...options?.headers,
      },
    })

    logApiDiag('authorized_fetch_resolved', {
      requestId,
      endpoint,
      url,
      method,
      status: response.status,
      ok: response.ok,
      elapsedMs: Date.now() - startedAt,
    })
    return response
  } catch (error) {
    logApiDiagError('authorized_fetch_rejected', error, {
      requestId,
      endpoint,
      url,
      method,
      elapsedMs: Date.now() - startedAt,
    })
    throw error
  } finally {
    window.clearTimeout(pendingTimer)
  }
}
