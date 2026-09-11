/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_PORT: string
  readonly VITE_API_BASE_URL?: string
  readonly VITE_TASK_WS_BASE_URL?: string
  readonly VITE_TASK_WS_HOST_OVERRIDE?: string
  readonly VITE_TASK_WS_USE_WINDOW_HOST?: string
  readonly VITE_ENV?: string
  readonly VITE_USER_NAME?: string
  readonly VITE_USER_AVATAR_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

