
<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
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
  registerExpectation
} from '../api/strategy.js'

const router = useRouter()
const route = useRoute()

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

const formatMoney = (value) => {
  if (value === null || value === undefined) return 'N/A'
  const n = Number(value)
  if (Number.isNaN(n)) return 'N/A'
  return n.toLocaleString('zh-CN', { style: 'currency', currency: 'USD' })
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
        areaStyle: { color: accentSoftColor }
      },
      {
        name: '买入持有',
        type: 'line',
        showSymbol: false,
        smooth: true,
        data: benchmarkCurve.map((point) => point.equity),
        lineStyle: { width: 1.5, type: 'dashed', color: mutedColor }
      }
    ]
  }, { notMerge: true })
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
  <div class="app-layout">
    <header>
      <h1>策略详情</h1>
      <div class="header-actions">
        <button type="button" class="nav-btn" @click="goBack">← 返回策略库</button>
      </div>
    </header>

    <main>
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
            </div>
          </div>

          <div class="meta num">
            <span>创建: {{ formatTime(strategy.createdAt) }}</span>
            <span>更新: {{ formatTime(strategy.updatedAt) }}</span>
            <span v-if="strategy.lastBacktestAt">最近回测: {{ formatTime(strategy.lastBacktestAt) }}</span>
          </div>

          <h3>策略配置</h3>
          <pre class="config-block">{{ prettyConfig }}</pre>
        </section>

        <!-- 决策来源：谁在做决定。放在配置之后、回测之前 ——
             因为它决定了**下面所有数字是谁做出来的**，读者得先知道这件事。 -->
        <section class="card">
          <h3>决策来源</h3>
          <p class="trace-hint">
            当前由 <strong>{{ decisionModeLabel(decisionMode) }}</strong> 做决定<template
              v-if="strategy.decisionModeSince"
            >，自 {{ strategy.decisionModeSince }} 起生效</template>。
            换来源之后，<strong>净值曲线从生效日起分成两段</strong>：规则那一段和委员会那一段
            不是同一个东西做出来的，不能当成一条连续的业绩读。
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
          <p class="trace-hint">
            委员会的代价写在明处：**有事才开会**（规则信号 / 波动骤增 / 太久没看 / 首次决策），
            开一次会 8 次模型调用、约 2 万 token；没有触发理由的日子不花一分钱，
            痕迹里会写"没有值得开会的理由"而不是"模型决定不动"。
          </p>
          <p class="trace-hint">
            另一件要知道的事：<strong>agent 模式下盘中不再做规则检查</strong>。
            换决策来源就是换决策者 —— 否则委员会当天说"不动"，盘中的策略信号照样能建仓，
            决定被悄悄覆盖。代价是账户的最新价与净值在盘中不再刷新，
            只在每日结算（15:30）后更新一次。
          </p>
        </section>

        <!-- 预期：把"接下来会怎样"变成到期能验的承诺。 -->
        <section class="card">
          <h3>可验证预期</h3>
          <p class="trace-hint">
            写清度量、门槛与到期日，到期后由每日结算用同一份净值算出实际值 ——
            <strong>达成 / 未达成 / 算不出来</strong>三者分开。都是账户层面可观测值，
            不含任何股价预测。同一时刻只有一条有效预期，登记新的即取代旧的。
          </p>
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
            还没有登记预期 —— 也就是说，**目前没有人对这条策略的未来下过承诺**。
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
              <h3>模型诊断</h3>
              <p>仅作低权重参考，不参与策略买卖决策</p>
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
            <p class="disclaimer">{{ diagnostic.disclaimer || '模型诊断仅作低权重参考，不构成投资建议。' }}</p>
          </template>
        </section>

        <section class="card">
          <h3>模拟盘账户</h3>
          <div v-if="account" class="account-grid num">
            <div><span>总权益</span><strong>{{ formatMoney(account.equity) }}</strong></div>
            <div><span>现金</span><strong>{{ formatMoney(account.cash) }}</strong></div>
            <div><span>持仓市值</span><strong>{{ formatMoney(account.shares * account.avgCost) }}</strong></div>
            <div><span>初始资金</span><strong>{{ formatMoney(account.initialCapital) }}</strong></div>
            <div><span>持仓数量</span><strong>{{ account.shares }}</strong></div>
            <div><span>平均成本</span><strong>{{ formatMoney(account.avgCost) }}</strong></div>
            <div><span>最高水位</span><strong>{{ formatMoney(account.highWatermark) }}</strong></div>
            <div><span>最新价格</span><strong>{{ formatMoney(account.lastPrice) }}</strong></div>
            <div>
              <span>最新信号</span>
              <strong>
                {{ account.lastSignal === 'buy' ? '买入' : account.lastSignal === 'sell' ? '卖出' : account.lastSignal === 'hold' ? '持有' : (account.lastSignal || 'N/A') }}
              </strong>
            </div>
            <div><span>最近评估</span><strong>{{ formatTime(account.lastEvalAt) }}</strong></div>
          </div>
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
          <h3>净值曲线与机会成本</h3>
          <p class="trace-hint">
            空仓在账面上是 0，看起来没有代价。<strong>把同期的买入持有摆在旁边，代价就显形了</strong>：
            负超额正是那些"什么都没做"的日子花掉的钱。汇总由后端同一份算法给出，此处只负责显示。
          </p>
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
            <!-- 起点必须写出来：曲线从哪天开始，决定它能不能被当成"一直如此" -->
            <p class="trace-hint">
              曲线自 {{ equity.summary.firstDate }} 起，共 {{ equity.summary.days }} 个交易日
              （中间没结算的日子是缺口，不补）。
              <template v-if="strategy.decisionModeSince">
                虚线处（{{ strategy.decisionModeSince }}）换了决策来源：竖线两侧的曲线
                不是同一个决策者做出来的。
              </template>
            </p>
            <div ref="equityChartRef" class="equity-chart"></div>
          </template>
        </section>

        <section class="card">
          <h3>结算痕迹</h3>
          <p class="trace-hint">
            每一行 = 一根 K 线上的一个结论，包括"什么都没做"和"为什么"。账户数字不动时，这里能看出
            是<strong>没信号</strong>、<strong>指标还没算出来</strong>，还是<strong>信号来了但被挡住</strong>。
          </p>
          <p v-if="tracesError" class="error" role="alert">{{ tracesError }}</p>
          <div v-if="traces.length === 0 && !tracesError" class="empty">
            暂无结算痕迹（模拟盘开始结算后，每个交易日会留下一行）
          </div>
          <div v-else class="trades-list num">
            <div v-for="trace in traces" :key="trace.id" class="trace-row">
              <div class="trace-head">
                <span class="trace-date">{{ formatTime(trace.barTime || trace.tradeDate) }}</span>
                <span class="trace-decision" :class="trace.decision">
                  {{ trace.decision === 'buy' ? '买入' : trace.decision === 'sell' ? '卖出' : '未成交' }}
                </span>
                <span class="trace-kind">{{ trace.settlementKind === 'daily' ? '日线结算' : '盘中评估' }}</span>
                <span v-if="trace.repeatCount > 1" class="trace-kind">同一结论 {{ trace.repeatCount }} 次</span>
              </div>
              <!-- 一句人话（后端确定性拼出来，不由模型生成）：这是"不看代码也能明白"的那一层 -->
              <p class="trace-summary">{{ trace.summary }}</p>
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
                <span v-if="trace.trigger && trace.trigger !== 'scheduled'">触发 {{ trace.trigger }}</span>
              </div>
              <details v-if="trace.snapshotJson" class="trace-evidence">
                <summary>当时的证据快照</summary>
                <pre>{{ prettySnapshot(trace.snapshotJson) }}</pre>
              </details>
            </div>
          </div>
        </section>
      </template>
    </main>
  </div>
</template>

<style scoped>
.app-layout {
  /* 移动浏览器地址栏会算进 100vh，底部会被顶出可视区；补 100dvh 兜底 */
  min-height: 100vh;
  min-height: 100dvh;
  background: var(--color-bg-page);
}
header {
  background: var(--color-bg-inverse);
  color: var(--color-text-inverse);
  padding: 16px 24px;
  /* iOS 独立模式（index.html 声明了 black-translucent）内容会顶到状态栏下，补顶部安全区 */
  padding-top: calc(16px + env(safe-area-inset-top, 0px));
  display: flex;
  justify-content: space-between;
  align-items: center;
}
header h1 { margin: 0; font-size: 20px; }
.header-actions { display: flex; gap: 8px; }
.nav-btn {
  /* 深色导航上的半透明胶囊：语义层没有"深底上的浮起表面"这个角色，
     用 color-mix 从 --color-text-inverse 派生，避免写死半透明白色。
     不支持 color-mix 时该声明失效、背景回落为透明，白字直接压在深色导航上
     仍是 17.06:1，不会出现读不清的文字。 */
  background: color-mix(in srgb, var(--color-text-inverse) 15%, transparent);
  border: none;
  color: var(--color-text-inverse);
  padding: 6px 14px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
}
.nav-btn:hover { background: color-mix(in srgb, var(--color-text-inverse) 25%, transparent); }

main { max-width: 820px; margin: 0 auto; padding: 24px 16px; }
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
.diagnostic-head p { margin: 4px 0 0; color: var(--color-text-muted); font-size: 12px; }
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
.disclaimer {
  margin: 12px 0 0;
  color: var(--color-text-muted);
  font-size: 12px;
}

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

/* ---------- 可验证预期 ---------- */
.notice { color: var(--color-success); font-size: 13px; margin-bottom: 12px; }
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
</style>
