import { config } from '../config'

/** Token 放 sessionStorage；若仅有 localStorage 旧值则迁移一次。 */
export const getAuthTokenFromStorage = (): string | null => {
  try {
    const sessionToken = sessionStorage.getItem(config.authTokenStorageKey)
    if (sessionToken) {
      return sessionToken
    }
    const legacyToken = localStorage.getItem(config.authTokenStorageKey)
    if (legacyToken) {
      sessionStorage.setItem(config.authTokenStorageKey, legacyToken)
      localStorage.removeItem(config.authTokenStorageKey)
      return legacyToken
    }
  } catch {
    return null
  }
  return null
}

export const setAuthTokenToStorage = (token: string): void => {
  try {
    sessionStorage.setItem(config.authTokenStorageKey, token)
    localStorage.removeItem(config.authTokenStorageKey)
  } catch {
    // 忽略
  }
}

export const clearAuthTokenFromStorage = (): void => {
  try {
    sessionStorage.removeItem(config.authTokenStorageKey)
    localStorage.removeItem(config.authTokenStorageKey)
  } catch {
    // 忽略
  }
}
