import { config } from '../config'

/**
 * 类型安全的 localStorage 读写助手。所有 JSON 解析/序列化均带 try/catch，
 * 解析失败时返回 fallback，避免抛出阻断业务流程。
 */

export function readStored<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return fallback
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

export function writeStored(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // 忽略写入失败（隐私模式 / 配额超限）
  }
}

/** 读取 key 下已有的 JSON 对象，合并 patch 后写回。 */
export function patchStored(key: string, patch: Record<string, unknown>): void {
  const existing = readStored<Record<string, unknown>>(key, {})
  writeStored(key, { ...existing, ...patch })
}

// --- 设置缓存（settingsStorageKey）下的 role / username / permissions 读写 ---

const SETTINGS_KEY = config.settingsStorageKey

export const saveUserRole = (role: string): void => {
  patchStored(SETTINGS_KEY, { role })
}

export const readUserRole = (): string => {
  const parsed = readStored<{ role?: unknown }>(SETTINGS_KEY, {})
  return String(parsed.role ?? '').trim()
}

export const readUsername = (): string => {
  const parsed = readStored<{ username?: unknown; user?: unknown }>(SETTINGS_KEY, {})
  return String(parsed.username ?? parsed.user ?? '').trim()
}

export const saveUserPermissions = (permissions: string[]): void => {
  patchStored(SETTINGS_KEY, { permissions })
}

export const readUserPermissions = (): string[] => {
  const parsed = readStored<{ permissions?: unknown }>(SETTINGS_KEY, {})
  return Array.isArray(parsed.permissions) ? (parsed.permissions as string[]) : []
}
