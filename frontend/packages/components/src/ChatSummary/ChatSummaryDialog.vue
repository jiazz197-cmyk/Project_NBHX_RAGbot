/* 对话摘要的弹窗 */
<template>
  <Teleport to="body">
    <Transition name="summary-dialog-fade">
      <div v-if="modelValue" class="summary-dialog-overlay" @click="handleOverlayClick">
        <div class="summary-dialog" @click.stop>
          <header class="summary-dialog__header">
            <h3 class="summary-dialog__title">对话摘要</h3>
            <button class="summary-dialog__close" type="button" aria-label="关闭" @click="closeDialog">
              ✕
            </button>
          </header>

          <section class="summary-dialog__body">
            <div v-if="loading" class="summary-dialog__state">正在读取摘要...</div>
            <div v-else-if="errorMessage" class="summary-dialog__state summary-dialog__state--error">
              {{ errorMessage }}
            </div>
            <div v-else-if="summary" class="summary-dialog__content">
              <div class="summary-dialog__meta">
                <div class="summary-dialog__meta-item">
                  <span class="summary-dialog__meta-label">用户</span>
                  <span class="summary-dialog__meta-value">{{ summary.user_id }}</span>
                </div>
                <div class="summary-dialog__meta-item">
                  <span class="summary-dialog__meta-label">摘要状态</span>
                  <span class="summary-dialog__meta-value">{{ summary.exists ? '已生成' : '暂无摘要' }}</span>
                </div>
              </div>

              <div class="summary-dialog__summary-block">
                <div class="summary-dialog__summary-title">最新摘要</div>
                <p class="summary-dialog__summary-text">{{ summary.latest_summary || '暂无摘要内容' }}</p>
              </div>
            </div>
            <div v-else class="summary-dialog__state">暂无摘要内容</div>
          </section>

          <footer class="summary-dialog__footer">
            <button class="summary-dialog__btn summary-dialog__btn--ghost" type="button" @click="loadSummary">
              刷新
            </button>
            <button class="summary-dialog__btn summary-dialog__btn--primary" type="button" @click="closeDialog">
              关闭
            </button>
          </footer>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { useChatSummary, type UserSummaryResult } from './useChatSummary'

interface Props {
  modelValue: boolean
  userId: string
  apiBaseUrl: string
  apiToken?: string
}

const props = defineProps<Props>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const loading = ref(false)
const errorMessage = ref('')
const summary = ref<UserSummaryResult | null>(null)
let latestLoadRequestId = 0

const normalizedUserId = computed(() => String(props.userId ?? '').trim())

const { loadUserSummary } = useChatSummary({
  apiBaseUrl: props.apiBaseUrl,
  apiToken: props.apiToken,
})

const loadSummary = async () => {
  const requestId = ++latestLoadRequestId
  if (!normalizedUserId.value) {
    if (requestId === latestLoadRequestId) {
      summary.value = null
      errorMessage.value = '缺少 user_id，无法读取摘要'
    }
    return
  }

  loading.value = true
  errorMessage.value = ''

  try {
    const result = await loadUserSummary(normalizedUserId.value)
    if (requestId !== latestLoadRequestId) {
      return
    }
    summary.value = result
  } catch (error) {
    if (requestId !== latestLoadRequestId) {
      return
    }
    summary.value = null
    errorMessage.value = error instanceof Error ? error.message : '读取摘要失败'
  } finally {
    if (requestId === latestLoadRequestId) {
      loading.value = false
    }
  }
}

const closeDialog = () => {
  emit('update:modelValue', false)
}

const handleOverlayClick = () => {
  closeDialog()
}

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) {
      loadSummary()
    }
  }
)

watch(normalizedUserId, () => {
  if (props.modelValue) {
    loadSummary()
  }
})
</script>

<style lang="scss" scoped>
.summary-dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.summary-dialog {
  width: min(680px, 100%);
  max-height: min(80vh, 780px);
  display: flex;
  flex-direction: column;
  border-radius: 16px;
  overflow: hidden;
  background: #ffffff;
  border: 1px solid #e8eaed;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.18);
}

.summary-dialog__header {
  height: 64px;
  padding: 0 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #f1f3f4;
}

.summary-dialog__title {
  margin: 0;
  font-size: 18px;
  line-height: 1;
  color: #202124;
  font-weight: 600;
}

.summary-dialog__close {
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: #5f6368;
  cursor: pointer;

  &:hover {
    background: #f1f3f4;
  }
}

.summary-dialog__body {
  padding: 20px;
  overflow-y: auto;
}

.summary-dialog__state {
  color: #5f6368;
  font-size: 14px;
  line-height: 1.6;
}

.summary-dialog__state--error {
  color: #d93025;
}

.summary-dialog__content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.summary-dialog__meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.summary-dialog__meta-item {
  border: 1px solid #e8eaed;
  border-radius: 10px;
  padding: 10px 12px;
  background: #f8f9fa;
}

.summary-dialog__meta-label {
  font-size: 12px;
  color: #9aa0a6;
  display: block;
}

.summary-dialog__meta-value {
  margin-top: 4px;
  font-size: 14px;
  color: #202124;
  font-weight: 500;
  display: block;
  word-break: break-all;
}

.summary-dialog__summary-block {
  border: 1px solid #e8eaed;
  border-radius: 12px;
  background: #ffffff;
  padding: 14px;
}

.summary-dialog__summary-title {
  font-size: 13px;
  color: #5f6368;
  margin-bottom: 8px;
}

.summary-dialog__summary-text {
  margin: 0;
  color: #202124;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.summary-dialog__footer {
  border-top: 1px solid #f1f3f4;
  padding: 14px 20px;
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.summary-dialog__btn {
  height: 36px;
  padding: 0 16px;
  border-radius: 8px;
  border: 1px solid transparent;
  font-size: 14px;
  cursor: pointer;
}

.summary-dialog__btn--ghost {
  background: #ffffff;
  color: #5f6368;
  border-color: #dadce0;

  &:hover {
    background: #f8f9fa;
  }
}

.summary-dialog__btn--primary {
  background: #202124;
  color: #ffffff;

  &:hover {
    background: #303134;
  }
}

.summary-dialog-fade-enter-active,
.summary-dialog-fade-leave-active {
  transition: opacity 0.2s ease;

  .summary-dialog {
    transition: transform 0.2s ease;
  }
}

.summary-dialog-fade-enter-from,
.summary-dialog-fade-leave-to {
  opacity: 0;

  .summary-dialog {
    transform: translateY(6px) scale(0.98);
  }
}
</style>