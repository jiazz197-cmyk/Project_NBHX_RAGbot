/* 一个任务完成提示组件，用于在页面顶部居中显示提示信息，并在一定时间后自动消失 */
<template>
  <Transition name="toast">
    <div v-if="visible" :class="['toast', `toast--${type}`]">
      <div class="toast__icon">
        <svg v-if="type === 'success'" width="20" height="20" viewBox="0 0 20 20" fill="none">
          <path d="M16.6667 5L7.50004 14.1667L3.33337 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <svg v-else-if="type === 'error'" width="20" height="20" viewBox="0 0 20 20" fill="none">
          <path d="M15 5L5 15M5 5L15 15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <svg v-else-if="type === 'warning'" width="20" height="20" viewBox="0 0 20 20" fill="none">
          <path d="M10 6V10M10 14H10.01M18.3333 10C18.3333 14.6024 14.6024 18.3333 10 18.3333C5.39763 18.3333 1.66667 14.6024 1.66667 10C1.66667 5.39763 5.39763 1.66667 10 1.66667C14.6024 1.66667 18.3333 5.39763 18.3333 10Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <svg v-else width="20" height="20" viewBox="0 0 20 20" fill="none">
          <path d="M10 10V14M10 6H10.01M18.3333 10C18.3333 14.6024 14.6024 18.3333 10 18.3333C5.39763 18.3333 1.66667 14.6024 1.66667 10C1.66667 5.39763 5.39763 1.66667 10 1.66667C14.6024 1.66667 18.3333 5.39763 18.3333 10Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
      <div class="toast__content">
        {{ message }}
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'

interface Props {
  message: string
  type?: 'success' | 'error' | 'warning' | 'info'
  duration?: number
  show?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  type: 'success',
  duration: 3000,
  show: false,
})

const emit = defineEmits<{
  close: []
}>()

const visible = ref(false)
let timer: number | null = null

const close = () => {
  visible.value = false
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
  setTimeout(() => {
    emit('close')
  }, 300) // Wait for transition to complete
}

watch(() => props.show, (newValue) => {
  if (newValue) {
    visible.value = true
    if (props.duration > 0) {
      timer = window.setTimeout(() => {
        close()
      }, props.duration)
    }
  } else {
    close()
  }
}, { immediate: true })

onMounted(() => {
  if (props.show) {
    visible.value = true
    if (props.duration > 0) {
      timer = window.setTimeout(() => {
        close()
      }, props.duration)
    }
  }
})
</script>

<style lang="scss" scoped>
.toast {
  position: fixed;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 20px;
  border-radius: var(--yamato-radius-sm);
  background: #ffffff;
  border: 1px solid var(--yamato-color-border-subtle);
  box-shadow: var(--yamato-shadow-card);
  z-index: 9999;
  max-width: 500px;
  min-width: 300px;

  &__icon {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
  }

  &__content {
    flex: 1;
    font-size: 15px;
    line-height: 1.6;
    color: var(--yamato-color-text-primary);
  }

  &--success {
    .toast__icon {
      color: var(--yamato-color-success);
    }
  }

  &--error {
    .toast__icon {
      color: var(--yamato-color-danger);
    }
  }

  &--warning {
    .toast__icon {
      color: var(--yamato-color-warning);
    }
  }

  &--info {
    .toast__icon {
      color: var(--yamato-color-accent);
    }
  }
}

.toast-enter-active,
.toast-leave-active {
  transition: all 0.3s ease;
}

.toast-enter-from {
  opacity: 0;
  transform: translateX(-50%) translateY(-20px);
}

.toast-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(-20px);
}
</style>
