/* 会话列表折叠 */
<template>
  <aside
    :class="['sidebar', { 'sidebar--collapsed': collapsed }]"
    :style="{ width: `${currentWidth}px` }"
  >
    <div v-if="showHeader" class="sidebar__header">
      <button v-if="collapsible" class="sidebar__toggle" type="button" @click="toggle">
        <svg
          v-if="!collapsed"
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        >
          <path d="M3 12h18M3 6h18M3 18h18" />
        </svg>
        <svg
          v-else
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        >
          <path d="M21 12H3M21 6H3M21 18H3" />
        </svg>
      </button>
      <img v-if="logoUrl" :src="logoUrl" class="sidebar__logo" alt="logo" />
      <h2 v-else class="sidebar__title">{{ title }}</h2>
    </div>
    <div class="sidebar__content">
      <slot />
    </div>
    <div v-if="showFooter" class="sidebar__footer">
      <div class="sidebar-user">
        <div class="sidebar-user__avatar" aria-label="用户头像">
          <img v-if="userAvatarUrl" class="sidebar-user__avatar-img" :src="userAvatarUrl" alt="" />
          <span v-else class="sidebar-user__avatar-text">{{ avatarText }}</span>
        </div>
        <div class="sidebar-user__meta">
          <div class="sidebar-user__top-row">
            <div class="sidebar-user__name">{{ displayName }}</div>
            <slot name="user-actions" />
          </div>
          <div class="sidebar-user__desc">{{ userDesc }}</div>
        </div>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue'

interface SidebarProps {
  title?: string
  logoUrl?: string
  userName?: string
  userAvatarUrl?: string
  userDesc?: string
  collapsible?: boolean
  showHeader?: boolean
  showFooter?: boolean
  width?: number
  collapsedWidth?: number
}

const props = withDefaults(defineProps<SidebarProps>(), {
  title: 'yamato',
  userName: '',
  userAvatarUrl: '',
  userDesc: '',
  collapsible: true,
  showHeader: true,
  showFooter: true,
  width: 280,
  collapsedWidth: 60,
})

const collapsed = defineModel<boolean>('collapsed', { default: false })

const displayName = computed(() => props.userName?.trim() || '--')
const avatarText = computed(() => displayName.value.slice(0, 1).toUpperCase())
const currentWidth = computed(() => (collapsed.value ? props.collapsedWidth : props.width))

const toggle = () => {
  collapsed.value = !collapsed.value
}
</script>

<style lang="scss" scoped>
.sidebar {
  height: calc(100vh - 24px);
  position: fixed;
  top: 12px;
  left: 12px;
  z-index: 10;
  background: #ffffff;
  border-radius: 16px;
  border: 1px solid var(--yamato-color-border-subtle);
  box-shadow: 0 10px 28px rgba(20, 20, 19, 0.08);
  transition: width 0.3s ease;
  display: flex;
  flex-direction: column;
  overflow: hidden;

  &__header {
    padding: 14px 14px 10px;
    display: flex;
    align-items: center;
    gap: 10px;
  }

  &__toggle {
    width: 40px;
    height: 40px;
    border: none;
    background: transparent;
    border-radius: var(--yamato-radius-pill);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #6d6b65;
    transition: all 0.2s ease;

    &:hover {
      background: var(--yamato-color-surface-alt);
      color: var(--yamato-color-text-primary);
    }

    &:focus-visible {
      outline: none;
      box-shadow: var(--yamato-focus-ring);
    }
  }

  &__title {
    font-size: 17px;
    font-weight: 600;
    color: var(--yamato-color-text-primary);
    letter-spacing: 0;
    margin: 0;
    max-width: 160px;
    overflow: hidden;
    white-space: nowrap;
    transition: opacity 0.2s ease, max-width 0.3s ease;
  }

  &__logo {
    height: 28px;
    width: auto;
    max-width: 100px;
    object-fit: contain;
    display: block;
    transition: opacity 0.2s ease;
    filter: none;
  }

  &__content {
    flex: 1;
    overflow-y: auto;
    padding: 8px;
    transition: opacity 0.2s ease;
    background: #ffffff;
  }

  &__footer {
    padding: 10px 14px 14px;
    height: 64px;
    box-sizing: border-box;
    margin-top: auto;
    border-top: 1px solid var(--yamato-color-border-subtle);
  }
}

.sidebar--collapsed {
  .sidebar__title {
    opacity: 0;
    max-width: 0;
  }

  .sidebar__logo {
    opacity: 0;
  }

  .sidebar__content {
    opacity: 0;
    pointer-events: none;
  }

  .sidebar-user__meta {
    opacity: 0;
    pointer-events: none;
  }
}

.sidebar-user {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 40px;

  &__avatar {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: #30302e;
    color: #ffffff;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 600;
    user-select: none;
    flex: 0 0 auto;
    font-size: 12px;
    overflow: hidden;
  }

  &__avatar-img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }

  &__avatar-text {
    line-height: 1;
  }

  &__meta {
    max-width: 160px;
    min-width: 0;
    overflow: hidden;
    opacity: 1;
    transition: opacity 0.2s ease;
    flex: 1;
  }

  &__name {
    font-size: 14px;
    font-weight: 600;
    color: var(--yamato-color-text-primary);
    line-height: 1.2;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
  }

  &__top-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    min-width: 0;
  }

  &__desc {
    font-size: 12px;
    color: #8a8881;
    line-height: 1.2;
    margin-top: 2px;
  }
}

/* 展开/折叠头像尺寸保持一致，不需要额外覆盖样式 */
</style>
