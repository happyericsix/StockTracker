<script setup>
/**
 * 策略详情：运行状态 / 配置 / 决策来源 / 可验证预期 / 回测 / 模型诊断 /
 * 模拟盘账户 / 交易 / 决策日志 / 净值曲线 / 结算痕迹。
 * 逻辑自旧版原样移植（零漂移）：口径纪律、图表令牌同源、快照不美化。
 */
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ArrowLeft, AlertTriangle } from 'lucide-vue-next'
import InfoTip from '../components/InfoTip.vue'
import StrategyEditSheet from '../components/StrategyEditSheet.vue'
import Badge from '../components/ui/badge.vue'
import Button from '../components/ui/button.vue'
import Empty from '../components/ui/empty.vue'
import Skeleton from '../components/ui/skeleton.vue'
import * as echarts from 'echarts'
import {
  listStrategies, runBacktest, getStrategyDiagnostic, startPaper, stopPaper,
  getPaperAccount, getPaperTrades, getPaperTraces, getPaperEquity,
  switchDecisionMode, getExpectation, registerExpectation, getPaperOverview,
  getStrategyJournal, enableShare, disableShare,
} from '../api/strategy.js'
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
const modeLoading = ref(false)
const modeError = ref('')
const expectation = ref(null)
const expectationError = ref('')
const expectationLoading = ref(false)
const expectationForm = ref({ metric: 'excess_vs_buy_and_hold_pct', threshold: 0, horizonDays: 28 })
const expectationNotice = ref('')
const equityChartRef = ref(null)
let equityChart = null
const overviewItem = ref(null)

const errorMessage = (e, fallback) => e?.response?.data?.message || fallback

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
}

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

/** 只画后端给的点，并在决策来源变更日画一条竖线（两段不是同一个决策者） */
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
  const since = strategy.value?.decisionModeSince ? String(strategy.value.decisionModeSince) : ''
  const markLine = since && dates.includes(since)
    ? {
        symbol: 'none',
        lineStyle: { color: mutedColor, type: 'dashed' },
        label: { formatter: '切换决策来源', color: mutedColor },
        data: [{ xAxis: since }],
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
        itemStyle: { color: accentColor },
        areaStyle: { color: accentSoftColor },
        markLine,
      },
    ],
  }, { notMerge: true })
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

/** 切换决策来源：会改变历史解释依据，走显式接口、后端记录生效日 */
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

      <!-- 决策来源 -->
      <section class="mb-4 rounded-xl border border-border bg-surface p-5">
        <h3 class="mb-2 flex items-center gap-1.5 text-[15px] font-semibold text-ink">
          决策来源
          <InfoTip
            label="决策来源说明"
            text="换来源后，净值曲线从生效日分成两段：两段不是同一个决策者做出来的，不能当一条连续的业绩读。委员会有事才开会（规则信号、波动骤增、太久没看、首次决策），开一次约 8 次模型调用、2 万 token；没事的日子不花钱。agent 模式的开仓与一般平仓只在 15:30 结算时决策；止损类卖出是硬风控，盘中直接执行，不受授权限制。"
          />
        </h3>
        <p class="text-[13px] leading-relaxed text-ink-2">
          当前由 <strong class="text-ink">{{ decisionModeLabel(decisionMode) }}</strong> 做决定<template v-if="strategy.decisionModeSince">，自 {{ strategy.decisionModeSince }} 起生效</template>。
        </p>
        <p v-if="modeError" class="mt-2 text-[12.5px] text-up" role="alert">{{ modeError }}</p>
        <div class="mt-3 flex flex-wrap gap-2">
          <Button size="sm" :variant="!isAgentMode ? 'primary' : 'secondary'" :disabled="modeLoading || !isAgentMode" :loading="modeLoading && !isAgentMode" @click="changeDecisionMode('rule')">
            {{ isAgentMode ? '切回规则引擎' : '当前：规则引擎' }}
          </Button>
          <Button size="sm" :variant="isAgentMode ? 'primary' : 'secondary'" :disabled="modeLoading || isAgentMode" :loading="modeLoading && isAgentMode" @click="changeDecisionMode('agent')">
            {{ isAgentMode ? '当前：多角色委员会' : '切换为多角色委员会' }}
          </Button>
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
              <strong class="text-[16px] font-semibold text-down">{{ formatPct(equity.summary.maxDrawdownPct) }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">同期买入持有</span>
              <strong class="text-[16px] font-semibold" :class="dirTextClass(equity.summary.buyAndHoldPct)">{{ formatPct(equity.summary.buyAndHoldPct) }}</strong>
            </div>
            <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
              <span class="text-[12px] text-ink-2">超额（不动的代价）</span>
              <strong class="text-[16px] font-semibold" :class="dirTextClass(equity.summary.excessVsBuyAndHoldPct)">{{ formatPct(equity.summary.excessVsBuyAndHoldPct) }}</strong>
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
          <p class="mb-2.5 text-[12px] leading-relaxed text-ink-3">
            自 {{ equity.summary.firstDate }} 起，共 {{ equity.summary.days }} 个交易日<template v-if="strategy.decisionModeSince">；{{ strategy.decisionModeSince }} 起换了决策来源（图中虚线），两侧不是同一个决策者</template>。
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
