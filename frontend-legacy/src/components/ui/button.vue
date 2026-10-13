<script setup lang="ts">
import { cn } from '../../lib/utils'
import { type HTMLAttributes, computed } from 'vue'

const props = defineProps<{
  variant?: 'default' | 'ghost' | 'outline' | 'secondary' | 'link'
  size?: 'sm' | 'md' | 'lg' | 'icon'
  class?: HTMLAttributes['class']
}>()

const variantClasses = {
  default: 'bg-primary text-primary-foreground hover:bg-primary/90 shadow',
  ghost: 'hover:bg-accent hover:text-accent-foreground',
  outline: 'border border-input bg-background hover:bg-accent hover:text-accent-foreground',
  secondary: 'bg-secondary text-secondary-foreground hover:bg-secondary/80',
  link: 'text-primary underline-offset-4 hover:underline',
}

const sizeClasses = {
  sm: 'h-8 px-3 text-xs rounded-md gap-1.5',
  md: 'h-9 px-4 text-sm rounded-md gap-2',
  lg: 'h-10 px-6 text-base rounded-md gap-2',
  icon: 'h-9 w-9 rounded-md',
}

const classes = computed(() =>
  cn('inline-flex items-center justify-center font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 cursor-pointer',
     variantClasses[props.variant || 'default'],
     sizeClasses[props.size || 'md'],
     props.class)
)
</script>

<template>
  <button :class="classes"><slot /></button>
</template>
