import { apiRequest, apiRequestFormData } from './api'

export interface ClosingFormRecord {
  id: string
  text: string
  file_name?: string | null
  upload_time: string | null
  uploader: string
  status: string
  image_url_1?: string | null
  image_url_2?: string | null
}

export interface ClosingFormListResponse {
  records: ClosingFormRecord[]
}

export const submitClosingForm = async (payload: Record<string, unknown>): Promise<void> => {
  await apiRequest('/closing-form/submit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export const listClosingFormRecords = async (): Promise<ClosingFormRecord[]> => {
  const data = await apiRequest<ClosingFormListResponse>('/closing-form/list')
  return data.records ?? []
}

export const approveClosingForm = async (formId: string): Promise<void> => {
  await apiRequest(`/closing-form/approve/${formId}`, { method: 'PATCH' })
}

export const rejectClosingForm = async (formId: string): Promise<void> => {
  await apiRequest(`/closing-form/reject/${formId}`, { method: 'PATCH' })
}

export const deleteApprovedClosingForm = async (formId: string): Promise<void> => {
  await apiRequest(`/closing-form/approved/${formId}`, { method: 'DELETE' })
}

export const deleteRejectedClosingForm = async (formId: string): Promise<void> => {
  await apiRequest(`/closing-form/rejected/${formId}`, { method: 'DELETE' })
}

export const reviseClosingForm = async (formId: string, payload: Record<string, unknown>): Promise<void> => {
  await apiRequest(`/closing-form/revise/${formId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export const listCollection2Records = async (): Promise<ClosingFormRecord[]> => {
  const data = await apiRequest<ClosingFormListResponse>('/closing-form/collection2/list')
  return data.records ?? []
}

export const deleteCollection2Record = async (recordId: string): Promise<void> => {
  await apiRequest(`/closing-form/collection2/${recordId}`, { method: 'DELETE' })
}

interface ImageUploadResult { success: boolean; object_name: string }

export const uploadClosingFormImage = async (file: File): Promise<ImageUploadResult> => {
  const fd = new FormData()
  fd.append('image', file)
  return apiRequestFormData<ImageUploadResult>('/closing-form/image/upload', fd)
}

export const deleteClosingFormImage = async (objectName: string): Promise<void> => {
  await apiRequest(`/closing-form/image?object_name=${encodeURIComponent(objectName)}`, {
    method: 'DELETE',
  })
}
