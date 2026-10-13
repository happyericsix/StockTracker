<script setup>
/**
 * 个股 K 线页：图表 + 事件锚定 + 散户情绪 + 当前怎么看。
 * 逻辑自旧版原样移植（零漂移）：事件竖线双向联动（原生鼠标事件 + 坐标换算，
 * 不依赖 ECharts markLine 交互）、解读后台轮询（2s/40 轮上限）、综合解读
 * "空手而归补问一次"、K 线主链路 loadDataSeq 竞态防护、市场口径副标题。
 * 令牌名已换到墨阶体系（--up、--down、--accent、--chart 系列与 --event 系列）。
 */
import { ref, onMounted, onUnmounted, nextTick, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Megaphone, Newspaper, FileText, Globe } from 'lucide-vue-next'
import { getHistory, getMinuteKline, getStock } from '../api/stock.js'
import { getStockEvents, getStockRead } from '../api/news.js'
import { getSentiment } from '../api/market.js'
import { anchorDateFor as anchorOnAxis } from '../utils/newsAnchor.js'
import { useMarketStatus } from '../composables/useMarketStatus.js'
import ChatDrawer from '../components/ChatDrawer.vue'
import InfoTip from '../components/InfoTip.vue'
import Badge from '../components/ui/badge.vue'
import Button from '../components/ui/button.vue'
import Skeleton from '../components/ui/skeleton.vue'
import * as echarts from 'echarts'

const route = useRoute()
const router = useRouter()

const chatOpen = ref(false)
const chatFocus = ref(null)
const askAbout = (event) => {
  chatFocus.value = event
  chatOpen.value = true
}

// ---------- 状态 ----------
const symbol = ref((route.params.symbol || '600519').toUpperCase())

const period = ref('day')
const periods = [
  { label: '日K', value: 'day' },
  { label: '周K', value: 'week' },
  { label: '月K', value: 'month' },
  { label: '分时', value: 'minute' },
]

const minutePeriod = ref(5)
const minuteOptions = [
  { label: '1分', value: 1 }, { label: '5分', value: 5 }, { label: '15分', value: 15 },
  { label: '30分', value: 30 }, { label: '60分', value: 60 },
]

const range = ref(180)
const ranges = computed(() => {
  if (period.value === 'day') return [
    { label: '1月', value: 30 }, { label: '3月', value: 90 }, { label: '6月', value: 180 },
    { label: '1年', value: 365 }, { label: '2年', value: 730 }, { label: '全部', value: 1000 },
  ]
  if (period.value === 'week') return [
    { label: '半年', value: 26 }, { label: '1年', value: 52 }, { label: '3年', value: 156 },
    { label: '5年', value: 260 }, { label: '10年', value: 520 }, { label: '全部', value: 1000 },
  ]
  if (period.value === 'month') return [
    { label: '1年', value: 12 }, { label: '3年', value: 36 }, { label: '5年', value: 60 },
    { label: '10年', value: 120 }, { label: '20年', value: 240 }, { label: '全部', value: 1000 },
  ]
  return [
    { label: '1天', value: 48 }, { label: '2天', value: 96 },
    { label: '5天', value: 240 }, { label: '1周', value: 480 },
  ]
})

const chartType = ref('candlestick')
const chartTypes = [
  { label: '蜡烛', value: 'candlestick' },
  { label: '折线', value: 'line' },
  { label: 'OHLC', value: 'ohlc' },
]

const indicators = ref(null)
const loading = ref(false)
const error = ref('')
const stockInfo = ref(null)
const lastUpdate = ref('')

// ---------- 事件区 ----------
const events = ref([])
const eventsError = ref('')
const eventsLoaded = ref(false)
// 后台还在抓取/补解读 → 继续轮询（必须来自后端，不能用"转了多久"猜）
const eventsAnalyzing = ref(false)
const eventsAnalyzedCount = ref(0)
const eventsPendingCount = ref(0)
const stockRead = ref('')
const stockReadLoading = ref(false)
const stockReadAttempted = ref(false)
const stockReadRetried = ref(false)
const showAllEvents = ref(false)
const activeEventId = ref(null)
// 图表实际绘制用的交易日序列（切片后）：事件锚点必须落在这条轴上
const chartDates = ref([])
const EVENT_PREVIEW = 3

const EVENT_POLL_INTERVAL_MS = 2000
const EVENT_POLL_MAX_ROUNDS = 40

const market = useMarketStatus()
const marketNotice = market.restNotice
const restNoticeParts = market.restNoticeParts

const EVENT_HIT_TOLERANCE_PX = 14
const EVENT_WINDOW_DAYS = 90
const MARK_LIMIT = 12

const chartRef = ref(null)
let chart = null

// ---------- 令牌读取（canvas 不认 CSS 变量名） ----------
// 缓存键带上当前主题：SPA 内切换深浅色后不刷新页面，旧缓存会把浅色值带回深色图上。
const tokenCache = new Map()
const token = (name, fallback) => {
  const themeKey = (typeof document !== 'undefined' && document.documentElement.classList.contains('dark'))
    ? 'dark:' : 'light:'
  const key = themeKey + name
  if (!tokenCache.has(key)) {
    let value = fallback
    try {
      value = getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
    } catch {
      value = fallback
    }
    tokenCache.set(key, value)
  }
  return tokenCache.get(key)
}

const handleResize = () => chart?.resize()

// 鼠标落在哪条事件竖线附近：原生事件 + ECharts 坐标换算（不依赖 markLine 事件派发）
const nearestMarkedEvent = (event) => {
  if (!chart || !chartRef.value || !markGroups.value.length) return null
  const rect = chartRef.value.getBoundingClientRect()
  const pixelX = event.clientX - rect.left
  const pixelY = event.clientY - rect.top
  if (!chart.containPixel({ gridIndex: 0 }, [pixelX, pixelY])) return null

  let best = null
  for (const group of markGroups.value) {
    const lineX = chart.convertToPixel({ xAxisIndex: 0 }, group.anchor)
    if (typeof lineX !== 'number' || Number.isNaN(lineX)) continue
    const distance = Math.abs(lineX - pixelX)
    if (distance <= EVENT_HIT_TOLERANCE_PX && (!best || distance < best.distance)) {
      best = { item: group.representative, distance }
    }
  }
  return best ? best.item : null
}

const onChartMouseMove = (event) => {
  const hit = nearestMarkedEvent(event)
  const id = hit ? hit.id : null
  if (hoveredEventId.value !== id) hoveredEventId.value = id
}

const onChartMouseLeave = () => {
  hoveredEventId.value = null
}

const onChartClick = (event) => {
  const hit = nearestMarkedEvent(event)
  if (!hit) return
  focusEvent(hit)
}

const disposeChart = () => {
  if (chart) {
    chart.dispose()
    chart = null
  }
  window.removeEventListener('resize', handleResize)
}

// ---------- 工具函数 ----------
const calcMA = (data, n) => {
  const result = []
  for (let i = 0; i < data.length; i++) {
    if (i < n - 1) result.push('-')
    else {
      let sum = 0
      for (let j = i - n + 1; j <= i; j++) sum += +data[j].close
      result.push(+(sum / n).toFixed(2))
    }
  }
  return result
}

const calcIndicators = (data) => {
  if (data.length < 20) return null
  const closes = data.map((d) => +d.close)

  const rsi = (() => {
    let gains = 0, losses = 0
    for (let i = closes.length - 14; i < closes.length; i++) {
      const diff = closes[i] - closes[i - 1]
      if (diff > 0) gains += diff
      else losses -= diff
    }
    if (gains + losses === 0) return 50
    const rs = gains / 14 / (losses / 14 || 1e-9)
    return +(100 - 100 / (1 + rs)).toFixed(2)
  })()

  const ema = (arr, n) => {
    const k = 2 / (n + 1)
    const out = [arr[0]]
    for (let i = 1; i < arr.length; i++) out.push(arr[i] * k + out[i - 1] * (1 - k))
    return out
  }
  const ema12 = ema(closes, 12)
  const ema26 = ema(closes, 26)
  const dif = +(ema12[ema12.length - 1] - ema26[ema26.length - 1]).toFixed(3)
  const difArr = ema12.map((v, i) => v - ema26[i])
  const deaArr = ema(difArr, 9)
  const dea = +deaArr[deaArr.length - 1].toFixed(3)
  const hist = +((dif - dea) * 2).toFixed(3)

  const n = 20
  const slice = closes.slice(-n)
  const ma = slice.reduce((a, b) => a + b, 0) / n
  const variance = slice.reduce((a, b) => a + (b - ma) ** 2, 0) / n
  const std = Math.sqrt(variance)
  const upper = +(ma + 2 * std).toFixed(2)
  const middle = +ma.toFixed(2)
  const lower = +(ma - 2 * std).toFixed(2)

  return { rsi, macd: { dif, dea, hist }, bollinger: { upper, middle, lower } }
}

// ---------- 事件区：锚点、标记与归因 ----------
const errorMessage = (e, fallback) => e?.response?.data?.message || fallback

const SOURCE_META = {
  1: { icon: Megaphone, label: '公告' },
  2: { icon: Newspaper, label: '媒体' },
  3: { icon: FileText, label: '研报' },
  4: { icon: Globe, label: '舆情' },
}
const sourceMeta = (level) => SOURCE_META[level] || { icon: FileText, label: '资讯' }

const eventCounts = computed(() => {
  const counts = { notice: 0, media: 0, report: 0 }
  for (const item of events.value) {
    if (item.sourceLevel === 1) counts.notice += 1
    else if (item.sourceLevel === 2) counts.media += 1
    else if (item.sourceLevel === 3) counts.report += 1
  }
  return counts
})

const anchorDateFor = (event) => anchorOnAxis(event.publishedAt, chartDates.value)

const anchoredEvents = computed(() => {
  const dates = chartDates.value
  if (!dates.length) return []
  const first = dates[0]
  const last = dates[dates.length - 1]
  return events.value
    .map((event) => ({ event, anchor: anchorDateFor(event) }))
    .filter(({ anchor }) => anchor && anchor >= first && anchor <= last)
})

const IMPACT_RANK = { high: 0, medium: 1, low: 2 }

/** 图上标注的事件（强度前 MARK_LIMIT，按日期分组，代表事件三处同源） */
const markGroups = computed(() => {
  const ranked = [...anchoredEvents.value].sort(
    (a, b) => (IMPACT_RANK[a.event.impactLevel] ?? 3) - (IMPACT_RANK[b.event.impactLevel] ?? 3))
  const byDate = new Map()
  for (const { event, anchor } of ranked.slice(0, MARK_LIMIT)) {
    if (!byDate.has(anchor)) byDate.set(anchor, [])
    byDate.get(anchor).push(event)
  }
  const groups = []
  for (const [anchor, evts] of byDate) {
    const representative = evts.reduce((best, item) =>
      (eventNumberOf(item) || Number.MAX_SAFE_INTEGER) <
      (eventNumberOf(best) || Number.MAX_SAFE_INTEGER) ? item : best, evts[0])
    groups.push({ anchor, events: evts, representative })
  }
  return groups
})

const buildMarkLine = () => {
  const focusId = hoveredEventId.value ?? activeEventId.value
  const data = []
  for (const { anchor, events: evts, representative } of markGroups.value) {
    const focused = evts.some((e) => e.id === focusId)
    const color = colorForEvent(representative)
    data.push({
      xAxis: anchor,
      eventDate: anchor,
      lineStyle: {
        color,
        type: directionLineTypeForGroup(evts),
        width: focused ? 4 : 2,
        opacity: focused ? 1 : 0.75,
      },
      // 线上不写标签：让人数编号是把问题换成另一道题
      label: { show: false },
    })
  }
  return { symbol: ['none', 'none'], silent: true, animation: false, data }
}

/** 只 merge 标记不重画整图（重画会重置用户的 dataZoom 区间） */
const updateMarks = () => {
  if (!chart) return
  try {
    chart.setOption({ series: [{ markLine: buildMarkLine() }] })
  } catch (e) {
    console.warn('事件标记更新失败', e)
  }
}

const changeValue = computed(() => {
  const raw = stockInfo.value?.changePercent
  if (raw === null || raw === undefined || raw === '') return null
  const value = Number.parseFloat(raw)
  return Number.isFinite(value) ? value : null
})

const lastClosePrice = ref('')
const sessionBadge = market.sessionBadge

const narrowMedia = typeof window !== 'undefined' && window.matchMedia
  ? window.matchMedia('(max-width: 640px)')
  : null
const isNarrow = ref(!!narrowMedia?.matches)
const onNarrowChange = (e) => { isNarrow.value = e.matches }

/** 副标题：休市/未开盘时把"这是哪一天的价"说清楚 */
const chartSubtext = computed(() => {
  const priceStr = stockInfo.value?.price
    ? `¥${stockInfo.value.price}`
    : (lastClosePrice.value ? `¥${lastClosePrice.value}` : 'N/A')
  const change = changeValue.value
  const changeStr = change === null ? null : `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`
  const anotherDay = market.quoteIsFromAnotherDay.value
  if (isNarrow.value) {
    return [
      priceStr === 'N/A' ? null : priceStr,
      changeStr,
      anotherDay ? `${market.quoteDateLabel.value}收盘` : `更新 ${stockInfo.value?.lastUpdated || lastUpdate.value}`,
    ].filter(Boolean).join(' · ')
  }
  return [
    sessionBadge.value,
    `当前价: ${priceStr}`,
    change === null ? null : `涨跌: ${change >= 0 ? '+' : ''}${change.toFixed(2)}%`,
    anotherDay ? `最近交易日 ${market.quoteDateLabel.value} 收盘` : `更新: ${stockInfo.value?.lastUpdated || lastUpdate.value}`,
  ].filter(Boolean).join('  |  ')
})

watch(chartSubtext, (value) => {
  if (!chart) return
  try {
    chart.setOption({ title: { subtext: value } })
  } catch (e) {
    console.warn('副标题更新失败', e)
  }
})

/** 一句话归因：确定性规则，不是 LLM 输出 */
const attributionNote = computed(() => {
  if (!eventsLoaded.value) return ''
  const list = events.value
  if (!list.length) return '近 30 天无重大事件，波动大概率跟板块/大盘走'
  const bull = list.filter((e) => e.direction === '利好').length
  const bear = list.filter((e) => e.direction === '利空').length
  if (!bull && !bear) return '近期事件多为程序性披露，未指向明确方向'
  const change = changeValue.value
  if (change === null) return `近期事件偏${bull >= bear ? '多' : '空'}`
  if ((change >= 0 && bull > bear) || (change < 0 && bear > bull)) return '与近期事件方向一致'
  if ((change >= 0 && bear > bull) || (change < 0 && bull > bear)) return '与近期事件方向相反，可能已提前反应或另有驱动'
  return '多空事件相当，方向不明'
})

const previewEvents = computed(() => (showAllEvents.value ? events.value : events.value.slice(0, EVENT_PREVIEW)))

// ---------- 事件色板：竖线 ↔ 列表条目（颜色=身份，线型=方向） ----------
const EVENT_COLOR_TOKENS = ['--event-1', '--event-2', '--event-3', '--event-4', '--event-5']

const eventNumberOf = (event) => {
  const index = events.value.findIndex((item) => item.id === event.id)
  return index < 0 ? 0 : index + 1
}

const colorForNumber = (number) =>
  token(EVENT_COLOR_TOKENS[(Math.max(number, 1) - 1) % EVENT_COLOR_TOKENS.length], '#3b82f6')

const colorForEvent = (event) => colorForNumber(eventNumberOf(event))

const directionLineType = (direction) => {
  if (direction === '利好') return 'solid'
  if (direction === '利空') return 'dashed'
  return 'dotted'
}

const directionLineTypeForGroup = (list) => directionLineType(list[0]?.direction)

const hoveredEventId = ref(null)

const focusedEvent = computed(() => {
  const id = hoveredEventId.value ?? activeEventId.value
  if (id == null) return null
  return events.value.find((item) => item.id === id) || null
})

const focusCaption = computed(() => {
  const event = focusedEvent.value
  if (!event) return ''
  const date = formatEventTime(event.publishedAt, event.dateOnly)
  const source = event.sourceName || sourceMeta(event.sourceLevel).label
  return `${event.title}　·　${source}　·　${date}　·　${directionLabel(event)}`
})

const directionTone = (direction) => (direction === '利好' ? 'up' : direction === '利空' ? 'down' : 'flat')

const directionLabel = (event) => {
  // direction 为 null 是"信息不足"，不是"中性"
  if (event.direction) return event.direction
  return event.analyzed ? '不判断方向' : '未解读'
}

const formatEventTime = (iso, dateOnly = false) => {
  const text = String(iso || '')
  if (!text) return '时间未知'
  if (dateOnly) return text.slice(0, 10)
  return text.slice(5, 16).replace('T', ' ')
}

const focusEvent = (event) => {
  activeEventId.value = activeEventId.value === event.id ? null : event.id
  updateMarks()
}

const hoverEvent = (event, on) => {
  hoveredEventId.value = on ? event.id : null
  updateMarks()
}

const focusEventAndReveal = (event) => {
  if (!event) return
  activeEventId.value = event.id
  showAllEvents.value = true
  updateMarks()
  nextTick(() => {
    try {
      document.getElementById(`event-${event.id}`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    } catch (e) {
      console.warn('事件定位失败', e)
    }
  })
}

// ---------- 数据加载 ----------
const loadEvents = async (sym) => {
  stopEventPolling()
  events.value = []
  eventsError.value = ''
  eventsLoaded.value = false
  eventsAnalyzing.value = false
  eventsAnalyzedCount.value = 0
  eventsPendingCount.value = 0
  showAllEvents.value = false
  activeEventId.value = null
  stockRead.value = ''
  stockReadAttempted.value = false
  stockReadRetried.value = false

  // 综合解读与事件流并行发起（顺序发等于把两次等待叠起来）
  void loadStockRead(sym)

  try {
    const res = await getStockEvents(sym, EVENT_WINDOW_DAYS)
    applyTimeline(res?.data)
  } catch (e) {
    eventsError.value = errorMessage(e, '资讯加载失败，稍后重试')
  } finally {
    eventsLoaded.value = true
    updateMarks()
  }
  maybeRetryStockRead()
  if (eventsAnalyzing.value) {
    startEventPolling(sym)
  }
}

const applyTimeline = (data) => {
  const payload = Array.isArray(data) ? { events: data } : (data || {})
  events.value = Array.isArray(payload.events) ? payload.events : []
  eventsAnalyzing.value = payload.analyzing === true
  eventsAnalyzedCount.value = Number(payload.analyzedCount) || 0
  eventsPendingCount.value = Number(payload.pendingCount) || 0
}

/** 第一次综合解读空手而归时，等解读落地后补问一次（冷标的实测必要） */
const maybeRetryStockRead = () => {
  if (stockRead.value || stockReadLoading.value || stockReadRetried.value) return
  if (!stockReadAttempted.value) return
  if (eventsAnalyzing.value) return
  if (!eventsAnalyzedCount.value) return
  stockReadRetried.value = true
  void loadStockRead(symbol.value)
}

let eventPollTimer = null
let eventPollRounds = 0

const stopEventPolling = () => {
  if (eventPollTimer) {
    clearTimeout(eventPollTimer)
    eventPollTimer = null
  }
  eventPollRounds = 0
}

// 递归 setTimeout：同一时刻只有一个在途请求，返回顺序不会打架
const startEventPolling = (sym) => {
  stopEventPolling()
  const tick = async () => {
    eventPollRounds += 1
    if (eventPollRounds > EVENT_POLL_MAX_ROUNDS) {
      eventsAnalyzing.value = false
      maybeRetryStockRead()
      return
    }
    try {
      const res = await getStockEvents(sym, EVENT_WINDOW_DAYS)
      if (sym !== symbol.value) return
      applyTimeline(res?.data)
      updateMarks()
    } catch {
      eventsAnalyzing.value = false
      maybeRetryStockRead()
      return
    }
    maybeRetryStockRead()
    if (eventsAnalyzing.value) {
      eventPollTimer = setTimeout(tick, EVENT_POLL_INTERVAL_MS)
    }
  }
  eventPollTimer = setTimeout(tick, EVENT_POLL_INTERVAL_MS)
}

const loadStockRead = async (sym) => {
  stockReadLoading.value = true
  stockReadAttempted.value = true
  try {
    const res = await getStockRead(sym, EVENT_WINDOW_DAYS)
    if (sym !== symbol.value) return
    stockRead.value = typeof res?.data === 'string' ? res.data : ''
  } catch {
    stockRead.value = ''
  } finally {
    stockReadLoading.value = false
    maybeRetryStockRead()
  }
}

// K 线主链路竞态防护（切股/切周期/切范围并发时旧响应不得覆盖新图）
let loadDataSeq = 0

const sentiment = ref(null)

const loadSentiment = async (sym, seq) => {
  try {
    const res = await getSentiment(sym)
    if (seq === loadDataSeq && res.data?.ok) {
      sentiment.value = res.data.data || null
    }
  } catch {
    if (seq === loadDataSeq) sentiment.value = null
  }
}

const sentimentVerdict = computed(() => {
  const v = sentiment.value?.validation
  if (!v) return '情绪对照验证暂不可用'
  const parts = []
  if (v.focus?.verdict && v.focus.verdict !== '样本不足') parts.push(`关注指数：${v.focus.verdict}`)
  if (v.score?.verdict && v.score.verdict !== '样本不足') parts.push(`评分：${v.score.verdict}`)
  if (v.desire?.verdict && v.desire.verdict !== '样本不足') parts.push(`参与意愿：${v.desire.verdict}`)
  if (parts.length) return parts[0]
  return '对照验证样本收集中（约需 20 个交易日），暂不下结论'
})

const loadData = async (sym) => {
  const seq = ++loadDataSeq
  const isStale = () => seq !== loadDataSeq
  error.value = ''
  loading.value = true
  stockInfo.value = null
  indicators.value = null
  lastUpdate.value = ''
  disposeChart()
  loadEvents(sym)
  loadSentiment(sym, seq)

  try {
    const stockRes = await getStock(sym).catch(() => null)
    if (isStale()) return
    stockInfo.value = stockRes?.data ?? null

    let list = []
    if (period.value === 'minute') {
      const res = await getMinuteKline(sym, minutePeriod.value).catch(() => null)
      if (isStale()) return
      const data = Array.isArray(res?.data) ? res.data : []
      list = data.map((r) => ({
        date: r.date, open: r.open, close: r.close, high: r.high, low: r.low, volume: r.volume,
      }))
    } else {
      const historyRes = await getHistory(sym, 0, 1000, period.value).catch(() => null)
      if (isStale()) return
      list = historyRes?.data?.content || []
    }

    if (list.length === 0) {
      if (isStale()) return
      error.value = period.value === 'minute'
        ? '暂无分钟K线数据（仅支持 A 股，分钟 K 由 akshare 提供）'
        : '暂无 K 线数据，请检查股票代码或稍后重试'
      loading.value = false
      return
    }

    const data = list.slice(-range.value)

    if (period.value === 'minute' && data.length > 0) {
      const last = data[data.length - 1]
      stockInfo.value = stockInfo.value || {}
      stockInfo.value.price = last.close
    }

    indicators.value = calcIndicators(data)
    lastUpdate.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })

    await nextTick()
    if (isStale()) return
    if (!chartRef.value) return

    chart = echarts.init(chartRef.value)
    renderChart(sym, data)
    window.addEventListener('resize', handleResize)
  } catch (e) {
    if (isStale()) return
    error.value = e?.response?.data?.message
      || 'K 线数据加载失败，请检查股票代码是否正确，或确认网络后重试'
  } finally {
    if (!isStale()) loading.value = false
  }
}

// ---------- 图表渲染 ----------
const renderChart = (sym, data) => {
  const dates = data.map((d) => d.date)
  chartDates.value = dates
  const isMinute = period.value === 'minute'

  const COLOR_UP = token('--up', '#d92c2c')
  const COLOR_DOWN = token('--down', '#0e9668')

  const series = []
  const legend = []

  if (chartType.value === 'candlestick') {
    const kline = data.map((d) => [+d.open, +d.close, +d.low, +d.high])
    series.push({
      name: 'K线', type: 'candlestick', data: kline,
      tooltip: { valueFormatter: (v) => (Array.isArray(v) ? v.map((x) => (+x).toFixed(3)).join(' / ') : (+v).toFixed(3)) },
      itemStyle: {
        color: COLOR_UP, color0: COLOR_DOWN,
        borderColor: COLOR_UP, borderColor0: COLOR_DOWN,
      },
    })
    legend.push('K线')
  } else if (chartType.value === 'line') {
    const closes = data.map((d) => +d.close)
    const lineName = isMinute ? '价格' : '收盘价'
    const lineColor = token('--accent', '#2153d6')
    series.push({
      name: lineName, type: 'line', data: closes,
      tooltip: { valueFormatter: (v) => (+v).toFixed(3) },
      smooth: true, showSymbol: false,
      lineStyle: { width: 2, color: lineColor },
      itemStyle: { color: lineColor },
      areaStyle: { color: token('--accent-soft', '#e8eefc') },
    })
    legend.push(lineName)
  } else { // ohlc
    const ohlc = data.map((d) => [+d.open, +d.close, +d.low, +d.high])
    series.push({
      name: 'OHLC', type: 'candlestick', data: ohlc,
      tooltip: { valueFormatter: (v) => (Array.isArray(v) ? v.map((x) => (+x).toFixed(3)).join(' / ') : (+v).toFixed(3)) },
      renderItem: (params, api) => {
        const open = api.value(0)
        const close = api.value(1)
        const low = api.value(2)
        const high = api.value(3)
        const halfWidth = Math.max(2, api.size([1, 0])[0] * 0.3)
        const isUp = close >= open
        const color = isUp ? COLOR_UP : COLOR_DOWN
        return {
          type: 'group',
          children: [
            { type: 'line', shape: { x1: api.coord([api.value(0), low])[0], y1: api.coord([api.value(0), low])[1],
                                       x2: api.coord([api.value(0), high])[0], y2: api.coord([api.value(0), high])[1] },
              style: { stroke: color, lineWidth: 1 } },
            { type: 'line', shape: { x1: api.coord([api.value(0), open])[0] - halfWidth, y1: api.coord([api.value(0), open])[1],
                                       x2: api.coord([api.value(0), open])[0], y2: api.coord([api.value(0), open])[1] },
              style: { stroke: color, lineWidth: 1 } },
            { type: 'line', shape: { x1: api.coord([api.value(0), close])[0], y1: api.coord([api.value(0), close])[1],
                                       x2: api.coord([api.value(0), close])[0] + halfWidth, y2: api.coord([api.value(0), close])[1] },
              style: { stroke: color, lineWidth: 1 } },
          ],
        }
      },
    })
    legend.push('OHLC')
  }

  // 均线（仅日/周/月）
  if (!isMinute && data.length >= 5) {
    const ma5 = calcMA(data, 5)
    const ma10 = calcMA(data, 10)
    const ma20 = calcMA(data, 20)
    const ma60 = data.length >= 60 ? calcMA(data, 60) : null
    const push = (name, arr, color) => series.push({
      name, type: 'line', data: arr, smooth: true, showSymbol: false,
      tooltip: { valueFormatter: (v) => (+v).toFixed(3) },
      lineStyle: { width: 1, color },
      itemStyle: { color },
    })
    push('MA5', ma5, token('--chart-ma5', '#d97706')); legend.push('MA5')
    push('MA10', ma10, token('--chart-ma10', '#7c3aed')); legend.push('MA10')
    push('MA20', ma20, token('--chart-ma20', '#0891b2')); legend.push('MA20')
    if (ma60) { push('MA60', ma60, token('--chart-ma60', '#64748b')); legend.push('MA60') }
  }

  // 布林带（仅日/周/月）
  if (!isMinute && data.length >= 20) {
    const n = 20
    const closes = data.map((d) => +d.close)
    const upper = [], middle = [], lower = []
    for (let i = 0; i < closes.length; i++) {
      if (i < n - 1) { upper.push('-'); middle.push('-'); lower.push('-'); continue }
      const slice = closes.slice(i - n + 1, i + 1)
      const m = slice.reduce((a, b) => a + b, 0) / n
      const v = slice.reduce((a, b) => a + (b - m) ** 2, 0) / n
      const s = Math.sqrt(v)
      upper.push(+(m + 2 * s).toFixed(2))
      middle.push(+m.toFixed(2))
      lower.push(+(m - 2 * s).toFixed(2))
    }
    const bollFmt = { valueFormatter: (v) => (+v).toFixed(3) }
    const bollBand = token('--chart-boll', '#db2777')
    const bollMid = token('--ink-2', '#575e6e')
    series.push({ name: 'BOLL上', type: 'line', data: upper, showSymbol: false, tooltip: bollFmt, lineStyle: { width: 1, color: bollBand, opacity: 0.6 }, itemStyle: { color: bollBand } })
    series.push({ name: 'BOLL中', type: 'line', data: middle, showSymbol: false, tooltip: bollFmt, lineStyle: { width: 1, color: bollMid, type: 'dashed' }, itemStyle: { color: bollMid } })
    series.push({ name: 'BOLL下', type: 'line', data: lower, showSymbol: false, tooltip: bollFmt, lineStyle: { width: 1, color: bollBand, opacity: 0.6 }, itemStyle: { color: bollBand } })
    legend.push('BOLL上', 'BOLL中', 'BOLL下')
  }

  const volumes = data.map((d, i) => [i, +d.volume || 0, +d.close >= +d.open ? 1 : -1])

  const subPeriodLabel = isMinute ? `${minutePeriod.value}分K` : { day: '日K', week: '周K', month: '月K' }[period.value]
  const stockName = stockInfo.value?.name || sym
  lastClosePrice.value = data.length ? data[data.length - 1].close : ''

  // 事件标记挂第一条 series（主图）
  if (series.length) series[0].markLine = buildMarkLine()

  chart.setOption({
    title: {
      text: `${stockName} ${subPeriodLabel}`,
      subtext: chartSubtext.value,
      left: 'center',
      top: 0,
      textStyle: { fontSize: 15, fontWeight: 600, color: token('--ink', '#1b1e26') },
      subtextStyle: { fontSize: 12, color: token('--ink-3', '#8b92a5') },
    },
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross' },
      backgroundColor: token('--surface', '#ffffff'),
      borderColor: token('--border', '#e3e6ec'),
      textStyle: { color: token('--ink', '#1b1e26'), fontSize: 12 },
      extraCssText: 'box-shadow: 0 8px 24px rgba(16,20,34,0.16); border-radius: 8px;',
      confine: true,
    },
    legend: { data: legend, top: 46, textStyle: { fontSize: 12, color: token('--ink-2', '#575e6e') } },
    grid: [
      { left: 50, right: 20, top: 82, height: '58%' },
      { left: 50, right: 20, top: '76%', height: '12%' },
    ],
    xAxis: [
      { type: 'category', data: dates, scale: true, boundaryGap: false,
        axisLine: { onZero: false, lineStyle: { color: token('--border', '#e3e6ec') } },
        axisLabel: { formatter: (v) => v.substring(5), fontSize: 11, color: token('--ink-3', '#8b92a5') },
        splitLine: { show: false },
        axisPointer: { z: 100 } },
      { type: 'category', gridIndex: 1, data: dates, scale: true, boundaryGap: false,
        axisLine: { onZero: false, lineStyle: { color: token('--border', '#e3e6ec') } },
        axisTick: { show: false }, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    yAxis: [
      { scale: true, splitArea: { show: true, areaStyle: { color: [token('--bg', '#f5f6f8'), 'transparent'] } },
        splitLine: { lineStyle: { type: 'dashed', color: token('--border', '#e3e6ec') } },
        axisLabel: { color: token('--ink-3', '#8b92a5') } },
      { scale: true, gridIndex: 1, splitNumber: 2, axisLabel: { show: false },
        axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 50, end: 100,
        moveOnMouseMove: false, zoomOnMouseWheel: true },
      { show: true, xAxisIndex: [0, 1], type: 'slider', top: '94%', height: 18, start: 50, end: 100,
        handleStyle: { color: token('--accent', '#2153d6') } },
    ],
    series: [
      ...series,
      { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volumes,
        itemStyle: { color: (p) => (p.data[2] > 0 ? COLOR_UP : COLOR_DOWN) } },
    ],
  }, { notMerge: true })
}

// ---------- 事件 ----------
const onSubmit = () => {
  const s = symbol.value.trim().toUpperCase()
  if (!s) return
  router.replace(`/chart/${encodeURIComponent(s)}`)
}

const onPeriodChange = (p) => {
  period.value = p
  if (p === 'minute' && range.value > 480) range.value = 240
  loadData(symbol.value)
}

const onMinuteChange = (m) => {
  minutePeriod.value = m
  loadData(symbol.value)
}

const onChartTypeChange = (t) => {
  chartType.value = t
  loadData(symbol.value)
}

const onRangeChange = (value) => {
  range.value = value
  loadData(symbol.value)
}

onMounted(() => {
  const container = chartRef.value
  container?.addEventListener('mousemove', onChartMouseMove)
  container?.addEventListener('mouseleave', onChartMouseLeave)
  container?.addEventListener('click', onChartClick)
  narrowMedia?.addEventListener('change', onNarrowChange)
  void market.load()
  loadData(route.params.symbol)
})

onUnmounted(() => {
  stopEventPolling()
  const container = chartRef.value
  container?.removeEventListener('mousemove', onChartMouseMove)
  container?.removeEventListener('mouseleave', onChartMouseLeave)
  container?.removeEventListener('click', onChartClick)
  narrowMedia?.removeEventListener('change', onNarrowChange)
  disposeChart()
})

watch(() => route.params.symbol, (newSym) => {
  if (newSym && newSym.toUpperCase() !== symbol.value) {
    symbol.value = newSym.toUpperCase()
    loadData(symbol.value)
  }
})

// 指标颜色：超买警示色 / 超卖主色 / 中性主文字色（不与涨跌撞义）
const rsiColor = (v) => v == null
  ? token('--ink-3', '#8b92a5')
  : v >= 70 ? token('--warn', '#b45309') : v <= 30 ? token('--accent', '#2153d6') : token('--ink', '#1b1e26')
const macdColor = (h) => h == null
  ? token('--ink-3', '#8b92a5')
  : h >= 0 ? token('--up', '#d92c2c') : token('--down', '#0e9668')
</script>

<template>
  <div>
    <div class="mb-4 flex items-center gap-3">
      <button
        type="button"
        class="flex h-8 items-center gap-1 rounded-lg px-2 text-[13px] text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
        @click="router.back()"
      >
        <ArrowLeft :size="15" :stroke-width="2" aria-hidden="true" /> 返回
      </button>
      <h1 class="text-[20px] font-semibold text-ink">K 线图</h1>
    </div>

    <!-- 工具栏 -->
    <div class="mb-4 rounded-xl border border-border bg-surface p-3.5">
      <form class="flex gap-2" @submit.prevent="onSubmit">
        <input
          v-model="symbol"
          placeholder="输入股票代码（如 600519）"
          class="num h-[34px] min-w-0 flex-1 rounded-lg border border-border bg-bg px-3 text-[13px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
          aria-label="股票代码"
        >
        <Button type="submit" variant="primary">查看</Button>
      </form>

      <div class="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        <div class="flex items-center gap-1.5">
          <span class="text-[12px] text-ink-2">周期</span>
          <div class="flex gap-0.5">
            <button
              v-for="p in periods"
              :key="p.value"
              type="button"
              class="h-[26px] rounded-full px-2.5 text-[12px] transition-colors"
              :class="period === p.value ? 'bg-accent text-on-accent' : 'text-ink-3 hover:bg-surface-2 hover:text-ink'"
              @click="onPeriodChange(p.value)"
            >{{ p.label }}</button>
          </div>
        </div>

        <div v-if="period === 'minute'" class="flex items-center gap-1.5">
          <span class="text-[12px] text-ink-2">分钟</span>
          <div class="flex gap-0.5">
            <button
              v-for="m in minuteOptions"
              :key="m.value"
              type="button"
              class="h-[26px] rounded-full px-2.5 text-[12px] transition-colors"
              :class="minutePeriod === m.value ? 'bg-accent text-on-accent' : 'text-ink-3 hover:bg-surface-2 hover:text-ink'"
              @click="onMinuteChange(m.value)"
            >{{ m.label }}</button>
          </div>
        </div>

        <div class="flex items-center gap-1.5">
          <span class="text-[12px] text-ink-2">类型</span>
          <div class="flex gap-0.5">
            <button
              v-for="t in chartTypes"
              :key="t.value"
              type="button"
              class="h-[26px] rounded-full px-2.5 text-[12px] transition-colors"
              :class="chartType === t.value ? 'bg-accent text-on-accent' : 'text-ink-3 hover:bg-surface-2 hover:text-ink'"
              @click="onChartTypeChange(t.value)"
            >{{ t.label }}</button>
          </div>
        </div>
      </div>

      <div class="mt-2 flex flex-wrap items-center gap-1">
        <span class="mr-1 text-[12px] text-ink-2">显示</span>
        <button
          v-for="r in ranges"
          :key="r.value"
          type="button"
          class="h-[26px] rounded-full px-2.5 text-[12px] transition-colors"
          :class="range === r.value ? 'bg-accent text-on-accent' : 'text-ink-3 hover:bg-surface-2 hover:text-ink'"
          @click="onRangeChange(r.value)"
        >{{ r.label }}</button>
      </div>
    </div>

    <!-- 单列（手机）：图 → 事件 → 指标；桌面两列：图/指标在左、事件在右 -->
    <div class="grid gap-3 [grid-template-areas:'notice''chart''events''indicators'] [grid-template-columns:minmax(0,1fr)] lg:[grid-template-areas:'notice_notice''chart_events''indicators_events'] lg:w-[calc(1200px+1.5rem)] lg:max-w-none lg:[grid-template-columns:minmax(0,1fr)_360px] lg:items-start">
      <!-- 休市口径提示：跨整幅宽度 -->
      <p
        v-if="marketNotice"
        class="flex flex-wrap items-center gap-x-2.5 gap-y-1 rounded-xl border border-border bg-surface px-4 py-3 [grid-area:notice]"
        role="status"
      >
        <Badge tone="warn">{{ marketNotice.badge }}</Badge>
        <span class="text-[13px] text-ink">
          <template v-for="(part, i) in restNoticeParts" :key="i">
            <span v-if="i" class="mx-1 text-ink-3">·</span>{{ part }}
          </template>
        </span>
        <span v-if="marketNotice.detail" class="w-full text-[12px] text-ink-2">{{ marketNotice.detail }}</span>
      </p>

      <div class="flex min-w-0 flex-col gap-2 [grid-area:chart]">
        <p v-if="error" class="role-alert rounded-xl border border-border bg-surface p-6 text-center text-[13.5px] text-up" role="alert">{{ error }}</p>
        <div v-if="loading && !chart" class="flex items-center justify-center rounded-xl border border-border bg-surface py-16 text-[13.5px] text-ink-2">加载中…</div>
        <div ref="chartRef" class="h-[clamp(320px,45dvh,560px)] rounded-xl border border-border bg-surface p-2"></div>

        <!-- 图例：竖线怎么读（放 DOM 而不是 ECharts legend：markLine 进不了图例，canvas 字读屏读不到） -->
        <div
          v-if="events.length && chartDates.length"
          class="flex flex-wrap items-center gap-x-3.5 gap-y-1.5 rounded-lg bg-surface-2 px-2.5 py-1.5 text-[11.5px] text-ink-2"
        >
          <span class="font-semibold text-ink">图例</span>
          <span class="inline-flex items-center gap-1.5">
            <span class="inline-block h-[13px] w-[3px] rounded-sm bg-[color:var(--event-1)]" aria-hidden="true"></span>
            竖线＝事件发生时间（最多标注最重要的 {{ MARK_LIMIT }} 条），线色与下方条目的左侧色条一致
          </span>
          <span class="inline-flex items-center gap-1.5">
            <span class="inline-block h-0 w-[18px] border-t-2 border-solid border-ink-3" aria-hidden="true"></span>利好
            <span class="ml-2 inline-block h-0 w-[18px] border-t-2 border-dashed border-ink-3" aria-hidden="true"></span>利空
            <span class="ml-2 inline-block h-0 w-[18px] border-t-2 border-dotted border-ink-3" aria-hidden="true"></span>未判方向
          </span>
          <span class="text-ink-3">把鼠标移到竖线上即可看到是哪条新闻</span>
        </div>
        <p
          v-if="focusCaption"
          class="rounded-lg border-l-[3px] border-accent bg-accent-soft px-2.5 py-1.5 text-[12px] leading-relaxed text-ink"
        >{{ focusCaption }}</p>
      </div>

      <!-- 事件区 -->
      <aside class="min-w-0 rounded-xl border border-border bg-surface p-4 [grid-area:events] lg:sticky lg:top-3 lg:max-h-[calc(100dvh-24px)] lg:overflow-y-auto" aria-labelledby="event-heading">
        <!-- 散户情绪：验证结论与数值同屏；拿不到整卡隐藏 -->
        <div v-if="sentiment" class="mb-3 rounded-lg border border-border bg-surface-2 p-3" aria-label="散户情绪">
          <div class="flex items-center justify-between">
            <h3 class="text-[13px] font-semibold text-ink">散户情绪</h3>
            <span class="rounded-full border border-border px-1.5 text-[11px] text-ink-3">未验证信号</span>
          </div>
          <div class="num mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[12px] text-ink-2">
            <span v-if="sentiment.desire?.latest != null">
              参与意愿 {{ sentiment.desire.latest.toFixed(0) }}<template v-if="sentiment.desire.change != null">（{{ sentiment.desire.change > 0 ? '+' : '' }}{{ sentiment.desire.change.toFixed(1) }}）</template>
            </span>
            <span v-if="sentiment.focus?.latest != null">关注 {{ sentiment.focus.latest.toFixed(0) }}</span>
            <span v-if="sentiment.score?.latest != null">千股千评 {{ sentiment.score.latest.toFixed(0) }}</span>
          </div>
          <p class="mt-1.5 text-[11px] leading-relaxed text-ink-3">{{ sentimentVerdict }}</p>
        </div>

        <div>
          <h2 id="event-heading" class="flex items-center gap-1.5 text-[15px] font-semibold text-ink">
            事件与解读
            <InfoTip label="解读免责说明" text="解读由 AI 生成，仅供参考，不构成投资建议。" />
          </h2>
          <p v-if="eventsError" class="mt-2 text-[12.5px] text-up" role="alert">{{ eventsError }}</p>
          <!-- 骨架屏：不冒充内容，只说明"这里马上会有东西" -->
          <div v-else-if="!eventsLoaded" class="mt-2.5 flex flex-col gap-2" role="status" aria-label="正在获取资讯">
            <span class="text-[12px] text-ink-3">正在获取近期资讯…</span>
            <div v-for="n in EVENT_PREVIEW" :key="`sk-${n}`" class="space-y-1.5 border-l-[3px] border-border bg-surface-2 p-2.5">
              <Skeleton width="38%" height="10px" />
              <Skeleton width="90%" height="10px" />
            </div>
          </div>
          <template v-else-if="events.length">
            <p class="num mt-1 text-[12px] text-ink-2">
              近 {{ EVENT_WINDOW_DAYS }} 天 {{ events.length }} 条（公告 {{ eventCounts.notice }} / 媒体 {{ eventCounts.media }} / 研报 {{ eventCounts.report }}）
            </p>
            <p v-if="eventsAnalyzing" class="mt-1 flex items-center gap-1.5 text-[12px] text-ink-2" role="status">
              <span class="live-dot h-1.5 w-1.5 rounded-full bg-accent" aria-hidden="true"></span>
              正在解读 {{ eventsAnalyzedCount }}/{{ Math.min(events.length, eventsAnalyzedCount + eventsPendingCount) }} 条…
            </p>
            <p v-if="attributionNote" class="mt-1 text-[13px] leading-relaxed text-ink">{{ attributionNote }}</p>
          </template>
        </div>

        <!-- 「当前怎么看」：整段连贯判断 -->
        <div v-if="stockRead" class="mt-2.5 rounded-lg border-l-[3px] border-accent bg-surface-2 px-3 py-2.5">
          <p class="mb-1 text-[12px] font-semibold text-accent">当前怎么看</p>
          <p class="text-[13px] leading-7 text-ink">{{ stockRead }}</p>
        </div>
        <p v-else-if="stockReadLoading && events.length" class="mt-2.5 text-[12px] text-ink-3" role="status">
          正在综合近期资讯生成判断…（约需几秒）
        </p>

        <p v-if="eventsLoaded && !eventsError && !events.length" class="mt-2.5 text-[13px] leading-relaxed text-ink-2">
          近 {{ EVENT_WINDOW_DAYS }} 天没有公告、媒体或研报记录。<br>
          没有可验证的事件驱动时，波动通常来自板块或大盘，而不是这只票自己的消息。
        </p>

        <ul v-else-if="events.length" class="mt-2.5 flex flex-col gap-2">
          <li
            v-for="(event, index) in previewEvents"
            :key="event.id"
            :id="`event-${event.id}`"
            class="cursor-pointer rounded-lg border border-border border-l-[3px] p-2.5 transition-colors"
            :class="(activeEventId === event.id || hoveredEventId === event.id)
              ? 'border-accent border-l-[5px] bg-accent-soft' : 'hover:border-accent'"
            :style="{ borderLeftColor: colorForNumber(index + 1) }"
            @click="focusEvent(event)"
            @mouseenter="hoverEvent(event, true)"
            @mouseleave="hoverEvent(event, false)"
          >
            <div class="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-ink-3">
              <span class="inline-flex items-center gap-1 text-ink-2">
                <component :is="sourceMeta(event.sourceLevel).icon" :size="13" :stroke-width="1.75" aria-hidden="true" />
                {{ event.sourceName || sourceMeta(event.sourceLevel).label }}
              </span>
              <span class="num">{{ formatEventTime(event.publishedAt, event.dateOnly) }}</span>
              <span v-if="event.freshnessLabel" class="num">{{ event.freshnessLabel }}</span>
              <span
                v-if="event.credibilityGrade"
                class="cursor-help rounded-full border border-current px-1.5"
                :class="event.credibilityGrade === '高' || event.credibilityGrade === '较高' ? 'text-accent' : event.credibilityGrade === '低' ? 'text-warn' : 'text-ink-3'"
                :title="(event.credibilityReasons || []).join('；')"
              >可信 {{ event.credibilityGrade }}</span>
              <Badge :tone="directionTone(event.direction)" class="ml-auto">{{ directionLabel(event) }}</Badge>
            </div>
            <p class="mt-1.5 text-[13px] leading-relaxed text-ink">
              <a v-if="event.url" :href="event.url" target="_blank" rel="noopener noreferrer" class="hover:underline" @click.stop>{{ event.title }}</a>
              <template v-else>{{ event.title }}</template>
              <button
                type="button"
                class="ml-2 rounded-md border border-border px-1.5 py-0.5 text-[11.5px] text-ink-2 transition-colors hover:border-accent hover:text-accent"
                @click.stop="askAbout(event)"
              >问 AI</button>
            </p>
            <!-- 传闻警示放在标题与解读之间：它改变解读该带着多大怀疑看 -->
            <p v-if="event.rumorFlag" class="mt-1.5 rounded-md bg-warn-soft px-2 py-1.5 text-[12px] leading-relaxed text-warn" role="alert">
              传闻特征明显，未经证实，请以官方公告为准
              <span v-if="(event.credibilityReasons || []).length" class="mt-0.5 block text-[11px] opacity-85">{{ event.credibilityReasons[0] }}</span>
            </p>
            <p v-if="event.analyzed" class="mt-1 text-[12px] leading-relaxed text-ink-2">{{ event.plainSummary }}</p>
            <!-- "还在解读"与"信息不足"是两件事，必须分开 -->
            <p v-else-if="eventsAnalyzing" class="mt-1 flex items-center gap-1.5 text-[12px] text-ink-2">
              <span class="live-dot h-[5px] w-[5px] rounded-full bg-ink-3" aria-hidden="true"></span>正在解读这条…
            </p>
            <p v-else class="mt-1 text-[12px] text-ink-3">信息不足，不判断方向</p>
            <details v-if="event.risks?.length || event.opportunities?.length" class="mt-1.5 text-[12px]" @click.stop>
              <summary class="cursor-pointer text-ink-2">风险 / 机会</summary>
              <div v-if="event.opportunities?.length">
                <p class="mt-1.5 text-[12px] font-semibold text-up">机会</p>
                <ul class="mt-0.5 list-disc pl-4 leading-relaxed text-ink-2">
                  <li v-for="(text, i) in event.opportunities" :key="`o${i}`">{{ text }}</li>
                </ul>
              </div>
              <div v-if="event.risks?.length">
                <p class="mt-1.5 text-[12px] font-semibold text-warn">风险</p>
                <ul class="mt-0.5 list-disc pl-4 leading-relaxed text-ink-2">
                  <li v-for="(text, i) in event.risks" :key="`r${i}`">{{ text }}</li>
                </ul>
              </div>
            </details>
          </li>
        </ul>

        <button
          v-if="events.length > EVENT_PREVIEW"
          type="button"
          class="mt-2.5 h-[32px] w-full rounded-lg border border-border text-[12px] text-ink-2 transition-colors hover:border-accent hover:text-accent"
          @click="showAllEvents = !showAllEvents"
        >
          {{ showAllEvents ? '收起' : `展开全部 ${events.length} 条` }}
        </button>
      </aside>

      <!-- 指标面板 -->
      <div v-if="indicators" class="flex flex-wrap gap-x-4 gap-y-2.5 rounded-xl border border-border bg-surface p-4 [grid-area:indicators]">
        <div class="flex min-w-[180px] flex-col gap-0.5">
          <span class="text-[12px] text-ink-2">RSI(14)</span>
          <span class="num text-[14px] font-semibold" :style="{ color: rsiColor(indicators.rsi) }">{{ indicators.rsi }}</span>
          <span class="text-[11px] text-ink-3">{{ indicators.rsi >= 70 ? '超买' : indicators.rsi <= 30 ? '超卖' : '中性' }}</span>
        </div>
        <div class="flex min-w-[180px] flex-col gap-0.5">
          <span class="text-[12px] text-ink-2">MACD</span>
          <span class="num text-[14px] font-semibold">
            <span style="color: var(--chart-ma10)">DIF {{ indicators.macd.dif }}</span>
            <span class="ml-1.5" style="color: var(--chart-ma20)">DEA {{ indicators.macd.dea }}</span>
          </span>
          <span class="num text-[11px]" :style="{ color: macdColor(indicators.macd.hist) }">
            HIST {{ indicators.macd.hist >= 0 ? '+' : '' }}{{ indicators.macd.hist }}
          </span>
        </div>
        <div class="flex min-w-[180px] flex-col gap-0.5">
          <span class="text-[12px] text-ink-2">BOLL(20)</span>
          <span class="num text-[14px] font-semibold text-ink-2">
            {{ indicators.bollinger.lower }} / {{ indicators.bollinger.middle }} / {{ indicators.bollinger.upper }}
          </span>
          <span class="text-[11px] text-ink-3">下轨 / 中轨 / 上轨</span>
        </div>
      </div>
    </div>

    <!-- 聚焦聊天抽屉 -->
    <ChatDrawer :open="chatOpen" :focus-event="chatFocus" @close="chatOpen = false" />
  </div>
</template>
