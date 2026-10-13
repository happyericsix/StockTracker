<script setup>
defineProps({
  size: { type: [Number, String], default: 16 },
})
</script>

<template>
  <span
    class="inline-block shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent align-[-2px] opacity-70"
    :style="{ width: `${size}px`, height: `${size}px` }"
    aria-hidden="true"
  />
</template>
