<script setup>
/**
 * 信息提示：把"常驻页面的大段说明文字"收进一个小 ⓘ 图标里，点开才看。
 *
 * <h3>为什么是组件而不是 title 属性</h3>
 * 原生 title 悬停提示在触屏上不存在、延迟一秒、样式不可控，而这里的说明
 * （免责声明、口径说明）在手机上同样需要能看到。点击切换在鼠标和触屏上
 * 行为一致，Esc 与点击外部都能关。
 *
 * <h3>为什么面板点击自身也关闭</h3>
 * 读完了点一下就走，不必去找那个小图标。说明文字不做交互（不可选中链接等），
 * 所以整块可点不会有误触。
 */
import { ref, watch, onBeforeUnmount } from 'vue'
import AppIcon from './AppIcon.vue'

defineProps({
  /** 提示正文。只用纯文本——这里不该承载富文本，需要强调就在原文里用引号 */
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
  <span ref="rootEl" class="info-tip">
    <button
      type="button"
      class="info-tip-btn"
      :aria-expanded="open"
      :aria-label="label"
      @click="open = !open"
      @keydown.escape="open = false"
    >
      <AppIcon name="info" :size="14" :stroke-width="2" />
    </button>
    <span v-if="open" class="info-tip-panel" role="note" @click="open = false">{{ text }}</span>
  </span>
</template>

<style scoped>
.info-tip {
  position: relative;
  display: inline-flex;
  align-items: center;
}
.info-tip-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  transition: color var(--duration-fast) var(--ease-out),
              background-color var(--duration-fast) var(--ease-out);
}
.info-tip-btn:hover { color: var(--color-accent); background: var(--color-accent-soft); }
/* 面板锚在图标下方，靠左对齐图标而不是居中：靠右的标题用居中会把面板顶出屏幕 */
.info-tip-panel {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 20;
  width: max-content;
  max-width: 280px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-surface);
  box-shadow: var(--shadow-2);
  font: var(--font-caption);
  line-height: 1.6;
  color: var(--color-text-secondary);
  text-align: left;
  cursor: pointer;
}
</style>
