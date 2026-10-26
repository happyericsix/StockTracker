<script setup>
/**
 * 区块卡片：全站统一的"区块"单元（2026-10-15 前端区块化改造）。
 *
 * 设计参考（调研结论，见 2026-10-15 会话）：
 * - TradingAgents-CN：el-card 式区块 = 卡头（标题 + 角标动作）+ 卡身；重要度低的区块可折叠，
 *   折叠态记 localStorage（用户刷新后不用重新收起）。
 * - FreqUI/OpenBB 的可拖拽网格是二期选项，这里先把"区块"这个单元收敛成一个组件，
 *   将来上拖拽时只需要替换外层容器。
 *
 * 用法：
 *   <BlockCard title="事件与解读" hint="解读由 AI 生成…">
 *     <template #actions><Button size="sm">刷新</Button></template>
 *     …内容…
 *   </BlockCard>
 */
import { ref } from 'vue'
import InfoTip from '../InfoTip.vue'

const props = defineProps({
  title: { type: String, default: '' },
  /** InfoTip 的说明文案；空则不渲染问号 */
  hint: { type: String, default: '' },
  /** 可折叠（折叠态记 localStorage，键必传且全站唯一） */
  collapsible: { type: Boolean, default: false },
  storageKey: { type: String, default: '' },
  /** 卡身内边距；图表卡常需要 p-0 自控 */
  pad: { type: Boolean, default: true },
  /** 卡身内容区额外的 class（如 max-h + overflow 滚动） */
  bodyClass: { type: String, default: '' },
})

const collapsed = ref(
  props.collapsible && props.storageKey
    ? localStorage.getItem(props.storageKey) === '1'
    : false,
)

const toggle = () => {
  collapsed.value = !collapsed.value
  if (props.collapsible && props.storageKey) {
    localStorage.setItem(props.storageKey, collapsed.value ? '1' : '0')
  }
}
</script>

<template>
  <section class="min-w-0 rounded-xl border border-border bg-surface">
    <header
      v-if="title || $slots.actions"
      class="flex min-h-[42px] items-center gap-2 px-4"
      :class="collapsible ? 'cursor-pointer select-none hover:bg-surface-2/60' : ''"
      @click="collapsible && toggle()"
    >
      <span
        v-if="collapsible"
        class="text-[11px] text-ink-3 transition-transform"
        :class="collapsed ? '' : 'rotate-90'"
        aria-hidden="true"
      >▶</span>
      <h3 v-if="title" class="flex items-center gap-1.5 text-[14px] font-semibold text-ink">
        {{ title }}
        <InfoTip v-if="hint" :label="`${title}说明`" :text="hint" />
      </h3>
      <div class="ml-auto flex items-center gap-1.5" @click.stop>
        <slot name="actions" />
      </div>
    </header>
    <div
      v-show="!collapsed"
      class="border-t border-border"
      :class="[pad ? 'p-4' : '', bodyClass]"
    >
      <slot />
    </div>
  </section>
</template>
