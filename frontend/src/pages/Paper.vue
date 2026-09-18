<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getPaperOverview, startPaper, stopPaper } from '../api/strategy.js'

/**
 * 模拟盘总览：**这个功能的主入口**。
 *
 * <h3>为什么要有这一页</h3>
 * 原来的模拟盘藏在「首页 → 策略库 → 某条策略 → 滚到最下面」三层之后，
 * 全站只有两行副标题提到过"模拟盘"四个字。实测反馈就是那句
 * "我不问 AI 都不知道有这个功能"。这一页存在的理由只有一个：
 * 用户打开它就能回答四个问题 —— **在跑什么 / 赚没赚 / 今天动没动 / 下次什么时候**。
 *
 * <p>数字与措辞全部来自后端同一份口径（`GET /paper/overview`）：汇总走
 * PaperEquitySeries，"下次评估时间"走 PaperSchedule，"为什么没动"走痕迹的人话映射。
 * 前端只负责显示，不自己算一遍 —— 否则总览页与详情页迟早对不上。
 */
const router = useRouter()

const items = ref([])
const loading = ref(false)
const error = ref('')
const actionId = ref(null)

const errorMessage = (e, fallback) => e?.response?.data?.message || fallback

const load = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await getPaperOverview()
    const data = res.data
    items.value = Array.isArray(data) ? data : []
  } catch (e) {
    items.value = []
    error.value = errorMessage(e, '模拟盘状态加载失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

const running = computed(() => items.value.filter((item) => item.paperEnabled))
const settledToday = computed(() =>
  running.value.filter((item) => item.lastSettlement && item.lastSettlement.tradeDate === today())
)
/** 所有运行中的策略共用的下一次评估时间（同一天，取第一条即可；没有运行中的就是空）。 */
const nextEvaluationNote = computed(() => running.value[0]?.nextEvaluationNote || '')

const today = () => new Date().toLocaleDateString('sv-SE') // sv-SE 给的是 YYYY-MM-DD

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
  return n.toLocaleString('zh-CN', { style: 'currency', currency: 'CNY' })
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

const modeLabel = (mode) => (mode === 'agent' ? '多角色委员会' : '规则引擎')

/**
 * 这一行是日线结算还是盘中评估。
 *
 * <p>痕迹那句人话**本身就以"日期 时间（标的）"开头**，所以这里只补口径、不再重复日期。
 */
const settlementKindLabel = (last) => {
  if (!last) return ''
  if (last.settlementKind === 'daily') return '日线结算'
  return last.trigger === 'manual' ? '盘中评估（手动触发）' : '盘中评估'
}

const signalLabel = (signal) => {
  const s = String(signal ?? '').toLowerCase()
  if (s === 'buy') return '买入'
  if (s === 'sell') return '卖出'
  if (s === 'hold') return '持有'
  return signal || 'N/A'
}

const togglePaper = async (item) => {
  actionId.value = item.strategyId
  error.value = ''
  try {
    if (item.paperEnabled) {
      await stopPaper(item.strategyId)
    } else {
      await startPaper(item.strategyId)
    }
    await load()
  } catch (e) {
    error.value = errorMessage(e, '模拟盘操作失败，请稍后重试')
  } finally {
    actionId.value = null
  }
}

const goDetail = (id) => router.push(`/strategies/${id}`)
const goStrategies = () => router.push('/strategies')
const goDashboard = () => router.push('/dashboard')

onMounted(load)
</script>

<template>
  <div class="app-layout">
    <header>
      <h1>模拟盘</h1>
      <div class="header-actions">
        <button type="button" class="nav-btn" @click="goStrategies">策略库</button>
        <button type="button" class="nav-btn" @click="goDashboard">← 返回</button>
      </div>
    </header>

    <main>
      <p class="page-hint">
        每个交易日 <strong>15:30</strong> 自动结算一次；切到多角色委员会的策略只在日线结算时决策。
        下面每条策略都直接回答四件事：<strong>在跑什么、赚没赚、今天动没动、下次什么时候</strong>。
      </p>

      <p v-if="error" class="error" role="alert">{{ error }}</p>

      <!-- 顶部状态条：让"它还在跑"这件事有确定的时间点 -->
      <section v-if="items.length" class="card status-strip num">
        <div><span>运行中</span><strong>{{ running.length }} / {{ items.length }} 条</strong></div>
        <div><span>今日已结算</span><strong>{{ settledToday.length }} / {{ running.length }} 条</strong></div>
        <div class="wide">
          <span>下一次评估</span>
          <strong>{{ nextEvaluationNote || '暂无运行中的模拟盘' }}</strong>
        </div>
      </section>

      <div v-if="loading" class="empty">加载中...</div>

      <section v-else-if="!items.length && !error" class="card empty-card">
        <p>你还没有任何策略，所以也没有模拟盘在跑。</p>
        <button type="button" class="primary-btn" @click="goStrategies">去策略库创建一条</button>
      </section>

      <section v-for="item in items" :key="item.strategyId" class="card strategy-card">
        <div class="card-head">
          <div class="titles">
            <h2>{{ item.name }}</h2>
            <span class="symbol-code">{{ item.symbol }}</span>
            <span class="badge" :class="{ on: item.paperEnabled }">
              {{ item.paperEnabled ? '模拟盘运行中' : '未启动模拟盘' }}
            </span>
            <span class="badge mode" :class="{ agent: item.decisionMode === 'agent' }">
              {{ modeLabel(item.decisionMode) }}
            </span>
          </div>
          <div class="card-actions">
            <button
              type="button"
              class="ghost-btn"
              :disabled="actionId === item.strategyId"
              @click="togglePaper(item)"
            >
              {{ item.paperEnabled ? '停止模拟盘' : '启动模拟盘' }}
            </button>
            <button type="button" class="ghost-btn" @click="goDetail(item.strategyId)">看详情</button>
          </div>
        </div>

        <!-- 还没被评估过：先把话说清楚，再谈数字（一片 N/A 会被读成"坏了"） -->
        <div v-if="!item.evaluated" class="pending-note">
          <strong>还没有被评估过一次</strong> ——
          <template v-if="item.decisionMode === 'agent'">
            委员会只在日线结算时决策，盘中不做规则检查，所以第一次决策会在下一个交易日 15:30 产生。
          </template>
          <template v-else>
            下一次盘中检查会在几分钟内发生；日线结算在下一个交易日 15:30。
          </template>
          初始资金 {{ formatMoney(item.initialCapital) }}。
        </div>

        <template v-else>
          <div class="metric-grid num">
            <div class="metric">
              <span>最新净值</span>
              <strong>{{ formatMoney(item.equity) }}</strong>
            </div>
            <div class="metric">
              <span>期间收益</span>
              <strong :class="pnlClass(item.summary?.returnPct)">
                {{ item.summary?.returnPct === null || item.summary?.returnPct === undefined ? 'N/A' : formatPct(item.summary.returnPct) }}
              </strong>
            </div>
            <div class="metric">
              <span>超额（vs 买入持有）</span>
              <strong :class="pnlClass(item.summary?.excessVsBuyAndHoldPct)">
                {{ item.summary?.excessVsBuyAndHoldPct === null || item.summary?.excessVsBuyAndHoldPct === undefined ? 'N/A' : formatPct(item.summary.excessVsBuyAndHoldPct) }}
              </strong>
            </div>
            <div class="metric">
              <span>最大回撤</span>
              <strong class="negative">
                {{ item.summary?.maxDrawdownPct === null || item.summary?.maxDrawdownPct === undefined ? 'N/A' : formatPct(item.summary.maxDrawdownPct) }}
              </strong>
            </div>
            <div class="metric">
              <span>空仓占比</span>
              <strong>
                {{ item.summary?.flatRatioPct === null || item.summary?.flatRatioPct === undefined ? 'N/A' : item.summary.flatRatioPct + '%' }}
              </strong>
            </div>
            <div class="metric">
              <span>样本</span>
              <strong>{{ item.summary?.days ?? 0 }} 天</strong>
            </div>
          </div>
          <!-- 还没有净值点时把原因写出来：一串 N/A 会被读成"坏了"，
               而真实原因是"这条曲线要等第一次日线结算才有第一个点" -->
          <p v-if="!item.summary" class="no-history">
            净值曲线还没有数据点：它由**日线结算**（每个交易日 15:30）记账，每天一格。
            盘中成交不会立刻产生净值点。
          </p>

          <div class="lines">
            <p>
              <span class="line-label">持仓</span>
              {{ item.shares }} 股 · 现金 {{ formatMoney(item.cash) }} · 持仓市值
              {{ formatMoney(item.positionValue) }} · 最新信号 {{ signalLabel(item.lastSignal) }}
            </p>
            <p v-if="item.lastSettlement">
              <span class="line-label">最近一次结算</span>
              {{ item.lastSettlement.sentence }}（{{ settlementKindLabel(item.lastSettlement) }}）
              <template v-if="item.lastSettlement.decisionMode === 'agent' && item.lastSettlement.agentLlmCalls">
                ｜委员会 {{ item.lastSettlement.agentLlmCalls }} 次调用 /
                {{ item.lastSettlement.agentTokens ?? 0 }} token
              </template>
            </p>
            <p v-else>
              <span class="line-label">最近一次结算</span>还没有结算记录（痕迹共 {{ item.traceCount }} 条）
            </p>
            <p>
              <span class="line-label">预期</span>
              {{ item.expectation ? item.expectation.sentence : '还没有登记预期 —— 目前没有人对这条策略的未来下过承诺' }}
            </p>
            <p>
              <span class="line-label">下次</span>
              {{ item.nextEvaluationNote }}
            </p>
            <p>
              <span class="line-label">评估过</span>{{ formatTime(item.lastEvalAt) }}
              ｜初始资金 {{ formatMoney(item.initialCapital) }}
            </p>
          </div>
        </template>
      </section>
    </main>
  </div>
</template>

<style scoped>
.app-layout {
  min-height: 100vh;
  min-height: 100dvh;
  background: var(--color-bg-page);
}
header {
  background: var(--color-bg-inverse);
  color: var(--color-text-inverse);
  padding: 16px 24px;
  padding-top: calc(16px + env(safe-area-inset-top, 0px));
  display: flex;
  justify-content: space-between;
  align-items: center;
}
header h1 { margin: 0; font-size: 20px; }
.header-actions { display: flex; gap: 8px; }
.nav-btn {
  background: color-mix(in srgb, var(--color-text-inverse) 15%, transparent);
  border: none;
  color: var(--color-text-inverse);
  padding: 6px 14px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
}
.nav-btn:hover { background: color-mix(in srgb, var(--color-text-inverse) 25%, transparent); }

main { max-width: 900px; margin: 0 auto; padding: 24px 16px 48px; }
.page-hint { font-size: 13px; color: var(--color-text-secondary); line-height: 1.7; margin: 0 0 16px; }
.card {
  background: var(--color-bg-surface);
  border-radius: var(--radius-lg);
  padding: 18px 20px;
  margin-bottom: 16px;
  box-shadow: var(--shadow-1);
}
.status-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
}
.status-strip > div { display: flex; flex-direction: column; gap: 4px; }
.status-strip > div.wide { grid-column: 1 / -1; }
.status-strip span { font-size: 12px; color: var(--color-text-secondary); }
.status-strip strong { font-size: 15px; color: var(--color-text-primary); font-weight: 600; }

.card-head { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; align-items: flex-start; }
.titles { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.titles h2 { margin: 0; font-size: 17px; }
.symbol-code { font-size: 13px; color: var(--color-text-muted); }
.badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-pill);
  background: var(--color-bg-subtle);
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border-strong);
}
.badge.on { color: var(--color-success); background: var(--color-success-soft); border-color: var(--color-success-mark); }
.badge.mode.agent { color: var(--color-accent); border-color: var(--color-accent); }
.card-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.ghost-btn {
  padding: 6px 12px;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-sm);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font-size: 13px;
  cursor: pointer;
}
.ghost-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 10px;
  margin: 14px 0 12px;
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
.metric strong { font-size: 17px; color: var(--color-text-primary); }
.metric strong.positive { color: var(--color-gain); }
.metric strong.negative { color: var(--color-loss); }

.lines { display: flex; flex-direction: column; gap: 6px; }
.lines p { margin: 0; font-size: 13px; color: var(--color-text-secondary); line-height: 1.7; }
.line-label {
  display: inline-block;
  min-width: 96px;
  color: var(--color-text-muted);
  font-size: 12px;
}
.pending-note {
  margin-top: 12px;
  padding: 10px 12px;
  background: var(--color-bg-subtle);
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.7;
}
/* 没有净值点时的一句解释（不是错误，所以不用错误色） */
.no-history {
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--color-text-muted);
  line-height: 1.7;
}
.empty-card { text-align: center; }
.primary-btn {
  margin-top: 8px;
  padding: 8px 16px;
  border: none;
  border-radius: var(--radius-sm);
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  font-size: 13px;
  cursor: pointer;
}
.empty { color: var(--color-text-muted); text-align: center; padding: 40px 16px; font-size: 14px; }
.error { color: var(--color-danger); font-size: 13px; margin-bottom: 12px; }
</style>
