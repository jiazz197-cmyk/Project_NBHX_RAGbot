import { config } from '../config'
import { apiRequest, handleApiError } from './api'

const AUTH_BASE = '/auth'

export interface LoginRequest {
  username: string
  password: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
}

export interface MeResponse {
  id: string
  username: string
  name: string | null
  email: string | null
  phone: string | null
  department: string | null
  avatar: string | null
  is_active: boolean
  role: string
  roles: { id: number; name: string }[]
  permissions: string[]
}

export interface RegisterRequest {
  username: string
  email: string
  password: string
  name?: string
}

export interface UserResponse {
  id: string
  username: string
  name: string | null
  email: string
  phone: string | null
  department: string | null
  avatar: string | null
  is_active: boolean
  role: string
  roles: { id: number; name: string }[]
  permissions: string[]
}

export interface UpdateRoleRequest {
  role: 'admin' | 'user'
}

export interface UserPagePermissions {
  view_quotation: boolean
}

export const login = async (payload: LoginRequest): Promise<LoginResponse> => {
  const response = await fetch(`${config.apiBaseUrl}${config.loginEndpoint}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    await handleApiError(response)
  }

  return response.json()
}

export const getMe = (): Promise<MeResponse> => {
  return apiRequest<MeResponse>(config.meEndpoint)
}

export const register = async (payload: RegisterRequest): Promise<UserResponse> => {
  const response = await fetch(`${config.apiBaseUrl}${AUTH_BASE}/register`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    await handleApiError(response)
  }

  return response.json()
}

export const listUsers = (): Promise<UserResponse[]> => {
  return apiRequest<UserResponse[]>(`${AUTH_BASE}/users`)
}

export const deleteUser = (userId: string): Promise<void> => {
  return apiRequest<void>(`${AUTH_BASE}/users/${userId}`, {
    method: 'DELETE',
  })
}

export const updateUserRole = (userId: string, payload: UpdateRoleRequest): Promise<UserResponse> => {
  return apiRequest<UserResponse>(`${AUTH_BASE}/users/${userId}/role`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export const updateUserPagePermissions = (userId: string, payload: UserPagePermissions): Promise<UserResponse> => {
  return apiRequest<UserResponse>(`${AUTH_BASE}/users/${userId}/page-permissions`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export interface ResetPasswordRequest {
  password: string
}

export const resetUserPassword = (userId: string, payload: ResetPasswordRequest): Promise<void> => {
  return apiRequest<void>(`${AUTH_BASE}/users/${userId}/password-reset`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

// role / username / permissions 的 localStorage 读写统一由 storage.ts 提供，
// 此处仅做再导出以保持既有 `from '@/services/auth'` 引用不变。
export {
  saveUserRole,
  readUserRole,
  readUsername,
  saveUserPermissions,
  readUserPermissions,
} from './storage'
