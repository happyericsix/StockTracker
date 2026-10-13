<script setup lang="ts">
import { cn } from '../../lib/utils'
const props = defineProps<{
  orientation?: 'horizontal' | 'vertical'
  class?: string
}>()
</script>

<template>
  <div
    role="separator"
    :orientation="props.orientation"
    :class="cn('shrink-0 bg-border',
      props.orientation === 'vertical' ? 'w-px h-full' : 'h-px w-full',
      props.class)"
  />
</template>
