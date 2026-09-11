import { createApp, h } from 'vue'
import Toast from './Toast.vue'

export interface ToastOptions {
  message: string
  type?: 'success' | 'error' | 'warning' | 'info'
  duration?: number
}

class ToastManager {
  private container: HTMLElement | null = null

  private getContainer(): HTMLElement {
    if (!this.container) {
      this.container = document.createElement('div')
      this.container.id = 'toast-container'
      document.body.appendChild(this.container)
    }
    return this.container
  }

  show(options: ToastOptions) {
    const container = this.getContainer()
    const div = document.createElement('div')
    container.appendChild(div)

    const app = createApp({
      render() {
        return h(Toast, {
          ...options,
          show: true,
          onClose: () => {
            app.unmount()
            container.removeChild(div)
          }
        })
      }
    })

    app.mount(div)
  }

  success(message: string, duration = 3000) {
    this.show({ message, type: 'success', duration })
  }

  error(message: string, duration = 3000) {
    this.show({ message, type: 'error', duration })
  }

  warning(message: string, duration = 3000) {
    this.show({ message, type: 'warning', duration })
  }

  info(message: string, duration = 3000) {
    this.show({ message, type: 'info', duration })
  }
}

export const toast = new ToastManager()

export const useToast = () => {
  return {
    toast,
    showSuccess: (message: string, duration?: number) => toast.success(message, duration),
    showError: (message: string, duration?: number) => toast.error(message, duration),
    showWarning: (message: string, duration?: number) => toast.warning(message, duration),
    showInfo: (message: string, duration?: number) => toast.info(message, duration),
  }
}
