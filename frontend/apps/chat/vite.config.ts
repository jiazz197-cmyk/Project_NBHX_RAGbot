import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = fileURLToPath(new URL('.', import.meta.url))
const root = resolve(__dirname, '../..')

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')

  if (!env.VITE_PORT) throw new Error('VITE_PORT is required in .env file')
  if (!env.VITE_BACKEND_TARGET) throw new Error('VITE_BACKEND_TARGET is required in .env file')
  if (!env.VITE_DIFY_TARGET) throw new Error('VITE_DIFY_TARGET is required in .env file')
  if (!env.VITE_API_BASE_URL) throw new Error('VITE_API_BASE_URL is required in .env file')
  if (!env.VITE_DIFY_API_PREFIX) throw new Error('VITE_DIFY_API_PREFIX is required in .env file')
  const chatProxyApiKey = env.CHAT_PROXY_API_KEY || env.CHAT_API_KEY || env.VITE_CHAT_API_KEY
  if (!chatProxyApiKey) {
    throw new Error('CHAT_PROXY_API_KEY (or CHAT_API_KEY) is required in .env file')
  }

  const port = Number(env.VITE_PORT)
  if (isNaN(port) || port <= 0) throw new Error('VITE_PORT must be a valid positive number')

  const apiBase = env.VITE_API_BASE_URL
  const difyApiPrefix = env.VITE_DIFY_API_PREFIX

  const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

  const makeProxy = (target: string, injectedApiKey?: string) => ({
    target,
    changeOrigin: true,
    secure: false,
    ws: true,
    configure: (proxy: any) => {
      if (injectedApiKey) {
        proxy.on('proxyReq', (proxyReq: any) => {
          proxyReq.setHeader('Authorization', `Bearer ${injectedApiKey}`)
        })
      }
      proxy.on('error', (err: Error, _req: any, res: any) => {
        if (res?.writeHead) {
          res.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' })
          res.end(
            JSON.stringify({
              code: 502,
              message: `Proxy target unavailable: ${target}`,
              data: err.message,
            })
          )
        }
      })
    },
  })

  return {
    plugins: [vue()],

    resolve: {
      alias: {
        '@': resolve(__dirname, './src'),
        '@yamato/components': resolve(root, './packages/components'),
      },
    },

    server: {
      port,
      host: true,

      proxy: {
        [`${apiBase}/auth`]: makeProxy(env.VITE_BACKEND_TARGET),
        [`${apiBase}/chat-summary`]: makeProxy(env.VITE_BACKEND_TARGET),
        [`${apiBase}/closing-form`]: makeProxy(env.VITE_BACKEND_TARGET),
        [`${apiBase}/docs`]: makeProxy(env.VITE_BACKEND_TARGET), // OpenAPI + legacy /docs/* doc-task routes
        [`${apiBase}/document-tasks`]: makeProxy(env.VITE_BACKEND_TARGET),
        [`${apiBase}/ocr`]: makeProxy(env.VITE_BACKEND_TARGET),
        [`${apiBase}/image2url`]: makeProxy(env.VITE_BACKEND_TARGET),
        [`${apiBase}/pdf2image`]: makeProxy(env.VITE_BACKEND_TARGET),
        [`${apiBase}/quotation`]: makeProxy(env.VITE_BACKEND_TARGET),
        [`${apiBase}/context-compression`]: makeProxy(env.VITE_BACKEND_TARGET),
        [apiBase]: {
          ...makeProxy(env.VITE_DIFY_TARGET, chatProxyApiKey),
          rewrite: (path) => path.replace(new RegExp(`^${escapeRegExp(apiBase)}`), difyApiPrefix),
        },
      },
    },
  }
})