<script setup>
/**
 * 信息提示：把"常驻页面的大段说明文字"收进一个小 ⓘ 里，点开才看。
 * 逻辑自旧版原样移植：点击切换（触屏/鼠标一致），Esc 与点外部关，
 * 点面板自身也关（读完了点一下就走）。
 */
import { ref, watch, onBeforeUnmount } from 'vue'
import { Info } from 'lucide-vue-next'

defineProps({
  /** 提示正文。只用纯文本 */
  text: { type: String, required: true },
  /** 无障碍名称，说明这个 ⓘ 是关于什么的 */
  label: { type: String, default: '说明' },
})

const open = ref(false)
const rootEl = ref(null)

const onDocPointerDown = (e) => {
  if (rootEl.value && !rootEl.value.contains(e.target)) open.value = false
}

watch(open, (value) => {
  if (value) document.addEventListener('pointerdown', onDocPointerDown)
  else document.removeEventListener('pointerdown', onDocPointerDown)
})
onBeforeUnmount(() => document.removeEventListener('pointerdown', onDocPointerDown))
</script>

<template>
  <!-- @click.stop（2026-10-18）：ⓘ 常驻在可点击容器里（如 BlockCard 的折叠卡头），
       点击冒泡到父层会连带触发折叠——"点 ⓘ 却点到下面一层"即此。ⓘ 的点击冒泡
       没有任何合理用途，在组件根部一刀切断，按钮与提示面板一并修好。 -->
  <span ref="rootEl" class="relative inline-flex items-center" @click.stop>
    <button
      type="button"
      class="flex h-[22px] w-[22px] items-center justify-center rounded-md text-ink-3 transition-colors hover:bg-accent-soft hover:text-accent max-sm:h-[32px] max-sm:w-[32px]"
      :aria-expanded="open"
      :aria-label="label"
      @click="open = !open"
      @keydown.escape="open = false"
    >
      <Info :size="14" :stroke-width="2" aria-hidden="true" />
    </button>
    <!-- 面板锚在图标下方、靠左对齐：靠右的标题用居中会把面板顶出屏幕 -->
    <span
      v-if="open"
      class="absolute left-0 top-[calc(100%+6px)] z-30 w-max max-w-[280px] cursor-pointer rounded-lg border border-border bg-surface p-2.5 text-left text-[12.5px] leading-relaxed text-ink-2"
      style="box-shadow: var(--shadow-pop)"
      role="note"
      @click="open = false"
    >{{ text }}</span>
  </span>
</template>
