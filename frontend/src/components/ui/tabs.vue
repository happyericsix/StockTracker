<script setup>
/**
 * 下划线式标签页（全站唯一标签页词汇）。
 * items: [{ key, label, count? }]；count 为数字时右侧带中性小计数。
 */
defineProps({
  items: { type: Array, required: true },
  modelValue: { type: [String, Number], required: true },
})
defineEmits(['update:modelValue'])
</script>

<template>
  <div class="flex items-end gap-0.5 border-b border-border" role="tablist">
    <button
      v-for="item in items"
      :key="item.key"
      type="button"
      role="tab"
      :aria-selected="modelValue === item.key"
      class="relative flex h-9 items-center gap-1.5 px-3 text-[13px] text-ink-2 transition-colors hover:text-ink"
      :class="modelValue === item.key ? 'font-medium text-ink' : ''"
      @click="$emit('update:modelValue', item.key)"
    >
      {{ item.label }}
      <span v-if="typeof item.count === 'number'" class="num text-[11.5px] text-ink-3">{{ item.count }}</span>
      <span
        class="absolute inset-x-2 -bottom-px h-0.5 rounded-full transition-opacity"
        :class="modelValue === item.key ? 'bg-accent opacity-100' : 'opacity-0'"
      />
    </button>
  </div>
</template>
