<template>
  <div class="input-wrapper">
    <textarea
      v-if="props.multiline"
      ref="textareaRef"
      :class="['input', 'input--multiline', { 'input--disabled': props.disabled }]"
      :placeholder="props.placeholder"
      :value="props.modelValue"
      :disabled="props.disabled"
      :rows="props.rows"
      @input="handleInput"
      @keydown.enter.exact.prevent="handleEnter"
    ></textarea>
    <input
      v-else
      :class="['input', { 'input--disabled': props.disabled }]"
      :type="props.type"
      :placeholder="props.placeholder"
      :value="props.modelValue"
      :disabled="props.disabled"
      @input="handleInput"
      @keydown.enter="handleEnter"
    />
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'

interface Props {
  modelValue: string
  type?: string
  placeholder?: string
  disabled?: boolean
  multiline?: boolean
  rows?: number
}

const props = withDefaults(defineProps<Props>(), {
  type: 'text',
  placeholder: '',
  disabled: false,
  multiline: false,
  rows: 1,
})

const textareaRef = ref<HTMLTextAreaElement | null>(null)

const emit = defineEmits<{
  'update:modelValue': [value: string]
  enter: []
}>()

const handleInput = (event: Event) => {
  const target = event.target as HTMLInputElement | HTMLTextAreaElement
  emit('update:modelValue', target.value)

  if (props.multiline && target instanceof HTMLTextAreaElement) {
    nextTick(() => {
      adjustTextareaHeight(target, true)
    })
  }
}

const handleEnter = () => {
  emit('enter')
}

const adjustTextareaHeight = (
  element?: HTMLTextAreaElement | null,
  scrollToEnd = false
) => {
  const textarea = element ?? textareaRef.value
  if (!textarea || !props.multiline) {
    return
  }

  const computedStyle = window.getComputedStyle(textarea)
  const lineHeight = Number.parseFloat(computedStyle.lineHeight) || 24
  const paddingTop = Number.parseFloat(computedStyle.paddingTop) || 0
  const paddingBottom = Number.parseFloat(computedStyle.paddingBottom) || 0
  const borderTop = Number.parseFloat(computedStyle.borderTopWidth) || 0
  const borderBottom = Number.parseFloat(computedStyle.borderBottomWidth) || 0
  const verticalOffset = paddingTop + paddingBottom + borderTop + borderBottom

  const minRows = Math.max(1, props.rows)
  const minHeight = lineHeight * minRows + verticalOffset
  const maxHeight = lineHeight * 5 + verticalOffset

  textarea.style.height = 'auto'
  const desiredHeight = Math.max(minHeight, Math.min(textarea.scrollHeight, maxHeight))
  textarea.style.height = `${desiredHeight}px`

  const hasOverflow = textarea.scrollHeight > maxHeight
  textarea.style.overflowY = hasOverflow ? 'auto' : 'hidden'

  if (hasOverflow && scrollToEnd) {
    textarea.scrollTop = textarea.scrollHeight
  }
}

onMounted(() => {
  if (props.multiline) {
    adjustTextareaHeight()
  }
})

watch(
  () => [props.modelValue, props.multiline, props.rows],
  () => {
    nextTick(() => {
      adjustTextareaHeight()
    })
  }
)
</script>

<style lang="scss" scoped>
.input-wrapper {
  width: 100%;
}

.input {
  width: 100%;
  padding: 10px 14px;
  border: 1px solid var(--yamato-color-border-subtle);
  border-radius: var(--yamato-radius-sm);
  box-sizing: border-box;
  font-size: 16px;
  line-height: 1.6;
  letter-spacing: normal;
  color: var(--yamato-color-text-primary);
  outline: none;
  transition: all 0.2s ease;
  background: #ffffff;

  &:focus {
    border-color: var(--yamato-color-accent);
    box-shadow: var(--yamato-focus-ring);
  }

  &--disabled {
    background: var(--yamato-color-surface-alt);
    color: var(--yamato-color-text-muted);
    cursor: not-allowed;
    opacity: 0.72;
  }

  &--multiline {
    resize: none;
    font-family: inherit;
    line-height: 1.5;
    padding-bottom: 12px;
    max-height: calc(1.5em * 5 + 26px); /* 5行 + padding */
    overflow-y: hidden;
    scrollbar-width: thin;
    scrollbar-color: rgba(77, 76, 72, 0.3) transparent;

    &::-webkit-scrollbar {
      width: 8px;
    }

    &::-webkit-scrollbar-track {
      background: transparent;
      margin: 6px 0;
    }

    &::-webkit-scrollbar-thumb {
      background: rgba(77, 76, 72, 0.3);
      border-radius: 8px;
      border: 2px solid transparent;
      background-clip: content-box;
    }

    &::-webkit-scrollbar-thumb:hover {
      background: rgba(77, 76, 72, 0.42);
      background-clip: content-box;
    }
    
    /* 隐藏滚动条调整按钮 */
    &::-webkit-resizer {
      display: none;
    }
    
    /* 隐藏滚动条按钮（箭头） */
    &::-webkit-scrollbar-button {
      display: none;
    }
  }

  &::placeholder {
    color: var(--yamato-color-text-muted);
  }
}
</style>
