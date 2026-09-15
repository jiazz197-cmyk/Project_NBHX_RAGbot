import { apiRequest, apiRequestFormData } from './api'

export interface KnowledgeRecord {
  id: string
  text: string
  file_name?: string | null
  upload_time: string | null
  uploader: string
  status: string
}

export interface KnowledgeRecordListResponse {
  success: boolean
  total: number
  records: KnowledgeRecord[]
}

/** 上传目标集合：documents = 文档知识，excel-db = Excel 数据库 */
export type KnowledgeUploadCollection = 'documents' | 'excel-db'

/** 同名冲突处理策略：replace 替换旧内容 / append 追加保留 */
export type KnowledgeConflictStrategy = 'replace' | 'append'

export interface KnowledgeUploadResponse {
  task_id: string
  status: string
  message: string
  files_count: number
  collection: string
}

export interface KnowledgeTaskStatus {
  task_id: string
  status: string
  progress: number
  message: string
  created_at: string
}

export const listKnowledgeRecords = async (): Promise<KnowledgeRecord[]> => {
  const data = await apiRequest<KnowledgeRecordListResponse>('/knowledge/records')
  return data.records ?? []
}

export const deleteKnowledgeRecord = async (recordId: string): Promise<void> => {
  await apiRequest(`/knowledge/records/${recordId}`, { method: 'DELETE' })
}

export const uploadKnowledgeFiles = async (
  collection: KnowledgeUploadCollection,
  files: File[],
  onConflict?: KnowledgeConflictStrategy
): Promise<KnowledgeUploadResponse> => {
  const formData = new FormData()
  for (const file of files) {
    formData.append('files', file)
  }
  const query = onConflict ? `?on_conflict=${onConflict}` : ''
  return apiRequestFormData<KnowledgeUploadResponse>(
    `/knowledge/${collection}${query}`,
    formData
  )
}

export const fetchKnowledgeTaskStatus = async (
  taskId: string
): Promise<KnowledgeTaskStatus> => {
  return apiRequest<KnowledgeTaskStatus>(
    `/document-tasks/status/${encodeURIComponent(taskId)}`
  )
}
