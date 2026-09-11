import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { readUserRole, readUserPermissions } from '../services/auth'
import { getAuthTokenFromStorage } from '../services/token_storage'

const PUBLIC_PATHS = ['/login', '/register']

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    redirect: '/chat',
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('@/pages/LoginPage.vue'),
    meta: { title: '登录' },
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('@/pages/RegisterPage.vue'),
    meta: { title: '注册' },
  },
  {
    path: '/chat',
    name: 'chat',
    component: () => import('@/pages/ChatPage.vue'),
    meta: { title: 'AI聊天' },
  },
  {
    path: '/files',
    name: 'files',
    component: () => import('@/pages/FileManagerPage.vue'),
    meta: { title: '报价生成', requiresPermission: 'view_quotation' },
  },
  {
    path: '/closing-form',
    name: 'closing-form',
    component: () => import('@/pages/PolicyGeneratePage.vue'),
    meta: { title: '营业订单信息', requiresPermission: 'view_closing_form' },
  },
  {
    path: '/users',
    name: 'users',
    component: () => import('@/pages/UserManagePage.vue'),
    meta: { title: '用户管理', requiresSuperuser: true },
  },
  {
    path: '/collection2',
    name: 'collection2',
    component: () => import('@/pages/Collection2ManagePage.vue'),
    meta: { title: '知识库管理', requiresAdminOrSuperuser: true },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to, _from, next) => {
  const token = getAuthTokenFromStorage()

  const isPublic = PUBLIC_PATHS.includes(to.path)

  if (!token && !isPublic) {
    next('/login')
    return
  }

  if (token && isPublic) {
    next('/chat')
    return
  }

  if (to.meta.requiresSuperuser && readUserRole() !== 'superuser') {
    next('/chat')
    return
  }

  if (to.meta.requiresAdminOrSuperuser) {
    const role = readUserRole()
    if (role !== 'admin' && role !== 'superuser') {
      next('/chat')
      return
    }
  }

  if (to.meta.requiresPermission) {
    const role = readUserRole()
    if (role === 'admin' || role === 'superuser') {
      next()
      return
    }
    const perms = readUserPermissions()
    const required = to.meta.requiresPermission as string
    if (!perms.includes(required)) {
      next('/chat')
      return
    }
  }

  next()
})

router.afterEach((to) => {
  const title = typeof to.meta.title === 'string' ? to.meta.title : ''
  document.title = title ? `${title} - yamato` : 'yamato'
})

export default router

