<script setup>
/**
 * 消息中心（口径：CENTER_TYPES = 预警 + 策略哨兵/策略信 + 新闻预警，见 messageBus）。
 * 逻辑自旧版原样移植：tab 竞态防护（loadSeq）、本地时间窗口、整卡已读按钮层、
 * SSE 增量 + 断线重连补拉首页、滚动到底自动加载。
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  Bell, Bot, MessageSquare, CandlestickChart, Megaphone, Info, Newspaper,
  TrendingUp, TrendingDown, Percent, Sun, Snowflake, Sparkles, ArrowDownRight, ChevronRight,
} from 'lucide-vue-next'
import { getMessages, markRead, markAllRead } from '../api/messages.js'
import { messageBus, CENTER_TYPES } from '../composables/messageBus.js'
import Button from '../components/ui/button.vue'
import Empty from '../components/ui/empty.vue'
import Skeleton from '../components/ui/skeleton.vue'

const router = useRouter()

const messages = ref([])
const page = ref(0)
const size = 20
const totalPages = ref(0)
const totalElements = ref(0)
const loading = ref(false)
const loadingMore = ref(false)
const loadError = ref('')
const hasMore = ref(true)
let unsubscribe = null
let unsubscribeReconnect = null

const activeTab = ref('all')

const symbolFilter = ref('')
const timeRange = ref('all')

const tabs = [
  { key: 'all', label: '全部' },
  { key: 'alert', label: '预警' },
]

const timeRanges = [
  { key: '7d', label: '最近 7 天', days: 7 },
  { key: '30d', label: '最近 30 天', days: 30 },
  { key: '90d', label: '最近 90 天', days: 90 },
  { key: 'all', label: '全部时间', days: null },
]

// 类型 → lucide 图标 + 中文标签
const typeMeta = (type) => {
  switch (type) {
    case 'ALERT': return { icon: Bell, label: '预警提醒' }
    case 'NEWS_ALERT': return { icon: Newspaper, label: '新闻预警' }
    case 'CHAT_BOT': return { icon: Bot, label: '智能助手' }
    case 'CHAT_USER': return { icon: MessageSquare, label: '我' }
    case 'PAPER_REPORT': return { icon: CandlestickChart, label: '模拟盘复盘' }
    case 'STRATEGY_SENTINEL': return { icon: Megaphone, label: '策略哨兵' }
    default: return { icon: Info, label: '系统通知' }
  }
}

const cardHitLabel = (m) => (m.read
  ? `${typeMeta(m.type).label}消息（已读）`
  : `标记为已读：${typeMeta(m.type).label}消息`)

// 预警条件 → lucide 图标 + 文案
const CONDITION_META = {
  price_above: { icon: TrendingUp, label: '价格突破' },
  price_below: { icon: TrendingDown, label: '价格跌破' },
  pnl_percent: { icon: Percent, label: '盈亏百分比' },
  rsi_overbought: { icon: Sun, label: 'RSI 超买' },
  rsi_oversold: { icon: Snowflake, label: 'RSI 超卖' },
  macd_golden_cross: { icon: Sparkles, label: 'MACD 金叉' },
  macd_death_cross: { icon: ArrowDownRight, label: 'MACD 死叉' },
}

const parseMetadata = (m) => {
  if (m.type !== 'ALERT' || !m.metadata) return null
  try {
    const meta = JSON.parse(m.metadata)
    return {
      conditionType: meta.conditionType || '',
      conditionIcon: (CONDITION_META[meta.conditionType] || {}).icon || Info,
      conditionLabel: (CONDITION_META[meta.conditionType] || {}).label || meta.conditionType || '未知',
      triggerPrice: typeof meta.triggerPrice === 'number' ? meta.triggerPrice : null,
      triggerValue: typeof meta.triggerValue === 'number' ? meta.triggerValue : null,
      threshold: typeof meta.threshold === 'number' ? meta.threshold : null,
    }
  } catch {
    return null
  }
}

const fmt = (n) => (n == null ? '-' : Number(n).toFixed(2))

const formatTime = (iso) => {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  return sameDay ? hm : `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`
}

// 客户端 tab 过滤（type）
const filtered = computed(() => {
  if (activeTab.value === 'alert') {
    return messages.value.filter((m) => m.type === 'ALERT')
  }
  return messages.value
})

const symbolFiltered = computed(() => {
  if (activeTab.value !== 'alert') return filtered.value
  const q = symbolFilter.value.trim().toUpperCase()
  if (!q) return filtered.value
  return filtered.value.filter((m) => (m.relatedSymbol || '').toUpperCase().includes(q))
})

const knownSymbols = computed(() => {
  const set = new Set()
  messages.value.forEach((m) => {
    if (m.relatedSymbol) set.add(m.relatedSymbol)
  })
  return Array.from(set).sort()
})

let loadSeq = 0
const load = async () => {
  const seq = ++loadSeq
  loading.value = true
  loadError.value = ''
  page.value = 0
  messages.value = []
  hasMore.value = true
  await loadPage(seq)
  if (seq === loadSeq) loading.value = false
}

const reload = () => load()

const loadPage = async (seq = loadSeq) => {
  if (loadingMore.value) return
  loadingMore.value = true
  try {
    const params = { page: page.value, size }
    if (activeTab.value === 'alert') params.type = 'ALERT'

    const range = timeRanges.find((r) => r.key === timeRange.value)
    if (range && range.days) {
      const since = new Date(Date.now() - range.days * 24 * 60 * 60 * 1000)
      // 手工拼本地时间：toISOString 是 UTC，后端按本地解析会偏移窗口
      const p = (n) => String(n).padStart(2, '0')
      params.since = `${since.getFullYear()}-${p(since.getMonth() + 1)}-${p(since.getDate())}` +
        `T${p(since.getHours())}:${p(since.getMinutes())}:${p(since.getSeconds())}`
    }

    const res = await getMessages(params)
    if (seq !== loadSeq) return
    const data = res.data || {}
    const list = data.content || []
    messages.value = page.value === 0 ? list : [...messages.value, ...list]
    totalPages.value = data.totalPages || 0
    totalElements.value = data.totalElements || 0
    hasMore.value = page.value < (data.totalPages || 0) - 1
  } catch {
    if (seq !== loadSeq) return
    // 只把首屏失败升级成页面级错误；翻页失败时已有内容还在
    if (page.value === 0) {
      loadError.value = '请检查网络连接后重试，或稍后再试。'
    }
  } finally {
    loadingMore.value = false
  }
}

const loadMore = async () => {
  if (!hasMore.value || loadingMore.value) return
  page.value += 1
  await loadPage()
}

const onBusMessage = (msg) => {
  if (!msg || !msg.id) return
  // 与列表接口同一口径（CENTER_TYPES），否则 SSE 推来的消息要等刷新才出现
  if (!CENTER_TYPES.includes(msg.type)) return
  const idx = messages.value.findIndex((m) => m.id === msg.id)
  if (idx >= 0) {
    messages.value[idx] = msg
  } else {
    messages.value.unshift(msg)
    totalElements.value += 1
  }
}

const handleRead = async (m) => {
  if (m.read) return
  m.read = true
  messageBus.unread = Math.max(0, messageBus.unread - 1)
  try {
    await markRead(m.id)
  } catch {
    m.read = false
    messageBus.unread += 1
  }
}

const handleReadAll = async () => {
  // 当前页可能一条未读都没有（未读在更深的分页或被过滤掉），但服务端未读数还在——
  // 只看列表会漏判，红点点不掉。列表与未读数都清零才真的无事可做。
  if (!messages.value.some((m) => !m.read) && messageBus.unread <= 0) return
  messages.value.forEach((m) => { m.read = true })
  messageBus.unread = 0
  try {
    await markAllRead()
  } catch {
    // 失败就向服务端对账，别让本地清零掩盖真实未读
    messageBus.refreshUnread()
  }
}

const onTabChange = () => load()
const onTimeRangeChange = () => load()

const viewChart = (symbol) => {
  if (!symbol) return
  router.push(`/chart/${encodeURIComponent(symbol.toUpperCase())}`)
}

const goChat = () => router.push('/assistant')

const onReconnect = async () => {
  await load()
}

const onScroll = async (e) => {
  const el = e.target
  if (!el) return
  if (el.scrollHeight - el.scrollTop - el.clientHeight < 100) {
    await loadMore()
  }
}

onMounted(() => {
  load()
  messageBus.connect()
  unsubscribe = messageBus.subscribe(onBusMessage)
  unsubscribeReconnect = messageBus.subscribeReconnect(onReconnect)
})

onUnmounted(() => {
  if (unsubscribe) unsubscribe()
  if (unsubscribeReconnect) unsubscribeReconnect()
})
</script>

<template>
  <div class="flex min-h-screen min-h-[100dvh] flex-col">
    <div class="mx-auto w-full max-w-[700px] px-4 pt-6 sm:px-6">
      <div class="mb-4 flex items-center justify-between gap-3">
        <h1 class="text-[20px] font-semibold text-ink">消息中心</h1>
        <Button size="sm" variant="ghost" @click="handleReadAll">全部已读</Button>
      </div>

      <!-- 筛选按钮（同一列表的不同视图，不是 ARIA tab）：aria-pressed 表选中 -->
      <div class="flex border-b border-border">
        <button
          v-for="t in tabs"
          :key="t.key"
          type="button"
          class="relative flex h-10 flex-1 items-center justify-center border-b-2 text-[13.5px] transition-colors"
          :class="activeTab === t.key ? 'border-accent font-semibold text-accent' : 'border-transparent text-ink-2 hover:text-ink'"
          :aria-pressed="activeTab === t.key"
          @click="activeTab = t.key; onTabChange()"
        >
          {{ t.label }}
        </button>
      </div>

      <!-- 预警 tab：symbol 过滤 + 时间范围 -->
      <div v-if="activeTab === 'alert'" class="flex flex-col gap-2 pb-3 pt-3">
        <input
          v-model="symbolFilter"
          aria-label="按股票代码过滤消息"
          placeholder="按股票代码过滤"
          list="known-symbols"
          class="h-[34px] w-full rounded-lg border border-border bg-surface px-3 text-[13px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
        >
        <datalist id="known-symbols">
          <option v-for="s in knownSymbols" :key="s" :value="s" />
        </datalist>
        <div class="flex flex-wrap gap-1">
          <button
            v-for="r in timeRanges"
            :key="r.key"
            type="button"
            class="h-[26px] rounded-full px-3 text-[12px] transition-colors max-sm:h-[34px]"
            :class="timeRange === r.key ? 'bg-accent text-on-accent' : 'text-ink-2 hover:bg-surface-2'"
            :aria-pressed="timeRange === r.key"
            @click="timeRange = r.key; onTimeRangeChange()"
          >
            {{ r.label }}
          </button>
        </div>
      </div>
    </div>

    <main
      class="mx-auto flex w-full max-w-[700px] flex-1 flex-col gap-2.5 px-4 pb-8 pt-2 sm:px-6"
      @scroll="onScroll"
    >
      <div v-if="loading" class="space-y-2.5">
        <div v-for="i in 4" :key="i" class="flex gap-3 rounded-xl border border-border bg-surface p-3.5">
          <Skeleton width="34px" height="34px" />
          <div class="flex-1 space-y-2">
            <Skeleton width="30%" height="12px" />
            <Skeleton width="85%" height="14px" />
          </div>
        </div>
      </div>

      <!-- 加载失败说出来并给恢复路径 -->
      <div
        v-else-if="loadError"
        class="flex flex-col items-center gap-2.5 rounded-xl border border-border bg-surface py-12 text-center"
        role="alert"
      >
        <p class="text-[15px] font-semibold text-ink">消息加载失败</p>
        <p class="text-[13px] text-ink-2">{{ loadError }}</p>
        <Button variant="primary" size="sm" @click="reload">重新加载</Button>
      </div>

      <Empty v-else-if="symbolFiltered.length === 0" title="暂无消息" :desc="symbolFilter ? '试试清空过滤条件' : '预警触发和策略哨兵的通报会出现在这里。'">
        <template #icon><Bell :size="20" :stroke-width="1.5" /></template>
      </Empty>

      <div
        v-for="m in symbolFiltered"
        :key="m.id"
        class="relative flex items-start gap-2.5 rounded-xl border p-3.5"
        :class="m.read ? 'border-border bg-surface' : 'border-accent/40 bg-accent-soft/60'"
      >
        <!-- 整卡点击标记已读：铺满卡片的原生按钮层（键盘可达，且不与内部按钮嵌套） -->
        <button
          type="button"
          class="absolute inset-0 z-0 rounded-xl"
          :aria-label="cardHitLabel(m)"
          @click="handleRead(m)"
        ></button>
        <span class="mt-0.5 flex h-8.5 w-8.5 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-ink-2">
          <component :is="typeMeta(m.type).icon" :size="17" :stroke-width="1.75" aria-hidden="true" />
        </span>
        <div class="z-[1] min-w-0 flex-1">
          <div class="flex items-center justify-between gap-2">
            <span class="text-[12px] font-semibold text-accent">{{ typeMeta(m.type).label }}</span>
            <span class="num text-[11px] text-ink-3">{{ formatTime(m.createdAt) }}</span>
          </div>
          <div class="mt-1 whitespace-pre-wrap break-words text-[13.5px] leading-relaxed text-ink">{{ m.content }}</div>

          <div v-if="m.type === 'ALERT' && m.relatedSymbol" class="mt-1.5 flex items-center gap-2.5">
            <span class="num text-[11.5px] text-warn">#{{ m.relatedSymbolName || m.relatedSymbol }}</span>
            <button
              type="button"
              class="relative z-[1] flex items-center gap-1 rounded-full border border-accent px-2 py-0.5 text-[11px] text-accent transition-colors hover:bg-accent hover:text-on-accent"
              @click.stop="viewChart(m.relatedSymbol)"
            >
              <CandlestickChart :size="12" :stroke-width="2" aria-hidden="true" /> 看 K 线
            </button>
          </div>
          <div v-else-if="m.relatedSymbol" class="num mt-1 text-[11.5px] text-warn">#{{ m.relatedSymbolName || m.relatedSymbol }}</div>

          <!-- ALERT metadata 详情 -->
          <div
            v-if="m.type === 'ALERT' && parseMetadata(m)"
            class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg bg-surface-2 px-2.5 py-1.5 text-[12px] text-ink-2"
          >
            <span class="flex items-center gap-1 rounded-full bg-warn px-2 py-0.5 text-[11px] font-semibold text-white">
              <component :is="parseMetadata(m).conditionIcon" :size="12" :stroke-width="2" aria-hidden="true" />
              {{ parseMetadata(m).conditionLabel }}
            </span>
            <span v-if="parseMetadata(m).triggerPrice != null">触发价 <b class="num font-semibold text-ink">{{ fmt(parseMetadata(m).triggerPrice) }}</b></span>
            <span v-if="parseMetadata(m).triggerValue != null">指标值 <b class="num font-semibold text-ink">{{ fmt(parseMetadata(m).triggerValue) }}</b></span>
            <span v-if="parseMetadata(m).threshold != null">阈值 <b class="num font-semibold text-ink">{{ fmt(parseMetadata(m).threshold) }}</b></span>
          </div>
        </div>
        <span v-if="!m.read" class="absolute right-3.5 top-3.5 h-2 w-2 rounded-full bg-up" aria-hidden="true"></span>
      </div>

      <div v-if="loadingMore" class="py-3 text-center text-[12px] text-ink-3">加载中...</div>
      <div v-else-if="!hasMore && symbolFiltered.length > 0" class="num py-3 text-center text-[12px] text-ink-3">
        共 {{ totalElements }} 条，已加载完<span v-if="symbolFilter">（已过滤显示 {{ symbolFiltered.length }}）</span>
      </div>

      <button
        type="button"
        class="mt-1.5 flex items-center justify-between rounded-xl bg-accent px-4 py-3.5 text-[13.5px] font-medium text-on-accent transition-colors hover:bg-accent-hover"
        @click="goChat"
      >
        <span class="flex items-center gap-2"><MessageSquare :size="16" :stroke-width="1.75" aria-hidden="true" /> 去和智能助手聊聊</span>
        <ChevronRight :size="17" :stroke-width="2" aria-hidden="true" />
      </button>
    </main>
  </div>
</template>
