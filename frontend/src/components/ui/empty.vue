<script setup>
/**
 * 空态：图标 + 一句人话 + 一个可选动作。空态要教界面，不说"这里什么都没有"。
 * icon 传 lucide 组件对象（markRaw 包一下或直接模板里用组件）。
 */
defineProps({
  title: { type: String, required: true },
  desc: { type: String, default: '' },
  size: { type: String, default: 'md' }, // md 常规区块 / lg 整页
})
</script>

<template>
  <div
    class="flex flex-col items-center justify-center gap-1.5 py-14 text-center"
    :class="size === 'lg' ? 'py-24' : ''"
  >
    <div
      class="mb-2 flex items-center justify-center rounded-full bg-surface-2 text-ink-3"
      :class="size === 'lg' ? 'h-14 w-14' : 'h-11 w-11'"
    >
      <slot name="icon" />
    </div>
    <p class="text-[14px] font-medium text-ink">{{ title }}</p>
    <p v-if="desc" class="max-w-[46ch] text-[12.5px] leading-relaxed text-ink-2">{{ desc }}</p>
    <div v-if="$slots.action" class="mt-3">
      <slot name="action" />
    </div>
  </div>
</template>
