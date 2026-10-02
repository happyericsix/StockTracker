<script setup>
/**
 * 应用壳：左侧导航 + 内容区。2026-10 重构，取代各页自带的顶部深色条。
 *
 * <h3>为什么是壳而不是每页各画一个头部</h3>
 * 旧结构每页一套 `.app-layout > header` + "← 返回"按钮——导航是全局事实，
 * 却由十个页面各自实现，样式漂移只是时间问题。壳一处实现、全站一致，
 * 这正是 Operate 模式要求的"同一视觉词汇跨屏"（impeccable operate.md）。
 *
 * <h3>布局</h3>
 * - 桌面（≥1024px）：固定左侧栏。第二中性层底色（比内容面稍冷），
 *   accent 只出现在"当前选中"与徽章，不做装饰。
 * - 移动（<1024px）：顶栏（菜单 + 标题 + 消息铃铛）+ 左滑抽屉。
 *   结构性响应（侧栏折叠成抽屉）而不是流式字号（operate.md 的响应式规则）。
 * - 抽屉开合 200ms ease-in-out（emil：屏幕内移动用强 ease-in-out，<300ms）。
 *
 * <h3>导航徽章的取数</h3>
 * 未读数来自 messageBus（SSE 单例，与消息中心共用）；模拟盘运行数来自
 * /paper/overview。都是软失败：拿不到就不显示徽章，绝不让导航等数据。
 */
import { ref, computed, onMounted, watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import AppIcon from './AppIcon.vue'
import { messageBus } from '../composables/messageBus.js'
import { useMarketStatus } from '../composables/useMarketStatus.js'
import { sessionBadgeFor } from '../utils/marketStatus.js'
import { getProfile } from '../api/user.js'
import { getPaperOverview } from '../api/strategy.js'

const route = useRoute()
const router = useRouter()

/** 导航唯一配置：图标、标题、徽章都从这一份来，桌面侧栏与移动抽屉共用 */
const navItems = [
  { to: '/dashboard', icon: 'chart', label: '首页' },
  { to: '/news', icon: 'search', label: '资讯雷达' },
  { to: '/assistant', icon: 'bot', label: '智能助手' },
  { to: '/strategies', icon: 'book', label: '策略库' },
  { to: '/paper', icon: 'trend-up', label: '模拟盘', badge: () => paperRunning.value },
  { to: '/alerts', icon: 'bell', label: '预警' },
  { to: '/messages', icon: 'message', label: '消息', badge: () => messageBus.unread, badgeKind: 'loud' },
  { to: '/memory', icon: 'database', label: '记忆' },
]

// ---------- 市场状态（壳底一行，与行情卡同一单例） ----------
// 注意导出名是 load：composable 没有 refresh。这里曾想当然写成 refresh，
// 运行时抛 TypeError 并中断 onMounted 后续的 messageBus.connect() —— 深链
// 直开非首页时 SSE 全挂，正是那次全面审查抓出来的 P0。
const { status: marketStatus, load: refreshMarket } = useMarketStatus()
// 三态，宁缺毋错：状态没到（加载中/拉取失败）显示中性的"…"，
// 不拿"开市"兜底 —— 把"不知道"渲染成"开市"是市场状态模块自己明令禁止的。
const marketBadge = computed(() => {
  if (marketStatus.value == null) return '…'
  return sessionBadgeFor(marketStatus.value) || '开市'
})

// ---------- 用户 ----------
const username = ref('')
const loadUser = async () => {
  try {
    const res = await getProfile()
    username.value = res.data?.username || ''
  } catch {
    username.value = ''
  }
}

// ---------- 模拟盘运行数（徽章；软失败不显示） ----------
const paperRunning = ref(0)
const loadPaper = async () => {
  try {
    const res = await getPaperOverview()
    // /paper/overview 返回的是**数组**，每条带 paperEnabled 布尔；
    // 顶层没有 running 字段（曾想当然读 res.data?.running，徽章因此永不显示）
    const list = Array.isArray(res.data) ? res.data : []
    paperRunning.value = list.filter((item) => item?.paperEnabled).length
  } catch {
    paperRunning.value = 0
  }
}

// ---------- 移动端抽屉 ----------
const drawerOpen = ref(false)
const openDrawer = () => { drawerOpen.value = true }
const closeDrawer = () => { drawerOpen.value = false }
const onNavKeydown = (e) => {
  if (e.key === 'Escape') closeDrawer()
}

const logout = () => {
  messageBus.disconnect()
  localStorage.removeItem('token')
  router.push('/login')
}

/** 徽章展示值：>99 折叠成 99+，0/空 不渲染 */
const badgeText = (count) => {
  const n = Number(count) || 0
  if (n <= 0) return ''
  return n > 99 ? '99+' : String(n)
}

onMounted(() => {
  loadUser()
  loadPaper()
  refreshMarket()
  messageBus.connect()
})

// 切页即收抽屉：抽屉是临时导航，路由变化说明导航已完成。
// 组件作用域内的 watch 随组件卸载自动停止，不必手动清理。
watch(() => route.path, closeDrawer)
</script>

<template>
  <div class="shell">
    <!-- 移动端顶栏：菜单 + 当前页 + 消息铃铛 -->
    <div class="shell-mobile-bar">
      <button type="button" class="shell-icon-btn" aria-label="打开导航" @click="openDrawer">
        <AppIcon name="menu" :size="20" :stroke-width="2" />
      </button>
      <span class="bar-title">Stock Tracker</span>
      <RouterLink to="/messages" class="shell-icon-btn" aria-label="消息中心">
        <AppIcon name="message" :size="20" :stroke-width="2" />
        <span v-if="badgeText(messageBus.unread)" class="shell-nav-badge num">
          {{ badgeText(messageBus.unread) }}
        </span>
      </RouterLink>
    </div>

    <!-- 侧栏（桌面常驻 / 移动抽屉，同一份 DOM） -->
    <nav class="shell-sidebar" :class="{ open: drawerOpen }" aria-label="主导航" @keydown="onNavKeydown">
      <RouterLink to="/dashboard" class="shell-brand" @click="closeDrawer">
        <span class="brand-mark"><AppIcon name="chart" :size="22" :stroke-width="2.2" /></span>
        Stock Tracker
      </RouterLink>

      <div class="shell-nav">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="shell-nav-item"
          @click="closeDrawer"
        >
          <AppIcon :name="item.icon" :size="18" :stroke-width="2" />
          {{ item.label }}
          <span
            v-if="item.badge && badgeText(item.badge())"
            class="shell-nav-badge num"
            :class="{ quiet: item.badgeKind !== 'loud' }"
          >{{ badgeText(item.badge()) }}</span>
        </RouterLink>
      </div>

      <div class="shell-side-foot">
        <p class="shell-market-line" role="status">
          <!-- 语义状态点：开市=实心绿，其余（休市/未开盘/午间休市/已收盘）=灰 -->
          <span class="shell-market-dot" :class="{ live: marketBadge === '开市' }" aria-hidden="true"></span>
          {{ marketBadge }}
        </p>
        <!-- 用户行 = 进入个人中心 + 退出登录，两个独立交互元素平铺，
             不把按钮嵌进链接里（interactive 嵌 interactive 违反 HTML 规范） -->
        <div class="shell-user-row">
          <RouterLink to="/profile" class="shell-user-link" @click="closeDrawer">
            <AppIcon name="user" :size="18" :stroke-width="2" />
            <span class="who">{{ username || '我的' }}</span>
          </RouterLink>
          <button type="button" class="shell-logout" aria-label="退出登录" @click="logout">
            <AppIcon name="logout" :size="16" :stroke-width="2" />
          </button>
        </div>
      </div>
    </nav>
    <div v-if="drawerOpen" class="shell-mask" aria-hidden="true" @click="closeDrawer"></div>

    <div class="shell-main">
      <RouterView />
    </div>
  </div>
</template>
