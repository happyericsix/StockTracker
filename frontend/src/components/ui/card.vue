<script setup>
/**
 * 卡片：border-first（1px 边框，无阴影——深度只声明一次）。
 * pad=false 时内边距由调用方自定（表格卡常用 p-0 + 内部块）。
 */
defineProps({
  pad: { type: Boolean, default: true },
})
</script>

<template>
  <section
    class="rounded-xl border border-border bg-surface"
    :class="pad ? 'p-5' : ''"
  >
    <slot />
  </section>
</template>
