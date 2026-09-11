<template>
  <div class="page-header">
    <h1 class="page-header__title">{{ title }}</h1>
    <p class="page-header__subtitle">{{ subtitle }}</p>
  </div>
</template>

<script setup lang="ts">
interface Props {
  title: string
  subtitle: string
}

defineProps<Props>()
</script>

<style scoped lang="scss">
.page-header {
  padding: 14px 32px;
  border-bottom: none;
  background: #ffffff;
}

.page-header__title {
  font-size: 24px;
  font-weight: 500;
  color: #1976d2;
  margin: 0 0 4px 0;
  line-height: 1.2;
}

.page-header__subtitle {
  font-size: 14px;
  color: #5f6368;
  margin: 0;
  line-height: 1.2;
}
</style>
