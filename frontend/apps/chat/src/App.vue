<template>
  <div v-if="isShellFreePage" class="login-shell">
    <RouterView />
  </div>
  <div v-else id="app" class="app-shell">
    <Sidebar
      title="yamato"
      logo-url="/yamato_icon.png"
      :user-name="userName"
      :user-avatar-url="userAvatarUrl"
      user-desc="在线"
      :collapsible="false"
      :width="200"
    >
      <nav class="sidebar-nav" aria-label="主导航">
        <RouterLink class="sidebar-nav__item" active-class="is-active" to="/chat">
          AI聊天
        </RouterLink>
        <RouterLink v-if="isAdminOrSuperuser" class="sidebar-nav__item" active-class="is-active" to="/knowledge">
          知识库管理
        </RouterLink>
        <RouterLink v-if="isSuperuser" class="sidebar-nav__item" active-class="is-active" to="/users">
          用户管理
        </RouterLink>
      </nav>

      <div id="sidebar-extra-slot" class="sidebar-extra"></div>

      <template #user-actions>
        <button class="logout-btn" type="button" @click="openLogoutDialog">
          退出
        </button>
      </template>
    </Sidebar>

    <main class="app-main">
      <RouterView />
    </main>

    <ConfirmDialog
      v-model="showLogoutDialog"
      title="退出登录"
      message="确定要退出当前账号吗？"
      type="warning"
      confirm-text="退出"
      cancel-text="取消"
      @confirm="doLogout"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { Sidebar, ConfirmDialog } from '@yamato/components'
import { config } from './config'
import { useIdleTimer } from './composables/useIdleTimer'
import { clearAuthTokenFromStorage } from './services/token_storage'
import { readStored } from './services/storage'

const sidebarUserId = ref('')
const sidebarUserName = ref('')
const userRole = ref('')

const readSidebarState = () => {
  const parsed = readStored<{
    userId?: unknown
    user?: unknown
    userName?: unknown
    username?: unknown
    role?: unknown
  } | null>(config.settingsStorageKey, null)

  if (!parsed) {
    sidebarUserId.value = ''
    sidebarUserName.value = ''
    userRole.value = ''
    return
  }

  sidebarUserId.value = String(parsed.userId ?? '').trim()
  sidebarUserName.value = String(parsed.userName ?? parsed.user ?? parsed.username ?? '').trim()
  userRole.value = String(parsed.role ?? '').trim()
}

const userName = computed(() => sidebarUserName.value || sidebarUserId.value || config.userName || '')
const userAvatarUrl = computed(() => config.userAvatarUrl || '')
const isSuperuser = computed(() => userRole.value === 'superuser')
const isAdminOrSuperuser = computed(() => userRole.value === 'admin' || userRole.value === 'superuser')

const route = useRoute()
const router = useRouter()

const showLogoutDialog = ref(false)

const isShellFreePage = computed(() => route.name === 'login' || route.name === 'register')

readSidebarState()

watch(
  () => route.fullPath,
  () => {
    readSidebarState()
  }
)

const openLogoutDialog = () => {
  showLogoutDialog.value = true
}

const doLogout = async () => {
  clearAuthTokenFromStorage()
  try {
    localStorage.removeItem(config.settingsStorageKey)
  } catch {
    // 忽略
  }
  await router.push('/login')
}

const IDLE_TIMEOUT_MS = 6 * 60 * 60 * 1000

const { start: startIdleTimer, stop: stopIdleTimer } = useIdleTimer(IDLE_TIMEOUT_MS, () => {
  void doLogout()
})

if (!isShellFreePage.value) {
  startIdleTimer()
}

watch(isShellFreePage, (onShellFreePage) => {
  if (onShellFreePage) {
    stopIdleTimer()
  } else {
    startIdleTimer()
  }
})
</script>

<style scoped lang="scss">
.app-shell {
  width: 100%;
  height: 100vh;
  overflow: hidden;
  background: var(--yamato-color-bg-light);
}

.login-shell {
  width: 100%;
  height: 100vh;
  overflow: hidden;
  background: var(--yamato-color-bg-light);
}

.app-main {
  height: 100%;
  padding-left: 224px;
  overflow: hidden;
  background: var(--yamato-color-bg-light);
}

.sidebar-extra {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 0 8px;
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
}

.sidebar-nav__item {
  display: flex;
  align-items: center;
  height: 38px;
  padding: 0 14px;
  border-radius: var(--yamato-radius-sm);
  color: var(--yamato-color-text-secondary);
  text-decoration: none;
  font-size: 14px;
  font-weight: 400;
  letter-spacing: 0;
  transition: background 0.2s ease, color 0.2s ease;

  &:hover {
    background: rgba(20, 20, 19, 0.05);
  }

  &.is-active {
    background: var(--yamato-color-accent-soft);
    color: var(--yamato-color-accent);
    font-weight: 600;
  }
}

.logout-btn {
  min-height: 28px;
  padding: 0 12px;
  border-radius: var(--yamato-radius-pill);
  border: 1px solid rgba(201, 100, 66, 0.32);
  background: rgba(201, 100, 66, 0.12);
  color: var(--yamato-color-accent);
  font-size: 12px;
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.2s ease, border-color 0.2s ease, transform 0.1s ease;

  &:hover {
    background: var(--yamato-color-accent-soft-strong);
    border-color: rgba(201, 100, 66, 0.44);
  }

  &:focus-visible {
    outline: none;
    box-shadow: var(--yamato-focus-ring);
  }

  &:active {
    transform: translateY(1px);
  }
}

@media (max-width: 1024px) {
  .app-main {
    padding-left: 216px;
  }
}
</style>
