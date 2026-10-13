<script setup>
import { cva } from 'class-variance-authority'
import { cn } from '../../lib/utils'
import Spinner from './spinner.vue'

/**
 * 全站唯一按钮词汇（DESIGN.md「组件词汇」）。
 * variant: primary（主操作）/ secondary（常规）/ ghost（弱化）/ danger（删除类）
 * size: md(34) / sm(30) / icon(34方形) / icon-sm(30方形)
 */
const props = defineProps({
  variant: { type: String, default: 'secondary' },
  size: { type: String, default: 'md' },
  block: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
})

const styles = cva(
  'inline-flex select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-lg font-medium transition-colors duration-150 disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        primary: 'bg-accent text-on-accent hover:bg-accent-hover',
        secondary: 'border border-border bg-surface text-ink hover:bg-surface-2',
        ghost: 'text-ink-2 hover:bg-surface-2 hover:text-ink',
        danger: 'border border-border bg-surface text-up hover:bg-up-soft',
      },
      size: {
        md: 'h-[34px] px-3.5 text-[13px]',
        sm: 'h-[30px] px-3 text-[12.5px]',
        icon: 'h-[34px] w-[34px]',
        'icon-sm': 'h-[30px] w-[30px]',
      },
    },
    defaultVariants: { variant: 'secondary', size: 'md' },
  },
)
</script>

<template>
  <button
    type="button"
    :class="cn(styles({ variant, size }), block && 'w-full')"
    :disabled="loading || undefined"
  >
    <Spinner v-if="loading" :size="14" />
    <slot />
  </button>
</template>
