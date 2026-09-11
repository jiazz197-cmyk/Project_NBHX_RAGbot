/// <reference types="vite/client" />
declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}

interface ImportMetaEnv {
  readonly VITE_PORT: string
  readonly VITE_API_BASE_URL: string
  readonly VITE_DIFY_TARGET: string
  readonly VITE_DIFY_API_PREFIX: string
  readonly VITE_BACKEND_TARGET: string
  readonly VITE_ENV?: string
  readonly VITE_LOGIN_ENDPOINT: string
  readonly VITE_ME_ENDPOINT: string
  readonly VITE_AUTH_TOKEN_KEY: string
  readonly VITE_SETTINGS_STORAGE_KEY: string
  readonly VITE_USER_NAME?: string
  readonly VITE_USER_AVATAR_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
