<script setup>
import { cva } from 'class-variance-authority'
import { cn } from '../../lib/utils'

/**
 * 徽章：胶囊 + 同色系浅底。
 * tone: up（涨/危险红域）/ down（跌/绿域）/ flat（平）/ accent（信息·蓝）/
 *       warn（哨兵·警示）/ neutral（中性灰）
 * 红绿语义全站只表达涨跌与告警方向，不挪作装饰。
 */
const props = defineProps({
  tone: { type: String, default: 'neutral' },
})
const styles = cva(
  'inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[12px] font-medium leading-5',
  {
    variants: {
      tone: {
        up: 'bg-up-soft text-up',
        down: 'bg-down-soft text-down',
        flat: 'bg-surface-2 text-ink-2',
        accent: 'bg-accent-soft text-accent',
        warn: 'bg-warn-soft text-warn',
        neutral: 'bg-surface-2 text-ink-2',
      },
    },
    defaultVariants: { tone: 'neutral' },
  },
)
</script>

<template>
  <span :class="cn(styles({ tone }))"><slot /></span>
</template>
