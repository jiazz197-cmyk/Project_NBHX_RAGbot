import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = fileURLToPath(new URL('.', import.meta.url))
const root = resolve(__dirname, '../..')

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')

  // 报错要能自导航：直接告诉新人模板在哪、复制到哪（issue #12）
  const missingEnv = (name: string) =>
    `${name} is required in .env — copy frontend/apps/chat/env.example to frontend/apps/chat/.env and set it`

  if (!env.VITE_PORT) throw new Error(missingEnv('VITE_PORT'))
  if (!env.VITE_BACKEND_TARGET) throw new Error(missingEnv('VITE_BACKEND_TARGET'))
  if (!env.VITE_API_BASE_URL) throw new Error(missingEnv('VITE_API_BASE_URL'))

  const port = Number(env.VITE_PORT)
  if (isNaN(port) || port <= 0) throw new Error('VITE_PORT must be a valid positive number')

  const apiBase = env.VITE_API_BASE_URL
  // 聊天两接口（POST /chat-messages 与 /{task_id}/stop）分流到 ragchain 容器；
  // 优先读真实进程环境变量（docker compose / shell 注入），再退回 .env 文件。
  const chatOrchestratorTarget =
    process.env.VITE_CHAT_ORCHESTRATOR_TARGET ||
    env.VITE_CHAT_ORCHESTRATOR_TARGET ||
    env.VITE_BACKEND_TARGET

  const makeProxy = (target: string) => ({
    target,
    changeOrigin: true,
    secure: false,
    ws: true,
    configure: (proxy: any) => {
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
        '@nbhx/components': resolve(root, './packages/components'),
      },
    },

    server: {
      port,
      host: true,

      proxy: {
        [`${apiBase}/auth`]: makeProxy(env.VITE_BACKEND_TARGET),
        // 前缀匹配天然覆盖 /chat-messages 与 /{task_id}/stop
        [`${apiBase}/chat-messages`]: makeProxy(chatOrchestratorTarget),
        [`${apiBase}/chat-summary`]: makeProxy(env.VITE_BACKEND_TARGET),
        [`${apiBase}/conversations`]: makeProxy(env.VITE_BACKEND_TARGET),
        [`${apiBase}/messages`]: makeProxy(env.VITE_BACKEND_TARGET),
        [`${apiBase}/knowledge`]: makeProxy(env.VITE_BACKEND_TARGET),
        [`${apiBase}/docs`]: makeProxy(env.VITE_BACKEND_TARGET), // OpenAPI + legacy /docs/* doc-task routes
        [`${apiBase}/document-tasks`]: makeProxy(env.VITE_BACKEND_TARGET),
        [`${apiBase}/ocr`]: makeProxy(env.VITE_BACKEND_TARGET),
        [`${apiBase}/image2url`]: makeProxy(env.VITE_BACKEND_TARGET),
        [`${apiBase}/pdf2image`]: makeProxy(env.VITE_BACKEND_TARGET),
        [`${apiBase}/context-compression`]: makeProxy(env.VITE_BACKEND_TARGET),
      },
    },
  }
})
