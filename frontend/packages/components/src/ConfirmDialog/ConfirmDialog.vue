<template>
  <Teleport to="body">
    <Transition name="dialog-fade">
      <div v-if="modelValue" class="dialog-overlay" @click="handleOverlayClick">
        <div class="dialog-container" @click.stop>
          <div class="dialog-header">
            <h3 class="dialog-title">{{ title }}</h3>
            <button
              v-if="showClose"
              class="dialog-close"
              @click="handleCancel"
              aria-label="关闭"
            >
              ✕
            </button>
          </div>
          
          <div class="dialog-body">
            <slot>
              <p class="dialog-message">{{ message }}</p>
            </slot>
          </div>
          
          <div class="dialog-footer">
            <button
              class="dialog-btn dialog-btn--cancel"
              @click="handleCancel"
            >
              {{ cancelText }}
            </button>
            <button
              class="dialog-btn dialog-btn--confirm"
              :class="{ [`dialog-btn--${type}`]: type }"
              @click="handleConfirm"
            >
              {{ confirmText }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
interface Props {
  modelValue: boolean
  title?: string
  message?: string
  confirmText?: string
  cancelText?: string
  showClose?: boolean
  closeOnClickOverlay?: boolean
  type?: 'primary' | 'danger' | 'warning'
}

const props = withDefaults(defineProps<Props>(), {
  title: '确认操作',
  message: '您确定要执行此操作吗？',
  confirmText: '确认',
  cancelText: '取消',
  showClose: true,
  closeOnClickOverlay: true,
  type: 'primary',
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  confirm: []
  cancel: []
}>()

const handleConfirm = () => {
  emit('confirm')
  emit('update:modelValue', false)
}

const handleCancel = () => {
  emit('cancel')
  emit('update:modelValue', false)
}

const handleOverlayClick = () => {
  if (props.closeOnClickOverlay) {
    handleCancel()
  }
}
</script>

<style lang="scss" scoped>
.dialog-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(20, 20, 19, 0.4);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 20px;
}

.dialog-container {
  background: #ffffff;
  border-radius: var(--yamato-radius-lg);
  box-shadow: var(--yamato-shadow-overlay);
  border: 1px solid var(--yamato-color-border-subtle);
  min-width: 320px;
  max-width: 500px;
  width: 100%;
  overflow: hidden;
}

.dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px;
  border-bottom: 1px solid var(--yamato-color-border-subtle);
}

.dialog-title {
  margin: 0;
  font-size: 21px;
  font-weight: 600;
  line-height: 1.19;
  letter-spacing: 0;
  color: var(--yamato-color-text-primary);
}

.dialog-close {
  border: none;
  background: transparent;
  font-size: 20px;
  color: var(--yamato-color-text-muted);
  cursor: pointer;
  padding: 0;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  transition: all 0.2s ease;

  &:hover {
    background-color: rgba(0, 0, 0, 0.05);
    color: var(--yamato-color-text-primary);
  }

  &:focus-visible {
    outline: none;
    box-shadow: var(--yamato-focus-ring);
  }
}

.dialog-body {
  padding: 24px;
  color: var(--yamato-color-text-secondary);
  font-size: 16px;
  line-height: 1.6;
  letter-spacing: normal;
}

.dialog-message {
  margin: 0;
}

.dialog-footer {
  display: flex;
  gap: 12px;
  padding: 16px 24px;
  border-top: 1px solid var(--yamato-color-border-subtle);
  justify-content: flex-end;
}

.dialog-btn {
  min-height: 36px;
  padding: 0 20px;
  border: none;
  border-radius: var(--yamato-radius-sm);
  font-size: 14px;
  font-weight: 400;
  cursor: pointer;
  transition: all 0.2s ease;
  outline: none;

  &:active {
    transform: translateY(1px);
  }

  &:focus-visible {
    box-shadow: var(--yamato-focus-ring);
  }
}

.dialog-btn--cancel {
  background: var(--yamato-color-surface-alt);
  color: #4d4c48;

  &:hover {
    background: #e8e6dc;
  }
}

.dialog-btn--confirm {
  color: #fff;

  &.dialog-btn--primary {
    background: var(--yamato-color-accent);

    &:hover {
      background: var(--yamato-color-accent-hover);
    }
  }

  &.dialog-btn--danger {
    background: var(--yamato-color-danger);

    &:hover {
      filter: brightness(1.04);
    }
  }

  &.dialog-btn--warning {
    background: var(--yamato-color-warning);

    &:hover {
      filter: brightness(1.04);
    }
  }
}

.dialog-fade-enter-active,
.dialog-fade-leave-active {
  transition: opacity 0.2s ease;

  .dialog-container {
    transition: transform 0.2s ease;
  }
}

.dialog-fade-enter-from,
.dialog-fade-leave-to {
  opacity: 0;

  .dialog-container {
    transform: scale(0.95);
  }
}
</style>
