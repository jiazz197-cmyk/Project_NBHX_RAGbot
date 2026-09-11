import { onUnmounted } from 'vue'

const ACTIVITY_EVENTS = [
  'mousemove',
  'mousedown',
  'keydown',
  'scroll',
  'touchstart',
  'click',
] as const

/** 无操作超过 timeoutMs 触发 onIdle；卸载时自动 stop。 */
export function useIdleTimer(timeoutMs: number, onIdle: () => void) {
  let timer: ReturnType<typeof setTimeout> | null = null

  const reset = () => {
    if (timer !== null) clearTimeout(timer)
    timer = setTimeout(onIdle, timeoutMs)
  }

  const start = () => {
    reset()
    ACTIVITY_EVENTS.forEach((e) =>
      document.addEventListener(e, reset, { passive: true })
    )
  }

  const stop = () => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
    ACTIVITY_EVENTS.forEach((e) => document.removeEventListener(e, reset))
  }

  onUnmounted(stop)

  return { start, stop }
}
