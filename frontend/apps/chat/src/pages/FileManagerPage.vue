<template>
  <div class="quotation-page">
    <input
      ref="fileInputRef"
      class="hidden-input"
      type="file"
      accept="application/pdf,.pdf"
      @change="handleFileSelected"
    />

    <header class="quotation-header">
      <div>
        <h1 class="quotation-title">报价生成</h1>
        <p class="quotation-subtitle">
          共 {{ tasks.length }} 个任务 · WebSocket 连接 {{ wsConnections }} 个
        </p>
      </div>
      <div class="header-actions">
        <button class="secondary-btn" :disabled="loading" @click="() => loadTasks()">
          刷新
        </button>
        <button class="secondary-btn" :disabled="directU8Submitting" @click="openDirectU8Dialog">
          {{ directU8Submitting ? '提交中...' : '直接进行u8查询' }}
        </button>
        <button class="secondary-btn" :disabled="directProjectSubmitting" @click="openDirectProjectDialog">
          {{ directProjectSubmitting ? '提交中...' : '直接上传项目编码' }}
        </button>
        <button class="primary-btn" :disabled="uploading" @click="openFilePicker">
          {{ uploading ? '上传中...' : '上传PDF' }}
        </button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-tip">{{ errorMessage }}</p>

    <section class="columns">
      <article class="column">
        <header class="column-header">
          <h2>排队任务</h2>
          <span>{{ queuedTasks.length }}</span>
        </header>
        <div v-if="queuedTasks.length === 0" class="empty">暂无排队任务</div>
        <TaskItemCard
          v-for="task in queuedTasks"
          :key="task.task_id"
          :task="task"
          :show-owner="true"
          :can-cancel="canCancel(task)"
          :can-delete="false"
          @cancel="handleCancel(task.task_id)"
          @view-file="handleViewFile(task.task_id)"
          @download-u8-xlsx="handleDownloadU8Xlsx(task.task_id)"
        />
      </article>

      <article class="column">
        <header class="column-header">
          <h2>处理中任务</h2>
          <span>{{ runningTasks.length }}</span>
        </header>
        <div v-if="runningTasks.length === 0" class="empty">暂无处理中任务</div>
        <TaskItemCard
          v-for="task in runningTasks"
          :key="task.task_id"
          :task="task"
          :show-owner="true"
          :can-cancel="canCancel(task)"
          :can-delete="false"
          :is-approving="approvingTasks.has(task.task_id)"
          @cancel="handleCancel(task.task_id)"
          @approve="(partids: string[], extra: string[], extraEntries: any[]) => handleApprove(task.task_id, partids, extra, extraEntries)"
          @view-file="handleViewFile(task.task_id)"
          @download-u8-xlsx="handleDownloadU8Xlsx(task.task_id)"
        />
      </article>

      <article class="column column--done">
        <header class="column-header">
          <h2>完成任务</h2>
          <span>{{ doneTasks.length }}</span>
        </header>
        <p class="dev-tip">该功能仍在开发中，非最终版</p>
        <div class="task-list task-list--scroll">
          <div v-if="doneTasks.length === 0" class="empty">暂无完成任务</div>
          <TaskItemCard
            v-for="task in doneTasks"
            :key="task.task_id"
            :task="task"
            :show-owner="true"
            :can-cancel="false"
            :can-delete="canDelete(task)"
            @cancel="handleCancel(task.task_id)"
            @delete="handleDelete(task.task_id)"
            @view-file="handleViewFile(task.task_id)"
            @download-u8-xlsx="handleDownloadU8Xlsx(task.task_id)"
          />
        </div>
      </article>
    </section>

    <ConfirmDialog
      v-model="showDeleteDialog"
      title="删除任务"
      :message="`确定删除任务「${pendingDeleteTaskName}」吗？此操作不可恢复。`"
      type="danger"
      confirm-text="删除"
      cancel-text="取消"
      @confirm="confirmDeleteTask"
    />

    <ConfirmDialog
      v-model="showTaskNameDialog"
      title="任务名称"
      type="primary"
      confirm-text="上传"
      cancel-text="取消"
      @confirm="confirmUploadWithTaskName"
      @cancel="cancelTaskNameDialog"
    >
      <label class="task-name-field">
        <span class="task-name-field__label">请输入任务名称（仅用于展示）</span>
        <input
          v-model="pendingTaskName"
          class="task-name-field__input"
          type="text"
          maxlength="120"
          @keydown.enter.prevent="confirmUploadWithTaskName"
        />
      </label>
    </ConfirmDialog>

    <ConfirmDialog
      v-model="showDirectU8Dialog"
      title="直接进行 U8 查询"
      type="primary"
      confirm-text="提交"
      cancel-text="取消"
      @confirm="confirmDirectU8Submit"
      @cancel="cancelDirectU8Dialog"
    >
      <label class="task-name-field">
        <span class="task-name-field__label">请输入任务名称（仅用于展示）</span>
        <input
          v-model="directU8TaskName"
          class="task-name-field__input"
          type="text"
          maxlength="120"
        />
      </label>
      <div class="direct-u8-input-grid">
        <label class="task-name-field">
          <span class="task-name-field__label">编码（每行一条，最多 1500 个）</span>
          <textarea
            v-model="directU8PartidsText"
            class="task-name-field__input task-name-field__textarea"
            rows="8"
            maxlength="10000"
            placeholder="每行一个编码&#10;例如：010101"
          />
        </label>
        <label class="task-name-field">
          <span class="task-name-field__label">数量（每行一条，可留空默认为 1，支持小数最多四位）</span>
          <textarea
            v-model="directU8QuantitiesText"
            class="task-name-field__input task-name-field__textarea"
            rows="8"
            maxlength="10000"
            placeholder="按左侧编码行号对齐&#10;例如：5 或 1.5"
          />
        </label>
      </div>
    </ConfirmDialog>

    <ConfirmDialog
      v-model="showDirectProjectDialog"
      title="直接上传项目编码"
      type="primary"
      confirm-text="提交"
      cancel-text="取消"
      @confirm="confirmDirectProjectSubmit"
      @cancel="cancelDirectProjectDialog"
    >
      <label class="task-name-field">
        <span class="task-name-field__label">请输入任务名称（仅用于展示）</span>
        <input
          v-model="directProjectTaskName"
          class="task-name-field__input"
          type="text"
          maxlength="120"
        />
      </label>
      <label class="task-name-field" style="margin-top: 12px">
        <span class="task-name-field__label">项目编码（每行一条，最多 1500 个）</span>
        <textarea
          v-model="directProjectCodesText"
          class="task-name-field__input task-name-field__textarea"
          rows="8"
          maxlength="10000"
          placeholder="每行一个项目编码&#10;例如：60334P542"
        />
      </label>
    </ConfirmDialog>
  </div>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onMounted, onUnmounted, ref, watch } from 'vue'
import { ConfirmDialog } from '@yamato/components'
import { readUserRole } from '../services/storage'
import type { QuotationPdmItem, QuotationTaskItem } from '../types/quotation'
import {
  approveQuotationTask,
  cancelQuotationTask,
  createDirectU8Task,
  createQuotationTask,
  deleteQuotationTask,
  downloadQuotationTaskFile,
  downloadQuotationU8ByTypeWorkbook,
  getQuotationTask,
  listQuotationTasks,
} from '../services/quotation'
import { createTaskWebSocket } from '../services/ws'

const fileInputRef = ref<HTMLInputElement | null>(null)
const tasks = ref<QuotationTaskItem[]>([])
const loading = ref(false)
const uploading = ref(false)
const errorMessage = ref('')
const wsConnections = ref(0)
const showDeleteDialog = ref(false)
const pendingDeleteTaskId = ref('')
const pendingDeleteTaskName = ref('')
const showTaskNameDialog = ref(false)
const pendingUploadFile = ref<File | null>(null)
const pendingTaskName = ref('')
const pendingUploadId = ref(0)
const showDirectU8Dialog = ref(false)
const directU8PartidsText = ref('')
const directU8QuantitiesText = ref('')
const directU8TaskName = ref('')
const directU8Submitting = ref(false)

const showDirectProjectDialog = ref(false)
const directProjectCodesText = ref('')
const directProjectTaskName = ref('')
const directProjectSubmitting = ref(false)

const wsMap = new Map<string, WebSocket>()
const wsTaskEpochMap = new Map<string, number>()
const wsConnectQueue: string[] = []
const wsConnectQueuedTaskIds = new Set<string>()
let wsConnectBatchTimer: number | null = null
const refreshTimers = new Map<string, number>()
const refreshInFlightTaskIds = new Set<string>()
const refreshQueuedTaskIds = new Set<string>()
const approvalDetailRequestedTaskIds = new Set<string>()
let loadTasksAbortController: AbortController | null = null
let loadTasksTimeoutId: number | null = null
let isPageUnmounted = false

const wsDisabledUntilByTaskId = new Map<string, number>()
const taskSyncModeByTaskId = new Map<string, 'ws' | 'list'>()
const wsFailureCountByTaskId = new Map<string, number>()
const wsExpectedCloseReasonsByTaskId = new Map<string, string>()
const wsHeartbeatTimerByTaskId = new Map<string, number>()

let listRefreshTimerId: number | null = null
let listRefreshBackoffMultiplier = 1
let listRefreshSuccessiveFailures = 0

const ACTIVE_STATUSES: string[] = ['queued', 'running', 'awaiting_approval']
const TERMINAL_STATUSES: string[] = ['completed', 'failed', 'cancelled']
const ENABLE_TASK_WEBSOCKET = true
const DEBUG_FILE_MANAGER_DIAGNOSTICS = import.meta.env.DEV
const WS_CONNECT_BATCH_SIZE = 4
const WS_CONNECT_BATCH_INTERVAL_MS = 100
const UPLOAD_CREATE_TASK_PENDING_WARN_MS = 20000
const WS_FAILURE_WINDOW_MS = 30000
const WS_FAILURE_WARN_COUNT = 6
const WS_QUEUE_WARN_LENGTH = 12
const EVENT_LOOP_LAG_SAMPLE_INTERVAL_MS = 1000
const EVENT_LOOP_LAG_WARN_MS = 250
const WS_COOLDOWN_MS = 60000
const WS_MAX_PER_TASK_FAILURES = 2
// 单标签页最多维持的 live WS 连接数：超出部分由列表轮询覆盖（避免大量任务时 WS 扇出）。
// 按状态优先级（running→awaiting_approval→queued）取前 N 个任务建 WS。
const WS_MAX_LIVE_CONNECTIONS = 8
const LIST_REFRESH_FAST_MS = 10000
const LIST_REFRESH_NORMAL_MS = 60000
const LIST_REFRESH_BACKOFF_1_MS = 90000
const LIST_REFRESH_BACKOFF_2_MS = 120000
const LIST_REFRESH_BACKOFF_MAX_MS = 180000

let loadTasksRequestSeq = 0
let loadTasksActiveSeq = 0
let uploadAttemptSeq = 0
let stateEpoch = 0
let wsConnectAttemptSeq = 0
let wsConnectFailureSeq = 0
let wsConsecutiveFailures = 0
let wsMaxQueueLengthSeen = 0
let loadTasksAbortErrorCount = 0
let loadTasksForceAbortCount = 0
let loadTasksSkipWhileLoadingCount = 0
let loadTasksInFlightRequestId: number | null = null
let eventLoopLagProbeTimer: number | null = null
let eventLoopLagProbeLastTickMs = 0
let eventLoopLagWarnCount = 0
const wsFailureTimestamps: number[] = []

const logDiag = (event: string, details?: Record<string, unknown>): void => {
  if (!DEBUG_FILE_MANAGER_DIAGNOSTICS) return
  try {
    console.info('[FileManagerDiag]', {
      event,
      ts: new Date().toISOString(),
      route: window.location.pathname,
      tasks: tasks.value.length,
      wsConnections: wsMap.size,
      loading: loading.value,
      ...details,
    })
  } catch {
    // ignore logging failures
  }
}

const logDiagError = (event: string, error: unknown, details?: Record<string, unknown>): void => {
  if (!DEBUG_FILE_MANAGER_DIAGNOSTICS) return
  try {
    const cast = error as { message?: string; stack?: string; name?: string }
    console.error('[FileManagerDiagError]', {
      event,
      ts: new Date().toISOString(),
      route: window.location.pathname,
      tasks: tasks.value.length,
      wsConnections: wsMap.size,
      loading: loading.value,
      errorName: cast?.name,
      errorMessage: cast?.message,
      errorStack: cast?.stack,
      ...details,
    })
  } catch {
    // ignore logging failures
  }
}

const logDiagCritical = (event: string, details?: Record<string, unknown>): void => {
  if (!DEBUG_FILE_MANAGER_DIAGNOSTICS) return
  try {
    console.error('[FileManagerDiagCritical]', {
      event,
      ts: new Date().toISOString(),
      route: window.location.pathname,
      tasks: tasks.value.length,
      wsConnections: wsMap.size,
      loading: loading.value,
      ...details,
    })
  } catch {
    // ignore logging failures
  }
}

const computeApproxJsonSize = (value: unknown): number | null => {
  try {
    return JSON.stringify(value).length
  } catch {
    return null
  }
}



const pruneWsFailureTimestamps = (nowMs: number): void => {
  while (wsFailureTimestamps.length > 0 && nowMs - wsFailureTimestamps[0] > WS_FAILURE_WINDOW_MS) {
    wsFailureTimestamps.shift()
  }
}

const recordWsQueuePressure = (source: string): void => {
  const queueLength = wsConnectQueue.length
  if (queueLength > wsMaxQueueLengthSeen) {
    wsMaxQueueLengthSeen = queueLength
    logDiag('ws_queue_new_peak', {
      source,
      queueLength,
      maxQueueLengthSeen: wsMaxQueueLengthSeen,
      queuedTaskIds: wsConnectQueue.slice(0, 8),
    })
  }

  if (queueLength >= WS_QUEUE_WARN_LENGTH) {
    logDiag('ws_queue_pressure_warn', {
      source,
      queueLength,
      activeConnections: wsMap.size,
      activeTasks: tasks.value.filter((task) => ACTIVE_STATUSES.includes(task.status)).length,
      queuedTaskIds: wsConnectQueue.slice(0, 12),
    })
  }
}

const recordWsFailure = (source: string, taskId: string, error?: unknown): void => {
  const nowMs = Date.now()
  wsConnectFailureSeq += 1
  wsConsecutiveFailures += 1
  wsFailureTimestamps.push(nowMs)
  pruneWsFailureTimestamps(nowMs)

  const prevCount = wsFailureCountByTaskId.get(taskId) ?? 0
  const newCount = prevCount + 1
  wsFailureCountByTaskId.set(taskId, newCount)

  logDiagError('ws_failure_observed', error ?? new Error('ws connection failure'), {
    source,
    taskId,
    failureSeq: wsConnectFailureSeq,
    consecutiveFailures: wsConsecutiveFailures,
    perTaskFailureCount: newCount,
    perTaskThreshold: WS_MAX_PER_TASK_FAILURES,
    failuresInWindow: wsFailureTimestamps.length,
    failureWindowMs: WS_FAILURE_WINDOW_MS,
    queueLength: wsConnectQueue.length,
    connections: wsMap.size,
  })

  if (wsFailureTimestamps.length >= WS_FAILURE_WARN_COUNT) {
    logDiag('ws_failure_burst_detected', {
      source,
      taskId,
      failuresInWindow: wsFailureTimestamps.length,
      failureWindowMs: WS_FAILURE_WINDOW_MS,
      consecutiveFailures: wsConsecutiveFailures,
    })
  }

  if (newCount >= WS_MAX_PER_TASK_FAILURES) {
    const cooldownUntil = nowMs + WS_COOLDOWN_MS
    wsDisabledUntilByTaskId.set(taskId, cooldownUntil)
    // WS 降级：不再逐任务轮询，改为依赖列表轮询（单一事实源）。
    switchTaskSyncMode(taskId, 'list', 'ws_cooldown_after_failures')
    closeSocket(taskId, 'ws_cooldown')
    logDiagCritical('ws_task_cooldown_activated', {
      taskId,
      perTaskFailureCount: newCount,
      cooldownUntil,
      cooldownMs: WS_COOLDOWN_MS,
      syncMode: taskSyncModeByTaskId.get(taskId),
    })
  }
}

const recordWsConnectSuccess = (taskId: string): void => {
  const nowMs = Date.now()
  pruneWsFailureTimestamps(nowMs)
  if (wsConsecutiveFailures > 0) {
    logDiag('ws_recovered_after_failures', {
      taskId,
      consecutiveFailuresBeforeRecovery: wsConsecutiveFailures,
      failuresStillInWindow: wsFailureTimestamps.length,
    })
  }
  wsConsecutiveFailures = 0
  wsFailureCountByTaskId.delete(taskId)
  wsDisabledUntilByTaskId.delete(taskId)
}

const recordWsHandshakeSuccess = (taskId: string): void => {
  recordWsConnectSuccess(taskId)
}

const switchTaskSyncMode = (taskId: string, newMode: 'ws' | 'list', reason: string): void => {
  const oldMode = taskSyncModeByTaskId.get(taskId) ?? 'ws'
  if (oldMode === newMode) return
  taskSyncModeByTaskId.set(taskId, newMode)
  logDiagCritical('task_sync_mode_changed', {
    taskId,
    oldMode,
    newMode,
    reason,
  })
  if (newMode === 'list') {
    // WS 降级：状态由列表轮询（单一事实源）覆盖，不启逐任务 HTTP 轮询。
    // socket 已由调用方（recordWsFailure / ensureTaskSockets）关闭。
    logDiagCritical('task_sync_mode_ws_to_list', { taskId, reason })
  } else {
    wsDisabledUntilByTaskId.delete(taskId)
    wsFailureCountByTaskId.delete(taskId)
    logDiagCritical('task_sync_mode_list_to_ws', { taskId, reason })
  }
}

const getNavigatorOnline = (): boolean | null => {
  if (typeof navigator === 'undefined') return null
  return typeof navigator.onLine === 'boolean' ? navigator.onLine : null
}

const getUserAgent = (): string => {
  if (typeof navigator === 'undefined') return ''
  return String(navigator.userAgent ?? '')
}

const logPageEnvironment = (event: string, details?: Record<string, unknown>): void => {
  logDiag(event, {
    visibilityState: document.visibilityState,
    online: getNavigatorOnline(),
    hasFocus: typeof document.hasFocus === 'function' ? document.hasFocus() : null,
    userAgent: getUserAgent(),
    ...details,
  })
}

const getWebSocketReadyStateLabel = (socket: WebSocket | null | undefined): string => {
  if (!socket) return 'missing'
  if (socket.readyState === WebSocket.CONNECTING) return 'connecting'
  if (socket.readyState === WebSocket.OPEN) return 'open'
  if (socket.readyState === WebSocket.CLOSING) return 'closing'
  if (socket.readyState === WebSocket.CLOSED) return 'closed'
  return `unknown_${socket.readyState}`
}

const describeWebSocket = (socket: WebSocket | null | undefined): Record<string, unknown> => ({
  readyState: socket?.readyState ?? null,
  readyStateLabel: getWebSocketReadyStateLabel(socket),
  bufferedAmount: socket?.bufferedAmount ?? null,
  protocol: socket?.protocol ?? '',
  extensions: socket?.extensions ?? '',
  url: socket?.url ?? '',
})

const startEventLoopLagProbe = (): void => {
  if (eventLoopLagProbeTimer !== null) return
  eventLoopLagProbeLastTickMs = Date.now()

  eventLoopLagProbeTimer = window.setInterval(() => {
    const nowMs = Date.now()
    const elapsedMs = nowMs - eventLoopLagProbeLastTickMs
    eventLoopLagProbeLastTickMs = nowMs
    const lagMs = elapsedMs - EVENT_LOOP_LAG_SAMPLE_INTERVAL_MS

    if (lagMs >= EVENT_LOOP_LAG_WARN_MS) {
      eventLoopLagWarnCount += 1
      logDiag('event_loop_lag_warn', {
        lagMs,
        elapsedMs,
        warnCount: eventLoopLagWarnCount,
        queueLength: wsConnectQueue.length,
        wsConnections: wsMap.size,
        taskCount: tasks.value.length,
        visibilityState: document.visibilityState,
      })
    }
  }, EVENT_LOOP_LAG_SAMPLE_INTERVAL_MS)

  logDiag('event_loop_lag_probe_started', {
    sampleIntervalMs: EVENT_LOOP_LAG_SAMPLE_INTERVAL_MS,
    warnThresholdMs: EVENT_LOOP_LAG_WARN_MS,
  })
}

const stopEventLoopLagProbe = (): void => {
  if (eventLoopLagProbeTimer === null) return
  window.clearInterval(eventLoopLagProbeTimer)
  eventLoopLagProbeTimer = null
  logDiag('event_loop_lag_probe_stopped', {
    warnCount: eventLoopLagWarnCount,
  })
}

const handleWindowError = (event: ErrorEvent): void => {
  logDiagError('window_error', event.error ?? new Error(event.message), {
    message: event.message,
    filename: event.filename,
    lineno: event.lineno,
    colno: event.colno,
  })
}

const handleUnhandledRejection = (event: PromiseRejectionEvent): void => {
  const reason = event.reason as { message?: string; stack?: string; name?: string } | undefined
  logDiagError('window_unhandled_rejection', reason ?? event.reason, {
    reasonType: typeof event.reason,
  })
}

const handleVisibilityChange = (): void => {
  logPageEnvironment('page_visibility_changed')
  if (document.visibilityState === 'visible') {
    triggerResumeSync('visibility_change')
  }
}

const handleOnline = (): void => {
  logPageEnvironment('page_online_state_changed', {
    onlineEvent: 'online',
  })
  triggerResumeSync('online')
}

const handleOffline = (): void => {
  logPageEnvironment('page_online_state_changed', {
    onlineEvent: 'offline',
  })
}

const handleFocus = (): void => {
  logPageEnvironment('page_focus_changed', {
    focusEvent: 'focus',
  })
  triggerResumeSync('focus')
}

const handleBlur = (): void => {
  logPageEnvironment('page_focus_changed', {
    focusEvent: 'blur',
  })
}

const currentRole = ref(readUserRole())

const statusOrder = (status: string): number => {
  if (status === 'running') return 0
  if (status === 'awaiting_approval') return 1
  if (status === 'queued') return 2
  if (status === 'completed') return 3
  if (status === 'failed') return 4
  return 5
}

const approvingTasks = ref<Set<string>>(new Set())

const byDateAsc = (a: QuotationTaskItem, b: QuotationTaskItem) => {
  return new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
}

const byDateDesc = (a: QuotationTaskItem, b: QuotationTaskItem) => {
  return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
}

const queuedTasks = computed(() => {
  return tasks.value
    .filter((task) => task.status === 'queued')
    .sort(byDateAsc)
})

const runningTasks = computed(() => {
  return tasks.value
    .filter((task) => task.status === 'running' || task.status === 'awaiting_approval')
    .sort(byDateAsc)
})

const doneTasks = computed(() => {
  return tasks.value
    .filter((task) => ['completed', 'failed', 'cancelled'].includes(task.status))
    .sort(byDateDesc)
})

const syncWsConnections = (): void => {
  wsConnections.value = wsMap.size
}

const isCurrentEpoch = (epoch?: number): boolean => {
  if (epoch === undefined) return true
  return epoch === stateEpoch
}

const guardEpoch = (epoch: number | undefined, source: string, details?: Record<string, unknown>): boolean => {
  if (isCurrentEpoch(epoch)) return true
  logDiag('state_epoch_discard', {
    source,
    receivedEpoch: epoch,
    currentEpoch: stateEpoch,
    ...details,
  })
  return false
}

const sortTasksByStatus = (items: QuotationTaskItem[]): QuotationTaskItem[] => {
  return [...items].sort((a, b) => statusOrder(a.status) - statusOrder(b.status))
}



const applyTaskListSnapshot = (incomingTasks: QuotationTaskItem[], options?: { epoch?: number; source?: string }): boolean => {
  if (!guardEpoch(options?.epoch, options?.source ?? 'apply_task_list_snapshot')) {
    return false
  }

  tasks.value = sortTasksByStatus(incomingTasks)

  return true
}

const applyTaskPatchById = (
  taskId: string,
  buildNext: (current: QuotationTaskItem) => QuotationTaskItem,
  options?: { epoch?: number; source?: string }
): QuotationTaskItem | null => {
  if (!guardEpoch(options?.epoch, options?.source ?? 'apply_task_patch', { taskId })) {
    return null
  }

  const index = tasks.value.findIndex((task) => task.task_id === taskId)
  if (index < 0) return null

  const current = tasks.value[index]
  const next = buildNext(current)
  const nextTasks = [...tasks.value]
  nextTasks[index] = next
  tasks.value = sortTasksByStatus(nextTasks)

  return next
}

const applyTaskUpsertById = (
  incoming: QuotationTaskItem,
  options?: { epoch?: number; source?: string }
): QuotationTaskItem | null => {
  if (!guardEpoch(options?.epoch, options?.source ?? 'apply_task_upsert', {
    taskId: incoming.task_id,
  })) {
    return null
  }

  const index = tasks.value.findIndex((task) => task.task_id === incoming.task_id)
  const nextTasks = [...tasks.value]

  if (index < 0) {
    nextTasks.unshift(incoming)
  } else {
    nextTasks[index] = incoming
  }

  tasks.value = sortTasksByStatus(nextTasks)

  return incoming
}

const hasApprovalItems = (task: QuotationTaskItem): boolean => {
  const items = task.approval_data?.pdm_result?.items
  return Array.isArray(items) && items.length > 0
}

const removeQueuedTaskSocket = (taskId: string): void => {
  const queueIndex = wsConnectQueue.indexOf(taskId)
  if (queueIndex >= 0) {
    wsConnectQueue.splice(queueIndex, 1)
  }
  wsConnectQueuedTaskIds.delete(taskId)
}

const clearWsConnectScheduler = (): void => {
  if (wsConnectBatchTimer !== null) {
    window.clearTimeout(wsConnectBatchTimer)
    wsConnectBatchTimer = null
  }
  wsConnectQueue.forEach((taskId) => {
    wsTaskEpochMap.delete(taskId)
  })
  wsConnectQueue.length = 0
  wsConnectQueuedTaskIds.clear()
}

const closeSocket = (taskId: string, reason?: 'ws_cooldown' | 'task_polling_terminal' | 'task_inactive' | 'sync_mode_switch' | undefined): void => {
  removeQueuedTaskSocket(taskId)
  wsTaskEpochMap.delete(taskId)
  stopWsHeartbeat(taskId)
  if (reason) {
    wsExpectedCloseReasonsByTaskId.set(taskId, reason)
  }

  const socket = wsMap.get(taskId)
  if (!socket) return

  logDiag('ws_close_initiated', {
    taskId,
    reason: reason ?? 'unspecified',
    beforeConnections: wsMap.size,
  })

  socket.close()
  wsMap.delete(taskId)
  syncWsConnections()

  logDiag('ws_close_done', {
    taskId,
    afterConnections: wsMap.size,
  })
}

const isTaskCurrentlyActive = (taskId: string): boolean => {
  return tasks.value.some((task) => task.task_id === taskId && ACTIVE_STATUSES.includes(task.status))
}

const WS_HEARTBEAT_INTERVAL_MS = 30000

const startWsHeartbeat = (taskId: string): void => {
  stopWsHeartbeat(taskId)
  const socket = wsMap.get(taskId)
  if (!socket || socket.readyState !== WebSocket.OPEN) return
  const timer = window.setInterval(() => {
    const s = wsMap.get(taskId)
    if (!s || s.readyState !== WebSocket.OPEN) {
      stopWsHeartbeat(taskId)
      return
    }
    try {
      s.send(JSON.stringify({ type: 'ping' }))
    } catch {
      stopWsHeartbeat(taskId)
    }
  }, WS_HEARTBEAT_INTERVAL_MS)
  wsHeartbeatTimerByTaskId.set(taskId, timer)
  logDiag('ws_heartbeat_started', { taskId, intervalMs: WS_HEARTBEAT_INTERVAL_MS })
}

const stopWsHeartbeat = (taskId: string): void => {
  const timer = wsHeartbeatTimerByTaskId.get(taskId)
  if (timer !== undefined) {
    window.clearInterval(timer)
    wsHeartbeatTimerByTaskId.delete(taskId)
  }
}

const patchTaskFromEvent = (
  eventPayload: {
    task_id?: string
    status?: string
    progress?: number
    message?: string
    error?: string | null
  },
  epoch?: number
): void => {
  const taskId = String(eventPayload.task_id ?? '').trim()
  if (!taskId) return

  logDiag('ws_event_received', {
    taskId,
    status: eventPayload.status,
    progress: eventPayload.progress,
    epoch,
    beforeTaskCount: tasks.value.length,
  })

  const next = applyTaskPatchById(
    taskId,
    (current) => ({
      ...current,
      status:
        ACTIVE_STATUSES.includes(String(eventPayload.status ?? '')) ||
        TERMINAL_STATUSES.includes(String(eventPayload.status ?? ''))
          ? (eventPayload.status as QuotationTaskItem['status'])
          : current.status,
      progress: typeof eventPayload.progress === 'number' ? eventPayload.progress : current.progress,
      message: typeof eventPayload.message === 'string' ? eventPayload.message : current.message,
      error: typeof eventPayload.error === 'string' ? eventPayload.error : current.error,
    }),
    {
      epoch,
      source: 'patch_task_from_event',
    }
  )

  if (!next) {
    if (isCurrentEpoch(epoch)) {
      logDiag('ws_event_task_missing', {
        taskId,
        status: eventPayload.status,
        currentTaskCount: tasks.value.length,
      })
    }
    return
  }

  logDiag('ws_event_applied', {
    taskId,
    toStatus: next.status,
    afterTaskCount: tasks.value.length,
  })

  // WS 事件只携带 status/progress/message/error，不携带 result。
  // 当任务进入 awaiting_approval 但本地 result 还没有 PDM items 时（phase1→phase2 的时序漏洞），
  // 必须立即拉一次完整任务详情，否则 UI 会显示 "PDM 未返回任何数据"，直到用户手动刷新。
  requestApprovalDetailRefresh(next, 200, stateEpoch, 'patch_task_from_event_awaiting')

  if (TERMINAL_STATUSES.includes(next.status)) {
    approvalDetailRequestedTaskIds.delete(taskId)
    closeSocket(taskId)
  }
}

const connectTaskSocket = (taskId: string): void => {
  const epoch = wsTaskEpochMap.get(taskId)
  if (epoch === undefined || !guardEpoch(epoch, 'ws_connect_guard', { taskId })) {
    removeQueuedTaskSocket(taskId)
    return
  }

  if (isPageUnmounted || !isTaskCurrentlyActive(taskId) || wsMap.has(taskId)) {
    removeQueuedTaskSocket(taskId)
    return
  }

  const wsAttemptId = ++wsConnectAttemptSeq
  const attemptStartedAt = Date.now()
  let firstMessageObserved = false
  logDiagCritical('ws_connect_attempt', {
    wsAttemptId,
    taskId,
    epoch,
    queueLengthBeforeAttempt: wsConnectQueue.length,
    currentConnections: wsMap.size,
    consecutiveFailures: wsConsecutiveFailures,
    visibilityState: document.visibilityState,
    online: getNavigatorOnline(),
    userAgent: getUserAgent(),
  })

  try {
    let socketRef: WebSocket | null = null
    const socket = createTaskWebSocket(taskId, {
      onOpen: (openedSocket) => {
        // 注意：onOpen 只是 TCP+WS 握手完成，auth 尚未验证。
        // 不在此调用 recordWsHandshakeSuccess，否则会过早重置失败计数器，
        // 导致服务端随后 1008 拒绝时 cooldown 机制永远无法生效。
        logDiagCritical('ws_open', {
          wsAttemptId,
          taskId,
          epoch,
          elapsedMs: Date.now() - attemptStartedAt,
          queueLengthAfterOpen: wsConnectQueue.length,
          currentConnections: wsMap.size,
          ...describeWebSocket(openedSocket),
        })
      },
      onMessage: (payload, rawEvent) => {
        if (!socketRef || wsMap.get(taskId) !== socketRef) return
        if (!firstMessageObserved) {
          firstMessageObserved = true
          logDiagCritical('ws_first_message', {
            wsAttemptId,
            taskId,
            elapsedMs: Date.now() - attemptStartedAt,
            payloadType: typeof payload,
            dataLength:
              typeof rawEvent.data === 'string'
                ? rawEvent.data.length
                : rawEvent.data instanceof Blob
                  ? rawEvent.data.size
                  : null,
            ...describeWebSocket(socketRef),
          })
        }
        if (!payload || typeof payload !== 'object') return
        const eventPayload = payload as {
          type?: string
          task_id?: string
          event_type?: string
          status?: string
          progress?: number
          message?: string
          error?: string | null
        }
        if (eventPayload.type === 'task_event' && eventPayload.task_id) {
          patchTaskFromEvent(eventPayload, wsTaskEpochMap.get(taskId))
        } else if (eventPayload.type === 'connection_established') {
          // 收到 connection_established 说明 auth 已通过，此时才确认握手成功
          recordWsHandshakeSuccess(taskId)
          logDiagCritical('ws_auth_confirmed', {
            wsAttemptId,
            taskId,
            elapsedMs: Date.now() - attemptStartedAt,
          })
          // 启动应用层心跳（30s 一次），防止代理/负载均衡因空闲断开连接
          startWsHeartbeat(taskId)
        }
      },
      onError: (event, errorSocket) => {
        if (!socketRef || wsMap.get(taskId) !== socketRef) return
        logDiag('ws_on_error', {
          wsAttemptId,
          taskId,
          currentConnections: wsMap.size,
          elapsedMs: Date.now() - attemptStartedAt,
          eventType: event.type,
          hadFirstMessage: firstMessageObserved,
          ...describeWebSocket(errorSocket),
          visibilityState: document.visibilityState,
          online: getNavigatorOnline(),
        })
        recordWsFailure('ws_on_error', taskId, new Error('ws onerror callback invoked'))
        closeSocket(taskId)
      },
      onClose: (event, closedSocket) => {
        if (!socketRef || wsMap.get(taskId) !== socketRef) return
        const expectedReason = wsExpectedCloseReasonsByTaskId.get(taskId)
        wsExpectedCloseReasonsByTaskId.delete(taskId)
        wsMap.delete(taskId)
        wsTaskEpochMap.delete(taskId)
        syncWsConnections()

        // 服务端主动关闭（如 1008 鉴权拒绝）只触发 onClose 不触发 onError，
        // 必须在此记录失败，否则失败计数永远为 0，cooldown/polling 降级永远不会触发。
        const isExpectedClose = Boolean(expectedReason)
        const isAbnormalClose = event.code === 1006 || event.code === 1008 || event.code === 1011
          || (event.code >= 3000 && event.code <= 3999)
        if (!isExpectedClose && (isAbnormalClose || !event.wasClean)) {
          recordWsFailure('ws_on_close_abnormal', taskId, new Error(
            `ws abnormal close: code=${event.code} reason=${event.reason} wasClean=${event.wasClean}`
          ))
        }

        logDiagCritical('ws_on_close', {
          wsAttemptId,
          taskId,
          currentConnections: wsMap.size,
          elapsedMs: Date.now() - attemptStartedAt,
          closeCode: event.code,
          closeReason: event.reason,
          wasClean: event.wasClean,
          expectedCloseReason: expectedReason ?? null,
          hadFirstMessage: firstMessageObserved,
          ...describeWebSocket(closedSocket),
          visibilityState: document.visibilityState,
          online: getNavigatorOnline(),
        })
      },
    })

    socketRef = socket
    wsMap.set(taskId, socket)
    syncWsConnections()

    logDiag('ws_connect_ok', {
      wsAttemptId,
      taskId,
      epoch,
      currentConnections: wsMap.size,
      elapsedMs: Date.now() - attemptStartedAt,
      ...describeWebSocket(socket),
    })
  } catch (error) {
    recordWsFailure('ws_connect_failed', taskId, error)
    logDiagError('ws_connect_failed', error, {
      wsAttemptId,
      taskId,
      epoch,
      currentConnections: wsMap.size,
      elapsedMs: Date.now() - attemptStartedAt,
      visibilityState: document.visibilityState,
      online: getNavigatorOnline(),
      userAgent: getUserAgent(),
    })
    // Ignore websocket startup failures and keep polling fallback.
  }
}

const drainWsConnectQueue = (): void => {
  if (isPageUnmounted) {
    clearWsConnectScheduler()
    return
  }

  if (wsConnectQueue.length > 0) {
    logDiag('ws_queue_drain_start', {
      queueLength: wsConnectQueue.length,
      batchSize: WS_CONNECT_BATCH_SIZE,
      currentConnections: wsMap.size,
    })
  }

  let processed = 0
  while (processed < WS_CONNECT_BATCH_SIZE && wsConnectQueue.length > 0) {
    const taskId = wsConnectQueue.shift()
    if (!taskId) break

    wsConnectQueuedTaskIds.delete(taskId)

    if (!isTaskCurrentlyActive(taskId) || wsMap.has(taskId)) {
      processed += 1
      continue
    }

    connectTaskSocket(taskId)
    processed += 1
  }

  if (wsConnectQueue.length > 0) {
    recordWsQueuePressure('drain_ws_connect_queue_remains')
    wsConnectBatchTimer = window.setTimeout(() => {
      wsConnectBatchTimer = null
      drainWsConnectQueue()
    }, WS_CONNECT_BATCH_INTERVAL_MS)
  }
}

const scheduleWsConnectDrain = (): void => {
  if (wsConnectBatchTimer !== null || wsConnectQueue.length === 0 || isPageUnmounted) return

  wsConnectBatchTimer = window.setTimeout(() => {
    wsConnectBatchTimer = null
    drainWsConnectQueue()
  }, 0)
}

const enqueueTaskSocketConnect = (taskId: string, epoch: number): void => {
  wsTaskEpochMap.set(taskId, epoch)
  if (wsMap.has(taskId) || wsConnectQueuedTaskIds.has(taskId) || isPageUnmounted) return

  wsConnectQueue.push(taskId)
  wsConnectQueuedTaskIds.add(taskId)
  recordWsQueuePressure('enqueue_task_socket_connect')
  scheduleWsConnectDrain()
}

const refreshSingleTask = async (taskId: string, epoch = stateEpoch): Promise<void> => {
  if (refreshInFlightTaskIds.has(taskId)) {
    refreshQueuedTaskIds.add(taskId)
    logDiag('refresh_single_task_deduped', {
      taskId,
      epoch,
      inFlightSize: refreshInFlightTaskIds.size,
      queuedSize: refreshQueuedTaskIds.size,
    })
    return
  }

  refreshInFlightTaskIds.add(taskId)
  logDiag('refresh_single_task_start', {
    taskId,
    epoch,
    inFlightSize: refreshInFlightTaskIds.size,
  })
  try {
    const task = await getQuotationTask(taskId)
    const updated = applyTaskUpsertById(task, {
      epoch,
      source: 'refresh_single_task_success',
    })
    if (updated && ['completed', 'failed', 'cancelled'].includes(updated.status)) {
      closeSocket(taskId)
    }
    logDiag('refresh_single_task_success', {
      taskId,
      epoch,
      status: task.status,
      progress: task.progress,
    })
  } catch (error) {
    logDiagError('refresh_single_task_failed', error, { taskId, epoch })
    // Keep existing task data; detail refresh failures must not trigger a list refresh loop.
  } finally {
    refreshInFlightTaskIds.delete(taskId)
    if (refreshQueuedTaskIds.delete(taskId)) {
      scheduleRefreshSingleTask(taskId, 300, stateEpoch)
    }
    logDiag('refresh_single_task_end', {
      taskId,
      epoch,
      inFlightSize: refreshInFlightTaskIds.size,
      queuedSize: refreshQueuedTaskIds.size,
    })
  }
}

const scheduleRefreshSingleTask = (taskId: string, delay = 500, epoch = stateEpoch): void => {
  const existingTimer = refreshTimers.get(taskId)
  if (existingTimer !== undefined) {
    window.clearTimeout(existingTimer)
  }
  const timer = window.setTimeout(() => {
    refreshTimers.delete(taskId)
    void refreshSingleTask(taskId, epoch)
  }, delay)
  refreshTimers.set(taskId, timer)
}

/**
 * 统一的 awaiting_approval 详情补拉：当任务进入 awaiting_approval 状态但本地 result 缺 PDM
 * items 时调度一次详情拉取。返回 true 表示真正发起了调度，false 表示已被去重 / 不需要。
 *
 * 同时被 loadTasks (轮询/手动刷新) 和 patchTaskFromEvent (WS 事件) 复用，确保去重和触发
 * 规则单点定义，避免两路径策略漂移。
 */
const requestApprovalDetailRefresh = (
  task: QuotationTaskItem,
  delay: number,
  epoch: number,
  source: string,
): boolean => {
  if (task.status !== 'awaiting_approval') return false
  if (hasApprovalItems(task)) return false
  if (approvalDetailRequestedTaskIds.has(task.task_id)) return false
  approvalDetailRequestedTaskIds.add(task.task_id)
  logDiag('approval_detail_refresh_scheduled', {
    taskId: task.task_id,
    delay,
    epoch,
    source,
  })
  scheduleRefreshSingleTask(task.task_id, delay, epoch)
  return true
}

const ensureTaskSockets = (epoch = stateEpoch): void => {
  if (!ENABLE_TASK_WEBSOCKET) {
    logDiag('ws_disabled_cleanup_start', { existingConnections: wsMap.size })
    clearWsConnectScheduler()
    for (const [taskId] of wsMap) {
      closeSocket(taskId)
    }
    syncWsConnections()
    logDiag('ws_disabled_cleanup_done', { remainingConnections: wsMap.size })
    return
  }

  const nowMs = Date.now()
  const activeTaskIds = new Set(
    tasks.value
      .filter((task) => ACTIVE_STATUSES.includes(task.status))
      .map((task) => task.task_id)
  )

  logDiag('ws_ensure_start', {
    epoch,
    activeTasks: activeTaskIds.size,
    existingConnections: wsMap.size,
    queuedConnections: wsConnectQueue.length,
    maxQueueLengthSeen: wsMaxQueueLengthSeen,
    recentFailuresInWindow: wsFailureTimestamps.length,
    consecutiveFailures: wsConsecutiveFailures,
  })

  for (const existingTaskId of wsMap.keys()) {
    if (!activeTaskIds.has(existingTaskId)) {
      closeSocket(existingTaskId, 'task_inactive')
      continue
    }
    const syncMode = taskSyncModeByTaskId.get(existingTaskId)
    if (syncMode === 'list') {
      closeSocket(existingTaskId, 'sync_mode_switch')
      continue
    }
    wsTaskEpochMap.set(existingTaskId, epoch)
  }

  for (let i = wsConnectQueue.length - 1; i >= 0; i -= 1) {
    const queuedTaskId = wsConnectQueue[i]
    if (!activeTaskIds.has(queuedTaskId)) {
      wsConnectQueue.splice(i, 1)
      wsConnectQueuedTaskIds.delete(queuedTaskId)
      wsTaskEpochMap.delete(queuedTaskId)
      continue
    }
    if (taskSyncModeByTaskId.get(queuedTaskId) === 'list') {
      wsConnectQueue.splice(i, 1)
      wsConnectQueuedTaskIds.delete(queuedTaskId)
      wsTaskEpochMap.delete(queuedTaskId)
    }
  }

  activeTaskIds.forEach((taskId) => {
    if (wsMap.has(taskId)) return
    const cooldownUntil = wsDisabledUntilByTaskId.get(taskId)
    if (cooldownUntil !== undefined && nowMs < cooldownUntil) {
      logDiagCritical('ws_connect_suppressed_due_to_cooldown', {
        taskId,
        cooldownUntil,
        remainingMs: cooldownUntil - nowMs,
      })
      if (taskSyncModeByTaskId.get(taskId) !== 'list') {
        switchTaskSyncMode(taskId, 'list', 'cooldown_suppress')
      }
      return
    }
    // live WS 连接数上限：超出部分交给列表轮询覆盖（避免大量任务时 WS 扇出）。
    const prospectiveLive = wsMap.size + wsConnectQueuedTaskIds.size
    if (prospectiveLive >= WS_MAX_LIVE_CONNECTIONS) {
      if (taskSyncModeByTaskId.get(taskId) !== 'list') {
        switchTaskSyncMode(taskId, 'list', 'live_ws_cap')
      }
      logDiag('ws_connect_skipped_live_cap', {
        taskId,
        prospectiveLive,
        cap: WS_MAX_LIVE_CONNECTIONS,
      })
      return
    }
    const syncMode = taskSyncModeByTaskId.get(taskId)
    if (syncMode === 'list') {
      // cooldown 已过期或上限已释放，切换回 WS 模式
      switchTaskSyncMode(taskId, 'ws', 'cooldown_expired')
    }
    enqueueTaskSocketConnect(taskId, epoch)
  })

  recordWsQueuePressure('ensure_task_sockets')
  scheduleWsConnectDrain()

  logDiag('ws_ensure_done', {
    epoch,
    activeTasks: activeTaskIds.size,
    finalConnections: wsMap.size,
    queuedConnections: wsConnectQueue.length,
  })
}

const clearLoadTasksTimeout = (): void => {
  if (loadTasksTimeoutId !== null) {
    window.clearTimeout(loadTasksTimeoutId)
    loadTasksTimeoutId = null
  }
}

const loadTasks = async (options?: { force?: boolean; silent?: boolean }): Promise<void> => {
  const requestId = ++loadTasksRequestSeq
  const mySeq = ++loadTasksActiveSeq
  const startedAt = Date.now()

  if (loading.value) {
    if (!options?.force) {
      loadTasksSkipWhileLoadingCount += 1
      logDiag('load_tasks_skip_due_to_loading', {
        requestId,
        force: false,
        skipCount: loadTasksSkipWhileLoadingCount,
        inFlightRequestId: loadTasksInFlightRequestId,
      })
      return
    }
    loadTasksForceAbortCount += 1
    logDiag('load_tasks_abort_previous', {
      requestId,
      abortReason: 'force_refresh',
      inFlightRequestId: loadTasksInFlightRequestId,
      forceAbortCount: loadTasksForceAbortCount,
    })
    loadTasksAbortController?.abort()
  }

  const requestEpoch = ++stateEpoch

  logDiag('load_tasks_start', {
    requestId,
    requestEpoch,
    force: Boolean(options?.force),
    silent: Boolean(options?.silent),
    loadingBefore: loading.value,
    inFlightRequestIdBeforeStart: loadTasksInFlightRequestId,
    visibilityState: document.visibilityState,
    online: getNavigatorOnline(),
  })

  clearLoadTasksTimeout()
  const controller = new AbortController()
  loadTasksAbortController = controller
  loadTasksInFlightRequestId = requestId
  loadTasksTimeoutId = window.setTimeout(() => {
    logDiag('load_tasks_timeout_abort', {
      requestId,
      requestEpoch,
      abortReason: 'timeout',
      elapsedMs: Date.now() - startedAt,
      inFlightRequestId: loadTasksInFlightRequestId,
    })
    controller.abort()
  }, 15000)

  loading.value = true
  if (!options?.silent) {
    errorMessage.value = ''
  }
  currentRole.value = readUserRole()
  let loadSucceeded = false
  try {
    const fetchStartedAt = Date.now()
    logDiag('load_tasks_fetch_start', {
      requestId,
      requestEpoch,
      limit: 100,
    })
    const response = await listQuotationTasks({
      limit: 100,
      signal: controller.signal,
    })
    const responseReceivedAt = Date.now()
    const fetchMs = responseReceivedAt - fetchStartedAt
    const items = Array.isArray(response.items) ? response.items : []
    const awaitingApprovalCount = items.filter((task) => task.status === 'awaiting_approval').length
    const activeCount = items.filter((task) => ACTIVE_STATUSES.includes(task.status)).length
    const completedCount = items.filter((task) => task.status === 'completed').length
    const responseApproxJsonChars = computeApproxJsonSize(response)
    const responseItemsApproxJsonChars = computeApproxJsonSize(items)

    logDiag('load_tasks_response_received', {
      requestId,
      requestEpoch,
      items: items.length,
      fetchMs,
      elapsedMs: responseReceivedAt - startedAt,
      awaitingApprovalCount,
      activeCount,
      completedCount,
      responseApproxJsonChars,
      responseItemsApproxJsonChars,
    })

    const snapshotApplyStartedAt = Date.now()
    const snapshotApplied = applyTaskListSnapshot(items, {
      epoch: requestEpoch,
      source: 'load_tasks_snapshot_apply',
    })
    const snapshotApplyMs = Date.now() - snapshotApplyStartedAt
    logDiag('load_tasks_snapshot_apply_done', {
      requestId,
      requestEpoch,
      snapshotApplied,
      snapshotApplyMs,
      taskCountAfterApply: tasks.value.length,
    })
    if (!snapshotApplied) {
      return
    }

    const ensureSocketsStartedAt = Date.now()
    ensureTaskSockets(requestEpoch)
    const ensureSocketsMs = Date.now() - ensureSocketsStartedAt
    logDiag('load_tasks_socket_ensure_done', {
      requestId,
      requestEpoch,
      ensureSocketsMs,
      wsConnections: wsMap.size,
      wsQueueLength: wsConnectQueue.length,
    })

    const approvalRefreshScheduleStartedAt = Date.now()
    // 轮询/手动刷新路径：每次 list 拉取最多调度 2 个待补拉详情的任务，阶梯延迟避免并发突刺。
    // WS 事件路径走 requestApprovalDetailRefresh 共享去重集合；先到先得，本路径会自动跳过已被去重的。
    tasks.value
      .filter((task) => task.status === 'awaiting_approval' && !hasApprovalItems(task))
      .slice(0, 2)
      .forEach((task, index) => {
        requestApprovalDetailRefresh(
          task,
          800 + index * 500,
          requestEpoch,
          'load_tasks_awaiting',
        )
      })
    const approvalRefreshScheduleMs = Date.now() - approvalRefreshScheduleStartedAt

    logDiag('load_tasks_success', {
      requestId,
      requestEpoch,
      totalTasksAfterMerge: tasks.value.length,
      fetchMs,
      snapshotApplyMs,
      ensureSocketsMs,
      approvalRefreshScheduleMs,
      elapsedMs: Date.now() - startedAt,
    })
    loadSucceeded = true
    listRefreshSuccessiveFailures = 0
    listRefreshBackoffMultiplier = 1
  } catch (error) {
    // stale-epoch 的请求已被取代，不计入退避阶梯，直接丢弃。
    if (!isCurrentEpoch(requestEpoch)) {
      logDiag('load_tasks_error_ignored_stale_epoch', {
        requestId,
        requestEpoch,
        currentEpoch: stateEpoch,
      })
      return
    }

    const isAbortError = (error as { name?: string })?.name === 'AbortError'
    let abortReason: string | null = null
    if (isAbortError) {
      loadTasksAbortErrorCount += 1
      const isTimeout = controller.signal.aborted && loadTasksTimeoutId === null
      const isUnmount = isPageUnmounted
      abortReason = isUnmount ? 'unmount' : isTimeout ? 'timeout' : 'force_refresh'
      logDiag('load_tasks_abort_error_observed', {
        requestId,
        requestEpoch,
        abortErrorCount: loadTasksAbortErrorCount,
        abortReason,
        signalAborted: controller.signal.aborted,
        elapsedMs: Date.now() - startedAt,
      })
    }

    // 仅真实失败（网络错误 / 超时）计入退避阶梯；
    // unmount / force_refresh 的 abort 不属于真实失败，不应触发退避。
    const countedAsFailure = !isAbortError || abortReason === 'timeout'
    if (countedAsFailure) {
      listRefreshSuccessiveFailures += 1
      listRefreshBackoffMultiplier = Math.min(listRefreshBackoffMultiplier + 1, 3)
    }

    logDiagError('load_tasks_failed', error, {
      requestId,
      requestEpoch,
      silent: Boolean(options?.silent),
      elapsedMs: Date.now() - startedAt,
      visibilityState: document.visibilityState,
      online: getNavigatorOnline(),
      abortReason,
      countedAsFailure,
      successiveFailures: listRefreshSuccessiveFailures,
      backoffMultiplier: listRefreshBackoffMultiplier,
    })
    if (!options?.silent) {
      if (isAbortError) {
        errorMessage.value = '加载任务超时，请稍后刷新'
      } else {
        errorMessage.value = (error as { message?: string })?.message ?? '加载任务失败'
      }
    }
  } finally {
    const controllerMatched = loadTasksAbortController === controller
    if (controllerMatched) {
      clearLoadTasksTimeout()
      loadTasksAbortController = null
      loading.value = false
      loadTasksInFlightRequestId = null
    }
    logDiag('load_tasks_end', {
      requestId,
      requestEpoch,
      currentEpoch: stateEpoch,
      elapsedMs: Date.now() - startedAt,
      loadingAfter: loading.value,
      taskCount: tasks.value.length,
      inFlightRequestIdAfterEnd: loadTasksInFlightRequestId,
    })

    if (mySeq === loadTasksActiveSeq) {
      scheduleNextListRefresh(loadSucceeded)
    }
  }
}

const applyJitter = (base: number, ratio: number): number => {
  // ratio=0.2 → ±20% 抖动，分散多标签/多用户的刷新突发
  const factor = 1 + (Math.random() * 2 - 1) * ratio
  return Math.max(1000, Math.round(base * factor))
}

const computeListRefreshDelayMs = (
  successiveFailures: number,
  backoffMultiplier: number,
  activeTaskCount: number,
  wsDegraded: boolean,
): number => {
  // 列表加载失败时走退避阶梯，错误期不快轮询（避免放大请求）
  if (successiveFailures > 0) {
    if (backoffMultiplier <= 1) return LIST_REFRESH_BACKOFF_1_MS
    if (backoffMultiplier <= 2) return LIST_REFRESH_BACKOFF_2_MS
    return LIST_REFRESH_BACKOFF_MAX_MS
  }
  // 有活动任务且 WS 未全覆盖（失败/冷却/超 live 上限）→ 快速档，±20% 抖动
  if (activeTaskCount > 0 && wsDegraded) {
    return applyJitter(LIST_REFRESH_FAST_MS, 0.2)
  }
  // WS 健康或无活动任务 → 60s 安全网，±10% 抖动
  return applyJitter(LIST_REFRESH_NORMAL_MS, 0.1)
}

const scheduleNextListRefresh = (justSucceeded: boolean): void => {
  if (isPageUnmounted) return
  if (listRefreshTimerId !== null) {
    window.clearTimeout(listRefreshTimerId)
    listRefreshTimerId = null
  }
  const activeTaskCount = tasks.value.filter((t) => ACTIVE_STATUSES.includes(t.status)).length
  // WS 覆盖 = 已建连（wsMap）或正在建连队列中（wsConnectQueuedTaskIds）。
  // 仅当活动任务未被 WS 覆盖（失败冷却 / 超 live cap / 未建连完成）时才算 degraded，
  // 避免握手进行中误判为 degraded 而触发不必要的快速轮询。
  const wsCovered = wsMap.size + wsConnectQueuedTaskIds.size
  const wsDegraded = activeTaskCount > 0 && wsCovered < activeTaskCount
  const delayMs = computeListRefreshDelayMs(
    listRefreshSuccessiveFailures,
    listRefreshBackoffMultiplier,
    activeTaskCount,
    wsDegraded,
  )
  // 仅在真正退避（successiveFailures > 0）时按 critical 级别告警；
  // 常规排程（含正常 60s 安全网与 WS 降级快速档）走 info 级别，避免正常加载误报为关键错误。
  const isBackoff = listRefreshSuccessiveFailures > 0
  const refreshLogger = isBackoff ? logDiagCritical : logDiag
  refreshLogger(
    isBackoff ? 'list_refresh_backoff_scheduled' : 'list_refresh_next_scheduled',
    {
      delayMs,
      successiveFailures: listRefreshSuccessiveFailures,
      backoffMultiplier: listRefreshBackoffMultiplier,
      activeTaskCount,
      wsDegraded,
      wsConnections: wsMap.size,
      wsQueued: wsConnectQueuedTaskIds.size,
      wsCovered: wsMap.size + wsConnectQueuedTaskIds.size,
      justSucceeded,
    },
  )
  listRefreshTimerId = window.setTimeout(() => {
    listRefreshTimerId = null
    void loadTasks({ silent: true })
  }, delayMs)
}

let resumeSyncDebounceTimer: number | null = null

const triggerResumeSync = (trigger: string): void => {
  logDiagCritical('page_resume_sync_triggered', {
    trigger,
    taskCount: tasks.value.length,
    activeCount: tasks.value.filter((t) => ACTIVE_STATUSES.includes(t.status)).length,
    wsConnections: wsMap.size,
  })
  if (resumeSyncDebounceTimer !== null) {
    window.clearTimeout(resumeSyncDebounceTimer)
  }
  resumeSyncDebounceTimer = window.setTimeout(() => {
    resumeSyncDebounceTimer = null
    void loadTasks({ silent: true })
  }, 500)
}

const openFilePicker = (): void => {
  fileInputRef.value?.click()
}

const handleFileSelected = async (event: Event): Promise<void> => {
  const target = event.target as HTMLInputElement
  const selectedFile = target.files?.[0]
  target.value = ''
  const uploadId = ++uploadAttemptSeq
  logDiag('upload_selected', {
    uploadId,
    hasFile: Boolean(selectedFile),
    selectedCount: target.files?.length ?? 0,
    wsMapSizeBeforeUpload: wsMap.size,
    taskCountBeforeUpload: tasks.value.length,
    visibilityState: document.visibilityState,
    online: navigator.onLine,
    loadingAtSelect: loading.value,
    uploadingAtSelect: uploading.value,
  })

  if (!selectedFile) return

  if (selectedFile.type !== 'application/pdf' && !selectedFile.name.toLowerCase().endsWith('.pdf')) {
    logDiag('upload_rejected_invalid_file_type', {
      uploadId,
      fileName: selectedFile.name,
      fileType: selectedFile.type,
    })
    errorMessage.value = '仅支持上传 PDF 文件'
    return
  }

  const defaultTaskName = selectedFile.name.replace(/\.pdf$/i, '') || selectedFile.name
  pendingUploadFile.value = selectedFile
  pendingTaskName.value = defaultTaskName
  pendingUploadId.value = uploadId
  showTaskNameDialog.value = true
}

const cancelTaskNameDialog = (): void => {
  logDiag('upload_cancelled_by_dialog', {
    uploadId: pendingUploadId.value,
    defaultTaskName: pendingTaskName.value,
  })
  pendingUploadFile.value = null
  pendingTaskName.value = ''
}

const confirmUploadWithTaskName = async (): Promise<void> => {
  const selectedFile = pendingUploadFile.value
  if (!selectedFile) return

  const defaultTaskName = selectedFile.name.replace(/\.pdf$/i, '') || selectedFile.name
  const customTaskName = pendingTaskName.value.trim() || defaultTaskName
  const uploadId = pendingUploadId.value || ++uploadAttemptSeq

  pendingUploadFile.value = null
  pendingTaskName.value = ''
  showTaskNameDialog.value = false

  await performUpload(selectedFile, customTaskName, uploadId)
}

const performUpload = async (selectedFile: File, customTaskName: string, uploadId: number): Promise<void> => {
  const uploadStartedAt = Date.now()
  let createTaskStartedAt = 0
  let createTaskPendingWarnTimer: number | null = null

  uploading.value = true
  errorMessage.value = ''
  logDiag('upload_begin', {
    uploadId,
    fileName: selectedFile.name,
    fileType: selectedFile.type,
    fileSize: selectedFile.size,
    taskNameLength: customTaskName.length,
    wsMapSizeAtBegin: wsMap.size,
    taskCountAtBegin: tasks.value.length,
    visibilityState: document.visibilityState,
    online: navigator.onLine,
    loadingAtBegin: loading.value,
  })
  try {
    createTaskStartedAt = Date.now()
    logDiag('upload_before_create_task', {
      uploadId,
      endpoint: '/quotation/tasks',
      visibilityState: document.visibilityState,
      online: navigator.onLine,
      loadingBeforeCreateTask: loading.value,
      wsMapSizeBeforeCreateTask: wsMap.size,
      taskCountBeforeCreateTask: tasks.value.length,
      elapsedFromUploadBeginMs: createTaskStartedAt - uploadStartedAt,
    })

    createTaskPendingWarnTimer = window.setTimeout(() => {
      logDiag('upload_create_task_pending_too_long', {
        uploadId,
        endpoint: '/quotation/tasks',
        elapsedMs: Date.now() - createTaskStartedAt,
        visibilityState: document.visibilityState,
        online: navigator.onLine,
        loadingNow: loading.value,
        wsMapSizeNow: wsMap.size,
        taskCountNow: tasks.value.length,
      })
    }, UPLOAD_CREATE_TASK_PENDING_WARN_MS)

    await createQuotationTask(selectedFile, customTaskName)
    logDiag('upload_after_create_task', {
      uploadId,
      createTaskElapsedMs: Date.now() - createTaskStartedAt,
      totalUploadElapsedMs: Date.now() - uploadStartedAt,
    })
    await loadTasks()
    logDiag('upload_after_load_tasks', {
      uploadId,
      taskCountAfterLoad: tasks.value.length,
      totalUploadElapsedMs: Date.now() - uploadStartedAt,
    })
  } catch (error) {
    logDiagError('upload_failed', error, {
      uploadId,
      fileName: selectedFile.name,
      wsMapSizeOnError: wsMap.size,
      loadingOnError: loading.value,
      onlineOnError: navigator.onLine,
      visibilityStateOnError: document.visibilityState,
      totalUploadElapsedMs: Date.now() - uploadStartedAt,
      createTaskElapsedMs: createTaskStartedAt > 0 ? Date.now() - createTaskStartedAt : null,
    })
    errorMessage.value = (error as { message?: string })?.message ?? '创建任务失败'
  } finally {
    if (createTaskPendingWarnTimer !== null) {
      window.clearTimeout(createTaskPendingWarnTimer)
    }
    uploading.value = false
    logDiag('upload_end', {
      uploadId,
      uploadingAfter: uploading.value,
      errorMessage: errorMessage.value,
      totalUploadElapsedMs: Date.now() - uploadStartedAt,
    })
  }
}

const canCancel = (task: QuotationTaskItem): boolean => {
  return ACTIVE_STATUSES.includes(task.status)
}

const canDelete = (task: QuotationTaskItem): boolean => {
  return TERMINAL_STATUSES.includes(task.status)
}

const handleApprove = async (taskId: string, approvedPartids: string[], extraPartids: string[] = [], extraPartidEntries: Array<{ partid: string; type: string }> = []): Promise<void> => {
  if (approvingTasks.value.has(taskId)) return
  if (!approvedPartids.length && !extraPartidEntries.length && !extraPartids.length) {
    errorMessage.value = '请至少保留一个已批准的 PARTID'
    return
  }
  approvingTasks.value = new Set([...approvingTasks.value, taskId])
  try {
    await approveQuotationTask(taskId, approvedPartids, extraPartids, extraPartidEntries)
    await refreshSingleTask(taskId)
  } catch (error) {
    errorMessage.value = (error as { message?: string })?.message ?? '提交审核同意失败'
  } finally {
    const next = new Set(approvingTasks.value)
    next.delete(taskId)
    approvingTasks.value = next
  }
}

const handleCancel = async (taskId: string): Promise<void> => {
  try {
    await cancelQuotationTask(taskId)
    await refreshSingleTask(taskId)
  } catch (error) {
    errorMessage.value = (error as { message?: string })?.message ?? '取消任务失败'
  }
}

const handleDelete = (taskId: string): void => {
  const task = tasks.value.find((item) => item.task_id === taskId)
  const taskName = task?.display_name || task?.uploaded_file_name || taskId
  pendingDeleteTaskId.value = taskId
  pendingDeleteTaskName.value = taskName
  showDeleteDialog.value = true
}

const confirmDeleteTask = async (): Promise<void> => {
  const taskId = pendingDeleteTaskId.value
  if (!taskId) return

  try {
    await deleteQuotationTask(taskId)
    closeSocket(taskId)
    approvalDetailRequestedTaskIds.delete(taskId)
    refreshInFlightTaskIds.delete(taskId)
    refreshQueuedTaskIds.delete(taskId)

    const timer = refreshTimers.get(taskId)
    if (timer !== undefined) {
      window.clearTimeout(timer)
      refreshTimers.delete(taskId)
    }

    tasks.value = tasks.value.filter((item) => item.task_id !== taskId)
  } catch (error) {
    errorMessage.value = (error as { message?: string })?.message ?? '删除任务失败'
  }
}

const handleViewFile = async (taskId: string): Promise<void> => {
  try {
    const { blob, filename } = await downloadQuotationTaskFile(taskId)
    const url = URL.createObjectURL(blob)
    const openWindow = window.open(url, '_blank')
    if (!openWindow) {
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
    }
    window.setTimeout(() => URL.revokeObjectURL(url), 2000)
  } catch (error) {
    errorMessage.value = (error as { message?: string })?.message ?? '查看文件失败'
  }
}

const handleDownloadU8Xlsx = async (taskId: string): Promise<void> => {
  try {
    const { blob, filename } = await downloadQuotationU8ByTypeWorkbook(taskId)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.setTimeout(() => URL.revokeObjectURL(url), 2000)
  } catch (error) {
    errorMessage.value = (error as { message?: string })?.message ?? '下载 U8 分组 Excel 失败'
  }
}

const openDirectU8Dialog = (): void => {
  directU8PartidsText.value = ''
  directU8QuantitiesText.value = ''
  directU8TaskName.value = ''
  directU8Submitting.value = false
  showDirectU8Dialog.value = true
}

const cancelDirectU8Dialog = (): void => {
  showDirectU8Dialog.value = false
  directU8PartidsText.value = ''
  directU8QuantitiesText.value = ''
  directU8TaskName.value = ''
}

const parseDirectU8Partids = (
  partidsText: string,
  quantitiesText: string
): { partids: string[]; quantities: number[]; errors: string[] } => {
  const seen = new Set<string>()
  const partids: string[] = []
  const quantities: number[] = []
  const errors: string[] = []
  const partidLines = partidsText.split(/\r?\n/)
  const quantityLines = quantitiesText.split(/\r?\n/)
  const lineCount = Math.max(partidLines.length, quantityLines.length)

  for (let i = 0; i < lineCount; i++) {
    const partid = (partidLines[i] ?? '').trim()
    const qtyRaw = (quantityLines[i] ?? '').trim()
    if (!partid) {
      if (qtyRaw) {
        errors.push(`第 ${i + 1} 行数量已填写但缺少编码`)
      }
      continue
    }
    if (/[,;\t]/.test(partid)) {
      errors.push(`第 ${i + 1} 行编码包含分隔符，请将数量填写到右侧数量框`)
      continue
    }
    if (seen.has(partid)) continue
    let qty = 1
    if (qtyRaw) {
      if (!/^\d+(?:\.\d{1,4})?$/.test(qtyRaw)) {
        errors.push(`第 ${i + 1} 行数量 "${qtyRaw}" 格式错误（必须为正数，最多四位小数）`)
        continue
      }
      const parsed = parseFloat(qtyRaw)
      if (parsed <= 0) {
        errors.push(`第 ${i + 1} 行数量 ${parsed} 必须为正数`)
        continue
      }
      qty = parsed
    }
    seen.add(partid)
    partids.push(partid)
    quantities.push(qty)
  }
  return { partids, quantities, errors }
}

const confirmDirectU8Submit = async (): Promise<void> => {
  const { partids, quantities, errors } = parseDirectU8Partids(directU8PartidsText.value, directU8QuantitiesText.value)
  if (errors.length > 0) {
    errorMessage.value = errors.join('；')
    return
  }
  if (partids.length === 0) {
    errorMessage.value = '请至少输入一个 PARTID'
    return
  }
  if (partids.length > 1500) {
    errorMessage.value = `最多支持 1500 个 PARTID，当前输入了 ${partids.length} 个`
    return
  }

  directU8Submitting.value = true
  errorMessage.value = ''
  showDirectU8Dialog.value = false

  try {
    await createDirectU8Task(partids, quantities, directU8TaskName.value.trim() || undefined)
    directU8PartidsText.value = ''
    directU8QuantitiesText.value = ''
    directU8TaskName.value = ''
    await loadTasks()
  } catch (error) {
    errorMessage.value = (error as { message?: string })?.message ?? '创建直接 U8 查询任务失败'
  } finally {
    directU8Submitting.value = false
  }
}

const openDirectProjectDialog = (): void => {
  directProjectCodesText.value = ''
  directProjectTaskName.value = ''
  directProjectSubmitting.value = false
  showDirectProjectDialog.value = true
}

const cancelDirectProjectDialog = (): void => {
  showDirectProjectDialog.value = false
  directProjectCodesText.value = ''
  directProjectTaskName.value = ''
}

const confirmDirectProjectSubmit = async (): Promise<void> => {
  const seen = new Set<string>()
  const partids: string[] = []
  const lines = directProjectCodesText.value.split(/\r?\n/)
  for (const line of lines) {
    const code = line.trim()
    if (!code || seen.has(code)) continue
    seen.add(code)
    partids.push(code)
  }

  if (partids.length === 0) {
    errorMessage.value = '请至少输入一个项目编码'
    return
  }
  if (partids.length > 1500) {
    errorMessage.value = `最多支持 1500 个编码，当前输入了 ${partids.length} 个`
    return
  }

  const quantities = partids.map(() => 1)

  directProjectSubmitting.value = true
  errorMessage.value = ''
  showDirectProjectDialog.value = false

  try {
    await createDirectU8Task(partids, quantities, directProjectTaskName.value.trim() || undefined, 'project')
    directProjectCodesText.value = ''
    directProjectTaskName.value = ''
    await loadTasks()
  } catch (error) {
    errorMessage.value = (error as { message?: string })?.message ?? '创建项目编码查询任务失败'
  } finally {
    directProjectSubmitting.value = false
  }
}

onMounted(() => {
  isPageUnmounted = false
  window.addEventListener('error', handleWindowError)
  window.addEventListener('unhandledrejection', handleUnhandledRejection)
  document.addEventListener('visibilitychange', handleVisibilityChange)
  window.addEventListener('online', handleOnline)
  window.addEventListener('offline', handleOffline)
  window.addEventListener('focus', handleFocus)
  window.addEventListener('blur', handleBlur)
  startEventLoopLagProbe()
  logPageEnvironment('page_mounted', {
    userRole: currentRole.value,
  })

  void loadTasks()
})

onUnmounted(() => {
  isPageUnmounted = true
  window.removeEventListener('error', handleWindowError)
  window.removeEventListener('unhandledrejection', handleUnhandledRejection)
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  window.removeEventListener('online', handleOnline)
  window.removeEventListener('offline', handleOffline)
  window.removeEventListener('focus', handleFocus)
  window.removeEventListener('blur', handleBlur)
  stopEventLoopLagProbe()
  logPageEnvironment('page_unmounted_start', {
    connectionsBeforeCleanup: wsMap.size,
  })

  if (listRefreshTimerId !== null) {
    window.clearTimeout(listRefreshTimerId)
    listRefreshTimerId = null
  }
  loadTasksAbortController?.abort()
  clearLoadTasksTimeout()
  clearWsConnectScheduler()
  if (resumeSyncDebounceTimer !== null) {
    window.clearTimeout(resumeSyncDebounceTimer)
    resumeSyncDebounceTimer = null
  }
  for (const timer of refreshTimers.values()) {
    window.clearTimeout(timer)
  }
  refreshTimers.clear()
  for (const timer of wsHeartbeatTimerByTaskId.values()) {
    window.clearInterval(timer)
  }
  wsHeartbeatTimerByTaskId.clear()
  for (const [taskId] of wsMap) {
    closeSocket(taskId)
  }

  logPageEnvironment('page_unmounted_done', {
    connectionsAfterCleanup: wsMap.size,
  })
})

const TaskItemCard = defineComponent({
  name: 'TaskItemCard',
  props: {
    task: {
      type: Object as () => QuotationTaskItem,
      required: true,
    },
    showOwner: {
      type: Boolean,
      required: true,
    },
    canCancel: {
      type: Boolean,
      required: true,
    },
    canDelete: {
      type: Boolean,
      required: true,
    },
    isApproving: {
      type: Boolean,
      default: false,
    },
  },
  emits: ['cancel', 'delete', 'approve', 'view-file', 'download-u8-xlsx'],
  setup(props, { emit }) {
    const RING_RADIUS = 20
    const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS
    const progress = computed(() => Math.max(0, Math.min(100, props.task.progress ?? 0)))
    const ringDashOffset = computed(() => RING_CIRCUMFERENCE * (1 - progress.value / 100))
    const statusText = computed(() => {
      if (props.task.status === 'queued') return '排队中'
      if (props.task.status === 'running') return '处理中'
      if (props.task.status === 'awaiting_approval') return '等待审核'
      if (props.task.status === 'completed') return '已完成'
      if (props.task.status === 'failed') return '失败'
      return '已取消'
    })
    const progressStrokeClass = computed(() => {
      if (props.task.status === 'queued') return 'task-progress-ring__value--queued'
      if (props.task.status === 'running') return 'task-progress-ring__value--running'
      if (props.task.status === 'awaiting_approval') return 'task-progress-ring__value--awaiting'
      if (props.task.status === 'completed') return 'task-progress-ring__value--completed'
      if (props.task.status === 'failed') return 'task-progress-ring__value--failed'
      return 'task-progress-ring__value--cancelled'
    })

    const pdmItems = computed<QuotationPdmItem[]>(() => {
      const approvalData = props.task.approval_data
      const items = approvalData?.pdm_result?.items
      return Array.isArray(items) ? items : []
    })

    let lastPhase2UiSnapshotAt = 0
    let lastApprovalGroupComputeLogAt = 0
    let lastPdmRenderCostLogAt = 0

    interface PdmApprovalRow {
      key: string
      partid: string
      chinaname: string
      typeName: string
      raw: QuotationPdmItem
    }

    interface PdmApprovalGroup {
      key: string
      typeName: string
      rows: PdmApprovalRow[]
    }

    const parseQueryIndex = (value: unknown): number | null => {
      if (typeof value === 'number' && Number.isFinite(value)) return Math.trunc(value)
      if (typeof value === 'string') {
        const parsed = Number.parseInt(value.trim(), 10)
        return Number.isFinite(parsed) ? parsed : null
      }
      return null
    }

    const makeRowKey = (groupKey: string, chinaname: string, partid: string): string =>
      `${groupKey}\u0000${chinaname}\u0000${partid}`

    const keywordTypeByIndex = computed<Map<number, string>>(() => {
      const map = new Map<number, string>()
      const approvalData = props.task.approval_data
      const keywords = approvalData?.keywords_payload?.keywords
      if (!Array.isArray(keywords)) return map

      keywords.forEach((entry, idx) => {
        if (!entry || typeof entry !== 'object') return
        const typeName = String((entry as Record<string, unknown>).type ?? '').trim()
        if (!typeName) return
        map.set(idx, typeName)
      })

      return map
    })

    const resolveTypeName = (item: QuotationPdmItem): string => {
      const queryIndex = parseQueryIndex(item?.QUERY_INDEX)
      // PDM API sets QUERY_INDEX with enumerate(keyword_groups, start=1) — map keywords_payload by (index - 1).
      if (queryIndex !== null) {
        const typeFromOneBasedPdm = keywordTypeByIndex.value.get(queryIndex - 1)
        if (typeFromOneBasedPdm) return typeFromOneBasedPdm
        const typeFromZeroBasedIndex = keywordTypeByIndex.value.get(queryIndex)
        if (typeFromZeroBasedIndex) return typeFromZeroBasedIndex
      }

      const fallbackKeywords = item?.QUERY_KEYWORDS
      if (Array.isArray(fallbackKeywords)) {
        const firstKeyword = fallbackKeywords.find(
          (keyword) => String(keyword ?? '').trim().length > 0
        )
        if (firstKeyword !== undefined && firstKeyword !== null) {
          const text = String(firstKeyword).trim()
          if (text) return text
        }
      }

      return '未分组'
    }

    const approvalGroups = computed<PdmApprovalGroup[]>(() => {
      const startedAt = performance.now()
      const orderedGroups: PdmApprovalGroup[] = []
      const groupMap = new Map<string, { group: PdmApprovalGroup; seenRowKeys: Set<string> }>()

      for (const item of pdmItems.value) {
        const rawPartid = item?.PARTID
        if (rawPartid === undefined || rawPartid === null) continue
        const partid = String(rawPartid).trim()
        if (!partid) continue

        const chinaname =
          item?.CHINANAME === undefined || item?.CHINANAME === null
            ? ''
            : String(item.CHINANAME).trim()

        const typeName = resolveTypeName(item)
        const groupKey = typeName

        if (!groupMap.has(groupKey)) {
          const group: PdmApprovalGroup = {
            key: groupKey,
            typeName,
            rows: [],
          }
          groupMap.set(groupKey, { group, seenRowKeys: new Set<string>() })
          orderedGroups.push(group)
        }

        const hit = groupMap.get(groupKey)
        if (!hit) continue
        const innerDedupKey = `${chinaname}\u0000${partid}`
        if (hit.seenRowKeys.has(innerDedupKey)) continue
        hit.seenRowKeys.add(innerDedupKey)

        hit.group.rows.push({
          key: makeRowKey(groupKey, chinaname, partid),
          partid,
          chinaname,
          typeName,
          raw: item,
        })
      }

      const elapsedMs = performance.now() - startedAt
      const nowMs = Date.now()
      if (elapsedMs >= 30 || nowMs - lastApprovalGroupComputeLogAt >= 5000) {
        lastApprovalGroupComputeLogAt = nowMs
        logDiag('phase2_approval_groups_compute_cost', {
          taskId: props.task.task_id,
          taskStatus: props.task.status,
          pdmItemCount: pdmItems.value.length,
          groupCount: orderedGroups.length,
          elapsedMs,
        })
      }

      return orderedGroups
    })

    const allApprovalRows = computed<PdmApprovalRow[]>(() =>
      approvalGroups.value.flatMap((group) => group.rows)
    )

    watch(
      [approvalGroups, allApprovalRows, keywordTypeByIndex],
      ([groups, rows, keywordTypeMap]) => {
        const nowMs = Date.now()
        if (nowMs - lastPhase2UiSnapshotAt < 1500) return
        lastPhase2UiSnapshotAt = nowMs

        logDiag('phase2_ui_snapshot', {
          taskId: props.task.task_id,
          taskStatus: props.task.status,
          pdmItemCount: pdmItems.value.length,
          groupCount: groups.length,
          rowCount: rows.length,
          keywordTypeCount: keywordTypeMap.size,
          resultApproxJsonChars: computeApproxJsonSize(props.task.approval_data),
        })
      },
      { immediate: true }
    )

    const approvedRowKeys = ref<Set<string>>(new Set())
    const expandedGroupKeys = ref<Set<string>>(new Set())
    const manualPartidRows = ref<{ value: string; type: string }[]>([{ value: '', type: '' }])

    const extraPartidsFromManual = computed<string[]>(() => {
      const seen = new Set<string>()
      const result: string[] = []
      for (const row of manualPartidRows.value) {
        const v = String(row.value ?? '').trim()
        if (v && !seen.has(v)) {
          seen.add(v)
          result.push(v)
        }
      }
      return result
    })

    const extraPartidEntriesFromManual = computed<Array<{ partid: string; type: string }>>(() => {
      const seen = new Set<string>()
      const result: Array<{ partid: string; type: string }> = []
      for (const row of manualPartidRows.value) {
        const v = String(row.value ?? '').trim()
        const t = String(row.type ?? '').trim()
        if (v && !seen.has(v)) {
          seen.add(v)
          result.push({ partid: v, type: t })
        }
      }
      return result
    })

    const updateManualPartidRow = (index: number, field: 'value' | 'type', text: string): void => {
      const next = manualPartidRows.value.map((row, i) =>
        i === index ? { ...row, [field]: text } : row
      )
      manualPartidRows.value = next
    }

    const addManualPartidRow = (): void => {
      manualPartidRows.value = [...manualPartidRows.value, { value: '', type: '' }]
    }

    const removeManualPartidRow = (index: number): void => {
      if (manualPartidRows.value.length <= 1) {
        manualPartidRows.value = [{ value: '', type: '' }]
        return
      }
      manualPartidRows.value = manualPartidRows.value.filter((_, i) => i !== index)
    }

    watch(
      allApprovalRows,
      (next, prev) => {
        const prevKeys = new Set((prev ?? []).map((row) => row.key))
        const nextKeys = new Set(next.map((row) => row.key))
        const updated = new Set<string>()
        for (const key of approvedRowKeys.value) {
          if (nextKeys.has(key)) updated.add(key)
        }
        for (const row of next) {
          if (!prevKeys.has(row.key)) updated.add(row.key)
        }
        approvedRowKeys.value = updated
      },
      { immediate: true }
    )

    watch(
      approvalGroups,
      (next, prev) => {
        const nextKeys = new Set(next.map((group) => group.key))
        const updated = new Set<string>()
        for (const key of expandedGroupKeys.value) {
          if (nextKeys.has(key)) updated.add(key)
        }

        const hadPrev = Array.isArray(prev) && prev.length > 0
        if (!hadPrev && next.length > 0 && updated.size === 0) {
          updated.add(next[0].key)
        }

        expandedGroupKeys.value = updated
      },
      { immediate: true }
    )

    const approvedCount = computed(() => approvedRowKeys.value.size)
    const allSelected = computed(
      () => allApprovalRows.value.length > 0 && approvedCount.value === allApprovalRows.value.length
    )
    const noneSelected = computed(() => approvedCount.value === 0 && extraPartidEntriesFromManual.value.length === 0)

    const approvedPartidsPreview = computed<string[]>(() => {
      const seen = new Set<string>()
      const ordered: string[] = []
      for (const row of allApprovalRows.value) {
        if (!approvedRowKeys.value.has(row.key)) continue
        if (seen.has(row.partid)) continue
        seen.add(row.partid)
        ordered.push(row.partid)
      }
      return ordered
    })

    const isGroupExpanded = (groupKey: string): boolean => expandedGroupKeys.value.has(groupKey)

    const toggleGroupExpanded = (groupKey: string): void => {
      const next = new Set(expandedGroupKeys.value)
      if (next.has(groupKey)) next.delete(groupKey)
      else next.add(groupKey)
      expandedGroupKeys.value = next
    }

    const allExpanded = computed(
      () =>
        approvalGroups.value.length > 0 &&
        approvalGroups.value.every((group) => expandedGroupKeys.value.has(group.key))
    )

    const toggleExpandAll = (): void => {
      if (allExpanded.value) {
        expandedGroupKeys.value = new Set()
      } else {
        expandedGroupKeys.value = new Set(approvalGroups.value.map((group) => group.key))
      }
    }

    const toggleRow = (rowKey: string): void => {
      const next = new Set(approvedRowKeys.value)
      if (next.has(rowKey)) next.delete(rowKey)
      else next.add(rowKey)
      approvedRowKeys.value = next
    }

    const toggleAll = (): void => {
      if (allSelected.value) {
        approvedRowKeys.value = new Set()
      } else {
        approvedRowKeys.value = new Set(allApprovalRows.value.map((row) => row.key))
      }
    }

    const getGroupSelectedCount = (group: PdmApprovalGroup): number => {
      let count = 0
      for (const row of group.rows) {
        if (approvedRowKeys.value.has(row.key)) count += 1
      }
      return count
    }

    const getGroupApprovedPartidCount = (group: PdmApprovalGroup): number => {
      const seen = new Set<string>()
      for (const row of group.rows) {
        if (!approvedRowKeys.value.has(row.key)) continue
        seen.add(row.partid)
      }
      return seen.size
    }

    const isGroupAllSelected = (group: PdmApprovalGroup): boolean =>
      group.rows.length > 0 && getGroupSelectedCount(group) === group.rows.length

    const isGroupNoneSelected = (group: PdmApprovalGroup): boolean => getGroupSelectedCount(group) === 0

    const toggleGroupSelection = (group: PdmApprovalGroup): void => {
      const next = new Set(approvedRowKeys.value)
      if (isGroupAllSelected(group)) {
        for (const row of group.rows) {
          next.delete(row.key)
        }
      } else {
        for (const row of group.rows) {
          next.add(row.key)
        }
      }
      approvedRowKeys.value = next
    }

    const submitApproval = (): void => {
      emit('approve', [...approvedPartidsPreview.value], [...extraPartidsFromManual.value], [...extraPartidEntriesFromManual.value])
    }

    const formatList = (value: unknown): string => {
      if (Array.isArray(value)) return value.join(' / ')
      if (value === undefined || value === null) return ''
      return String(value)
    }

    const renderPdmGroupTable = (group: PdmApprovalGroup) => {
      if (!isGroupExpanded(group.key)) return null

      return h('div', { class: 'pdm-approval-group__table-wrapper' }, [
        h('table', { class: 'pdm-approval__table' }, [
          h('thead', null, [
            h('tr', null, [
              h('th', { key: '__checkbox', class: 'pdm-approval__checkbox-col' }),
              h('th', { key: 'CHINANAME' }, '名称'),
              h('th', { key: 'PARTID' }, 'PARTID'),
              h('th', { key: 'QUERY_KEYWORDS' }, '关键词'),
              h('th', { key: 'QUERY_EXPANDED_KEYWORDS' }, '扩展关键词'),
            ]),
          ]),
          h(
            'tbody',
            null,
            group.rows.map((row) => {
              const isChecked = approvedRowKeys.value.has(row.key)
              const queryKeywordsText = formatList(row.raw?.QUERY_KEYWORDS)
              const queryExpandedKeywordsText = formatList(row.raw?.QUERY_EXPANDED_KEYWORDS)

              return h(
                'tr',
                {
                  key: row.key,
                  class: !isChecked ? 'pdm-approval__row--skipped' : undefined,
                },
                [
                  h('td', { key: '__checkbox', class: 'pdm-approval__checkbox-col' }, [
                    h('input', {
                      type: 'checkbox',
                      class: 'pdm-approval__checkbox',
                      checked: isChecked,
                      disabled: props.isApproving,
                      'aria-label': `批准 ${row.chinaname || '(无名称)'} / ${row.partid}`,
                      onChange: () => toggleRow(row.key),
                    }),
                  ]),
                  h('td', { key: 'CHINANAME', title: row.chinaname }, row.chinaname),
                  h('td', { key: 'PARTID', title: row.partid }, row.partid),
                  h('td', { key: 'QUERY_KEYWORDS', title: queryKeywordsText }, queryKeywordsText),
                  h(
                    'td',
                    { key: 'QUERY_EXPANDED_KEYWORDS', title: queryExpandedKeywordsText },
                    queryExpandedKeywordsText
                  ),
                ]
              )
            })
          ),
        ]),
      ])
    }

    const renderPdmTable = () => {
      if (props.task.status !== 'awaiting_approval') return null
      const renderStartedAt = performance.now()
      const items = pdmItems.value
      if (items.length === 0) {
        const emptyNode = h('div', { class: 'pdm-approval' }, [h('p', { class: 'pdm-approval__empty' }, 'PDM 未返回任何数据')])
        const elapsedMs = performance.now() - renderStartedAt
        if (elapsedMs >= 20) {
          logDiag('phase2_render_pdm_table_cost', {
            taskId: props.task.task_id,
            taskStatus: props.task.status,
            pdmItemCount: items.length,
            groupCount: 0,
            rowCount: 0,
            elapsedMs,
            isEmpty: true,
          })
        }
        return emptyNode
      }

      const groups = approvalGroups.value
      const rowCount = allApprovalRows.value.length
      const partidCount = approvedPartidsPreview.value.length

      const node = h('div', { class: 'pdm-approval' }, [
        h(
          'p',
          { class: 'pdm-approval__title' },
          `PDM 候选审核（按 type 折叠，${groups.length} 组） · 共 ${items.length} 条，已批准 ${approvedCount.value}/${rowCount} 行，将提交 ${partidCount} 个 PARTID`
        ),
        h('div', { class: 'pdm-approval__toolbar' }, [
          h('label', { class: 'pdm-approval__toolbar-main' }, [
            h('input', {
              type: 'checkbox',
              class: 'pdm-approval__checkbox',
              checked: allSelected.value,
              indeterminate: !allSelected.value && !noneSelected.value,
              disabled: props.isApproving || rowCount === 0,
              'aria-label': '全选/全不选',
              onChange: toggleAll,
            }),
            h('span', null, `全选候选（${approvedCount.value}/${rowCount}）`),
          ]),
          h(
            'button',
            {
              type: 'button',
              class: 'pdm-approval__ghost-btn',
              disabled: props.isApproving || groups.length === 0,
              onClick: toggleExpandAll,
            },
            allExpanded.value ? '全部收起' : '全部展开'
          ),
        ]),
        h(
          'div',
          { class: 'pdm-approval__groups' },
          groups.map((group) => {
            const isExpanded = isGroupExpanded(group.key)
            const groupSelectedCount = getGroupSelectedCount(group)
            const groupPartidCount = getGroupApprovedPartidCount(group)
            const groupAllSelected = isGroupAllSelected(group)
            const groupNoneSelected = isGroupNoneSelected(group)

            return h('section', { key: group.key, class: 'pdm-approval-group' }, [
              h('div', { class: 'pdm-approval-group__header' }, [
                h(
                  'button',
                  {
                    type: 'button',
                    class: 'pdm-approval-group__toggle',
                    onClick: () => toggleGroupExpanded(group.key),
                  },
                  [
                    h('span', { class: 'pdm-approval-group__chevron', 'aria-hidden': 'true' }, isExpanded ? '▾' : '▸'),
                    h('span', { class: 'pdm-approval-group__type', title: group.typeName }, group.typeName),
                    h(
                      'span',
                      { class: 'pdm-approval-group__meta' },
                      `已选 ${groupSelectedCount}/${group.rows.length} · 提交 ${groupPartidCount} PARTID`
                    ),
                  ]
                ),
                h('label', { class: 'pdm-approval-group__pickall' }, [
                  h('input', {
                    type: 'checkbox',
                    class: 'pdm-approval__checkbox',
                    checked: groupAllSelected,
                    indeterminate: !groupAllSelected && !groupNoneSelected,
                    disabled: props.isApproving || group.rows.length === 0,
                    'aria-label': `全选 ${group.typeName} 候选`,
                    onChange: () => toggleGroupSelection(group),
                  }),
                  h('span', null, '本组全选'),
                ]),
              ]),
              renderPdmGroupTable(group),
            ])
          })
        ),
        h('div', { class: 'pdm-approval__manual' }, [
          h('label', { class: 'pdm-approval__manual-label' },
            '手动补充 PARTID — 每行输入 PARTID 和产品类型（可与上方表格同时使用）'
          ),
          h(
            'div',
            { class: 'pdm-approval__manual-rows' },
            manualPartidRows.value.map((row, index) =>
              h('div', { key: index, class: 'pdm-approval__manual-row' }, [
                h('input', {
                  type: 'text',
                  class: 'pdm-approval__manual-input',
                  placeholder: 'PARTID: 50GB-XXXXXX',
                  disabled: props.isApproving,
                  value: row.value,
                  onInput: (e: Event) => {
                    updateManualPartidRow(index, 'value', (e.target as HTMLInputElement).value)
                  },
                }),
                h('input', {
                  type: 'text',
                  class: 'pdm-approval__manual-type-input',
                  placeholder: '类型: 轴承',
                  disabled: props.isApproving,
                  value: row.type,
                  onInput: (e: Event) => {
                    updateManualPartidRow(index, 'type', (e.target as HTMLInputElement).value)
                  },
                }),
                index === manualPartidRows.value.length - 1
                  ? h(
                      'button',
                      {
                        type: 'button',
                        class: 'pdm-approval__manual-add-btn',
                        disabled: props.isApproving,
                        'aria-label': '添加一行',
                        title: '添加一行',
                        onClick: addManualPartidRow,
                      },
                      '+'
                    )
                  : null,
                manualPartidRows.value.length > 1
                  ? h(
                      'button',
                      {
                        type: 'button',
                        class: 'pdm-approval__manual-remove-btn',
                        disabled: props.isApproving,
                        'aria-label': '删除此行',
                        title: '删除此行',
                        onClick: () => removeManualPartidRow(index),
                      },
                      '−'
                    )
                  : null,
              ])
            )
          ),
          extraPartidEntriesFromManual.value.length > 0
            ? h('p', { class: 'pdm-approval__manual-hint' },
                `已识别 ${extraPartidEntriesFromManual.value.length} 个手动 PARTID`
              )
            : null,
        ]),
        h(
          'button',
          {
            class: 'task-action-btn task-action-btn--accent pdm-approval__approve',
            disabled: props.isApproving || noneSelected.value,
            onClick: submitApproval,
          },
          (() => {
            if (props.isApproving) return '提交中...'
            const manualCount = extraPartidEntriesFromManual.value.length
            const total = partidCount + manualCount
            if (manualCount > 0) {
              return `提交（${partidCount} 表格 + ${manualCount} 手动 = ${total} 个 PARTID）并继续 U8 查询`
            }
            return `提交已批准项（${partidCount} 个 PARTID）并继续 U8 查询`
          })()
        ),
      ])

      const elapsedMs = performance.now() - renderStartedAt
      const nowMs = Date.now()
      if (elapsedMs >= 25 || nowMs - lastPdmRenderCostLogAt >= 5000) {
        lastPdmRenderCostLogAt = nowMs
        logDiag('phase2_render_pdm_table_cost', {
          taskId: props.task.task_id,
          taskStatus: props.task.status,
          pdmItemCount: items.length,
          groupCount: groups.length,
          rowCount,
          approvedCount: approvedCount.value,
          elapsedMs,
          isEmpty: false,
        })
      }

      return node
    }
    const progressMessage = computed(() => {
      const raw = String(props.task.message ?? '').trim()
      if (!raw || raw === statusText.value) {
        return ''
      }
      return raw
    })
    const shortTaskId = computed(() => {
      const raw = String(props.task.task_id ?? '').trim()
      if (!raw) return '--'
      return `#${raw.slice(-8)}`
    })

    const formatTime = (value: string): string => {
      const date = new Date(value)
      if (Number.isNaN(date.getTime())) {
        return value
      }
      return date.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    }

    const hasU8ByTypeWorkbook = computed(() => {
      return props.task.status === 'completed'
    })

    return () =>
      h('article', { class: 'task-card' }, [
        h('div', { class: 'task-card__title' }, props.task.display_name || props.task.uploaded_file_name),
        h('div', { class: 'task-card__meta' }, [h('span', { class: ['status', `status--${props.task.status}`] }, statusText.value)]),
        h('div', { class: 'task-card__progress' }, [
          h(
            'div',
            {
              class: 'task-progress-ring',
              role: 'progressbar',
              'aria-label': `任务进度 ${progress.value}%`,
              'aria-valuemin': 0,
              'aria-valuemax': 100,
              'aria-valuenow': progress.value,
            },
            [
              h('svg', { class: 'task-progress-ring__svg', viewBox: '0 0 48 48', 'aria-hidden': 'true' }, [
                h('circle', { class: 'task-progress-ring__track', cx: 24, cy: 24, r: RING_RADIUS }),
                h('circle', {
                  class: ['task-progress-ring__value', progressStrokeClass.value],
                  cx: 24,
                  cy: 24,
                  r: RING_RADIUS,
                  'stroke-dasharray': RING_CIRCUMFERENCE.toFixed(2),
                  'stroke-dashoffset': ringDashOffset.value.toFixed(2),
                }),
              ]),
              h('span', { class: 'task-progress-ring__label' }, `${progress.value}%`),
            ]
          ),
          h('div', { class: 'task-card__progress-copy' }, [
            h('p', { class: 'task-card__progress-percent' }, `${progress.value}%`),
            h('p', { class: 'task-card__progress-caption' }, `当前状态：${statusText.value}`),
          ]),
        ]),
        progressMessage.value ? h('p', { class: 'task-card__progress-text' }, progressMessage.value) : null,
        renderPdmTable(),
        h('div', { class: 'task-card__footnote' }, [
          props.showOwner ? h('span', { class: 'task-card__owner', title: props.task.owner_username }, props.task.owner_username) : null,
          h('span', { class: 'task-card__id', title: props.task.task_id }, shortTaskId.value),
          h('span', { class: 'task-card__time' }, formatTime(props.task.created_at)),
        ]),
        props.task.error ? h('p', { class: 'task-card__error' }, props.task.error) : null,
        h('div', { class: 'task-card__actions' }, [
          h(
            'button',
            { class: 'task-action-btn task-action-btn--danger', disabled: !props.canCancel, onClick: () => emit('cancel') },
            '中断'
          ),
          h('button', { class: 'task-action-btn task-action-btn--accent', onClick: () => emit('view-file') }, '文件'),
          hasU8ByTypeWorkbook.value
            ? h(
                'button',
                {
                  class: 'task-action-btn task-action-btn--accent',
                  onClick: () => emit('download-u8-xlsx'),
                },
                '下载Excel'
              )
            : null,
          props.canDelete
            ? h(
                'button',
                { class: 'task-action-btn task-action-btn--danger', onClick: () => emit('delete') },
                '删除'
              )
            : null,
        ]),
      ])
  },
})
</script>

<style scoped lang="scss">
.quotation-page {
  height: 100%;
  padding: 24px 24px 16px;
  overflow: auto;
  box-sizing: border-box;
  background: var(--yamato-color-bg-light);
}

.hidden-input {
  display: none;
}

.quotation-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 16px;
}

.quotation-title {
  margin: 0;
  color: var(--yamato-color-text-primary);
  font-family: var(--yamato-font-display);
  font-size: 34px;
  font-weight: 500;
  line-height: 1.2;
  letter-spacing: 0;
}

.quotation-subtitle {
  margin: 6px 0 0;
  color: var(--yamato-color-text-secondary);
  font-size: 14px;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.primary-btn,
.secondary-btn {
  min-height: 36px;
  padding: 0 14px;
  cursor: pointer;
  font-size: 14px;
  border-radius: var(--yamato-radius-md);
  transition: background 0.2s ease, color 0.2s ease, box-shadow 0.2s ease;

  &:focus-visible {
    outline: none;
    box-shadow: var(--yamato-focus-ring);
  }
}

.primary-btn {
  border: none;
  background: var(--yamato-color-accent);
  color: #fff;

  &:hover:not(:disabled) {
    background: var(--yamato-color-accent-hover);
  }
}

.secondary-btn {
  border: 1px solid var(--yamato-color-border-subtle);
  background: var(--yamato-color-surface);
  color: var(--yamato-color-text-primary);

  &:hover:not(:disabled) {
    background: var(--yamato-color-surface-alt);
  }
}

.primary-btn:disabled,
.secondary-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.error-tip {
  margin: 0 0 12px;
  color: var(--yamato-color-danger);
}

.columns {
  display: grid;
  /* Slightly narrow queue column; give space to in-progress (处理中) column; done column share unchanged */
  grid-template-columns: minmax(220px, 0.6fr) minmax(260px, 1.4fr) minmax(220px, 1fr);
  gap: 14px;
  min-height: 0;
}

.column {
  background: #ffffff;
  border-radius: var(--yamato-radius-lg);
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 420px;
  box-shadow: var(--yamato-shadow-card);
}

.task-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
}

.column--done .task-list--scroll {
  max-height: clamp(260px, calc(100vh - 320px), 640px);
  overflow-y: auto;
  padding-right: 4px;
}

.column-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.column-header h2 {
  margin: 0;
  font-size: 21px;
  line-height: 1.19;
  letter-spacing: -0.22px;
  color: var(--yamato-color-text-primary);
}

.column-header span {
  color: var(--yamato-color-text-muted);
  font-size: 13px;
}

.dev-tip {
  margin: 0;
  color: var(--yamato-color-warning);
  background: var(--yamato-color-warning-soft);
  padding: 8px;
  border-radius: var(--yamato-radius-sm);
  font-size: 12px;
}

.empty {
  color: var(--yamato-color-text-muted);
  font-size: 13px;
  margin-top: 6px;
}

:deep(.task-card) {
  border-radius: var(--yamato-radius-sm);
  padding: 10px 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 7px;
  background: var(--yamato-color-surface-alt);
}

:deep(.task-card__title) {
  font-weight: 600;
  font-size: 14px;
  color: var(--yamato-color-text-primary);
  word-break: break-all;
}

:deep(.task-card__owner),
:deep(.task-card__time),
:deep(.task-card__progress-text),
:deep(.task-card__id) {
  margin: 0;
  color: var(--yamato-color-text-secondary);
  font-size: 12px;
  line-height: 1.4;
}

:deep(.task-card__meta) {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
}

:deep(.task-card__progress) {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px;
  border-radius: var(--yamato-radius-sm);
  border: 1px solid var(--yamato-color-border-subtle);
  background: #ffffff;
}

:deep(.task-progress-ring) {
  position: relative;
  width: 58px;
  height: 58px;
  flex: 0 0 58px;
}

:deep(.task-progress-ring__svg) {
  width: 58px;
  height: 58px;
}

:deep(.task-progress-ring__track),
:deep(.task-progress-ring__value) {
  fill: none;
  stroke-width: 5;
}

:deep(.task-progress-ring__track) {
  stroke: #e8e6dc;
}

:deep(.task-progress-ring__value) {
  stroke-linecap: round;
  transform: rotate(-90deg);
  transform-origin: 50% 50%;
  transition: stroke-dashoffset 0.28s ease, stroke 0.28s ease;
}

:deep(.task-progress-ring__value--queued) {
  stroke: var(--yamato-color-warning);
}

:deep(.task-progress-ring__value--running) {
  stroke: var(--yamato-color-accent);
}

:deep(.task-progress-ring__value--awaiting) {
  stroke: var(--yamato-color-warning);
}

:deep(.task-progress-ring__value--completed) {
  stroke: var(--yamato-color-success);
}

:deep(.task-progress-ring__value--failed),
:deep(.task-progress-ring__value--cancelled) {
  stroke: var(--yamato-color-danger);
}

:deep(.task-progress-ring__label) {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 700;
  color: var(--yamato-color-text-primary);
}

:deep(.task-card__progress-copy) {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

:deep(.task-card__progress-percent) {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--yamato-color-text-primary);
}

:deep(.task-card__progress-caption) {
  margin: 0;
  font-size: 12px;
  color: var(--yamato-color-text-secondary);
  line-height: 1.4;
}

:deep(.status) {
  font-size: 12px;
  font-weight: 600;
}

:deep(.status--queued) {
  color: var(--yamato-color-warning);
}

:deep(.status--running) {
  color: var(--yamato-color-accent);
}

:deep(.status--awaiting_approval) {
  color: var(--yamato-color-warning);
}

:deep(.status--completed) {
  color: var(--yamato-color-success);
}

:deep(.status--failed),
:deep(.status--cancelled) {
  color: var(--yamato-color-danger);
}

:deep(.task-card__error) {
  margin: 0;
  color: var(--yamato-color-danger);
  font-size: 12px;
}

:deep(.task-card__footnote) {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--yamato-color-text-muted);
  min-width: 0;
}

:deep(.task-card__footnote .task-card__owner),
:deep(.task-card__footnote .task-card__id),
:deep(.task-card__footnote .task-card__time) {
  white-space: nowrap;
}

:deep(.task-card__footnote .task-card__owner) {
  max-width: 86px;
  overflow: hidden;
  text-overflow: ellipsis;
}

:deep(.task-card__footnote .task-card__id) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
}

:deep(.pdm-approval) {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
  border-radius: 12px;
  border: 1px solid #f0eee6;
  background: #faf9f5;
  box-shadow: 0 0 0 1px #f0eee6;
}

:deep(.pdm-approval__title) {
  margin: 0;
  color: #141413;
  font-family: var(--yamato-font-display), Georgia, serif;
  font-size: 16px;
  font-weight: 500;
  line-height: 1.25;
}

:deep(.pdm-approval__empty) {
  margin: 0;
  font-size: 12px;
  color: #87867f;
}

:deep(.pdm-approval__toolbar) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

:deep(.pdm-approval__toolbar-main) {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #4d4c48;
  font-size: 12px;
  line-height: 1.4;
}

:deep(.pdm-approval__ghost-btn) {
  height: 28px;
  padding: 0 10px;
  border: 1px solid #d1cfc5;
  border-radius: 8px;
  background: #e8e6dc;
  color: #4d4c48;
  font-size: 12px;
  cursor: pointer;
  box-shadow: 0 0 0 1px #d1cfc5;

  &:hover:not(:disabled) {
    background: #f0eee6;
  }

  &:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }
}

:deep(.pdm-approval__groups) {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

:deep(.pdm-approval-group) {
  border: 1px solid #e8e6dc;
  border-radius: 10px;
  background: #ffffff;
  overflow: hidden;
}

:deep(.pdm-approval-group__header) {
  min-height: 40px;
  background: #faf9f5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  border-bottom: 1px solid #f0eee6;
}

:deep(.pdm-approval-group__toggle) {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0;
  text-align: left;
  cursor: pointer;

  &:hover {
    opacity: 0.86;
  }
}

:deep(.pdm-approval-group__chevron) {
  color: #5e5d59;
  font-size: 12px;
}

:deep(.pdm-approval-group__type) {
  color: #141413;
  font-family: var(--yamato-font-display), Georgia, serif;
  font-size: 15px;
  font-weight: 500;
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.pdm-approval-group__meta) {
  color: #5e5d59;
  font-size: 12px;
  white-space: nowrap;
}

:deep(.pdm-approval-group__pickall) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #4d4c48;
  font-size: 12px;
}

:deep(.pdm-approval-group__table-wrapper) {
  max-height: 240px;
  overflow: auto;
}

:deep(.pdm-approval__table) {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  color: #141413;
}

:deep(.pdm-approval__table th),
:deep(.pdm-approval__table td) {
  padding: 7px 8px;
  text-align: left;
  border-bottom: 1px solid #f0eee6;
  white-space: nowrap;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
}

:deep(.pdm-approval__table thead th) {
  position: sticky;
  top: 0;
  background: #faf9f5;
  color: #4d4c48;
  font-weight: 500;
  z-index: 1;
}

:deep(.pdm-approval__checkbox-col) {
  width: 30px;
  padding: 4px 6px;
  text-align: center;
}

:deep(.pdm-approval__checkbox) {
  margin: 0;
  cursor: pointer;
  accent-color: #c96442;
}

:deep(.pdm-approval__checkbox:disabled) {
  cursor: not-allowed;
}

:deep(.pdm-approval__row--skipped td) {
  color: #87867f;
  text-decoration: line-through;
}

:deep(.pdm-approval__approve) {
  align-self: flex-start;
  margin-top: 2px;
}

:deep(.pdm-approval__manual) {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 12px;
}

:deep(.pdm-approval__manual-label) {
  font-size: 12px;
  color: #4d4c48;
  font-weight: 500;
}

:deep(.pdm-approval__manual-rows) {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

:deep(.pdm-approval__manual-row) {
  display: flex;
  align-items: center;
  gap: 6px;
}

:deep(.pdm-approval__manual-input),
:deep(.pdm-approval__manual-type-input) {
  flex: 1;
  min-width: 0;
  box-sizing: border-box;
  font-family: monospace;
  font-size: 12px;
  padding: 6px 8px;
  border: 1px solid #d1cdc7;
  border-radius: 4px;
  background: #fafaf8;
  color: #3c3a36;
}

:deep(.pdm-approval__manual-input:focus),
:deep(.pdm-approval__manual-type-input:focus) {
  outline: none;
  border-color: #8b7355;
}

:deep(.pdm-approval__manual-input:disabled),
:deep(.pdm-approval__manual-type-input:disabled) {
  opacity: 0.6;
  cursor: not-allowed;
}

:deep(.pdm-approval__manual-type-input) {
  font-family: sans-serif;
  font-size: 12px;
}

:deep(.pdm-approval__manual-add-btn),
:deep(.pdm-approval__manual-remove-btn) {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 1px solid #d1cdc7;
  border-radius: 4px;
  background: #fafaf8;
  color: #4d4c48;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}

:deep(.pdm-approval__manual-add-btn:hover:not(:disabled)),
:deep(.pdm-approval__manual-remove-btn:hover:not(:disabled)) {
  border-color: #8b7355;
  color: #8b7355;
}

:deep(.pdm-approval__manual-add-btn:disabled),
:deep(.pdm-approval__manual-remove-btn:disabled) {
  opacity: 0.6;
  cursor: not-allowed;
}

:deep(.pdm-approval__manual-hint) {
  font-size: 11px;
  color: #6b7280;
  margin: 0;
}

:deep(.task-card__actions) {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

:deep(.task-action-btn) {
  height: 30px;
  min-width: 58px;
  padding: 0 12px;
  font-size: 12px;
  font-weight: 500;
  border-radius: var(--yamato-radius-md);
  border: 1px solid transparent;
  cursor: pointer;
  transition: background 0.18s ease, border-color 0.18s ease, color 0.18s ease, opacity 0.18s ease, transform 0.18s ease,
    box-shadow 0.18s ease;

  &:focus-visible {
    outline: none;
    box-shadow: var(--yamato-focus-ring);
  }

  &:hover:not(:disabled) {
    transform: translateY(-1px);
  }

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
}

:deep(.task-action-btn--danger) {
  background: var(--yamato-color-danger-soft);
  color: var(--yamato-color-danger);
  border-color: rgba(181, 51, 51, 0.24);
  box-shadow: 0 0 0 1px rgba(181, 51, 51, 0.11);

  &:hover:not(:disabled) {
    background: rgba(181, 51, 51, 0.14);
    border-color: rgba(181, 51, 51, 0.32);
  }
}

:deep(.task-action-btn--neutral) {
  background: #ffffff;
  color: var(--yamato-color-text-primary);
  border-color: var(--yamato-color-border-subtle);
  box-shadow: 0 0 0 1px #f0eee6;

  &:hover:not(:disabled) {
    background: #f4f2ea;
    border-color: #d1cfc5;
  }
}

:deep(.task-action-btn--accent) {
  background: var(--yamato-color-accent);
  color: var(--yamato-color-text-on-dark);
  border-color: rgba(201, 100, 66, 0.88);
  box-shadow: 0 0 0 1px rgba(201, 100, 66, 0.38);

  &:hover:not(:disabled) {
    background: var(--yamato-color-accent-hover);
    border-color: rgba(217, 119, 87, 0.92);
  }
}

.task-name-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.task-name-field__label {
  font-size: 14px;
  color: var(--yamato-color-text-secondary);
}

.task-name-field__input {
  width: 100%;
  box-sizing: border-box;
  min-height: 36px;
  padding: 0 12px;
  border: 1px solid var(--yamato-color-border-subtle);
  border-radius: var(--yamato-radius-sm);
  font-size: 14px;
  color: var(--yamato-color-text-primary);
  background: var(--yamato-color-surface);

  &:focus-visible {
    outline: none;
    box-shadow: var(--yamato-focus-ring);
  }
}

.task-name-field__textarea {
  padding: 8px 12px;
  resize: vertical;
  line-height: 1.5;
  font-family: inherit;
}

.direct-u8-input-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 12px;
  margin-top: 12px;
}

@media (max-width: 640px) {
  .direct-u8-input-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 1200px) {
  .columns {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 860px) {
  .quotation-page {
    padding: 20px 16px 12px;
  }

  .quotation-header {
    flex-direction: column;
    align-items: flex-start;
  }

  .quotation-title {
    font-size: 28px;
  }

  :deep(.task-card__progress) {
    gap: 8px;
    padding: 7px;
  }

  :deep(.task-progress-ring),
  :deep(.task-progress-ring__svg) {
    width: 52px;
    height: 52px;
    flex-basis: 52px;
  }

  :deep(.task-card__actions) {
    gap: 6px;
  }

  :deep(.task-action-btn) {
    flex: 1 1 calc(50% - 6px);
    min-width: 0;
  }
}
</style>

