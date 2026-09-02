
<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import {
  listStrategies,
  runBacktest,
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

const backtestText = computed(() => {
  if (backtestData.value === null || backtestData.value === undefined) return ''
  if (typeof backtestData.value === 'string') {
    try {
      return JSON.stringify(JSON.parse(backtestData.value), null, 2)
    } catch (e) {
      return backtestData.value
    }
  }
  return JSON.stringify(backtestData.value, null, 2)
})

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
          <pre v-else class="config-block">{{ backtestText }}</pre>
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
