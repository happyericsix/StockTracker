<script setup>
/**
 * 应用壳：墨色侧栏（两主题恒定，本产品的身份锚点）+ 内容区。
 *
 * 逻辑全部从旧壳原样移植（零漂移）：
 * - 市场状态三态（宁缺毋错：拿不到显示"…"，不编造"开市"）
 * - 徽章取数：未读数来自 messageBus（SSE 单例），模拟盘运行数来自 /paper/overview，
 *   都是软失败：拿不到就不显示徽章，绝不让导航等数据
 * - /paper/overview 返回的是数组（每条带 paperEnabled），顶层没有 running 字段
 * - 切页即收移动端抽屉；登出时断开两条 SSE 总线
 */
import { ref, computed, onMounted, watch } from 'vue'
import { RouterView, RouterLink, useRoute, useRouter } from 'vue-router'
import {
  CandlestickChart, Radar, Bot, BookOpen, TrendingUp, Bell, MessageSquare,
  Database, Menu, LogOut, Sun, Moon,
} from 'lucide-vue-next'
import { messageBus } from '../composables/messageBus.js'
import { quoteBus } from '../composables/quoteBus.js'
import { useMarketStatus } from '../composables/useMarketStatus.js'
import { sessionBadgeFor } from '../utils/marketStatus.js'
import { useTheme } from '../composables/useTheme.js'
import { getProfile, completeOnboarding } from '../api/user.js'
import { getPaperOverview } from '../api/strategy.js'
import UsageGuide from './UsageGuide.vue'
import BeianFooter from './BeianFooter.vue'

const route = useRoute()
const router = useRouter()
const { toggle: toggleTheme } = useTheme()

/**
 * 合规备案信息已抽到 BeianFooter.vue（Login/Share 壳外页也要展示）。
 */

/** 导航唯一配置：图标、标题、徽章都从这一份来，桌面侧栏与移动抽屉共用 */
const navItems = [
  { to: '/dashboard', icon: CandlestickChart, label: '首页' },
  { to: '/news', icon: Radar, label: '资讯雷达' },
  { to: '/assistant', icon: Bot, label: '智能助手' },
  { to: '/strategies', icon: BookOpen, label: '策略库' },
  { to: '/paper', icon: TrendingUp, label: '模拟盘', badge: () => paperRunning.value },
  { to: '/alerts', icon: Bell, label: '预警' },
  { to: '/messages', icon: MessageSquare, label: '消息', badge: () => messageBus.unread, badgeKind: 'loud' },
  { to: '/memory', icon: Database, label: '记忆' },
]

const isActive = (to) => route.path === to || route.path.startsWith(`${to}/`)

// ---------- 市场状态（壳底一行，与行情卡同一单例） ----------
const { status: marketStatus, load: refreshMarket } = useMarketStatus()
const marketBadge = computed(() => {
  if (marketStatus.value == null) return '…'
  return sessionBadgeFor(marketStatus.value) || '开市'
})

// ---------- 用户 ----------
const username = ref('')
// 首次登录的使用说明引导：profile 里 onboardingCompleted === false 才弹
// （服务端三态折叠后只有新注册账号会是 false；老账号不打扰）
const showOnboarding = ref(false)
const loadUser = async () => {
  try {
    const res = await getProfile()
    username.value = res.data?.username || ''
    if (res.data?.onboardingCompleted === false) showOnboarding.value = true
  } catch {
    username.value = ''
  }
}

const finishOnboarding = async () => {
  showOnboarding.value = false
  try {
    await completeOnboarding()
  } catch {
    // 标记失败就让它失败：下次登录还会再弹一次（状态以服务端为准）
  }
}

// ---------- 模拟盘运行数（徽章；软失败不显示） ----------
const paperRunning = ref(0)
const loadPaper = async () => {
  try {
    const res = await getPaperOverview()
    const list = Array.isArray(res.data) ? res.data : []
    paperRunning.value = list.filter((item) => item?.paperEnabled).length
  } catch {
    paperRunning.value = 0
  }
}

// ---------- 移动端抽屉 ----------
const drawerOpen = ref(false)
const closeDrawer = () => { drawerOpen.value = false }
const onNavKeydown = (e) => {
  if (e.key === 'Escape') closeDrawer()
}

const logout = () => {
  messageBus.disconnect()
  quoteBus.disconnect()
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

// 切页即收抽屉：抽屉是临时导航，路由变化说明导航已完成
watch(() => route.path, closeDrawer)
</script>

<template>
  <div class="min-h-screen">
    <!-- 移动端顶栏（<1024px）：菜单 + 标题 + 消息铃铛 -->
    <header
      class="sticky top-0 z-30 flex h-13 items-center gap-2 border-b border-border bg-[color:var(--bg)]/85 px-3 backdrop-blur lg:hidden"
    >
      <button
        type="button"
        class="flex h-9 w-9 items-center justify-center rounded-lg text-ink-2 hover:bg-surface-2 hover:text-ink"
        aria-label="打开导航"
        @click="drawerOpen = true"
      >
        <Menu :size="20" :stroke-width="2" aria-hidden="true" />
      </button>
      <span class="flex-1 truncate text-[15px] font-semibold text-ink">Stock Tracker</span>
      <RouterLink
        to="/messages"
        class="relative flex h-9 w-9 items-center justify-center rounded-lg text-ink-2 hover:bg-surface-2 hover:text-ink"
        aria-label="消息中心"
      >
        <MessageSquare :size="20" :stroke-width="2" aria-hidden="true" />
        <span
          v-if="badgeText(messageBus.unread)"
          class="num absolute -right-0.5 -top-0.5 rounded-full bg-up px-1.5 text-[10.5px] font-semibold leading-4 text-white"
        >{{ badgeText(messageBus.unread) }}</span>
      </RouterLink>
    </header>

    <!-- 侧栏（桌面常驻 / 移动抽屉，同一份 DOM） -->
    <nav
      class="fixed inset-y-0 left-0 z-40 flex w-[232px] flex-col border-r border-sidebar-border bg-sidebar transition-transform duration-200 lg:translate-x-0"
      :class="drawerOpen ? 'translate-x-0' : '-translate-x-full'"
      aria-label="主导航"
      @keydown="onNavKeydown"
    >
      <RouterLink to="/dashboard" class="flex items-center gap-2.5 px-4 pb-2 pt-5" @click="closeDrawer">
        <span class="flex h-8 w-8 items-center justify-center rounded-lg bg-sidebar-accent-soft text-sidebar-accent">
          <CandlestickChart :size="19" :stroke-width="1.75" aria-hidden="true" />
        </span>
        <span class="text-[15px] font-semibold tracking-wide text-white">Stock Tracker</span>
      </RouterLink>

      <div class="flex-1 space-y-0.5 overflow-y-auto px-3 pb-3 pt-2">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="group flex h-9 items-center gap-2.5 rounded-lg px-2.5 text-[13px] transition-colors"
          :class="isActive(item.to)
            ? 'bg-sidebar-accent-soft font-medium text-sidebar-accent'
            : 'text-sidebar-dim hover:bg-white/[0.04] hover:text-sidebar-ink'"
          @click="closeDrawer"
        >
          <component :is="item.icon" :size="16.5" :stroke-width="1.75" aria-hidden="true" />
          {{ item.label }}
          <span
            v-if="item.badge && badgeText(item.badge())"
            class="num ml-auto rounded-full px-1.5 text-[10.5px] font-semibold leading-4"
            :class="item.badgeKind === 'loud' ? 'bg-up text-white' : 'bg-sidebar-accent-soft text-sidebar-accent'"
          >{{ badgeText(item.badge()) }}</span>
        </RouterLink>
      </div>

      <div class="space-y-1 border-t border-sidebar-border px-3 pb-4 pt-3">
        <p class="flex h-8 items-center gap-2 px-2.5 text-[12px] text-sidebar-dim" role="status">
          <!-- 语义状态点：开市=呼吸的实心暖点，其余（休市/未开盘/午间休市/已收盘/未知）=静态灰 -->
          <span
            class="h-1.5 w-1.5 rounded-full"
            :class="marketBadge === '开市' ? 'live-dot bg-[#3ed598]' : 'bg-sidebar-dim/60'"
            aria-hidden="true"
          ></span>
          {{ marketBadge }}
        </p>
        <div class="flex items-center gap-1">
          <!-- 用户行 = 进入个人中心 + 退出登录，两个独立交互元素平铺 -->
          <RouterLink
            to="/profile"
            class="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-lg px-2.5 text-[13px] text-sidebar-dim transition-colors hover:bg-white/[0.04] hover:text-sidebar-ink"
            @click="closeDrawer"
          >
            <span class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/[0.08] text-[11px] font-semibold text-sidebar-ink">
              {{ (username || '我').slice(0, 1).toUpperCase() }}
            </span>
            <span class="truncate">{{ username || '我的' }}</span>
          </RouterLink>
          <button
            type="button"
            class="flex h-9 w-9 items-center justify-center rounded-lg text-sidebar-dim transition-colors hover:bg-white/[0.04] hover:text-sidebar-ink"
            aria-label="切换深浅主题"
            @click="toggleTheme"
          >
            <Sun :size="15" :stroke-width="1.75" class="dark:hidden" aria-hidden="true" />
            <Moon :size="15" :stroke-width="1.75" class="hidden dark:block" aria-hidden="true" />
          </button>
          <button
            type="button"
            class="flex h-9 w-9 items-center justify-center rounded-lg text-sidebar-dim transition-colors hover:bg-white/[0.04] hover:text-sidebar-ink"
            aria-label="退出登录"
            @click="logout"
          >
            <LogOut :size="15" :stroke-width="1.75" aria-hidden="true" />
          </button>
        </div>
      </div>
    </nav>
    <div
      v-if="drawerOpen"
      class="fixed inset-0 z-30 bg-[color:var(--overlay)] lg:hidden"
      aria-hidden="true"
      @click="closeDrawer"
    ></div>

    <main class="lg:pl-[232px]">
      <div class="mx-auto max-w-[1200px] px-4 py-6 sm:px-6 lg:px-8">
        <RouterView />
      </div>

      <!-- 备案合规：ICP 与公安备案号都挂 footer（公安号审核通过后自动出现） -->
      <BeianFooter />
    </main>

    <!-- 首次登录的全屏使用说明（线性翻页，翻完才能开始使用） -->
    <UsageGuide :open="showOnboarding" mode="onboarding" @complete="finishOnboarding" />
  </div>
</template>
