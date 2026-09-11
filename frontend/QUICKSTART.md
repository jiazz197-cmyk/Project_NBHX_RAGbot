# 快速启动指南

## 前置要求

- Node.js 18+ 
- pnpm 8+

## 安装步骤

1. **安装依赖**
   ```bash
   pnpm install
   ```

2. **配置环境变量**
   
   在 `apps/chat/` 目录下创建 `.env` 文件（或复制 `env.example`）：
   ```bash
   cp apps/chat/env.example apps/chat/.env
   ```
   
   默认端口已设置为 8888，如需修改请编辑 `.env` 文件。

3. **启动开发服务器**
   ```bash
   pnpm dev
   ```

4. **访问应用**
   
   打开浏览器访问：`http://localhost:8888`

## 项目特性

✅ **Monorepo 架构** - 使用 pnpm workspace 管理多应用和共享包  
✅ **Vue 3 + TypeScript** - 现代化的前端技术栈  
✅ **公共组件库** - 可复用的组件位于 `packages/components`  
✅ **Gemini 风格布局** - 参考 Google Gemini 的界面设计  
✅ **浅蓝色主题** - 清新的视觉风格  
✅ **响应式设计** - 适配不同屏幕尺寸  
✅ **环境变量配置** - 遵循工程规范，无硬编码

## 开发说明

- 公共组件位于 `packages/components`，可在其他应用中复用
- 所有配置通过环境变量管理，禁止硬编码
- 使用 ESLint 进行代码规范检查
- 使用 TypeScript 确保类型安全

## 下一步

- 连接真实的 AI API
- 实现对话历史持久化
- 添加更多功能页面
