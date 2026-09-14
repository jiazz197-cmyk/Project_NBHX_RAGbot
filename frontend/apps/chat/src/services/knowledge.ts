import { apiRequest } from './api'

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

export const listKnowledgeRecords = async (): Promise<KnowledgeRecord[]> => {
  const data = await apiRequest<KnowledgeRecordListResponse>('/knowledge/records')
  return data.records ?? []
}

export const deleteKnowledgeRecord = async (recordId: string): Promise<void> => {
  await apiRequest(`/knowledge/records/${recordId}`, { method: 'DELETE' })
}
