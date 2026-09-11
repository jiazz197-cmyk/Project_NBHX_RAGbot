<template>
  <div class="register-page">
    <div class="register-card" role="form" aria-label="注册">
      <h1 class="register-card__title">创建账号</h1>
      <p class="register-card__subtitle">填写以下信息完成注册</p>

      <form class="register-form" @submit.prevent="handleSubmit">
        <label class="register-form__field">
          <span class="register-form__label">用户名 <em class="required">*</em></span>
          <Input
            v-model="username"
            class="register-form__input"
            placeholder="请输入用户名"
            autocomplete="username"
          />
        </label>

        <label class="register-form__field">
          <span class="register-form__label">邮箱 <em class="required">*</em></span>
          <Input
            v-model="email"
            class="register-form__input"
            type="email"
            placeholder="请输入邮箱地址"
            autocomplete="email"
          />
        </label>

        <label class="register-form__field">
          <span class="register-form__label">密码 <em class="required">*</em></span>
          <Input
            v-model="password"
            class="register-form__input"
            type="password"
            placeholder="请输入密码（至少 6 位）"
            autocomplete="new-password"
          />
        </label>

        <label class="register-form__field">
          <span class="register-form__label">姓名（选填）</span>
          <Input
            v-model="name"
            class="register-form__input"
            placeholder="请输入真实姓名"
            autocomplete="name"
          />
        </label>

        <button
          class="register-form__submit"
          type="submit"
          :disabled="submitting || !canSubmit"
        >
          {{ submitting ? '注册中...' : '注册' }}
        </button>
      </form>

      <p class="register-card__footer">
        已有账号？
        <RouterLink class="register-card__link" to="/login">去登录</RouterLink>
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { Input, useToast } from '@yamato/components'
import { register } from '../services/auth'

const router = useRouter()
const { showSuccess, showError } = useToast()

const username = ref('')
const email = ref('')
const password = ref('')
const name = ref('')
const submitting = ref(false)

const canSubmit = computed(
  () =>
    username.value.trim() !== '' &&
    email.value.trim() !== '' &&
    password.value.length >= 6
)

const handleSubmit = async () => {
  if (submitting.value || !canSubmit.value) return

  submitting.value = true
  try {
    await register({
      username: username.value.trim(),
      email: email.value.trim(),
      password: password.value,
      name: name.value.trim() || undefined,
    })
    showSuccess('注册成功，请登录')
    await router.push('/login')
  } catch (error: any) {
    const detail = error?.detail ?? error?.message
    showError(detail || '注册失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped lang="scss">
.register-page {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100vh;
  background: var(--yamato-color-bg-light);
  padding: 24px;
}

.register-card {
  width: min(460px, 100%);
  padding: 34px 32px 30px;
  border-radius: var(--yamato-radius-lg);
  background: #ffffff;
  box-shadow: var(--yamato-shadow-card);
  box-sizing: border-box;
}

.register-card__title {
  margin: 0 0 8px;
  font-family: var(--yamato-font-display);
  font-size: 34px;
  font-weight: 500;
  line-height: 1.2;
  letter-spacing: 0;
  color: var(--yamato-color-text-primary);
}

.register-card__subtitle {
  margin: 0 0 22px;
  font-size: 15px;
  line-height: 1.6;
  letter-spacing: normal;
  color: var(--yamato-color-text-secondary);
}

.register-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.register-form__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.register-form__label {
  font-size: 14px;
  font-weight: 500;
  letter-spacing: normal;
  color: var(--yamato-color-text-primary);
}

.required {
  font-style: normal;
  color: var(--yamato-color-danger);
}

.register-form__input {
  width: 100%;
}

.register-form__submit {
  margin-top: 6px;
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
  transition: background 0.2s ease, transform 0.1s ease, opacity 0.2s ease, box-shadow 0.2s ease;

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

.register-card__footer {
  margin: 20px 0 0;
  font-size: 14px;
  color: var(--yamato-color-text-secondary);
  text-align: center;
}

.register-card__link {
  color: var(--yamato-color-link);
  text-decoration: none;
  font-weight: 400;

  &:hover {
    text-decoration: underline;
  }
}

@media (max-width: 640px) {
  .register-card {
    padding: 28px 22px 24px;
  }

  .register-card__title {
    font-size: 28px;
  }
}
</style>
