<template>
  <div class="page">
    <div class="page-header">
      <div class="page-header__left">
        <h1 class="page-header__title">知识库管理</h1>
      </div>
      <div class="page-header__actions">
        <input
          ref="docInputRef"
          class="upload-input"
          type="file"
          multiple
          accept=".txt,.md,.pdf,.doc,.docx,.ppt,.pptx,.html,.json"
          @change="onDocFilesChosen"
        />
        <input
          ref="excelInputRef"
          class="upload-input"
          type="file"
          multiple
          accept=".xlsx,.xls"
          @change="onExcelFilesChosen"
        />
        <button
          class="upload-btn"
          type="button"
          :disabled="uploading !== null || conflictDialog.visible"
          @click="pickDocumentFiles"
        >
          上传文档
        </button>
        <button
          class="upload-btn upload-btn--excel"
          type="button"
          :disabled="uploading !== null || conflictDialog.visible"
          @click="pickExcelFiles"
        >
          上传 Excel 数据库
        </button>
      </div>
    </div>

    <div v-if="uploading" class="upload-progress" role="status" aria-live="polite">
      <div class="upload-progress__header">
        <span class="upload-progress__title" :title="uploading.fileNames.join('、')">
          {{ uploading.collection === 'documents' ? '文档' : 'Excel 数据库' }}上传
          · {{ uploading.fileNames.join('、') }}
        </span>
        <span class="upload-progress__status">{{ uploadStatusText }}</span>
      </div>
      <div class="upload-progress__bar">
        <div
          class="upload-progress__fill"
          :class="{ 'upload-progress__fill--indeterminate': uploading.status === 'submitting' }"
          :style="uploading.status === 'submitting' ? {} : { width: `${uploading.progress}%` }"
        ></div>
      </div>
      <div v-if="uploading.message" class="upload-progress__message">{{ uploading.message }}</div>
    </div>

    <div class="page__content">
      <template v-if="isAdmin">
      <div class="records">
        <div class="records__toolbar">
          <button class="records__refresh" type="button" :disabled="loadingRecords" @click="loadRecords">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              :class="{ 'records__refresh-icon--spinning': loadingRecords }"
              class="records__refresh-icon"
              aria-hidden="true"
            >
              <path
                d="M1 4v6h6M23 20v-6h-6"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
              <path
                d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4-4.64 4.36A9 9 0 0 1 3.51 15"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
            刷新
          </button>
          <span class="records__count">共 {{ records.length }} 条 · {{ groupedRecords.length }} 个文件</span>
        </div>

        <div v-if="loadingRecords" class="records__loading">
          <div class="records__loading-dots">
            <span></span><span></span><span></span>
          </div>
        </div>

        <div v-else-if="records.length === 0" class="records__empty">
          暂无记录
        </div>

        <div v-else class="records__groups">
          <section
            v-for="group in groupedRecords"
            :key="group.key"
            class="file-group"
          >
            <header class="file-group__header">
              <div class="file-group__title-row">
                <h2 class="file-group__title" :title="group.fileName">{{ group.fileName }}</h2>
                <span class="file-group__badge">{{ group.count }} 条</span>
              </div>
              <div class="file-group__meta">
                <span class="file-group__meta-item">最近：{{ formatUploadTime(group.latestUploadTime) }}</span>
                <span v-if="group.uploaders" class="file-group__meta-item">上传：{{ group.uploaders }}</span>
              </div>
            </header>

            <div class="file-group__records">
              <div
                v-for="record in group.items"
                :key="record.id"
                class="record-card"
                :class="{ 'record-card--expanded': expandedId === record.id }"
              >
                <div class="record-card__header" @click="toggleExpand(record.id)">
                  <div class="record-card__meta">
                    <span class="record-card__time">{{ formatUploadTime(record.upload_time) }}</span>
                    <span v-if="record.uploader" class="record-card__uploader">{{ record.uploader }}</span>
                    <span class="record-card__summary">{{ getSummary(record.text) }}</span>
                  </div>
                  <div class="record-card__right">
                    <button
                      class="delete-btn"
                      :disabled="deletingId === record.id"
                      @click.stop="openDeleteDialog(record.id)"
                    >
                      {{ deletingId === record.id ? '...' : '删除' }}
                    </button>
                    <svg
                      width="14"
                      height="14"
                      viewBox="0 0 24 24"
                      fill="none"
                      class="record-card__chevron"
                      aria-hidden="true"
                    >
                      <path
                        d="M6 9l6 6 6-6"
                        stroke="currentColor"
                        stroke-width="2"
                        stroke-linecap="round"
                        stroke-linejoin="round"
                      />
                    </svg>
                  </div>
                </div>

                <div v-if="expandedId === record.id" class="record-card__body">
                  <div v-if="hasStructuredFields(record.text)" class="record-fields">
                    <div
                      v-for="field in parseFields(record.text)"
                      :key="field.label"
                      class="record-field"
                    >
                      <span class="record-field__label">{{ field.label }}</span>
                      <span class="record-field__value">{{ field.value }}</span>
                    </div>
                  </div>
                  <div v-else class="record-raw-text">{{ record.text }}</div>
                </div>
              </div>
            </div>
          </section>
        </div>
      </div>
      </template>

      <div v-else class="records__restricted">
        <h2 class="records__restricted-title">知识库记录仅管理员可见</h2>
        <p class="records__restricted-text">
          你可以通过右上角「上传文档 / 上传 Excel 数据库」为知识库添加内容，AI 检索将使用这些资料。
        </p>
      </div>
    </div>

    <ConfirmDialog
      v-model="showDeleteDialog"
      title="删除知识库记录"
      message="确定要删除这条记录吗？此操作无法撤销。"
      type="danger"
      confirm-text="删除"
      cancel-text="取消"
      @confirm="confirmDelete"
    />

    <div v-if="conflictDialog.visible" class="conflict-mask" @click.self="cancelConflict">
      <div class="conflict-dialog" role="dialog" aria-modal="true" aria-label="同名文件冲突">
        <h3 class="conflict-dialog__title">同名文件已存在</h3>
        <p class="conflict-dialog__text">以下文件在知识库中已有记录：</p>
        <ul class="conflict-dialog__list">
          <li v-for="name in conflictDialog.fileNames" :key="name">{{ name }}</li>
        </ul>
        <p class="conflict-dialog__text">请选择处理方式：</p>
        <div class="conflict-dialog__actions">
          <button class="conflict-btn conflict-btn--replace" type="button" @click="resolveConflict('replace')">
            替换（删除旧内容）
          </button>
          <button class="conflict-btn conflict-btn--append" type="button" @click="resolveConflict('append')">
            追加（保留旧内容）
          </button>
          <button class="conflict-btn conflict-btn--cancel" type="button" @click="cancelConflict">
            取消
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ConfirmDialog, useToast } from '@nbhx/components'
import {
  deleteKnowledgeRecord,
  fetchKnowledgeTaskStatus,
  listKnowledgeRecords,
  uploadKnowledgeFiles,
  type KnowledgeConflictStrategy,
  type KnowledgeRecord,
  type KnowledgeUploadCollection,
} from '../services/knowledge'
import { readStored } from '../services/storage'
import { config } from '../config'

interface ParsedField {
  label: string
  value: string
}

interface FileGroup {
  key: string
  fileName: string
  items: KnowledgeRecord[]
  count: number
  latestUploadTime: string | null
  uploaders: string
}

const { showSuccess, showError, showWarning } = useToast()

// 角色（列表仅管理员可见，上传对所有登录用户开放）
const userRole = ref('')
const isAdmin = computed(() => userRole.value === 'admin' || userRole.value === 'superuser')

const readUserRole = () => {
  const parsed = readStored<{ role?: unknown } | null>(config.settingsStorageKey, null)
  userRole.value = String(parsed?.role ?? '').trim()
}

const records = ref<KnowledgeRecord[]>([])
const loadingRecords = ref(false)
const expandedId = ref<string | null>(null)
const deletingId = ref<string | null>(null)
const showDeleteDialog = ref(false)
const recordToDelete = ref<string | null>(null)

const UNTITLED_FILE_KEY = '__untitled_file__'

// ---------- 上传相关 ----------

/** 后端限制：文档 ≤50MB，Excel ≤20MB（与 domain 规则一致） */
const MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
const MAX_EXCEL_BYTES = 20 * 1024 * 1024
const UPLOAD_POLL_INTERVAL_MS = 1500

interface UploadingState {
  taskId: string
  collection: KnowledgeUploadCollection
  fileNames: string[]
  status: 'submitting' | 'processing' | 'completed' | 'failed' | 'cancelled'
  progress: number
  message: string
}

interface ConflictState {
  visible: boolean
  collection: KnowledgeUploadCollection
  files: File[]
  fileNames: string[]
}

const docInputRef = ref<HTMLInputElement | null>(null)
const excelInputRef = ref<HTMLInputElement | null>(null)
const uploading = ref<UploadingState | null>(null)
let uploadPollTimer: number | null = null
const conflictDialog = ref<ConflictState>({
  visible: false,
  collection: 'documents',
  files: [],
  fileNames: [],
})

const uploadStatusText = computed(() => {
  const current = uploading.value
  if (!current) return ''
  switch (current.status) {
    case 'submitting':
      return '提交中…'
    case 'processing':
      return `处理中 ${current.progress}%`
    case 'completed':
      return '已完成'
    case 'failed':
      return '失败'
    case 'cancelled':
      return '已取消'
    default:
      return ''
  }
})

const pickDocumentFiles = () => {
  docInputRef.value?.click()
}

const pickExcelFiles = () => {
  excelInputRef.value?.click()
}

const onDocFilesChosen = (event: Event) => {
  handleFilesChosen(event, 'documents')
}

const onExcelFilesChosen = (event: Event) => {
  handleFilesChosen(event, 'excel-db')
}

const handleFilesChosen = (event: Event, collection: KnowledgeUploadCollection) => {
  const input = event.target as HTMLInputElement | null
  const files = Array.from(input?.files ?? [])
  if (input) {
    input.value = ''
  }
  if (files.length === 0) return

  const maxBytes = collection === 'documents' ? MAX_DOCUMENT_BYTES : MAX_EXCEL_BYTES
  const oversized = files.filter((file) => file.size > maxBytes)
  if (oversized.length > 0) {
    showError(
      `以下文件超过大小限制（${Math.round(maxBytes / 1024 / 1024)}MB）：${oversized
        .map((file) => file.name)
        .join('、')}`
    )
    return
  }

  void submitUpload(collection, files)
}

const submitUpload = async (
  collection: KnowledgeUploadCollection,
  files: File[],
  onConflict?: KnowledgeConflictStrategy
) => {
  uploading.value = {
    taskId: '',
    collection,
    fileNames: files.map((file) => file.name),
    status: 'submitting',
    progress: 0,
    message: '正在提交…',
  }

  try {
    const result = await uploadKnowledgeFiles(collection, files, onConflict)
    uploading.value.taskId = result.task_id
    uploading.value.status = 'processing'
    uploading.value.message = result.message || '任务已创建'
    void pollTaskStatus(result.task_id)
  } catch (err: unknown) {
    const apiErr = err as {
      status?: number
      code?: string
      message?: string
      details?: unknown
    }
    if (apiErr.status === 409 || apiErr.code === 'KNOWLEDGE_FILE_NAME_CONFLICT') {
      const details = (apiErr.details ?? {}) as {
        file_name?: unknown
      }
      const conflictName = typeof details.file_name === 'string' ? details.file_name : ''
      conflictDialog.value = {
        visible: true,
        collection,
        files,
        fileNames: conflictName ? [conflictName] : files.map((file) => file.name),
      }
      uploading.value = null
      return
    }
    showError(apiErr.message || '上传失败')
    uploading.value = null
  }
}

const pollTaskStatus = async (taskId: string) => {
  if (!uploading.value || uploading.value.taskId !== taskId) return

  try {
    const result = await fetchKnowledgeTaskStatus(taskId)
    if (!uploading.value || uploading.value.taskId !== taskId) return

    const status = String(result.status ?? '').toLowerCase()
    uploading.value.progress = result.progress ?? 0
    uploading.value.message = result.message ?? ''

    if (status === 'completed') {
      uploading.value.status = 'completed'
      // issue15：部分文件解析失败时任务整体完成，但要把失败明细亮给用户
      const failedFiles = result.result?.failed_files ?? []
      if (failedFiles.length > 0) {
        const names = failedFiles
          .map((item) => item.file_name || '未命名文件')
          .join('、')
        showWarning(`部分文件处理失败：${names}，请检查文件格式后重新上传`, 6000)
      } else {
        showSuccess('上传处理完成')
      }
      uploading.value = null
      if (isAdmin.value) {
        void loadRecords()
      }
      return
    }

    if (status === 'failed' || status === 'cancelled') {
      uploading.value.status = status
      // issue15：failed 时优先展示后端带回的具体失败原因（error 字段）
      const failureText =
        status === 'failed'
          ? result.error || result.message || '上传处理失败'
          : result.message || '任务已取消'
      showError(failureText, 6000)
      uploading.value = null
      return
    }

    uploading.value.status = 'processing'
    uploadPollTimer = window.setTimeout(() => {
      void pollTaskStatus(taskId)
    }, UPLOAD_POLL_INTERVAL_MS)
  } catch (err: unknown) {
    showError((err as { message?: string })?.message || '上传状态查询失败')
    uploading.value = null
  }
}

const cancelConflict = () => {
  conflictDialog.value.visible = false
  conflictDialog.value.files = []
  conflictDialog.value.fileNames = []
}

const resolveConflict = (strategy: KnowledgeConflictStrategy) => {
  const { collection, files } = conflictDialog.value
  cancelConflict()
  void submitUpload(collection, files, strategy)
}

const formatUploadTime = (value: string | null | undefined): string => {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const groupedRecords = computed<FileGroup[]>(() => {
  const groupedMap = new Map<string, KnowledgeRecord[]>()

  for (const record of records.value) {
    const fileName = String(record.file_name ?? '').trim()
    const key = fileName || UNTITLED_FILE_KEY
    const bucket = groupedMap.get(key)
    if (bucket) {
      bucket.push(record)
    } else {
      groupedMap.set(key, [record])
    }
  }

  return Array.from(groupedMap.entries()).map(([key, items]) => {
    const uploaders = Array.from(
      new Set(
        items
          .map((item) => String(item.uploader ?? '').trim())
          .filter(Boolean)
      )
    ).join('、')

    const latestUploadTime = items
      .map((item) => item.upload_time)
      .filter((time): time is string => Boolean(time))
      .sort((a, b) => new Date(b).getTime() - new Date(a).getTime())[0] ?? null

    return {
      key,
      fileName: key === UNTITLED_FILE_KEY ? '未命名文件' : key,
      items,
      count: items.length,
      latestUploadTime,
      uploaders,
    }
  })
})

const loadRecords = async () => {
  loadingRecords.value = true
  try {
    records.value = await listKnowledgeRecords()
  } catch (err: any) {
    showError(err?.message || '加载失败')
  } finally {
    loadingRecords.value = false
  }
}

const toggleExpand = (id: string) => {
  expandedId.value = expandedId.value === id ? null : id
}

const parseFields = (text: string): ParsedField[] => {
  return text
    .split(/,\s*/)
    .map((part) => {
      const trimmed = part.trim()
      if (!trimmed) return null
      const match = /^([^：:\n]{1,24})[：:]\s*(.+)$/.exec(trimmed)
      if (!match) return null
      const label = match[1].trim()
      const value = match[2].trim()
      if (!label || !value) return null
      return { label, value }
    })
    .filter((f): f is ParsedField => f !== null)
}

const hasStructuredFields = (text: string): boolean => {
  const fields = parseFields(text)
  // Structured key-value records usually contain many fields.
  return fields.length >= 3
}

const getSummary = (text: string): string => {
  const fields = parseFields(text)
  const customer = fields.find((f) => f.label === '客户名称')?.value ?? ''
  const model = fields.find((f) => f.label === '型号规格')?.value ?? ''
  const qty = fields.find((f) => f.label === '数量')?.value ?? ''
  const parts = [customer, model, qty ? `×${qty}` : ''].filter(Boolean)
  return parts.join('  ') || text.slice(0, 50)
}

const openDeleteDialog = (id: string) => {
  recordToDelete.value = id
  showDeleteDialog.value = true
}

const confirmDelete = async () => {
  if (!recordToDelete.value) return

  const id = recordToDelete.value
  deletingId.value = id
  try {
    await deleteKnowledgeRecord(id)
    records.value = records.value.filter((record) => record.id !== id)
    if (expandedId.value === id) {
      expandedId.value = null
    }
    showSuccess('删除成功')
  } catch (err: any) {
    showError(err?.message || '删除失败')
  } finally {
    deletingId.value = null
    recordToDelete.value = null
    showDeleteDialog.value = false
  }
}

onMounted(() => {
  readUserRole()
  if (isAdmin.value) {
    void loadRecords()
  }
})

onBeforeUnmount(() => {
  if (uploadPollTimer !== null) {
    window.clearTimeout(uploadPollTimer)
  }
})
</script>

<style scoped lang="scss">
.page {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 32px 32px 24px;
  box-sizing: border-box;
  overflow: auto;
  background: var(--nbhx-color-bg-light);
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
  flex-shrink: 0;
}

.page-header__left {
  display: flex;
  align-items: baseline;
  gap: 32px;
}

.page-header__title {
  margin: 0;
  font-family: var(--nbhx-font-display);
  font-size: 34px;
  font-weight: 500;
  line-height: 1.2;
  letter-spacing: 0;
  color: var(--nbhx-color-text-primary);
}

.page__content {
  background: #ffffff;
  border-radius: var(--nbhx-radius-lg);
  box-shadow: var(--nbhx-shadow-card);
  padding: 32px;
  flex: 1;
  display: flex;
  justify-content: center;
}

.records {
  width: 100%;
  max-width: 960px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.records__toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
}

.records__refresh {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 34px;
  padding: 0 14px;
  border-radius: var(--nbhx-radius-sm);
  border: 1px solid var(--nbhx-color-border-subtle);
  background: var(--nbhx-color-surface);
  color: var(--nbhx-color-text-primary);
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s ease, box-shadow 0.2s ease;

  &:hover:not(:disabled) {
    background: var(--nbhx-color-surface-alt);
  }

  &:focus-visible {
    outline: none;
    box-shadow: var(--nbhx-focus-ring);
  }

  &:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
}

.records__refresh-icon {
  flex-shrink: 0;
  transition: transform 0.4s linear;

  &--spinning {
    animation: spin 0.8s linear infinite;
  }
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.records__count {
  font-size: 14px;
  color: var(--nbhx-color-text-secondary);
}

.records__loading {
  display: flex;
  justify-content: center;
  padding: 40px 0;
}

.records__loading-dots {
  display: flex;
  gap: 6px;

  span {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--nbhx-color-accent);
    animation: dot-bounce 1.2s ease-in-out infinite;

    &:nth-child(2) { animation-delay: 0.2s; }
    &:nth-child(3) { animation-delay: 0.4s; }
  }
}

@keyframes dot-bounce {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}

.records__empty {
  padding: 48px 0;
  text-align: center;
  font-size: 14px;
  color: var(--nbhx-color-text-muted);
}

.records__groups {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.file-group {
  border-radius: var(--nbhx-radius-md);
  border: 1px solid var(--nbhx-color-border-subtle);
  background: #ffffff;
  box-shadow: 0 2px 10px rgba(20, 20, 19, 0.06);
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.file-group__header {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 2px 2px 6px;
  border-bottom: 1px solid var(--nbhx-color-border-subtle);
}

.file-group__title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.file-group__title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--nbhx-color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-group__badge {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--nbhx-color-text-secondary);
  background: var(--nbhx-color-surface-alt);
  border-radius: var(--nbhx-radius-pill);
  padding: 2px 8px;
}

.file-group__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.file-group__meta-item {
  font-size: 12px;
  color: var(--nbhx-color-text-muted);
}

.file-group__records {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.record-card {
  border-radius: var(--nbhx-radius-sm);
  border: 1px solid var(--nbhx-color-border-subtle);
  background: var(--nbhx-color-surface);
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  transition: box-shadow 0.2s ease, border-color 0.2s ease;
  border-left: 3px solid var(--nbhx-color-accent);

  &:hover {
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.12);
  }

  &--expanded {
    border-color: rgba(201, 100, 66, 0.4);
  }
}

.record-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  cursor: pointer;
  user-select: none;
}

.record-card__meta {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  flex: 1;
}

.record-card__time {
  font-size: 12px;
  color: var(--nbhx-color-text-muted);
  white-space: nowrap;
  flex-shrink: 0;
}

.record-card__uploader {
  font-size: 12px;
  color: var(--nbhx-color-text-muted);
  white-space: nowrap;
  flex-shrink: 0;
  background: #ebe8de;
  padding: 1px 7px;
  border-radius: var(--nbhx-radius-pill);
}

.record-card__summary {
  font-size: 13px;
  color: var(--nbhx-color-text-primary);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.record-card__right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  margin-left: 12px;
}

.record-card__chevron {
  flex-shrink: 0;
  color: var(--nbhx-color-text-muted);
  transition: transform 0.2s ease;

  .record-card--expanded & {
    transform: rotate(180deg);
  }
}

.delete-btn {
  height: 26px;
  padding: 0 12px;
  border-radius: var(--nbhx-radius-pill);
  border: none;
  color: var(--nbhx-color-danger);
  background: var(--nbhx-color-danger-soft);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease, opacity 0.15s ease;
  white-space: nowrap;

  &:hover:not(:disabled) {
    background: rgba(196, 59, 47, 0.2);
  }

  &:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
}

.record-card__body {
  border-top: 1px solid var(--nbhx-color-border-subtle);
  padding: 16px;
  background: var(--nbhx-color-surface-alt);
}

.record-fields {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}

.record-field {
  display: grid;
  grid-template-columns: 96px 1fr;
  gap: 12px;
  align-items: start;
  padding-bottom: 8px;
  border-bottom: 1px dashed rgba(0, 0, 0, 0.12);
}

.record-field__label {
  font-size: 12px;
  color: var(--nbhx-color-text-muted);
  font-weight: 600;
  text-align: left;
}

.record-field__value {
  font-size: 13px;
  color: var(--nbhx-color-text-primary);
  line-height: 1.7;
  text-align: justify;
  text-justify: inter-ideograph;
  word-break: break-word;
}

.record-raw-text {
  font-size: 13px;
  color: var(--nbhx-color-text-primary);
  line-height: 1.8;
  text-align: justify;
  text-justify: inter-ideograph;
  white-space: pre-wrap;
  word-break: break-word;
}

.page-header__actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.upload-input {
  display: none;
}

.upload-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 36px;
  padding: 0 18px;
  border-radius: var(--nbhx-radius-sm);
  border: 1px solid var(--nbhx-color-accent);
  background: var(--nbhx-color-accent);
  color: #ffffff;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: filter 0.2s ease, opacity 0.2s ease;

  &:hover:not(:disabled) {
    filter: brightness(0.92);
  }

  &:focus-visible {
    outline: none;
    box-shadow: var(--nbhx-focus-ring);
  }

  &:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  &--excel {
    background: #2f6b4f;
    border-color: #2f6b4f;
  }
}

.upload-progress {
  margin-bottom: 16px;
  padding: 14px 18px;
  border-radius: var(--nbhx-radius-md);
  border: 1px solid var(--nbhx-color-border-subtle);
  background: #ffffff;
  box-shadow: var(--nbhx-shadow-card);
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex-shrink: 0;
}

.upload-progress__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.upload-progress__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--nbhx-color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upload-progress__status {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--nbhx-color-text-secondary);
}

.upload-progress__bar {
  height: 8px;
  border-radius: var(--nbhx-radius-pill);
  background: var(--nbhx-color-surface-alt);
  overflow: hidden;
}

.upload-progress__fill {
  height: 100%;
  border-radius: var(--nbhx-radius-pill);
  background: var(--nbhx-color-accent);
  transition: width 0.4s ease;

  &--indeterminate {
    width: 40%;
    animation: upload-slide 1.2s ease-in-out infinite;
  }
}

@keyframes upload-slide {
  0% {
    margin-left: -40%;
  }
  100% {
    margin-left: 100%;
  }
}

.upload-progress__message {
  font-size: 12px;
  color: var(--nbhx-color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.records__restricted {
  width: 100%;
  max-width: 960px;
  align-self: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 64px 32px;
  text-align: center;
}

.records__restricted-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--nbhx-color-text-primary);
}

.records__restricted-text {
  margin: 0;
  max-width: 560px;
  font-size: 14px;
  line-height: 1.8;
  color: var(--nbhx-color-text-secondary);
}

.conflict-mask {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(20, 20, 19, 0.45);
}

.conflict-dialog {
  width: min(480px, calc(100vw - 48px));
  border-radius: var(--nbhx-radius-lg);
  background: #ffffff;
  box-shadow: var(--nbhx-shadow-card);
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.conflict-dialog__title {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  color: var(--nbhx-color-text-primary);
}

.conflict-dialog__text {
  margin: 0;
  font-size: 13px;
  color: var(--nbhx-color-text-secondary);
}

.conflict-dialog__list {
  margin: 0;
  padding: 8px 12px 8px 28px;
  border-radius: var(--nbhx-radius-sm);
  background: var(--nbhx-color-surface-alt);
  font-size: 13px;
  color: var(--nbhx-color-text-primary);
  max-height: 120px;
  overflow: auto;
}

.conflict-dialog__actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 6px;
}

.conflict-btn {
  min-height: 38px;
  border-radius: var(--nbhx-radius-sm);
  border: 1px solid var(--nbhx-color-border-subtle);
  background: var(--nbhx-color-surface);
  color: var(--nbhx-color-text-primary);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease;

  &:hover {
    background: var(--nbhx-color-surface-alt);
  }

  &:focus-visible {
    outline: none;
    box-shadow: var(--nbhx-focus-ring);
  }

  &--replace {
    border-color: var(--nbhx-color-accent);
    background: var(--nbhx-color-accent);
    color: #ffffff;

    &:hover {
      background: var(--nbhx-color-accent);
      filter: brightness(0.92);
    }
  }

  &--cancel {
    color: var(--nbhx-color-text-secondary);
  }
}

@media (max-width: 980px) {
  .page {
    padding: 24px 20px 18px;
  }

  .page__content {
    padding: 20px;
  }

  .file-group {
    padding: 10px;
  }

  .record-card__meta {
    flex-wrap: wrap;
    gap: 8px;
  }
}
</style>
