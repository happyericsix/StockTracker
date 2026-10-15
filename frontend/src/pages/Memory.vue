<script setup>
/**
 * 记忆管理台（2026-10-15 分区化 + 白盒化）。
 *
 * 分区：记忆按"域"分开治理 —— 「助手与我」是对话域（偏好/持仓/对话经验/摘要），
 * 「模拟盘策略」是每条策略独立的域（运行结论/周反思教训），策略之间互不串用，
 * 委员会回读只取本策略的经验（后端 scope 过滤，这里只是如实展示）。
 * 白盒：每条记忆带"查看来源"——点开就是产生它的原始对话/事件，AI 为什么记住这件事
 * 可以亲手核对；教训展示 LLM 原句与当时的上下文，不再是无头无尾的规范化键。
 */
import { computed, onMounted, nextTick, ref } from 'vue'
import { useRouter } from 'vue-router'
import InfoTip from '../components/InfoTip.vue'
import Badge from '../components/ui/badge.vue'
import Button from '../components/ui/button.vue'
import Skeleton from '../components/ui/skeleton.vue'
import {
  getMemoryOverview, listMemoryFacts, listMemoryPersona, listMemoryLessons,
  listMemoryEpisodes, retractFact, activateLesson, retireLesson, fetchMemorySources,
} from '../api/memory.js'

const router = useRouter()

const loading = ref(true)
const error = ref('')
const status = ref('')
const overview = ref(null)
const persona = ref([])
const facts = ref([])
const lessons = ref([])
const episodes = ref([])

const showAllEpisodes = ref(false)
const pendingRetractId = ref(null)
const busyKey = ref('')
const confirmButton = ref(null)

const errorMessage = (e, fallback) => e?.response?.data?.message || fallback

// ---------- 来源抽屉（记忆白盒）：同屏只开一个，按条目键缓存展开状态 ----------
const sourcesKey = ref('')
const sourcesLoading = ref(false)
const sourcesItems = ref([])

const toggleSources = async (key, ids) => {
  const list = Array.isArray(ids) ? ids.filter((n) => Number.isFinite(n)) : []
  if (!list.length) return
  if (sourcesKey.value === key) {
    sourcesKey.value = ''
    sourcesItems.value = []
    return
  }
  sourcesKey.value = key
  sourcesLoading.value = true
  sourcesItems.value = []
  try {
    const res = await fetchMemorySources(list)
    sourcesItems.value = Array.isArray(res.data) ? res.data : []
  } catch {
    sourcesItems.value = []
  } finally {
    sourcesLoading.value = false
  }
}

const SPEAKER_LABELS = { chat_user: '你', chat_bot: '助手', strategy: '系统', system: '系统', tool_result: '工具' }
const speakerOf = (item) => SPEAKER_LABELS[item.kind] || item.kind || '记录'
const timeOf = (item) => (item.occurredAt ? String(item.occurredAt).slice(0, 16).replace('T', ' ') : '')
const hasSource = (item) => Array.isArray(item.sourceEventIds) && item.sourceEventIds.length > 0
const hasEvidence = (lesson) => Array.isArray(lesson.evidenceEventIds) && lesson.evidenceEventIds.length > 0

// ---------- 教训详情展开（当时的情况 / 尝试过什么） ----------
const lessonDetailKey = ref('')
const toggleLessonDetail = (lesson) => {
  lessonDetailKey.value = lessonDetailKey.value === `lesson-${lesson.id}` ? '' : `lesson-${lesson.id}`
}
const contextEntries = (lesson) => {
  const ctx = lesson.context
  if (!ctx || typeof ctx !== 'object') return []
  return Object.entries(ctx).filter(([, v]) => v !== null && v !== '')
}

// ---------- 分区 ----------
const STRATEGY_SCOPE_RE = /^strategy:(\d+)$/
const scopeStrategyId = (scope) => {
  const m = String(scope || '').match(STRATEGY_SCOPE_RE)
  return m ? m[1] : null
}

/** 对话域的经验：scope 为 chat（或历史行的空值） */
const chatLessons = computed(() => lessons.value.filter((l) => !scopeStrategyId(l.scope)))

/** 模拟盘策略分区：事实按 subject=strategy:N、经验按 scope=strategy:N 聚到一起 */
const strategySections = computed(() => {
  const map = new Map()
  const bucketOf = (id) => {
    if (!map.has(id)) map.set(id, { id, decisions: [], observations: [], lessons: [] })
    return map.get(id)
  }
  for (const fact of facts.value) {
    const id = scopeStrategyId(fact.subject)
    if (!id) continue
    const bucket = fact.factType === 'decision' ? 'decisions' : 'observations'
    bucketOf(id)[bucket].push(fact)
  }
  for (const lesson of lessons.value) {
    const id = scopeStrategyId(lesson.scope)
    if (!id) continue
    bucketOf(id).lessons.push(lesson)
  }
  return [...map.values()]
})

// ---------- 事实展示（沿用旧口径，叠加分区过滤） ----------
const PREDICATE_LABELS = {
  stop_loss_pct: '止损', take_profit_pct: '止盈', trailing_stop_pct: '跟踪止盈',
  risk_preference: '风险偏好', position_size: '仓位', holding_cost: '持仓成本',
  holding: '持有', watch_reason: '关注理由', investment_horizon: '投资周期',
  capital: '资金量', trading_frequency: '交易频率', sector_preference: '板块偏好',
  investing_style: '投资风格', excluded_subject: '不参与', target_return: '目标收益',
  max_drawdown_tolerance: '最大回撤容忍', learning_goal: '学习目标', purpose: '用途',
  backtest_total_return_pct: '回测总收益', backtest_buy_and_hold_return_pct: '回测同期买入持有收益',
  backtest_excess_pct: '回测超额收益', backtest_max_drawdown_pct: '回测最大回撤',
  backtest_sharpe: '回测夏普比率', backtest_win_rate_pct: '回测胜率',
  backtest_trade_count: '回测交易笔数', backtest_at: '回测时间',
  paper_equity: '模拟盘净值', paper_cash: '模拟盘可用现金', paper_shares: '模拟盘持仓股数',
  paper_return_pct: '模拟盘收益率', paper_last_eval_at: '模拟盘最近结算',
  paper_max_drawdown_pct: '模拟盘最大回撤', paper_flat_days: '模拟盘连续空仓天数',
  verify_at: '验证时间', verify_valid_cells_count: '验证有效样本数',
  verify_beat_buy_and_hold_count: '跑赢买入持有的样本数', verify_avg_excess_pct: '验证平均超额收益',
  verify_fee_drag_pct: '验证摩擦损耗占比', verify_engine_version: '验证引擎版本',
  expectation_at: '预期登记时间', expectation_metric: '预期度量', expectation_threshold: '预期门槛',
  expectation_deadline: '预期到期日', expectation_status: '预期状态',
  expectation_outcome: '到期实际结果', expectation_evaluated_at: '预期回填时间',
}

const predicateLabel = (fact) =>
  fact.predicateLabel || PREDICATE_LABELS[fact.predicate] || fact.predicate || '未命名'

const factLineText = (fact) => {
  const name = factName(fact)
  return name ? `${name}：${factValue(fact)}` : String(factValue(fact) ?? '')
}

const factValue = (fact) => fact.objectLabel || fact.object

const factName = (fact) => {
  const subject = fact.subjectLabel !== undefined && fact.subjectLabel !== null
    ? String(fact.subjectLabel).trim()
    : String(fact.subject || '').trim()
  const bare = !subject || subject.toLowerCase() === 'user'
  if (fact.predicate === 'run_summary') return bare ? '' : subject
  const label = predicateLabel(fact)
  return bare ? label : `${subject} ${label}`
}

const shortDate = (value) => (value ? String(value).slice(0, 10) : '')

/** 事实的时间与来源注脚：变更链必须展示 */
const factNotes = (fact) => {
  const notes = []
  const when = shortDate(fact.recordedAt) || shortDate(fact.validFrom)
  if (when) notes.push(when)
  if (fact.previousValue) notes.push(`此前为 ${withUnit(fact, fact.previousValue)}`)
  if (fact.rawTimePhrase) notes.push(`你当时说的是“${fact.rawTimePhrase}”`)
  if (fact.dataAsOf) notes.push(`数据截至 ${shortDate(fact.dataAsOf)}`)
  return notes.join(' · ')
}

const withUnit = (fact, value) => {
  const text = String(value ?? '').trim()
  if (!text) return value
  return String(fact.predicate || '').endsWith('_pct') && !isNaN(Number(text))
    ? `${text}%`
    : value
}

/** 低置信度且未经确认 = 模型推断，不是用户说过的话 */
const isInferred = (item) => !item.confirmed && Number(item.confidence ?? 1) < 0.6
const isSystemObserved = (fact) => fact.provenance === 'system'

const personaIds = computed(() => new Set(persona.value.map((f) => f.id)))

const chatFacts = computed(() => facts.value.filter((fact) => {
  if (personaIds.value.has(fact.id)) return false
  return !scopeStrategyId(fact.subject)
}))
const preferenceFacts = computed(() => chatFacts.value.filter((f) => ['preference', 'constraint', 'goal'].includes(f.factType)))
const holdingFacts = computed(() => chatFacts.value.filter((f) => f.factType === 'holding'))
const chatDecisionFacts = computed(() => chatFacts.value.filter((f) => f.factType === 'decision'))
const chatObservationFacts = computed(() => chatFacts.value.filter((f) => (f.factType || 'observation') === 'observation'))
const showObservations = ref(false)

const visibleEpisodes = computed(() => (showAllEpisodes.value ? episodes.value : episodes.value.slice(0, 3)))

/** 接口最多 50 条：必须说明"还有多少没显示"，静默截断会被以为记忆丢了 */
const truncatedFacts = computed(() => {
  const total = Number(overview.value?.facts?.visibleActive ?? facts.value.length)
  return Math.max(0, total - facts.value.length)
})

const lessonStatusLabel = (lesson) => ({ active: '已确认', retired: '已停用' }[lesson.status] || '待确认')
const scopeLabel = (lesson) => {
  const id = scopeStrategyId(lesson.scope)
  return id ? `策略 #${id}` : ''
}

const load = async () => {
  loading.value = true
  error.value = ''
  try {
    const [overviewRes, personaRes, factsRes, lessonsRes, episodesRes] = await Promise.all([
      getMemoryOverview(), listMemoryPersona(), listMemoryFacts({ limit: 50 }),
      listMemoryLessons(), listMemoryEpisodes(),
    ])
    overview.value = overviewRes.data || null
    persona.value = Array.isArray(personaRes.data) ? personaRes.data : []
    facts.value = Array.isArray(factsRes.data) ? factsRes.data : []
    lessons.value = Array.isArray(lessonsRes.data) ? lessonsRes.data : []
    episodes.value = Array.isArray(episodesRes.data) ? episodesRes.data : []
  } catch (e) {
    error.value = errorMessage(e, '记忆加载失败，请检查网络后重试')
    overview.value = null
    persona.value = []
    facts.value = []
    lessons.value = []
    episodes.value = []
  } finally {
    loading.value = false
  }
}

// 撤回 = 改变助手以后回答的依据：就地展开确认（焦点交给确认按钮 + 双击不算确认）
const setConfirmButton = (el) => {
  if (el) confirmButton.value = el
}

const askRetract = async (fact) => {
  pendingRetractId.value = fact.id
  await nextTick()
  confirmButton.value?.focus()
}

const cancelRetract = () => {
  pendingRetractId.value = null
}

const confirmRetract = (fact, event) => {
  if (event && event.detail > 1) return
  doRetract(fact)
}

const doRetract = async (fact) => {
  pendingRetractId.value = null
  error.value = ''
  busyKey.value = `fact-${fact.id}`
  try {
    await retractFact(fact.id)
    status.value = `已忘记「${factName(fact) || factValue(fact)}」`
    await load()
  } catch (e) {
    error.value = errorMessage(e, '操作失败，请稍后重试')
  } finally {
    busyKey.value = ''
  }
}

const changeLessonStatus = async (lesson, action) => {
  error.value = ''
  busyKey.value = `lesson-${lesson.id}`
  try {
    if (action === 'activate') {
      await activateLesson(lesson.id)
      status.value = `已确认「${lesson.symptom}」为可信做法`
    } else {
      await retireLesson(lesson.id)
      status.value = `已停用「${lesson.symptom}」`
    }
    await load()
  } catch (e) {
    error.value = errorMessage(e, '操作失败，请稍后重试')
  } finally {
    busyKey.value = ''
  }
}

const goAssistant = () => router.push('/assistant')
onMounted(load)
</script>

<template>
  <div>
    <div class="mb-4">
      <h1 class="flex items-center gap-1.5 text-[20px] font-semibold text-ink">
        记忆
        <InfoTip label="记忆说明" text="记忆分两个域：「助手与我」是你和聊天助手的对话沉淀（随时可忘记任何一条）；「模拟盘策略」是每条策略独立的运行结论与经验，策略之间互不串用。每条记忆都可以点「查看来源」核对原始对话。" />
      </h1>
    </div>

    <p class="mb-3 min-h-5 text-[13px] text-down" role="status">{{ status }}</p>
    <p v-if="error" class="mb-3 text-[13px] text-up" role="alert">
      {{ error }}
      <button type="button" class="ml-1 text-accent underline underline-offset-2" @click="load">重新加载</button>
    </p>

    <div v-if="loading" class="space-y-4">
      <div class="flex gap-3">
        <Skeleton v-for="i in 3" :key="i" width="30%" height="72px" />
      </div>
      <div v-for="i in 2" :key="i" class="space-y-3 rounded-xl border border-border bg-surface p-5">
        <Skeleton width="25%" height="16px" />
        <Skeleton width="80%" height="13px" />
        <Skeleton width="65%" height="13px" />
      </div>
    </div>

    <template v-else-if="!error">
      <!-- 概览 -->
      <section v-if="overview" class="num mb-5 grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(140px,1fr))]" aria-label="记忆概览">
        <div class="flex flex-col gap-0.5 rounded-xl border border-border bg-surface p-4">
          <span class="text-[22px] font-semibold text-ink">{{ overview.facts?.visibleActive ?? overview.facts?.active ?? 0 }}</span>
          <span class="text-[12px] text-ink-2">条你告诉助手的</span>
        </div>
        <div class="flex flex-col gap-0.5 rounded-xl border border-border bg-surface p-4">
          <span class="text-[22px] font-semibold text-ink">{{ overview.lessons?.pending ?? 0 }}</span>
          <span class="text-[12px] text-ink-2">条经验待确认</span>
        </div>
        <div class="flex flex-col gap-0.5 rounded-xl border border-border bg-surface p-4">
          <span class="text-[22px] font-semibold text-ink">{{ overview.episodes ?? 0 }}</span>
          <span class="text-[12px] text-ink-2">段对话摘要</span>
        </div>
      </section>

      <!-- ==================== 分区一：助手与我（对话域） ==================== -->
      <section class="mb-2 mt-6 flex items-center gap-2">
        <h2 class="text-[16px] font-semibold text-ink">助手与我</h2>
        <span class="text-[12px] text-ink-3">你和聊天助手的对话沉淀，只影响对话</span>
      </section>

      <!-- 长期画像 -->
      <section v-if="persona.length" class="mb-5 rounded-xl border border-border bg-surface p-5">
        <h3 class="text-[15px] font-semibold text-ink">长期画像</h3>
        <p class="mb-3 text-[12px] leading-relaxed text-ink-3">这几条是每次对话都会优先参考的稳定信息，其余的记在下面。</p>
        <ul>
          <li v-for="fact in persona" :key="`persona-${fact.id}`" class="border-t border-border py-2.5 first:border-t-0 first:pt-0">
            <p class="text-[13.5px] leading-relaxed text-ink">
              <strong class="font-semibold">{{ factName(fact) }}</strong>：{{ factValue(fact) }}
              <Badge v-if="isInferred(fact)" tone="warn" class="ml-1">推断</Badge>
              <Badge v-if="isSystemObserved(fact)" tone="neutral" class="ml-1">系统观测</Badge>
            </p>
            <span v-if="factNotes(fact)" class="num mt-0.5 block text-[12px] text-ink-3">{{ factNotes(fact) }}</span>
          </li>
        </ul>
      </section>

      <!-- 对话域三区：偏好 / 持仓 / 结论 -->
      <section v-for="sec in [
          { key: 'preference', title: '我的偏好', hint: '你告诉过助手的习惯、约束和目标。助手回答时会参考它们。', list: preferenceFacts },
          { key: 'holding', title: '持仓与关注', hint: '你提到持有的标的和关注的理由。', list: holdingFacts },
          { key: 'decision', title: '结论', hint: '对话里产生的一句话结论（助手自动记录）。', list: chatDecisionFacts },
        ]" v-show="sec.list.length" :key="sec.key" class="mb-5 rounded-xl border border-border bg-surface p-5">
        <h3 class="text-[15px] font-semibold text-ink">{{ sec.title }}</h3>
        <p class="mb-3 text-[12px] leading-relaxed text-ink-3">{{ sec.hint }}</p>
        <ul>
          <li v-for="fact in sec.list" :key="fact.id" class="border-t border-border py-2.5 first:border-t-0 first:pt-0">
            <div class="flex items-start justify-between gap-3">
              <p class="min-w-0 text-[13.5px] leading-relaxed text-ink">
                <strong class="font-semibold">{{ factLineText(fact) }}</strong>
                <Badge v-if="isInferred(fact)" tone="warn" class="ml-1">推断</Badge>
                <Badge v-if="isSystemObserved(fact) && fact.predicate !== 'run_summary'" tone="neutral" class="ml-1">系统观测</Badge>
              </p>
              <div class="flex shrink-0 items-center gap-1.5">
                <Button
                  v-if="hasSource(fact)"
                  size="sm"
                  variant="ghost"
                  @click="toggleSources(`fact-${fact.id}`, fact.sourceEventIds)"
                >{{ sourcesKey === `fact-${fact.id}` ? '收起来源' : '查看来源' }}</Button>
                <Button
                  v-if="pendingRetractId !== fact.id"
                  size="sm"
                  variant="ghost"
                  :disabled="busyKey === `fact-${fact.id}`"
                  @click="askRetract(fact)"
                >忘记</Button>
              </div>
            </div>
            <span v-if="factNotes(fact)" class="num mt-0.5 block text-[12px] text-ink-3">{{ factNotes(fact) }}</span>
            <div v-if="pendingRetractId === fact.id" class="mt-2 flex flex-wrap items-center gap-2">
              <span class="text-[12px] text-ink-2">忘记后助手不再使用这条记忆，历史记录仍保留。</span>
              <Button :ref="setConfirmButton" size="sm" variant="danger" :disabled="busyKey === `fact-${fact.id}`" @click="confirmRetract(fact, $event)">忘记这条</Button>
              <Button size="sm" variant="ghost" @click="cancelRetract">取消</Button>
            </div>
            <!-- 来源抽屉：产生这条记忆的原始对话 -->
            <div v-if="sourcesKey === `fact-${fact.id}`" class="mt-2.5 rounded-lg border border-border bg-surface-2 p-3">
              <p v-if="sourcesLoading" class="text-[12px] text-ink-3">正在取原始对话…</p>
              <p v-else-if="!sourcesItems.length" class="text-[12px] text-ink-3">这条记忆没有留存到具体的对话原文（可能来自系统客观记录）。</p>
              <ul v-else class="space-y-2">
                <li v-for="src in sourcesItems" :key="src.id" class="text-[12.5px] leading-relaxed text-ink-2">
                  <span class="num mr-1.5 text-ink-3">{{ timeOf(src) }}</span>
                  <strong class="text-ink">{{ speakerOf(src) }}</strong>：{{ src.content }}
                </li>
              </ul>
            </div>
          </li>
        </ul>
      </section>

      <!-- 对话域的经验 -->
      <section class="mb-5 rounded-xl border border-border bg-surface p-5">
        <h3 class="text-[15px] font-semibold text-ink">经验</h3>
        <p class="mb-3 text-[12px] leading-relaxed text-ink-3">
          助手在解决问题时总结的做法，只作参考：确认后助手会更信任它，停用后不再使用。
        </p>

        <div v-if="chatLessons.length === 0" class="flex flex-col items-start gap-1.5">
          <p class="text-[14px] font-semibold text-ink">还没有总结出经验</p>
          <p class="text-[12.5px] leading-relaxed text-ink-2">助手在遇到问题并解决之后，会把做法记在这里，等你确认。</p>
        </div>

        <ul v-else>
          <li v-for="lesson in chatLessons" :key="lesson.id" class="border-t border-border py-3 first:border-t-0 first:pt-0">
            <div class="mb-1.5 flex flex-wrap items-center gap-2">
              <Badge :tone="lesson.status === 'active' ? 'down' : 'neutral'">{{ lessonStatusLabel(lesson) }}</Badge>
              <span v-if="lesson.occurrences > 1" class="num text-[12px] text-ink-3">出现过 {{ lesson.occurrences }} 次</span>
              <span v-if="lesson.promotionSuggested" class="text-[12px] text-warn">可考虑固化成固定流程</span>
            </div>
            <p class="mb-1 text-[13px] leading-relaxed text-ink"><span class="mr-1.5 inline-block min-w-[2.2rem] text-[12px] text-ink-3">症状</span>{{ lesson.symptom }}</p>
            <p class="text-[13px] leading-relaxed text-ink"><span class="mr-1.5 inline-block min-w-[2.2rem] text-[12px] text-ink-3">做法</span>{{ lesson.reusableRule || lesson.resolution }}</p>

            <!-- 详情展开：当时的情况 / 尝试过什么（记忆白盒） -->
            <div v-if="lessonDetailKey === `lesson-${lesson.id}`" class="mt-2 rounded-lg border border-border bg-surface-2 p-3 text-[12.5px] leading-relaxed text-ink-2">
              <p v-if="contextEntries(lesson).length" class="mb-1.5">
                <span class="text-ink-3">当时的情况：</span>
                <span v-for="([k, v]) in contextEntries(lesson)" :key="k" class="mr-2">{{ k }}：{{ v }}</span>
              </p>
              <p v-if="lesson.attempts?.length" class="mb-1.5">
                <span class="text-ink-3">尝试过：</span>{{ lesson.attempts.join('；') }}
              </p>
              <p v-if="lesson.resolution"><span class="text-ink-3">解决：</span>{{ lesson.resolution }}</p>
              <p v-if="!contextEntries(lesson).length && !lesson.attempts?.length && !lesson.resolution" class="text-ink-3">这条经验没有留存的上下文（早期生成的经验）。</p>
            </div>

            <p class="num mt-1 text-[12px] text-ink-3">
              类型：{{ lesson.taskTypeLabel || lesson.taskType }}
              <span v-if="lesson.recordedAt"> · {{ shortDate(lesson.recordedAt) }}</span>
            </p>
            <div class="mt-2 flex flex-wrap gap-2">
              <Button
                v-if="hasEvidence(lesson) || contextEntries(lesson).length || lesson.attempts?.length"
                size="sm"
                variant="ghost"
                @click="toggleLessonDetail(lesson); hasEvidence(lesson) && toggleSources(`lesson-${lesson.id}`, lesson.evidenceEventIds)"
              >{{ lessonDetailKey === `lesson-${lesson.id}` ? '收起详情' : '详情与来源' }}</Button>
              <Button
                v-if="lesson.status !== 'active'"
                size="sm"
                variant="primary"
                :loading="busyKey === `lesson-${lesson.id}`"
                @click="changeLessonStatus(lesson, 'activate')"
              >确认可信</Button>
              <Button
                v-if="lesson.status !== 'retired'"
                size="sm"
                :disabled="busyKey === `lesson-${lesson.id}`"
                @click="changeLessonStatus(lesson, 'retire')"
              >停用</Button>
              <Button
                v-else
                size="sm"
                :disabled="busyKey === `lesson-${lesson.id}`"
                @click="changeLessonStatus(lesson, 'activate')"
              >恢复使用</Button>
            </div>

            <!-- 来源抽屉：产生这条教训的原始对话 -->
            <div v-if="hasEvidence(lesson) && lessonDetailKey === `lesson-${lesson.id}` && sourcesKey === `lesson-${lesson.id}`" class="mt-2.5 rounded-lg border border-border bg-surface-2 p-3">
              <p v-if="sourcesLoading" class="text-[12px] text-ink-3">正在取原始对话…</p>
              <p v-else-if="!sourcesItems.length" class="text-[12px] text-ink-3">没有留存到具体的对话原文。</p>
              <ul v-else class="space-y-2">
                <li v-for="src in sourcesItems" :key="src.id" class="text-[12.5px] leading-relaxed text-ink-2">
                  <span class="num mr-1.5 text-ink-3">{{ timeOf(src) }}</span>
                  <strong class="text-ink">{{ speakerOf(src) }}</strong>：{{ src.content }}
                </li>
              </ul>
            </div>
          </li>
        </ul>
      </section>

      <!-- 对话域的助手观察（默认折叠：机器观测不占用户门面；策略域的观测在各自分区） -->
      <section v-if="chatObservationFacts.length" class="mb-5 rounded-xl border border-border bg-surface p-5">
        <h3 class="text-[15px] font-semibold text-ink">助手观察</h3>
        <p class="mb-3 text-[12px] leading-relaxed text-ink-3">助手自动记录的运行事实，仅供对话参考。</p>
        <Button size="sm" variant="ghost" :aria-expanded="showObservations" @click="showObservations = !showObservations">
          {{ showObservations ? '收起' : `展开 ${chatObservationFacts.length} 条观察` }}
        </Button>
        <ul v-if="showObservations" class="mt-3">
          <li v-for="fact in chatObservationFacts" :key="fact.id" class="border-t border-border py-2.5 first:border-t-0 first:pt-0">
            <div class="flex items-start justify-between gap-3">
              <p class="min-w-0 text-[13.5px] leading-relaxed text-ink">
                <strong class="font-semibold">{{ factLineText(fact) }}</strong>
                <Badge v-if="isInferred(fact)" tone="warn" class="ml-1">推断</Badge>
                <Badge v-if="isSystemObserved(fact) && fact.predicate !== 'run_summary'" tone="neutral" class="ml-1">系统观测</Badge>
              </p>
              <Button
                v-if="pendingRetractId !== fact.id"
                size="sm"
                variant="ghost"
                class="shrink-0"
                :disabled="busyKey === `fact-${fact.id}`"
                @click="askRetract(fact)"
              >忘记</Button>
            </div>
            <span v-if="factNotes(fact)" class="num mt-0.5 block text-[12px] text-ink-3">{{ factNotes(fact) }}</span>
            <div v-if="pendingRetractId === fact.id" class="mt-2 flex flex-wrap items-center gap-2">
              <span class="text-[12px] text-ink-2">忘记后助手不再使用这条记忆，历史记录仍保留。</span>
              <Button :ref="setConfirmButton" size="sm" variant="danger" :disabled="busyKey === `fact-${fact.id}`" @click="confirmRetract(fact, $event)">忘记这条</Button>
              <Button size="sm" variant="ghost" @click="cancelRetract">取消</Button>
            </div>
          </li>
        </ul>
      </section>

      <!-- 对话摘要 -->
      <section class="mb-5 rounded-xl border border-border bg-surface p-5">
        <h3 class="text-[15px] font-semibold text-ink">对话摘要</h3>
        <p class="mb-3 text-[12px] leading-relaxed text-ink-3">助手每天会把当天的对话整理成一段摘要，下次对话开始时参考它。</p>

        <div v-if="episodes.length === 0" class="flex flex-col items-start gap-1.5">
          <p class="text-[14px] font-semibold text-ink">还没有对话摘要</p>
          <p class="text-[12.5px] leading-relaxed text-ink-2">聊完一天之后，摘要会在后台自动生成。</p>
        </div>

        <template v-else>
          <ul>
            <li v-for="episode in visibleEpisodes" :key="episode.sessionKey" class="border-t border-border py-2.5 first:border-t-0 first:pt-0">
              <span class="num text-[12px] text-ink-3">{{ String(episode.sessionKey).split(':').pop() }}</span>
              <p class="mt-1 text-[13px] leading-relaxed text-ink">{{ episode.summary }}</p>
              <ul v-if="episode.keyPoints?.length" class="mt-1.5 list-disc pl-4.5 text-[12px] leading-relaxed text-ink-2">
                <li v-for="(point, index) in episode.keyPoints" :key="index">{{ point }}</li>
              </ul>
              <!-- 未定事项：系统自己标注的"还不确定的事"（白盒的另一半——知道自己不知道什么） -->
              <p v-if="episode.openQuestions?.length" class="mt-1.5 text-[12px] leading-relaxed text-ink-3">
                尚不确定：{{ episode.openQuestions.join('；') }}
              </p>
            </li>
          </ul>
          <Button
            v-if="episodes.length > 3"
            size="sm"
            variant="ghost"
            class="mt-3"
            :aria-expanded="showAllEpisodes"
            @click="showAllEpisodes = !showAllEpisodes"
          >
            {{ showAllEpisodes ? '收起' : `显示全部 ${episodes.length} 段` }}
          </Button>
        </template>
      </section>

      <div
        v-if="preferenceFacts.length + holdingFacts.length === 0 && chatDecisionFacts.length === 0 && persona.length === 0"
        class="mb-5 flex flex-col items-start gap-1.5 rounded-xl border border-border bg-surface p-5"
      >
        <p class="text-[14px] font-semibold text-ink">还没有可显示的长期记忆</p>
        <p class="text-[12.5px] leading-relaxed text-ink-2">跟助手聊几句，你提到的重要信息（偏好、约束、持仓）会自动沉淀到这里。</p>
        <Button variant="primary" size="sm" class="mt-1.5" @click="goAssistant">去和助手聊聊</Button>
      </div>

      <p v-if="truncatedFacts > 0" class="mb-5 text-[12px] leading-relaxed text-ink-3">
        只显示了 {{ facts.length }} 条（共 {{ overview?.facts?.visibleActive ?? facts.length }} 条），这里优先显示最近和最重要的。
      </p>

      <!-- ==================== 分区二：模拟盘策略（每策略独立域） ==================== -->
      <section class="mb-2 mt-6 flex items-center gap-2">
        <h2 class="text-[16px] font-semibold text-ink">模拟盘策略</h2>
        <span class="text-[12px] text-ink-3">各策略独立记账：运行结论、周反思教训只进自己的策略，委员会互相看不见</span>
      </section>

      <div v-if="strategySections.length === 0" class="mb-5 rounded-xl border border-border bg-surface p-5">
        <p class="text-[14px] font-semibold text-ink">还没有策略域的记忆</p>
        <p class="mt-1 text-[12.5px] leading-relaxed text-ink-2">
          模拟盘跑起来之后，回测/验证结论、运行里程碑和每周反思提炼的教训会沉淀到对应策略名下——
          策略 A 的委员会不会读到策略 B 的教训。
        </p>
      </div>

      <section v-for="group in strategySections" :key="group.id" class="mb-5 rounded-xl border border-border bg-surface p-5">
        <h3 class="text-[15px] font-semibold text-ink">策略 #{{ group.id }}</h3>

        <!-- 该策略的运行结论 -->
        <template v-if="group.decisions.length">
          <p class="mb-2 mt-2 text-[12px] text-ink-3">运行结论（回测 / 验证 / 模拟盘里程碑）</p>
          <ul>
            <li v-for="fact in group.decisions" :key="fact.id" class="border-t border-border py-2 first:border-t-0 first:pt-0">
              <div class="flex items-start justify-between gap-3">
                <p class="min-w-0 text-[13px] leading-relaxed text-ink">{{ factLineText(fact) }}</p>
                <Button
                  v-if="hasSource(fact)"
                  size="sm"
                  variant="ghost"
                  class="shrink-0"
                  @click="toggleSources(`fact-${fact.id}`, fact.sourceEventIds)"
                >{{ sourcesKey === `fact-${fact.id}` ? '收起来源' : '查看来源' }}</Button>
              </div>
              <span v-if="factNotes(fact)" class="num mt-0.5 block text-[12px] text-ink-3">{{ factNotes(fact) }}</span>
              <div v-if="sourcesKey === `fact-${fact.id}`" class="mt-2.5 rounded-lg border border-border bg-surface-2 p-3">
                <p v-if="sourcesLoading" class="text-[12px] text-ink-3">正在取原始记录…</p>
                <p v-else-if="!sourcesItems.length" class="text-[12px] text-ink-3">这条结论来自系统客观记录，没有对话原文。</p>
                <ul v-else class="space-y-2">
                  <li v-for="src in sourcesItems" :key="src.id" class="text-[12.5px] leading-relaxed text-ink-2">
                    <span class="num mr-1.5 text-ink-3">{{ timeOf(src) }}</span>
                    <strong class="text-ink">{{ speakerOf(src) }}</strong>：{{ src.content }}
                  </li>
                </ul>
              </div>
            </li>
          </ul>
        </template>

        <!-- 该策略的经验（周反思教训，scope 隔离） -->
        <template v-if="group.lessons.length">
          <p class="mb-2 mt-3 text-[12px] text-ink-3">该策略的经验（周反思提炼，只有它的委员会会读到）</p>
          <ul>
            <li v-for="lesson in group.lessons" :key="lesson.id" class="border-t border-border py-2.5 first:border-t-0 first:pt-0">
              <div class="mb-1 flex flex-wrap items-center gap-2">
                <Badge :tone="lesson.status === 'active' ? 'down' : 'neutral'">{{ lessonStatusLabel(lesson) }}</Badge>
                <span v-if="scopeLabel(lesson)" class="num text-[12px] text-ink-3">{{ scopeLabel(lesson) }}</span>
                <span v-if="lesson.occurrences > 1" class="num text-[12px] text-ink-3">出现过 {{ lesson.occurrences }} 次</span>
              </div>
              <p class="mb-1 text-[13px] leading-relaxed text-ink"><span class="mr-1.5 inline-block min-w-[2.2rem] text-[12px] text-ink-3">症状</span>{{ lesson.symptom }}</p>
              <p class="text-[13px] leading-relaxed text-ink"><span class="mr-1.5 inline-block min-w-[2.2rem] text-[12px] text-ink-3">做法</span>{{ lesson.reusableRule || lesson.resolution }}</p>
              <p class="num mt-1 text-[12px] text-ink-3">
                {{ lesson.taskTypeLabel || lesson.taskType }}
                <span v-if="lesson.recordedAt"> · {{ shortDate(lesson.recordedAt) }}</span>
              </p>
              <div class="mt-2 flex flex-wrap gap-2">
                <Button
                  v-if="lesson.status !== 'active'"
                  size="sm"
                  variant="primary"
                  :loading="busyKey === `lesson-${lesson.id}`"
                  @click="changeLessonStatus(lesson, 'activate')"
                >确认可信</Button>
                <Button
                  v-if="lesson.status !== 'retired'"
                  size="sm"
                  :disabled="busyKey === `lesson-${lesson.id}`"
                  @click="changeLessonStatus(lesson, 'retire')"
                >停用</Button>
                <Button
                  v-else
                  size="sm"
                  :disabled="busyKey === `lesson-${lesson.id}`"
                  @click="changeLessonStatus(lesson, 'activate')"
                >恢复使用</Button>
              </div>
            </li>
          </ul>
        </template>

        <!-- 该策略的机器观察（默认折叠） -->
        <template v-if="group.observations.length">
          <p class="mb-2 mt-3 text-[12px] text-ink-3">系统观测（{{ group.observations.length }} 条，策略运行的客观数字）</p>
          <ul class="grid gap-1 text-[12.5px] leading-relaxed text-ink-2 [grid-template-columns:repeat(auto-fit,minmax(240px,1fr))]">
            <li v-for="fact in group.observations" :key="fact.id">
              {{ factLineText(fact) }}
              <span v-if="fact.dataAsOf" class="num text-ink-3">（截至 {{ shortDate(fact.dataAsOf) }}）</span>
            </li>
          </ul>
        </template>
      </section>
    </template>
  </div>
</template>
