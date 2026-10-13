<script setup>
/**
 * 文本输入（34px 标准）：label 由调用方放在上方（表单纪律），
 * 这里只管控件本体：边框、占位、焦点环、禁用。
 */
defineProps({
  modelValue: { type: [String, Number], default: '' },
  type: { type: String, default: 'text' },
  placeholder: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
})
defineEmits(['update:modelValue'])
</script>

<template>
  <input
    :type="type"
    :value="modelValue"
    :placeholder="placeholder"
    :disabled="disabled"
    class="h-[34px] w-full rounded-lg border border-border bg-surface px-3 text-[13px] text-ink transition-colors placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)] disabled:cursor-not-allowed disabled:opacity-50"
    @input="$emit('update:modelValue', $event.target.value)"
  />
</template>
