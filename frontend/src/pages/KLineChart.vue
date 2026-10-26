<script setup>
/**
 * 个股 K 线页：图表 + 事件锚定 + 散户情绪 + 当前怎么看。
 * 主图渲染层 2026-10-19 路线A迁移到 klinecharts v10：蜡烛/OHLC/面积三形态、
 * MA/BOLL/VOL 内置指标、十字光标与缩放平移由库接管；数据喂入走 v10 唯一入口
 * setDataLoader（getBars 整段 + subscribeBar 盘中补丁/跨分钟滚动）。
 * 事件竖线双向联动保持原生 DOM 事件 + 坐标换算思路（竖线用 verticalStraightLine
 * overlay 绘制、lock 防误拖，命中检测仍走 convertToPixel）。
 * 其余逻辑原样保留：解读后台轮询（2s/40 轮上限）、综合解读"空手而归补问一次"、
 * K 线主链路 loadDataSeq 竞态防护、市场口径副标题、估值/主力资金副图（仍 ECharts）。
 */
import { ref, onMounted, onUnmounted, nextTick, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Megaphone, Newspaper, FileText, Globe, Star, Check } from 'lucide-vue-next'
import { getHistory, getMinuteKline, getStock , getValuation, getStockFundFlow, addFavorite, getFavoritesBrief } from '../api/stock.js'
import { quoteBus } from '../composables/quoteBus.js'
import { getStockEvents, getStockRead } from '../api/news.js'
import { getSentiment } from '../api/market.js'
import { anchorDateFor as anchorOnAxis, anchorMinuteFor } from '../utils/newsAnchor.js'
import { useMarketStatus } from '../composables/useMarketStatus.js'
import { toNum, dirTextClass, signed } from '../utils/format.js'
import { swrRead, swrWrite } from '../utils/swrCache.js'
import { renderMarkdown } from '../utils/markdown.js'
import ChatDrawer from '../components/ChatDrawer.vue'
import Badge from '../components/ui/badge.vue'
import Skeleton from '../components/ui/skeleton.vue'
import BlockCard from '../components/ui/block-card.vue'
import * as echarts from 'echarts'
import { init as initKlinecharts, dispose as disposeKlinecharts } from 'klinecharts'

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

// ---------- 加入自选（2026-10-25）：详情页头部直达入口 ----------
// 背景：10-19 移除本页搜索表单后，"加自选"只剩首页「查询后」一条路——在详情页
// 看着一只票想收藏，得回首页把代码再搜一遍，实测用户根本找不到入口。
// 已在自选 → 只读「已自选」；不在 → 一键加入（买入价/数量选填项留给首页补录）。
const favState = ref('checking')   // checking | none | adding | added | exists
const favError = ref('')

/** brief 列表里的代码可能带市场前缀（SH600519），路由参数不带——后 6 位对齐 */
const sameSymbol = (a, b) => {
  const x = String(a || '').toUpperCase(), y = String(b || '').toUpperCase()
  if (!x || !y) return false
  return x === y || (x.length >= 6 && y.length >= 6 && x.slice(-6) === y.slice(-6))
}

const checkFavorite = async (sym) => {
  favState.value = 'checking'
  favError.value = ''
  try {
    const res = await getFavoritesBrief()
    const list = Array.isArray(res?.data) ? res.data : []
    favState.value = list.some((s) => sameSymbol(s, sym)) ? 'exists' : 'none'
  } catch {
    favState.value = 'none'   // 查询失败按"未加"处理：点击时后端还会再校验一次（400=已在自选）
  }
}

const addToFavorites = async () => {
  if (favState.value !== 'none') return
  favState.value = 'adding'
  try {
    await addFavorite(symbol.value)
    favState.value = 'added'
  } catch (e) {
    const msg = e?.response?.data?.message || e?.friendlyMessage || ''
    if (msg.includes('已在自选') || e?.businessCode === 400) {
      favState.value = 'exists'
    } else {
      favState.value = 'none'
      favError.value = msg || '添加失败，请稍后重试'
      setTimeout(() => { favError.value = '' }, 3000)
    }
  }
}

const market = useMarketStatus()
const marketNotice = market.restNotice
const restNoticeParts = market.restNoticeParts

const EVENT_HIT_TOLERANCE_PX = 14
const EVENT_WINDOW_DAYS = 90
const MARK_LIMIT = 12

const chartRef = ref(null)

// ---------- 估值分位副图（PE-TTM 滚动 5 年分位，与规则/委员会同一口径） ----------
const valuationRows = ref([])
const valChartRef = ref(null)
let valChart = null
const valLatest = computed(() => {
  const rows = valuationRows.value.filter((r) => r.pct != null)
  return rows.length ? rows[rows.length - 1] : null
})
const valTone = (pct) => (pct >= 80 ? 'text-up' : pct <= 20 ? 'text-down' : 'text-ink-2')

const cssTokenV = (name, fallback) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback

const loadValuation = async (sym, seq) => {
  // SWR 首屏：估值分位是日频慢数据，先用上次快照出副图，网络回来原地换新
  const cached = swrRead(`val:${sym}`, 5 * 24 * 3600 * 1000)
  if (Array.isArray(cached) && cached.length) {
    valuationRows.value = cached
    renderValuation()
  }
  try {
    const res = await getValuation(sym)
    // 切走后到的旧票估值不得覆盖副图（与主链路 loadDataSeq 同一序号口径）
    if (seq !== loadDataSeq) return
    valuationRows.value = Array.isArray(res.data?.items) ? res.data.items : []
    if (valuationRows.value.length) swrWrite(`val:${sym}`, valuationRows.value)
  } catch {
    // 网络失败但有快照：保留快照副图不闪空；没有快照才清空
    if (seq === loadDataSeq && !cached?.length) valuationRows.value = []
  }
  renderValuation()
}

const renderValuation = async () => {
  await nextTick()
  if (!valChartRef.value) return
  const rows = valuationRows.value.filter((r) => r.pct != null)
  if (rows.length < 30) return
  if (!valChart) valChart = echarts.init(valChartRef.value)
  const shown = rows.slice(-1250)   // 最多 5 年，与分位窗口对齐
  valChart.setOption({
    grid: { left: 34, right: 10, top: 8, bottom: 20 },
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        const i = params[0].dataIndex
        return `${shown[i].date}<br/>分位 <b>${shown[i].pct}</b>　PE ${shown[i].pe.toFixed(2)}`
      },
    },
    xAxis: { type: 'category', data: shown.map((r) => r.date.slice(2)),
             axisLabel: { fontSize: 10, color: cssTokenV('--ink-3', '#8b92a5') } },
    yAxis: {
      type: 'value', min: 0, max: 100,
      axisLabel: { fontSize: 10, color: cssTokenV('--ink-3', '#8b92a5') },
      splitLine: { lineStyle: { color: cssTokenV('--border', '#e5e7eb') } },
    },
    series: [{
      type: 'line', data: shown.map((r) => r.pct), symbol: 'none',
      lineStyle: { width: 1.6, color: cssTokenV('--accent', '#3b6df6') },
      markArea: {
        silent: true,
        data: [
          [{ yAxis: 80, itemStyle: { color: cssTokenV('--up', '#d92c2c'), opacity: 0.07 } },
           { yAxis: 100 }],
          [{ yAxis: 0, itemStyle: { color: cssTokenV('--down', '#0e9668'), opacity: 0.07 } },
           { yAxis: 20 }],
        ],
      },
    }],
  })
}
// ---------- klinecharts 主图（v10，2026-10-19 路线A） ----------
let chart = null
// subscribeBar 注册的"当前柱推送"入口：SSE 改写当前柱 / 跨分钟滚动出新柱都走它。
// 时间戳与 bar 同时间戳 → 原位更新；时间戳更新 → 追加新柱（库内建语义）。
let barPush = null

const handleResize = () => chart?.resize()

// 数据形状：后端 {date:"YYYY-MM-DD[ HH:MM:SS]", open, ...}（北京时间字符串）
// → klinecharts {timestamp, OHLCV}。显式 +08:00：标签是北京口径，图表时区也钉死
// Asia/Shanghai，双保险不随宿主机时区漂移。
const toTimestamp = (dateStr) => Date.parse(`${String(dateStr).replace(' ', 'T')}+08:00`)
const toBar = (r) => ({
  timestamp: toTimestamp(r.date),
  open: +r.open, high: +r.high, low: +r.low, close: +r.close, volume: +r.volume || 0,
})

const chartPeriodOf = () => (period.value === 'minute'
  ? { type: 'minute', span: minutePeriod.value }
  : { type: period.value, span: 1 })   // day / week / month

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

// 主题令牌 → klinecharts 样式（canvas 不认 CSS 变量名，取计算值）
const chartStylesOf = () => {
  const up = token('--up', '#d92c2c')
  const down = token('--down', '#0e9668')
  const border = token('--border', '#e3e6ec')
  const ink3 = token('--ink-3', '#8b92a5')
  return {
    candle: {
      bar: {
        upColor: up, downColor: down, noChangeColor: ink3,
        upBorderColor: up, downBorderColor: down, noChangeBorderColor: ink3,
        upWickColor: up, downWickColor: down, noChangeWickColor: ink3,
      },
      area: { lineStyle: { color: token('--accent', '#2153d6'), size: 2 } },
      priceMark: {
        high: { color: ink3 }, low: { color: ink3 },
        last: { upColor: up, downColor: down, noChangeColor: ink3, text: { color: ink3 } },
      },
    },
    grid: { horizontal: { show: true, style: 'dashed', color: border }, vertical: { show: false } },
    xAxis: { tickText: { color: ink3 }, axisLine: { color: border } },
    yAxis: { tickText: { color: ink3 }, axisLine: { color: border } },
  }
}

// ---------- 主题切换即时重绘（2026-10-26） ----------
// 三块画布（主图/估值/资金流）都不认 CSS 变量名，配色是绘制时取的字面值：
// 切深浅色后画布保持旧配色，要刷新页面才变——主题切换时最先被注意到的就是它。
// html.dark 翻转时重刷三块画布；DOM 部分的颜色改走 var(--…)（见 rsiColor 等）自动跟随。
let themeObserver = null
let lastDarkSeen = typeof document !== 'undefined'
  && document.documentElement.classList.contains('dark')

const repaintChartsForTheme = () => {
  const dark = document.documentElement.classList.contains('dark')
  if (dark === lastDarkSeen) return   // html 上其它 class 变化不触发重绘
  lastDarkSeen = dark
  if (chart) {
    chart.setStyles(chartStylesOf())  // token 缓存按主题分键，这里取到的就是新主题值
    updateMarks()                     // 事件竖线颜色是 overlay 里的字面色，需重建
  }
  renderValuation()                   // cssTokenV 现取计算值，重跑 setOption 即换色
  renderFundFlow()
}

// 鼠标落在哪条事件竖线附近：原生事件 + klinecharts 坐标换算
// （竖线 overlay 只负责画，交互仍走 DOM 命中——与旧 ECharts 实现同一思路）
const nearestMarkedEvent = (event) => {
  if (!chart || !chartRef.value || !markGroups.value.length) return null
  const rect = chartRef.value.getBoundingClientRect()
  const pixelX = event.clientX - rect.left
  const pixelY = event.clientY - rect.top
  if (pixelX < 0 || pixelY < 0 || pixelX > rect.width || pixelY > rect.height) return null

  let best = null
  for (const group of markGroups.value) {
    const lineX = chart.convertToPixel(
      { timestamp: toTimestamp(group.anchor) }, { paneId: 'candle_pane' })?.x
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
    disposeKlinecharts(chart)
    chart = null
  }
  barPush = null
  window.removeEventListener('resize', handleResize)
}

// ---------- 工具函数 ----------
// MA/BOLL/VOL 均由 klinecharts 内置指标绘制；页面侧只保留技术指标卡的 RSI/MACD/BOLL 现算
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
const errorMessage = (e, fallback) => e?.friendlyMessage || e?.response?.data?.message || fallback

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

// 分钟图用分钟轴锚定（带时刻的事件落到对应分钟柱，日级事件落当日首根）；
// 日线图维持交易日锚定（公告只给到日，多条同日事件共用一根是口径而非 bug）
const anchorDateFor = (event) => period.value === 'minute'
  ? anchorMinuteFor(event.publishedAt, chartDates.value)
  : anchorOnAxis(event.publishedAt, chartDates.value)

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

// 事件竖线 → klinecharts overlay。⚠️ 实测（2026-10-19 冒烟）：v10 里 `name` 字段
// 是**类型名**（verticalStraightLine），填自定义标签会查不到类、静默返回 null；
// 批量管理用 groupId（removeOverlay 按 groupId 过滤）。lock=true 让线只做展示
// （不可拖动改点），交互命中仍走 nearestMarkedEvent 的 DOM 换算——
// 与旧 ECharts markLine 方案同一分工：画归画、事件归事件。
const EVENT_OVERLAY_TYPE = 'verticalStraightLine'
const EVENT_OVERLAY_GROUP = 'event-lines'

const buildEventOverlays = () => {
  const focusId = hoveredEventId.value ?? activeEventId.value
  return markGroups.value.map(({ anchor, events: evts, representative }) => {
    const focused = evts.some((e) => e.id === focusId)
    const lineType = directionLineTypeForGroup(evts)   // klinecharts 只有实/虚线：点线并入虚线
    return {
      name: EVENT_OVERLAY_TYPE,
      groupId: EVENT_OVERLAY_GROUP,
      lock: true,
      points: [{ timestamp: toTimestamp(anchor) }],
      styles: {
        line: {
          color: colorForEvent(representative),
          style: lineType === 'solid' ? 'solid' : 'dashed',
          size: focused ? 3 : 1.5,
        },
      },
      extendData: { anchor },
    }
  })
}

/** 重建事件竖线（事件/悬停/聚焦变化时；pan/zoom 由库按 timestamp 自动跟随，无需重建） */
const updateMarks = () => {
  if (!chart) return
  try {
    chart.removeOverlay({ groupId: EVENT_OVERLAY_GROUP })
    const overlays = buildEventOverlays()
    if (overlays.length) chart.createOverlay(overlays)
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

// 副标题现在是 DOM 文本（klinecharts 无内建标题），computed 直接驱动模板，无需 watch

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

// DOM 侧（事件列表左边框）用 CSS 变量形态——切主题自动跟随；
// colorForNumber 的字面值形态留给画布 overlay（canvas 不认 var()）
const colorVarForNumber = (number) =>
  `var(${EVENT_COLOR_TOKENS[(Math.max(number, 1) - 1) % EVENT_COLOR_TOKENS.length]}, #3b82f6)`

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
const loadEvents = async (sym, seq) => {
  const isStale = () => seq !== loadDataSeq
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
    // 事件窗口跟随图表范围：默认 180 根日线 ≈ 180 天，窗口小于图会让左半图"没有事件"，
    // 看起来像"信息都是旧的"。取 max(90, range)；分钟模式图短，维持 90 天。
    const eventDays = period.value === 'minute'
      ? EVENT_WINDOW_DAYS
      : Math.max(EVENT_WINDOW_DAYS, Number(range.value) || EVENT_WINDOW_DAYS)
    const res = await getStockEvents(sym, eventDays)
    if (isStale()) return   // 排队期间又切走了：旧票事件不得盖到新票上
    applyTimeline(res?.data)
  } catch (e) {
    if (isStale()) return
    eventsError.value = errorMessage(e, '资讯加载失败，稍后重试')
  } finally {
    if (!isStale()) {
      eventsLoaded.value = true
      updateMarks()
      maybeRetryStockRead()
      if (eventsAnalyzing.value) {
        startEventPolling(sym)
      }
    }
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

// ---------- 实时刷新（2026-10-15）：SSE 即时补丁 + 盘中 30 秒轮询 ----------
// 之前页面只在挂载时拉一次数据，之后永远不动——盘中最新的那根蜡烛是死的。
// 两条腿：① quoteBus 的行情 SSE（约 15 秒一批，图表页此前根本没接）做即时价格补丁；
// ② 交易时段每 30 秒整段重取（分钟缓存 30s、日线 25s，节奏对齐服务端）重画最后一根。
const currentData = ref([])
/** 未切片的全量序列（至多 1000 根）：切范围时本地重切，见 onRangeChange */
const fullData = ref([])
let liveTimer = null

const inMarketHours = () => {
  const d = new Date()
  const day = d.getDay()
  if (day === 0 || day === 6) return false
  const m = d.getHours() * 60 + d.getMinutes()
  return (m >= 555 && m <= 690) || (m >= 780 && m <= 905)   // 09:15-11:30 / 13:00-15:05，含缓冲
}

/** SSE 即时补丁：只改头牌价与最后一根蜡烛的 close/high/low，不整段重取 */
const patchLivePrice = (price) => {
  if (!Number.isFinite(price) || !currentData.value.length) return
  const last = currentData.value[currentData.value.length - 1]
  last.close = price
  last.high = Math.max(Number(last.high) || price, price)
  last.low = Math.min(Number(last.low) || price, price)
  if (stockInfo.value) stockInfo.value.price = price
  lastUpdate.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  // 当前柱原位推送：不整图重画，用户的平移/缩放位置由库保持
  if (!loading.value) barPush?.(toBar(last))
}

/** SSE 批次里找本页标的的条目（键是带市场前缀的完整代码，做精确/后缀两级匹配） */
const liveEntryOf = (sym) => {
  if (!sym) return undefined
  const key = String(sym).toUpperCase()
  const direct = quoteBus.quotes[key]
  if (direct) return direct
  const digits = key.slice(-6)
  for (const [k, v] of Object.entries(quoteBus.quotes)) {
    if (String(k).toUpperCase().endsWith(digits)) return v
  }
  return undefined
}

const livePriceOf = (sym) => liveEntryOf(sym)?.price

// ---------- 报价条（2026-10-15 区块化改造）：同花顺式头部行情区 ----------
// SSE 推价优先（盘中 3 秒一跳），回落到 stockInfo 初载值；涨跌按昨收现算，
// 与 SSE 补丁口径一致（不用接口带回的 change，那可能是旧快照的）。
const liveQuote = computed(() => {
  const live = livePriceOf(symbol.value)
  const base = toNum(stockInfo.value?.price)
  const price = live ?? base
  if (price == null || !Number.isFinite(price)) return null
  const prev = toNum(stockInfo.value?.previousClose)
  if (prev != null && prev > 0) {
    return { price, change: price - prev, pct: (price / prev - 1) * 100, prev }
  }
  const change = toNum(stockInfo.value?.change)
  const pct = toNum(stockInfo.value?.changePercent)
  return { price, change, pct, prev }
})

/** 当前视图的最后一根 bar（报价条的最高/最低/量从这里取，随轮询与 SSE 补丁一起动） */
const lastBar = computed(() => {
  const list = currentData.value
  return list.length ? list[list.length - 1] : null
})

const fmtVol = (v) => {
  const n = toNum(v)
  if (n == null) return null
  if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿'
  if (n >= 1e4) return (n / 1e4).toFixed(0) + '万'
  return String(n)
}

const fmtPrice = (v) => {
  const n = toNum(v)
  return n == null ? null : n.toFixed(2)
}

/** 盘中轮询：整段重取校准页面状态，图表只推最后一根（更早的柱不会变，
 *  原位推送保住用户的平移/缩放，不整图重置） */
const refreshLive = async () => {
  const sym = symbol.value
  if (loading.value || !chart || !sym) return
  try {
    let list = []
    if (period.value === 'minute') {
      const res = await getMinuteKline(sym, minutePeriod.value).catch(() => null)
      list = (Array.isArray(res?.data) ? res.data : []).map((r) => ({
        date: r.date, open: r.open, close: r.close, high: r.high, low: r.low, volume: r.volume,
      }))
    } else {
      const res = await getHistory(sym, 0, 1000, period.value).catch(() => null)
      list = res?.data?.content || []
    }
    if (symbol.value !== sym || !list.length) return   // 切股竞态：丢弃旧响应
    fullData.value = list
    currentData.value = list
    indicators.value = calcIndicators(list)
    // 日线的"今日收盘"就是最新价：头部价格跟着每轮刷新走（SSE 焦点价优先，见 patchLivePrice）
    if (period.value !== 'minute' && list.length) {
      const lastClose = Number(list[list.length - 1].close)
      if (Number.isFinite(lastClose)) {
        stockInfo.value = stockInfo.value || {}
        stockInfo.value.price = lastClose
      }
    }
    lastUpdate.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    barPush?.(toBar(list[list.length - 1]))
  } catch {
    // 轮询失败静默：下一轮再来，不打断页面
  }
}

const startLive = () => {
  stopLive()
  liveTimer = setInterval(() => { if (inMarketHours()) refreshLive() }, 30_000)
}
const stopLive = () => {
  if (liveTimer) { clearInterval(liveTimer); liveTimer = null }
}

// SSE 补丁：盯"到货时刻"（entry.at）而不是价格值 —— 焦点票 3 秒一批的推送，
// 即使价格暂时没动也让「更新 HH:MM:SS」跳一下。行情页"活着"的观感一半来自时间戳在走。
watch(() => liveEntryOf(symbol.value)?.at, () => {
  if (!inMarketHours()) return
  const price = livePriceOf(symbol.value)
  patchLivePrice(price)
  maybeRollMinuteBar(price)
})

// ---------- 跨分钟滚动（2026-10-19，klinecharts 版）：SSE 推送顺带当哨兵 ----------
// 价格秒级更新由 SSE 补丁负责；跨分钟时用当前价**合成新柱**直接推送（open=high=low=close=
// 当前价、volume=0），新柱秒级出现；真实 open 与量由下一轮 refreshLive（≤30 秒）校正。
// 合成的 date 也推进页面状态，哨兵才不会在同一分钟里重复造柱。
let minuteRolledAt = 0
const maybeRollMinuteBar = (price) => {
  if (period.value !== 'minute' || loading.value || !Number.isFinite(price) || !currentData.value.length) return
  const last = currentData.value[currentData.value.length - 1]
  const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})/.exec(String(last?.date || ''))
  if (!m) return
  // 标签是窗口起点（open-time 口径），终点 = 起点 + 分钟粒度
  const windowEnd = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5] + minutePeriod.value)
  if (Date.now() < windowEnd.getTime() + 1000) return   // 当前窗口还没走完（+1s 钟差缓冲）
  if (minuteRolledAt >= windowEnd.getTime()) return     // 这个窗口已经滚过柱了
  minuteRolledAt = windowEnd.getTime()
  const d = windowEnd
  const pad = (n) => String(n).padStart(2, '0')
  const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:00`
  currentData.value.push({ date, open: price, close: price, high: price, low: price, volume: 0 })
  chartDates.value.push(date)
  barPush?.(toBar(currentData.value[currentData.value.length - 1]))
  updateMarks()   // 新窗口可能有恰好落在其间的事件锚点
}

// K 线主链路竞态防护（切股/切周期/切范围并发时旧响应不得覆盖新图）
let loadDataSeq = 0
// 上一次主链路的在途请求：新一次加载开始前整体取消。
// 高频切换时旧请求立即作废 —— 省浏览器同源并发槽（SSE 已占 2 个），
// 也少消耗 nginx 限流额度（否则排队中的旧请求照样计 10 r/s）
let loadAbort = null

const sentiment = ref(null)

// ---------- 主力资金卡（个股口径，2026-10-19）：与大盘卡同链路，看的是"这只票的钱" ----------
const fundFlowRows = ref([])
const fundFlowChartRef = ref(null)
let fundFlowChart = null
const fundFlowLatest = computed(() => fundFlowRows.value[fundFlowRows.value.length - 1] || null)

const loadFundFlow = async (sym, seq) => {
  // SWR 首屏：日频数据，先出上次快照，网络回来原地换新
  const cached = swrRead(`ff:${sym}`, 24 * 3600 * 1000)
  if (Array.isArray(cached) && cached.length) {
    fundFlowRows.value = cached
    renderFundFlow()
  }
  try {
    const res = await getStockFundFlow(sym)
    if (seq !== loadDataSeq) return
    fundFlowRows.value = Array.isArray(res.data?.items) ? res.data.items : []
    if (fundFlowRows.value.length) swrWrite(`ff:${sym}`, fundFlowRows.value)
  } catch {
    if (seq === loadDataSeq && !cached?.length) fundFlowRows.value = []
  }
  renderFundFlow()
}

const renderFundFlow = async () => {
  await nextTick()
  const rows = fundFlowRows.value
  if (!rows.length || !fundFlowChartRef.value) {
    // 卡片随 v-if 卸载后 canvas 已不在 DOM：实例留着无法复用，释放掉
    if (fundFlowChart) { fundFlowChart.dispose(); fundFlowChart = null }
    return
  }
  if (!fundFlowChart) fundFlowChart = echarts.init(fundFlowChartRef.value)
  const shown = rows.slice(-20)   // 腾讯源上限 50 根；侧栏卡只看近一个月的资金节奏
  fundFlowChart.setOption({
    grid: { left: 40, right: 6, top: 6, bottom: 18 },
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        const p = params[0]
        const row = shown[p.dataIndex]
        return `${row.date}<br/>主力净流入 <b>${p.value > 0 ? '+' : ''}${p.value}</b> 亿`
      },
    },
    xAxis: { type: 'category', data: shown.map((r) => r.date.slice(5)),
             axisLabel: { fontSize: 10, color: token('--ink-3', '#8b92a5') } },
    yAxis: { type: 'value', axisLabel: { fontSize: 10, color: token('--ink-3', '#8b92a5') },
             splitLine: { lineStyle: { color: token('--border', '#e5e7eb') } } },
    series: [{
      type: 'bar', data: shown.map((r) => r.main_net_yi), barMaxWidth: 10,
      itemStyle: { color: (p) => (p.value >= 0 ? token('--up', '#d92c2c') : token('--down', '#0e9668')) },
    }],
  }, true)
}

const loadSentiment = async (sym, seq) => {
  // SWR 首屏：情绪是参考性慢数据，先出上次快照，网络回来原地换新
  const cached = swrRead(`sent:${sym}`, 24 * 3600 * 1000)
  if (cached && typeof cached === 'object') sentiment.value = cached
  try {
    const res = await getSentiment(sym)
    if (seq === loadDataSeq && res.data?.ok) {
      sentiment.value = res.data.data || null
      if (sentiment.value) swrWrite(`sent:${sym}`, sentiment.value)
    }
  } catch {
    if (seq === loadDataSeq && !cached) sentiment.value = null
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

// ---------- 主链路（klinecharts 版，2026-10-19） ----------
const fetchKlineList = async (sym, signal) => {
  if (period.value === 'minute') {
    const res = await getMinuteKline(sym, minutePeriod.value, signal).catch(() => null)
    return (Array.isArray(res?.data) ? res.data : []).map((r) => ({
      date: r.date, open: r.open, close: r.close, high: r.high, low: r.low, volume: r.volume,
    }))
  }
  const res = await getHistory(sym, 0, 1000, period.value, signal).catch(() => null)
  return res?.data?.content || []
}

/** 图表数据落进页面状态（loader 整段取到时调用；指标卡/事件锚定/报价条都吃这里） */
const onPageData = (list) => {
  fullData.value = list
  currentData.value = list
  chartDates.value = list.map((d) => d.date)
  indicators.value = calcIndicators(list)
  // 副标题的价格兜底：stockInfo 缺席时至少能显示最近收盘
  if (list.length) lastClosePrice.value = list[list.length - 1].close
  if (period.value === 'minute' && list.length) {
    stockInfo.value = stockInfo.value || {}
    stockInfo.value.price = list[list.length - 1].close
  }
  lastUpdate.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  updateMarks()
}

/** "显示 N 根"在 klinecharts 里的映射：柱宽 = 绘图宽/N，再滚回最新。
 *  平移/缩放仍是自由的——这只设置初始/按钮点击时的可见密度。 */
const applyVisibleBars = (n) => {
  if (!chart) return
  const width = chart.getSize()?.width || chartRef.value?.clientWidth || 800
  chart.setBarSpace(Math.max(1.2, (width - 64) / Math.max(24, n)))   // 64 ≈ y 轴宽
  chart.scrollToRealTime()
}

/** 蜡烛/折线/OHLC → klinecharts 的 candle.type（面积图即折线形态） */
const applyChartType = () => {
  if (!chart) return
  const typeMap = { candlestick: 'candle_solid', line: 'area', ohlc: 'ohlc' }
  chart.setStyles({ candle: { type: typeMap[chartType.value] || 'candle_solid' } })
}

// 图表实例跨切股/切周期复用（库内换数据），key 防重复触发 loader
let lastChartKey = ''

const ensureChart = () => {
  if (chart || !chartRef.value) return chart
  chart = initKlinecharts(chartRef.value, {
    locale: 'zh-CN',
    timezone: 'Asia/Shanghai',
    styles: chartStylesOf(),
  })
  chart.setDataLoader({
    // v10 唯一数据入口。历史一次拉满（1000 根/全量分钟），无分页：
    // 左滑到底的 backward 请求直接回空 + more=false。
    getBars: async ({ type, callback }) => {
      if (type === 'backward' || type === 'forward') { callback([], false); return }
      const seq = loadDataSeq
      const sym = symbol.value
      // SWR 首屏（2026-10-24）：有快照先整段上屏，K 线毫秒级出图；网络回来后
      // 二次 callback 整体换新——"旧数据一跳变成新数据"。二次调用安全：
      // klinecharts 的 init 回调走 _addData 的 init 分支（_clearData 后整体替换，
      // 已核对 node_modules/klinecharts/dist 源码），不是追加。
      const klineKey = `kline:${sym}:${period.value === 'minute' ? `m${minutePeriod.value}` : period.value}`
      const cacheTtl = period.value === 'minute' ? 24 * 3600 * 1000 : 5 * 24 * 3600 * 1000
      const cached = swrRead(klineKey, cacheTtl)
      const cachedShown = Array.isArray(cached) && cached.length > 0
      if (cachedShown) {
        loading.value = false
        onPageData(cached)
        callback(cached.map(toBar), false)
        ensureIndicators()
        nextTick(() => applyVisibleBars(range.value))
      }
      try {
        const list = await fetchKlineList(sym, loadAbort?.signal)
        if (seq !== loadDataSeq || symbol.value !== sym) return   // 切走的请求：不回调
        loading.value = false
        if (!list.length) {
          // fetchKlineList 内部吞错（离线/被取消/上游失败都回到这里）。
          // 快照已在屏上：保留缓存图不报错不清图，SSE 与 30s 轮询会自愈；
          // 没有快照：维持原有的"空数据 + 明确报错"口径。
          if (!cachedShown) {
            error.value = period.value === 'minute'
              ? '暂无分钟K线数据（仅支持 A 股，分钟 K 由新浪提供）'
              : '暂无 K 线数据，请检查股票代码或稍后重试'
            onPageData([])
            callback([], false)
          }
        } else {
          onPageData(list)
          callback(list.map(toBar), false)
          swrWrite(klineKey, list)
          ensureIndicators()   // 必须在 callback 之后：数据先入图表再挂指标（实测无数据时 MA 会静默失败）
          nextTick(() => applyVisibleBars(range.value))
        }
      } catch (e) {
        if (seq !== loadDataSeq) return
        loading.value = false
        if (!cachedShown) {
          error.value = errorMessage(e, 'K 线数据加载失败，请检查股票代码是否正确，或确认网络后重试')
          callback([], false)
        }
      }
    },
    // 盘中"当前柱"推送口：SSE 改写当前柱 / 跨分钟滚动出新柱都走它
    subscribeBar: ({ callback: push }) => { barPush = push },
    unsubscribeBar: () => { barPush = null },
  })
  window.addEventListener('resize', handleResize)
  return chart
}

// 指标挂载。⚠️ 实测（2026-10-19 冒烟）：无数据时 createIndicator('MA') 返回 null
// 静默失败（BOLL/VOL 不受影响），所以统一放到**首批数据回调之后**创建，一次性。
let indicatorsReady = false
const ensureIndicators = () => {
  if (indicatorsReady || !chart) return
  indicatorsReady = true
  // 主图 MA/BOLL；VOL 挂到不存在的 paneId 上，库会自建副图
  chart.createIndicator({ name: 'MA', calcParams: [5, 10, 20, 60], paneId: 'candle_pane' })
  chart.createIndicator({ name: 'BOLL', calcParams: [20, 2], paneId: 'candle_pane' })
  chart.createIndicator({ name: 'VOL', calcParams: [5, 10], paneId: 'pane_vol' })
}

const loadData = async (sym) => {
  const seq = ++loadDataSeq
  if (loadAbort) loadAbort.abort()
  loadAbort = new AbortController()
  const signal = loadAbort.signal
  error.value = ''
  loading.value = true
  stockInfo.value = null
  // SWR 首屏：报价条先用上次快照（名称/现价/昨收）秒出，网络回来覆盖
  const cachedInfo = swrRead(`stock:${sym}`, 24 * 3600 * 1000)
  if (cachedInfo && typeof cachedInfo === 'object') stockInfo.value = cachedInfo
  indicators.value = null
  lastUpdate.value = ''
  currentData.value = []
  fullData.value = []
  chartDates.value = []
  if (valChart) { valChart.dispose(); valChart = null }
  loadEvents(sym, seq)
  loadSentiment(sym, seq)
  loadValuation(sym, seq)
  loadFundFlow(sym, seq)
  checkFavorite(sym)

  // SWR 首屏：图表创建不等 getStock——先 ensureChart 触发 loader（缓存 K 线毫秒级
  // 上屏），报价/K 线/估值等网络请求并行。getStock 排前面的话，慢网络下缓存图
  // 也要干等一次报价往返，秒开就名存实亡。
  await nextTick()
  if (seq === loadDataSeq && chartRef.value) {
    const c = ensureChart()
    if (c) {
      // setSymbol/setPeriod 触发 loader 重新取数；loading 的收尾在 getBars 回调里
      const key = `${sym}|${period.value}|${minutePeriod.value}`
      if (key !== lastChartKey) {
        lastChartKey = key
        c.setSymbol({ ticker: sym, pricePrecision: 2, volumePrecision: 0 })
        c.setPeriod(chartPeriodOf())
      }
      applyChartType()
    }
  }

  try {
    const stockRes = await getStock(sym, signal).catch(() => null)
    if (seq !== loadDataSeq) return
    if (stockRes?.data) {
      stockInfo.value = stockRes.data
      swrWrite(`stock:${sym}`, stockRes.data)
    } else if (!cachedInfo) {
      stockInfo.value = null
    }
    // 网络失败但有快照：保留秒出的头部，SSE/30s 轮询很快校正价格
  } catch (e) {
    if (seq !== loadDataSeq) return
    error.value = errorMessage(e, 'K 线数据加载失败，请检查股票代码是否正确，或确认网络后重试')
  } finally {
    // loader 正常路径自己收 loading；这里兜底（如图表未初始化成功）
    if (seq === loadDataSeq) {
      if (!chart) loading.value = false
    }
  }
}

// ---------- 事件 ----------

// 切换类操作（周期/分钟粒度/换票）的合并器：首次立即加载，250ms 内的连点合并为
// 一次尾沿重载。两个按钮来回快速点时，每次点击都整套重发（约 6 个接口）会把
// 浏览器并发槽和 nginx 限流同时打满 —— 那正是"加载缓慢/加载不全"的直接来源。
// 不用纯尾沿防抖：每次切换都迟 250ms 的观感在交易时段不可接受。
let reloadTimer = null
let lastReloadAt = 0
const scheduleReload = () => {
  const now = Date.now()
  if (now - lastReloadAt >= 250) {
    lastReloadAt = now
    loadData(symbol.value)
    return
  }
  if (reloadTimer) clearTimeout(reloadTimer)
  reloadTimer = setTimeout(() => {
    reloadTimer = null
    lastReloadAt = Date.now()
    loadData(symbol.value)
  }, 250)
}

const onPeriodChange = (p) => {
  period.value = p
  if (p === 'minute' && range.value > 480) range.value = 240
  scheduleReload()
}

const onMinuteChange = (m) => {
  minutePeriod.value = m
  scheduleReload()
}

const onChartTypeChange = (t) => {
  // 图形类型只影响画法不影响数据：切 candle.type 即可，零网络零重取
  chartType.value = t
  applyChartType()
}

const onRangeChange = (value) => {
  // "显示 N 根"在 klinecharts 里只是柱宽/可见密度的调整：数据已全量在图上，
  // 纯本地缩放，零网络。
  range.value = value
  applyVisibleBars(value)
}

onMounted(() => {
  const container = chartRef.value
  container?.addEventListener('mousemove', onChartMouseMove)
  container?.addEventListener('mouseleave', onChartMouseLeave)
  container?.addEventListener('click', onChartClick)
  narrowMedia?.addEventListener('change', onNarrowChange)
  themeObserver = new MutationObserver(repaintChartsForTheme)
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
  void market.load()
  loadData(route.params.symbol)
  quoteBus.connect()
  quoteBus.setFocus(route.params.symbol)
  startLive()
})

onUnmounted(() => {
  stopEventPolling()
  stopLive()
  themeObserver?.disconnect()
  themeObserver = null
  if (reloadTimer) { clearTimeout(reloadTimer); reloadTimer = null }
  loadAbort?.abort()
  quoteBus.setFocus('')
  if (fundFlowChart) { fundFlowChart.dispose(); fundFlowChart = null }
  const container = chartRef.value
  container?.removeEventListener('mousemove', onChartMouseMove)
  container?.removeEventListener('mouseleave', onChartMouseLeave)
  container?.removeEventListener('click', onChartClick)
  narrowMedia?.removeEventListener('change', onNarrowChange)
  disposeChart()
})

watch(() => route.params.symbol, (newSym) => {
  quoteBus.setFocus(newSym)
  if (newSym && newSym.toUpperCase() !== symbol.value) {
    symbol.value = newSym.toUpperCase()
    scheduleReload()
  }
})

// 指标颜色：DOM 内联样式直接用 var(--…)——主题切换由 CSS 变量自行跟随，
// 不必像 canvas 那样取字面值重绘（这两个函数只喂 DOM，不进画布）
const rsiColor = (v) => v == null
  ? 'var(--ink-3)'
  : v >= 70 ? 'var(--warn)' : v <= 30 ? 'var(--accent)' : 'var(--ink)'
const macdColor = (h) => h == null
  ? 'var(--ink-3)'
  : h >= 0 ? 'var(--up)' : 'var(--down)'
</script>

<template>
  <div>
    <!-- ==================== 区块一：报价条（同花顺式头部行情区） ==================== -->
    <div class="mb-3 rounded-xl border border-border bg-surface p-3.5">
      <div class="flex flex-wrap items-center gap-x-6 gap-y-2.5">
        <div class="flex min-w-0 items-center gap-3">
          <button
            type="button"
            class="flex h-8 shrink-0 items-center gap-1 rounded-lg px-2 text-[13px] text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
            @click="router.back()"
          >
            <ArrowLeft :size="15" :stroke-width="2" aria-hidden="true" /> 返回
          </button>
          <div class="flex min-w-0 items-baseline gap-2">
            <h1 class="truncate text-[18px] font-semibold text-ink">{{ stockInfo?.name || symbol }}</h1>
            <span class="num shrink-0 text-[12px] text-ink-3">{{ symbol }}</span>
          </div>
          <!-- 加自选：详情页直达入口（10-19 移除搜索表单后入口只剩首页查询后那条，藏太深） -->
          <button
            v-if="favState === 'none' || favState === 'adding'"
            type="button"
            class="flex h-8 shrink-0 items-center gap-1.5 rounded-lg border border-border px-2.5 text-[13px] text-ink-2 transition-colors hover:border-accent hover:text-accent disabled:opacity-60 max-sm:h-9"
            :disabled="favState === 'adding'"
            @click="addToFavorites"
          >
            <Star :size="14" :stroke-width="2" aria-hidden="true" />
            {{ favState === 'adding' ? '加入中…' : '加自选' }}
          </button>
          <span
            v-else-if="favState === 'added' || favState === 'exists'"
            class="flex h-8 shrink-0 items-center gap-1 rounded-lg px-2 text-[12.5px] text-down"
            title="已在自选中，可在首页管理买入价与预警"
          >
            <Check :size="14" :stroke-width="2" aria-hidden="true" />已自选
          </span>
          <span v-if="favError" class="shrink-0 text-[12px] text-up" role="alert">{{ favError }}</span>
        </div>

        <div v-if="liveQuote" class="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <span class="num text-[26px] font-semibold leading-none" :class="dirTextClass(liveQuote.pct ?? liveQuote.change)">¥{{ liveQuote.price.toFixed(2) }}</span>
          <span class="num text-[13px] font-medium" :class="dirTextClass(liveQuote.pct ?? liveQuote.change)">
            {{ signed(liveQuote.change) }} {{ signed(liveQuote.pct, '%') }}
          </span>
          <span v-if="lastUpdate" class="num text-[11px] text-ink-3">更新 {{ lastUpdate }}</span>
        </div>
        <p v-else-if="loading" class="text-[13px] text-ink-3">行情加载中…</p>

        <!-- 快照 chips：昨收 / 当日高低 / 量（随 30s 轮询与 SSE 补丁一起动） -->
        <dl v-if="liveQuote" class="num flex flex-wrap items-baseline gap-x-4 gap-y-1.5 text-[12px] text-ink-2">
          <div class="flex items-baseline gap-1.5"><dt class="text-ink-3">昨收</dt><dd>{{ fmtPrice(liveQuote.prev) ?? '—' }}</dd></div>
          <div class="flex items-baseline gap-1.5"><dt class="text-ink-3">最高</dt><dd class="text-up">{{ fmtPrice(lastBar?.high) ?? '—' }}</dd></div>
          <div class="flex items-baseline gap-1.5"><dt class="text-ink-3">最低</dt><dd class="text-down">{{ fmtPrice(lastBar?.low) ?? '—' }}</dd></div>
          <div class="flex items-baseline gap-1.5"><dt class="text-ink-3">量</dt><dd>{{ fmtVol(lastBar?.volume) ?? '—' }}</dd></div>
        </dl>
      </div>
    </div>

    <!-- ==================== 区块二：工具条 ==================== -->
    <!-- 代码搜索表单已按需求移除（2026-10-19）：进哪只票由首页的行情查询/自选卡决定，
         本页通过路由参数切换标的，避免两个搜索入口维护两套输入习惯 -->
    <div class="mb-3 rounded-xl border border-border bg-surface px-3.5 py-2.5">
      <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div class="flex items-center gap-1.5">
          <span class="text-[12px] text-ink-2">周期</span>
          <div class="flex gap-0.5">
            <button
              v-for="p in periods"
              :key="p.value"
              type="button"
              class="h-[26px] rounded-full px-2.5 text-[12px] transition-colors max-sm:h-[34px] max-sm:px-3"
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
              class="h-[26px] rounded-full px-2.5 text-[12px] transition-colors max-sm:h-[34px] max-sm:px-3"
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
              class="h-[26px] rounded-full px-2.5 text-[12px] transition-colors max-sm:h-[34px] max-sm:px-3"
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
          class="h-[26px] rounded-full px-2.5 text-[12px] transition-colors max-sm:h-[34px] max-sm:px-3"
          :class="range === r.value ? 'bg-accent text-on-accent' : 'text-ink-3 hover:bg-surface-2 hover:text-ink'"
          @click="onRangeChange(r.value)"
        >{{ r.label }}</button>
      </div>
    </div>

    <!-- 单列（手机）：图 → 事件 → 指标；桌面两列：图/指标在左、事件在右 -->
    <!-- 宽度跟随壳层容器（1200px 含内边距）：旧版强制 1224px 会把右栏顶出视口 48px，整页出现横向滚动 -->
    <div class="grid gap-3 [grid-template-areas:'notice''chart''events''indicators'] [grid-template-columns:minmax(0,1fr)] lg:[grid-template-areas:'notice_notice''chart_events''indicators_events'] lg:[grid-template-columns:minmax(0,1fr)_360px] lg:items-start">
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
        <!-- 图表头：标题/副标题原是 ECharts 画布内文字，klinecharts 无内建标题，移到 DOM -->
        <div v-if="chart" class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 px-1">
          <p class="text-[14px] font-semibold text-ink">
            {{ stockInfo?.name || symbol }}
            <span class="ml-1 text-[12px] font-normal text-ink-3">
              {{ period === 'minute' ? `${minutePeriod}分K` : { day: '日K', week: '周K', month: '月K' }[period] }}
            </span>
          </p>
          <p class="num text-[11.5px] text-ink-3">{{ chartSubtext }}</p>
        </div>
        <div ref="chartRef" class="h-[clamp(320px,45dvh,560px)] rounded-xl border border-border bg-surface p-2"></div>
        <div class="flex justify-end">
          <button
            type="button"
            class="rounded-md border border-border px-2 py-0.5 text-[11.5px] text-ink-2 transition-colors hover:border-accent hover:text-accent max-sm:py-1.5"
            @click="chart?.scrollToRealTime()"
          >回到最新</button>
        </div>

        <!-- 估值分位副图：PE-TTM 滚动 5 年分位（与委员会证据、规则 pe_percentile 同一口径） -->
        <div
          v-if="valLatest"
          class="mt-2.5 rounded-xl border border-border bg-surface p-3"
        >
          <div class="mb-1 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <p class="text-[13px] font-semibold text-ink">
              估值分位
              <span class="ml-1.5 text-[11.5px] font-normal text-ink-3">PE-TTM 处于近 5 年的百分位（0=最便宜，100=最贵）</span>
            </p>
            <p class="num text-[12px] text-ink-2">
              最新 <span class="font-semibold" :class="valTone(valLatest.pct)">{{ valLatest.pct }}%</span>
              <span class="text-ink-3">· PE {{ valLatest.pe.toFixed(2) }} · {{ valLatest.date }}</span>
            </p>
          </div>
          <div ref="valChartRef" class="h-[150px] w-full" role="img" aria-label="PE-TTM 近 5 年分位走势"></div>
        </div>

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

      <!-- 右侧信息栏（区块化 2026-10-15）：散户情绪 / 事件与解读 各自独立成卡 -->
      <aside class="flex min-w-0 flex-col gap-3 [grid-area:events] lg:sticky lg:top-3 lg:max-h-[calc(100dvh-24px)] lg:overflow-y-auto">
        <!-- 区块：散户情绪 -->
        <BlockCard
          v-if="sentiment"
          title="散户情绪"
          collapsible
          storage-key="kchart-sentiment"
          body-class="pb-3.5"
        >
          <template #actions>
            <span class="rounded-full border border-border px-1.5 text-[11px] text-ink-3">未验证信号</span>
          </template>
          <div class="num flex flex-wrap gap-x-3 gap-y-1 text-[12px] text-ink-2">
            <span v-if="sentiment.desire?.latest != null">
              参与意愿 {{ sentiment.desire.latest.toFixed(0) }}<template v-if="sentiment.desire.change != null">（{{ sentiment.desire.change > 0 ? '+' : '' }}{{ sentiment.desire.change.toFixed(1) }}）</template>
            </span>
            <span v-if="sentiment.focus?.latest != null">关注 {{ sentiment.focus.latest.toFixed(0) }}</span>
            <span v-if="sentiment.score?.latest != null">千股千评 {{ sentiment.score.latest.toFixed(0) }}</span>
          </div>
          <p class="mt-1.5 text-[11px] leading-relaxed text-ink-3">{{ sentimentVerdict }}</p>
        </BlockCard>

        <!-- 区块：主力资金（个股口径：这只票的钱在进还是出；与首页大盘卡互补） -->
        <BlockCard
          v-if="fundFlowRows.length"
          title="主力资金"
          hint="主力净流入=超大单+大单（腾讯口径，正=流入红、负=流出绿）；四档为当日快照，日频收盘后更新。"
          collapsible
          storage-key="kchart-fund-flow"
          body-class="pb-3"
        >
          <template #actions>
            <span class="num text-[11px] text-ink-3">截至 {{ fundFlowLatest.date.slice(5) }}</span>
          </template>
          <div class="num flex flex-wrap items-baseline gap-x-3 gap-y-1 px-4 pt-3 text-[12px] text-ink-2">
            <span>今日主力
              <span class="font-semibold" :class="dirTextClass(fundFlowLatest.main_net_yi)">
                {{ signed(fundFlowLatest.main_net_yi) }} 亿
              </span></span>
            <span v-if="fundFlowLatest.super_net_yi != null">超大单
              <span :class="dirTextClass(fundFlowLatest.super_net_yi)">{{ signed(fundFlowLatest.super_net_yi) }}</span></span>
            <span v-if="fundFlowLatest.big_net_yi != null">大单
              <span :class="dirTextClass(fundFlowLatest.big_net_yi)">{{ signed(fundFlowLatest.big_net_yi) }}</span></span>
            <span v-if="fundFlowLatest.mid_net_yi != null">中单
              <span :class="dirTextClass(fundFlowLatest.mid_net_yi)">{{ signed(fundFlowLatest.mid_net_yi) }}</span></span>
            <span v-if="fundFlowLatest.small_net_yi != null">小单
              <span :class="dirTextClass(fundFlowLatest.small_net_yi)">{{ signed(fundFlowLatest.small_net_yi) }}</span></span>
          </div>
          <div ref="fundFlowChartRef" class="mx-2 mt-2 h-[120px]" role="img" aria-label="近 20 日该股主力净流入柱状图"></div>
        </BlockCard>

        <!-- 区块：事件与解读 -->
        <BlockCard
          title="事件与解读"
          hint="解读由 AI 生成，仅供参考，不构成投资建议。"
          collapsible
          storage-key="kchart-events"
          body-class="p-4 pt-3"
        >
          <p v-if="eventsError" class="text-[12.5px] text-up" role="alert">{{ eventsError }}</p>
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

          <!-- 「当前怎么看」：整段连贯判断 -->
          <div v-if="stockRead" class="mt-2.5 rounded-lg border-l-[3px] border-accent bg-surface-2 px-3 py-2.5">
          <p class="mb-1 text-[12px] font-semibold text-accent">当前怎么看</p>
          <div class="md-body text-[13px] leading-7 text-ink" v-html="renderMarkdown(stockRead)"></div>
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
            :style="{ borderLeftColor: colorVarForNumber(index + 1) }"
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
                class="ml-2 rounded-md border border-border px-1.5 py-0.5 text-[11.5px] text-ink-2 transition-colors hover:border-accent hover:text-accent max-sm:py-1.5"
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
        </BlockCard>
      </aside>

      <!-- 区块：技术指标 -->
      <BlockCard
        v-if="indicators"
        class="[grid-area:indicators]"
        title="技术指标"
        hint="RSI/MACD/BOLL 由最新收盘序列计算，随盘中轮询更新。"
        collapsible
        storage-key="kchart-indicators"
        body-class="p-4"
      >
        <div class="flex flex-wrap gap-x-4 gap-y-2.5">
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
      </BlockCard>
    </div>

    <!-- 聚焦聊天抽屉 -->
    <ChatDrawer :open="chatOpen" :focus-event="chatFocus" @close="chatOpen = false" />
  </div>
</template>
