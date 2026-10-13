<script setup>
/**
 * 模拟盘总览：这个功能的主入口。逻辑自旧版原样移植（零漂移）。
 *
 * 页面只回答四个问题：在跑什么 / 赚没赚 / 今天动没动 / 下次什么时候。
 * 数字与措辞全部来自后端同一份口径（/paper/overview 等），前端只负责显示。
 * 报告日期口径 = lastEvalAt（六条口径之一），不自己算"今天"。
 */
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getPaperOverview, startPaper, stopPaper } from '../api/strategy.js'
import { getPortfolio, getDecisionQuality, getPaperReport } from '../api/paper.js'
import StrategyEditSheet from '../components/StrategyEditSheet.vue'
import Badge from '../components/ui/badge.vue'
import Button from '../components/ui/button.vue'
import Skeleton from '../components/ui/skeleton.vue'
import { dirTextClass } from '../utils/format.js'

const router = useRouter()

const items = ref([])
const loading = ref(false)
const error = ref('')
const actionId = ref(null)
const reports = ref([])
const reportOf = (strategyId) => reports.value.find((r) => r.strategyId === strategyId) || null
const editingId = ref(null)
const openEdit = (strategyId) => { editingId.value = strategyId }
const onEditSaved = () => load()

/** 规则口径徽标：configUpdatedAt 距今 7 天内提示"前后不可比" */
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
  } catch {
    reports.value = []
  }
}

const running = computed(() => items.value.filter((item) => item.paperEnabled))
/** 今日已结算：只认日线结算成功的那一条痕迹（盘中痕迹与结算失败都不算） */
const settledToday = computed(() =>
  running.value.filter((item) => {
    const s = item.lastSettlement
    if (!s || s.tradeDate !== today()) return false
    if (s.settlementKind !== 'daily') return false
    return s.skipReason !== 'no_bar' && s.skipReason !== 'data_unavailable'
  })
)
const nextEvaluationNote = computed(() => running.value[0]?.nextEvaluationNote || '')

const today = () => new Date().toLocaleDateString('sv-SE')

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

const modeLabel = (mode) => (mode === 'agent' ? '多角色委员会' : '规则引擎')

/** 痕迹那句人话本身就以"日期 时间（标的）"开头，这里只补口径 */
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
const portfolio = ref(null)
const loadPortfolio = async () => {
  try {
    const res = await getPortfolio()
    portfolio.value = res.data || null
  } catch {
    portfolio.value = null
  }
}

// ---------- 决策质量对账（agent 到底比规则好多少） ----------
const quality = ref(null)
const loadQuality = async () => {
  try {
    const res = await getDecisionQuality(5, 60)
    quality.value = res.data || null
  } catch {
    quality.value = null
  }
}

/** 相关系数的"人话"档位 */
const corrLabel = (rho) => {
  const v = Number(rho)
  if (Number.isNaN(v)) return '—'
  const a = Math.abs(v)
  if (a >= 0.8) return v > 0 ? '同涨同跌（伪分散）' : '显著对冲'
  if (a >= 0.5) return v > 0 ? '偏同向' : '偏反向'
  return '基本独立'
}

const modeKeyLabel = (key) => (key === 'agent' ? '委员会（agent）' : '规则引擎')
// 未知方向兜底显示"—"而不是"卖出"
const sideKeyLabel = (key) => (key === 'buy' ? '买入' : key === 'sell' ? '卖出' : '—')
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
  <div>
    <div class="mb-4">
      <h1 class="text-[20px] font-semibold text-ink">模拟盘</h1>
      <p class="mt-1 max-w-[75ch] text-[13px] leading-relaxed text-ink-2">
        每个交易日 <strong class="font-medium text-ink">15:30</strong> 自动结算一次；切到多角色委员会的策略只在日线结算时决策。
        下面每条策略都直接回答四件事：<strong class="font-medium text-ink">在跑什么、赚没赚、今天动没动、下次什么时候</strong>。
      </p>
    </div>

    <p v-if="error" class="mb-3 text-[13px] text-up" role="alert">{{ error }}</p>

    <!-- 顶部状态条：让"它还在跑"这件事有确定的时间点 -->
    <section v-if="items.length" class="num mb-4 grid gap-x-6 gap-y-3 rounded-xl border border-border bg-surface p-4 [grid-template-columns:repeat(auto-fit,minmax(160px,1fr))]">
      <div class="flex flex-col gap-1"><span class="text-[12px] text-ink-2">运行中</span><strong class="text-[15px] font-semibold text-ink">{{ running.length }} / {{ items.length }} 条</strong></div>
      <div class="flex flex-col gap-1"><span class="text-[12px] text-ink-2">今日已结算</span><strong class="text-[15px] font-semibold text-ink">{{ settledToday.length }} / {{ running.length }} 条</strong></div>
      <div class="col-span-full flex flex-col gap-1"><span class="text-[12px] text-ink-2">下一次评估</span><strong class="text-[15px] font-semibold text-ink">{{ nextEvaluationNote || '暂无运行中的模拟盘' }}</strong></div>
    </section>

    <!-- 组合总览 -->
    <section v-if="portfolio && portfolio.activeStrategies > 0" class="mb-4 rounded-xl border border-border bg-surface p-5">
      <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 class="text-[15px] font-semibold text-ink">组合总览</h2>
        <Badge v-if="portfolio.haltActive" tone="warn">组合熔断中：新开仓已暂停（平仓不受影响）</Badge>
      </div>
      <div class="num grid gap-x-6 gap-y-3 [grid-template-columns:repeat(auto-fit,minmax(150px,1fr))]">
        <div class="flex flex-col gap-1"><span class="text-[12px] text-ink-2">总净值</span><strong class="text-[15px] font-semibold text-ink">{{ formatMoney(portfolio.totalEquity) }}</strong></div>
        <div class="flex flex-col gap-1"><span class="text-[12px] text-ink-2">当日盈亏</span><strong class="text-[15px] font-semibold" :class="dirTextClass(portfolio.dayDrawdownPct)">{{ formatPct(portfolio.dayDrawdownPct) }}</strong></div>
        <div class="flex flex-col gap-1"><span class="text-[12px] text-ink-2">熔断阈值</span><strong class="text-[15px] font-semibold text-ink">{{ portfolio.haltThresholdPct }}%（超过即暂停新开仓）</strong></div>
      </div>
      <div v-if="(portfolio.correlations || []).length" class="mt-2.5 border-t border-dashed border-border pt-2">
        <p class="mb-1 text-[12px] text-ink-2">策略间日收益相关性</p>
        <p v-for="(c, i) in portfolio.correlations" :key="i" class="py-0.5 text-[13px] text-ink">
          {{ c.a }} × {{ c.b }}：<span class="num text-accent">{{ c.rho }}</span>（{{ corrLabel(c.rho) }}）
        </p>
      </div>
    </section>

    <!-- 决策质量对账 -->
    <section v-if="quality && (qualityEvaluated > 0 || qualityPendingCount > 0)" class="mb-4 rounded-xl border border-border bg-surface p-5">
      <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 class="text-[15px] font-semibold text-ink">决策质量对账</h2>
        <span class="num text-[12px] text-ink-3">每笔成交 {{ quality.horizonDays }} 个交易日后回头看</span>
      </div>
      <p class="mb-3 max-w-[75ch] text-[12.5px] leading-relaxed text-ink-2">
        按「方向对错」对账（买入看涨对、卖出看跌对），不是盈亏——盈亏看上面各策略的曲线。
        还没走满 {{ quality.horizonDays }} 个交易日的成交不计入。
      </p>
      <div class="num grid gap-2.5 [grid-template-columns:repeat(auto-fit,minmax(200px,1fr))]">
        <div v-for="s in quality.bySource" :key="s.key" class="flex flex-col gap-1 rounded-lg bg-surface-2 p-3">
          <span class="text-[12px] text-ink-2">{{ modeKeyLabel(s.key) }}</span>
          <strong class="text-[18px] font-semibold text-ink">{{ s.winRatePct === null ? '—' : s.winRatePct + '%' }} 胜率</strong>
          <span class="text-[12px] text-ink-3">{{ s.evaluated }} 笔已对账 · 平均方向收益 {{ s.avgAlignedPct === null ? '—' : formatPct(s.avgAlignedPct) }}</span>
        </div>
      </div>
      <details v-if="(quality.recent || []).length" class="mt-2.5 overflow-x-auto">
        <summary class="cursor-pointer text-[13px] text-ink-2">最近 {{ quality.recent.length }} 笔明细</summary>
        <table class="mt-2 w-full min-w-[560px] text-[12.5px]">
          <thead>
            <tr class="text-left text-ink-2">
              <th class="py-1 pr-2 font-medium">日期</th><th class="pr-2 font-medium">标的</th><th class="pr-2 font-medium">方向</th>
              <th class="pr-2 font-medium">决策</th><th class="pr-2 font-medium">价格</th><th class="pr-2 font-medium">对齐收益</th><th class="font-medium">状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in quality.recent" :key="i" class="border-t border-border">
              <td class="num py-1.5 pr-2 text-ink">{{ r.date }}</td>
              <td class="num pr-2 text-ink">{{ r.symbol }}</td>
              <td class="pr-2 text-ink">{{ sideKeyLabel(r.side) }}</td>
              <td class="pr-2 text-ink">{{ modeKeyLabel(r.mode) }}</td>
              <td class="num pr-2 text-ink">{{ r.price }}</td>
              <td class="num pr-2" :class="dirTextClass(r.alignedPct)">{{ r.alignedPct == null ? '—' : formatPct(r.alignedPct) }}</td>
              <td class="text-ink">{{ r.status === 'pending' ? '未到期' : (r.win ? '对' : '错') }}</td>
            </tr>
          </tbody>
        </table>
      </details>
    </section>

    <!-- 加载骨架 -->
    <div v-if="loading" class="space-y-4">
      <div v-for="i in 2" :key="i" class="space-y-3 rounded-xl border border-border bg-surface p-5">
        <Skeleton width="40%" height="18px" />
        <div class="flex gap-2.5">
          <Skeleton v-for="j in 3" :key="j" width="30%" height="48px" />
        </div>
        <Skeleton width="80%" height="13px" />
      </div>
    </div>

    <div v-else-if="!items.length && !error" class="flex flex-col items-center gap-3 rounded-xl border border-border bg-surface py-14 text-center">
      <p class="text-[14px] text-ink-2">你还没有任何策略，所以也没有模拟盘在跑。</p>
      <Button variant="primary" @click="goStrategies">去策略库创建一条</Button>
    </div>

    <!-- 每条策略 -->
    <section v-for="item in items" :key="item.strategyId" class="mb-4 rounded-xl border border-border bg-surface p-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="flex flex-wrap items-center gap-2">
          <h2 class="text-[16px] font-semibold text-ink">{{ item.name }}</h2>
          <span class="num text-[12px] text-ink-3">{{ item.symbol }}</span>
          <Badge :tone="item.paperEnabled ? 'down' : 'neutral'">
            {{ item.paperEnabled ? '模拟盘运行中' : '未启动模拟盘' }}
          </Badge>
          <Badge :tone="item.decisionMode === 'agent' ? 'accent' : 'neutral'">{{ modeLabel(item.decisionMode) }}</Badge>
          <Badge v-if="configUpdatedNote(item)" tone="warn">{{ configUpdatedNote(item) }}</Badge>
        </div>
        <div class="flex flex-wrap gap-2">
          <Button size="sm" :loading="actionId === item.strategyId" @click="togglePaper(item)">
            {{ item.paperEnabled ? '停止模拟盘' : '启动模拟盘' }}
          </Button>
          <Button size="sm" @click="openEdit(item.strategyId)">调整策略</Button>
          <Button size="sm" @click="goDetail(item.strategyId)">看详情</Button>
        </div>
      </div>

      <!-- 还没被评估过：先把话说清楚，再谈数字 -->
      <div v-if="!item.evaluated" class="mt-3 rounded-lg border border-dashed border-border bg-surface-2 p-3 text-[13px] leading-relaxed text-ink-2">
        <strong class="text-ink">还没有被评估过一次。</strong>
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
        <div class="num my-3.5 grid gap-2.5 [grid-template-columns:repeat(auto-fit,minmax(130px,1fr))]">
          <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
            <span class="text-[12px] text-ink-2">最新净值</span>
            <strong class="text-[16px] font-semibold text-ink">{{ formatMoney(item.equity) }}</strong>
          </div>
          <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
            <span class="text-[12px] text-ink-2">期间收益</span>
            <strong class="text-[16px] font-semibold" :class="dirTextClass(item.summary?.returnPct)">
              {{ item.summary?.returnPct === null || item.summary?.returnPct === undefined ? 'N/A' : formatPct(item.summary.returnPct) }}
            </strong>
          </div>
          <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
            <span class="text-[12px] text-ink-2">超额（vs 买入持有）</span>
            <strong class="text-[16px] font-semibold" :class="dirTextClass(item.summary?.excessVsBuyAndHoldPct)">
              {{ item.summary?.excessVsBuyAndHoldPct === null || item.summary?.excessVsBuyAndHoldPct === undefined ? 'N/A' : formatPct(item.summary.excessVsBuyAndHoldPct) }}
            </strong>
          </div>
          <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
            <span class="text-[12px] text-ink-2">最大回撤</span>
            <strong class="text-[16px] font-semibold text-down">
              {{ item.summary?.maxDrawdownPct === null || item.summary?.maxDrawdownPct === undefined ? 'N/A' : formatPct(item.summary.maxDrawdownPct) }}
            </strong>
          </div>
          <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
            <span class="text-[12px] text-ink-2">空仓占比</span>
            <strong class="text-[16px] font-semibold text-ink">{{ item.summary?.flatRatioPct === null || item.summary?.flatRatioPct === undefined ? 'N/A' : item.summary.flatRatioPct + '%' }}</strong>
          </div>
          <div class="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
            <span class="text-[12px] text-ink-2">样本</span>
            <strong class="text-[16px] font-semibold text-ink">{{ item.summary?.days ?? 0 }} 天</strong>
          </div>
        </div>
        <!-- 还没有净值点时把原因写出来：一串 N/A 会被读成"坏了" -->
        <p v-if="!item.summary" class="mb-3 text-[12px] leading-relaxed text-ink-3">
          净值曲线还没有数据点：它由日线结算（每个交易日 15:30）记账，每天一格。
          盘中成交不会立刻产生净值点。
        </p>

        <div class="flex flex-col gap-1.5">
          <p class="text-[13px] leading-relaxed text-ink-2">
            <span class="inline-block min-w-[5.5rem] text-[12px] text-ink-3">持仓</span>
            <span class="num">{{ item.shares }} 股 · 现金 {{ formatMoney(item.cash) }} · 持仓市值 {{ formatMoney(item.positionValue) }} · 最新信号 {{ signalLabel(item.lastSignal) }}</span>
          </p>
          <p v-if="item.lastSettlement" class="text-[13px] leading-relaxed text-ink-2">
            <span class="inline-block min-w-[5.5rem] text-[12px] text-ink-3">最近一次结算</span>
            {{ item.lastSettlement.sentence }}（{{ settlementKindLabel(item.lastSettlement) }}）
            <template v-if="item.lastSettlement.decisionMode === 'agent' && item.lastSettlement.agentLlmCalls">
              ｜委员会 {{ item.lastSettlement.agentLlmCalls }} 次调用 / {{ item.lastSettlement.agentTokens ?? 0 }} token
            </template>
          </p>
          <p v-else class="text-[13px] leading-relaxed text-ink-2">
            <span class="inline-block min-w-[5.5rem] text-[12px] text-ink-3">最近一次结算</span>还没有结算记录（痕迹共 {{ item.traceCount }} 条）
          </p>
          <p class="text-[13px] leading-relaxed text-ink-2">
            <span class="inline-block min-w-[5.5rem] text-[12px] text-ink-3">预期</span>
            {{ item.expectation ? item.expectation.sentence : '还没有登记预期 —— 目前没有人对这条策略的未来下过承诺' }}
          </p>
          <p class="text-[13px] leading-relaxed text-ink-2">
            <span class="inline-block min-w-[5.5rem] text-[12px] text-ink-3">下次</span>{{ item.nextEvaluationNote }}
          </p>
          <p class="text-[13px] leading-relaxed text-ink-2">
            <span class="inline-block min-w-[5.5rem] text-[12px] text-ink-3">评估过</span>
            <span class="num">{{ formatTime(item.lastEvalAt) }}</span>｜初始资金 {{ formatMoney(item.initialCapital) }}
          </p>
        </div>
      </template>

      <!-- 今日报告 + 哨兵观察（默认收起，点开看全文） -->
      <details v-if="reportOf(item.strategyId)" class="mt-3 border-t border-dashed border-border pt-2.5">
        <summary class="flex cursor-pointer list-none items-center gap-2.5 text-[13px] text-ink-2 [&::-webkit-details-marker]:hidden">
          <span class="text-ink-3 transition-transform duration-150 [details[open]_&]:rotate-90">▸</span>
          <span class="font-semibold text-ink">最近报告</span>
          <span class="num text-[12px] text-ink-3">{{ reportOf(item.strategyId).reportDate }}</span>
          <span v-if="reportOf(item.strategyId).sentinels.length" class="text-[12px] text-warn">哨兵 {{ reportOf(item.strategyId).sentinels.length }} 条观察</span>
          <span v-else class="text-[12px] text-ink-3">哨兵暂无观察</span>
        </summary>
        <pre class="my-2.5 whitespace-pre-wrap break-words rounded-lg bg-surface-2 p-3 font-mono text-[12.5px] leading-7 text-ink">{{ reportOf(item.strategyId).report || reportOf(item.strategyId).briefing || '这一天的结算还没跑：交易日 15:30 结算后这里会有内容。' }}</pre>
        <ul v-if="reportOf(item.strategyId).sentinels.length">
          <li v-for="sen in reportOf(item.strategyId).sentinels" :key="sen.at" class="border-t border-border py-2 text-[12.5px]">
            <span class="mr-2 text-[11.5px] font-semibold text-warn">{{ sen.label }}</span>
            <span class="num text-[11.5px] text-ink-3">{{ formatTime(sen.at) }}</span>
            <p class="mt-1 leading-relaxed text-ink-2">{{ sen.evidence }}</p>
          </li>
        </ul>
        <div class="pt-1">
          <Button size="sm" @click="openEdit(item.strategyId)">去调整策略</Button>
        </div>
      </details>
    </section>

    <StrategyEditSheet :open="editingId !== null" :strategy-id="editingId" @close="editingId = null" @saved="onEditSaved" />
  </div>
</template>
