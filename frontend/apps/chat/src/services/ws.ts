import { config } from '../config'
import { getAuthTokenFromStorage } from './token_storage'

const resolveWsHost = (): { host: string; protocol: 'ws:' | 'wss:' } => {
  const wsBaseUrl = config.wsBaseUrl?.trim() || ''
  if (wsBaseUrl) {
    try {
      const url = new URL(wsBaseUrl)
      const protocol = url.protocol === 'wss:' ? 'wss:' : 'ws:'
      return { host: url.host, protocol }
    } catch {
      console.warn('[WsDiag] invalid wsBaseUrl in config, falling back:', wsBaseUrl)
    }
  }
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return { host: window.location.host, protocol }
}

export interface TaskWsCallbacks {
  onOpen?: (socket: WebSocket) => void
  onMessage?: (payload: unknown, event: MessageEvent) => void
  onError?: (event: Event, socket: WebSocket) => void
  onClose?: (event: CloseEvent, socket: WebSocket) => void
}

export const createTaskWebSocket = (taskId: string, callbacks?: TaskWsCallbacks): WebSocket => {
  const token = getAuthTokenFromStorage()
  if (!token) {
    throw new Error('未登录或登录态已失效，请重新登录')
  }

  const { host, protocol } = resolveWsHost()
  const wsUrl = `${protocol}//${host}/api/v1/document-tasks/ws/${encodeURIComponent(taskId)}`

  console.info('[WsDiag] ws_connect_url_resolved', {
    taskId,
    apiBaseUrl: config.apiBaseUrl,
    wsBaseFromConfig: config.wsBaseUrl ?? null,
    resolvedHost: host,
    resolvedProtocol: protocol,
    wsUrl,
    locationHost: window.location.host,
    locationProtocol: window.location.protocol,
  })
  const socket = new WebSocket(wsUrl)

  socket.onopen = () => {
    socket.send(JSON.stringify({ type: 'auth', token }))
    callbacks?.onOpen?.(socket)
  }

  socket.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data) as unknown
      callbacks?.onMessage?.(payload, event)
    } catch {
      callbacks?.onMessage?.(event.data, event)
    }
  }

  socket.onerror = (event) => {
    callbacks?.onError?.(event, socket)
  }

  socket.onclose = (event) => {
    callbacks?.onClose?.(event, socket)
  }

  return socket
}
