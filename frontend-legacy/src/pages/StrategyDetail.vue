
<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import InfoTip from '../components/InfoTip.vue'
import StrategyEditSheet from '../components/StrategyEditSheet.vue'
import * as echarts from 'echarts'
import {
  listStrategies,
  runBacktest,
  getStrategyDiagnostic,
  startPaper,
  stopPaper,
  getPaperAccount,
  getPaperTrades,
  getPaperTraces,
  getPaperEquity,
  switchDecisionMode,
  getExpectation,
  registerExpectation,
  getPaperOverview,
  getStrategyJournal,
  enableShare,
  disableShare
} from '../api/strategy.js'

const router = useRouter()
const route = useRoute()

// 策略编辑面板：表单化调整规则；保存后刷新本页（回测/模拟盘数据随新规则下次评估生效）
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
// 决策来源与预期
const modeLoading = ref(false)
const modeError = ref('')
const expectation = ref(null)
const expectationError = ref('')
const expectationLoading = ref(false)
const expectationForm = ref({ metric: 'excess_vs_buy_and_hold_pct', threshold: 0, horizonDays: 28 })
const expectationNotice = ref('')
const equityChartRef = ref(null)
let equityChart = null
/** 模拟盘总览里这一条（含"下次评估时间"与"最近一次结算"的**后端原话**）。 */
const overviewItem = ref(null)

// 响应形状（Result 信封 vs 裸 DTO）由 api/request.js 的响应拦截器统一处理，
// 信封会被剥掉，所以这里一律直接读 res.data —— 不再需要本地 unwrap()。

// 只认后端返回的中文业务提示，其次是本地的 fallback。
// 不再回落到 e?.message —— 那是 axios 的英文原文
// （如 "Request failed with status code 500"），会直接泄漏给用户且没说怎么恢复。
const errorMessage = (e, fallback) =>
  e?.response?.data?.message || fallback

// 图表颜色与 CSS 令牌同源，避免在 JS 里再抄一遍字面颜色。
// 读不到令牌时返回 undefined，交给 ECharts 默认色板兜底，
// 不会因为令牌缺失而画出不可读的线。
const colorToken = (name) => {
  if (typeof document === 'undefined') return undefined
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  // 万一拿到没被替换的 "var(...)" 也当作取不到，不能把非法颜色交给 ECharts
  return value && !value.startsWith('var(') ? value : undefined
}

const formatTime = (iso) => {
  if (!iso) return 'N/A'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return 'N/A'
  return d.toLocaleString('zh-CN', { hour12: false })
}

/**
 * 快照是"当时的证据"，原样展示、**不做美化**：
 * 它是给对账和排查用的，改成好看的形状反而会让数字与落库的内容不一致。
 * 解析失败就原样显示字符串 —— 证据宁可难看，不能不可信。
 */
const prettySnapshot = (json) => {
  if (!json) return ''
  try {
    return JSON.stringify(JSON.parse(json), null, 2)
  } catch (e) {
    return json
  }
}

// 模拟盘的账是**人民币**：整条链路（佣金最低 5 元、印花税、整手）都按 A 股规则结算，
// 这里却一直按 USD 格式化，页面上会出现 $96887 这种与账户口径不符的符号。
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

const pnlClass = (value) => {
  const n = Number(value)
  if (Number.isNaN(n) || n === 0) return ''
  return n > 0 ? 'positive' : 'negative'
}

// 交易方向在界面上统一显示中文。接口可能返回 buy/BUY/sell/SELL，
// 同一页的两个交易列表必须用同一套文案（原来模拟盘列表直接把英文原样显示）。
const sideLabel = (side) => {
  const s = String(side ?? '').toLowerCase()
  if (s === 'buy') return '买入'
  if (s === 'sell') return '卖出'
  return side || 'N/A'
}

// 账户是否**被评估过至少一次**。判断依据是 lastEvalAt：
// 刚启动的账户（尤其是 agent 模式）会只有初始资金，其余字段全空 ——
// 那不是坏了，是还没轮到它被评估，界面必须说出来（留白会被读成"一切正常"或"坏了"）。
const accountEvaluated = computed(() => Boolean(account.value?.lastEvalAt))

/**
 * 持仓市值 = 股数 × **最新价**。
 *
 * <p>以前这里写的是 `shares * avgCost`，也就是**成本**，却挂着"持仓市值"的标签：
 * 一个浮盈的账户会显示成几乎不赚钱，而这一页正是用来看"到底赚没赚"的。
 * 最新价拿不到时返回 null（显示 N/A），而不是拿成本冒充市值。
 */
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
    } catch (e) {
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
    } catch (e) {
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
    disclaimer: data.disclaimer || ''
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

  // 从 :root 读语义令牌：主色（策略曲线）、主色浅底（面积填充）、最弱一级文字（基准曲线）
  const accentColor = colorToken('--color-accent')
  const accentSoftColor = colorToken('--color-accent-soft')
  const mutedColor = colorToken('--color-text-muted')

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
        lineStyle: { width: 2, color: accentColor },
        // 图例图标 = 线段(lineStyle.stroke) + 空心圆点(itemStyle.fill)，两者不同源。
        // 只写 lineStyle.color，圆点就会去 ECharts 默认色板取色（#5070dd / #b6d634…），
        // 图例旁边于是挂着一个两条曲线上都不存在的颜色；tooltip 的小色点同源，也一起跑偏。
        itemStyle: { color: accentColor },
        areaStyle: { color: accentSoftColor }
      },
      {
        name: '买入持有',
        type: 'line',
        showSymbol: false,
        smooth: true,
        data: benchmarkCurve.map((point) => point.equity),
        lineStyle: { width: 1.5, type: 'dashed', color: mutedColor },
        itemStyle: { color: mutedColor }
      }
    ]
  }, { notMerge: true })
}

const journal = ref(null)
const journalError = ref('')

/**
 * 决策日志：每天"说了什么"×次日收盘"实际怎样"。
 * 命中率由记录说话，不由自述说话——它是"模型可信度"唯一的公开账本。
 */
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
  } catch (e) {
    account.value = null
  }
  try {
    const res = await getPaperTrades(strategy.value.id)
    const data = res.data
    trades.value = Array.isArray(data) ? data : []
  } catch (e) {
    trades.value = []
  }
  await loadTraces()
  await loadEquity()
  await loadJournal()
}

/**
 * 净值曲线 + 机会成本。
 *
 * 这一块存在的理由只有一个：**让"不动"也有代价**。
 * 空仓在账面上是 0，看起来没有代价；把同期的买入持有摆在旁边，
 * 负超额就是那些"什么都没做"的日子真正花掉的钱。
 * 汇总（回撤/空仓比例/超额）由后端同一份算法给出，前端只负责显示 —— 不自己再算一遍。
 */
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

/**
 * 净值曲线：**只画后端给的点**，并在决策来源变更日画一条竖线。
 *
 * 竖线是这一块的重点：换决策来源之后，曲线两段的含义不同（规则那一段和委员会那一段
 * 不是同一个东西做出来的），不标出来就会被当成一条连续的业绩。
 * 汇总数字（回撤/超额）后端已经算好，这里不重算 —— 前端自己算一遍就会和报告对不上。
 */
const renderEquityChart = async () => {
  await nextTick()
  const el = equityChartRef.value
  const points = equity.value?.points
  if (!el || !Array.isArray(points) || points.length === 0) return

  if (!equityChart) equityChart = echarts.init(el)

  const accentColor = colorToken('--color-accent')
  const accentSoftColor = colorToken('--color-accent-soft')
  const mutedColor = colorToken('--color-text-muted')
  const dates = points.map((point) => String(point.tradeDate || ''))
  const since = strategy.value?.decisionModeSince ? String(strategy.value.decisionModeSince) : ''
  const markLine = since && dates.includes(since)
    ? {
        symbol: 'none',
        lineStyle: { color: mutedColor, type: 'dashed' },
        label: { formatter: '切换决策来源', color: mutedColor },
        data: [{ xAxis: since }]
      }
    : undefined

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
        // 这条曲线没有图例，但 tooltip 的小色点同样取 itemStyle.fill，
        // 不写就会是 ECharts 默认色板的颜色，和曲线对不上。
        itemStyle: { color: accentColor },
        areaStyle: { color: accentSoftColor },
        markLine
      }
    ]
  }, { notMerge: true })
}

// 度量是封闭集（与后端 PaperEquitySeries.METRICS 同一批），界面只提供这三个。
const METRIC_OPTIONS = [
  { value: 'excess_vs_buy_and_hold_pct', label: '相对买入持有的超额（%）' },
  { value: 'return_pct', label: '账户收益（%）' },
  { value: 'max_drawdown_pct', label: '最大回撤（%，越大越好）' }
]

const EXPECTATION_STATUS_LABELS = {
  pending: '尚未到期',
  met: '已达成',
  unmet: '未达成',
  unmeasurable: '算不出来'
}

const expectationStatusLabel = (status) =>
  EXPECTATION_STATUS_LABELS[status] || status || '未知'

/**
 * 预期：把"这条策略接下来会怎样"变成到期能验的承诺。
 *
 * 没有登记过时**要说出来**（而不是显示空白）：空白会被读成"一切都好"，
 * 而这里恰恰是"还没有人下过承诺"的地方 —— 不表态不该看起来像没问题。
 */
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
      horizonDays: Number(expectationForm.value.horizonDays)
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
const decisionModeLabel = (mode) =>
  (mode || 'rule') === 'agent' ? '多角色委员会（agent）' : '规则引擎（rule）'
const isAgentMode = computed(() => decisionMode.value === 'agent')

// 止损类条件（DSL 封闭集里的那三类）。退出规则一条都没有时给出提示：
// 大跌时没有硬性减亏线，止损硬通道再通畅也无险可止 —— 这是配置问题，不是代码问题。
const STOP_CONDITION_TYPES = ['stop_loss_pct', 'take_profit_pct', 'trailing_stop_pct']
const hasStopCondition = computed(() => {
  const cfg = strategy.value?.configJson
  if (!cfg) return true // 读不到配置就不下断言（宁可不提示，也不误报）
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

/**
 * 切换决策来源。
 *
 * 这是一次**会改变历史解释依据**的操作（净值曲线从生效日起分成两段），
 * 所以走显式接口、由后端记录生效日；界面上把代价（每次开会约 2 万 token）
 * 直接写在按钮旁边，避免"点一下试试"变成一次没人预期的开销。
 */
const changeDecisionMode = async (mode) => {
  if (mode === decisionMode.value) return
  modeLoading.value = true
  modeError.value = ''
  try {
    await switchDecisionMode(strategy.value.id, mode)
    await loadDetail()
  } catch (e) {
    modeError.value = errorMessage(e, '切换决策来源失败')
  } finally {
    modeLoading.value = false
  }
}

/**
 * 痕迹：这一块存在的唯一理由是回答"**为什么今天没成交**"。
 *
 * 账户数字没变、成交列表为空时，用户以前无从判断是"没信号"还是"信号来了但被挡住"。
 * 失败时也**要说出来**（而不是留白）：留白会被读成"什么都没发生"，
 * 而这里恰恰是"发生了什么但没成交"的地方。
 */
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

/**
 * 痕迹行的时间：盘中痕迹有 barTime（一根实时 bar）；日线痕迹只有 tradeDate（一个日期）。
 *
 * 日期字符串直接 new Date("2026-10-07") 会按 UTC 零点解析、本地（+8）显示成 08:00 ——
 * 一个不存在的"早上八点结算"。所以日线那一行只显示日期本身，不造一个时间出来。
 */
const traceTimeLabel = (trace) =>
  trace.barTime ? formatTime(trace.barTime) : (trace.tradeDate || '')

/** 折叠区要不要渲染：一个技术字段都没有（也没有快照）时，连"技术口径"这一行都不出。 */
const hasTraceMeta = (trace) => Boolean(
  trace.barClose || trace.fillBasis || trace.engineVersion || trace.adjustMode
  || trace.decisionMode || trace.trigger
)

/**
 * 顶部状态条的数据：直接取总览里这一条。
 *
 * <p>刻意**不在前端自己拼**"下次评估时间"和"为什么没动"：那两句话的口径在
 * PaperSchedule 与痕迹的人话映射里各只有一处实现，前端复述一遍迟早会与总览页不一致
 * （而"三处各说一个时间"比不给时间更糟）。读失败就整条不显示，不影响页面其它部分。
 */
const loadOverview = async () => {
  try {
    const res = await getPaperOverview()
    const list = Array.isArray(res.data) ? res.data : []
    overviewItem.value = list.find((row) => String(row.strategyId) === String(strategy.value.id)) || null
  } catch (e) {
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
const shareLink = computed(() =>
  shareToken.value ? `${location.origin}/share/${shareToken.value}` : '')

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

onMounted(loadDetail)
onUnmounted(() => {
  if (backtestChart) {
    backtestChart.dispose()
    backtestChart = null
  }
  if (equityChart) {
    equityChart.dispose()
    equityChart = null
  }
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>策略详情</h1>
      <div class="page-actions">
        <button type="button" class="btn quiet" @click="goBack">← 返回策略库</button>
      </div>
    </div>

      <!-- 状态互斥，顺序为：加载中 → 出错 → 未找到 → 有数据。
           加载失败时不再渲染任何"空"状态，
           否则会出现"没拿到数据"和"未找到该策略"同时成立的矛盾提示。 -->
      <div v-if="loading" class="empty">加载中...</div>

      <p v-else-if="error && !strategy" class="error" role="alert">{{ error }}</p>

      <div v-else-if="notFound" class="empty">
        未找到该策略
        <button type="button" class="back-link" @click="goBack">← 返回策略库</button>
      </div>

      <template v-else-if="strategy">
        <!-- 操作类错误（回测 / 模拟盘 / 诊断）在内容区提示，不影响已加载的数据 -->
        <p v-if="error" class="error" role="alert">{{ error }}</p>

        <!-- 运行状态条：把"它在不在跑、谁在做决定、今天动没动、下次什么时候"提到第一屏。
             原来这些只能从下面几张卡里逐块拼，用户看不出这条策略此刻是什么状态。 -->
        <section v-if="overviewItem" class="card run-strip num">
          <div class="run-badges">
            <span class="run-badge" :class="{ on: overviewItem.paperEnabled }">
              {{ overviewItem.paperEnabled ? '模拟盘运行中' : '模拟盘未启动' }}
            </span>
            <span class="run-badge mode" :class="{ agent: overviewItem.decisionMode === 'agent' }">
              {{ decisionModeLabel(overviewItem.decisionMode) }}
            </span>
            <span v-if="overviewItem.decisionModeSince" class="run-note">
              自 {{ overviewItem.decisionModeSince }} 起生效
            </span>
          </div>
          <p v-if="overviewItem.lastSettlement" class="run-line">
            <span class="run-label">最近一次结算</span>
            <!-- 痕迹那句人话本身就以"日期 时间（标的）"开头，这里只补口径，不再重复日期 -->
            {{ overviewItem.lastSettlement.sentence }}
            （{{ overviewItem.lastSettlement.settlementKind === 'daily' ? '日线结算' : '盘中评估' }}）
            <template v-if="overviewItem.lastSettlement.decisionMode === 'agent' && overviewItem.lastSettlement.agentLlmCalls">
              （委员会 {{ overviewItem.lastSettlement.agentLlmCalls }} 次调用 /
              {{ overviewItem.lastSettlement.agentTokens ?? 0 }} token）
            </template>
          </p>
          <p v-else class="run-line">
            <span class="run-label">最近一次结算</span>还没有结算记录
          </p>
          <p class="run-line">
            <span class="run-label">下次</span>{{ overviewItem.nextEvaluationNote }}
          </p>
        </section>

        <section class="card">
          <div class="detail-title">
            <div>
              <h2>{{ strategy.name }}</h2>
              <span class="symbol-code">{{ strategy.symbol }}</span>
              <span class="paper-badge" :class="{ enabled: strategy.paperEnabled }">
                {{ strategy.paperEnabled ? '模拟盘中' : '未启动' }}
              </span>
            </div>
            <div class="detail-actions">
              <button type="button" class="btn-edit" @click="editOpen = true">调整策略</button>
              <button type="button" class="btn-backtest" :disabled="backtestLoading" @click="handleBacktest">
                {{ backtestLoading ? '运行中...' : '运行回测' }}
              </button>
              <button
                type="button"
                class="btn-paper"
                :class="{ running: strategy.paperEnabled }"
                :disabled="paperLoading"
                @click="togglePaper"
              >
                {{ strategy.paperEnabled ? '停止模拟盘' : '启动模拟盘' }}
              </button>
              <!-- 只读分享：token 即凭证，重新生成=旧链接失效 -->
              <button
                type="button"
                class="btn-share"
                :disabled="shareBusy"
                @click="toggleShare"
              >{{ shareToken ? '重发分享链接' : '生成分享链接' }}</button>
            </div>
          </div>

          <p v-if="shareLink" class="share-line" role="status">
            分享链接（只读，任何人可看）：<code>{{ shareLink }}</code>
            <button type="button" class="btn-copy" @click="copyShare">{{ copied ? '已复制' : '复制' }}</button>
            <button type="button" class="btn-copy" :disabled="shareBusy" @click="stopShare">停止分享</button>
          </p>

          <div class="meta num">
            <span>创建: {{ formatTime(strategy.createdAt) }}</span>
            <span>更新: {{ formatTime(strategy.updatedAt) }}</span>
            <span v-if="strategy.lastBacktestAt">最近回测: {{ formatTime(strategy.lastBacktestAt) }}</span>
          </div>

          <!-- 缺止损条件：配置层的减亏缺口，必须在第一屏说出来 ——
               止损硬通道只对配置了止损条件的策略生效，没有条件就无险可止 -->
          <p v-if="!hasStopCondition" class="stop-warning" role="note">
            ⚠ 退出规则里没有任何止损条件（止损 stop_loss_pct / 移动止损 trailing_stop_pct）——
            大跌时这条策略没有硬性减亏线。在策略配置的 exit 里加一条止损条件即可接上盘中硬止损。
          </p>

          <h3>策略配置</h3>
          <pre class="config-block">{{ prettyConfig }}</pre>
        </section>

        <!-- 决策来源：谁在做决定。只留状态一行 + 切换按钮；
             机制说明（分段、开会成本、盘中不做规则检查）收进 ⓘ —— 用户要先看到"现在是谁"，
             "为什么"应该是按需展开的第二层，而不是常驻的三段文字。 -->
        <section class="card">
          <h3>决策来源
            <InfoTip
              label="决策来源说明"
              text="换来源后，净值曲线从生效日分成两段：两段不是同一个决策者做出来的，不能当一条连续的业绩读。委员会有事才开会（规则信号、波动骤增、太久没看、首次决策），开一次约 8 次模型调用、2 万 token；没事的日子不花钱。agent 模式的开仓与一般平仓只在 15:30 结算时决策；止损类卖出是硬风控，盘中直接执行，不受授权限制。"
            />
          </h3>
          <p class="trace-hint">
            当前由 <strong>{{ decisionModeLabel(decisionMode) }}</strong> 做决定<template
              v-if="strategy.decisionModeSince"
            >，自 {{ strategy.decisionModeSince }} 起生效</template>。
          </p>
          <p v-if="modeError" class="error" role="alert">{{ modeError }}</p>
          <div class="detail-actions">
            <button
              type="button"
              class="btn-mode"
              :disabled="modeLoading || !isAgentMode"
              @click="changeDecisionMode('rule')"
            >
              {{ isAgentMode ? '切回规则引擎' : '当前：规则引擎' }}
            </button>
            <button
              type="button"
              class="btn-mode agent"
              :disabled="modeLoading || isAgentMode"
              @click="changeDecisionMode('agent')"
            >
              {{ isAgentMode ? '当前：多角色委员会' : '切换为多角色委员会' }}
            </button>
          </div>
        </section>

        <!-- 预期：把"接下来会怎样"变成到期能验的承诺。机制说明收进 ⓘ。 -->
        <section class="card">
          <h3>可验证预期
            <InfoTip
              label="可验证预期说明"
              text="写清度量、门槛与到期日，到期后由每日结算用同一份净值自动判定：达成、未达成或算不出来，三者分开。都是账户层面可观测值，不含股价预测。同一时刻只有一条有效预期，登记新的即取代旧的。"
            />
          </h3>
          <p v-if="expectationError" class="error" role="alert">{{ expectationError }}</p>
          <p v-if="expectationNotice" class="notice">{{ expectationNotice }}</p>

          <template v-if="expectation">
            <div class="expectation-head">
              <span class="expectation-status" :class="expectation.status">
                {{ expectationStatusLabel(expectation.status) }}
              </span>
              <span class="trace-kind">
                度量：{{ expectation.metricLabel || expectation.metric }}
                ｜ 门槛：{{ expectation.threshold }}
                ｜ 到期：{{ expectation.deadline }}
              </span>
            </div>
            <p class="expectation-sentence">{{ expectation.sentence }}</p>
            <div class="trace-meta num">
              <span v-if="expectation.registeredAt">登记于 {{ expectation.registeredAt }}</span>
              <span v-if="expectation.outcome !== null && expectation.outcome !== undefined">
                实际值 {{ expectation.outcome }}
              </span>
              <span v-if="expectation.evaluatedAt">回填于 {{ formatTime(expectation.evaluatedAt) }}</span>
            </div>
          </template>
          <div v-else-if="!expectationError" class="expectation-empty">
            还没有登记预期 —— 目前没有人对这条策略的未来下过承诺。
          </div>

          <div class="expectation-form">
            <label>
              <span>度量</span>
              <select v-model="expectationForm.metric">
                <option v-for="option in METRIC_OPTIONS" :key="option.value" :value="option.value">
                  {{ option.label }}
                </option>
              </select>
            </label>
            <label>
              <span>门槛（实际 ≥ 门槛记为达成）</span>
              <input v-model="expectationForm.threshold" type="number" step="0.1" />
            </label>
            <label>
              <span>跨度（自然日）</span>
              <input v-model="expectationForm.horizonDays" type="number" min="1" step="1" />
            </label>
            <button type="button" class="btn-expectation" :disabled="expectationLoading" @click="submitExpectation">
              {{ expectationLoading ? '登记中...' : '登记预期' }}
            </button>
          </div>
        </section>

        <section v-if="backtestLoading || backtestData !== null" class="card">
          <h3>回测结果</h3>
          <div v-if="backtestLoading" class="empty">回测运行中...</div>
          <div v-else-if="backtestResult && backtestResult.error" class="empty">{{ backtestResult.error }}</div>
          <template v-else-if="backtestResult">
            <!-- .num 让块内所有数字用等宽字形，行情/指标刷新时不会左右抖动 -->
            <div class="metric-grid num">
              <div class="metric">
                <span>策略收益</span>
                <strong :class="pnlClass(backtestResult.total_return_pct)">{{ formatPct(backtestResult.total_return_pct) }}</strong>
              </div>
              <div class="metric">
                <span>买入持有</span>
                <strong :class="pnlClass(backtestResult.buy_and_hold_return_pct)">{{ formatPct(backtestResult.buy_and_hold_return_pct) }}</strong>
              </div>
              <div class="metric">
                <span>超额收益</span>
                <strong :class="pnlClass(backtestResult.excess_return_pct)">{{ formatPct(backtestResult.excess_return_pct) }}</strong>
              </div>
              <div class="metric">
                <span>最大回撤</span>
                <strong class="negative">{{ formatPct(backtestResult.max_drawdown_pct) }}</strong>
              </div>
              <div class="metric">
                <span>胜率</span>
                <strong>{{ formatPct(backtestResult.win_rate) }}</strong>
              </div>
              <div class="metric">
                <span>夏普比率</span>
                <strong>{{ formatNumber(backtestResult.sharpe_ratio, 3) }}</strong>
              </div>
              <div class="metric">
                <span>年化收益</span>
                <strong :class="pnlClass(backtestResult.annualized_return_pct)">{{ formatPct(backtestResult.annualized_return_pct) }}</strong>
              </div>
              <div class="metric">
                <span>已平仓交易</span>
                <strong>{{ backtestResult.closed_trades ?? 0 }}</strong>
              </div>
            </div>

            <div ref="backtestChartRef" class="equity-chart"></div>

            <div v-if="backtestResult.trade_log && backtestResult.trade_log.length" class="backtest-trades">
              <h4>回测交易明细</h4>
              <div v-for="(trade, index) in backtestResult.trade_log" :key="index" class="trade-row num">
                <span class="trade-date">{{ trade.date }}</span>
                <span class="trade-side" :class="trade.side">{{ sideLabel(trade.side) }}</span>
                <span>价格: {{ formatNumber(trade.price, 3) }}</span>
                <span>数量: {{ formatNumber(trade.shares, 2) }}</span>
                <span>金额: {{ formatMoney(trade.amount) }}</span>
                <span class="trade-reason" v-if="trade.reason">触发: {{ trade.reason }}</span>
              </div>
            </div>
          </template>
        </section>

        <section class="card diagnostic-card">
          <div class="diagnostic-head">
            <div>
              <!-- diagnostic 在未点"加载模型诊断"时是 null；这里若写 diagnostic.disclaimer，
                   渲染会抛 TypeError 中断整个组件的 patch，页面永久停在"加载中..."（2026-10-04 全量测试实测） -->
              <h3>模型诊断 <InfoTip label="模型诊断说明" :text="diagnostic?.disclaimer || '模型诊断仅作低权重参考，不参与策略买卖决策，不构成投资建议。'" /></h3>
            </div>
            <button type="button" class="btn-diagnostic" :disabled="diagnosticLoading" @click="loadDiagnostic">
              {{ diagnosticLoading ? '加载中...' : diagnosticData ? '重新加载' : '加载模型诊断' }}
            </button>
          </div>

          <p v-if="diagnosticError" class="error" role="alert">{{ diagnosticError }}</p>

          <template v-if="diagnostic">
            <div class="diagnostic-grid">
              <div class="diagnostic-group">
                <span class="group-label">风险指标</span>
                <div v-if="diagnostic.risk && !diagnostic.risk.error" class="diagnostic-meta num">
                  <span>波动率: {{ formatNumber(diagnostic.risk.annual_volatility_pct, 2) }}%</span>
                  <span>最大回撤: {{ formatPct(diagnostic.risk.max_drawdown_pct) }}</span>
                  <span>当前回撤: {{ formatPct(diagnostic.risk.current_drawdown_pct) }}</span>
                  <span>距 MA20: {{ formatPct(diagnostic.risk.distance_from_ma20_pct) }}</span>
                  <span>风险等级: <b>{{ diagnostic.risk.risk_level || 'N/A' }}</b></span>
                </div>
                <div v-else class="empty">风险数据不可用</div>
              </div>

              <div class="diagnostic-group">
                <span class="group-label">模型状态</span>
                <div v-if="diagnostic.model_status" class="diagnostic-meta num">
                  <span>可用: {{ diagnostic.model_status.available ? '是' : '否' }}</span>
                  <span>缓存命中: {{ diagnostic.model_status.cache_hit ? '是' : '否' }}</span>
                  <span>磁盘模型: {{ diagnostic.model_status.disk_model_available ? '有' : '无' }}</span>
                  <span>参与决策: {{ diagnostic.model_status.decision_use ? '是' : '否' }}</span>
                </div>
                <div v-else class="empty">模型状态不可用</div>
              </div>

              <div class="diagnostic-group">
                <span class="group-label">模型共识</span>
                <div v-if="diagnostic.model_consensus" class="diagnostic-meta num">
                  <span>共识: <b>{{ diagnostic.model_consensus.consensus || 'neutral' }}</b></span>
                  <span>置信度: <b>{{ diagnostic.model_consensus.confidence || 'N/A' }}</b></span>
                  <span>参与决策: {{ diagnostic.model_consensus.decision_use ? '是' : '否' }}</span>
                </div>
                <div v-else class="empty">模型共识不可用</div>
              </div>
            </div>
          </template>
        </section>

        <section class="card">
          <h3>模拟盘账户</h3>
          <template v-if="account">
            <!-- 先给"还没有评估过"一个明确的说法，再决定要不要摆出那一格数字。
                 只摆 N/A 的后果实测过一次：用户看到一片 N/A，第一反应是"这功能坏了"。 -->
            <div v-if="!accountEvaluated" class="account-pending">
              <strong>这个账户还没有被评估过一次。</strong>除初始资金与现金外，其余字段都还没有值。
              <template v-if="isAgentMode">
                委员会只在交易日 15:30 结算时决策；启动或切换的那一刻会先开一次首会，
                当天没有 K 线（休市/数据未出）时才会停在这里。
              </template>
              <template v-else>
                下一次盘中检查在几分钟内，日线结算在下一个交易日 15:30。
              </template>
            </div>
            <div class="account-grid num">
              <div><span>初始资金</span><strong>{{ formatMoney(account.initialCapital) }}</strong></div>
              <div><span>现金</span><strong>{{ formatMoney(account.cash) }}</strong></div>
              <template v-if="accountEvaluated">
                <div><span>总权益</span><strong>{{ formatMoney(account.equity) }}</strong></div>
                <div><span>持仓市值</span><strong>{{ formatMoney(positionValue) }}</strong></div>
                <div><span>持仓数量</span><strong>{{ account.shares }}</strong></div>
                <div><span>平均成本</span><strong>{{ formatMoney(account.avgCost) }}</strong></div>
                <!-- 澄清语义：这是持仓期间的最高"股价"（trailing_stop 的基准），
                     不是净值高点 —— 金融语境的"最高水位"默认指后者，混排容易误读 -->
                <div><span>持仓期最高价（移动止损基准）</span><strong>{{ formatMoney(account.highWatermark) }}</strong></div>
                <div><span>最新价格</span><strong>{{ formatMoney(account.lastPrice) }}</strong></div>
                <div>
                  <span>最新信号</span>
                  <strong>
                    {{ account.lastSignal === 'buy' ? '买入' : account.lastSignal === 'sell' ? '卖出' : account.lastSignal === 'hold' ? '持有' : (account.lastSignal || 'N/A') }}
                  </strong>
                </div>
                <div><span>最近评估</span><strong>{{ formatTime(account.lastEvalAt) }}</strong></div>
              </template>
            </div>
          </template>
          <div v-else class="empty">暂无模拟盘账户</div>
        </section>

        <section class="card">
          <h3>模拟盘交易记录</h3>
          <div v-if="trades.length === 0" class="empty">暂无交易记录</div>
          <div v-else class="trades-list num">
            <div v-for="(trade, index) in trades" :key="index" class="trade-row">
              <span class="trade-date">{{ formatTime(trade.createdAt || trade.tradeDate) }}</span>
              <span class="trade-side" :class="trade.side">{{ sideLabel(trade.side) }}</span>
              <span>{{ trade.symbol }}</span>
              <span>价格: {{ trade.price }}</span>
              <span>数量: {{ trade.shares }}</span>
              <span>金额: {{ formatMoney(trade.amount) }}</span>
              <span class="trade-reason" v-if="trade.reason">{{ trade.reason }}</span>
              <!-- 有痕迹就给出跳转依据；为空时说明是"上线前的成交"或"记录写入失败"，
                   不能留白 —— 留白会被读成"一切正常" -->
              <span class="trade-reason" v-if="trade.traceId">痕迹 #{{ trade.traceId }}</span>
              <span class="trade-reason" v-else>无痕迹（痕迹功能上线前的成交，或痕迹写入失败）</span>
            </div>
          </div>
        </section>

        <section class="card">
          <h3>决策日志 · 说了什么 vs 后来怎样
            <InfoTip
              label="决策日志说明"
              text="每天的判定用次日收盘方向对答案：买对了涨、卖对了跌算命中。量的是方向对错，不是盈亏。最近一行要等下一根收盘才能判定；缺数据的日子标无法判定，不算错。"
            />
          </h3>
          <p v-if="journalError" class="error" role="alert">{{ journalError }}</p>
          <div v-else-if="!journal || !journal.entries || journal.entries.length === 0" class="empty">
            还没有可对照的决策记录（模拟盘跑起来之后，这里每天会多一行）
          </div>
          <template v-else>
            <div class="metric-grid num" v-if="journal.evaluated > 0 || journal.pending > 0">
              <div class="metric">
                <span>方向命中率</span>
                <strong>{{ journal.hitRate != null ? journal.hitRate + '%' : '暂无样本' }}</strong>
              </div>
              <div class="metric">
                <span>命中 / 判错</span>
                <strong>{{ journal.hits }} / {{ journal.misses }}</strong>
              </div>
              <div class="metric">
                <span>待判定</span>
                <strong>{{ journal.pending }}</strong>
              </div>
              <div class="metric">
                <span>选择不动</span>
                <strong>{{ journal.skips }} 天</strong>
              </div>
            </div>
            <div class="journal-list num">
              <div v-for="entry in journal.entries" :key="entry.tradeDate" class="journal-row">
                <span class="journal-date">{{ entry.tradeDate }}</span>
                <span class="journal-decision" :class="entry.decision">{{ decisionLabel(entry.decision) }}</span>
                <span v-if="entry.decisionMode" class="journal-mode">{{ entry.decisionMode === 'agent' ? '委员会' : '规则' }}</span>
                <span v-if="entry.verdict === 'hit'" class="journal-verdict hit">✓ 命中</span>
                <span v-else-if="entry.verdict === 'miss'" class="journal-verdict miss">✗ 判错</span>
                <span v-else-if="entry.verdict === 'pending'" class="journal-verdict pending">待判定</span>
                <span v-else-if="entry.verdict === 'no_price'" class="journal-verdict pending">无法判定</span>
                <span v-else class="journal-verdict skip">{{ entry.skipReason ? '不动 · ' + entry.skipReason : '不动' }}</span>
                <span v-if="entry.nextChangePct != null" class="journal-change" :class="pnlClass(entry.nextChangePct)">
                  次日 {{ formatPct(entry.nextChangePct) }}
                </span>
              </div>
            </div>
          </template>
        </section>

        <section class="card">
          <h3>净值曲线与机会成本
            <InfoTip
              label="机会成本说明"
              text="空仓在账面上是 0，看起来没有代价；把同期买入持有摆在旁边，不动的代价就显形了 —— 负超额正是那些什么都没做的日子花掉的钱。曲线只画后端给的点，中间没结算的日子是缺口，不补。"
            />
          </h3>
          <p v-if="equityError" class="error" role="alert">{{ equityError }}</p>
          <div v-else-if="!equity || !equity.summary || equity.summary.days === 0" class="empty">
            暂无净值快照（模拟盘每日结算后，每个交易日会记一格）
          </div>
          <template v-else>
            <div class="metric-grid num">
              <div class="metric">
                <span>最新净值</span>
                <strong>{{ formatMoney(equity.summary.latestEquity) }}</strong>
              </div>
              <div class="metric">
                <span>期间收益</span>
                <strong :class="pnlClass(equity.summary.returnPct)">{{ formatPct(equity.summary.returnPct) }}</strong>
              </div>
              <div class="metric">
                <span>最大回撤</span>
                <strong class="negative">{{ formatPct(equity.summary.maxDrawdownPct) }}</strong>
              </div>
              <div class="metric">
                <span>同期买入持有</span>
                <strong :class="pnlClass(equity.summary.buyAndHoldPct)">{{ formatPct(equity.summary.buyAndHoldPct) }}</strong>
              </div>
              <div class="metric">
                <span>超额（不动的代价）</span>
                <strong :class="pnlClass(equity.summary.excessVsBuyAndHoldPct)">{{ formatPct(equity.summary.excessVsBuyAndHoldPct) }}</strong>
              </div>
              <div class="metric">
                <span>空仓占比</span>
                <strong>{{ equity.summary.flatRatioPct === null ? 'N/A' : equity.summary.flatRatioPct + '%' }}</strong>
              </div>
              <div class="metric">
                <span>连续空仓</span>
                <strong>{{ equity.summary.flatDays }} 天</strong>
              </div>
              <div class="metric">
                <span>样本</span>
                <strong>{{ equity.summary.days }} 天</strong>
              </div>
            </div>
            <!-- 曲线起讫与分界：一句话给全，详细机制在 ⓘ 里 -->
            <p class="trace-hint">
              自 {{ equity.summary.firstDate }} 起，共 {{ equity.summary.days }} 个交易日<template
                v-if="strategy.decisionModeSince"
              >；{{ strategy.decisionModeSince }} 起换了决策来源（图中虚线），两侧不是同一个决策者</template>。
            </p>
            <div ref="equityChartRef" class="equity-chart"></div>
          </template>
        </section>

        <section class="card">
          <h3>结算痕迹
            <InfoTip
              label="结算痕迹说明"
              text="每一行是一根 K 线上的一个结论，包括什么都没做以及为什么。账户数字不动时，从这里能看出是没信号、指标还没算出来，还是信号来了但被挡住。同一结论反复出现只记一行并累加次数。"
            />
          </h3>
          <p v-if="tracesError" class="error" role="alert">{{ tracesError }}</p>
          <div v-if="traces.length === 0 && !tracesError" class="empty">
            暂无结算痕迹（模拟盘开始结算后，每个交易日会留下一行）
          </div>
          <div v-else class="traces-list num">
            <div v-for="trace in traces" :key="trace.id" class="trace-row">
              <div class="trace-head">
                <span class="trace-date">{{ traceTimeLabel(trace) }}</span>
                <span class="trace-decision" :class="trace.decision">
                  {{ trace.decision === 'buy' ? '买入' : trace.decision === 'sell' ? '卖出' : '未成交' }}
                </span>
                <span class="trace-kind">{{ trace.settlementKind === 'daily' ? '日线结算' : '盘中评估' }}</span>
                <span v-if="trace.repeatCount > 1" class="trace-kind">同一结论 {{ trace.repeatCount }} 次</span>
              </div>
              <!-- 一句人话（后端确定性拼出来，不由模型生成）：这是"不看代码也能明白"的那一层 -->
              <p class="trace-summary">{{ trace.summary }}</p>
              <!-- 口径/引擎/复权/决策者/成本是审计材料，不是日常阅读材料：
                   折叠进"技术口径"，默认只留人话那一层（2026-10-08 精简）。 -->
              <details v-if="trace.snapshotJson || hasTraceMeta(trace)" class="trace-evidence">
                <summary>技术口径与证据</summary>
                <div class="trace-meta">
                  <span v-if="trace.barClose">价格 {{ formatNumber(trace.barClose, 3) }}</span>
                  <span v-if="trace.fillBasis">口径 {{ trace.fillBasis }}</span>
                  <span v-if="trace.equityAfter !== null && trace.equityAfter !== undefined">
                    净值 {{ formatMoney(trace.equityAfter) }}
                  </span>
                  <span v-if="trace.engineVersion">引擎 {{ trace.engineVersion }}</span>
                  <span v-if="trace.adjustMode">复权 {{ trace.adjustMode }}</span>
                  <!-- 谁做的决定 + 花了多少：agent 的调用与 token 事后只能从这里重建 -->
                  <span>
                    决策者 {{ trace.decisionMode === 'agent' ? '多角色委员会' : '规则引擎' }}
                    <template v-if="trace.decisionMode === 'agent' && trace.agentLlmCalls">
                      （{{ trace.agentLlmCalls }} 次调用 / {{ trace.agentTokens ?? 0 }} token）
                    </template>
                  </span>
                  <!-- 触发来源（人话）：例行的 cron 调度不标；盯盘 agent 与手动触发值得标 -->
                  <span v-if="trace.trigger && trace.trigger !== 'cron'">
                    触发 {{ trace.trigger === 'watch' ? '盯盘 agent' : trace.trigger }}
                  </span>
                </div>
                <pre v-if="trace.snapshotJson">{{ prettySnapshot(trace.snapshotJson) }}</pre>
              </details>
            </div>
          </div>
        </section>
      </template>
  </div>

    <StrategyEditSheet :open="editOpen" :strategy-id="strategy?.id"
                        @close="editOpen = false" @saved="loadDetail" />
</template>

<style scoped>


.card {
  background: var(--color-bg-surface);
  border-radius: var(--radius-lg);
  padding: 18px 20px;
  margin-bottom: 16px;
  box-shadow: var(--shadow-1);
}
.detail-title { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.detail-title h2 { margin: 0 0 6px; font-size: 20px; }
.symbol-code { font-size: 13px; color: var(--color-text-muted); }
/* 徽章文字对浅底 4.97 / 4.98:1 达标；状态另有文字（模拟盘中/未启动），不靠颜色单独表意。
   语义层没有"危险/成功的浅色底"令牌，底色统一用 --color-bg-subtle，色相交给文字与边框。 */
.paper-badge {
  display: inline-block;
  margin-left: 8px;
  font-size: 11px;
  padding: 2px 7px;
  border-radius: var(--radius-pill);
  background: var(--color-bg-subtle);
  /* "未启动"是普通关闭态而非错误：原来用危险红会把中性状态渲染成故障提示
     （危险色用在非破坏性状态上）。关闭态用中性色，只有启用才用成功色。 */
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border-strong);
}
.paper-badge.enabled {
  color: var(--color-success);
  background: var(--color-success-soft);
  border-color: var(--color-success-mark);
}
.detail-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.detail-actions button {
  padding: 7px 14px;
  border: none;
  border-radius: var(--radius-sm);
  font-size: 13px;
  cursor: pointer;
  color: var(--color-text-on-accent);
}
.detail-actions button:disabled { opacity: 0.5; cursor: not-allowed; }
/* 回测沿用原来的紫色：语义层没有"次级动作"色，只能取原始色板 --c-purple-700，白字 6.94:1 */
.btn-backtest { background: var(--c-purple-700); }
/* 旧的橙色、绿色配白字只有 2.38:1 / 2.27:1，都不达 AA */
.btn-paper { background: var(--color-warning); }
.btn-paper.running { background: var(--color-success); }
/* 决策来源按钮：不产生方向（买/卖）语义，所以用中性色与主色，而不是涨跌色 */
.btn-mode { background: var(--c-neutral-800); }
/* 分享：外向动作，主色描边的中空样式与"改变策略状态"的实心按钮区分开 */
.btn-share {
  background: transparent;
  border: 1px solid var(--color-accent);
  color: var(--color-accent);
}
.share-line {
  margin: 10px 0 0;
  font-size: 12.5px;
  color: var(--color-text-secondary);
  word-break: break-all;
}
.share-line code { background: var(--color-bg-subtle); padding: 2px 6px; border-radius: var(--radius-sm); }
.btn-copy {
  margin-left: 8px;
  padding: 3px 10px;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 12px;
  cursor: pointer;
}
.btn-copy:hover { border-color: var(--color-accent); color: var(--color-accent); }
.btn-mode.agent { background: var(--color-accent); }
.btn-mode:disabled { cursor: default; }

.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  color: var(--color-text-secondary);
  font-size: 12px;
  margin: 12px 0 16px;
}
.card h3 { margin: 0 0 12px; font-size: 16px; }

.config-block {
  margin: 0;
  background: var(--color-bg-inverse);
  color: var(--color-text-inverse);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 400px;
  overflow: auto;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}
.metric {
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.metric span { font-size: 12px; color: var(--color-text-secondary); }
.metric strong { font-size: 18px; color: var(--color-text-primary); }
/* A 股习惯红涨绿跌：涨用红、跌用绿。
   注意这里用 --color-gain / --color-loss 而不是 --color-danger / --color-success：
   涨跌是"行情方向"，与"成功/危险"是两个不同的语义，虽然恰好落到同一批色相。
   直接引用 danger/success 会让"红色=危险"的既有含义漂移（better-colors：一个颜色一个含义）。
   两者对浅底的实测对比度：gain 4.97:1、loss 4.98:1，均达正文要求。 */
.metric strong.positive { color: var(--color-gain); }
.metric strong.negative { color: var(--color-loss); }

.equity-chart {
  width: 100%;
  height: 320px;
  margin: 4px 0 18px;
}

.backtest-trades h4 { margin: 4px 0 10px; font-size: 14px; }

.diagnostic-card { border-left: 3px solid var(--color-border-strong); }
.diagnostic-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
  flex-wrap: wrap;
}
.diagnostic-head h3 { margin: 0; }
.btn-diagnostic {
  padding: 7px 14px;
  border: none;
  border-radius: var(--radius-sm);
  font-size: 13px;
  cursor: pointer;
  /* 旧的中灰配白字仅 3.36:1，不达 AA。语义层没有"中性实心按钮"色，
     取最接近的原始色板 --c-neutral-800，白字 7.00:1 */
  background: var(--c-neutral-800);
  color: var(--color-text-inverse);
}
.btn-diagnostic:hover { background: var(--color-bg-inverse); }
.btn-diagnostic:disabled { opacity: 0.5; cursor: not-allowed; }

.diagnostic-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  margin-top: 14px;
}
.diagnostic-group {
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 12px;
}
.group-label { display: block; font-size: 12px; color: var(--color-text-secondary); margin-bottom: 8px; }
.diagnostic-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--color-text-secondary);
}
.diagnostic-meta b { color: var(--color-text-primary); }
.account-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
}
.account-grid > div {
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.account-grid span { font-size: 12px; color: var(--color-text-secondary); }
.account-grid strong { font-size: 16px; color: var(--color-text-primary); }

.trades-list { display: flex; flex-direction: column; gap: 8px; }
.trade-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  padding: 8px 10px;
  border-bottom: 1px solid var(--color-border);
  font-size: 12px;
  color: var(--color-text-secondary);
}
.trade-row:last-child { border-bottom: none; }
.trade-date { color: var(--color-text-secondary); }
.trade-side { font-weight: 600; padding: 1px 6px; border-radius: var(--radius-sm); }
/* 买卖方向沿用同一套"方向"语义（红买绿卖），文字对浅底 4.97 / 4.98:1；
   方向另有文字标注，不靠颜色单独表意 */
.trade-side.BUY, .trade-side.buy { color: var(--color-gain); background: var(--color-bg-subtle); }
.trade-side.SELL, .trade-side.sell { color: var(--color-loss); background: var(--color-bg-subtle); }
.trade-reason { color: var(--color-text-muted); width: 100%; }

/* ---------- 结算痕迹 ---------- */
.trace-hint { font-size: 12px; color: var(--color-text-muted); margin: 4px 0 12px; line-height: 1.6; }
/* 痕迹列表：与交易记录同构但独立命名（2026-10-08 起痕迹区与交易区样式分家） */
.traces-list { display: flex; flex-direction: column; gap: 8px; }
.trace-row {
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--color-text-secondary);
}
.trace-head { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.trace-date { color: var(--color-text-secondary); }
.trace-decision { font-weight: 600; padding: 1px 6px; border-radius: var(--radius-sm); }
/* 沿用同一套方向语义：买=涨色、卖=跌色、"未成交"用中性色（它不是好事也不是坏事） */
.trace-decision.buy { color: var(--color-gain); background: var(--color-bg-subtle); }
.trace-decision.sell { color: var(--color-loss); background: var(--color-bg-subtle); }
.trace-decision.skip { color: var(--color-text-secondary); background: var(--color-bg-subtle); }
.trace-kind { color: var(--color-text-muted); }
.trace-summary { margin: 6px 0 4px; color: var(--color-text-primary); line-height: 1.6; }
.trace-meta { display: flex; flex-wrap: wrap; gap: 10px; color: var(--color-text-muted); }
.trace-evidence { margin-top: 6px; }
.trace-evidence summary { cursor: pointer; color: var(--color-text-muted); }
.trace-evidence pre {
  margin: 6px 0 0;
  padding: 8px;
  max-height: 260px;
  overflow: auto;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  font-size: 11px;
  line-height: 1.5;
}

/* ---------- 顶部运行状态条 ---------- */
/* 它是"这条策略此刻是什么状态"的唯一入口，所以放在第一屏；
   徽章用中性色 + 语义色各一个，状态另有文字，不靠颜色单独表意。 */
.run-strip { padding: 14px 20px; }
.run-badges { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.run-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-pill);
  background: var(--color-bg-subtle);
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border-strong);
}
.run-badge.on { color: var(--color-success); background: var(--color-success-soft); border-color: var(--color-success-mark); }
.run-badge.mode.agent { color: var(--color-accent); border-color: var(--color-accent); }
.run-note { font-size: 12px; color: var(--color-text-muted); }
.run-line { margin: 8px 0 0; font-size: 13px; color: var(--color-text-secondary); line-height: 1.7; }
.run-label { display: inline-block; min-width: 92px; color: var(--color-text-muted); font-size: 12px; }

/* ---------- 可验证预期 ---------- */
.notice { color: var(--color-success); font-size: 13px; margin-bottom: 12px; }
/* "还没有评估过"的说明块：用中性色，不用错误色 —— 它是正常状态，不是故障 */
.account-pending {
  margin: 0 0 12px;
  padding: 10px 12px;
  background: var(--color-bg-subtle);
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.7;
}
.expectation-head { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
/* 状态色沿用语义层：达成=成功、未达成=危险、"算不出来"=中性（它不是失败，是我们的数据不够），
   尚未到期=普通中性。四种状态都另有文字，不靠颜色单独表意。 */
.expectation-status {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-pill);
  background: var(--color-bg-subtle);
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border-strong);
}
.expectation-status.met { color: var(--color-success); background: var(--color-success-soft); border-color: var(--color-success-mark); }
.expectation-status.unmet { color: var(--color-danger); background: var(--color-danger-soft); border-color: var(--color-danger); }
.expectation-status.pending { color: var(--color-warning); background: var(--color-warning-soft); border-color: var(--color-warning-mark); }
.expectation-status.unmeasurable { border-style: dashed; }
.expectation-sentence { margin: 8px 0 6px; color: var(--color-text-primary); line-height: 1.6; font-size: 13px; }
/* 缺止损条件的提示：警示色系（该改配置的提醒），与 expectation-status.pending 同一套颜色组合 */
.stop-warning {
  margin: 12px 0 0;
  padding: 10px 12px;
  background: var(--color-warning-soft);
  border: 1px dashed var(--color-warning-mark);
  border-radius: var(--radius-sm);
  color: var(--color-warning);
  font-size: 13px;
  line-height: 1.7;
}
.expectation-empty {
  margin: 8px 0 12px;
  padding: 10px 12px;
  background: var(--color-bg-subtle);
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.6;
}
.expectation-form {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-end;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border);
}
.expectation-form label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--color-text-secondary); }
.expectation-form select,
.expectation-form input {
  padding: 6px 8px;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-sm);
  font-size: 13px;
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}
.expectation-form input { width: 120px; }
.btn-expectation {
  padding: 7px 14px;
  border: none;
  border-radius: var(--radius-sm);
  font-size: 13px;
  cursor: pointer;
  background: var(--color-accent);
  color: var(--color-text-on-accent);
}
.btn-expectation:disabled { opacity: 0.5; cursor: not-allowed; }

/* 旧灰字对灰底仅 2.54:1 */
.empty { color: var(--color-text-muted); text-align: center; padding: 40px 16px; font-size: 14px; }
.error { color: var(--color-danger); font-size: 13px; margin-bottom: 12px; }
.back-link {
  display: inline-block;
  margin-top: 10px;
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border: none;
  border-radius: var(--radius-sm);
  padding: 6px 12px;
  cursor: pointer;
}

/* 决策日志：一天一行的对答案表。命中/判错只用颜色+符号双通道（无障碍） */
.journal-list { display: flex; flex-direction: column; gap: 6px; margin-top: 10px; }
.journal-row {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 7px 10px; border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 8px); background: var(--color-bg-surface);
  font-size: 13px;
}
.journal-date { min-width: 86px; color: var(--color-text-secondary); }
.journal-decision { font-weight: 600; }
.journal-decision.buy { color: var(--color-gain); }
.journal-decision.sell { color: var(--color-loss); }
.journal-decision.skip { color: var(--color-text-muted); }
.journal-mode { font-size: 11px; color: var(--color-text-muted); border: 1px solid var(--color-border-strong); border-radius: var(--radius-pill); padding: 0 6px; }
.journal-verdict { margin-left: auto; font-size: 12px; }
.journal-verdict.hit { color: var(--color-accent); }
.journal-verdict.miss { color: var(--color-warning); }
.journal-verdict.pending { color: var(--color-text-muted); }
.journal-verdict.skip { color: var(--color-text-muted); }
.journal-change { font-size: 12px; }
</style>
