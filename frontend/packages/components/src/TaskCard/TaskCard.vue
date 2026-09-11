<template>
  <div class="task-card">
    <div class="task-card__header">
      <div class="task-card__title">{{ title }}</div>
      <div :class="['task-card__status', `task-card__status--${status}`]">
        {{ statusText }}
      </div>
    </div>

    <div class="task-card__progress">
      <div class="task-card__progress-meta">
        <div class="task-card__progress-label">进度</div>
        <div class="task-card__progress-value">{{ displayProgress }}%</div>
      </div>
      <div class="task-card__progress-bar" role="progressbar" :aria-valuenow="displayProgress" aria-valuemin="0" aria-valuemax="100">
        <div class="task-card__progress-bar-inner" :style="{ width: `${displayProgress}%` }" />
      </div>
    </div>

    <div class="task-card__actions">
      <Button class="task-card__action task-card__action--refresh" variant="secondary" @click="emit('refresh')">
        刷新
      </Button>
      <Button
        class="task-card__action task-card__action--cancel"
        variant="secondary"
        :disabled="status !== 'in_progress'"
        @click="emit('cancel')"
      >
        取消
      </Button>
      <Button class="task-card__action task-card__action--delete" variant="secondary" @click="emit('delete')">
        删除
      </Button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import Button from '../Button/Button.vue'

type TaskStatus = 'in_progress' | 'cancelled' | 'completed'

interface Props {
  title: string
  status: TaskStatus
  progress: number
}

const props = defineProps<Props>()

const emit = defineEmits<{
  refresh: []
  cancel: []
  delete: []
}>()

const displayProgress = computed(() => {
  const safe = Number.isFinite(props.progress) ? props.progress : 0
  const clamped = Math.min(100, Math.max(0, safe))
  return props.status === 'completed' ? 100 : Math.round(clamped)
})

const statusText = computed(() => {
  if (props.status === 'in_progress') return '进行中'
  if (props.status === 'cancelled') return '已取消'
  return '已完成'
})
</script>

<style scoped lang="scss">
.task-card {
  background: #ffffff;
  border: 1px solid #e8eaed;
  border-radius: 12px;
  padding: 16px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.task-card__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.task-card__title {
  font-size: 14px;
  font-weight: 600;
  color: #202124;
  line-height: 1.4;
}

.task-card__status {
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  margin-top: 2px;
  user-select: none;

  &--in_progress {
    color: #1a73e8;
  }

  &--cancelled {
    color: #d93025;
  }

  &--completed {
    color: #188038;
  }
}

.task-card__progress-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.task-card__progress-label {
  font-size: 12px;
  color: #5f6368;
}

.task-card__progress-value {
  font-size: 12px;
  font-weight: 600;
  color: #202124;
}

.task-card__progress-bar {
  width: 100%;
  height: 10px;
  background: #f1f3f4;
  border-radius: 999px;
  overflow: hidden;
}

.task-card__progress-bar-inner {
  height: 100%;
  background: #4285f4;
  border-radius: 999px;
  transition: width 0.2s ease;
}

.task-card__actions {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 10px;
}

/* action buttons: keep secondary variant but use semantic colors */
:deep(.task-card__action--refresh) {
  background: #e8f0fe;
  color: #1a73e8;
}

:deep(.task-card__action--refresh:hover:not(.btn--disabled)) {
  background: #d2e3fc;
}

:deep(.task-card__action--cancel) {
  background: rgba(217, 48, 37, 0.12);
  color: #d93025;
}

:deep(.task-card__action--cancel:hover:not(.btn--disabled)) {
  background: rgba(217, 48, 37, 0.18);
}

:deep(.task-card__action--delete) {
  background: #f1f3f4;
  color: #3c4043;
}

:deep(.task-card__action--delete:hover:not(.btn--disabled)) {
  background: #e8eaed;
}
</style>

