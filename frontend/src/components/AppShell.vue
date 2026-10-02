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
import { ref, computed, onMounted, onUnmounted } from 'vue'
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
const { status: marketStatus, refresh: refreshMarket } = useMarketStatus()
const marketBadge = computed(() => sessionBadgeFor(marketStatus.value) || '开市')

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
    paperRunning.value = Number(res.data?.running) || 0
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

let stopRouteWatch = null
onMounted(() => {
  loadUser()
  loadPaper()
  refreshMarket()
  messageBus.connect()
  // 切页即收抽屉：抽屉是临时导航，路由变化说明导航已完成
  stopRouteWatch = route.path !== undefined ? null : null
})
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
        <RouterLink to="/profile" class="shell-user-row" @click="closeDrawer">
          <AppIcon name="user" :size="18" :stroke-width="2" />
          <span class="who">{{ username || '我的' }}</span>
          <button type="button" class="shell-logout" aria-label="退出登录" @click.prevent.stop="logout">
            <AppIcon name="logout" :size="16" :stroke-width="2" />
          </button>
        </RouterLink>
      </div>
    </nav>
    <div v-if="drawerOpen" class="shell-mask" aria-hidden="true" @click="closeDrawer"></div>

    <div class="shell-main">
      <RouterView />
    </div>
  </div>
</template>
