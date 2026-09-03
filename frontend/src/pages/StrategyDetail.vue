
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
  getPaperTrades
} from '../api/strategy.js'

const router = useRouter()
const route = useRoute()

const strategy = ref(null)
const account = ref(null)
const trades = ref([])
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

const unwrap = (res) => {
  const payload = res?.data
  if (payload && typeof payload === 'object' && 'data' in payload) return payload.data
  return payload
}

const errorMessage = (e, fallback) =>
  e?.response?.data?.message || e?.message || fallback

const formatTime = (iso) => {
  if (!iso) return 'N/A'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return 'N/A'
  return d.toLocaleString('zh-CN', { hour12: false })
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
      return { error: '回测结果解析失败' }
    }
  }
  if (backtestData.value.valid === false) return { error: backtestData.value.error || '策略校验失败' }
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
        lineStyle: { width: 2, color: '#1677ff' },
        areaStyle: { color: 'rgba(22,119,255,0.08)' }
      },
      {
        name: '买入持有',
        type: 'line',
        showSymbol: false,
        smooth: true,
        data: benchmarkCurve.map((point) => point.equity),
        lineStyle: { width: 1.5, type: 'dashed', color: '#999' }
      }
    ]
  }, { notMerge: true })
}

const loadPaper = async () => {
  try {
    const res = await getPaperAccount(strategy.value.id)
    account.value = unwrap(res)
  } catch (e) {
    account.value = null
  }
  try {
    const res = await getPaperTrades(strategy.value.id)
    const data = unwrap(res)
    trades.value = Array.isArray(data) ? data : []
  } catch (e) {
    trades.value = []
  }
}

const loadDetail = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await listStrategies()
    const data = unwrap(res)
    const list = Array.isArray(data) ? data : []
    const found = list.find((item) => String(item.id) === String(route.params.id))
    if (found) {
      strategy.value = found
      await loadPaper()
    } else {
      notFound.value = true
    }
  } catch (e) {
    error.value = errorMessage(e, '策略详情加载失败')
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
    backtestData.value = unwrap(res)
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
    diagnosticData.value = unwrap(res)
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
})
</script>

<template>
  <div class="app-layout">
    <header>
      <h1>策略详情</h1>
      <div class="header-actions">
        <button class="nav-btn" @click="goBack">← 返回策略库</button>
      </div>
    </header>

    <main>
      <p v-if="error" class="error">{{ error }}</p>

      <div v-if="loading" class="empty">加载中...</div>

      <div v-else-if="notFound" class="empty">
        未找到该策略
        <button class="back-link" @click="goBack">← 返回策略库</button>
      </div>

      <template v-else-if="strategy">
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
              <button class="btn-backtest" :disabled="backtestLoading" @click="handleBacktest">
                {{ backtestLoading ? '运行中...' : '运行回测' }}
              </button>
              <button
                class="btn-paper"
                :class="{ running: strategy.paperEnabled }"
                :disabled="paperLoading"
                @click="togglePaper"
              >
                {{ strategy.paperEnabled ? '停止模拟盘' : '启动模拟盘' }}
              </button>
            </div>
          </div>

          <div class="meta">
            <span>创建: {{ formatTime(strategy.createdAt) }}</span>
            <span>更新: {{ formatTime(strategy.updatedAt) }}</span>
            <span v-if="strategy.lastBacktestAt">最近回测: {{ formatTime(strategy.lastBacktestAt) }}</span>
          </div>

          <h3>策略配置</h3>
          <pre class="config-block">{{ prettyConfig }}</pre>
        </section>

        <section v-if="backtestLoading || backtestData !== null" class="card">
          <h3>回测结果</h3>
          <div v-if="backtestLoading" class="empty">回测运行中...</div>
          <div v-else-if="backtestResult && backtestResult.error" class="empty">{{ backtestResult.error }}</div>
          <template v-else-if="backtestResult">
            <div class="metric-grid">
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
              <div v-for="(trade, index) in backtestResult.trade_log" :key="index" class="trade-row">
                <span class="trade-date">{{ trade.date }}</span>
                <span class="trade-side" :class="trade.side">{{ trade.side === 'buy' ? '买入' : '卖出' }}</span>
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
            <button class="btn-diagnostic" :disabled="diagnosticLoading" @click="loadDiagnostic">
              {{ diagnosticLoading ? '加载中...' : diagnosticData ? '重新加载' : '加载模型诊断' }}
            </button>
          </div>

          <p v-if="diagnosticError" class="error">{{ diagnosticError }}</p>

          <template v-if="diagnostic">
            <div class="diagnostic-grid">
              <div class="diagnostic-group">
                <span class="group-label">风险指标</span>
                <div v-if="diagnostic.risk && !diagnostic.risk.error" class="diagnostic-meta">
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
                <div v-if="diagnostic.model_status" class="diagnostic-meta">
                  <span>可用: {{ diagnostic.model_status.available ? '是' : '否' }}</span>
                  <span>缓存命中: {{ diagnostic.model_status.cache_hit ? '是' : '否' }}</span>
                  <span>磁盘模型: {{ diagnostic.model_status.disk_model_available ? '有' : '无' }}</span>
                  <span>参与决策: {{ diagnostic.model_status.decision_use ? '是' : '否' }}</span>
                </div>
                <div v-else class="empty">模型状态不可用</div>
              </div>

              <div class="diagnostic-group">
                <span class="group-label">模型共识</span>
                <div v-if="diagnostic.model_consensus" class="diagnostic-meta">
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
          <div v-if="account" class="account-grid">
            <div><span>总权益</span><strong>{{ formatMoney(account.equity) }}</strong></div>
            <div><span>现金</span><strong>{{ formatMoney(account.cash) }}</strong></div>
            <div><span>持仓市值</span><strong>{{ formatMoney(account.shares * account.avgCost) }}</strong></div>
            <div><span>初始资金</span><strong>{{ formatMoney(account.initialCapital) }}</strong></div>
            <div><span>持仓数量</span><strong>{{ account.shares }}</strong></div>
            <div><span>平均成本</span><strong>{{ formatMoney(account.avgCost) }}</strong></div>
            <div><span>最高水位</span><strong>{{ formatMoney(account.highWatermark) }}</strong></div>
          </div>
          <div v-else class="empty">暂无模拟盘账户</div>
        </section>

        <section class="card">
          <h3>模拟盘交易记录</h3>
          <div v-if="trades.length === 0" class="empty">暂无交易记录</div>
          <div v-else class="trades-list">
            <div v-for="(trade, index) in trades" :key="index" class="trade-row">
              <span class="trade-date">{{ formatTime(trade.tradeDate) }}</span>
              <span class="trade-side" :class="trade.side">{{ trade.side }}</span>
              <span>{{ trade.symbol }}</span>
              <span>价格: {{ trade.price }}</span>
              <span>数量: {{ trade.shares }}</span>
              <span>金额: {{ formatMoney(trade.amount) }}</span>
              <span class="trade-reason" v-if="trade.reason">{{ trade.reason }}</span>
            </div>
          </div>
        </section>
      </template>
    </main>
  </div>
</template>

<style scoped>
.app-layout { min-height: 100vh; background: #f0f2f5; }
header {
  background: #1a1a2e;
  color: white;
  padding: 16px 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
header h1 { margin: 0; font-size: 20px; }
.header-actions { display: flex; gap: 8px; }
.nav-btn {
  background: rgba(255,255,255,0.15);
  border: none;
  color: white;
  padding: 6px 14px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
}
.nav-btn:hover { background: rgba(255,255,255,0.25); }

main { max-width: 820px; margin: 0 auto; padding: 24px 16px; }
.card {
  background: white;
  border-radius: 8px;
  padding: 18px 20px;
  margin-bottom: 16px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.08);
}
.detail-title { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.detail-title h2 { margin: 0 0 6px; font-size: 20px; }
.symbol-code { font-size: 13px; color: #999; }
.paper-badge {
  display: inline-block;
  margin-left: 8px;
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 10px;
  background: #fff1f0;
  color: #ff4d4f;
  border: 1px solid #ffa39e;
}
.paper-badge.enabled {
  background: #f6ffed;
  color: #52c41a;
  border-color: #b7eb8f;
}
.detail-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.detail-actions button {
  padding: 7px 14px;
  border: none;
  border-radius: 4px;
  font-size: 13px;
  cursor: pointer;
  color: white;
}
.detail-actions button:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-backtest { background: #722ed1; }
.btn-paper { background: #fa8c16; }
.btn-paper.running { background: #52c41a; }

.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  color: #888;
  font-size: 12px;
  margin: 12px 0 16px;
}
.card h3 { margin: 0 0 12px; font-size: 16px; }

.config-block {
  margin: 0;
  background: #1f1f2e;
  color: #e6e6f0;
  border-radius: 6px;
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
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.metric span { font-size: 12px; color: #888; }
.metric strong { font-size: 18px; color: #333; }
.metric strong.positive { color: #cf1322; }
.metric strong.negative { color: #389e0d; }

.equity-chart {
  width: 100%;
  height: 320px;
  margin: 4px 0 18px;
}

.backtest-trades h4 { margin: 4px 0 10px; font-size: 14px; }

.diagnostic-card { border-left: 3px solid #d9d9d9; }
.diagnostic-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
  flex-wrap: wrap;
}
.diagnostic-head h3 { margin: 0; }
.diagnostic-head p { margin: 4px 0 0; color: #999; font-size: 12px; }
.btn-diagnostic {
  padding: 7px 14px;
  border: none;
  border-radius: 4px;
  font-size: 13px;
  cursor: pointer;
  color: white;
  background: #8c8c8c;
}
.btn-diagnostic:hover { background: #595959; }
.btn-diagnostic:disabled { opacity: 0.5; cursor: not-allowed; }

.diagnostic-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  margin-top: 14px;
}
.diagnostic-group {
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  padding: 12px;
}
.group-label { display: block; font-size: 12px; color: #888; margin-bottom: 8px; }
.diagnostic-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: #555;
}
.diagnostic-meta b { color: #333; }
.disclaimer {
  margin: 12px 0 0;
  color: #999;
  font-size: 12px;
}

.account-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
}
.account-grid > div {
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.account-grid span { font-size: 12px; color: #888; }
.account-grid strong { font-size: 16px; color: #333; }

.trades-list { display: flex; flex-direction: column; gap: 8px; }
.trade-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  padding: 8px 10px;
  border-bottom: 1px solid #f0f0f0;
  font-size: 12px;
  color: #555;
}
.trade-row:last-child { border-bottom: none; }
.trade-date { color: #888; }
.trade-side { font-weight: 600; padding: 1px 6px; border-radius: 4px; }
.trade-side.BUY, .trade-side.buy { color: #cf1322; background: #fff1f0; }
.trade-side.SELL, .trade-side.sell { color: #389e0d; background: #f6ffed; }
.trade-reason { color: #999; width: 100%; }

.empty { color: #999; text-align: center; padding: 40px 16px; font-size: 14px; }
.error { color: #ff4d4f; font-size: 13px; margin-bottom: 12px; }
.back-link {
  display: inline-block;
  margin-top: 10px;
  background: #1677ff;
  color: white;
  border: none;
  border-radius: 4px;
  padding: 6px 12px;
  cursor: pointer;
}
</style>
