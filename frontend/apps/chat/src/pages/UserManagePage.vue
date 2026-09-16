<template>
  <div class="user-manage-page">
    <div class="user-manage-header">
      <div class="user-manage-header__left">
        <h1 class="user-manage-header__title">用户管理</h1>
        <span class="user-manage-header__count">共 {{ users.length }} 位用户</span>
      </div>
      <button class="refresh-btn" :disabled="loading" @click="loadUsers">
        <span class="refresh-btn__icon" :class="{ 'is-spinning': loading }">↻</span>
        刷新
      </button>
    </div>

    <div class="user-manage-content">
      <div v-if="loading && users.length === 0" class="state-placeholder">
        加载中...
      </div>

      <div v-else-if="!loading && users.length === 0" class="state-placeholder">
        暂无用户数据
      </div>

      <table v-else class="user-table">
        <thead>
          <tr>
            <th class="user-table__th">用户名</th>
            <th class="user-table__th">姓名</th>
            <th class="user-table__th">邮箱</th>
            <th class="user-table__th">角色</th>
            <th class="user-table__th user-table__th--actions">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="user in users" :key="user.id" class="user-table__row">
            <td class="user-table__td user-table__td--username">{{ user.username }}</td>
            <td class="user-table__td">{{ user.name || '—' }}</td>
            <td class="user-table__td user-table__td--email">{{ user.email }}</td>
            <td class="user-table__td">
              <span class="role-badge" :class="`role-badge--${user.role}`">
                {{ roleLabel(user.role) }}
              </span>
            </td>
            <td class="user-table__td user-table__td--actions">
              <template v-if="user.role !== 'superuser'">
                <button
                  class="action-btn action-btn--role"
                  :disabled="actionPending === user.id"
                  @click="toggleRole(user)"
                >
                  {{ user.role === 'admin' ? '降为普通用户' : '设为管理员' }}
                </button>
                <button
                  class="action-btn action-btn--delete"
                  :disabled="actionPending === user.id"
                  @click="openDeleteDialog(user)"
                >
                  删除
                </button>
                <button
                  class="action-btn action-btn--reset"
                  :disabled="actionPending === user.id"
                  @click="openResetDialog(user)"
                >
                  重置密码
                </button>
              </template>
              <span v-else class="self-label">超级管理员</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <ConfirmDialog
      v-model="showDeleteDialog"
      title="删除用户"
      :message="`确定要删除用户「${pendingDeleteUser?.username}」吗？此操作不可恢复。`"
      type="danger"
      confirm-text="删除"
      cancel-text="取消"
      @confirm="confirmDelete"
    />

    <!-- 重置密码弹窗 -->
    <Teleport to="body">
      <Transition name="dialog-fade">
        <div v-if="showResetDialog" class="dialog-overlay" @click.self="closeResetDialog">
          <div class="dialog-container">
            <div class="dialog-header">
              <h3 class="dialog-title">重置密码</h3>
              <button class="dialog-close" @click="closeResetDialog" aria-label="关闭">✕</button>
            </div>
            <div class="dialog-body">
              <p class="reset-dialog__desc">
                为 <strong>{{ pendingResetUser?.username }}</strong> 设置新密码。
              </p>
              <input
                v-model="resetPasswordValue"
                type="password"
                class="reset-dialog__input"
                placeholder="请输入新密码（6-128 位）"
                :minlength="6"
                :maxlength="128"
                autocomplete="new-password"
                @keyup.enter="confirmResetPassword"
              />
              <p class="reset-dialog__hint">重置后请将新密码人工告知该用户。</p>
            </div>
            <div class="dialog-footer">
              <button class="dialog-btn dialog-btn--cancel" @click="closeResetDialog">取消</button>
              <button
                class="dialog-btn dialog-btn--confirm"
                :disabled="!resetPasswordValue || resetPasswordValue.length < 6"
                @click="confirmResetPassword"
              >
                确认重置
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ConfirmDialog, useToast } from '@nbhx/components'
import { listUsers, deleteUser, updateUserRole, resetUserPassword } from '../services/auth'
import type { UserResponse } from '../services/auth'

const { showSuccess, showError } = useToast()

const users = ref<UserResponse[]>([])
const loading = ref(false)
const actionPending = ref<string | null>(null)

const showDeleteDialog = ref(false)
const pendingDeleteUser = ref<UserResponse | null>(null)

const showResetDialog = ref(false)
const resetPasswordValue = ref('')
const pendingResetUser = ref<UserResponse | null>(null)

const roleLabel = (role: string) => {
  if (role === 'superuser') return '超级管理员'
  if (role === 'admin') return '管理员'
  return '普通用户'
}

const loadUsers = async () => {
  loading.value = true
  try {
    users.value = await listUsers()
  } catch (error: any) {
    showError(error?.message || '加载用户列表失败')
  } finally {
    loading.value = false
  }
}

const toggleRole = async (user: UserResponse) => {
  const newRole = user.role === 'admin' ? 'user' : 'admin'
  actionPending.value = user.id
  try {
    const updated = await updateUserRole(user.id, { role: newRole })
    const idx = users.value.findIndex((u) => u.id === user.id)
    if (idx !== -1) users.value[idx] = updated
    showSuccess(`已将「${user.username}」${newRole === 'admin' ? '设为管理员' : '降为普通用户'}`)
  } catch (error: any) {
    showError(error?.message || '角色修改失败')
  } finally {
    actionPending.value = null
  }
}

const openDeleteDialog = (user: UserResponse) => {
  pendingDeleteUser.value = user
  showDeleteDialog.value = true
}

const confirmDelete = async () => {
  if (!pendingDeleteUser.value) return
  const target = pendingDeleteUser.value
  actionPending.value = target.id
  try {
    await deleteUser(target.id)
    users.value = users.value.filter((u) => u.id !== target.id)
    showSuccess(`用户「${target.username}」已删除`)
  } catch (error: any) {
    showError(error?.message || '删除失败')
  } finally {
    actionPending.value = null
    pendingDeleteUser.value = null
  }
}

const openResetDialog = (user: UserResponse) => {
  pendingResetUser.value = user
  resetPasswordValue.value = ''
  showResetDialog.value = true
}

const closeResetDialog = () => {
  showResetDialog.value = false
  pendingResetUser.value = null
  resetPasswordValue.value = ''
}

const confirmResetPassword = async () => {
  if (actionPending.value) return
  if (!pendingResetUser.value || resetPasswordValue.value.length < 6) return
  const target = pendingResetUser.value
  actionPending.value = target.id
  try {
    await resetUserPassword(target.id, { password: resetPasswordValue.value })
    showSuccess(`已重置「${target.username}」的密码，请将该新密码人工告知此用户`)
    closeResetDialog()
  } catch (error: any) {
    showError(error?.message || '密码重置失败')
  } finally {
    actionPending.value = null
  }
}

onMounted(loadUsers)
</script>

<style scoped lang="scss">
.user-manage-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--nbhx-color-bg-light);
  padding: 32px 32px 24px;
  box-sizing: border-box;
  overflow: hidden;
}

.user-manage-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
  flex-shrink: 0;
}

.user-manage-header__left {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.user-manage-header__title {
  margin: 0;
  font-family: var(--nbhx-font-display);
  font-size: 34px;
  font-weight: 500;
  line-height: 1.2;
  letter-spacing: 0;
  color: var(--nbhx-color-text-primary);
}

.user-manage-header__count {
  font-size: 14px;
  color: var(--nbhx-color-text-muted);
}

.refresh-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 36px;
  padding: 0 16px;
  border: 1px solid var(--nbhx-color-border-subtle);
  border-radius: var(--nbhx-radius-sm);
  background: var(--nbhx-color-surface);
  color: var(--nbhx-color-text-primary);
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;

  &:hover:not(:disabled) {
    background: var(--nbhx-color-surface-alt);
  }

  &:focus-visible {
    outline: none;
    box-shadow: var(--nbhx-focus-ring);
  }

  &:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
}

.refresh-btn__icon {
  font-size: 16px;
  display: inline-block;
  transition: transform 0.5s ease;

  &.is-spinning {
    animation: spin 0.8s linear infinite;
  }
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.user-manage-content {
  background: #ffffff;
  border-radius: var(--nbhx-radius-lg);
  box-shadow: var(--nbhx-shadow-card);
  overflow: auto;
  flex: 1;
  min-height: 0;
}

.state-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 160px;
  font-size: 14px;
  color: var(--nbhx-color-text-muted);
}

.user-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
  color: var(--nbhx-color-text-primary);
}

.user-table__th {
  padding: 14px 16px;
  text-align: left;
  font-size: 12px;
  font-weight: 600;
  color: var(--nbhx-color-text-secondary);
  background: rgba(0, 0, 0, 0.03);
  border-bottom: 1px solid var(--nbhx-color-border-subtle);
  white-space: nowrap;
  position: sticky;
  top: 0;
  z-index: 1;

  &--actions {
    text-align: right;
    white-space: nowrap;
  }

  &--perms {
    white-space: nowrap;
  }
}

.user-table__row {
  transition: background 0.15s ease;

  &:not(:last-child) {
    border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  }

  &:hover {
    background: rgba(0, 0, 0, 0.02);
  }
}

.user-table__td {
  padding: 14px 16px;
  vertical-align: middle;

  &--username {
    font-weight: 500;
    color: var(--nbhx-color-text-primary);
  }

  &--email {
    color: var(--nbhx-color-text-secondary);
    font-size: 13px;
  }

  &--actions {
    text-align: right;
    white-space: nowrap;
  }
}

.role-badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: var(--nbhx-radius-pill);
  font-size: 12px;
  font-weight: 500;

  &--superuser {
    background: var(--nbhx-color-accent-soft);
    color: var(--nbhx-color-accent);
  }

  &--admin {
    background: var(--nbhx-color-success-soft);
    color: var(--nbhx-color-success);
  }

  &--user {
    background: rgba(0, 0, 0, 0.08);
    color: var(--nbhx-color-text-secondary);
  }
}

.action-btn {
  height: 30px;
  padding: 0 12px;
  border-radius: var(--nbhx-radius-sm);
  border: 1px solid transparent;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease, opacity 0.15s ease;

  & + & {
    margin-left: 8px;
  }

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  &--role {
    background: var(--nbhx-color-accent-soft);
    color: var(--nbhx-color-accent);
    border-color: rgba(201, 100, 66, 0.3);

    &:hover:not(:disabled) {
      background: var(--nbhx-color-accent-soft-strong);
    }
  }

  &--delete {
    background: var(--nbhx-color-danger-soft);
    color: var(--nbhx-color-danger);
    border-color: rgba(196, 59, 47, 0.3);

    &:hover:not(:disabled) {
      background: rgba(196, 59, 47, 0.2);
    }
  }

  &--reset {
    background: rgba(52, 168, 83, 0.1);
    color: var(--nbhx-color-success);
    border-color: rgba(52, 168, 83, 0.3);

    &:hover:not(:disabled) {
      background: rgba(52, 168, 83, 0.2);
    }
  }
}

.self-label {
  font-size: 12px;
  color: var(--nbhx-color-text-muted);
  font-style: italic;
}

.perm-label {
  font-size: 12px;

  &--static {
    color: var(--nbhx-color-text-muted);
    font-style: italic;
  }

  &--always {
    color: var(--nbhx-color-success);
    font-weight: 500;
  }
}

.perm-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  user-select: none;

  & + & {
    margin-left: 12px;
  }
}

.perm-toggle__label {
  font-size: 12px;
  color: var(--nbhx-color-text-secondary);
}

.perm-toggle__input {
  width: 16px;
  height: 16px;
  cursor: pointer;
  accent-color: var(--nbhx-color-accent);

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
}

/* ---- 重置密码弹窗 ---- */

.dialog-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(20, 20, 19, 0.4);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 20px;
}

.dialog-container {
  background: #ffffff;
  border-radius: var(--nbhx-radius-lg);
  box-shadow: var(--nbhx-shadow-overlay);
  border: 1px solid var(--nbhx-color-border-subtle);
  min-width: 320px;
  max-width: 440px;
  width: 100%;
  overflow: hidden;
}

.dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px;
  border-bottom: 1px solid var(--nbhx-color-border-subtle);
}

.dialog-title {
  margin: 0;
  font-size: 21px;
  font-weight: 600;
  line-height: 1.19;
  color: var(--nbhx-color-text-primary);
}

.dialog-close {
  border: none;
  background: transparent;
  font-size: 20px;
  color: var(--nbhx-color-text-muted);
  cursor: pointer;
  padding: 0;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  transition: all 0.2s ease;

  &:hover {
    background-color: rgba(0, 0, 0, 0.05);
    color: var(--nbhx-color-text-primary);
  }
}

.dialog-body {
  padding: 24px;
  color: var(--nbhx-color-text-secondary);
  font-size: 16px;
  line-height: 1.6;
}

.dialog-footer {
  display: flex;
  gap: 12px;
  padding: 16px 24px;
  border-top: 1px solid var(--nbhx-color-border-subtle);
  justify-content: flex-end;
}

.dialog-btn {
  min-height: 36px;
  padding: 0 20px;
  border: none;
  border-radius: var(--nbhx-radius-sm);
  font-size: 14px;
  font-weight: 400;
  cursor: pointer;
  transition: all 0.2s ease;
  outline: none;

  &:active {
    transform: translateY(1px);
  }

  &:focus-visible {
    box-shadow: var(--nbhx-focus-ring);
  }

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
}

.dialog-btn--cancel {
  background: var(--nbhx-color-surface-alt);
  color: #4d4c48;

  &:hover {
    background: #e8e6dc;
  }
}

.dialog-btn--confirm {
  background: var(--nbhx-color-accent);
  color: #fff;

  &:hover:not(:disabled) {
    background: var(--nbhx-color-accent-hover);
  }
}

.reset-dialog__desc {
  margin: 0 0 16px;
  font-size: 14px;
  color: var(--nbhx-color-text-secondary);

  strong {
    color: var(--nbhx-color-text-primary);
    font-weight: 600;
  }
}

.reset-dialog__input {
  width: 100%;
  box-sizing: border-box;
  height: 40px;
  padding: 0 14px;
  border: 1px solid var(--nbhx-color-border-subtle);
  border-radius: var(--nbhx-radius-sm);
  font-size: 14px;
  color: var(--nbhx-color-text-primary);
  background: var(--nbhx-color-surface);
  outline: none;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;

  &::placeholder {
    color: var(--nbhx-color-text-muted);
  }

  &:focus {
    border-color: var(--nbhx-color-accent);
    box-shadow: var(--nbhx-focus-ring);
  }
}

.reset-dialog__hint {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--nbhx-color-text-muted);
  line-height: 1.5;
}

.dialog-fade-enter-active,
.dialog-fade-leave-active {
  transition: opacity 0.2s ease;

  .dialog-container {
    transition: transform 0.2s ease;
  }
}

.dialog-fade-enter-from,
.dialog-fade-leave-to {
  opacity: 0;

  .dialog-container {
    transform: scale(0.95);
  }
}

@media (max-width: 980px) {
  .user-manage-page {
    padding: 24px 20px 18px;
  }

  .user-manage-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }

  .user-manage-header__left {
    flex-direction: column;
    gap: 8px;
    align-items: flex-start;
  }
}
</style>
