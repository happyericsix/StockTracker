<script setup>
/**
 * 策略详情：运行状态 / 配置 / 决策来源 / 可验证预期 / 回测 / 模型诊断 /
 * 模拟盘账户 / 交易 / 决策日志 / 净值曲线 / 结算痕迹。
 * 逻辑自旧版原样移植（零漂移）：口径纪律、图表令牌同源、快照不美化。
 */
import { ref, computed, nextTick, watch, onMounted, onUnmounted } from 'vue'
import { init as initKlinecharts, dispose as disposeKlinecharts, registerOverlay } from 'klinecharts'
import { useRouter, useRoute } from 'vue-router'
import { ArrowLeft, AlertTriangle } from 'lucide-vue-next'
import InfoTip from '../components/InfoTip.vue'
import StrategyEditSheet from '../components/StrategyEditSheet.vue'
import Badge from '../components/ui/badge.vue'
import Button from '../components/ui/button.vue'
import Empty from '../components/ui/empty.vue'
import Skeleton from '../components/ui/skeleton.vue'
import * as echarts from 'echarts'
import { quoteBus } from '../composables/quoteBus.js'
import {
  listStrategies, runBacktest, getStrategyDiagnostic, startPaper, stopPaper,
  getPaperAccount, getPaperTrades, getPaperTraces, getPaperEquity,
  getExpectation, registerExpectation, getPaperOverview,
  getStrategyJournal, enableShare, disableShare,
  getRevisions, getRevisionHealth, generateRevision,
  approveRevision, rejectRevision,
} from '../api/strategy.js'
import { getHistory } from '../api/stock.js'
import { dirTextClass } from '../utils/format.js'

const router = useRouter()
const route = useRoute()

const editOpen = ref(false)

const strategy = ref(null)
const account = ref(null)
const trades = ref([])
const traces = ref([])
const tracesError = ref('')
const equity = ref(null)
const equityError = ref('')
const loading = ref(false)
const notFound = ref(false)
const error = ref('')
const backtestLoading = ref(false)
const backtestData = ref(null)
const paperLoading = ref(false)
const diagnosticLoading = ref(false)
const diagnosticData = ref(null)
const diagnosticError = ref('')
const backtestChartRef = ref(null)
let backtestChart = null
const expectation = ref(null)
const expectationError = ref('')
const expectationLoading = ref(false)
const expectationForm = ref({ metric: 'excess_vs_buy_and_hold_pct', threshold: 0, horizonDays: 28 })
const expectationNotice = ref('')
const equityChartRef = ref(null)
let equityChart = null
const overviewItem = ref(null)

const errorMessage = (e, fallback) => e?.friendlyMessage || e?.response?.data?.message || fallback

// 图表颜色与 CSS 令牌同源（读不到交给 ECharts 默认色板兜底）
const colorToken = (name) => {
  if (typeof document === 'undefined') return undefined
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value && !value.startsWith('var(') ? value : undefined
}

const formatTime = (iso) => {
  if (!iso) return 'N/A'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return 'N/A'
  return d.toLocaleString('zh-CN', { hour12: false })
}

/** 快照是"当时的证据"，原样展示、不做美化；解析失败就原样显示字符串 */
const prettySnapshot = (json) => {
  if (!json) return ''
  try {
    return JSON.stringify(JSON.parse(json), null, 2)
  } catch {
    return json
  }
}

// 模拟盘的账是人民币（整条链路按 A 股规则结算）
const formatMoney = (value) => {
  if (value === null || value === undefined) return 'N/A'
  const n = Number(value)
  if (Number.isNaN(n)) return 'N/A'
  return n.toLocaleString('zh-CN', { style: 'currency', currency: 'CNY' })
}

const formatNumber = (value, digits = 2) => {
  if (value === null || value === undefined) return 'N/A'
  const n = Number(value)
  if (Number.isNaN(n)) return 'N/A'
  return n.toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits })
}

const formatPct = (value) => {
  if (value === null || value === undefined) return 'N/A'
  const n = Number(value)
  if (Number.isNaN(n)) return 'N/A'
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

// 交易方向统一中文（接口可能返回 buy/BUY/sell/SELL）
const sideLabel = (side) => {
  const s = String(side ?? '').toLowerCase()
  if (s === 'buy') return '买入'
  if (s === 'sell') return '卖出'
  return side || 'N/A'
}

const accountEvaluated = computed(() => Boolean(account.value?.lastEvalAt))

/** 持仓市值 = 股数 × 最新价（拿不到就 null，不拿成本冒充市值） */
const positionValue = computed(() => {
  const a = account.value
  if (!a || a.lastPrice === null || a.lastPrice === undefined) return null
  const shares = Number(a.shares)
  const price = Number(a.lastPrice)
  if (Number.isNaN(shares) || Number.isNaN(price)) return null
  return shares * price
})

const prettyConfig = computed(() => {
  const cfg = strategy.value?.configJson
  if (!cfg) return '{}'
  if (typeof cfg === 'string') {
    try {
      return JSON.stringify(JSON.parse(cfg), null, 2)
    } catch {
      return cfg
    }
  }
  return JSON.stringify(cfg, null, 2)
})

const backtestResult = computed(() => {
  if (backtestData.value === null || backtestData.value === undefined) return null
  if (typeof backtestData.value === 'string') {
    try {
      return JSON.parse(backtestData.value)
    } catch {
      return { error: '回测结果解析失败，请重新运行回测' }
    }
  }
  if (backtestData.value.valid === false) return { error: backtestData.value.error || '策略校验失败，请检查策略配置后重试' }
  return backtestData.value.backtest || backtestData.value
})

const diagnostic = computed(() => {
  const data = diagnosticData.value
  if (!data || typeof data !== 'object') return null
  return {
    symbol: data.symbol || '',
    risk: data.risk || null,
    model_status: data.model_status || data.modelStatus || null,
    model_consensus: data.model_consensus || data.modelConsensus || null,
    disclaimer: data.disclaimer || '',
  }
})

const renderBacktestChart = async () => {
  await nextTick()
  const el = backtestChartRef.value
  const result = backtestResult.value
  if (!el || !result || result.error || !result.equity_curve) return

  if (!backtestChart) backtestChart = echarts.init(el)

  const strategyCurve = Array.isArray(result.equity_curve) ? result.equity_curve : []
  const benchmarkCurve = Array.isArray(result.benchmark_equity_curve) ? result.benchmark_equity_curve : []
  const dates = strategyCurve.map((point) => point.date || '')

  const accentColor = colorToken('--accent')
  const accentSoftColor = colorToken('--accent-soft')
  const mutedColor = colorToken('--ink-3')

  backtestChart.setOption({
    tooltip: { trigger: 'axis', confine: true },
    legend: { data: ['策略权益', '买入持有'], top: 0 },
    grid: { left: 70, right: 20, top: 36, bottom: 36 },
    xAxis: { type: 'category', data: dates, boundaryGap: false },
    yAxis: { type: 'value', scale: true },
    series: [
      {
        name: '策略权益',
        type: 'line',
        showSymbol: false,
        smooth: true,
        data: strategyCurve.map((point) => point.equity),
        // 图例图标 = 线段 + 空心圆点（itemStyle），两者必须同源
        lineStyle: { width: 2, color: accentColor },
        itemStyle: { color: accentColor },
        areaStyle: { color: accentSoftColor },
      },
      {
        name: '买入持有',
        type: 'line',
        showSymbol: false,
        smooth: true,
        data: benchmarkCurve.map((point) => point.equity),
        lineStyle: { width: 1.5, type: 'dashed', color: mutedColor },
        itemStyle: { color: mutedColor },
      },
    ],
  }, { notMerge: true })
}

const journal = ref(null)
const journalError = ref('')

const decisionLabel = (decision) => {
  if (decision === 'buy') return '买入'
  if (decision === 'sell') return '卖出'
  return '不动'
}

const loadJournal = async () => {
  journalError.value = ''
  try {
    const res = await getStrategyJournal(strategy.value.id)
    journal.value = res.data || null
  } catch (e) {
    journal.value = null
    journalError.value = errorMessage(e, '决策日志加载失败')
  }
}

const loadPaper = async () => {
  try {
    const res = await getPaperAccount(strategy.value.id)
    account.value = res.data
  } catch {
    account.value = null
  }
  try {
    const res = await getPaperTrades(strategy.value.id)
    const data = res.data
    trades.value = Array.isArray(data) ? data : []
  } catch {
    trades.value = []
  }
  await loadTraces()
  await loadEquity()
  await loadJournal()
  await loadOpsChart()
}

/** 冷启动（样本不足两天）：回撤/买入持有/超额在数学上需要至少两天的净值路径。
 *  这个阶段用"次日起"和说明行替代裸 N/A——一排 N/A 看起来像坏了，实际是还没开始算。 */
const equityColdStart = computed(() => {
  const days = equity.value?.summary?.days
  return days != null && days < 2
})

/** 净值曲线 + 机会成本：汇总由后端同一份算法给出，前端只显示 */
const loadEquity = async () => {
  equityError.value = ''
  try {
    const res = await getPaperEquity(strategy.value.id)
    equity.value = res.data || null
  } catch (e) {
    equity.value = null
    equityError.value = errorMessage(e, '净值曲线加载失败')
  }
  await renderEquityChart()
}

/** 口径分界线：决策来源切换日 + 参数修订日（两侧曲线不是同一个口径，不能直接比） */
const boundaryMarkLine = (dates, mutedColor) => {
  const lines = []
  const since = strategy.value?.decisionModeSince ? String(strategy.value.decisionModeSince) : ''
  if (since && dates.includes(since)) {
    lines.push({ xAxis: since, label: { formatter: '切换决策来源', color: mutedColor } })
  }
  const configAt = strategy.value?.configUpdatedAt ? String(strategy.value.configUpdatedAt).slice(0, 10) : ''
  // 与决策来源线同日时只画一条（两条完全重合的虚线只会让标签叠成墨团）
  if (configAt && configAt !== since && dates.includes(configAt)) {
    lines.push({ xAxis: configAt, label: { formatter: '参数修订', color: mutedColor } })
  }
  return lines.length
    ? { symbol: 'none', lineStyle: { color: mutedColor, type: 'dashed' }, data: lines }
    : undefined
}

/** 只画后端给的点，并在口径变更日画竖线（决策来源切换 / 参数修订） */
const renderEquityChart = async () => {
  await nextTick()
  const el = equityChartRef.value
  const points = equity.value?.points
  if (!el || !Array.isArray(points) || points.length === 0) return

  if (!equityChart) equityChart = echarts.init(el)

  const accentColor = colorToken('--accent')
  const accentSoftColor = colorToken('--accent-soft')
  const mutedColor = colorToken('--ink-3')
  const dates = points.map((point) => String(point.tradeDate || ''))
  const markLine = boundaryMarkLine(dates, mutedColor)

  equityChart.setOption({
    tooltip: { trigger: 'axis', confine: true },
    grid: { left: 70, right: 20, top: 20, bottom: 36 },
    xAxis: { type: 'category', data: dates, boundaryGap: false },
    yAxis: { type: 'value', scale: true },
    series: [
      {
        name: '账户净值',
        type: 'line',
        showSymbol: false,
        data: points.map((point) => point.equity),
        lineStyle: { width: 2, color: accentColor },
        itemStyle: { color: accentColor },
        areaStyle: { color: accentSoftColor },
        markLine,
      },
    ],
  }, { notMerge: true })
}

// ---- 操作 K 线（klinecharts v10，2026-10-19 与主图统一引擎）----
// 把每笔成交钉到标的行情上（在哪买的、在哪卖的，一眼可见）。
// 成交标记 = 自定义 tradeMarker overlay（▲买/▼卖 + 时刻小字），锚点是
// (当日时间戳, 外移价位)——价位在页面侧算好，同日同向多笔逐级外移与旧实现同款；
// 悬停/点按标记在图下信息条看成交明细（替代旧 ECharts item tooltip）。
// 口径分界线复用 verticalStraightLine；范围按钮（近N根）= 柱宽+滚回最新。
const opsChartRef = ref(null)
let opsChart = null
// subscribeBar 注册的"当前柱"推送口：盘中 3 秒行情改写当日那根日线
let opsBarPush = null
const opsError = ref('')
const opsBars = ref(120)
const opsHiddenTrades = ref(0)
/** 悬停/点按标记时的图内浮动卡（e.x/e.y 是相对图表容器的坐标，klinecharts 约定） */
const opsHover = ref(null)
const opsHoverStyle = computed(() => {
  if (!opsHover.value) return {}
  const width = opsChartRef.value?.clientWidth || 800
  // 靠边翻转：不超出容器，也不压顶
  const x = Math.min(Math.max(opsHover.value.x, 100), width - 100)
  const y = Math.max(opsHover.value.y, 104)
  return { left: `${x}px`, top: `${y}px`, transform: 'translate(-50%, calc(-100% - 12px))' }
})
let opsRawBars = []
let opsDecorated = false   // 指标/标记只在首批数据回调后挂一次（无数据时挂 MA 会静默失败，实测）

const OPS_RANGES = [
  { label: '近60根', value: 60 },
  { label: '近120根', value: 120 },
  { label: '近250根', value: 250 },
]

const toOpsTimestamp = (dateStr) => Date.parse(`${String(dateStr).replace(' ', 'T')}+08:00`)
const toOpsBar = (r) => ({
  timestamp: toOpsTimestamp(r.date),
  open: +r.open, high: +r.high, low: +r.low, close: +r.close, volume: +r.volume || 0,
})

// 成交标记 overlay：三角形围绕锚点坐标画（买 ▲ 指向蜡烛、卖 ▼ 背向），
// 颜色/方向/时刻标签都走 extendData，样式形状不猜——canvas 里画什么由这里说了算
let tradeMarkerRegistered = false
const registerTradeMarkerOverlay = () => {
  if (tradeMarkerRegistered) return
  tradeMarkerRegistered = true
  registerOverlay({
    name: 'tradeMarker',
    totalStep: 1,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ overlay, coordinates }) => {
      const { side, time, color } = overlay.extendData || {}
      if (!coordinates[0]) return []
      const { x, y } = coordinates[0]
      const s = 6
      // 命中策略（2026-10-19 修）：三角形本身只有 ~12px，悬停/点按很难命中。
      // 改为透明大圆（r=14）负责事件命中，三角形与时间文字 ignoreEvent 只做显示。
      const figures = [
        { type: 'circle', attrs: { x, y, r: 14 },
          styles: { color: 'rgba(0,0,0,0)', style: 'fill' } },
        { type: 'polygon',
          attrs: { coordinates: side === 'buy'
            ? [{ x, y: y - s }, { x: x - s, y: y + s * 0.7 }, { x: x + s, y: y + s * 0.7 }]
            : [{ x, y: y + s }, { x: x - s, y: y - s * 0.7 }, { x: x + s, y: y - s * 0.7 }] },
          styles: { color, style: 'fill' }, ignoreEvent: true },
      ]
      if (time) {
        figures.push({
          type: 'text',
          attrs: { x, y: side === 'buy' ? y + s * 0.7 + 12 : y - s * 0.7 - 6, text: time },
          styles: { color, size: 10, align: 'center' }, ignoreEvent: true,
        })
      }
      return figures
    },
  })
}

const opsStylesOf = () => {
  const up = colorToken('--up') || '#d92c2c'
  const down = colorToken('--down') || '#0e9668'
  const border = colorToken('--border') || '#e3e6ec'
  const ink3 = colorToken('--ink-3') || '#8b92a5'
  return {
    candle: {
      bar: {
        upColor: up, downColor: down, noChangeColor: ink3,
        upBorderColor: up, downBorderColor: down, noChangeBorderColor: ink3,
        upWickColor: up, downWickColor: down, noChangeWickColor: ink3,
      },
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

const handleOpsResize = () => opsChart && opsChart.resize()

/** "近N根"= 柱宽 + 滚回最新（数据全量在图上，纯缩放零重取；滚轮拖拽仍自由） */
const applyOpsRange = (n) => {
  if (!opsChart) return
  const width = opsChart.getSize()?.width || opsChartRef.value?.clientWidth || 800
  opsChart.setBarSpace(Math.max(1.2, (width - 64) / Math.max(24, n)))
  opsChart.scrollToRealTime()
}

const changeOpsRange = async (n) => {
  if (opsBars.value === n) return
  opsBars.value = n
  applyOpsRange(n)
}

/** 成交标记 + 口径分界线（锚定 timestamp，随缩放平移自动跟随） */
const rebuildOpsMarkers = () => {
  if (!opsChart) return
  opsHover.value = null
  opsChart.removeOverlay({ groupId: 'ops-trade' })
  opsChart.removeOverlay({ groupId: 'ops-boundary' })

  const up = colorToken('--up') || '#d92c2c'
  const down = colorToken('--down') || '#0e9668'
  const timeOf = (t) => {
    const m = String(t.createdAt || '').match(/T(\d{2}:\d{2})/)
    return m ? m[1] : ''
  }
  const showLabels = trades.value.length <= 40   // 交易太密时标签只会糊成一片，不硬塞
  const barByDate = new Map(opsRawBars.map((d) => [d.date, d]))
  const stackCount = new Map()
  const markers = []
  let hidden = 0
  for (const t of trades.value) {
    const side = String(t.side ?? '').toLowerCase()
    if (side !== 'buy' && side !== 'sell') continue
    const day = String(t.tradeDate ?? '').slice(0, 10)
    const bar = barByDate.get(day)
    if (!bar) { hidden++; continue }
    // 买 ▲ 钉在当日最低价下方、卖 ▼ 钉在最高价上方（不压蜡烛本体），同日同向逐级外移
    const high = +bar.high
    const low = +bar.low
    const span = Math.max(high - low, high * 0.004)
    const k = stackCount.get(day + side) || 0
    stackCount.set(day + side, k + 1)
    const y = side === 'buy'
      ? low - span * (0.35 + k * 0.4)
      : high + span * (0.35 + k * 0.4)
    markers.push({
      name: 'tradeMarker',
      groupId: 'ops-trade',
      // 不 lock：lock=true 时 klinecharts 直接不派发 overlay 事件（源码级行为）。
      // 分界线可以 lock，成交标记必须可交互
      points: [{ timestamp: toOpsTimestamp(day), value: y }],
      extendData: { side, time: showLabels ? timeOf(t) : '', color: side === 'buy' ? up : down, trade: t },
      onMouseEnter: (e) => { opsHover.value = { trade: t, x: e.x, y: e.y } },
      onMouseLeave: () => { opsHover.value = null },
      onClick: (e) => { opsHover.value = { trade: t, x: e.x, y: e.y } },
    })
  }
  opsHiddenTrades.value = hidden
  if (markers.length) opsChart.createOverlay(markers)

  // 口径分界线（与净值曲线同一语义：决策来源切换 / 参数修订；同日只画一条）
  const daySet = new Set(opsRawBars.map((d) => d.date))
  const since = strategy.value?.decisionModeSince ? String(strategy.value.decisionModeSince) : ''
  const configAt = strategy.value?.configUpdatedAt ? String(strategy.value.configUpdatedAt).slice(0, 10) : ''
  const boundaries = []
  if (since && daySet.has(since)) boundaries.push(since)
  if (configAt && configAt !== since && daySet.has(configAt)) boundaries.push(configAt)
  if (boundaries.length) {
    opsChart.createOverlay(boundaries.map((date) => ({
      name: 'verticalStraightLine',
      groupId: 'ops-boundary',
      lock: true,
      points: [{ timestamp: toOpsTimestamp(date) }],
      styles: { line: { color: colorToken('--ink-3') || '#8b92a5', style: 'dashed', size: 1 } },
    })))
  }
}

// ---------- 实时补丁（2026-10-21）：操作K线的当日蜡烛跟随 3 秒行情 ----------
// 与个股K线页同一快车道：setFocus(strategy.symbol) 让后端 3 秒推这只票，
// 推送只改写"当日"那根日线的 close/high/low（成交标记不动——它们钉在成交
// 那一刻；最后一根还不是今天时也不改，不能用今天的价改写历史柱）。
const opsInMarketHours = () => {
  const d = new Date()
  const day = d.getDay()
  if (day === 0 || day === 6) return false
  const m = d.getHours() * 60 + d.getMinutes()
  return (m >= 555 && m <= 690) || (m >= 780 && m <= 905)   // 09:15-11:30 / 13:00-15:05
}

/** SSE 批次里找本策略标的的条目（键可能带市场前缀，做精确/后缀两级匹配） */
const opsLiveQuote = () => {
  const sym = strategy.value?.symbol
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

const patchOpsLivePrice = (price) => {
  if (!Number.isFinite(price) || !opsRawBars.length || !opsChart) return
  const last = opsRawBars[opsRawBars.length - 1]
  if (String(last.date).slice(0, 10) !== new Date().toLocaleDateString('sv-SE')) return
  last.close = price
  last.high = Math.max(+last.high || price, price)
  last.low = Math.min(+last.low || price, price)
  opsBarPush?.(toOpsBar(last))
}

watch(() => opsLiveQuote()?.at, () => {
  if (opsInMarketHours()) patchOpsLivePrice(opsLiveQuote()?.price)
})

const ensureOpsChart = () => {
  if (opsChart || !opsChartRef.value) return opsChart
  registerTradeMarkerOverlay()
  opsChart = initKlinecharts(opsChartRef.value, {
    locale: 'zh-CN',
    timezone: 'Asia/Shanghai',
    styles: opsStylesOf(),
  })
  opsChart.setDataLoader({
    // 全量日线一次喂入（loadOpsChart 已先行拉好）；左右边界请求回空 + 无更多
    getBars: async ({ type, callback }) => {
      if (type === 'backward' || type === 'forward') return callback([], false)
      callback(opsRawBars.map(toOpsBar), false)
      if (!opsDecorated) {
        opsDecorated = true
        opsChart.createIndicator({ name: 'MA', calcParams: [5, 20, 60], paneId: 'candle_pane' })
        rebuildOpsMarkers()
        // 库的首轮自适应布局在数据入图后才跑，立刻 setBarSpace 会被它顶掉
        // （冒烟实测：首屏变成全量 500+ 根）。等两帧让布局稳定再设窗口。
        requestAnimationFrame(() => requestAnimationFrame(() => applyOpsRange(opsBars.value)))
      }
    },
    // 盘中 3 秒行情的"当前柱"推送口（2026-10-21 接入，见 patchOpsLivePrice）
    subscribeBar: ({ callback: push }) => { opsBarPush = push },
    unsubscribeBar: () => { opsBarPush = null },
  })
  window.addEventListener('resize', handleOpsResize)
  return opsChart
}

const opsDataReady = ref(false)

/** 图表挂载时机 = 数据就绪 && 容器在 DOM。首次加载时页面骨架（loading）在
 *  loadDetail 收尾才卸下，容器渲染晚于 loadOpsChart 拿到数据——在 loadOpsChart
 *  里 nextTick 后直接 init 会撞上 ref 还是 null、图表静默消失（2026-10-19 冒烟
 *  实测）。双向 watch 覆盖"数据先到"与"容器先挂"两种先后。 */
const mountOpsChart = () => {
  if (!strategy.value?.symbol || !ensureOpsChart()) return
  opsChart.setSymbol({ ticker: strategy.value.symbol, pricePrecision: 2, volumePrecision: 0 })
  opsChart.setPeriod({ type: 'day', span: 1 })
  // 挂载时机由 watch 驱动，可能撞上容器 0 尺寸的时刻（canvas 停在 300x150 默认值、
  // 库不会自动补 resize，冒烟实测）。等布局稳定强制 resize 兜底。
  requestAnimationFrame(() => requestAnimationFrame(() => opsChart?.resize()))
}
watch(opsChartRef, (el) => { if (el && opsDataReady.value) mountOpsChart() })
watch(opsDataReady, (v) => { if (v && opsChartRef.value) mountOpsChart() })

const loadOpsChart = async () => {
  opsError.value = ''
  opsDataReady.value = false
  if (!strategy.value?.symbol) return
  try {
    // 与 KLineChart 同源同一接口：page=0 size=1000 拿全量日线，窗口在渲染时切
    const res = await getHistory(strategy.value.symbol, 0, 1000, 'day')
    opsRawBars = res.data?.content || []
    if (!Array.isArray(opsRawBars) || opsRawBars.length === 0) {
      opsError.value = 'K 线数据为空，暂时画不了操作图'
      return
    }
    opsDataReady.value = true   // 挂载交给 watch：容器可能还在骨架里没渲染
  } catch (e) {
    opsError.value = errorMessage(e, 'K 线数据加载失败，暂时画不了操作图')
  }
}

const METRIC_OPTIONS = [
  { value: 'excess_vs_buy_and_hold_pct', label: '相对买入持有的超额（%）' },
  { value: 'return_pct', label: '账户收益（%）' },
  { value: 'max_drawdown_pct', label: '最大回撤（%，越大越好）' },
]

const EXPECTATION_STATUS_LABELS = {
  pending: '尚未到期',
  met: '已达成',
  unmet: '未达成',
  unmeasurable: '算不出来',
}

const expectationStatusLabel = (status) => EXPECTATION_STATUS_LABELS[status] || status || '未知'
const expectationTone = (status) =>
  status === 'met' ? 'down' : status === 'unmet' ? 'up' : status === 'pending' ? 'warn' : 'neutral'

const loadExpectation = async () => {
  expectationError.value = ''
  try {
    const res = await getExpectation(strategy.value.id)
    expectation.value = res.data || null
  } catch (e) {
    expectation.value = null
    expectationError.value = errorMessage(e, '预期加载失败')
  }
}

const submitExpectation = async () => {
  expectationLoading.value = true
  expectationError.value = ''
  expectationNotice.value = ''
  try {
    const res = await registerExpectation(strategy.value.id, {
      metric: expectationForm.value.metric,
      threshold: Number(expectationForm.value.threshold),
      horizonDays: Number(expectationForm.value.horizonDays),
    })
    expectation.value = res.data || null
    expectationNotice.value = '已登记。到期后由每日结算自动判定达成与否。'
  } catch (e) {
    expectationError.value = errorMessage(e, '预期登记失败')
  } finally {
    expectationLoading.value = false
  }
}

const decisionMode = computed(() => strategy.value?.decisionMode || 'rule')
const decisionModeLabel = (mode) => (mode || 'rule') === 'agent' ? '多角色委员会（agent）' : '规则引擎（rule）'

// ---- 对账可视化（2026-10-16）：模式命中率对比 + 不动原因分布，全部从 journal 派生 ----
const modeStats = computed(() => {
  const byMode = {}
  for (const e of journal.value?.entries || []) {
    if (e.verdict !== 'hit' && e.verdict !== 'miss') continue
    const mode = e.decisionMode === 'agent' ? 'agent' : 'rule'
    const s = byMode[mode] || (byMode[mode] = { mode, hits: 0, misses: 0 })
    if (e.verdict === 'hit') s.hits += 1
    else s.misses += 1
  }
  return Object.values(byMode).map((s) => ({
    ...s,
    total: s.hits + s.misses,
    rate: s.total ? Math.round((s.hits / s.total) * 100) : null,
  }))
})

const skipReasons = computed(() => {
  const counts = {}
  let total = 0
  for (const e of journal.value?.entries || []) {
    if (e.verdict || !e.skipReason) continue
    counts[e.skipReason] = (counts[e.skipReason] || 0) + 1
    total += 1
  }
  return { total, items: Object.entries(counts)
    .map(([reason, n]) => ({ reason, n }))
    .sort((a, b) => b.n - a.n) }
})

const SKIP_REASON_LABELS = {
  agent_no_new_information: '没触发开会',
  agent_budget_exceeded: '预算用尽',
  data_unavailable: '数据不可用',
  state_mismatch: '信号矛盾',
}

/** 分布条配色：同族蓝灰渐进，不与红绿涨跌语义打架（分布不是方向） */
const SKIP_COLORS = ['#3b6df6', '#7aa2f7', '#a8bffc', '#cbd8f9', '#e4ebfb']
const isAgentMode = computed(() => decisionMode.value === 'agent')

// 止损类条件（DSL 封闭集三类）：退出规则一条都没有时给出配置层提示
const STOP_CONDITION_TYPES = ['stop_loss_pct', 'take_profit_pct', 'trailing_stop_pct']
const hasStopCondition = computed(() => {
  const cfg = strategy.value?.configJson
  if (!cfg) return true
  const parsed = typeof cfg === 'string' ? safeParseConfig(cfg) : cfg
  const conditions = parsed?.exit?.conditions
  if (!Array.isArray(conditions)) return true
  return conditions.some((condition) => STOP_CONDITION_TYPES.includes(condition?.type))
})

function safeParseConfig(json) {
  try {
    return JSON.parse(json)
  } catch {
    return null
  }
}

/** 痕迹：回答"为什么今天没成交"。失败也要说出来，留白会被读成"什么都没发生" */
const loadTraces = async () => {
  tracesError.value = ''
  try {
    const res = await getPaperTraces(strategy.value.id)
    const data = res.data
    traces.value = Array.isArray(data) ? data : []
  } catch (e) {
    traces.value = []
    tracesError.value = errorMessage(e, '痕迹加载失败，暂时看不到"为什么没成交"')
  }
}

/** 盘中痕迹有 barTime；日线痕迹只有 tradeDate，不造一个不存在的时间 */
const traceTimeLabel = (trace) =>
  trace.barTime ? formatTime(trace.barTime) : (trace.tradeDate || '')

const hasTraceMeta = (trace) => Boolean(
  trace.barClose || trace.fillBasis || trace.engineVersion || trace.adjustMode
  || trace.decisionMode || trace.trigger
)

/** 顶部状态条取总览里这一条（口径只由后端实现一处） */
const loadOverview = async () => {
  try {
    const res = await getPaperOverview()
    const list = Array.isArray(res.data) ? res.data : []
    overviewItem.value = list.find((row) => String(row.strategyId) === String(strategy.value.id)) || null
  } catch {
    overviewItem.value = null
  }
}

const loadDetail = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await listStrategies()
    const data = res.data
    const list = Array.isArray(data) ? data : []
    const found = list.find((item) => String(item.id) === String(route.params.id))
    if (found) {
      strategy.value = found
      quoteBus.setFocus(found.symbol)   // 3 秒快车道：告诉后端本页盯着这只票
      await loadPaper()
      await loadExpectation()
      await loadOverview()
    } else {
      notFound.value = true
    }
  } catch (e) {
    error.value = errorMessage(e, '策略详情加载失败，请检查网络后重试')
  } finally {
    loading.value = false
    // 骨架屏分支在 loading=true 时占着 DOM：图表容器此刻才挂载，
    // 数据加载期间的渲染调用全部扑空（净值曲线空白的老 bug）——
    // 这里等一拍再统一画，两处图表共用同一时机。
    await nextTick()
    await renderEquityChart()
    await renderOpsChart()
  }
}

const handleBacktest = async () => {
  backtestLoading.value = true
  error.value = ''
  backtestData.value = null
  try {
    const res = await runBacktest(strategy.value.id)
    backtestData.value = res.data
  } catch (e) {
    error.value = errorMessage(e, '回测失败，请稍后重试')
  } finally {
    backtestLoading.value = false
  }
  await renderBacktestChart()
}

const loadDiagnostic = async () => {
  if (!strategy.value) return
  diagnosticLoading.value = true
  diagnosticError.value = ''
  diagnosticData.value = null
  try {
    const res = await getStrategyDiagnostic(strategy.value.id)
    diagnosticData.value = res.data
  } catch (e) {
    diagnosticError.value = errorMessage(e, '模型诊断加载失败，请稍后重试')
  } finally {
    diagnosticLoading.value = false
  }
}

const togglePaper = async () => {
  paperLoading.value = true
  error.value = ''
  try {
    if (strategy.value.paperEnabled) {
      await stopPaper(strategy.value.id)
    } else {
      await startPaper(strategy.value.id)
    }
    await loadDetail()
  } catch (e) {
    error.value = errorMessage(e, '模拟盘操作失败，请稍后重试')
  } finally {
    paperLoading.value = false
  }
}

const goBack = () => router.push('/strategies')

// ---- 只读分享（token 即凭证）----
const shareToken = ref('')
const shareBusy = ref(false)
const copied = ref(false)
const shareLink = computed(() => (shareToken.value ? `${location.origin}/share/${shareToken.value}` : ''))

const toggleShare = async () => {
  if (!strategy.value) return
  shareBusy.value = true
  copied.value = false
  try {
    const res = await enableShare(strategy.value.id)
    shareToken.value = res.data?.token || ''
  } catch (e) {
    error.value = errorMessage(e, '生成分享链接失败，请稍后重试')
  } finally {
    shareBusy.value = false
  }
}

const stopShare = async () => {
  if (!strategy.value) return
  shareBusy.value = true
  try {
    await disableShare(strategy.value.id)
    shareToken.value = ''
  } catch (e) {
    error.value = errorMessage(e, '停止分享失败')
  } finally {
    shareBusy.value = false
  }
}

const copyShare = async () => {
  try {
    await navigator.clipboard.writeText(shareLink.value)
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  } catch {
    // 剪贴板被浏览器策略拦了：链接明文展示着，手动也能选
  }
}

// ---- 策略修订闭环（P2，2026-10-15）：健康信号 → 提案 → 人审批 ----
// 展示分层（2026-10-19）：主视图只放通俗结论与简化对比（普通人能据此决策）；
// 专业口径（决策段/留出段/半衰期/权重占比/系统原话）收进折叠区。
// 未过门禁的提案也展示（否决理由透明），但不给批准按钮。
const revisionHealth = ref([])
const revisions = ref([])
const revisionGenerating = ref(false)
const revisionError = ref('')
const revisionNotice = ref('')
const revisionActing = ref(false)

const pendingRevision = computed(() =>
  revisions.value.find((r) => r.status === 'pending') || null)
const historyRevisions = computed(() =>
  revisions.value.filter((r) => r.status !== 'pending').slice(0, 5))

const parseJsonField = (text) => {
  if (!text) return null
  try { return JSON.parse(text) } catch { return null }
}
const pendingProposal = computed(() => parseJsonField(pendingRevision.value?.proposalJson))
const pendingEvidence = computed(() => parseJsonField(pendingRevision.value?.evidenceJson))
const pendingHealth = computed(() => parseJsonField(pendingRevision.value?.healthJson))

/** 系统结论的大白话版：优先用后端门禁的 summary；旧提案（无 summary）给通用兜底 */
const gateSummaryText = computed(() => {
  if (pendingEvidence.value?.summary) return pendingEvidence.value.summary
  return pendingRevision.value?.gatePassed
    ? '系统模拟对比显示：新参数整体表现更好，可以批准应用。'
    : '系统模拟对比显示：新参数没有跑赢现在的配置，不建议采用这次修改。'
})

/** 百分比显示：null/undefined（算不出来）显示 —，其余保留两位小数 */
const fmtPct = (v) => (v === null || v === undefined || v === '' ? '—' : `${Number(v).toFixed(2)}%`)

const signalTone = (level) => (level === 'warn' ? 'warn' : level === 'ok' ? 'down' : 'neutral')

const revisionStatusText = { approved: '已批准', rejected: '已拒绝', superseded: '被顶替' }

const loadRevisions = async () => {
  if (!strategy.value) return
  try {
    const [revRes, healthRes] = await Promise.all([
      getRevisions(strategy.value.id),
      getRevisionHealth(strategy.value.id),
    ])
    revisions.value = Array.isArray(revRes.data) ? revRes.data : []
    revisionHealth.value = Array.isArray(healthRes.data) ? healthRes.data : []
  } catch {
    // 健康信号是软失败：拿不到就只留生成入口
    revisionHealth.value = []
  }
}

const generateProposal = async () => {
  if (!strategy.value) return
  revisionGenerating.value = true
  revisionError.value = ''
  revisionNotice.value = ''
  try {
    await generateRevision(strategy.value.id)
      revisionNotice.value = '提案已生成，系统已完成行情模拟验证，请审批。'
    await loadRevisions()
  } catch (e) {
    revisionError.value = errorMessage(e, '提案生成失败：模型或行情服务暂不可用，稍后再试')
  } finally {
    revisionGenerating.value = false
  }
}

/** 批准/拒绝后重载：批准会改 configJson（口径线右移），详情与修订列表都要刷新 */
const actOnRevision = async (action) => {
  const revision = pendingRevision.value
  if (!revision || !strategy.value) return
  revisionActing.value = true
  revisionError.value = ''
  revisionNotice.value = ''
  try {
    if (action === 'approve') {
      await approveRevision(strategy.value.id, revision.id)
      revisionNotice.value = '已批准并应用：策略已按新参数运行，净值图上会标出这次调整的时间。'
    } else {
      await rejectRevision(strategy.value.id, revision.id)
      revisionNotice.value = '已拒绝该提案。'
    }
    await Promise.all([loadDetail(), loadRevisions()])
  } catch (e) {
    revisionError.value = errorMessage(e, '操作失败，请稍后重试')
  } finally {
    revisionActing.value = false
  }
}

onMounted(async () => {
  quoteBus.connect()   // 行情 SSE（全局单例，已连接则空操作）；focus 在 loadDetail 拿到 symbol 后注册
  await loadDetail()
  loadRevisions()
})
onUnmounted(() => {
  quoteBus.setFocus('')   // 离开页面：撤销 3 秒快车道的 focus 注册
  if (backtestChart) {
    backtestChart.dispose()
    backtestChart = null
  }
  if (equityChart) {
    equityChart.dispose()
    equityChart = null
  }
  if (opsChart) {
    window.removeEventListener('resize', handleOpsResize)
    disposeKlinecharts(opsChart)
    opsChart = null
  }
})
</script>

<template>
  <div>
    <div class="mb-5 flex items-center justify-between gap-3">
      <h1 class="text-[20px] font-semibold text-ink">策略详情</h1>
      <Button size="sm" variant="ghost" @click="goBack">
        <ArrowLeft :size="14" :stroke-width="2" aria-hidden="true" /> 返回策略库
      </Button>
    </div>

    <!-- 状态互斥：加载中 → 出错 → 未找到 → 有数据 -->
    <div v-if="loading" class="space-y-4">
      <div v-for="i in 3" :key="i" class="space-y-3 rounded-xl border border-border bg-surface p-5">
        <Skeleton width="35%" height="18px" />
        <div class="flex gap-2.5">
          <Skeleton v-for="j in 4" :key="j" width="22%" height="48px" />
        </div>
        <Skeleton width="75%" height="13px" />
      </div>
    </div>

    <p v-else-if="error && !strategy" class="text-[13px] text-up" role="alert">{{ error }}</p>

    <Empty v-else-if="notFound" title="未找到该策略">
      <template #action>
        <Button size="sm" variant="primary" @click="goBack">← 返回策略库</Button>
      </template>
    </Empty>

    <template v-else-if="strategy">
      <p v-if="error" class="mb-3 text-[13px] text-up" role="alert">{{ error }}</p>

      <!-- 运行状态条：它在不在跑、谁在做决定、今天动没动、下次什么时候 -->
      <section v-if="overviewItem" class="num mb-4 rounded-xl border border-border bg-surface p-4">
        <div class="flex flex-wrap items-center gap-2">
          <Badge :tone="overviewItem.paperEnabled ? 'down' : 'neutral'">
            {{ overviewItem.paperEnabled ? '模拟盘运行中' : '模拟盘未启动' }}
          </Badge>
          <Badge :tone="overviewItem.decisionMode === 'agent' ? 'accent' : 'neutral'">
            {{ decisionModeLabel(overviewItem.decisionMode) }}
          </Badge>
          <span v-if="overviewItem.decisionModeSince" class="text-[12px] text-ink-3">自 {{ overviewItem.decisionModeSince }} 起生效</span>
        </div>
        <p v-if="overviewItem.lastSettlement" class="mt-2 text-[13px] leading-relaxed text-ink-2">
          <span class="mr-1.5 inline-block min-w-[5.5rem] text-[12px] text-ink-3">最近一次结算</span>
          {{ overviewItem.lastSettlement.sentence }}
          （{{ overviewItem.lastSettlement.settlementKind === 'daily' ? '日线结算' : '盘中评估' }}）
          <template v-if="overviewItem.lastSettlement.decisionMode === 'agent' && overviewItem.lastSettlement.agentLlmCalls">
            （委员会 {{ overviewItem.lastSettlement.agentLlmCalls }} 次调用 / {{ overviewItem.lastSettlement.agentTokens ?? 0 }} token）
          </template>
        </p>
        <p v-else class="mt-2 text-[13px] leading-relaxed text-ink-2">
          <span class="mr-1.5 inline-block min-w-[5.5rem] text-[12px] text-ink-3">最近一次结算</span>还没有结算记录
        </p>
        <p class="mt-1 text-[13px] leading-relaxed text-ink-2">
          <span class="mr-1.5 inline-block min-w-[5.5rem] text-[12px] text-ink-3">下次</span>{{ overviewItem.nextEvaluationNote }}
        </p>
      </section>

      <!-- 策略头卡 -->
      <section class="mb-4 rounded-xl border border-border bg-surface p-5">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="flex flex-wrap items-center gap-2">
            <h2 class="text-[18px] font-semibold text-ink">{{ strategy.name }}</h2>
            <span class="num text-[13px] text-ink-3">{{ strategy.symbol }}</span>
            <Badge :tone="strategy.paperEnabled ? 'down' : 'neutral'">
              {{ strategy.paperEnabled ? '模拟盘中' : '未启动' }}
            </Badge>
          </div>
          <div class="flex flex-wrap gap-2">
            <Button size="sm" @click="editOpen = true">调整策略</Button>
            <Button size="sm" :loading="backtestLoading" @click="handleBacktest">运行回测</Button>
            <Button size="sm" :loading="paperLoading" @click="togglePaper">
              {{ strategy.paperEnabled ? '停止模拟盘' : '启动模拟盘' }}
            </Button>
            <Button size="sm" variant="ghost" class="border border-accent text-accent hover:bg-accent-soft" :disabled="shareBusy" @click="toggleShare">
              {{ shareToken ? '重发分享链接' : '生成分享链接' }}
            </Button>
          </div>
        </div>

        <p v-if="shareLink" class="mt-2.5 break-words text-[12.5px] text-ink-2" role="status">
          分享链接（只读，任何人可看）：<code class="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[12px]">{{ shareLink }}</code>
          <button type="button" class="ml-2 rounded-md border border-border px-2 py-0.5 text-[12px] text-ink-2 transition-colors hover:border-accent hover:text-accent" @click="copyShare">{{ copied ? '已复制' : '复制' }}</button>
          <button type="button" class="ml-1.5 rounded-md border border-border px-2 py-0.5 text-[12px] text-ink-2 transition-colors hover:border-accent hover:text-accent" :disabled="shareBusy" @click="stopShare">停止分享</button>
        </p>

        <div class="num mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink-3">
          <span>创建: {{ formatTime(strategy.createdAt) }}</span>
          <span>更新: {{ formatTime(strategy.updatedAt) }}</span>
          <span v-if="strategy.lastBacktestAt">最近回测: {{ formatTime(strategy.lastBacktestAt) }}</span>
        </div>

        <!-- 缺止损条件：配置层的减亏缺口，第一屏说出来 -->
        <p v-if="!hasStopCondition" class="mt-3 flex items-start gap-2 rounded-lg border border-dashed border-warn bg-warn-soft p-3 text-[13px] leading-relaxed text-warn" role="note">
          <AlertTriangle :size="15" :stroke-width="1.75" class="mt-0.5 shrink-0" aria-hidden="true" />
          退出规则里没有任何止损条件（止损 stop_loss_pct / 移动止损 trailing_stop_pct）——
          大跌时这条策略没有硬性减亏线。在策略配置的 exit 里加一条止损条件即可接上盘中硬止损。
        </p>

        <h3 class="mb-2.5 mt-4 text-[15px] font-semibold text-ink">策略配置</h3>
        <pre class="num m-0 max-h-[400px] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-sidebar p-3.5 font-mono text-[12px] leading-relaxed text-sidebar-ink">{{ prettyConfig }}</pre>
      </section>

      <!-- 决策来源区块已于 2026-10-15 移除：决策者统一为多角色委员会（agent）。
           规则 DSL 仍是策略定义与确定性降级底座，不再作为可切换的独立模式。 -->

      <!-- 策略修订（P2）：健康信号 → 结构化提案（截至当天、近期加权的回测证据）→ 人审批 -->
      <section class="mb-4 rounded-xl border border-border bg-surface p-5">
        <h3 class="mb-2 flex items-center gap-1.5 text-[15px] font-semibold text-ink">
          策略修订
          <InfoTip
            label="策略修订说明"
            text="策略运行一段时间后，系统会根据客观运行数据（决策命中率、收益与回撤、近期成交）自动生成参数调整提案，并用截至今天的真实行情做模拟对比，验证「新参数是否比现在更好」。提案需要你人工批准才会生效；验证未通过的提案只能拒绝。"
          />
        </h3>

        <!-- 健康信号：确定性代码算的，不是模型说的 -->
        <div v-if="revisionHealth.length" class="mb-3 flex flex-col gap-1.5">
          <p
            v-for="s in revisionHealth"
            :key="s.key"
            class="flex items-start gap-2 text-[13px] leading-relaxed"
            :class="s.level === 'warn' ? 'text-warn' : 'text-ink-2'"
          >
            <Badge :tone="signalTone(s.level)">{{ s.level === 'warn' ? '预警' : s.level === 'ok' ? '正常' : '说明' }}</Badge>
            <span>{{ s.detail }}</span>
          </p>
        </div>

        <p v-if="revisionError" class="text-[12.5px] text-up" role="alert">{{ revisionError }}</p>
        <p v-if="revisionNotice" class="mb-2.5 text-[13px] text-down">{{ revisionNotice }}</p>

        <!-- 待审批提案：主视图只放"普通人能决策"的信息（结论 + 通俗对比），
             专业口径（决策段/留出段/半衰期/权重占比）收进折叠区，想深究再展开 -->
        <div v-if="pendingRevision && pendingProposal" class="rounded-lg border border-border bg-surface-2 p-3.5">
          <div class="flex flex-wrap items-center gap-2">
            <Badge :tone="pendingRevision.gatePassed ? 'down' : 'warn'">
              {{ pendingRevision.gatePassed ? '系统验证通过' : '系统验证未通过' }}
            </Badge>
            <span v-if="pendingEvidence?.window?.end" class="text-[12px] text-ink-3">
              已用截至 {{ pendingEvidence.window.end }} 的真实行情完成模拟对比
            </span>
          </div>

          <!-- 系统结论（大白话版，来自后端门禁；旧数据无 summary 时给通用兜底） -->
          <p
            class="mt-2.5 rounded-lg border p-2.5 text-[13.5px] leading-relaxed"
            :class="pendingRevision.gatePassed
              ? 'border-down/30 bg-down/5 text-ink'
              : 'border-warn/30 bg-warn-soft text-ink'"
            role="status"
          >
            {{ gateSummaryText }}
          </p>

          <p class="mt-2.5 text-[13px] leading-relaxed text-ink">
            <span class="mr-1.5 text-[12px] text-ink-3">修改理由</span>{{ pendingProposal.rationale }}
          </p>
          <p class="mt-1 text-[12.5px] leading-relaxed text-ink-2">
            <span class="mr-1.5 text-[12px] text-ink-3">预期与代价</span>{{ pendingProposal.expected_effect }}
          </p>

          <!-- 参数 diff：提案的核心，一行一处改动 -->
          <div v-if="pendingProposal.diff?.length" class="mt-3 overflow-x-auto">
            <table class="num w-full min-w-[480px] border-collapse text-[12px]">
              <thead>
                <tr class="border-b border-border text-left text-ink-3">
                  <th class="py-1.5 pr-3 font-medium">参数</th>
                  <th class="py-1.5 pr-3 font-medium">现值</th>
                  <th class="py-1.5 font-medium">提案值</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(d, i) in pendingProposal.diff" :key="i" class="border-b border-border/60">
                  <td class="py-1.5 pr-3 font-mono text-[11.5px] text-ink-2">{{ d.path }}</td>
                  <td class="py-1.5 pr-3 text-ink-3">{{ JSON.stringify(d.old) }}</td>
                  <td class="py-1.5 font-medium text-ink">{{ JSON.stringify(d.new) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- 通俗版回测对比：只回答"新参数和现版谁表现好"，术语不进主视图 -->
          <div v-if="pendingEvidence?.old && pendingEvidence?.new" class="mt-3 overflow-x-auto">
            <table class="num w-full min-w-[440px] border-collapse text-[12.5px]">
              <thead>
                <tr class="border-b border-border text-left text-ink-3">
                  <th class="py-1.5 pr-3 font-medium">模拟对比（真实行情回放：{{ pendingEvidence.window?.start }} ~ {{ pendingEvidence.window?.end }}）</th>
                  <th class="py-1.5 pr-3 font-medium">现行配置</th>
                  <th class="py-1.5 font-medium">提案配置</th>
                </tr>
              </thead>
              <tbody>
                <tr class="border-b border-border/60">
                  <td class="py-1.5 pr-3 text-ink-2">近期行情下的年化收益（越近的行情越重要）</td>
                  <td class="py-1.5 pr-3 text-ink-3">{{ fmtPct(pendingEvidence.old.decision_weighted_daily_return_pct) }}</td>
                  <td class="py-1.5 font-medium" :class="dirTextClass(pendingEvidence.new.decision_weighted_daily_return_pct - pendingEvidence.old.decision_weighted_daily_return_pct)">
                    {{ fmtPct(pendingEvidence.new.decision_weighted_daily_return_pct) }}
                  </td>
                </tr>
                <tr class="border-b border-border/60">
                  <td class="py-1.5 pr-3 text-ink-2">最近 {{ pendingEvidence.window?.holdout_bars || 40 }} 个交易日的收益（这段新行情未参与评估）</td>
                  <td class="py-1.5 pr-3 text-ink-3">{{ fmtPct(pendingEvidence.old.holdout_return_pct) }}</td>
                  <td class="py-1.5 font-medium">{{ fmtPct(pendingEvidence.new.holdout_return_pct) }}</td>
                </tr>
                <tr class="border-b border-border/60">
                  <td class="py-1.5 pr-3 text-ink-2">整个模拟期总收益 / 期间最深回落</td>
                  <td class="py-1.5 pr-3 text-ink-3">{{ fmtPct(pendingEvidence.old.total_return_pct) }} / {{ fmtPct(pendingEvidence.old.max_drawdown_pct) }}</td>
                  <td class="py-1.5 font-medium">{{ fmtPct(pendingEvidence.new.total_return_pct) }} / {{ fmtPct(pendingEvidence.new.max_drawdown_pct) }}</td>
                </tr>
                <tr class="border-b border-border/60">
                  <td class="py-1.5 pr-3 text-ink-2">完成交易次数（越多，交易成本越高）</td>
                  <td class="py-1.5 pr-3 text-ink-3">{{ pendingEvidence.old.closed_trades }}</td>
                  <td class="py-1.5 font-medium">{{ pendingEvidence.new.closed_trades }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="mt-3 flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="primary"
              :loading="revisionActing"
              :disabled="!pendingRevision.gatePassed"
              @click="actOnRevision('approve')"
            >批准并应用</Button>
            <Button size="sm" :disabled="revisionActing" @click="actOnRevision('reject')">拒绝</Button>
            <span v-if="!pendingRevision.gatePassed" class="self-center text-[12px] text-ink-3">
              验证未通过的提案不能批准（模拟对比里没跑赢现版），可拒绝后重新生成一份
            </span>
          </div>

          <!-- 专业口径：完整验证数据与系统原话，默认折叠 -->
          <details v-if="pendingEvidence?.old && pendingEvidence?.new" class="mt-3 rounded-lg border border-border bg-surface">
            <summary class="cursor-pointer select-none px-3 py-2 text-[12.5px] text-ink-3">展开专业口径的验证数据</summary>
            <div class="border-t border-border px-3 py-2.5">
              <p class="text-[12px] leading-relaxed text-ink-3">{{ pendingEvidence.window?.note }}</p>
              <p v-if="pendingEvidence.complexity?.note" class="mt-1 text-[12px] leading-relaxed text-ink-3">{{ pendingEvidence.complexity.note }}</p>
              <div class="mt-2 overflow-x-auto">
                <table class="num w-full min-w-[420px] border-collapse text-[12px]">
                  <thead>
                    <tr class="border-b border-border text-left text-ink-3">
                      <th class="py-1.5 pr-3 font-medium">指标</th>
                      <th class="py-1.5 pr-3 font-medium">现行配置</th>
                      <th class="py-1.5 font-medium">提案配置</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr class="border-b border-border/60">
                      <td class="py-1.5 pr-3 text-ink-2">决策段加权年化收益（半衰期 {{ pendingEvidence.new.half_life_days }} 日，截至 {{ (pendingEvidence.window?.holdout_start || '').slice(5) }}）</td>
                      <td class="py-1.5 pr-3 text-ink-3">{{ fmtPct(pendingEvidence.old.decision_weighted_daily_return_pct) }}</td>
                      <td class="py-1.5 font-medium">{{ fmtPct(pendingEvidence.new.decision_weighted_daily_return_pct) }}</td>
                    </tr>
                    <tr class="border-b border-border/60">
                      <td class="py-1.5 pr-3 text-ink-2">留出段收益（最近 {{ pendingEvidence.window?.holdout_bars }} 根，不参与挑选、只做反证）</td>
                      <td class="py-1.5 pr-3 text-ink-3">{{ fmtPct(pendingEvidence.old.holdout_return_pct) }}</td>
                      <td class="py-1.5 font-medium">{{ fmtPct(pendingEvidence.new.holdout_return_pct) }}</td>
                    </tr>
                    <tr class="border-b border-border/60">
                      <td class="py-1.5 pr-3 text-ink-2">近期加权最大回撤（近 {{ pendingEvidence.new.recent_window_bars }} 根，占权重 {{ pendingEvidence.new.recent_window_weight_share_pct }}%）</td>
                      <td class="py-1.5 pr-3 text-ink-3">{{ fmtPct(pendingEvidence.old.weighted_max_drawdown_pct) }}</td>
                      <td class="py-1.5 font-medium">{{ fmtPct(pendingEvidence.new.weighted_max_drawdown_pct) }}</td>
                    </tr>
                    <tr class="border-b border-border/60">
                      <td class="py-1.5 pr-3 text-ink-2">全期收益 / 全期最大回撤</td>
                      <td class="py-1.5 pr-3 text-ink-3">{{ fmtPct(pendingEvidence.old.total_return_pct) }} / {{ fmtPct(pendingEvidence.old.max_drawdown_pct) }}</td>
                      <td class="py-1.5 font-medium">{{ fmtPct(pendingEvidence.new.total_return_pct) }} / {{ fmtPct(pendingEvidence.new.max_drawdown_pct) }}</td>
                    </tr>
                    <tr class="border-b border-border/60">
                      <td class="py-1.5 pr-3 text-ink-2">完成交易次数</td>
                      <td class="py-1.5 pr-3 text-ink-3">{{ pendingEvidence.old.closed_trades }}</td>
                      <td class="py-1.5 font-medium">{{ pendingEvidence.new.closed_trades }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p class="mt-2 text-[12px] leading-relaxed text-ink-3">系统结论（原话）：{{ pendingEvidence.reason }}</p>
            </div>
          </details>
        </div>

        <!-- 生成入口 + 历史 -->
        <div class="mt-3 flex flex-wrap items-center gap-2.5 border-t border-border pt-3">
          <Button size="sm" variant="primary" :loading="revisionGenerating" :disabled="!!pendingRevision" @click="generateProposal">
            {{ pendingRevision ? '已有待审批提案' : '生成修订提案' }}
          </Button>
          <span class="text-[12px] text-ink-3">由 AI 生成参数提案并自动用真实行情验证，约需 30~90 秒</span>
        </div>

        <div v-if="historyRevisions.length" class="mt-2.5 flex flex-col gap-1">
          <p v-for="r in historyRevisions" :key="r.id" class="num flex flex-wrap items-center gap-2 text-[12px] text-ink-3">
            <Badge :tone="r.status === 'approved' ? 'down' : 'neutral'">{{ revisionStatusText[r.status] || r.status }}</Badge>
            <span>{{ formatTime(r.createdAt) }}</span>
            <span v-if="r.gatePassed === false">系统验证未通过</span>
            <span v-if="r.note" class="text-ink-2">{{ r.note }}</span>
          </p>
        </div>
      </section>

      <!-- 可验证预期 -->
      <section class="mb-4 rounded-xl border border-border bg-surface p-5">
        <h3 class="mb-2 flex items-center gap-1.5 text-[15px] font-semibold text-ink">
          可验证预期
          <InfoTip
            label="可验证预期说明"
            text="写清度量、门槛与到期日，到期后由每日结算用同一份净值自动判定：达成、未达成或算不出来，三者分开。都是账户层面可观测值，不含股价预测。同一时刻只有一条有效预期，登记新的即取代旧的。"
          />
        </h3>
        <p v-if="expectationError" class="text-[12.5px] text-up" role="alert">{{ expectationError }}</p>
        <p v-if="expectationNotice" class="mb-2.5 text-[13px] text-down">{{ expectationNotice }}</p>

        <template v-if="expectation">
          <div class="flex flex-wrap items-center gap-2.5">
            <Badge :tone="expectationTone(expectation.status)">{{ expectationStatusLabel(expectation.status) }}</Badge>
            <span class="num text-[12.5px] text-ink-2">
              度量：{{ expectation.metricLabel || expectation.metric }}
              ｜ 门槛：{{ expectation.threshold }}
              ｜ 到期：{{ expectation.deadline }}
            </span>
          </div>
          <p class="mt-2 text-[13px] leading-relaxed text-ink">{{ expectation.sentence }}</p>
          <div class="num mt-1.5 flex flex-wrap gap-x-4 text-[12px] text-ink-3">
            <span v-if="expectation.registeredAt">登记于 {{ expectation.registeredAt }}</span>
            <span v-if="expectation.outcome !== null && expectation.outcome !== undefined">实际值 {{ expectation.outcome }}</span>
            <span v-if="expectation.evaluatedAt">回填于 {{ formatTime(expectation.evaluatedAt) }}</span>
          </div>
        </template>
        <div v-else-if="!expectationError" class="rounded-lg border border-dashed border-border bg-surface-2 p-3 text-[13px] leading-relaxed text-ink-2">
          还没有登记预期 —— 目前没有人对这条策略的未来下过承诺。
        </div>

        <div class="mt-3 flex flex-wrap items-end gap-3 border-t border-border pt-3">
          <label class="flex flex-col gap-1 text-[12px] text-ink-2">
            度量
            <select
              v-model="expectationForm.metric"
              class="h-[34px] min-w-[220px] rounded-lg border border-border bg-bg px-2.5 text-[13px] text-ink focus:border-accent focus:outline-none"
            >
              <option v-for="option in METRIC_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
          </label>
          <label class="flex flex-col gap-1 text-[12px] text-ink-2">
            门槛（实际 ≥ 门槛记为达成）
            <input
              v-model="expectationForm.threshold"
              type="number"
              step="0.1"
              class="num h-[34px] w-[120px] rounded-lg border border-border bg-bg px-2.5 text-[13px] text-ink focus:border-accent focus:outline-none"
            >
          </label>
          <label class="flex flex-col gap-1 text-[12px] text-ink-2">
            跨度（自然日）
            <input
              v-model="expectationForm.horizonDays"
              type="number"
              min="1"
              step="1"
              class="num h-[34px] w-[110px] rounded-lg border border-border bg-bg px-2.5 text-[13px] text-ink focus:border-accent focus:outline-none"
            >
          </label>
          <Button variant="primary" :loading="expectationLoading" @click="submitExpectation">登记预期</Button>
        </div>
      </section>

      <!-- 回测结果 -->
      <section v-if="backtestLoading || backtestData !== null" class="mb-4 rounded-xl border border-border bg-surface p-5">
        <h3 class="mb-3 text-[15px] font-semibold text-ink">回测结果</h3>
        <div v-if="backtestLoading" class="text-[13px] text-ink-2">回测运行中...</div>
        <div v-else-if="backtestResult && backtestResult.error" class="text-[13px] text-ink-2">{{ backtestResult.error }}</div>
        <template v-else-if="backtestResult">
          <div class="num mb-4 grid gap-2.5 [grid-template-columns:repeat(auto-fit,minmax(130px,1fr))]">
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">策略收益</span>
              <strong class="text-[16px] font-semibold" :class="dirTextClass(backtestResult.total_return_pct)">{{ formatPct(backtestResult.total_return_pct) }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">买入持有</span>
              <strong class="text-[16px] font-semibold" :class="dirTextClass(backtestResult.buy_and_hold_return_pct)">{{ formatPct(backtestResult.buy_and_hold_return_pct) }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">超额收益</span>
              <strong class="text-[16px] font-semibold" :class="dirTextClass(backtestResult.excess_return_pct)">{{ formatPct(backtestResult.excess_return_pct) }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">最大回撤</span>
              <strong class="text-[16px] font-semibold text-down">{{ formatPct(backtestResult.max_drawdown_pct) }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">胜率</span>
              <strong class="text-[16px] font-semibold text-ink">{{ formatPct(backtestResult.win_rate) }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">夏普比率</span>
              <strong class="text-[16px] font-semibold text-ink">{{ formatNumber(backtestResult.sharpe_ratio, 3) }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">年化收益</span>
              <strong class="text-[16px] font-semibold" :class="dirTextClass(backtestResult.annualized_return_pct)">{{ formatPct(backtestResult.annualized_return_pct) }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">已平仓交易</span>
              <strong class="text-[16px] font-semibold text-ink">{{ backtestResult.closed_trades ?? 0 }}</strong>
            </div>
          </div>

          <div ref="backtestChartRef" class="my-4 h-[320px]"></div>

          <div v-if="backtestResult.trade_log && backtestResult.trade_log.length">
            <h4 class="mb-2.5 text-[14px] font-semibold text-ink">回测交易明细</h4>
            <div v-for="(trade, index) in backtestResult.trade_log" :key="index" class="num flex flex-wrap items-center gap-x-2.5 gap-y-1 border-b border-border py-2 text-[12px] text-ink-2 last:border-b-0">
              <span class="text-ink">{{ trade.date }}</span>
              <Badge :tone="String(trade.side).toLowerCase() === 'buy' ? 'up' : 'down'">{{ sideLabel(trade.side) }}</Badge>
              <span>价格: {{ formatNumber(trade.price, 3) }}</span>
              <span>数量: {{ formatNumber(trade.shares, 2) }}</span>
              <span>金额: {{ formatMoney(trade.amount) }}</span>
              <span v-if="trade.reason" class="w-full text-ink-3">触发: {{ trade.reason }}</span>
            </div>
          </div>
        </template>
      </section>

      <!-- 模型诊断 -->
      <section class="mb-4 rounded-xl border border-border bg-surface p-5">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <h3 class="flex items-center gap-1.5 text-[15px] font-semibold text-ink">
            模型诊断
            <InfoTip label="模型诊断说明" :text="diagnostic?.disclaimer || '模型诊断仅作低权重参考，不参与策略买卖决策，不构成投资建议。'" />
          </h3>
          <Button size="sm" :loading="diagnosticLoading" @click="loadDiagnostic">
            {{ diagnosticData ? '重新加载' : '加载模型诊断' }}
          </Button>
        </div>

        <p v-if="diagnosticError" class="mt-2 text-[12.5px] text-up" role="alert">{{ diagnosticError }}</p>

        <template v-if="diagnostic">
          <div class="mt-3 grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(220px,1fr))]">
            <div class="rounded-lg border border-border bg-surface-2 p-3">
              <span class="mb-2 block text-[12px] text-ink-2">风险指标</span>
              <div v-if="diagnostic.risk && !diagnostic.risk.error" class="num flex flex-col gap-1.5 text-[13px] text-ink-2">
                <span>波动率: {{ formatNumber(diagnostic.risk.annual_volatility_pct, 2) }}%</span>
                <span>最大回撤: {{ formatPct(diagnostic.risk.max_drawdown_pct) }}</span>
                <span>当前回撤: {{ formatPct(diagnostic.risk.current_drawdown_pct) }}</span>
                <span>距 MA20: {{ formatPct(diagnostic.risk.distance_from_ma20_pct) }}</span>
                <span>风险等级: <b class="font-semibold text-ink">{{ diagnostic.risk.risk_level || 'N/A' }}</b></span>
              </div>
              <p v-else class="text-[12.5px] text-ink-3">风险数据不可用</p>
            </div>
            <div class="rounded-lg border border-border bg-surface-2 p-3">
              <span class="mb-2 block text-[12px] text-ink-2">模型状态</span>
              <div v-if="diagnostic.model_status" class="num flex flex-col gap-1.5 text-[13px] text-ink-2">
                <span>可用: {{ diagnostic.model_status.available ? '是' : '否' }}</span>
                <span>缓存命中: {{ diagnostic.model_status.cache_hit ? '是' : '否' }}</span>
                <span>磁盘模型: {{ diagnostic.model_status.disk_model_available ? '有' : '无' }}</span>
                <span>参与决策: {{ diagnostic.model_status.decision_use ? '是' : '否' }}</span>
              </div>
              <p v-else class="text-[12.5px] text-ink-3">模型状态不可用</p>
            </div>
            <div class="rounded-lg border border-border bg-surface-2 p-3">
              <span class="mb-2 block text-[12px] text-ink-2">模型共识</span>
              <div v-if="diagnostic.model_consensus" class="num flex flex-col gap-1.5 text-[13px] text-ink-2">
                <span>共识: <b class="font-semibold text-ink">{{ diagnostic.model_consensus.consensus || 'neutral' }}</b></span>
                <span>置信度: <b class="font-semibold text-ink">{{ diagnostic.model_consensus.confidence || 'N/A' }}</b></span>
                <span>参与决策: {{ diagnostic.model_consensus.decision_use ? '是' : '否' }}</span>
              </div>
              <p v-else class="text-[12.5px] text-ink-3">模型共识不可用</p>
            </div>
          </div>
        </template>
      </section>

      <!-- 模拟盘账户 -->
      <section class="mb-4 rounded-xl border border-border bg-surface p-5">
        <h3 class="mb-3 text-[15px] font-semibold text-ink">模拟盘账户</h3>
        <template v-if="account">
          <div v-if="!accountEvaluated" class="mb-3 rounded-lg border border-dashed border-border bg-surface-2 p-3 text-[13px] leading-relaxed text-ink-2">
            <strong class="text-ink">这个账户还没有被评估过一次。</strong>除初始资金与现金外，其余字段都还没有值。
            <template v-if="isAgentMode">
              委员会只在交易日 15:30 结算时决策；启动或切换的那一刻会先开一次首会，
              当天没有 K 线（休市/数据未出）时才会停在这里。
            </template>
            <template v-else>下一次盘中检查在几分钟内，日线结算在下一个交易日 15:30。</template>
          </div>
          <div class="num grid gap-2.5 [grid-template-columns:repeat(auto-fit,minmax(140px,1fr))]">
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">初始资金</span>
              <strong class="text-[15px] font-semibold text-ink">{{ formatMoney(account.initialCapital) }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">现金</span>
              <strong class="text-[15px] font-semibold text-ink">{{ formatMoney(account.cash) }}</strong>
            </div>
            <template v-if="accountEvaluated">
              <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
                <span class="text-[12px] text-ink-2">总权益</span>
                <strong class="text-[15px] font-semibold text-ink">{{ formatMoney(account.equity) }}</strong>
              </div>
              <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
                <span class="text-[12px] text-ink-2">持仓市值</span>
                <strong class="text-[15px] font-semibold text-ink">{{ formatMoney(positionValue) }}</strong>
              </div>
              <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
                <span class="text-[12px] text-ink-2">持仓数量</span>
                <strong class="text-[15px] font-semibold text-ink">{{ account.shares }}</strong>
              </div>
              <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
                <span class="text-[12px] text-ink-2">平均成本</span>
                <strong class="text-[15px] font-semibold text-ink">{{ formatMoney(account.avgCost) }}</strong>
              </div>
              <!-- 澄清语义：持仓期最高"股价"（trailing_stop 基准），不是净值高点 -->
              <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
                <span class="text-[12px] text-ink-2">持仓期最高价（移动止损基准）</span>
                <strong class="text-[15px] font-semibold text-ink">{{ formatMoney(account.highWatermark) }}</strong>
              </div>
              <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
                <span class="text-[12px] text-ink-2">最新价格</span>
                <strong class="text-[15px] font-semibold text-ink">{{ formatMoney(account.lastPrice) }}</strong>
              </div>
              <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
                <span class="text-[12px] text-ink-2">最新信号</span>
                <strong class="text-[15px] font-semibold text-ink">
                  {{ account.lastSignal === 'buy' ? '买入' : account.lastSignal === 'sell' ? '卖出' : account.lastSignal === 'hold' ? '持有' : (account.lastSignal || 'N/A') }}
                </strong>
              </div>
              <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
                <span class="text-[12px] text-ink-2">最近评估</span>
                <strong class="text-[15px] font-semibold text-ink">{{ formatTime(account.lastEvalAt) }}</strong>
              </div>
            </template>
          </div>
        </template>
        <Empty v-else title="暂无模拟盘账户" />
      </section>

      <!-- 模拟盘交易记录 -->
      <section class="mb-4 rounded-xl border border-border bg-surface p-5">
        <h3 class="mb-2.5 text-[15px] font-semibold text-ink">模拟盘交易记录</h3>
        <Empty v-if="trades.length === 0" title="暂无交易记录" />
        <div v-else class="num">
          <div v-for="(trade, index) in trades" :key="index" class="flex flex-wrap items-center gap-x-2.5 gap-y-1 border-b border-border py-2.5 text-[12.5px] text-ink-2 last:border-b-0">
            <span class="text-ink">{{ formatTime(trade.createdAt || trade.tradeDate) }}</span>
            <Badge :tone="String(trade.side).toLowerCase() === 'buy' ? 'up' : 'down'">{{ sideLabel(trade.side) }}</Badge>
            <span class="font-mono">{{ trade.symbol }}</span>
            <span>价格: {{ trade.price }}</span>
            <span>数量: {{ trade.shares }}</span>
            <span>金额: {{ formatMoney(trade.amount) }}</span>
            <span v-if="trade.reason" class="w-full text-ink-3">{{ trade.reason }}</span>
            <span v-if="trade.traceId" class="w-full text-ink-3">痕迹 #{{ trade.traceId }}</span>
            <span v-else class="w-full text-ink-3">无痕迹（痕迹功能上线前的成交，或痕迹写入失败）</span>
          </div>
        </div>
      </section>

      <!-- 操作 K 线：成交钉在行情上 -->
      <section class="mb-4 rounded-xl border border-border bg-surface p-5">
        <h3 class="mb-2.5 flex flex-wrap items-center gap-1.5 text-[15px] font-semibold text-ink">
          操作 K 线 · 在行情里看每一步
          <InfoTip
            label="操作 K 线说明"
            text="蜡烛是该标的的日线，▲ 是买入、▼ 是卖出：标记在哪根蜡烛上就是哪天成交的，旁边的小字是该笔的成交时刻，与下方交易记录的时间一一对应（悬停还能看成交价、数量与金额）。同一天一买一卖会分居蜡烛上下两侧。标记反复挤在同一片区域，说明策略在均线附近来回买卖——相邻的一对 ▼▲ 就是一次卖低买高的往返。滚轮/拖动可缩放平移。"
          />
          <span class="ml-auto flex gap-1.5">
            <button
              v-for="r in OPS_RANGES" :key="r.value" type="button"
              class="rounded-md border px-2 py-0.5 text-[12px] transition-colors"
              :class="opsBars === r.value ? 'border-accent bg-accent-soft text-accent' : 'border-border text-ink-2 hover:border-accent hover:text-accent'"
              @click="changeOpsRange(r.value)"
            >{{ r.label }}</button>
          </span>
        </h3>
        <p v-if="opsError" class="text-[12.5px] text-up" role="alert">{{ opsError }}</p>
        <Empty
          v-else-if="trades.length === 0"
          title="还没有成交可标注"
          desc="模拟盘成交后，每一笔会作为 ▲▼ 标记出现在这里对应的 K 线上。"
        />
        <template v-else>
          <p v-if="opsHiddenTrades > 0" class="mb-2 text-[12px] text-ink-3">
            有 {{ opsHiddenTrades }} 笔成交落在已加载行情之外（更早或当日 K 线未出），暂无法标注。
          </p>
          <!-- 成交明细回到图上（2026-10-19）：悬停/点按 ▲▼ 出浮动卡，贴着标记、
               pointer-events-none 不挡画布交互，靠边自动翻转不溢出容器 -->
          <div class="relative">
            <div ref="opsChartRef" class="h-[420px]"></div>
            <div
              v-if="opsHover"
              class="num pointer-events-none absolute z-20 w-max max-w-[280px] rounded-lg border border-border bg-surface px-3 py-2 text-[12px] leading-relaxed text-ink shadow-lg"
              :style="opsHoverStyle"
              role="status"
            >
              <p class="mb-0.5 font-semibold" :class="opsHover.trade.side === 'buy' ? 'text-up' : 'text-down'">
                {{ opsHover.trade.side === 'buy' ? '买入' : '卖出' }} · {{ String(opsHover.trade.tradeDate || '').slice(0, 10) }}
              </p>
              <p>{{ opsHover.trade.price ?? 'N/A' }} × {{ opsHover.trade.shares ?? 'N/A' }} 股　金额 {{ formatMoney(opsHover.trade.amount) }}</p>
              <p v-if="opsHover.trade.reason" class="text-ink-2">信号 {{ opsHover.trade.reason }}</p>
              <p v-if="opsHover.trade.createdAt" class="text-ink-3">{{ formatTime(opsHover.trade.createdAt) }}</p>
            </div>
          </div>
        </template>
      </section>

      <!-- 决策日志 -->
      <section class="mb-4 rounded-xl border border-border bg-surface p-5">
        <h3 class="mb-2.5 flex items-center gap-1.5 text-[15px] font-semibold text-ink">
          决策日志 · 说了什么 vs 后来怎样
          <InfoTip
            label="决策日志说明"
            text="每天的判定用次日收盘方向对答案：买对了涨、卖对了跌算命中。量的是方向对错，不是盈亏。最近一行要等下一根收盘才能判定；缺数据的日子标无法判定，不算错。"
          />
        </h3>
        <p v-if="journalError" class="text-[12.5px] text-up" role="alert">{{ journalError }}</p>
        <Empty
          v-else-if="!journal || !journal.entries || journal.entries.length === 0"
          title="还没有可对照的决策记录"
          desc="模拟盘跑起来之后，这里每天会多一行。"
        />
        <template v-else>
          <div v-if="journal.evaluated > 0 || journal.pending > 0" class="num mb-3 grid gap-2.5 [grid-template-columns:repeat(auto-fit,minmax(130px,1fr))]">
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">方向命中率</span>
              <strong class="text-[16px] font-semibold text-ink">{{ journal.hitRate != null ? journal.hitRate + '%' : '暂无样本' }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">命中 / 判错</span>
              <strong class="text-[16px] font-semibold text-ink">{{ journal.hits }} / {{ journal.misses }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">待判定</span>
              <strong class="text-[16px] font-semibold text-ink">{{ journal.pending }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">选择不动</span>
              <strong class="text-[16px] font-semibold text-ink">{{ journal.skips }} 天</strong>
            </div>
          </div>

          <!-- 模式命中率对比：委员会与规则各有判读样本时才显示（切换模式前的历史也计入） -->
          <div v-if="modeStats.length === 2" class="num mt-1 flex flex-wrap gap-3">
            <div
              v-for="s in modeStats"
              :key="s.mode"
              class="flex min-w-[200px] flex-1 items-center gap-2.5 rounded-lg border border-border bg-surface-2 px-3 py-2"
            >
              <span class="shrink-0 text-[12px] text-ink-2">{{ s.mode === 'agent' ? '委员会' : '规则' }}</span>
              <div class="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-surface">
                <div
                  class="h-full rounded-full bg-accent"
                  :style="{ width: (s.rate || 0) + '%' }"
                  role="progressbar"
                  :aria-valuenow="s.rate || 0" aria-valuemin="0" aria-valuemax="100"
                  :aria-label="`${s.mode === 'agent' ? '委员会' : '规则'}命中率 ${s.rate ?? '—'}%`"
                ></div>
              </div>
              <span class="shrink-0 text-[12.5px] font-semibold text-ink">{{ s.rate != null ? s.rate + '%' : '—' }}</span>
              <span class="shrink-0 text-[11px] text-ink-3">{{ s.hits }}/{{ s.total }}</span>
            </div>
          </div>

          <!-- 不动原因分布：回答"为什么总不动"——分布条 + 前三原因 -->
          <div v-if="skipReasons.total >= 3" class="mt-1">
            <div class="flex h-2 w-full overflow-hidden rounded-full" role="img" :aria-label="`不动的 ${skipReasons.total} 天里：${skipReasons.items.map((i) => (SKIP_REASON_LABELS[i.reason] || i.reason) + ' ' + i.n + ' 天').join('，')}`">
              <div
                v-for="(item, i) in skipReasons.items"
                :key="item.reason"
                :style="{ width: (item.n / skipReasons.total * 100) + '%', background: SKIP_COLORS[i % SKIP_COLORS.length] }"
              ></div>
            </div>
            <p class="num mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[11.5px] text-ink-3">
              <span v-for="(item, i) in skipReasons.items.slice(0, 4)" :key="item.reason">
                <span class="mr-1 inline-block h-1.5 w-1.5 rounded-full align-middle" :style="{ background: SKIP_COLORS[i % SKIP_COLORS.length] }" aria-hidden="true"></span>
                {{ SKIP_REASON_LABELS[item.reason] || item.reason }} {{ item.n }} 天
              </span>
            </p>
          </div>
          <div class="num flex flex-col gap-1.5">
            <div v-for="entry in journal.entries" :key="entry.tradeDate" class="flex flex-wrap items-center gap-2.5 rounded-lg border border-border px-2.5 py-2 text-[12.5px]">
              <span class="min-w-[5.5rem] text-ink-2">{{ entry.tradeDate }}</span>
              <span class="font-semibold" :class="entry.decision === 'buy' ? 'text-up' : entry.decision === 'sell' ? 'text-down' : 'text-ink-3'">{{ decisionLabel(entry.decision) }}</span>
              <span v-if="entry.decisionMode" class="rounded-full border border-border px-1.5 text-[11px] text-ink-3">{{ entry.decisionMode === 'agent' ? '委员会' : '规则' }}</span>
              <span v-if="entry.verdict === 'hit'" class="ml-auto text-accent">✓ 命中</span>
              <span v-else-if="entry.verdict === 'miss'" class="ml-auto text-warn">✗ 判错</span>
              <span v-else-if="entry.verdict === 'pending'" class="ml-auto text-ink-3">待判定</span>
              <span v-else-if="entry.verdict === 'no_price'" class="ml-auto text-ink-3">无法判定</span>
              <span v-else class="ml-auto text-ink-3">{{ entry.skipReason ? '不动 · ' + entry.skipReason : '不动' }}</span>
              <span v-if="entry.nextChangePct != null" class="num" :class="dirTextClass(entry.nextChangePct)">次日 {{ formatPct(entry.nextChangePct) }}</span>
            </div>
          </div>
        </template>
      </section>

      <!-- 净值曲线与机会成本 -->
      <section class="mb-4 rounded-xl border border-border bg-surface p-5">
        <h3 class="mb-2.5 flex items-center gap-1.5 text-[15px] font-semibold text-ink">
          净值曲线与机会成本
          <InfoTip
            label="机会成本说明"
            text="空仓在账面上是 0，看起来没有代价；把同期买入持有摆在旁边，不动的代价就显形了 —— 负超额正是那些什么都没做的日子花掉的钱。曲线只画后端给的点，中间没结算的日子是缺口，不补。"
          />
        </h3>
        <p v-if="equityError" class="text-[12.5px] text-up" role="alert">{{ equityError }}</p>
        <Empty
          v-else-if="!equity || !equity.summary || equity.summary.days === 0"
          title="暂无净值快照"
          desc="模拟盘每日结算后，每个交易日会记一格。"
        />
        <template v-else>
          <div class="num mb-4 grid gap-2.5 [grid-template-columns:repeat(auto-fit,minmax(130px,1fr))]">
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">最新净值</span>
              <strong class="text-[16px] font-semibold text-ink">{{ formatMoney(equity.summary.latestEquity) }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">期间收益</span>
              <strong class="text-[16px] font-semibold" :class="dirTextClass(equity.summary.returnPct)">{{ formatPct(equity.summary.returnPct) }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">最大回撤</span>
              <strong v-if="equityColdStart" class="text-[13px] font-normal text-ink-3">次日起算</strong>
              <strong v-else class="text-[16px] font-semibold text-down">{{ formatPct(equity.summary.maxDrawdownPct) }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">同期买入持有</span>
              <strong v-if="equityColdStart" class="text-[13px] font-normal text-ink-3">次日起算</strong>
              <strong v-else class="text-[16px] font-semibold" :class="dirTextClass(equity.summary.buyAndHoldPct)">{{ formatPct(equity.summary.buyAndHoldPct) }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">超额（不动的代价）</span>
              <strong v-if="equityColdStart" class="text-[13px] font-normal text-ink-3">次日起算</strong>
              <strong v-else class="text-[16px] font-semibold" :class="dirTextClass(equity.summary.excessVsBuyAndHoldPct)">{{ formatPct(equity.summary.excessVsBuyAndHoldPct) }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">空仓占比</span>
              <strong class="text-[16px] font-semibold text-ink">{{ equity.summary.flatRatioPct === null ? 'N/A' : equity.summary.flatRatioPct + '%' }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">连续空仓</span>
              <strong class="text-[16px] font-semibold text-ink">{{ equity.summary.flatDays }} 天</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">样本</span>
              <strong class="text-[16px] font-semibold text-ink">{{ equity.summary.days }} 天</strong>
            </div>
          </div>
          <p v-if="equityColdStart" class="mb-2.5 rounded-lg bg-surface-2 px-3 py-2 text-[12.5px] leading-relaxed text-ink-2" role="status">
            模拟盘刚启动（样本 {{ equity.summary.days }} 天）：最大回撤、同期买入持有与超额需要至少两个交易日的净值才能计算，下一个交易日结算后开始有值——现在显示 N/A 不是数据丢了。
          </p>
          <p class="mb-2.5 text-[12px] leading-relaxed text-ink-3">
            自 {{ equity.summary.firstDate }} 起，共 {{ equity.summary.days }} 个交易日<template v-if="strategy.decisionModeSince">；{{ strategy.decisionModeSince }} 起换了决策来源（图中虚线），两侧不是同一个决策者</template><template v-if="strategy.configUpdatedAt">；{{ String(strategy.configUpdatedAt).slice(0, 10) }} 起参数修订过（图中虚线），前后口径不可比</template>。
          </p>
          <div ref="equityChartRef" class="h-[320px]"></div>
        </template>
      </section>

      <!-- 结算痕迹 -->
      <section class="mb-4 rounded-xl border border-border bg-surface p-5">
        <h3 class="mb-2.5 flex items-center gap-1.5 text-[15px] font-semibold text-ink">
          结算痕迹
          <InfoTip
            label="结算痕迹说明"
            text="每一行是一根 K 线上的一个结论，包括什么都没做以及为什么。账户数字不动时，从这里能看出是没信号、指标还没算出来，还是信号来了但被挡住。同一结论反复出现只记一行并累加次数。"
          />
        </h3>
        <p v-if="tracesError" class="text-[12.5px] text-up" role="alert">{{ tracesError }}</p>
        <Empty
          v-if="traces.length === 0 && !tracesError"
          title="暂无结算痕迹"
          desc="模拟盘开始结算后，每个交易日会留下一行。"
        />
        <div v-else class="flex flex-col gap-2">
          <div v-for="trace in traces" :key="trace.id" class="num rounded-lg border border-border p-3 text-[12px]">
            <div class="flex flex-wrap items-center gap-2.5">
              <span class="text-ink-2">{{ traceTimeLabel(trace) }}</span>
              <Badge :tone="trace.decision === 'buy' ? 'up' : trace.decision === 'sell' ? 'down' : 'neutral'">
                {{ trace.decision === 'buy' ? '买入' : trace.decision === 'sell' ? '卖出' : '未成交' }}
              </Badge>
              <span class="text-ink-3">{{ trace.settlementKind === 'daily' ? '日线结算' : '盘中评估' }}</span>
              <span v-if="trace.repeatCount > 1" class="text-ink-3">同一结论 {{ trace.repeatCount }} 次</span>
            </div>
            <p class="mt-1.5 text-[12.5px] leading-relaxed text-ink">{{ trace.summary }}</p>
            <details v-if="trace.snapshotJson || hasTraceMeta(trace)" class="mt-1.5">
              <summary class="cursor-pointer text-ink-3">技术口径与证据</summary>
              <div class="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-ink-3">
                <span v-if="trace.barClose">价格 {{ formatNumber(trace.barClose, 3) }}</span>
                <span v-if="trace.fillBasis">口径 {{ trace.fillBasis }}</span>
                <span v-if="trace.equityAfter !== null && trace.equityAfter !== undefined">净值 {{ formatMoney(trace.equityAfter) }}</span>
                <span v-if="trace.engineVersion">引擎 {{ trace.engineVersion }}</span>
                <span v-if="trace.adjustMode">复权 {{ trace.adjustMode }}</span>
                <span>
                  决策者 {{ trace.decisionMode === 'agent' ? '多角色委员会' : '规则引擎' }}
                  <template v-if="trace.decisionMode === 'agent' && trace.agentLlmCalls">
                    （{{ trace.agentLlmCalls }} 次调用 / {{ trace.agentTokens ?? 0 }} token）
                  </template>
                </span>
                <span v-if="trace.trigger && trace.trigger !== 'cron'">
                  触发 {{ trace.trigger === 'watch' ? '盯盘 agent' : trace.trigger }}
                </span>
              </div>
              <pre v-if="trace.snapshotJson" class="mt-1.5 max-h-[260px] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface-2 p-2 font-mono text-[11px] leading-relaxed text-ink-2">{{ prettySnapshot(trace.snapshotJson) }}</pre>
            </details>
          </div>
        </div>
      </section>
    </template>

    <StrategyEditSheet :open="editOpen" :strategy-id="strategy?.id" @close="editOpen = false" @saved="loadDetail" />
  </div>
</template>
