export type QuotationTaskStatus =
  | 'queued'
  | 'running'
  | 'awaiting_approval'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface QuotationPdmItem {
  CHINANAME?: string
  PARTID?: string
  QUERY_INDEX?: number
  QUERY_KEYWORDS?: string[]
  QUERY_EXPANDED_KEYWORDS?: string[]
  [key: string]: unknown
}

export interface QuotationPdmResult {
  total?: number
  items?: QuotationPdmItem[]
  [key: string]: unknown
}

export interface QuotationApprovalData {
  pdm_result?: QuotationPdmResult
  keywords_payload?: Record<string, unknown>
  pdm_partids?: string[]
  temp_image_url?: string
}

export interface ExtraPartidEntry {
  partid: string
  type: string
}

export interface ApproveQuotationTaskResponse {
  success: boolean
  message: string
  task_id: string
  status: QuotationTaskStatus
  approved_count: number
}

export interface QuotationTaskItem {
  task_id: string
  status: QuotationTaskStatus
  progress: number
  message: string
  owner_id: string
  owner_username: string
  uploaded_file_name: string
  display_name: string
  uploaded_file_content_type: string
  uploaded_file_size: number
  created_at: string
  started_at?: string | null
  completed_at?: string | null
  approval_data?: QuotationApprovalData | null
  error?: string | null
}

export interface QuotationTaskListResponse {
  total: number
  items: QuotationTaskItem[]
}

export interface ListQuotationTasksParams {
  status?: string
  ownerUsername?: string
  limit?: number
  activeOnly?: boolean
  signal?: AbortSignal
}

export interface CreateQuotationTaskResponse {
  task_id: string
  status: QuotationTaskStatus
  message: string
  queue_position: number
}

export interface CancelQuotationTaskResponse {
  success: boolean
  message: string
  task_id: string
}

export interface DeleteQuotationTaskResponse {
  success: boolean
  message: string
  task_id: string
  cleanup: Record<string, unknown>
  task_record_removed: boolean
}
