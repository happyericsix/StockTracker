<script setup>
import { computed, onMounted, nextTick, ref } from 'vue'
import { useRouter } from 'vue-router'
import InfoTip from '../components/InfoTip.vue'
import {
  getMemoryOverview,
  listMemoryFacts,
  listMemoryPersona,
  listMemoryLessons,
  listMemoryEpisodes,
  retractFact,
  activateLesson,
  retireLesson
} from '../api/memory.js'

const router = useRouter()

const loading = ref(true)
const error = ref('')
// 操作结果用 aria-live 播报（屏幕阅读器用户点完按钮需要知道发生了什么）
const status = ref('')
const overview = ref(null)
const persona = ref([])
const facts = ref([])
const lessons = ref([])
const episodes = ref([])

const showAllEpisodes = ref(false)
// 助手观察（系统遥测的可见部分：里程碑等）默认折叠 —— 机器观测不占用户门面
const showObservations = ref(false)
const pendingRetractId = ref(null)
const busyKey = ref('')
const confirmButton = ref(null)

// 只认后端返回的中文业务提示，其次是本地兜底。
// 不回落 e?.message —— 那是 axios 的英文原文（"Request failed with status code 500"）。
const errorMessage = (e, fallback) => e?.response?.data?.message || fallback

// 谓词是后端用来做"取代链"的稳定键（stop_loss_pct 之类），展示时给中文名。
// **后端的 predicateLabel 是权威**（Java 侧 MemoryDisplayLabels 统一维护，
// 含 25+ 个客观事实谓词）；下面这张表只服务旧数据/接口降级时的回退，
// 没有映射的键直接显示原文，好过显示"未知"。
const PREDICATE_LABELS = {
  stop_loss_pct: '止损',
  take_profit_pct: '止盈',
  trailing_stop_pct: '跟踪止盈',
  risk_preference: '风险偏好',
  position_size: '仓位',
  holding_cost: '持仓成本',
  holding: '持有',
  watch_reason: '关注理由',
  investment_horizon: '投资周期',
  capital: '资金量',
  trading_frequency: '交易频率',
  sector_preference: '板块偏好',
  investing_style: '投资风格',
  excluded_subject: '不参与',
  target_return: '目标收益',
  max_drawdown_tolerance: '最大回撤容忍',
  learning_goal: '学习目标',
  purpose: '用途',
  // —— 客观事实（系统观测），与 Java MemoryDisplayLabels 对齐 ——
  backtest_total_return_pct: '回测总收益',
  backtest_buy_and_hold_return_pct: '回测同期买入持有收益',
  backtest_excess_pct: '回测超额收益',
  backtest_max_drawdown_pct: '回测最大回撤',
  backtest_sharpe: '回测夏普比率',
  backtest_win_rate_pct: '回测胜率',
  backtest_trade_count: '回测交易笔数',
  backtest_at: '回测时间',
  paper_equity: '模拟盘净值',
  paper_cash: '模拟盘可用现金',
  paper_shares: '模拟盘持仓股数',
  paper_return_pct: '模拟盘收益率',
  paper_last_eval_at: '模拟盘最近结算',
  paper_max_drawdown_pct: '模拟盘最大回撤',
  paper_flat_days: '模拟盘连续空仓天数',
  verify_at: '验证时间',
  verify_valid_cells_count: '验证有效样本数',
  verify_beat_buy_and_hold_count: '跑赢买入持有的样本数',
  verify_avg_excess_pct: '验证平均超额收益',
  verify_fee_drag_pct: '验证摩擦损耗占比',
  verify_engine_version: '验证引擎版本',
  expectation_at: '预期登记时间',
  expectation_metric: '预期度量',
  expectation_threshold: '预期门槛',
  expectation_deadline: '预期到期日',
  expectation_status: '预期状态',
  expectation_outcome: '到期实际结果',
  expectation_evaluated_at: '预期回填时间'
}

const predicateLabel = (fact) =>
  fact.predicateLabel || PREDICATE_LABELS[fact.predicate] || fact.predicate || '未命名'
// 一条记忆的正文：键值类（"止损：5%"）与叙事类（"策略 #1" + 一句话结论）统一在这里
const factLineText = (fact) => {
  const name = factName(fact)
  return name ? `${name}：${factValue(fact)}` : String(factValue(fact) ?? '')
}

// 值的展示：后端 objectLabel 已带单位（"12.5%"）并翻译枚举值（"已达成"），
// 没有时回退原始值。
const factValue = (fact) => fact.objectLabel || fact.object

const factName = (fact) => {
  // 聚合叙事（run_summary）的值是完整一句话，前缀只保留主体（"策略 #1"），
  // 再叠"运行结论"会和句子里的"回测结论：""样本外验证："重复
  const subject = fact.subjectLabel !== undefined && fact.subjectLabel !== null
    ? String(fact.subjectLabel).trim()
    : String(fact.subject || '').trim()
  const bare = !subject || subject.toLowerCase() === 'user'
  if (fact.predicate === 'run_summary') return bare ? '' : subject
  const label = predicateLabel(fact)
  return bare ? label : `${subject} ${label}`
}

const shortDate = (value) => (value ? String(value).slice(0, 10) : '')

const formatTime = (iso) => {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleString('zh-CN', { hour12: false })
}

/** 事实的时间与来源注脚：变更链必须展示，否则用户看到"止损 5%"会以为系统搞错了 */
const factNotes = (fact) => {
  const notes = []
  const when = shortDate(fact.recordedAt) || shortDate(fact.validFrom)
  if (when) notes.push(when)
  if (fact.previousValue) notes.push(`此前为 ${withUnit(fact, fact.previousValue)}`)
  if (fact.rawTimePhrase) notes.push(`你当时说的是“${fact.rawTimePhrase}”`)
  if (fact.dataAsOf) notes.push(`数据截至 ${shortDate(fact.dataAsOf)}`)
  return notes.join(' · ')
}

/** 注脚里的旧值补单位（后端 objectLabel 只处理当前值，历史值在这里对齐口径） */
const withUnit = (fact, value) => {
  const text = String(value ?? '').trim()
  if (!text) return value
  return String(fact.predicate || '').endsWith('_pct') && !isNaN(Number(text))
    ? `${text}%`
    : value
}

/** 低置信度且未经用户确认的条目要标出来：那是模型的推断，不是用户说过的话 */
const isInferred = (item) =>
  !item.confirmed && Number(item.confidence ?? 1) < 0.6

/** 系统观测（回测/模拟盘/验证指标）：数值可靠但不是用户说的，标出来避免误解 */
const isSystemObserved = (fact) => fact.provenance === 'system'

const personaIds = computed(() => new Set(persona.value.map((f) => f.id)))

// 双层记忆分区（2026-10-09）：用户层按用途分三区，系统观察折叠。
// 后端已只回可见层（visibleOnly），这里的分组只是展示语义 ——
// 「哪部分是模拟盘、哪部分是聊天助手、哪部分是结论」一眼可辨。
const nonPersonaFacts = computed(() =>
  facts.value.filter((fact) => !personaIds.value.has(fact.id))
)

const preferenceFacts = computed(() => nonPersonaFacts.value.filter((f) =>
  ['preference', 'constraint', 'goal'].includes(f.factType)))

const holdingFacts = computed(() => nonPersonaFacts.value.filter((f) =>
  f.factType === 'holding'))

const decisionFacts = computed(() => nonPersonaFacts.value.filter((f) =>
  f.factType === 'decision'))

const observationFacts = computed(() => nonPersonaFacts.value.filter((f) =>
  (f.factType || 'observation') === 'observation'))

// 用户层计数："助手记住了你说的 N 件事"（偏好 + 持仓 + 结论 + 画像）
const userMemoryCount = computed(() =>
  preferenceFacts.value.length + holdingFacts.value.length
  + decisionFacts.value.length + persona.value.length)

const visibleEpisodes = computed(() =>
  showAllEpisodes.value ? episodes.value : episodes.value.slice(0, 3)
)

// 接口最多返回 30 条事实（那个上限是为了保护注入给模型的上下文）。
// 页面上必须说明"还有多少没显示" —— 静默截断会让用户以为记忆丢了。
const truncatedFacts = computed(() => {
  // 可见层口径：被隐藏的系统遥测不算"没显示的记忆"
  const total = Number(overview.value?.facts?.visibleActive ?? facts.value.length)
  return Math.max(0, total - facts.value.length)
})

const lessonStatusLabel = (lesson) =>
  ({ active: '已确认', retired: '已停用' }[lesson.status] || '待确认')

const load = async () => {
  loading.value = true
  error.value = ''
  try {
    const [overviewRes, personaRes, factsRes, lessonsRes, episodesRes] = await Promise.all([
      getMemoryOverview(),
      listMemoryPersona(),
      listMemoryFacts({ limit: 50 }),
      listMemoryLessons(),
      listMemoryEpisodes()
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

// 撤回会改变助手以后回答的依据，属于要先讲清后果的动作：
// 就地展开确认，而不是弹窗（项目里统一这么做，window.confirm 无法样式化）。
// 用函数 ref 记住确认按钮：同一时刻只渲染一个，v-for 里的字符串 ref 会变成数组。
const setConfirmButton = (el) => {
  if (el) confirmButton.value = el
}

const askRetract = async (fact) => {
  pendingRetractId.value = fact.id
  // 原按钮被替换后焦点会掉回 body，键盘用户会丢失位置
  await nextTick()
  confirmButton.value?.focus()
}

const cancelRetract = () => {
  pendingRetractId.value = null
}

const confirmRetract = (fact, event) => {
  // 鼠标连击的第二次点击（detail > 1）不算确认，避免习惯性双击把记忆撤回
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
  <div class="page">
    <div class="page-head">
      <h1>记忆 <InfoTip label="记忆说明" text="助手会记住你告诉它的偏好与持仓（你随时可以忘记任何一条），也会自动记录策略运行的关键结论。系统性的日常数据不占用这里的记忆，只在对话中被问到时查询。" /></h1>
    </div>

      <!-- 结果播报：稳定的空区域先渲染再更新文本，重复播报才可靠 -->
      <p class="status" role="status">{{ status }}</p>
      <p v-if="error" class="error" role="alert">
        {{ error }}
        <button type="button" class="inline-action" @click="load">重新加载</button>
      </p>

      <div v-if="loading" class="empty">加载中...</div>

      <template v-else-if="!error">
        <!-- 概览：三个数字，回答"到底记住了多少" -->
        <section v-if="overview" class="overview" aria-label="记忆概览">
          <div class="stat">
            <span class="stat-value num">{{ overview.facts?.visibleActive ?? overview.facts?.active ?? 0 }}</span>
            <span class="stat-label">条你告诉助手的</span>
          </div>
          <div class="stat">
            <span class="stat-value num">{{ overview.lessons?.pending ?? 0 }}</span>
            <span class="stat-label">条经验待确认</span>
          </div>
          <div class="stat">
            <span class="stat-value num">{{ overview.episodes ?? 0 }}</span>
            <span class="stat-label">段对话摘要</span>
          </div>
        </section>

        <!-- 长期画像：always-on 的一小块，与下面的列表不重复 -->
        <section v-if="persona.length" class="block">
          <h2>长期画像</h2>
          <p class="block-hint">这几条是每次对话都会优先参考的稳定信息，其余的记在下面。</p>
          <ul class="fact-list">
            <li v-for="fact in persona" :key="`persona-${fact.id}`" class="fact-item">
              <span class="fact-main">
                <strong>{{ factName(fact) }}</strong>：{{ factValue(fact) }}
                <span v-if="isInferred(fact)" class="tag tag-inferred">推断</span>
                <span v-if="isSystemObserved(fact)" class="tag tag-system">系统观测</span>
              </span>
              <span v-if="factNotes(fact)" class="fact-meta num">{{ factNotes(fact) }}</span>
            </li>
          </ul>
        </section>

        <!-- 用户层：按用途分三区 —— 哪些是偏好、哪些是持仓、哪些是策略结论，一眼可辨 -->
        <section v-if="preferenceFacts.length" class="block">
          <h2>我的偏好</h2>
          <p class="block-hint">你告诉过助手的习惯、约束和目标。助手回答时会参考它们。</p>
          <ul class="fact-list">
            <li v-for="fact in preferenceFacts" :key="fact.id" class="fact-item"><div class="fact-row">
                <span class="fact-main">
                  <strong>{{ factLineText(fact) }}</strong>
                  <span v-if="isInferred(fact)" class="tag tag-inferred">推断</span>
                  <span v-if="isSystemObserved(fact) && fact.predicate !== 'run_summary'" class="tag tag-system">系统观测</span>
                </span>
                <button
                  v-if="pendingRetractId !== fact.id"
                  type="button"
                  class="btn-quiet"
                  :disabled="busyKey === `fact-${fact.id}`"
                  @click="askRetract(fact)"
                >
                  忘记
                </button>
              </div>
              <span v-if="factNotes(fact)" class="fact-meta num">{{ factNotes(fact) }}</span>

              <!-- 就地确认：先说清后果，再给动作 -->
              <div v-if="pendingRetractId === fact.id" class="confirm-row">
                <span class="confirm-hint">忘记后助手不再使用这条记忆，历史记录仍保留。</span>
                <button
                  :ref="setConfirmButton"
                  type="button"
                  class="btn-danger"
                  :disabled="busyKey === `fact-${fact.id}`"
                  @click="confirmRetract(fact, $event)"
                >
                  忘记这条
                </button>
                <button type="button" class="btn-quiet" @click="cancelRetract">取消</button>
              </div></li>
          </ul>
        </section>

        <section v-if="holdingFacts.length" class="block">
          <h2>持仓与关注</h2>
          <p class="block-hint">你提到持有的标的和关注的理由。</p>
          <ul class="fact-list">
            <li v-for="fact in holdingFacts" :key="fact.id" class="fact-item"><div class="fact-row">
                <span class="fact-main">
                  <strong>{{ factLineText(fact) }}</strong>
                  <span v-if="isInferred(fact)" class="tag tag-inferred">推断</span>
                  <span v-if="isSystemObserved(fact) && fact.predicate !== 'run_summary'" class="tag tag-system">系统观测</span>
                </span>
                <button
                  v-if="pendingRetractId !== fact.id"
                  type="button"
                  class="btn-quiet"
                  :disabled="busyKey === `fact-${fact.id}`"
                  @click="askRetract(fact)"
                >
                  忘记
                </button>
              </div>
              <span v-if="factNotes(fact)" class="fact-meta num">{{ factNotes(fact) }}</span>

              <!-- 就地确认：先说清后果，再给动作 -->
              <div v-if="pendingRetractId === fact.id" class="confirm-row">
                <span class="confirm-hint">忘记后助手不再使用这条记忆，历史记录仍保留。</span>
                <button
                  :ref="setConfirmButton"
                  type="button"
                  class="btn-danger"
                  :disabled="busyKey === `fact-${fact.id}`"
                  @click="confirmRetract(fact, $event)"
                >
                  忘记这条
                </button>
                <button type="button" class="btn-quiet" @click="cancelRetract">取消</button>
              </div></li>
          </ul>
        </section>

        <section v-if="decisionFacts.length" class="block">
          <h2>策略结论</h2>
          <p class="block-hint">策略回测、样本外验证与模拟盘里程碑的一句话结论（助手自动记录）。</p>
          <ul class="fact-list">
            <li v-for="fact in decisionFacts" :key="fact.id" class="fact-item"><div class="fact-row">
                <span class="fact-main">
                  <strong>{{ factLineText(fact) }}</strong>
                  <span v-if="isInferred(fact)" class="tag tag-inferred">推断</span>
                  <span v-if="isSystemObserved(fact) && fact.predicate !== 'run_summary'" class="tag tag-system">系统观测</span>
                </span>
                <button
                  v-if="pendingRetractId !== fact.id"
                  type="button"
                  class="btn-quiet"
                  :disabled="busyKey === `fact-${fact.id}`"
                  @click="askRetract(fact)"
                >
                  忘记
                </button>
              </div>
              <span v-if="factNotes(fact)" class="fact-meta num">{{ factNotes(fact) }}</span>

              <!-- 就地确认：先说清后果，再给动作 -->
              <div v-if="pendingRetractId === fact.id" class="confirm-row">
                <span class="confirm-hint">忘记后助手不再使用这条记忆，历史记录仍保留。</span>
                <button
                  :ref="setConfirmButton"
                  type="button"
                  class="btn-danger"
                  :disabled="busyKey === `fact-${fact.id}`"
                  @click="confirmRetract(fact, $event)"
                >
                  忘记这条
                </button>
                <button type="button" class="btn-quiet" @click="cancelRetract">取消</button>
              </div></li>
          </ul>
        </section>

        <section class="block">
          <h2>助手观察</h2>
          <p class="block-hint">
            助手自动记录的策略运行事实（如首笔成交、清仓），仅供对话参考；
            完整的回测与模拟盘数据在策略详情页。这部分不影响上面"你告诉助手的"计数。
          </p>
          <p v-if="observationFacts.length === 0" class="empty-block">
            <span class="empty-desc">暂无值得单独提醒的观察。策略的日常数据不进记忆，只在对话中被问到时查询。</span>
          </p>
          <template v-else>
            <button type="button" class="btn-quiet obs-toggle" :aria-expanded="showObservations" @click="showObservations = !showObservations">
              {{ showObservations ? '收起' : `展开 ${observationFacts.length} 条观察` }}
            </button>
            <ul v-if="showObservations" class="fact-list obs-list">
              <li v-for="fact in observationFacts" :key="fact.id" class="fact-item"><div class="fact-row">
                <span class="fact-main">
                  <strong>{{ factLineText(fact) }}</strong>
                  <span v-if="isInferred(fact)" class="tag tag-inferred">推断</span>
                  <span v-if="isSystemObserved(fact) && fact.predicate !== 'run_summary'" class="tag tag-system">系统观测</span>
                </span>
                <button
                  v-if="pendingRetractId !== fact.id"
                  type="button"
                  class="btn-quiet"
                  :disabled="busyKey === `fact-${fact.id}`"
                  @click="askRetract(fact)"
                >
                  忘记
                </button>
              </div>
              <span v-if="factNotes(fact)" class="fact-meta num">{{ factNotes(fact) }}</span>

              <!-- 就地确认：先说清后果，再给动作 -->
              <div v-if="pendingRetractId === fact.id" class="confirm-row">
                <span class="confirm-hint">忘记后助手不再使用这条记忆，历史记录仍保留。</span>
                <button
                  :ref="setConfirmButton"
                  type="button"
                  class="btn-danger"
                  :disabled="busyKey === `fact-${fact.id}`"
                  @click="confirmRetract(fact, $event)"
                >
                  忘记这条
                </button>
                <button type="button" class="btn-quiet" @click="cancelRetract">取消</button>
              </div></li>
            </ul>
          </template>
        </section>

        <p v-if="preferenceFacts.length + holdingFacts.length === 0 && decisionFacts.length === 0 && persona.length === 0" class="empty-block">
          <span class="empty-title">还没有可显示的长期记忆</span>
          <span class="empty-desc">跟助手聊几句，你提到的重要信息（偏好、约束、持仓）会自动沉淀到这里。</span>
          <button type="button" class="btn-primary" @click="goAssistant">去和助手聊聊</button>
        </p>

        <p v-if="truncatedFacts > 0" class="block-hint">
          只显示了 {{ facts.length }} 条（共 {{ overview?.facts?.visibleActive ?? facts.length }} 条），
          这里优先显示最近和最重要的。
        </p>

        <section class="block">
          <h2>经验</h2>
          <p class="block-hint">
            这些是助手在解决问题时总结的做法，只作参考：确认后助手会更信任它，停用后不再使用。
          </p>

          <p v-if="lessons.length === 0" class="empty-block">
            <span class="empty-title">还没有总结出经验</span>
            <span class="empty-desc">助手在遇到问题并解决之后，会把做法记在这里，等你确认。</span>
          </p>

          <ul v-else class="lesson-list">
            <li v-for="lesson in lessons" :key="lesson.id" class="lesson-item">
              <div class="lesson-head">
                <span class="tag" :class="`tag-${lesson.status}`">{{ lessonStatusLabel(lesson) }}</span>
                <span v-if="lesson.occurrences > 1" class="lesson-count num">
                  出现过 {{ lesson.occurrences }} 次
                </span>
                <span v-if="lesson.promotionSuggested" class="lesson-hint">可考虑固化成固定流程</span>
              </div>
              <p class="lesson-line"><span class="lesson-key">症状</span>{{ lesson.symptom }}</p>
              <p class="lesson-line"><span class="lesson-key">做法</span>{{ lesson.reusableRule || lesson.resolution }}</p>
              <p v-if="lesson.taskTypeLabel || lesson.taskType" class="lesson-meta num">
                类型：{{ lesson.taskTypeLabel || lesson.taskType }}
              </p>
              <div class="lesson-actions">
                <button
                  v-if="lesson.status !== 'active'"
                  type="button"
                  class="btn-primary"
                  :disabled="busyKey === `lesson-${lesson.id}`"
                  @click="changeLessonStatus(lesson, 'activate')"
                >
                  确认可信
                </button>
                <button
                  v-if="lesson.status !== 'retired'"
                  type="button"
                  class="btn-quiet"
                  :disabled="busyKey === `lesson-${lesson.id}`"
                  @click="changeLessonStatus(lesson, 'retire')"
                >
                  停用
                </button>
                <button
                  v-else
                  type="button"
                  class="btn-quiet"
                  :disabled="busyKey === `lesson-${lesson.id}`"
                  @click="changeLessonStatus(lesson, 'activate')"
                >
                  恢复使用
                </button>
              </div>
            </li>
          </ul>
        </section>

        <section class="block">
          <h2>对话摘要</h2>
          <p class="block-hint">助手每天会把当天的对话整理成一段摘要，下次对话开始时参考它。</p>

          <p v-if="episodes.length === 0" class="empty-block">
            <span class="empty-title">还没有对话摘要</span>
            <span class="empty-desc">聊完一天之后，摘要会在后台自动生成。</span>
          </p>

          <template v-else>
            <ul class="episode-list">
              <li v-for="episode in visibleEpisodes" :key="episode.sessionKey" class="episode-item">
                <span class="episode-date num">{{ String(episode.sessionKey).split(':').pop() }}</span>
                <p class="episode-summary">{{ episode.summary }}</p>
                <ul v-if="episode.keyPoints?.length" class="episode-points">
                  <li v-for="(point, index) in episode.keyPoints" :key="index">{{ point }}</li>
                </ul>
              </li>
            </ul>
            <button
              v-if="episodes.length > 3"
              type="button"
              class="btn-quiet show-more"
              :aria-expanded="showAllEpisodes"
              @click="showAllEpisodes = !showAllEpisodes"
            >
              {{ showAllEpisodes ? '收起' : `显示全部 ${episodes.length} 段` }}
            </button>
          </template>
        </section>
      </template>
  </div>
</template>

<style scoped>



/* 播报区在无内容时不占位，但始终存在于 DOM 里（live region 需要先渲染再更新） */
.status {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--color-success);
}

.error {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--color-danger);
}

.inline-action {
  background: none;
  border: none;
  padding: 2px 4px;
  margin-inline-start: 4px;
  color: var(--color-accent);
  font-size: 13px;
  text-decoration: underline;
}

.overview {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 20px;
}

.stat {
  flex: 1 1 140px;
  background: var(--color-bg-surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-1);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat-value { font-size: 22px; font-weight: 600; color: var(--color-text-primary); }
.stat-label { font-size: 12px; color: var(--color-text-muted); }

/* 区块之间留 24px，区块内 8~12px：靠间距分组，不靠分隔线 */
.block {
  background: var(--color-bg-surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-1);
  padding: 16px 18px;
  margin-bottom: 20px;
}

.block h2 {
  margin: 0 0 6px;
  font-size: 16px;
  color: var(--color-text-primary);
}

.block-hint {
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--color-text-muted);
  line-height: 1.5;
}


/* 筛选是"可切换状态"，用 aria-pressed + 底色 + 边框三重表达，不靠颜色单独表意 */
.chip {
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border-control);
  border-radius: var(--radius-pill);
  padding: 6px 12px;
  min-height: 32px;
  font-size: 12px;
  color: var(--color-text-secondary);
  transition: background-color var(--duration-fast) var(--ease-out),
    color var(--duration-fast) var(--ease-out),
    border-color var(--duration-fast) var(--ease-out);
}

.chip.active {
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
  color: var(--color-accent);
  font-weight: 600;
}

.fact-list, .lesson-list, .episode-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.fact-item {
  border-top: 1px solid var(--color-border);
  padding-top: 10px;
}

.fact-item:first-child { border-top: none; padding-top: 0; }

.fact-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.fact-main {
  font-size: 14px;
  color: var(--color-text-primary);
  line-height: 1.5;
  min-width: 0;
}

.fact-meta {
  display: block;
  margin-top: 2px;
  font-size: 12px;
  color: var(--color-text-muted);
}

.tag {
  display: inline-block;
  margin-inline-start: 6px;
  padding: 1px 7px;
  border-radius: var(--radius-pill);
  font-size: 11px;
  border: 1px solid var(--color-border-strong);
  background: var(--color-bg-subtle);
  color: var(--color-text-secondary);
}

.tag-inferred {
  border-color: var(--color-warning-mark);
  background: var(--color-warning-soft);
  color: var(--color-warning);
}

/* 系统观测是"可复算的数字"而不是"用户说过的话"：中性色区分，不与"推断"的警示混淆 */
.tag-system {
  border-color: var(--color-border-strong);
  background: var(--color-bg-subtle);
  color: var(--color-text-muted);
}

.tag-active {
  border-color: var(--color-success-mark);
  background: var(--color-success-soft);
  color: var(--color-success);
}

.tag-retired { color: var(--color-text-muted); }

.confirm-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.confirm-hint { font-size: 12px; color: var(--color-text-secondary); }

.lesson-item {
  border-top: 1px solid var(--color-border);
  padding-top: 10px;
}

.lesson-item:first-child { border-top: none; padding-top: 0; }

.lesson-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.lesson-count { font-size: 12px; color: var(--color-text-muted); }
.lesson-hint { font-size: 12px; color: var(--color-warning); }

.lesson-line {
  margin: 0 0 4px;
  font-size: 13px;
  color: var(--color-text-primary);
  line-height: 1.5;
}

.lesson-key {
  display: inline-block;
  min-width: 34px;
  margin-inline-end: 6px;
  color: var(--color-text-muted);
  font-size: 12px;
}

.lesson-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }

.lesson-meta {
  margin: 0;
  font-size: 12px;
  color: var(--color-text-muted);
}

.episode-item {
  border-top: 1px solid var(--color-border);
  padding-top: 10px;
}

.episode-item:first-child { border-top: none; padding-top: 0; }

.episode-date { font-size: 12px; color: var(--color-text-muted); }

.episode-summary {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-primary);
}

.episode-points {
  margin: 6px 0 0;
  padding-inline-start: 18px;
  font-size: 12px;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

/* 按钮：命中区 ≥32px，相邻控件留 8px 以上，避免误点 */
.btn-primary, .btn-quiet, .btn-danger {
  border-radius: var(--radius-sm);
  padding: 6px 12px;
  min-height: 32px;
  font-size: 13px;
  transition: background-color var(--duration-fast) var(--ease-out);
}

.btn-primary {
  background: var(--color-accent);
  border: none;
  color: var(--color-text-on-accent);
}

.btn-primary:hover { background: var(--color-accent-hover); }

.btn-quiet {
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border-control);
  color: var(--color-text-primary);
}

/* 撤回不是删除，但会改变助手以后的回答依据；用危险色提醒后果，语义正确 */
.btn-danger {
  background: var(--color-danger);
  border: none;
  color: var(--color-text-on-accent);
}

.btn-danger:hover { background: var(--color-danger-hover); }

.btn-primary:disabled, .btn-quiet:disabled, .btn-danger:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.show-more { margin-top: 12px; }

.empty-block {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  margin: 0;
  padding: 8px 0 4px;
}

.empty-title { font-size: 14px; font-weight: 600; color: var(--color-text-primary); }
.empty-desc { font-size: 12px; color: var(--color-text-muted); line-height: 1.5; }
.empty-block .btn-primary { margin-top: 6px; }

.empty {
  color: var(--color-text-muted);
  text-align: center;
  padding: 48px 16px;
  font-size: 14px;
}
</style>
