/** 自 Vite 环境变量构建，缺项在 getConfig 里抛错。 */

interface AppConfig {
  port: number
  apiBaseUrl: string
  wsBaseUrl?: string
  env: string
  userName?: string
  userAvatarUrl?: string
  loginEndpoint: string
  meEndpoint: string
  authTokenStorageKey: string
  settingsStorageKey: string
}

// 报错要能自导航：直接告诉新人模板在哪、复制到哪（issue #12）
const missingEnv = (name: string) =>
  `${name} is required in .env — copy frontend/apps/chat/env.example to frontend/apps/chat/.env and set it`

const getConfig = (): AppConfig => {
  const port = import.meta.env.VITE_PORT
  if (!port) {
    throw new Error(missingEnv('VITE_PORT'))
  }

  const portNumber = Number(port)
  if (isNaN(portNumber) || portNumber <= 0) {
    throw new Error('VITE_PORT must be a valid positive number')
  }

  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL
  if (!apiBaseUrl) {
    throw new Error(missingEnv('VITE_API_BASE_URL'))
  }

  const loginEndpoint = import.meta.env.VITE_LOGIN_ENDPOINT
  if (!loginEndpoint) {
    throw new Error(missingEnv('VITE_LOGIN_ENDPOINT'))
  }

  const meEndpoint = import.meta.env.VITE_ME_ENDPOINT
  if (!meEndpoint) {
    throw new Error(missingEnv('VITE_ME_ENDPOINT'))
  }

  const authTokenStorageKey = import.meta.env.VITE_AUTH_TOKEN_KEY
  if (!authTokenStorageKey) {
    throw new Error(missingEnv('VITE_AUTH_TOKEN_KEY'))
  }

  const settingsStorageKey = import.meta.env.VITE_SETTINGS_STORAGE_KEY
  if (!settingsStorageKey) {
    throw new Error(missingEnv('VITE_SETTINGS_STORAGE_KEY'))
  }

  return {
    port: portNumber,
    apiBaseUrl,
    wsBaseUrl: import.meta.env.VITE_WS_BASE_URL || undefined,
    env: import.meta.env.VITE_ENV || import.meta.env.MODE,
    userName: import.meta.env.VITE_USER_NAME,
    userAvatarUrl: import.meta.env.VITE_USER_AVATAR_URL,
    loginEndpoint,
    meEndpoint,
    authTokenStorageKey,
    settingsStorageKey,
  }
}

export const config = getConfig()
