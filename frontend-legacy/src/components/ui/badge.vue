<script setup lang="ts">
import { cn } from '../../lib/utils'
import { type HTMLAttributes, computed } from 'vue'

const props = defineProps<{
  variant?: 'default' | 'secondary' | 'destructive' | 'outline' | 'gain' | 'loss' | 'warning'
  class?: HTMLAttributes['class']
}>()

const variants = {
  default: 'bg-primary text-primary-foreground',
  secondary: 'bg-secondary text-secondary-foreground',
  destructive: 'bg-destructive text-destructive-foreground',
  outline: 'text-foreground border-border',
  gain: 'bg-green-50 text-green-700 border border-green-200',
  loss: 'bg-red-50 text-red-700 border border-red-200',
  warning: 'bg-amber-50 text-amber-700 border border-amber-200',
}

const classes = computed(() =>
  cn('inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap',
     variants[props.variant || 'default'], props.class)
)
</script>

<template>
  <span :class="classes"><slot /></span>
</template>
