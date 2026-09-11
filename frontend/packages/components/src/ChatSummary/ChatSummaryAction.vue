<template>
  <button class="chat-summary-action" type="button" :disabled="disabled" @click="emit('click')">
    <span class="chat-summary-action__icon" aria-hidden="true">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
        <path
          d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7A2.5 2.5 0 0 1 17.5 16H11l-4.5 4v-4H6.5A2.5 2.5 0 0 1 4 13.5v-7Z"
          stroke="currentColor"
          stroke-width="1.8"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
    </span>
    <span class="chat-summary-action__label">对话摘要</span>
  </button>
</template>

<script setup lang="ts">
interface Props {
  disabled?: boolean
}

withDefaults(defineProps<Props>(), {
  disabled: false,
})

const emit = defineEmits<{
  click: []
}>()
</script>

<style lang="scss" scoped>
.chat-summary-action {
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

  &:hover:not(:disabled) {
    background: #f1f3f4;
  }

  &:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
}

.chat-summary-action__icon {
  width: 14px;
  height: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #5f6368;
  flex: 0 0 auto;
}

.chat-summary-action__label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>