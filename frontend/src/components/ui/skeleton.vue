<script setup>
/** 骨架块：加载态用它拼出内容的形状，不用居中大转圈 */
defineProps({
  width: { type: String, default: '100%' },
  height: { type: String, default: '14px' },
})
</script>

<template>
  <div class="animate-pulse rounded-md bg-surface-2" :style="{ width, height }" aria-hidden="true" />
</template>
