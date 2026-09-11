<template>
  <div class="login-page">
    <div class="login-card" role="form" aria-label="登录">
      <h1 class="login-card__title">登录</h1>
      <p class="login-card__subtitle">请输入账号和密码以继续使用系统</p>

      <form class="login-form" @submit.prevent="handleSubmit">
        <label class="login-form__field">
          <span class="login-form__label">用户名</span>
          <Input
            v-model="username"
            class="login-form__input"
            placeholder="请输入用户名"
            autocomplete="username"
          />
        </label>

        <label class="login-form__field">
          <span class="login-form__label">密码</span>
          <Input
            v-model="password"
            class="login-form__input"
            type="password"
            placeholder="请输入密码"
            autocomplete="current-password"
          />
        </label>

        <button
          class="login-form__submit"
          type="submit"
          :disabled="submitting || !username.trim() || !password"
        >
          {{ submitting ? '登录中...' : '登录' }}
        </button>
      </form>

      <p class="login-card__footer">
        没有账号？
        <RouterLink class="login-card__link" to="/register">去注册</RouterLink>
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { Input, useToast } from '@yamato/components'
import { config } from '../config'
import { login, getMe, saveUserRole, saveUserPermissions } from '../services/auth'
import { readStored, patchStored } from '../services/storage'
import { setAuthTokenToStorage } from '../services/token_storage'

const router = useRouter()
const { showSuccess, showError } = useToast()

const username = ref('')
const password = ref('')
const submitting = ref(false)

const handleSubmit = async () => {
  if (submitting.value) return

  submitting.value = true
  try {
    const result = await login({
      username: username.value.trim(),
      password: password.value,
    })

    setAuthTokenToStorage(result.access_token)

    try {
      const me = await getMe()
      const existing = readStored<{ search?: unknown }>(config.settingsStorageKey, {})
      const loginUserId = String(me.id || '').trim()
      const loginUser = String(me.username || me.id || '').trim()
      const loginUserName = String((me as any).name || me.username || me.id || '').trim()
      patchStored(config.settingsStorageKey, {
        userId: loginUser,
        userUUID: loginUserId,
        user: loginUser,
        userName: loginUserName,
        username: loginUser,
        role: me.role || '',
        search: existing.search || '联网搜索',
      })
      saveUserRole(me.role || '')
      saveUserPermissions(me.permissions || [])
    } catch {
      // /me 失败不阻塞登录
    }

    showSuccess('登录成功')
    await router.push('/chat')
  } catch (error: any) {
    const message = error?.message || '登录失败，请检查账号密码'
    showError(message)
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped lang="scss">
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100vh;
  background: var(--yamato-color-bg-light);
  padding: 24px;
}

.login-card {
  width: min(420px, 100%);
  padding: 34px 32px 30px;
  border-radius: var(--yamato-radius-lg);
  background: #ffffff;
  box-shadow: var(--yamato-shadow-card);
  box-sizing: border-box;
}

.login-card__title {
  margin: 0 0 8px;
  font-family: var(--yamato-font-display);
  font-size: 34px;
  font-weight: 500;
  line-height: 1.2;
  letter-spacing: 0;
  color: var(--yamato-color-text-primary);
}

.login-card__subtitle {
  margin: 0 0 22px;
  font-size: 15px;
  line-height: 1.6;
  letter-spacing: normal;
  color: var(--yamato-color-text-secondary);
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.login-form__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.login-form__label {
  font-size: 14px;
  font-weight: 500;
  letter-spacing: normal;
  color: var(--yamato-color-text-primary);
}

.login-form__input {
  width: 100%;
}

.login-card__footer {
  margin: 18px 0 0;
  font-size: 14px;
  color: var(--yamato-color-text-secondary);
  text-align: center;
}

.login-card__link {
  color: var(--yamato-color-link);
  text-decoration: none;
  font-weight: 400;

  &:hover {
    text-decoration: underline;
  }
}

.login-form__submit {
  margin-top: 8px;
  width: 100%;
  min-height: 42px;
  border: none;
  border-radius: var(--yamato-radius-md);
  background: var(--yamato-color-accent);
  color: #ffffff;
  font-size: 16px;
  line-height: 1;
  letter-spacing: normal;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.2s ease, box-shadow 0.2s ease, transform 0.1s ease, opacity 0.2s ease;

  &:hover:not(:disabled) {
    background: var(--yamato-color-accent-hover);
  }

  &:active:not(:disabled) {
    transform: translateY(1px);
  }

  &:focus-visible {
    outline: none;
    box-shadow: var(--yamato-focus-ring);
  }

  &:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
}

@media (max-width: 640px) {
  .login-card {
    padding: 28px 22px 24px;
  }

  .login-card__title {
    font-size: 28px;
  }
}
</style>

