<script setup>
import { computed, onMounted, nextTick, ref } from 'vue'
import { useRouter } from 'vue-router'
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

const factFilter = ref('all')
const showAllEpisodes = ref(false)
const pendingRetractId = ref(null)
const busyKey = ref('')
const confirmButton = ref(null)

// 只认后端返回的中文业务提示，其次是本地兜底。
// 不回落 e?.message —— 那是 axios 的英文原文（"Request failed with status code 500"）。
const errorMessage = (e, fallback) => e?.response?.data?.message || fallback

// 谓词是后端用来做"取代链"的稳定键（stop_loss_pct 之类），展示时给中文名。
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
  purpose: '用途'
}

const FACT_TYPE_LABELS = {
  preference: '偏好',
  constraint: '约束',
  holding: '持仓',
  goal: '目标',
  decision: '决定',
  observation: '观察'
}

const predicateLabel = (predicate) => PREDICATE_LABELS[predicate] || predicate || '未命名'
const factTypeLabel = (type) => FACT_TYPE_LABELS[type] || '其他'

const factName = (fact) => {
  const label = predicateLabel(fact.predicate)
  const subject = String(fact.subject || '').trim()
  // subject=user 的条目不重复"用户"二字：这个页面本身就是在讲用户自己
  return !subject || subject.toLowerCase() === 'user' ? label : `${subject} ${label}`
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
  if (fact.previousValue) notes.push(`此前为 ${fact.previousValue}`)
  if (fact.rawTimePhrase) notes.push(`你当时说的是“${fact.rawTimePhrase}”`)
  if (fact.dataAsOf) notes.push(`数据截至 ${shortDate(fact.dataAsOf)}`)
  return notes.join(' · ')
}

/** 低置信度且未经用户确认的条目要标出来：那是模型的推断，不是用户说过的话 */
const isInferred = (item) =>
  !item.confirmed && Number(item.confidence ?? 1) < 0.6

const personaIds = computed(() => new Set(persona.value.map((f) => f.id)))

const factTypes = computed(() => {
  const seen = []
  for (const fact of facts.value) {
    const type = fact.factType || 'observation'
    if (!seen.includes(type)) seen.push(type)
  }
  return seen
})

const visibleFacts = computed(() => {
  // 画像已经单独成段，这里不重复展示
  const rest = facts.value.filter((fact) => !personaIds.value.has(fact.id))
  if (factFilter.value === 'all') return rest
  return rest.filter((fact) => (fact.factType || 'observation') === factFilter.value)
})

const visibleEpisodes = computed(() =>
  showAllEpisodes.value ? episodes.value : episodes.value.slice(0, 3)
)

// 接口最多返回 30 条事实（那个上限是为了保护注入给模型的上下文）。
// 页面上必须说明"还有多少没显示" —— 静默截断会让用户以为记忆丢了。
const truncatedFacts = computed(() => {
  const total = Number(overview.value?.facts?.active ?? facts.value.length)
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
    status.value = `已撤回「${factName(fact)}」`
    await load()
  } catch (e) {
    error.value = errorMessage(e, '撤回失败，请稍后重试')
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
const goDashboard = () => router.push('/dashboard')

onMounted(load)
</script>

<template>
  <div class="app-layout">
    <header>
      <h1>记忆</h1>
      <div class="header-actions">
        <button type="button" class="nav-btn" @click="goDashboard">← 返回</button>
      </div>
    </header>

    <main>
      <p class="intro">
        这里是你和助手之间被记住的内容。助手每次回答前都会参考它们，所以你可以随时查看、
        撤回；助手从解决问题的过程里总结的做法，也要由你确认后才算数。
      </p>

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
            <span class="stat-value num">{{ overview.facts?.active ?? 0 }}</span>
            <span class="stat-label">条事实有效</span>
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
                <strong>{{ factName(fact) }}</strong>：{{ fact.object }}
                <span v-if="isInferred(fact)" class="tag tag-inferred">推断</span>
              </span>
              <span v-if="factNotes(fact)" class="fact-meta num">{{ factNotes(fact) }}</span>
            </li>
          </ul>
        </section>

        <section class="block">
          <h2>记住的事</h2>

          <div v-if="factTypes.length > 1" class="filters" role="group" aria-label="按类型筛选">
            <button
              type="button"
              class="chip"
              :aria-pressed="factFilter === 'all'"
              :class="{ active: factFilter === 'all' }"
              @click="factFilter = 'all'"
            >
              全部 <span class="num">{{ facts.length - persona.length }}</span>
            </button>
            <button
              v-for="type in factTypes"
              :key="type"
              type="button"
              class="chip"
              :aria-pressed="factFilter === type"
              :class="{ active: factFilter === type }"
              @click="factFilter = type"
            >
              {{ factTypeLabel(type) }}
            </button>
          </div>

          <p v-if="truncatedFacts > 0" class="block-hint">
            只显示了 {{ facts.length }} 条（共 {{ overview?.facts?.active ?? facts.length }} 条），
            这里优先显示最近和最重要的。
          </p>

          <p v-if="visibleFacts.length === 0" class="empty-block">
            <span class="empty-title">还没有可显示的长期记忆</span>
            <span class="empty-desc">跟助手聊几句，你提到的重要信息（偏好、约束、持仓）会自动沉淀到这里。</span>
            <button type="button" class="btn-primary" @click="goAssistant">去和助手聊聊</button>
          </p>

          <ul v-else class="fact-list">
            <li v-for="fact in visibleFacts" :key="fact.id" class="fact-item">
              <div class="fact-row">
                <span class="fact-main">
                  <strong>{{ factName(fact) }}</strong>：{{ fact.object }}
                  <span v-if="isInferred(fact)" class="tag tag-inferred">推断</span>
                </span>
                <button
                  v-if="pendingRetractId !== fact.id"
                  type="button"
                  class="btn-quiet"
                  :disabled="busyKey === `fact-${fact.id}`"
                  @click="askRetract(fact)"
                >
                  撤回
                </button>
              </div>
              <span v-if="factNotes(fact)" class="fact-meta num">{{ factNotes(fact) }}</span>

              <!-- 就地确认：先说清后果，再给动作 -->
              <div v-if="pendingRetractId === fact.id" class="confirm-row">
                <span class="confirm-hint">撤回后助手不再使用这条记忆，历史记录仍保留。</span>
                <button
                  :ref="setConfirmButton"
                  type="button"
                  class="btn-danger"
                  :disabled="busyKey === `fact-${fact.id}`"
                  @click="confirmRetract(fact, $event)"
                >
                  撤回这条
                </button>
                <button type="button" class="btn-quiet" @click="cancelRetract">取消</button>
              </div>
            </li>
          </ul>
        </section>

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

.nav-btn {
  background: color-mix(in srgb, var(--color-text-inverse) 15%, transparent);
  border: none;
  color: var(--color-text-inverse);
  padding: 6px 14px;
  border-radius: var(--radius-sm);
  font-size: 13px;
}

.nav-btn:hover { background: color-mix(in srgb, var(--color-text-inverse) 25%, transparent); }

main { max-width: 820px; margin: 0 auto; padding: 24px 16px; }

.intro {
  margin: 0 0 16px;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.6;
}

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

.filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }

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
