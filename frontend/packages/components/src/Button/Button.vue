<template>
  <button
    :class="['btn', `btn--${variant}`, { 'btn--disabled': disabled }]"
    :disabled="disabled"
    @click="handleClick"
  >
    <slot />
  </button>
</template>

<script setup lang="ts">
interface Props {
  variant?: 'primary' | 'secondary' | 'text'
  disabled?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  variant: 'primary',
  disabled: false,
})

const emit = defineEmits<{
  click: [event: MouseEvent]
}>()

const handleClick = (event: MouseEvent) => {
  if (!props.disabled) {
    emit('click', event)
  }
}
</script>

<style lang="scss" scoped>
.btn {
  height: 44px;
  padding: 0 18px;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  line-height: 1;
  cursor: pointer;
  transition: all 0.2s ease;
  outline: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  white-space: nowrap;

  &:hover:not(.btn--disabled) {
    opacity: 0.9;
    transform: translateY(-1px);
  }

  &:active:not(.btn--disabled) {
    transform: translateY(0);
  }

  &--primary {
    background: #4285f4;
    color: white;

    &:hover:not(.btn--disabled) {
      background: #357ae8;
    }
  }

  &--secondary {
    background: #e8f0fe;
    color: #1a73e8;

    &:hover:not(.btn--disabled) {
      background: #d2e3fc;
    }
  }

  &--text {
    background: transparent;
    color: #1a73e8;

    &:hover:not(.btn--disabled) {
      background: rgba(26, 115, 232, 0.1);
    }
  }

  &--disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
}
</style>
