<template>
  <div class="chat-page">
    <Teleport to="#sidebar-extra-slot" v-if="isMounted">
      <MessageList
        ref="historyMessageListRef"
        variant="history"
        :history-items="chatHistory"
        v-model:active-item-id="currentConversationId"
        :editing-item-id="editingChatId"
        :editing-title="editingChatTitle"
        :rename-api-base-url="config.apiBaseUrl"
        :rename-api-token="''"
        :rename-user="chatSettings.user"
        :delete-api-base-url="config.apiBaseUrl"
        :delete-api-token="''"
        :delete-user="chatSettings.user"
        @update:editingTitle="editingChatTitle = $event"
        @create="createNewChat"
        @select="loadChat"
        @rename-start="renameChat"
        @rename-save="saveRename"
        @rename-cancel="cancelRename"
        @rename-error="handleRenameError"
        @archive="archiveChat"
        @delete="deleteChat"
      >
        <template #history-actions>
          <ChatSummaryAction @click="openSummaryDialog" />
        </template>
      </MessageList>
    </Teleport>

    <div class="chat-body">
      <div class="chat-main">
        <div ref="messageListRef" class="message-list-container">
          <div v-if="messages.length === 0" class="chat-welcome">
            <h1 class="chat-welcome__text">{{ welcomeDisplayText }}</h1>
          </div>
          <MessageList v-else>
            <MessageItem
              v-for="(message, index) in messages"
              :key="index"
              :role="message.role"
              :content="message.content"
              :timestamp="message.role === 'user' ? message.timestamp : undefined"
              :answer-pending="isLoading && index === messages.length - 1 && message.role === 'assistant'"
            />
          </MessageList>
        </div>

          <div class="chat-input-container">
            <div class="chat-input-wrapper">
                <Input
                  v-model="inputMessage"
                  class="chat-input"
                  placeholder="输入消息..."
                  multiline
                  :rows="1"
                  :disabled="isCompressing"
                  @enter="sendMessage"
                />

                <div class="chat-input-overlay chat-input-overlay--left">
                  <div ref="searchDropdownRef" class="search-mode-dropdown">
                    <button class="search-mode-btn" type="button" @click.stop="toggleSearchDropdown">
                      <span class="search-mode-label">{{ searchModeLabel }}</span>
                      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                        <path
                          d="M6 15l6-6 6 6"
                          stroke="currentColor"
                          stroke-width="2.5"
                          stroke-linecap="round"
                          stroke-linejoin="round"
                        />
                      </svg>
                    </button>
                  </div>

                  <button
                    class="knowledge-upload-btn"
                    :class="{ 'knowledge-upload-btn--uploading': isUploadingKnowledge }"
                    type="button"
                    :disabled="isUploadingKnowledge"
                    :aria-busy="isUploadingKnowledge"
                    @click.stop="openKnowledgeUpload"
                  >
                    <span v-if="isUploadingKnowledge" class="knowledge-upload-btn__spinner" aria-hidden="true"></span>
                    <span>{{ isUploadingKnowledge ? '知识上传中' : '知识上传' }}</span>
                  </button>

                  <div class="token-indicator" v-if="tokenUsage > 0" title="当前会话 Token 消耗">
                    <div class="token-indicator-label">{{ tokenUsage }} / 32k</div>
                    <div class="token-indicator-progress">
                      <div
                        class="token-indicator-bar"
                        :style="{
                          width: `${Math.min((tokenUsage / 32000) * 100, 100)}%`,
                          backgroundColor: tokenBarColor
                        }"
                      ></div>
                    </div>
                    <button
                      class="compress-context-btn"
                      type="button"
                      title="压缩当前对话以节省 Token"
                      :disabled="isCompressing"
                      @click.stop="handleCompressContext"
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" stroke-linecap="round" stroke-linejoin="round"/>
                      </svg>
                      {{ isCompressing ? '压缩中...' : '压缩' }}
                    </button>
                  </div>

                  <input
                    ref="knowledgeUploadInputRef"
                    class="knowledge-upload-input"
                    type="file"
                    multiple
                    @change="handleKnowledgeUploadChange"
                  />
                </div>

                <div class="chat-input-overlay chat-input-overlay--right">
                  <button
                    class="chat-send-btn"
                    :class="{ 'chat-send-btn--stop': isLoading }"
                    :disabled="(!isLoading && !inputMessage.trim()) || isCompressing"
                    type="button"
                    @click="isLoading ? stopGeneration() : sendMessage()"
                  >
                    <svg v-if="!isLoading" width="20" height="20" viewBox="0 0 24 24" fill="none">
                      <path
                        d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"
                        stroke="currentColor"
                        stroke-width="2"
                        stroke-linecap="round"
                        stroke-linejoin="round"
                      />
                    </svg>
                    <svg v-else width="20" height="20" viewBox="0 0 24 24" fill="none">
                      <rect
                        x="6"
                        y="6"
                        width="12"
                        height="12"
                        rx="1"
                        fill="currentColor"
                      />
                    </svg>
                  </button>
                </div>
              </div>

              <Teleport to="body">
                <div
                  v-if="showSearchDropdown"
                  class="search-mode-menu"
                  :style="searchDropdownStyle"
                  @click.stop
                >
                  <button
                    class="search-mode-item"
                    :class="{ 'search-mode-item--active': chatSettings.search === '联网搜索' }"
                    type="button"
                    @click="setSearchMode('联网搜索')"
                  >联网搜索</button>
                  <button
                    class="search-mode-item"
                    :class="{ 'search-mode-item--active': chatSettings.search === '本地检索' }"
                    type="button"
                    @click="setSearchMode('本地检索')"
                  >本地检索</button>
                  <button
                    class="search-mode-item"
                    :class="{ 'search-mode-item--active': chatSettings.search === '本地&网络' }"
                    type="button"
                    @click="setSearchMode('本地&网络')"
                  >本地&amp;网络</button>
                </div>
              </Teleport>
              <p class="chat-input-hint">AI 可能会出错，请验证重要信息</p>
        </div>
      </div>
    </div>

    <ConfirmDialog
      v-model="showDeleteDialog"
      title="删除对话"
      message="确定要删除这个对话吗？此操作无法撤销。"
      type="danger"
      confirm-text="删除"
      cancel-text="取消"
      @confirm="confirmDelete"
    />

    <ChatSummaryDialog
      v-model="showSummaryDialog"
      :user-id="summaryUserId"
      :api-base-url="config.apiBaseUrl"
      :api-token="authToken"
    />

  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, onMounted, onBeforeUnmount } from 'vue'
import {
  MessageList,
  ChatSummaryAction,
  MessageItem,
  ConfirmDialog,
  ChatSummaryDialog,
  Input,
  useToast,
  useChatSummary,
} from '@yamato/components'
import { config } from '../config'
import { sendChatMessage, getConversations, getMessages, stopChatMessage, compressContext } from '../services/chat'
import { getAuthTokenFromStorage } from '../services/token_storage'
import { readStored, patchStored } from '../services/storage'
import { countTokens } from '../utils/token_counter'
import type { Conversation, SearchMode } from '../types/chat'

interface Message {
  role: 'user' | 'assistant'
  content: string
  timestamp?: string
}

interface ChatHistoryItem {
  id: string
  title: string
}

interface ChatSettings {
  user: string
  userId?: string
  search: SearchMode
}

interface BackendMessageRecord {
  role?: string
  content?: string
  query?: string
  answer?: string
  created_at?: number
}

const inputMessage = ref('')
const messages = ref<Message[]>([])
const isLoading = ref(false)
const isUnmounted = ref(false)
const messageListRef = ref<HTMLElement | null>(null)
const historyMessageListRef = ref<{ deleteConversation: (chatId: string) => Promise<boolean> } | null>(null)
const isMounted = ref(false)
const showDeleteDialog = ref(false)
const chatToDelete = ref<string | null>(null)
const editingChatId = ref<string | null>(null)
const editingChatTitle = ref('')
const showSummaryDialog = ref(false)
const chatHistory = ref<ChatHistoryItem[]>([
  {
    id: '1',
    title: '新对话',
  },
])
const currentConversationId = ref<string | undefined>(undefined)
const currentTaskId = ref<string | undefined>(undefined)
const activeGenerationId = ref(0)
const tokenUsage = ref<number>(0)
const isCompressing = ref(false)
const chatBackgrounds = ref<Record<string, string>>({})

const BACKGROUND_STORAGE_KEY_PREFIX = 'yamato_chat_background_'

const getConversationBackgroundKey = (conversationId: string, userId: string): string => {
  return `${BACKGROUND_STORAGE_KEY_PREFIX}${userId}_${conversationId}`
}

const loadBackground = (conversationId: string): string => {
  if (!conversationId || conversationId.startsWith('new-') || conversationId.startsWith('temp-')) {
    return ''
  }
  const normalizedUser = String(chatSettings.value.userId || chatSettings.value.user || 'user').trim()
  const key = getConversationBackgroundKey(conversationId, normalizedUser)
  try {
    return localStorage.getItem(key) || ''
  } catch {
    return ''
  }
}

const saveBackground = (conversationId: string, background: string) => {
  if (!conversationId || conversationId.startsWith('new-') || conversationId.startsWith('temp-')) {
    return
  }
  const normalizedUser = String(chatSettings.value.userId || chatSettings.value.user || 'user').trim()
  const key = getConversationBackgroundKey(conversationId, normalizedUser)
  try {
    localStorage.setItem(key, background)
    chatBackgrounds.value[conversationId] = background
  } catch (error) {
    if (import.meta.env.DEV) {
      console.error('保存 Background 失败:', error)
    }
  }
}

const TOKEN_USAGE_STORAGE_KEY_PREFIX = 'yamato_chat_token_usage_'

const getConversationTokenKey = (conversationId: string, userId: string): string => {
  return `${TOKEN_USAGE_STORAGE_KEY_PREFIX}${userId}_${conversationId}`
}

const saveTokenUsage = (conversationId: string, usage: number) => {
  if (!conversationId || conversationId.startsWith('new-') || conversationId.startsWith('temp-')) {
    return
  }
  const normalizedUser = String(chatSettings.value.userId || chatSettings.value.user || 'user').trim()
  const key = getConversationTokenKey(conversationId, normalizedUser)
  try {
    localStorage.setItem(key, usage.toString())
  } catch (error) {
    if (import.meta.env.DEV) {
      console.error('保存 Token 消耗失败:', error)
    }
  }
}

const loadTokenUsage = (conversationId: string): number => {
  if (!conversationId || conversationId.startsWith('new-') || conversationId.startsWith('temp-')) {
    return 0
  }
  const normalizedUser = String(chatSettings.value.userId || chatSettings.value.user || 'user').trim()
  const key = getConversationTokenKey(conversationId, normalizedUser)
  try {
    const cached = localStorage.getItem(key)
    return cached ? parseInt(cached, 10) : 0
  } catch {
    return 0
  }
}

const tokenBarColor = computed(() => {
  const percentage = tokenUsage.value / 32000
  if (percentage < 0.5) return '#10b981' // 绿
  if (percentage < 0.8) return '#f59e0b' // 橙
  return '#ef4444' // 红
})

const SETTINGS_STORAGE_KEY = 'yamato_chat_settings'
const chatSettings = ref<ChatSettings>({ user: '', search: '本地&网络' })

type CachedChatSettings = {
  user?: string | number
  userId?: string | number
  username?: string | number
  userName?: string | number
  userUUID?: string | number
  search?: SearchMode
}

const SEARCH_MODE_VALUES: SearchMode[] = ['联网搜索', '本地检索', '本地&网络']

const isSearchMode = (value: unknown): value is SearchMode => {
  return typeof value === 'string' && SEARCH_MODE_VALUES.includes(value as SearchMode)
}

const getErrorMessage = (error: unknown, fallback: string): string => {
  const rateLimitMessage = '重试次数过多，已限流，稍后重试'

  if (error instanceof Error && error.message) {
    const normalized = error.message.toLowerCase()
    const isRateLimited =
      normalized.includes('429') ||
      normalized.includes('too many request') ||
      normalized.includes('rate limit') ||
      normalized.includes('限流')

    if (isRateLimited) {
      return rateLimitMessage
    }
    return error.message
  }
  return fallback
}

const stripThinkContent = (raw: string): string => {
  if (!raw) {
    return ''
  }

  const normalized = raw
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')

  const openTagPattern = /^<\s*think\s*>/i
  const closeTagPattern = /^<\s*\/\s*think\s*>/i

  let inThinkBlock = false
  let cursor = 0
  let visibleText = ''

  while (cursor < normalized.length) {
    const tail = normalized.slice(cursor)
    const openMatch = openTagPattern.exec(tail)
    const closeMatch = closeTagPattern.exec(tail)

    if (!inThinkBlock && openMatch) {
      inThinkBlock = true
      cursor += openMatch[0].length
      continue
    }

    if (closeMatch) {
      inThinkBlock = false
      cursor += closeMatch[0].length
      continue
    }

    if (!inThinkBlock) {
      visibleText += normalized[cursor]
    }
    cursor += 1
  }

  return visibleText
}

const showSearchDropdown = ref(false)
const searchDropdownRef = ref<HTMLElement | null>(null)
const searchDropdownStyle = ref<Record<string, string>>({ top: '0px', left: '0px' })

const searchModeLabel = computed(() => {
  return chatSettings.value.search
})

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

const summaryUserId = computed(() => {
  const candidate = String(chatSettings.value.userId ?? '').trim()
  if (candidate && !UUID_PATTERN.test(candidate)) {
    return candidate
  }

  const fallback = String(chatSettings.value.user ?? '').trim()
  return fallback
})

const authToken = computed(() => {
  return getAuthTokenFromStorage() || ''
})

const { showSuccess, showError } = useToast()
const { archiveConversation } = useChatSummary({
  apiBaseUrl: config.apiBaseUrl,
  apiToken: authToken.value,
})

let streamingAbortController: AbortController | null = null
const currentStreamRendererStopper = ref<(() => void) | null>(null)
const knowledgeUploadInputRef = ref<HTMLInputElement | null>(null)
const isUploadingKnowledge = ref(false)
const activeKnowledgeTaskId = ref<string | null>(null)
let knowledgeStatusPollTimer: number | null = null
let knowledgeStatusAbortController: AbortController | null = null
/** 同一次上传只处理一轮，避免 change 重复触发。 */
let knowledgeUploadProcessing = false

const WELCOME_TEXT = '有什么我能帮您的吗😊'
const welcomeDisplayText = ref('')
let welcomeTypingTimer: number | null = null

const stopWelcomeTyping = () => {
  if (welcomeTypingTimer !== null) {
    window.clearInterval(welcomeTypingTimer)
    welcomeTypingTimer = null
  }
}

const startWelcomeTyping = () => {
  stopWelcomeTyping()
  welcomeDisplayText.value = ''

  let cursor = 0
  welcomeTypingTimer = window.setInterval(() => {
    cursor += 1
    welcomeDisplayText.value = WELCOME_TEXT.slice(0, cursor)

    if (cursor >= WELCOME_TEXT.length) {
      stopWelcomeTyping()
    }
  }, 45)
}

const saveCachedSettings = () => {
  patchStored(SETTINGS_STORAGE_KEY, {
    user: chatSettings.value.user,
    userId: chatSettings.value.userId,
    search: chatSettings.value.search,
  })
}

const loadCachedSettings = () => {
  const parsed = readStored<CachedChatSettings | null>(SETTINGS_STORAGE_KEY, null)
  if (parsed) {
    const cachedUser = String(parsed.user ?? parsed.username ?? parsed.userName ?? '').trim()
    const cachedUserId = String(parsed.userId ?? '').trim()
    const normalizedUserId =
      cachedUserId && !UUID_PATTERN.test(cachedUserId)
        ? cachedUserId
        : cachedUser || undefined

    chatSettings.value = {
      user: cachedUser || cachedUserId,
      userId: normalizedUserId,
      search: isSearchMode(parsed.search) ? parsed.search : '联网搜索',
    }
  }
}

const toggleSearchDropdown = () => {
  if (showSearchDropdown.value) {
    showSearchDropdown.value = false
    return
  }
  const el = searchDropdownRef.value
  if (el) {
    const rect = el.getBoundingClientRect()
    searchDropdownStyle.value = {
      bottom: `${window.innerHeight - rect.top + 6}px`,
      left: `${rect.left}px`,
    }
  }
  showSearchDropdown.value = true
}

const setSearchMode = (mode: SearchMode) => {
  chatSettings.value.search = mode
  showSearchDropdown.value = false
  saveCachedSettings()
}

const openKnowledgeUpload = () => {
  if (isUploadingKnowledge.value) {
    return
  }
  knowledgeUploadInputRef.value?.click()
}

const getUploadAuthorization = (): string => {
  const token = getAuthTokenFromStorage()
  if (token) {
    return `Bearer ${token}`
  }
  throw new Error('未登录或登录态已失效，请重新登录')
}

const clearKnowledgeTaskPolling = () => {
  if (knowledgeStatusPollTimer !== null) {
    window.clearTimeout(knowledgeStatusPollTimer)
    knowledgeStatusPollTimer = null
  }
  if (knowledgeStatusAbortController) {
    knowledgeStatusAbortController.abort()
    knowledgeStatusAbortController = null
  }
}

const finishKnowledgeTask = () => {
  clearKnowledgeTaskPolling()
  activeKnowledgeTaskId.value = null
  isUploadingKnowledge.value = false
}

const pollKnowledgeTaskStatus = async (taskId: string) => {
  if (isUnmounted.value || activeKnowledgeTaskId.value !== taskId) {
    return
  }

  const abortController = new AbortController()
  if (knowledgeStatusAbortController) {
    knowledgeStatusAbortController.abort()
  }
  knowledgeStatusAbortController = abortController

  try {
    const response = await fetch(`${config.apiBaseUrl}/document-tasks/status/${encodeURIComponent(taskId)}`, {
      method: 'GET',
      headers: {
        Authorization: getUploadAuthorization(),
      },
      signal: abortController.signal,
    })

    if (!response.ok) {
      throw new Error('知识上传状态查询失败')
    }

    const result = (await response.json()) as {
      status?: string
      progress?: number
      message?: string
      error?: string
    }

    const status = String(result.status ?? '').toLowerCase()

    if (status === 'completed') {
      showSuccess('知识上传处理完成')
      finishKnowledgeTask()
      return
    }

    if (status === 'failed' || status === 'cancelled') {
      showError(result.error || result.message || '知识上传处理失败')
      finishKnowledgeTask()
      return
    }

    if (isUnmounted.value || activeKnowledgeTaskId.value !== taskId) {
      return
    }

    knowledgeStatusPollTimer = window.setTimeout(() => {
      if (isUnmounted.value || activeKnowledgeTaskId.value !== taskId) {
        return
      }
      void pollKnowledgeTaskStatus(taskId)
    }, 1500)
  } catch (error: unknown) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      return
    }
    showError(getErrorMessage(error, '知识上传状态查询失败'))
    finishKnowledgeTask()
  } finally {
    if (knowledgeStatusAbortController === abortController) {
      knowledgeStatusAbortController = null
    }
  }
}

const handleKnowledgeUploadChange = async (event: Event) => {
  const target = event.target as HTMLInputElement
  const selectedFiles = target.files ? Array.from(target.files) : []

  if (selectedFiles.length === 0 || isUploadingKnowledge.value || knowledgeUploadProcessing) {
    return
  }

  knowledgeUploadProcessing = true

  const formData = new FormData()
  for (const file of selectedFiles) {
    formData.append('files', file)
  }

  const uploader = String(chatSettings.value.user ?? '').trim() || 'user'

  let keepUploadingState = false
  isUploadingKnowledge.value = true

  try {
    const query = new URLSearchParams({
      instance_id: '2',
      uploader,
    })

    const response = await fetch(`${config.apiBaseUrl}/document-tasks/process?${query.toString()}`, {
      method: 'POST',
      headers: {
        Authorization: getUploadAuthorization(),
      },
      body: formData,
    })

    if (!response.ok) {
      const errorPayload = (await response.json().catch(() => null)) as
        | { detail?: string; message?: string }
        | null
      const message = errorPayload?.detail || errorPayload?.message || '知识上传失败'
      throw new Error(message)
    }

    const result = (await response.json()) as { task_id?: string; files_count?: number }
    const uploadedCount = result.files_count ?? selectedFiles.length
    const taskId = typeof result.task_id === 'string' ? result.task_id.trim() : ''

    showSuccess(`知识上传任务已创建（${uploadedCount} 个文件）`)

    if (taskId) {
      keepUploadingState = true
      activeKnowledgeTaskId.value = taskId
      clearKnowledgeTaskPolling()
      void pollKnowledgeTaskStatus(taskId)
    }
  } catch (error: unknown) {
    showError(getErrorMessage(error, '知识上传失败'))
  } finally {
    knowledgeUploadProcessing = false
    if (!keepUploadingState) {
      isUploadingKnowledge.value = false
    }
    target.value = ''
  }
}

const formatTime = (): string => {
  const now = new Date()
  return now.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

const buildConversationTitle = (text: string): string => {
  const trimmed = text.trim().replace(/\s+/g, ' ')
  if (!trimmed) {
    return '新对话'
  }
  return trimmed.length > 24 ? `${trimmed.slice(0, 24)}...` : trimmed
}

const upsertChatHistoryToTop = (chatId: string, title?: string) => {
  const index = chatHistory.value.findIndex((chat) => chat.id === chatId)

  if (index === -1) {
    chatHistory.value.unshift({
      id: chatId,
      title: title || '新对话',
    })
    return
  }

  const existing = chatHistory.value[index]
  if (title && (!existing.title || existing.title === '新对话')) {
    existing.title = title
  }

  chatHistory.value.splice(index, 1)
  chatHistory.value.unshift(existing)
}

const removeChatHistoryItem = (chatId: string) => {
  const index = chatHistory.value.findIndex((chat) => chat.id === chatId)
  if (index !== -1) {
    chatHistory.value.splice(index, 1)
  }
}

const scrollToBottom = async () => {
  await nextTick()
  const messageListEl = messageListRef.value
  if (messageListEl) {
    messageListEl.scrollTop = messageListEl.scrollHeight
  }
}

const invalidateActiveGeneration = () => {
  activeGenerationId.value += 1
  if (streamingAbortController) {
    streamingAbortController.abort()
    streamingAbortController = null
  }
}

const stopGeneration = async () => {
  invalidateActiveGeneration()
  if (currentStreamRendererStopper.value) {
    currentStreamRendererStopper.value()
    currentStreamRendererStopper.value = null
  }
  if (currentTaskId.value) {
    try {
      await stopChatMessage(currentTaskId.value)
    } catch (error) {
      if (import.meta.env.DEV) {
        console.error('停止消息生成失败:', error)
      }
    }
  }
  isLoading.value = false
  currentTaskId.value = undefined
}

const sendMessage = async () => {
  if (!inputMessage.value.trim() || isLoading.value || isCompressing.value) {
    return
  }

  stopWelcomeTyping()
  const generationId = activeGenerationId.value + 1
  activeGenerationId.value = generationId
  const isGenerationStale = () => generationId !== activeGenerationId.value

  const userMessage: Message = {
    role: 'user',
    content: inputMessage.value,
    timestamp: formatTime(),
  }

  messages.value.push(userMessage)
  const currentInput = inputMessage.value
  const optimisticTitle = buildConversationTitle(currentInput)
  const existingConversationId = currentConversationId.value
  let tempConversationId: string | null = null

  if (existingConversationId && !existingConversationId.startsWith('new-') && !existingConversationId.startsWith('temp-')) {
    upsertChatHistoryToTop(existingConversationId)
    chatHistory.value = [...chatHistory.value]
  } else {
    if (existingConversationId && (existingConversationId.startsWith('new-') || existingConversationId.startsWith('temp-'))) {
      tempConversationId = existingConversationId
    } else {
      tempConversationId = `temp-${Date.now()}`
    }
    
    upsertChatHistoryToTop(tempConversationId, optimisticTitle)
    chatHistory.value = [...chatHistory.value]
    currentConversationId.value = tempConversationId
  }

  const currentMsg = inputMessage.value

  if (tokenUsage.value > 28000) {
    inputMessage.value = ''
    const assistantMessage: Message = {
      role: 'assistant',
      content: '正在压缩上下文，请等候',
      timestamp: formatTime(),
    }
    messages.value.push(assistantMessage)
    const reactiveAssistantMessage = messages.value[messages.value.length - 1]

    isCompressing.value = true
    await scrollToBottom()

    try {
      const userId = summaryUserId.value
      const convId = currentConversationId.value
      if (!convId) throw new Error('No conversation ID')

      const response = await compressContext(userId, convId)
      const compressedText = response.data.compressed_context

      if (import.meta.env.DEV) {
        console.debug('[context-compression] compressed context:', {
          conversationId: convId,
          length: compressedText.length,
          content: compressedText,
        })
      }

      saveBackground(convId, compressedText)
      const estimatedTokens = Math.ceil(compressedText.length * 1.2)
      tokenUsage.value = estimatedTokens
      saveTokenUsage(convId, estimatedTokens)

      reactiveAssistantMessage.content = '压缩完成，请继续对话'
    } catch (error: unknown) {
      showError(getErrorMessage(error, '压缩上下文失败'))
      reactiveAssistantMessage.content = '压缩上下文失败，请重试'
    } finally {
      isCompressing.value = false
      await scrollToBottom()
      inputMessage.value = currentMsg
    }
    return
  }

  inputMessage.value = ''
  isLoading.value = true
  streamingAbortController = new AbortController()

  await scrollToBottom()

  const assistantMessage: Message = {
    role: 'assistant',
    content: '',
    timestamp: formatTime(),
  }
  messages.value.push(assistantMessage)
  // 后续改内容要用数组最后一项（代理），不要改局部 assistantMessage 引用。
  const reactiveAssistantMessage = messages.value[messages.value.length - 1]

  let renderedContent = ''
  let targetContent = ''
  let streamRenderTimer: number | undefined

  const stopStreamRenderer = () => {
    if (streamRenderTimer !== undefined) {
      window.clearInterval(streamRenderTimer)
      streamRenderTimer = undefined
    }
  }

  const flushRenderImmediately = () => {
    stopStreamRendererAndRelease()
    renderedContent = targetContent
    reactiveAssistantMessage.content = renderedContent
  }

  const stopStreamRendererAndRelease = () => {
    stopStreamRenderer()
    if (currentStreamRendererStopper.value === stopStreamRendererAndRelease) {
      currentStreamRendererStopper.value = null
    }
  }

  currentStreamRendererStopper.value = stopStreamRendererAndRelease

  const startStreamRenderer = () => {
    if (streamRenderTimer !== undefined) {
      return
    }

    streamRenderTimer = window.setInterval(() => {
      if (renderedContent === targetContent) {
        stopStreamRendererAndRelease()
        return
      }

      const remaining = targetContent.length - renderedContent.length
      const step = Math.max(1, Math.min(8, Math.ceil(remaining / 6)))
      const nextLength = Math.min(targetContent.length, renderedContent.length + step)
      renderedContent = targetContent.slice(0, nextLength)
      reactiveAssistantMessage.content = renderedContent
      scrollToBottom()
    }, 16)
  }

  try {
    if (isGenerationStale()) {
      return
    }
    const normalizedUser = String(chatSettings.value.user ?? '').trim()

    const currentBg = currentConversationId.value ? (chatBackgrounds.value[currentConversationId.value] || loadBackground(currentConversationId.value)) : ''
    
    if (currentConversationId.value && currentBg) {
      saveBackground(currentConversationId.value, '')
    }

    const result = await sendChatMessage(
      currentInput,
      currentConversationId.value,
      {
        user: normalizedUser,
        search: chatSettings.value.search,
        background: currentBg,
      },
      {
        onMessage: (content: string) => {
          if (isGenerationStale()) {
            return
          }
          targetContent = content
          startStreamRenderer()
        },
        onEnd: (data) => {
          if (isGenerationStale()) {
            return
          }
          if (streamRenderTimer === undefined) {
            flushRenderImmediately()
          }
          
          // Token：按剥掉 think 后的可见文本估算；与流式 usage 口径对齐用前端累计。
          const cleanAiText = stripThinkContent(targetContent)
          const aiTokens = countTokens(cleanAiText)
          const userTokens = countTokens(currentInput)
          const increment = aiTokens + userTokens

          tokenUsage.value += increment

          if (currentConversationId.value) {
            saveTokenUsage(currentConversationId.value, tokenUsage.value)
          }

          const conversationId =
            typeof (data as { conversation_id?: unknown }).conversation_id === 'string'
              ? (data as { conversation_id: string }).conversation_id
              : undefined
          if (conversationId) {
            currentConversationId.value = conversationId
          }
          streamingAbortController = null
          isLoading.value = false
          currentTaskId.value = undefined
        },
        onError: (error) => {
          if (isGenerationStale()) {
            return
          }
          stopStreamRendererAndRelease()
          reactiveAssistantMessage.content = getErrorMessage(error, '发送消息失败')
          streamingAbortController = null
          isLoading.value = false
          currentTaskId.value = undefined
          if (import.meta.env.DEV) {
            console.error('发送消息失败:', error)
          }
          if (currentConversationId.value && currentBg) {
            saveBackground(currentConversationId.value, currentBg)
          }
        },
      },
      streamingAbortController.signal
    )

    if (isGenerationStale()) {
      stopStreamRendererAndRelease()
      return
    }

    currentTaskId.value = result.taskId
    if (result.conversationId) {
      const oldId = currentConversationId.value
      currentConversationId.value = result.conversationId
      if (oldId && (oldId.startsWith('temp-') || oldId.startsWith('new-'))) {
         if (tokenUsage.value > 0) saveTokenUsage(result.conversationId, tokenUsage.value)
         if (chatBackgrounds.value[oldId]) {
           saveBackground(result.conversationId, chatBackgrounds.value[oldId])
           delete chatBackgrounds.value[oldId]
         }
      }
    }

    const resolvedConversationId = result.conversationId || currentConversationId.value
    if (resolvedConversationId) {
      if (tempConversationId) {
        removeChatHistoryItem(tempConversationId)
      }
      upsertChatHistoryToTop(resolvedConversationId, optimisticTitle)
      currentConversationId.value = resolvedConversationId
      chatHistory.value = [...chatHistory.value]
    }
  } catch (error: unknown) {
    streamingAbortController = null
    if (error instanceof DOMException && error.name === 'AbortError') {
      stopStreamRendererAndRelease()
      isLoading.value = false
      currentTaskId.value = undefined
      return
    }
    if (tempConversationId) {
      removeChatHistoryItem(tempConversationId)
      if (currentConversationId.value === tempConversationId) {
        currentConversationId.value = undefined
      }
    }
    stopStreamRendererAndRelease()
    reactiveAssistantMessage.content = getErrorMessage(error, '发送消息失败')
    isLoading.value = false
    currentTaskId.value = undefined
    if (import.meta.env.DEV) {
      console.error('发送消息失败:', error)
    }
  }
}

const createNewChat = (tempId?: string) => {
  invalidateActiveGeneration()
  messages.value = []
  
  const newId = tempId && typeof tempId === 'string' ? tempId : `new-${Date.now()}`
  currentConversationId.value = newId
  
  upsertChatHistoryToTop(newId, '新对话')
  chatHistory.value = [...chatHistory.value]

  currentTaskId.value = undefined
  tokenUsage.value = 0
  
  startWelcomeTyping()
}

const loadChat = async (chatId: string) => {
  invalidateActiveGeneration()
  if (chatId.startsWith('new-') || chatId.startsWith('temp-')) {
    messages.value = []
    tokenUsage.value = 0
    currentConversationId.value = chatId
    currentTaskId.value = undefined
    return
  }

  try {
    const normalizedUser = String(chatSettings.value.user ?? '').trim() || 'user'
    const response = await getMessages(normalizedUser, chatId)
    
    messages.value = []
    tokenUsage.value = 0
    
    if (response.data && response.data.length > 0) {
      messages.value = response.data.flatMap((msg: BackendMessageRecord) => {
        const timestamp = msg.created_at
          ? new Date(msg.created_at * 1000).toLocaleTimeString('zh-CN', {
              hour: '2-digit',
              minute: '2-digit',
            })
          : undefined

        const normalizedRole = msg.role === 'user' || msg.role === 'assistant' ? msg.role : undefined
        if (normalizedRole) {
          const roleContent = msg.content || (normalizedRole === 'assistant' ? msg.answer : msg.query) || ''
          return roleContent
            ? [
                {
                  role: normalizedRole,
                  content: roleContent,
                  timestamp,
                },
              ]
            : []
        }

        const mappedMessages: Message[] = []

        if (msg.query) {
          mappedMessages.push({
            role: 'user',
            content: msg.query,
            timestamp,
          })
        }

        if (msg.answer) {
          mappedMessages.push({
            role: 'assistant',
            content: msg.answer,
            timestamp,
          })
        }

        if (mappedMessages.length === 0 && msg.content) {
          mappedMessages.push({
            role: 'assistant',
            content: msg.content,
            timestamp,
          })
        }

        return mappedMessages
      })
    }
    
    currentConversationId.value = chatId
    currentTaskId.value = undefined
    
    tokenUsage.value = loadTokenUsage(chatId)
    chatBackgrounds.value[chatId] = loadBackground(chatId)
    
    await scrollToBottom()
  } catch (error: unknown) {
    if (import.meta.env.DEV) {
      console.error('加载会话失败:', error)
    }
  }
}


const renameChat = (chatId: string) => {
  const chat = chatHistory.value.find((c) => c.id === chatId)
  if (chat) {
    editingChatId.value = chatId
    editingChatTitle.value = chat.title
  }
}

const saveRename = (chatId: string, newTitle: string) => {
  const chat = chatHistory.value.find((c) => c.id === chatId)
  if (chat) {
    chat.title = newTitle
  }
  editingChatId.value = null
  editingChatTitle.value = ''
}

const cancelRename = () => {
  editingChatId.value = null
  editingChatTitle.value = ''
}

const handleRenameError = (_chatId: string, errorMessage: string) => {
  if (import.meta.env.DEV) {
    console.error('重命名失败:', errorMessage)
  }
}

const handleCompressContext = async () => {
  if (!currentConversationId.value || isCompressing.value) return
  
  const conversationId = currentConversationId.value
  if (conversationId.startsWith('new-') || conversationId.startsWith('temp-')) {
    showError('新对话暂不支持压缩，请先发送至少一条消息')
    return
  }

  isCompressing.value = true
  try {
    const userId = summaryUserId.value
    const response = await compressContext(userId, conversationId)
    const compressedText = response.data.compressed_context
    
    saveBackground(conversationId, compressedText)
    
    const estimatedTokens = Math.ceil(compressedText.length * 1.2)
    tokenUsage.value = estimatedTokens
    saveTokenUsage(conversationId, estimatedTokens)
    
    showSuccess('上下文已压缩并更新到背景记忆中')
  } catch (error: unknown) {
    showError(getErrorMessage(error, '压缩上下文失败'))
  } finally {
    isCompressing.value = false
  }
}

const archiveChat = async (chatId: string) => {
  const normalizedUser = String(chatSettings.value.user ?? '').trim() || 'user'

  try {
    await archiveConversation({
      userId: normalizedUser,
      conversationId: chatId,
      limit: 20,
    })
    showSuccess('归档已完成')
  } catch (error: unknown) {
    showError(getErrorMessage(error, '归档失败'))
    if (import.meta.env.DEV) {
      console.error('归档失败:', error)
    }
  }
}

const deleteChat = (chatId: string) => {
  chatToDelete.value = chatId
  showDeleteDialog.value = true
}

const openSummaryDialog = () => {
  showSummaryDialog.value = true
}

const confirmDelete = async () => {
  if (chatToDelete.value) {
    const chatId = chatToDelete.value
    const deleted = await historyMessageListRef.value?.deleteConversation(chatId)

    if (deleted) {
      const index = chatHistory.value.findIndex((c) => c.id === chatId)
      if (index !== -1) {
        chatHistory.value.splice(index, 1)
      }

      if (currentConversationId.value === chatId) {
        currentConversationId.value = undefined
        currentTaskId.value = undefined
        messages.value = []
      }
    }

    chatToDelete.value = null
    showDeleteDialog.value = false
  }
}

const handleDocumentClick = () => {
  showSearchDropdown.value = false
}

onMounted(async () => {
  isUnmounted.value = false
  isMounted.value = true

  loadCachedSettings()

  startWelcomeTyping()

  try {
    const normalizedUser = String(chatSettings.value.user ?? '').trim() || 'user'
    const response = await getConversations(normalizedUser)
    if (response.data && response.data.length > 0) {
      chatHistory.value = response.data.map((conv: Conversation) => ({
        id: conv.id,
        title: conv.name || '新对话',
      }))
    }
  } catch (error: unknown) {
    if (import.meta.env.DEV) {
      console.error('加载会话列表失败:', error)
    }
  }

  document.addEventListener('click', handleDocumentClick)
})

onBeforeUnmount(() => {
  isUnmounted.value = true
  invalidateActiveGeneration()
  document.removeEventListener('click', handleDocumentClick)
  clearKnowledgeTaskPolling()
  if (currentStreamRendererStopper.value) {
    currentStreamRendererStopper.value()
    currentStreamRendererStopper.value = null
  }
  stopWelcomeTyping()
})
</script>

<style lang="scss" scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--yamato-color-bg-light);
}

.chat-body {
  display: flex;
  flex: 1;
  min-height: 0;
  position: relative;
}

.chat-history-panel--collapsed {
  display: none;
}

.chat-main {
  display: flex;
  flex-direction: column;
  height: 100%;
  z-index: 1;
  overflow: hidden;
  flex: 1;
}

.chat-history-toggle {
  align-self: stretch;
  width: 20px;
  border: none;
  background: transparent;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--yamato-color-text-muted);
}

.chat-history-toggle svg {
  transition: transform 0.2s ease;
}

.chat-history-toggle--collapsed svg {
  transform: rotate(180deg);
}

.chat-input-container {
  padding: 20px 24px;
  border-top: none;
  background: transparent;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.chat-input-wrapper {
  position: relative;
  margin-bottom: 8px;
  width: min(980px, 92%);
  max-width: 980px;
}

.chat-input {
  width: 100%;

  :deep(.input) {
    border: 1px solid var(--yamato-color-border-subtle);
    background: var(--yamato-color-surface);
    box-shadow: var(--yamato-shadow-card);
    border-radius: var(--yamato-radius-lg);
    padding: 14px 54px 54px 16px;
    min-height: 92px;
  }

  :deep(.input:focus) {
    border-color: var(--yamato-color-accent);
    box-shadow: var(--yamato-focus-ring);
  }
}

.chat-input-overlay {
  position: absolute;
  bottom: 10px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.chat-input-overlay--left {
  left: 10px;
}

.token-indicator {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.compress-context-btn {
  position: static;
  height: 24px;
  padding: 0 8px;
  font-size: 11px;
  background: var(--yamato-color-surface);
  border: 1px solid var(--yamato-color-border-subtle);
  border-radius: var(--yamato-radius-pill);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--yamato-color-text-secondary);
  cursor: pointer;
  white-space: nowrap;
  opacity: 1;
  pointer-events: auto;
  transition: all 0.2s ease;

  &:hover:not(:disabled) {
    background: rgba(0, 0, 0, 0.05);
    border-color: rgba(201, 100, 66, 0.35);
  }

  &:disabled {
    cursor: not-allowed;
    color: var(--yamato-color-text-muted);
    opacity: 0.7;
  }
}

.chat-input-overlay--right {
  right: 10px;
}

.chat-send-btn {
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 50%;
  background: var(--yamato-color-accent);
  color: white;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
  flex-shrink: 0;

  &:hover:not(:disabled) {
    background: var(--yamato-color-accent-hover);
  }

  &:disabled {
    background: rgba(0, 0, 0, 0.16);
    cursor: not-allowed;
    opacity: 0.5;
  }

  &--stop {
    background: var(--yamato-color-danger);

    &:hover {
      filter: brightness(1.05);
    }
  }
}

.chat-input-hint {
  font-size: 12px;
  color: var(--yamato-color-text-muted);
  text-align: center;
  margin: 0;
}

.message-list-container {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
}

.chat-welcome {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  transform: translateY(-48px);
}

.chat-welcome__text {
  margin: 0;
  font-family: var(--yamato-font-display);
  font-size: 40px;
  font-weight: 500;
  line-height: 1.2;
  color: var(--yamato-color-text-primary);
  letter-spacing: 0;
  text-align: center;
}

.search-mode-dropdown {
  flex-shrink: 0;
}

.search-mode-btn {
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--yamato-color-border-subtle);
  border-radius: var(--yamato-radius-pill);
  background: var(--yamato-color-surface);
  color: var(--yamato-color-text-primary);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  transition: all 0.2s ease;

  &:hover {
    background: rgba(0, 0, 0, 0.04);
    border-color: rgba(201, 100, 66, 0.35);
    color: var(--yamato-color-text-primary);
  }
}

.search-mode-label {
  line-height: 1;
}

.knowledge-upload-btn {
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--yamato-color-border-subtle);
  border-radius: var(--yamato-radius-pill);
  background: var(--yamato-color-surface);
  color: var(--yamato-color-text-primary);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  transition: all 0.2s ease;

  &:hover:not(:disabled) {
    background: rgba(0, 0, 0, 0.04);
    border-color: rgba(201, 100, 66, 0.35);
    color: var(--yamato-color-text-primary);
  }

  &:disabled {
    cursor: not-allowed;
  }
}

.knowledge-upload-btn__spinner {
  width: 12px;
  height: 12px;
  border: 2px solid rgba(255, 255, 255, 0.45);
  border-top-color: #ffffff;
  border-radius: 50%;
  animation: knowledge-upload-spin 0.9s linear infinite;
}

.knowledge-upload-btn--uploading {
  border-color: var(--yamato-color-accent);
  background: var(--yamato-color-accent);
  color: #ffffff;
  box-shadow: var(--yamato-focus-ring);
  animation: knowledge-upload-pulse 1.3s ease-in-out infinite;
}

@keyframes knowledge-upload-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

@keyframes knowledge-upload-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 3px rgba(201, 100, 66, 0.2);
  }
  50% {
    box-shadow: 0 0 0 6px rgba(201, 100, 66, 0.28);
  }
}

.token-indicator {
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--yamato-color-border-subtle);
  border-radius: var(--yamato-radius-pill);
  background: var(--yamato-color-surface);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: default;

  .token-indicator-label {
    font-size: 11px;
    font-weight: 600;
    color: var(--yamato-color-text-secondary);
    white-space: nowrap;
  }

  .token-indicator-progress {
    width: 34px;
    height: 4px;
    background-color: rgba(0, 0, 0, 0.12);
    border-radius: 2px;
    overflow: hidden;

    .token-indicator-bar {
      height: 100%;
      border-radius: 2px;
      transition: width 0.3s ease, background-color 0.3s ease;
    }
  }
}

.knowledge-upload-input {
  position: absolute;
  width: 0;
  height: 0;
  opacity: 0;
  pointer-events: none;
}

.search-mode-menu {
  position: fixed;
  background: var(--yamato-color-surface);
  border: 1px solid var(--yamato-color-border-subtle);
  border-radius: var(--yamato-radius-sm);
  box-shadow: var(--yamato-shadow-overlay);
  padding: 4px;
  min-width: 110px;
  z-index: 9999;
}

.search-mode-item {
  width: 100%;
  padding: 8px 12px;
  border: none;
  background: transparent;
  text-align: left;
  font-size: 13px;
  color: var(--yamato-color-text-primary);
  cursor: pointer;
  border-radius: 4px;
  transition: background 0.15s ease;
  display: block;
  white-space: nowrap;

  &:hover {
    background: rgba(0, 0, 0, 0.06);
  }

  &--active {
    color: var(--yamato-color-accent);
    font-weight: 600;

    &::before {
      content: '✓ ';
    }
  }
}

@media (max-width: 900px) {
  .chat-input-container {
    padding: 14px 12px 10px;
  }

  .chat-input-wrapper {
    width: 100%;
  }

  .chat-welcome__text {
    font-size: 28px;
  }
}

</style>
