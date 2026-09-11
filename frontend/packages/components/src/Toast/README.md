# Toast 组件使用说明

## 组件功能

Toast 是一个任务完成提示组件，用于在页面顶部居中显示提示信息，并在一定时间后自动消失。

## 使用方法

### 方法 1: 使用 useToast composable（推荐）

```vue
<script setup lang="ts">
import { useToast } from '@/packages/components'

const { showSuccess, showError, showWarning, showInfo } = useToast()

// 显示成功提示
const handleTaskComplete = () => {
  showSuccess('任务已完成！')
}

// 显示错误提示
const handleError = () => {
  showError('操作失败，请重试')
}

// 显示警告提示
const handleWarning = () => {
  showWarning('请注意检查输入内容')
}

// 显示信息提示
const handleInfo = () => {
  showInfo('正在处理您的请求...')
}

// 自定义持续时间（毫秒）
const handleCustomDuration = () => {
  showSuccess('此消息将显示 5 秒', 5000)
}
</script>

<template>
  <div>
    <button @click="handleTaskComplete">完成任务</button>
    <button @click="handleError">显示错误</button>
    <button @click="handleWarning">显示警告</button>
    <button @click="handleInfo">显示信息</button>
  </div>
</template>
```

### 方法 2: 使用 toast 实例

```typescript
import { toast } from '@/packages/components'

// 显示成功提示
toast.success('任务已完成！')

// 显示错误提示
toast.error('操作失败')

// 显示警告提示
toast.warning('警告信息')

// 显示信息提示
toast.info('提示信息')

// 自定义配置
toast.show({
  message: '自定义消息',
  type: 'success',
  duration: 5000 // 持续时间（毫秒）
})
```

## API

### useToast()

返回一个包含以下方法的对象：

- `showSuccess(message: string, duration?: number)` - 显示成功提示
- `showError(message: string, duration?: number)` - 显示错误提示
- `showWarning(message: string, duration?: number)` - 显示警告提示
- `showInfo(message: string, duration?: number)` - 显示信息提示
- `toast` - toast 管理器实例

### toast 实例方法

- `toast.success(message, duration)` - 显示成功提示
- `toast.error(message, duration)` - 显示错误提示
- `toast.warning(message, duration)` - 显示警告提示
- `toast.info(message, duration)` - 显示信息提示
- `toast.show(options)` - 显示自定义提示

### ToastOptions

```typescript
interface ToastOptions {
  message: string          // 提示消息
  type?: 'success' | 'error' | 'warning' | 'info'  // 提示类型，默认 'success'
  duration?: number        // 显示持续时间（毫秒），默认 3000，设置为 0 则不自动关闭
}
```

## 特性

- ✅ 顶部居中显示
- ✅ 自动消失（可配置持续时间）
- ✅ 多种提示类型（成功、错误、警告、信息）
- ✅ 流畅的进入/退出动画
- ✅ 响应式设计
- ✅ TypeScript 支持
- ✅ 可编程调用
- ✅ 支持同时显示多个提示

## 样式

组件提供了 4 种预设主题色：
- 成功：绿色 (#52c41a)
- 错误：红色 (#ff4d4f)
- 警告：橙色 (#faad14)
- 信息：蓝色 (#1890ff)

如需自定义样式，可以通过修改 `Toast.vue` 中的 SCSS 变量。
