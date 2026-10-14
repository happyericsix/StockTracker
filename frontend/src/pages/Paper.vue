<script setup>
/**
 * 模拟盘总览：这个功能的主入口。
 *
 * 页面只回答四个问题：在跑什么 / 赚没赚 / 今天动没动 / 下次什么时候。
 * 数字与措辞全部来自后端同一份口径（/paper/overview 等），前端只负责显示。
 * 报告日期口径 = lastEvalAt（六条口径之一），不自己算"今天"。
 *
 * 分区（2026-10-14 重排，向自选行情页的阅读节奏看齐：一区一问、主数字大、
 * 操作弱化；所有原功能保留，只是重新分区与降级）：
 *   ① 资产总览 —— 赚没赚（总资产大数字 + 当日盈亏 + 次级数据一行）
 *   ② 我的策略 —— 在跑什么（卡片网格：净值主数字 + 期间收益徽章 + 最近动态一句）
 *   ③ 决策对账 —— 智囊比规则准多少（默认收起：这是专业视角，不该占首屏）
 * 数据聚合只做显示层的求和/求差，口径仍以后端为准。
 */
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getPaperOverview, startPaper, stopPaper } from '../api/strategy.js'
import { getPortfolio, getDecisionQuality, getPaperReport } from '../api/paper.js'
import StrategyEditSheet from '../components/StrategyEditSheet.vue'
import Badge from '../components/ui/badge.vue'
import Button from '../components/ui/button.vue'
import Skeleton from '../components/ui/skeleton.vue'
import InfoTip from '../components/InfoTip.vue'
import { dirTextClass, dirTone, signed, dateTime } from '../utils/format.js'

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

const formatMoney = (value) => {
  if (value === null || value === undefined) return 'N/A'
  const n = Number(value)
  if (Number.isNaN(n)) return 'N/A'
  return n.toLocaleString('zh-CN', { style: 'currency', currency: 'CNY', maximumFractionDigits: 2 })
}

const formatPct = (value) => {
  if (value === null || value === undefined) return 'N/A'
  const n = Number(value)
  if (Number.isNaN(n)) return 'N/A'
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

/** 决策模式的用户话术：委员会 = 一组 AI 角色辩论后拍板；徽章只留两个字的识别位 */
const modeBadge = (mode) =>
  mode === 'agent' ? { label: '智囊', tone: 'accent' } : { label: '规则', tone: 'neutral' }
const modeFullLabel = (mode) => (mode === 'agent' ? '智囊（多角色 AI 委员会决策）' : '规则（按既定条件自动执行）')

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

// ---------- 资产总览（① 赚没赚：显示层聚合，口径以后端为准） ----------
const portfolio = ref(null)
const loadPortfolio = async () => {
  try {
    const res = await getPortfolio()
    portfolio.value = res.data || null
  } catch {
    portfolio.value = null
  }
}

const sumOf = (fn) => running.value.reduce((sum, item) => {
  const n = Number(fn(item))
  return Number.isFinite(n) ? sum + n : sum
}, 0)

/** 总资产：后端组合口径优先；没有组合数据（刚起步/接口失败）时用各策略净值求和兜底 */
const totalEquity = computed(() => {
  if (portfolio.value && portfolio.value.activeStrategies > 0 && portfolio.value.totalEquity != null) {
    return Number(portfolio.value.totalEquity)
  }
  return running.value.length ? sumOf((i) => i.equity) : null
})
const totalInitial = computed(() =>
  running.value.length ? sumOf((i) => i.initialCapital) : null)
/** 累计收益：有初始资金合计才可算（显示层，与各策略详情同一分子分母口径） */
const totalReturnPct = computed(() => {
  const equity = totalEquity.value
  const initial = totalInitial.value
  if (equity == null || !initial) return null
  return ((equity - initial) / initial) * 100
})
const totalCash = computed(() => (running.value.length ? sumOf((i) => i.cash) : null))
const totalPositionValue = computed(() => (running.value.length ? sumOf((i) => i.positionValue) : null))

/** 相关系数的"人话"档位 */
const corrLabel = (rho) => {
  const v = Number(rho)
  if (Number.isNaN(v)) return '—'
  const a = Math.abs(v)
  if (a >= 0.8) return v > 0 ? '同涨同跌（伪分散）' : '显著对冲'
  if (a >= 0.5) return v > 0 ? '偏同向' : '偏反向'
  return '基本独立'
}

// ---------- 决策对账（③ 智囊比规则准多少：默认收起） ----------
const quality = ref(null)
const loadQuality = async () => {
  try {
    const res = await getDecisionQuality(5, 60)
    quality.value = res.data || null
  } catch {
    quality.value = null
  }
}
const modeKeyLabel = (key) => (key === 'agent' ? '智囊' : '规则')
const sideKeyLabel = (key) => (key === 'buy' ? '买入' : key === 'sell' ? '卖出' : '—')
const qualityPendingCount = computed(() =>
  (quality.value?.recent || []).filter((r) => r.status === 'pending').length)
const qualityEvaluated = computed(() =>
  (quality.value?.bySource || []).reduce((sum, s) => sum + (s.evaluated || 0), 0))
/** 对账 summary 那一行的一句总览：两方胜率并排，谁高谁低一眼可见 */
const qualitySummary = computed(() => {
  const src = quality.value?.bySource || []
  const agent = src.find((s) => s.key === 'agent')
  const rule = src.find((s) => s.key === 'rule')
  if (!agent && !rule) return ''
  const fmt = (s) => (s && s.winRatePct != null ? `${s.winRatePct}%（${s.evaluated} 笔）` : '暂无样本')
  return `智囊 ${fmt(agent)} · 规则 ${fmt(rule)}`
})

onMounted(() => {
  load()
  loadPortfolio()
  loadQuality()
})
</script>

<template>
  <div>
    <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
      <h1 class="text-[20px] font-semibold text-ink">模拟盘</h1>
      <span class="num text-[12px] text-ink-3">{{ nextEvaluationNote || '' }}</span>
    </div>

    <p v-if="error" class="mb-3 text-[13px] text-up" role="alert">{{ error }}</p>

    <!-- ① 资产总览：赚没赚。总资产是大数字，当日盈亏定向，其余一行小字 -->
    <section
      v-if="items.length"
      class="mb-5 rounded-xl border border-border bg-surface p-5"
    >
      <div class="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div>
          <p class="flex items-center gap-2 text-[12px] text-ink-2">
            <span
              class="h-2 w-2 rounded-full"
              :class="running.length ? 'bg-down' : 'bg-ink-3'"
              aria-hidden="true"
            ></span>
            总资产
            <span v-if="portfolio?.haltActive" class="text-warn">· 组合熔断中：新开仓已暂停（平仓不受影响）</span>
          </p>
          <p class="num mt-1.5 text-[28px] font-semibold leading-tight text-ink">
            {{ totalEquity == null ? '—' : formatMoney(totalEquity) }}
          </p>
          <p class="mt-1.5 flex flex-wrap items-center gap-3">
            <Badge v-if="portfolio?.dayDrawdownPct != null" :tone="dirTone(portfolio.dayDrawdownPct)" class="num">
              当日 {{ signed(portfolio.dayDrawdownPct, '%') || '0.00%' }}
            </Badge>
            <Badge v-else tone="flat" class="num">当日 —</Badge>
            <span v-if="totalReturnPct != null" class="num text-[13px]" :class="dirTextClass(totalReturnPct)">
              累计 {{ formatPct(totalReturnPct) }}
            </span>
            <span class="num text-[12px] text-ink-3">
              自 {{ formatMoney(totalInitial) }} 起
            </span>
          </p>
        </div>
        <!-- 次级数据：一行小字说完"钱在哪、跑几条、什么时候动" -->
        <dl class="num grid gap-x-8 gap-y-1.5 text-[12.5px] [grid-template-columns:repeat(auto-fit,minmax(120px,1fr))] sm:min-w-[280px]">
          <div class="flex flex-col gap-0.5"><dt class="font-normal text-ink-3">现金</dt><dd class="font-medium text-ink">{{ totalCash == null ? '—' : formatMoney(totalCash) }}</dd></div>
          <div class="flex flex-col gap-0.5"><dt class="font-normal text-ink-3">持仓市值</dt><dd class="font-medium text-ink">{{ totalPositionValue == null ? '—' : formatMoney(totalPositionValue) }}</dd></div>
          <div class="flex flex-col gap-0.5"><dt class="font-normal text-ink-3">在跑</dt><dd class="font-medium text-ink">{{ running.length }} / {{ items.length }} 条</dd></div>
          <div class="flex flex-col gap-0.5"><dt class="font-normal text-ink-3">今日已结算</dt><dd class="font-medium text-ink">{{ settledToday.length }} / {{ running.length }} 条</dd></div>
        </dl>
      </div>
      <p class="num mt-3 border-t border-dashed border-border pt-2.5 text-[12px] text-ink-3">
        每个交易日 15:30 自动结算一次；盘中由盯盘值守按授权边界执行。{{ nextEvaluationNote }}
      </p>
      <!-- 策略间相关性：专业视角，收进折叠，不与主数字抢位置 -->
      <details v-if="(portfolio?.correlations || []).length" class="mt-1 border-t border-dashed border-border pt-2">
        <summary class="cursor-pointer text-[12.5px] text-ink-2">策略间相关性与熔断阈值</summary>
        <p class="num mt-2 text-[12px] text-ink-3">熔断阈值 {{ portfolio.haltThresholdPct }}%（当日合计回撤超过即暂停新开仓）</p>
        <p v-for="(c, i) in portfolio.correlations" :key="i" class="py-0.5 text-[12.5px] text-ink">
          {{ c.a }} × {{ c.b }}：<span class="num text-accent">{{ c.rho }}</span>（{{ corrLabel(c.rho) }}）
        </p>
      </details>
    </section>

    <!-- 加载骨架：拼出资产卡 + 策略卡网格的形状 -->
    <div v-if="loading" class="space-y-4">
      <div class="rounded-xl border border-border bg-surface p-5">
        <Skeleton width="30%" height="14px" />
        <div class="mt-2"><Skeleton width="45%" height="30px" /></div>
      </div>
      <div class="grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(260px,1fr))]">
        <div v-for="i in 3" :key="i" class="space-y-2.5 rounded-xl border border-border bg-surface p-4">
          <Skeleton width="55%" height="14px" />
          <Skeleton width="35%" height="24px" />
          <Skeleton width="80%" height="12px" />
        </div>
      </div>
    </div>

    <div v-else-if="!items.length && !error" class="flex flex-col items-center gap-3 rounded-xl border border-border bg-surface py-14 text-center">
      <p class="text-[14px] text-ink-2">你还没有任何策略，所以也没有模拟盘在跑。</p>
      <Button variant="primary" @click="goStrategies">去策略库创建一条</Button>
    </div>

    <!-- ② 我的策略：在跑什么。卡片网格 = 自选行情页的阅读节奏 -->
    <section v-if="!loading && items.length">
      <h2 class="mb-3.5 flex items-baseline gap-2 text-[16px] font-semibold text-ink">
        我的策略
        <span v-if="items.length" class="num text-[12px] font-normal text-ink-3">{{ items.length }}</span>
        <InfoTip
          label="模式说明"
          text="智囊：一组 AI 角色开会辩论后决定买卖时机；规则：按你设的条件自动执行。两种模式的买卖都由同一套风控执行。"
        />
      </h2>

      <ul class="grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(260px,1fr))]">
        <li
          v-for="item in items"
          :key="item.strategyId"
          class="flex flex-col rounded-xl border border-border bg-surface p-4 transition-colors hover:border-ink-3"
        >
          <!-- 卡头：状态点 + 名称 + 模式徽章 -->
          <button type="button" class="flex-grow text-start" @click="goDetail(item.strategyId)">
            <p class="flex flex-wrap items-center gap-2">
              <span
                class="h-2 w-2 shrink-0 rounded-full"
                :class="item.paperEnabled ? 'bg-down' : 'bg-ink-3'"
                :aria-label="item.paperEnabled ? '运行中' : '已停止'"
              ></span>
              <span class="truncate text-[13.5px] font-semibold text-ink">{{ item.name }}</span>
              <span class="num text-[11.5px] text-ink-3">{{ item.symbol }}</span>
              <Badge :tone="modeBadge(item.decisionMode).tone">{{ modeBadge(item.decisionMode).label }}</Badge>
            </p>

            <!-- 还没被评估过：不摆 N/A，先把"为什么还没有数字"说清楚 -->
            <div v-if="!item.evaluated" class="mt-3 rounded-lg border border-dashed border-border bg-surface-2 p-3 text-[12.5px] leading-relaxed text-ink-2">
              <p class="num text-[15px] font-semibold text-ink">{{ formatMoney(item.initialCapital) }}</p>
              <p class="mt-1">刚起步，还没有第一笔净值：
                <template v-if="item.decisionMode === 'agent'">智囊在启动时会先开一次会，若当天没有 K 线则顺延到交易日 15:30。</template>
                <template v-else>下一次检查在几分钟内，日线结算在交易日 15:30。</template>
              </p>
            </div>

            <!-- 主数字：净值（大）+ 期间收益（定向徽章），对应自选卡的"价格 + 涨跌幅" -->
            <template v-else>
              <p class="num mt-2.5 text-[20px] font-semibold leading-tight text-ink">
                {{ item.equity == null ? '—' : formatMoney(item.equity) }}
              </p>
              <p class="mt-1.5 flex flex-wrap items-center gap-2">
                <Badge
                  v-if="item.summary?.returnPct != null"
                  :tone="dirTone(item.summary.returnPct)"
                  class="num"
                >{{ signed(item.summary.returnPct, '%') || '0.00%' }}</Badge>
                <Badge v-else tone="flat" class="num">收益 —</Badge>
                <span class="num text-[11.5px] text-ink-3">
                  {{ item.shares > 0 ? `持仓 ${item.shares} 股 · 现金 ${formatMoney(item.cash)}` : `空仓 · 现金 ${formatMoney(item.cash)}` }}
                </span>
              </p>
            </template>

            <!-- 最近动态：一句人话（后端 sentence 已是口径统一的白话） -->
            <p v-if="item.lastSettlement" class="mt-3 line-clamp-2 border-t border-dashed border-border pt-2.5 text-[12px] leading-relaxed text-ink-2">
              {{ item.lastSettlement.sentence }}
            </p>
            <p v-else-if="item.evaluated" class="mt-3 border-t border-dashed border-border pt-2.5 text-[12px] text-ink-3">
              还没有结算记录（痕迹共 {{ item.traceCount }} 条）
            </p>
            <p v-if="configUpdatedNote(item)" class="mt-1.5 text-[11.5px] text-warn">{{ configUpdatedNote(item) }}</p>
          </button>

          <!-- 操作行：弱化到底部，与自选卡一致 -->
          <div class="mt-3 flex gap-1 border-t border-border pt-2.5">
            <button
              type="button"
              class="rounded-md px-2 py-1 text-[12px] text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
              @click="goDetail(item.strategyId)"
            >详情</button>
            <button
              type="button"
              class="rounded-md px-2 py-1 text-[12px] text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
              @click="openEdit(item.strategyId)"
            >调整</button>
            <button
              type="button"
              class="rounded-md px-2 py-1 text-[12px] text-ink-2 transition-colors hover:bg-up-soft hover:text-up"
              :disabled="actionId === item.strategyId"
              @click="togglePaper(item)"
            >{{ item.paperEnabled ? '停止' : '启动' }}</button>
          </div>

          <!-- 最近报告 + 哨兵观察（默认收起，点开看全文） -->
          <details v-if="reportOf(item.strategyId)" class="mt-1.5">
            <summary class="flex cursor-pointer list-none items-center gap-2 text-[12px] text-ink-2 [&::-webkit-details-marker]:hidden">
              <span class="text-ink-3 transition-transform duration-150 [details[open]_&]:rotate-90">▸</span>
              <span>最近报告</span>
              <span class="num text-ink-3">{{ reportOf(item.strategyId).reportDate }}</span>
              <span v-if="reportOf(item.strategyId).sentinels.length" class="text-warn">哨兵 {{ reportOf(item.strategyId).sentinels.length }} 条</span>
            </summary>
            <pre class="my-2 whitespace-pre-wrap break-words rounded-lg bg-surface-2 p-3 font-mono text-[12px] leading-6 text-ink">{{ reportOf(item.strategyId).report || reportOf(item.strategyId).briefing || '这一天的结算还没跑：交易日 15:30 结算后这里会有内容。' }}</pre>
            <ul v-if="reportOf(item.strategyId).sentinels.length">
              <li v-for="sen in reportOf(item.strategyId).sentinels" :key="sen.at" class="border-t border-border py-1.5 text-[12px]">
                <span class="mr-2 text-[11px] font-semibold text-warn">{{ sen.label }}</span>
                <span class="num text-[11px] text-ink-3">{{ dateTime(sen.at) }}</span>
                <p class="mt-0.5 leading-relaxed text-ink-2">{{ sen.evidence }}</p>
              </li>
            </ul>
          </details>
        </li>
      </ul>
    </section>

    <!-- ③ 决策对账：智囊比规则准多少。专业视角，默认收起 -->
    <details
      v-if="!loading && quality && (qualityEvaluated > 0 || qualityPendingCount > 0)"
      class="mt-5 rounded-xl border border-border bg-surface p-5"
    >
      <summary class="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 [&::-webkit-details-marker]:hidden">
        <span class="text-[15px] font-semibold text-ink">决策对账：智囊 vs 规则谁更准</span>
        <span class="num text-[12px] text-ink-3">{{ qualitySummary }}</span>
        <span class="text-ink-3 transition-transform duration-150 [details[open]_&]:rotate-90">▸</span>
      </summary>
      <p class="mb-3 mt-2.5 max-w-[75ch] text-[12.5px] leading-relaxed text-ink-2">
        按「方向对错」对账（买入看涨对、卖出看跌对），不是盈亏——盈亏看上面各策略的曲线。
        每笔成交 {{ quality.horizonDays }} 个交易日后回头看，还没走满的不计入。
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
    </details>

    <StrategyEditSheet :open="editingId !== null" :strategy-id="editingId" @close="editingId = null" @saved="onEditSaved" />
  </div>
</template>
