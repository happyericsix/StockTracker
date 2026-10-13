<script setup>
/**
 * 轻量模态：遮罩 + 卡面盒。Esc / 点遮罩关闭（语义：无意点空处=放弃）。
 * 承载需要打断与保护的窄任务（扫码绑定、删除确认类），不做大表单。
 */
import { watch, onBeforeUnmount } from 'vue'
import { X } from 'lucide-vue-next'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '' },
  width: { type: String, default: 'min(400px, 100%)' },
})
const emit = defineEmits(['close'])

const onKeydown = (e) => {
  if (e.key === 'Escape') emit('close')
}
watch(() => props.open, (value) => {
  if (value) document.addEventListener('keydown', onKeydown)
  else document.removeEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-50 flex items-center justify-center p-4"
      :style="{ background: 'var(--overlay)' }"
      @click.self="$emit('close')"
    >
      <div
        role="dialog"
        aria-modal="true"
        :aria-label="title || '对话框'"
        class="flex max-h-[calc(100dvh-32px)] w-full flex-col overflow-y-auto rounded-xl border border-border bg-surface p-5"
        :style="{ maxWidth: width, boxShadow: 'var(--shadow-pop)' }"
      >
        <div class="mb-3 flex items-center justify-between gap-3">
          <h3 class="text-[15px] font-semibold text-ink">{{ title }}</h3>
          <button
            type="button"
            class="flex h-8 w-8 items-center justify-center rounded-lg text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
            aria-label="关闭对话框"
            @click="$emit('close')"
          >
            <X :size="16" :stroke-width="2" aria-hidden="true" />
          </button>
        </div>
        <slot />
      </div>
    </div>
  </Teleport>
</template>
