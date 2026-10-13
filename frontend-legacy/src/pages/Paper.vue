<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getPaperOverview, startPaper, stopPaper } from '../api/strategy.js'
import { getPortfolio, getDecisionQuality, getPaperReport } from '../api/paper.js'
import StrategyEditSheet from '../components/StrategyEditSheet.vue'

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
// 每策略的"最近报告 + 哨兵观察"（2026-10-10 起日报/哨兵不再推消息，页面实时看）
const reports = ref([])
const reportOf = (strategyId) =>
  reports.value.find((r) => r.strategyId === strategyId) || null
// 策略编辑面板：选定策略 → 表单化调整规则（保存自动走 DSL 校验）
const editingId = ref(null)
const openEdit = (strategyId) => { editingId.value = strategyId }
const onEditSaved = () => load()
/** 规则口径徽标：configUpdatedAt 距今 7 天内提示"前后不可比"（与决策来源分界同一纪律）。 */
const configUpdatedNote = (item) => {
  if (!item.configUpdatedAt) return null
  const days = Math.floor((Date.now() - new Date(item.configUpdatedAt).getTime()) / 86400000)
  return days <= 7 ? `规则 ${days < 1 ? '今天' : days + ' 天前'}调整过，之前的净值口径不同` : null
}

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
  // 报告是旁路：失败不挡页面（哨兵观察列表同样在里面）
  try {
    const res = await getPaperReport()
    reports.value = Array.isArray(res.data) ? res.data : []
  } catch (e) {
    reports.value = []
  }
}

const running = computed(() => items.value.filter((item) => item.paperEnabled))
/**
 * 今日已结算：只认**日线结算成功**的那一条痕迹。
 * 盘中痕迹（settlementKind=realtime，开盘第一分钟就有）和结算失败
 * （no_bar / data_unavailable，正等 16:10/17:10 重试）都不算 ——
 * 页面自己定义的"结算"就是 15:30 的那次日线结算，数字必须与定义一致。
 */
const settledToday = computed(() =>
  running.value.filter((item) => {
    const s = item.lastSettlement
    if (!s || s.tradeDate !== today()) return false
    if (s.settlementKind !== 'daily') return false
    return s.skipReason !== 'no_bar' && s.skipReason !== 'data_unavailable'
  })
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
  if (last.trigger === 'watch') return '盯盘 agent'
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

// ---------- 组合总览（跨策略：总净值 / 当日回撤 / 熔断 / 相关性） ----------
// 单看每条策略各自的曲线，"三条策略同一天全在跌"是看不见的 —— 组合风险的定义。
const portfolio = ref(null)
const loadPortfolio = async () => {
  try {
    const res = await getPortfolio()
    portfolio.value = res.data || null
  } catch {
    portfolio.value = null // 旁路卡片：拿不到就整块隐藏，不占位
  }
}

// ---------- 决策质量对账（产品良心面板：agent 到底比规则好多少） ----------
const quality = ref(null)
const loadQuality = async () => {
  try {
    const res = await getDecisionQuality(5, 60)
    quality.value = res.data || null
  } catch {
    quality.value = null
  }
}

/** 相关系数的"人话"档位：读数比数字好记，档位边界与常规统计口径一致。 */
const corrLabel = (rho) => {
  const v = Number(rho)
  if (Number.isNaN(v)) return '—'
  const a = Math.abs(v)
  if (a >= 0.8) return v > 0 ? '同涨同跌（伪分散）' : '显著对冲'
  if (a >= 0.5) return v > 0 ? '偏同向' : '偏反向'
  return '基本独立'
}

const modeKeyLabel = (key) => (key === 'agent' ? '委员会（agent）' : '规则引擎')
// 未知方向兜底显示"—"而不是"卖出"：把读不到的数据说成任何一个方向都是假话
const sideKeyLabel = (key) => (key === 'buy' ? '买入' : key === 'sell' ? '卖出' : '—')
/** evaluated=0 但 recent 里有 pending 时给出"还没走满窗口"的说明，避免被读成"没数据"。 */
const qualityPendingCount = computed(() =>
  (quality.value?.recent || []).filter((r) => r.status === 'pending').length)
const qualityEvaluated = computed(() =>
  (quality.value?.bySource || []).reduce((sum, s) => sum + (s.evaluated || 0), 0))

onMounted(() => {
  load()
  loadPortfolio()
  loadQuality()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>模拟盘</h1>
    </div>

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

      <!-- 组合总览：把每条策略各自一条曲线叠成"这个人全部模拟盘"的总视图 -->
      <section v-if="portfolio && portfolio.activeStrategies > 0" class="card portfolio-card">
        <div class="section-head">
          <h2>组合总览</h2>
          <span v-if="portfolio.haltActive" class="badge halt-on">组合熔断中：新开仓已暂停（平仓不受影响）</span>
        </div>
        <div class="portfolio-grid num">
          <div><span>总净值</span><strong>{{ formatMoney(portfolio.totalEquity) }}</strong></div>
          <div>
            <span>当日盈亏</span>
            <strong :class="pnlClass(portfolio.dayDrawdownPct)">{{ formatPct(portfolio.dayDrawdownPct) }}</strong>
          </div>
          <div><span>熔断阈值</span><strong>{{ portfolio.haltThresholdPct }}%（超过即暂停新开仓）</strong></div>
        </div>
        <div v-if="(portfolio.correlations || []).length" class="corr-list">
          <p class="corr-title">策略间日收益相关性</p>
          <p v-for="(c, i) in portfolio.correlations" :key="i" class="corr-row">
            {{ c.a }} × {{ c.b }}：<span class="num">{{ c.rho }}</span>（{{ corrLabel(c.rho) }}）
          </p>
        </div>
      </section>

      <!-- 决策质量对账：agent 与规则在同样的窗口里比方向对错 -->
      <section v-if="quality && (qualityEvaluated > 0 || qualityPendingCount > 0)" class="card quality-card">
        <div class="section-head">
          <h2>决策质量对账</h2>
          <span class="num quality-horizon">每笔成交 {{ quality.horizonDays }} 个交易日后回头看</span>
        </div>
        <p class="quality-note">
          按「方向对错」对账（买入看涨对、卖出看跌对），不是盈亏——盈亏看上面各策略的曲线。
          还没走满 {{ quality.horizonDays }} 个交易日的成交不计入。
        </p>
        <div class="quality-grid num">
          <div v-for="s in quality.bySource" :key="s.key" class="quality-block">
            <span class="quality-source">{{ modeKeyLabel(s.key) }}</span>
            <strong>{{ s.winRatePct === null ? '—' : s.winRatePct + '%' }} 胜率</strong>
            <em>{{ s.evaluated }} 笔已对账 · 平均方向收益 {{ s.avgAlignedPct === null ? '—' : formatPct(s.avgAlignedPct) }}</em>
          </div>
        </div>
        <details v-if="(quality.recent || []).length" class="quality-recent">
          <summary>最近 {{ quality.recent.length }} 笔明细</summary>
          <table>
            <thead>
              <tr><th>日期</th><th>标的</th><th>方向</th><th>决策</th><th>价格</th><th>对齐收益</th><th>状态</th></tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in quality.recent" :key="i">
                <td>{{ r.date }}</td>
                <td>{{ r.symbol }}</td>
                <td>{{ sideKeyLabel(r.side) }}</td>
                <td>{{ modeKeyLabel(r.mode) }}</td>
                <td>{{ r.price }}</td>
                <td :class="r.alignedPct != null ? pnlClass(r.alignedPct) : ''">
                  {{ r.alignedPct == null ? '—' : formatPct(r.alignedPct) }}
                </td>
                <td>{{ r.status === 'pending' ? '未到期' : (r.win ? '对' : '错') }}</td>
              </tr>
            </tbody>
          </table>
        </details>
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
            <span v-if="configUpdatedNote(item)" class="badge config-updated">{{ configUpdatedNote(item) }}</span>
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
            <button type="button" class="ghost-btn" @click="openEdit(item.strategyId)">调整策略</button>
            <button type="button" class="ghost-btn" @click="goDetail(item.strategyId)">看详情</button>
          </div>
        </div>

        <!-- 还没被评估过：先把话说清楚，再谈数字（一片 N/A 会被读成"坏了"） -->
        <div v-if="!item.evaluated" class="pending-note">
          <strong>还没有被评估过一次。</strong>
          <template v-if="item.decisionMode === 'agent'">
            启动或切换到委员会的那一刻会先开一次首会；停在这里说明当天还没有 K 线（休市或数据未出），
            下一次尝试在交易日 15:30 的日线结算。
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
            净值曲线还没有数据点：它由日线结算（每个交易日 15:30）记账，每天一格。
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

        <!-- 今日报告 + 哨兵观察（2026-10-10 起不再推消息，复盘内容长在这里）：
             默认收起成一行摘要，点开看全文；哨兵观察配"去调整策略"直达详情页。 -->
        <details v-if="reportOf(item.strategyId)" class="report-box">
          <summary>
            <span class="report-title">最近报告</span>
            <span class="report-meta">{{ reportOf(item.strategyId).reportDate }}</span>
            <span v-if="reportOf(item.strategyId).sentinels.length" class="report-sentinel-count">
              哨兵 {{ reportOf(item.strategyId).sentinels.length }} 条观察
            </span>
            <span v-else class="report-meta">哨兵暂无观察</span>
          </summary>
          <pre class="report-text">{{ reportOf(item.strategyId).report || reportOf(item.strategyId).briefing || '这一天的结算还没跑：交易日 15:30 结算后这里会有内容。' }}</pre>
          <ul v-if="reportOf(item.strategyId).sentinels.length" class="sentinel-list">
            <li v-for="sen in reportOf(item.strategyId).sentinels" :key="sen.at">
              <span class="sentinel-tag">{{ sen.label }}</span>
              <span class="sentinel-time num">{{ formatTime(sen.at) }}</span>
              <p class="sentinel-evidence">{{ sen.evidence }}</p>
            </li>
          </ul>
          <div class="report-actions">
            <button type="button" class="ghost-btn" @click="openEdit(item.strategyId)">去调整策略</button>
          </div>
        </details>
      </section>

      <StrategyEditSheet :open="editingId !== null" :strategy-id="editingId"
                          @close="editingId = null" @saved="onEditSaved" />
  </div>
</template>

<style scoped>


.page-hint { font-size: 13px; color: var(--color-text-secondary); line-height: 1.7; margin: 0 0 16px; }
.card {
  background: var(--color-bg-surface);
  border-radius: var(--radius-lg);
  padding: 18px 20px;
  margin-bottom: 16px;
  box-shadow: var(--shadow-1);
}
.badge.config-updated {
  background: var(--color-warning-soft, rgba(250, 204, 21, .12));
  color: var(--color-warning);
  font-size: 11px;
}

.status-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
}

/* 今日报告 + 哨兵观察（收起一行、点开全文；内容来自后端确定性文本，纯文本展示） */
.report-box {
  margin-top: 12px;
  border-top: 1px dashed var(--color-border);
  padding-top: 10px;
}
.report-box summary {
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: var(--color-text-secondary);
  list-style: none;
}
.report-box summary::-webkit-details-marker { display: none; }
.report-box summary::before {
  content: '▸';
  transition: transform var(--duration-fast) var(--ease-out);
  color: var(--color-text-tertiary);
}
.report-box[open] summary::before { transform: rotate(90deg); }
.report-title { font-weight: 600; color: var(--color-text-primary); }
.report-meta { color: var(--color-text-tertiary); font-size: 12px; }
.report-sentinel-count {
  font-size: 12px;
  color: var(--color-warning);
}
.report-text {
  margin: 10px 0 8px;
  padding: 10px 12px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  font: 12.5px/1.7 var(--font-mono, monospace);
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--color-text-primary);
}
.sentinel-list { list-style: none; margin: 0 0 8px; padding: 0; }
.sentinel-list li {
  padding: 8px 0;
  border-top: 1px solid var(--color-border);
  font-size: 12.5px;
}
.sentinel-tag {
  display: inline-block;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--color-warning);
  margin-right: 8px;
}
.sentinel-time { color: var(--color-text-tertiary); font-size: 11.5px; }
.sentinel-evidence { margin: 4px 0 0; color: var(--color-text-secondary); line-height: 1.6; }
.report-actions { padding-top: 4px; }
.status-strip > div { display: flex; flex-direction: column; gap: 4px; }
.status-strip > div.wide { grid-column: 1 / -1; }
.status-strip span { font-size: 12px; color: var(--color-text-secondary); }
.status-strip strong { font-size: 15px; color: var(--color-text-primary); font-weight: 600; }

/* ---------- 组合总览 ---------- */
.section-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.section-head h2 { margin: 0; font-size: 16px; }
.badge.halt-on {
  color: var(--color-loss);
  background: color-mix(in srgb, var(--color-loss) 12%, transparent);
  border-color: var(--color-loss);
}
.portfolio-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
}
.portfolio-grid > div { display: flex; flex-direction: column; gap: 4px; }
.portfolio-grid span { font-size: 12px; color: var(--color-text-secondary); }
.portfolio-grid strong { font-size: 15px; font-weight: 600; }
.corr-list { margin-top: 10px; padding-top: 8px; border-top: 1px dashed var(--color-border); }
.corr-title { margin: 0 0 4px; font-size: 12px; color: var(--color-text-secondary); }
.corr-row { margin: 2px 0; font-size: 13px; color: var(--color-text-primary); }
.corr-row .num { color: var(--color-accent); }

/* ---------- 决策质量对账 ---------- */
.quality-note { margin: 0 0 10px; font-size: 12.5px; line-height: 1.6; color: var(--color-text-muted); }
.quality-horizon { font-size: 12px; color: var(--color-text-muted); }
.quality-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
.quality-block { display: flex; flex-direction: column; gap: 4px; padding: 10px 12px; border-radius: var(--radius-md); background: var(--color-bg-subtle); }
.quality-source { font-size: 12px; color: var(--color-text-secondary); }
.quality-block strong { font-size: 18px; font-weight: 600; }
.quality-block em { font-style: normal; font-size: 12px; color: var(--color-text-muted); }
.quality-recent { margin-top: 10px; }
.quality-recent summary { cursor: pointer; font-size: 13px; color: var(--color-text-secondary); }
.quality-recent table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 12.5px; }
.quality-recent th, .quality-recent td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--color-border); }
.quality-recent th { color: var(--color-text-secondary); font-weight: 500; }
/* 手机：明细表允许横向滚动而不是把页面撑爆（列数不少，硬塞只会字挤成竖条） */
.quality-recent { overflow-x: auto; }
.quality-recent table { min-width: 560px; }

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
