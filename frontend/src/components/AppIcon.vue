<script setup>
/**
 * 图标组件 —— 取代原先散落全站的 28 处 emoji（🤖📚📈🧠📊📱🔔💬📌💰🔥❄️✨💀…）。
 *
 * <p>为什么必须换掉 emoji：emoji 的字形由**操作系统**决定，Windows / macOS / Android
 * 各画一套，颜色、粗细、基线全不一样；它们是彩色的、有自己的一套色板，既不受
 * `currentColor` 控制，也不随字号/字重变化。一个专业行情工具把 emoji 当图标系统，
 * 是"AI 生成感"最直接的来源 —— 它读起来像占位符，而不是设计决定。
 *
 * <p>本组图标遵守的规矩（better-ui/icons.md）：
 * <ul>
 *   <li>统一 24×24 viewBox，`stroke="currentColor"`、`fill="none"`，所以颜色由 CSS 的
 *       `color` 决定，hover / 选中 / 禁用态一律从颜色和透明度来，不换资产。</li>
 *   <li>一套图标只有一个描边宽度，且要和旁边文字的**光学重量**匹配：
 *       1.5px 配常规（400）文字，2px 配半粗（600）文字。默认 1.5，粗文字旁显式传 2。</li>
 *   <li>`aria-hidden="true"` + `focusable="false"`：图标是装饰，可访问名称由外层文字或
 *       按钮的 aria-label 提供，读屏不该把图标念一遍。</li>
 *   <li>圆形端点与圆角连接（`stroke-linecap/linejoin: round`），避免出现尖角噪点。</li>
 * </ul>
 *
 * <p>用 `v-if` 分支而不是 `v-html`：后者在 SVG 命名空间下依赖 `innerHTML`，行为随浏览器
 * 版本变化；声明式分支由 Vue 编译成真正的 SVG 元素，没有这个不确定性。
 */
const props = defineProps({
  /** 图标名。见下方分支；新增图标时同时加一条分支和一个语义注释。 */
  name: { type: String, required: true },
  /** 边长（px）。默认 20 与 --font-ui(14px) 同排时视觉重量相当。 */
  size: { type: [Number, String], default: 20 },
  /** 描边宽度。旁边是 400 文字用 1.5，是 600 文字用 2。 */
  strokeWidth: { type: [Number, String], default: 1.5 },
})

const ICON_NAMES = [
  'trend-up', 'trend-down', 'chart', 'bot', 'book', 'database', 'smartphone',
  'bell', 'message', 'info', 'percent', 'sun', 'snow', 'sparkle',
  'arrow-down-right', 'chevron-right', 'chevron-left', 'close', 'check', 'wifi-off',
  'search',
]

if (import.meta.env.DEV && !ICON_NAMES.includes(props.name)) {
  // 写错名字会在开发期报出来，而不是静默渲染一个空盒子
  console.error(`[AppIcon] 未知图标名: ${props.name}`)
}
</script>

<template>
  <svg
    class="app-icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="strokeWidth"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <!-- 涨：价格突破、上涨趋势 -->
    <template v-if="name === 'trend-up'">
      <polyline points="3 17 9 11 13 15 21 7" />
      <polyline points="15 7 21 7 21 13" />
    </template>

    <!-- 跌：价格跌破、下跌趋势 -->
    <template v-else-if="name === 'trend-down'">
      <polyline points="3 7 9 13 13 9 21 17" />
      <polyline points="15 17 21 17 21 11" />
    </template>

    <!-- 行情/图表：模拟盘、复盘、看 K 线 -->
    <template v-else-if="name === 'chart'">
      <line x1="3" y1="20" x2="21" y2="20" />
      <line x1="6.5" y1="20" x2="6.5" y2="12" />
      <line x1="12" y1="20" x2="12" y2="6" />
      <line x1="17.5" y1="20" x2="17.5" y2="15" />
    </template>

    <!-- 智能助手 / 机器人消息 -->
    <template v-else-if="name === 'bot'">
      <rect x="4" y="8" width="16" height="12" rx="3" />
      <line x1="12" y1="4" x2="12" y2="8" />
      <circle cx="12" cy="3" r="1" />
      <circle cx="9.5" cy="14" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="14.5" cy="14" r="1.1" fill="currentColor" stroke="none" />
    </template>

    <!-- 策略库 -->
    <template v-else-if="name === 'book'">
      <rect x="5" y="3" width="14" height="18" rx="2" />
      <line x1="9.5" y1="3" x2="9.5" y2="21" />
    </template>

    <!-- 记忆：一个存储层，而不是一颗大脑 —— 它是数据，不是认知 -->
    <template v-else-if="name === 'database'">
      <ellipse cx="12" cy="6" rx="7" ry="3" />
      <path d="M5 6v6c0 1.66 3.13 3 7 3s7-1.34 7-3V6" />
      <path d="M5 12v6c0 1.66 3.13 3 7 3s7-1.34 7-3v-6" />
    </template>

    <!-- 扫码登录 -->
    <template v-else-if="name === 'smartphone'">
      <rect x="7" y="2" width="10" height="20" rx="2.5" />
      <line x1="11" y1="18.5" x2="13" y2="18.5" />
    </template>

    <!-- 预警提醒 -->
    <template v-else-if="name === 'bell'">
      <path d="M18 8a6 6 0 1 0-12 0c0 6-2 7-2 7h16s-2-1-2-7" />
      <path d="M10.5 20a2 2 0 0 0 3 0" />
    </template>

    <!-- 聊天消息 -->
    <template v-else-if="name === 'message'">
      <path d="M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H9l-5 4z" />
    </template>

    <!-- 系统通知。原先用 📌（图钉）表达"公告"，图钉在中文语境里更像"置顶"，
         换成信息图标，含义更直白。 -->
    <template v-else-if="name === 'info'">
      <circle cx="12" cy="12" r="9" />
      <line x1="12" y1="11" x2="12" y2="16.5" />
      <circle cx="12" cy="7.8" r="1" fill="currentColor" stroke="none" />
    </template>

    <!-- 盈亏百分比 -->
    <template v-else-if="name === 'percent'">
      <line x1="19" y1="5" x2="5" y2="19" />
      <circle cx="7.5" cy="7.5" r="2.5" />
      <circle cx="16.5" cy="16.5" r="2.5" />
    </template>

    <!-- RSI 超买：过热。原先用 🔥 —— emoji 的火焰在浅色底上是橙红色块，
         既不受 currentColor 控制，也和"涨红"的语义撞色。 -->
    <template v-else-if="name === 'sun'">
      <circle cx="12" cy="12" r="4" />
      <line x1="12" y1="2" x2="12" y2="4.5" />
      <line x1="12" y1="19.5" x2="12" y2="22" />
      <line x1="2" y1="12" x2="4.5" y2="12" />
      <line x1="19.5" y1="12" x2="22" y2="12" />
      <line x1="5.2" y1="5.2" x2="7" y2="7" />
      <line x1="17" y1="17" x2="18.8" y2="18.8" />
      <line x1="5.2" y1="18.8" x2="7" y2="17" />
      <line x1="17" y1="7" x2="18.8" y2="5.2" />
    </template>

    <!-- RSI 超卖：过冷。三线交叉即六角雪花。 -->
    <template v-else-if="name === 'snow'">
      <line x1="12" y1="3" x2="12" y2="21" />
      <line x1="4.2" y1="7.5" x2="19.8" y2="16.5" />
      <line x1="4.2" y1="16.5" x2="19.8" y2="7.5" />
    </template>

    <!-- MACD 金叉：四角星（"金"） -->
    <template v-else-if="name === 'sparkle'">
      <path d="M12 3l2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2z" />
    </template>

    <!-- MACD 死叉：右下箭头。原先用 💀 —— 骷髅表示"死叉"是字面直译，
         但它同时读作"危险/故障"，而金叉只是一条技术信号，不是故障。 -->
    <template v-else-if="name === 'arrow-down-right'">
      <line x1="7" y1="7" x2="17" y2="17" />
      <polyline points="17 9.5 17 17 9.5 17" />
    </template>

    <template v-else-if="name === 'chevron-right'">
      <polyline points="9 5 16 12 9 19" />
    </template>

    <!-- 返回：与 chevron-right 同一套几何，只是镜像。文字箭头 ← 与图标系统混用会
         出现两种描边重量和两种字形风格。 -->
    <template v-else-if="name === 'chevron-left'">
      <polyline points="15 5 8 12 15 19" />
    </template>

    <template v-else-if="name === 'close'">
      <line x1="6" y1="6" x2="18" y2="18" />
      <line x1="18" y1="6" x2="6" y2="18" />
    </template>

    <template v-else-if="name === 'check'">
      <polyline points="4 12.5 9.5 18 20 6.5" />
    </template>

    <!-- 搜索（放大镜） -->
    <template v-else-if="name === 'search'">
      <circle cx="11" cy="11" r="7" />
      <line x1="21" y1="21" x2="16.5" y2="16.5" />
    </template>

    <!-- 断线/离线 -->
    <template v-else-if="name === 'wifi-off'">
      <line x1="3" y1="3" x2="21" y2="21" />
      <path d="M8.5 15.5a5 5 0 0 1 7 0" />
      <path d="M5 12a10 10 0 0 1 3.5-2.3" />
      <path d="M15.5 9.7A10 10 0 0 1 19 12" />
      <circle cx="12" cy="19" r="1" fill="currentColor" stroke="none" />
    </template>
  </svg>
</template>

<style scoped>
.app-icon {
  /* style.css 里有一条全局 `img, svg, video, canvas { display: block }`（用来消除
     行内替换元素的基线缝隙）。图标在按钮里是跟着文字走的行内元素，
     `display: block` 会让它独占一行、把后面的文字挤到第二行 ——
     所以这里显式改回 inline-block（本规则的特异性高于那条元素选择器）。
     用 vertical-align 让图标坐在文字中线上；flex 容器里由 align-items 接管。 */
  display: inline-block;
  vertical-align: -0.15em;
  flex-shrink: 0;
}
</style>
