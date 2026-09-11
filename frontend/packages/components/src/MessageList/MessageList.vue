<template>
  <div :class="wrapperClass">
    <template v-if="variant === 'history'">
      <div class="chat-history-panel__actions">
        <button class="chat-history-panel__action" type="button" @click="onCreateClick">
          <span class="chat-history-panel__icon" aria-hidden="true">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
              <path
                d="M12 5v14M5 12h14"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
              />
            </svg>
          </span>
          <span class="chat-history-panel__label">新聊天</span>
        </button>
        <slot name="history-actions" />
      </div>

      <div class="chat-history-panel__list">
        <div
          v-for="chat in internalHistoryItems"
          :key="chat.id"
          class="chat-history-item"
          :class="{
            'chat-history-item--active': chat.id === activeItemId,
            'chat-history-item--editing': editingItemId === chat.id,
          }"
        >
          <div class="chat-history-item__content" @click="activeItemId = chat.id; emit('select', chat.id)">
            <input
              v-if="editingItemId === chat.id"
              class="chat-history-item__title-input"
              type="text"
              :value="editingTitle"
              @input="onInputChange"
              @blur="onRenameCommit(chat.id)"
              @keydown.enter.prevent="onRenameCommit(chat.id)"
              @keydown.esc="emit('rename-cancel')"
              @click.stop
            />
            <div v-else class="chat-history-item__title">{{ chat.title }}</div>
          </div>
          <div class="chat-history-item__actions">
            <button
              class="chat-history-item__menu-btn"
              type="button"
              @click.stop="toggleMenu(chat.id, $event)"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="5" r="2" fill="currentColor" />
                <circle cx="12" cy="12" r="2" fill="currentColor" />
                <circle cx="12" cy="19" r="2" fill="currentColor" />
              </svg>
            </button>
          </div>
        </div>
      </div>

      <Teleport to="body">
        <div
          v-if="activeMenuId"
          class="chat-history-item__menu"
          :style="menuPosition"
          @click.stop
        >
          <button
            class="chat-history-item__menu-item"
            type="button"
            @click="onStartRename"
          >
            重命名
          </button>
          <button
            class="chat-history-item__menu-item"
            type="button"
            @click="onArchive"
          >
            归档
          </button>
          <button
            class="chat-history-item__menu-item chat-history-item__menu-item--danger"
            type="button"
            @click="onDelete"
          >
            删除
          </button>
        </div>
      </Teleport>
    </template>

    <template v-else>
      <slot />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

interface HistoryItem {
  id: string
  title: string
}

interface Props {
  variant?: 'messages' | 'history'
  historyItems?: HistoryItem[]
  editingItemId?: string | null
  editingTitle?: string
  renameApiBaseUrl?: string
  renameApiToken?: string
  renameAutoGenerate?: boolean
  renameUser?: string
  deleteApiBaseUrl?: string
  deleteApiToken?: string
  deleteUser?: string
}

const activeItemId = defineModel<string>('activeItemId')

const props = withDefaults(defineProps<Props>(), {
  variant: 'messages',
  historyItems: () => [],
  editingItemId: null,
  editingTitle: '',
  renameApiBaseUrl: '',
  renameApiToken: '',
  renameAutoGenerate: false,
  renameUser: '',
  deleteApiBaseUrl: '',
  deleteApiToken: '',
  deleteUser: '',
})

const emit = defineEmits<{
  create: [tempId: string]
  select: [id: string]
  'rename-start': [id: string]
  'rename-save': [id: string, title: string]
  'rename-cancel': []
  'rename-error': [id: string, message: string]
  archive: [id: string]
  delete: [id: string]
  'update:editingTitle': [value: string]
}>()

const activeMenuId = ref<string | null>(null)
const menuPosition = ref({ top: '0px', left: '0px' })
const savingRenameId = ref<string | null>(null)
const deletingChatId = ref<string | null>(null)
const internalHistoryItems = ref<HistoryItem[]>([])

const NEW_CHAT_TITLE = '新聊天'

const buildConversationTitle = (text: string): string => {
  const trimmed = text.trim().replace(/\s+/g, ' ')
  if (!trimmed) {
    return NEW_CHAT_TITLE
  }
  return trimmed.length > 24 ? `${trimmed.slice(0, 24)}...` : trimmed
}

const upsertConversationToTop = (chatId: string, title?: string) => {
  const index = internalHistoryItems.value.findIndex((chat) => chat.id === chatId)

  if (index === -1) {
    internalHistoryItems.value.unshift({
      id: chatId,
      title: title || NEW_CHAT_TITLE,
    })
    return
  }

  const existing = internalHistoryItems.value[index]
  if (title && (!existing.title || existing.title === NEW_CHAT_TITLE)) {
    existing.title = title
  }

  internalHistoryItems.value.splice(index, 1)
  internalHistoryItems.value.unshift(existing)
}

const removeConversationItem = (chatId: string) => {
  const index = internalHistoryItems.value.findIndex((chat) => chat.id === chatId)
  if (index !== -1) {
    internalHistoryItems.value.splice(index, 1)
  }
}

const beginNewConversation = (): string => {
  const tempId = `temp-${Date.now()}`
  internalHistoryItems.value.unshift({
    id: tempId,
    title: NEW_CHAT_TITLE,
  })
  return tempId
}

const touchConversation = (chatId: string) => {
  upsertConversationToTop(chatId)
}

const finalizeConversation = (payload: { tempId?: string; conversationId: string; firstUserMessage?: string }) => {
  const { tempId, conversationId, firstUserMessage = '' } = payload
  if (tempId) {
    removeConversationItem(tempId)
  }
  upsertConversationToTop(conversationId, buildConversationTitle(firstUserMessage))
}

const wrapperClass = computed(() => {
  if (props.variant === 'history') {
    return 'chat-history-panel'
  }
  return 'message-list'
})

const closeMenu = () => {
  activeMenuId.value = null
}

const toggleMenu = (chatId: string, event: MouseEvent) => {
  if (activeMenuId.value === chatId) {
    closeMenu()
    return
  }

  const target = event.currentTarget as HTMLElement | null
  if (target) {
    const rect = target.getBoundingClientRect()
    menuPosition.value = {
      top: `${rect.top}px`,
      left: `${rect.right + 4}px`,
    }
  }

  activeMenuId.value = chatId
}

const onInputChange = (event: Event) => {
  const target = event.target as HTMLInputElement
  emit('update:editingTitle', target.value)
}

const onCreateClick = () => {
  const tempId = beginNewConversation()
  activeItemId.value = tempId
  emit('create', tempId)
}

const onRenameCommit = async (chatId: string) => {
  if (savingRenameId.value === chatId) {
    return
  }

  const targetChat = internalHistoryItems.value.find((chat) => chat.id === chatId)
  const newTitle = props.editingTitle.trim()

  if (!targetChat || !newTitle) {
    emit('rename-cancel')
    return
  }

  if (newTitle === targetChat.title) {
    upsertConversationToTop(chatId, newTitle)
    emit('rename-save', chatId, newTitle)
    return
  }

  if (!props.renameApiBaseUrl) {
    emit('rename-error', chatId, '缺少重命名接口地址')
    emit('rename-cancel')
    return
  }

  const renameUser = props.renameUser.trim()
  if (!renameUser) {
    emit('rename-error', chatId, '缺少用户标识，无法重命名会话')
    emit('rename-cancel')
    return
  }

  savingRenameId.value = chatId

  try {
    const response = await fetch(
      `${props.renameApiBaseUrl}/conversations/${encodeURIComponent(chatId)}/name`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(props.renameApiToken ? { Authorization: `Bearer ${props.renameApiToken}` } : {}),
        },
        body: JSON.stringify({
          name: newTitle,
          auto_generate: props.renameAutoGenerate,
          user: renameUser,
        }),
      }
    )

    if (!response.ok) {
      let message = '重命名会话失败'
      try {
        const error = await response.json()
        if (error && typeof error.message === 'string' && error.message) {
          message = error.message
        }
      } catch {
        // 解析失败则跳过
      }
      emit('rename-error', chatId, message)
      emit('rename-cancel')
      return
    }

    upsertConversationToTop(chatId, newTitle)
    emit('rename-save', chatId, newTitle)
  } catch {
    emit('rename-error', chatId, '重命名会话失败')
    emit('rename-cancel')
  } finally {
    savingRenameId.value = null
  }
}

const onStartRename = () => {
  if (!activeMenuId.value) {
    return
  }
  emit('rename-start', activeMenuId.value)
  closeMenu()
}

const onArchive = () => {
  if (!activeMenuId.value) {
    return
  }
  emit('archive', activeMenuId.value)
  closeMenu()
}

const onDelete = () => {
  if (!activeMenuId.value) {
    return
  }
  emit('delete', activeMenuId.value)
  closeMenu()
}

const deleteConversation = async (chatId: string): Promise<boolean> => {
  if (deletingChatId.value === chatId) {
    return false
  }

  if (!props.deleteApiBaseUrl) {
    emit('rename-error', chatId, '缺少删除接口地址')
    return false
  }

  const deleteUser = props.deleteUser.trim()
  if (!deleteUser) {
    emit('rename-error', chatId, '缺少用户标识，无法删除会话')
    return false
  }

  deletingChatId.value = chatId

  try {
    const response = await fetch(
      `${props.deleteApiBaseUrl}/conversations/${encodeURIComponent(chatId)}`,
      {
        method: 'DELETE',
        headers: {
          'Content-Type': 'application/json',
          ...(props.deleteApiToken ? { Authorization: `Bearer ${props.deleteApiToken}` } : {}),
        },
        body: JSON.stringify({
          user: deleteUser,
        }),
      }
    )

    if (!response.ok) {
      let message = `删除会话失败 (${response.status})`
      try {
        const error = await response.json()
        if (error && typeof error.message === 'string' && error.message) {
          message = error.message
        }
      } catch {
        // 解析失败则跳过
      }
      emit('rename-error', chatId, message)
      return false
    }

    removeConversationItem(chatId)
    return true
  } catch {
    emit('rename-error', chatId, '删除会话失败')
    return false
  } finally {
    deletingChatId.value = null
  }
}

defineExpose({
  deleteConversation,
  beginNewConversation,
  touchConversation,
  finalizeConversation,
  removeConversationItem,
})

watch(
  () => props.historyItems,
  (items) => {
    internalHistoryItems.value = [...items]
  },
  { immediate: true }
)

const onDocumentClick = () => {
  closeMenu()
}

watch(
  () => props.editingItemId,
  (editingItemId) => {
    if (!editingItemId) {
      return
    }

    nextTick(() => {
      const input = document.querySelector('.chat-history-item__title-input') as HTMLInputElement | null
      if (input) {
        input.focus()
        input.select()
      }
    })
  }
)

onMounted(() => {
  document.addEventListener('click', onDocumentClick)
})

onUnmounted(() => {
  document.removeEventListener('click', onDocumentClick)
})
</script>

<style lang="scss" scoped>
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.chat-history-panel {
  width: 160px;
  background: #ffffff;
  border-right: 1px solid #e8eaed;
  padding: 8px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.chat-history-panel__actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 8px;
  padding-left: 4px;
  padding-right: 4px;
}

.chat-history-panel__action {
  height: 40px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: #202124;
  cursor: pointer;
  text-align: left;
  padding: 0 12px;
  font-size: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
  transition: background 0.2s ease;

  &:hover {
    background: #f1f3f4;
  }
}

.chat-history-panel__icon {
  width: 14px;
  height: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #5f6368;
  flex: 0 0 auto;
}

.chat-history-panel__label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chat-history-panel__list {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding-left: 4px;
  padding-right: 4px;
  min-height: 0;
}

.chat-history-item {
  padding: 8px;
  border-radius: 10px;
  margin-bottom: 4px;
  background: transparent;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;

  &:hover {
    background: #f8f9fa;

    .chat-history-item__menu-btn {
      opacity: 1;
    }
  }
}

.chat-history-item--active {
  background: #f1f3f4;

  .chat-history-item__menu-btn {
    opacity: 1;
  }

  &:hover {
    background: #f8f9fa;
  }
}

.chat-history-item--editing {
  background: #f8f9fa;

  .chat-history-item__menu-btn {
    opacity: 1;
  }

  &:hover {
    background: #f8f9fa;
  }
}

.chat-history-item__content {
  flex: 1;
  min-width: 0;
  cursor: pointer;
}

.chat-history-item__title {
  font-size: 13px;
  font-weight: 600;
  color: #202124;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chat-history-item__title-input {
  width: 100%;
  max-width: 100%;
  height: 20px;
  font-size: 13px;
  font-weight: 600;
  line-height: 20px;
  color: #202124;
  text-align: left;
  border: 1px solid transparent;
  border-radius: 4px;
  padding: 0 2px;
  vertical-align: middle;
  outline: none;
  background: transparent;
  box-sizing: border-box;

  &:focus {
    border-color: transparent;
    box-shadow: none;
  }

  &::selection {
    background: #d2e3fc;
    color: #174ea6;
  }
}

.chat-history-item__actions {
  position: static;
  flex-shrink: 0;
}

.chat-history-item__menu-btn {
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  border-radius: 4px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #5f6368;
  opacity: 0;
  transition: opacity 0.2s ease, background 0.2s ease;
  position: relative;

  &:hover {
    background: #e8eaed;
  }
}

.chat-history-item__menu {
  position: fixed;
  background: #ffffff;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  padding: 4px;
  min-width: 120px;
  z-index: 9999;
  white-space: nowrap;
}

.chat-history-item__menu-item {
  width: 100%;
  padding: 8px 12px;
  border: none;
  background: transparent;
  text-align: left;
  font-size: 13px;
  color: #202124;
  cursor: pointer;
  border-radius: 4px;
  transition: background 0.2s ease;
  display: block;

  &:hover {
    background: #f1f3f4;
  }

  &--danger {
    color: #d93025;

    &:hover {
      background: #fce8e6;
    }
  }
}
</style>
