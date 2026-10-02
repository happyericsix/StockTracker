<template>
  <div class="page">
    <div class="page-head">
      <h1>资讯雷达</h1>
    </div>

    <!-- 搜索条件：一次提交型（不是每键入一个字就打一次后端） -->
      <form class="search-form" @submit.prevent="load">
        <div class="form-row">
          <label class="field keyword">
            <span class="field-label">关键词</span>
            <input
              v-model.trim="form.keyword"
              type="search"
              placeholder="如：回购 / 增持 / 重组 / 业绩预告"
              maxlength="40"
            >
          </label>
          <label class="field symbol">
            <span class="field-label">标的（可空）</span>
            <input
              v-model.trim="form.symbol"
              type="text"
              placeholder="600519 / SH600519"
              maxlength="10"
            >
          </label>
        </div>
        <div class="form-row">
          <fieldset class="field levels">
            <legend class="field-label">信源</legend>
            <label v-for="opt in LEVEL_OPTIONS" :key="opt.value" class="chip">
              <input v-model="form.types" type="checkbox" :value="opt.value">
              <span>{{ opt.label }}</span>
            </label>
          </fieldset>
          <label class="field days">
            <span class="field-label">时间窗口</span>
            <select v-model.number="form.days">
              <option v-for="d in DAY_OPTIONS" :key="d" :value="d">{{ d }} 天内</option>
            </select>
          </label>
          <label class="chip scope">
            <input v-model="form.scope" type="checkbox" true-value="mine" false-value="">
            <span>只看我的自选</span>
          </label>
          <button type="submit" class="primary-btn" :disabled="loading">
            {{ loading ? '搜索中…' : '搜索' }}
          </button>
        </div>
      </form>

      <p v-if="error" class="error" role="alert">
        {{ error }}
        <button type="button" class="inline-action" @click="load">重试</button>
      </p>

      <div v-if="!loading && !error && searched && results.length === 0" class="empty">
        没有命中的资讯。可以试试更短的关键词、放宽时间窗口，或勾掉信源过滤。
      </div>

      <!-- 结果列表：与个股页事件卡同一套信息层级（信源/时间/可信度 → 标题 → 警示 → 解读） -->
      <ul v-if="results.length" class="result-list">
        <li v-for="event in results" :key="event.id" class="result-card">
          <div class="meta-line">
            <span class="source">
              <AppIcon :name="sourceMeta(event.sourceLevel).icon" :size="13" :stroke-width="2" />
              {{ event.sourceName || sourceMeta(event.sourceLevel).label }}
            </span>
            <span class="time num">{{ formatTime(event.publishedAt) }}</span>
            <span v-if="event.freshnessLabel" class="fresh num">{{ event.freshnessLabel }}</span>
            <span
              v-if="event.credibilityGrade"
              class="credibility"
              :class="credibilityClass(event.credibilityGrade)"
              :title="(event.credibilityReasons || []).join('；')"
            >可信 {{ event.credibilityGrade }}</span>
            <span v-if="event.direction" class="direction" :class="directionClass(event.direction)">{{ event.direction }}</span>
          </div>
          <p class="title">
            <a v-if="event.url" :href="event.url" target="_blank" rel="noopener noreferrer">{{ event.title }}</a>
            <template v-else>{{ event.title }}</template>
            <button
              v-if="event.symbol"
              type="button"
              class="inline-action"
              @click="goChart(event.symbol)"
            >看K线</button>
          </p>
          <p v-if="event.rumorFlag" class="rumor" role="alert">
            传闻特征明显，未经证实，请以官方公告为准
          </p>
          <p v-if="event.analyzed" class="summary">{{ event.plainSummary }}</p>
          <p v-else class="summary muted">信息不足，不判断方向</p>
          <p v-if="(event.credibilityReasons || []).length" class="reasons">
            {{ event.credibilityReasons.slice(0, 3).join('；') }}
          </p>
        </li>
      </ul>

      <button
        v-if="hasMore"
        type="button"
        class="load-more"
        :disabled="loadingMore"
        @click="loadMore"
      >{{ loadingMore ? '加载中…' : '加载更多' }}</button>

      <p class="notice">可信度评估的是传播链路（信源/措辞/印证），判不了事实真伪；AI 解读仅供参考，不构成投资建议。</p>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import { searchNews } from '../api/news.js'

const router = useRouter()

const LEVEL_OPTIONS = [
  { value: 1, label: '公告' },
  { value: 2, label: '媒体' },
  { value: 3, label: '研报' },
  { value: 4, label: '舆情' },
]
const DAY_OPTIONS = [3, 7, 30, 90]
const SOURCE_META = {
  1: { icon: 'megaphone', label: '公告' },
  2: { icon: 'newspaper', label: '媒体' },
  3: { icon: 'doc', label: '研报' },
  4: { icon: 'globe', label: '舆情' },
}

const form = reactive({
  keyword: '',
  symbol: '',
  types: [],
  days: 7,
  scope: '',
})

const PAGE_SIZE = 20
const results = ref([])
const page = ref(0)
const totalPages = ref(0)
const totalElements = ref(0)
const loading = ref(false)
const loadingMore = ref(false)
const error = ref('')
const searched = ref(false)

const hasMore = ref(false)

const sourceMeta = (level) => SOURCE_META[level] || { icon: 'doc', label: '资讯' }

const credibilityClass = (grade) => {
  if (grade === '高' || grade === '较高') return 'cred-good'
  if (grade === '中') return 'cred-mid'
  return 'cred-low'
}

const directionClass = (direction) => {
  if (direction === '利好') return 'bull'
  if (direction === '利空') return 'bear'
  return 'flat'
}

const formatTime = (value) => {
  if (!value) return '时间未知'
  return String(value).replace('T', ' ').slice(0, 16)
}
const goChart = (symbol) => router.push('/chart/' + symbol)

// 竞态防护：快速连点"搜索"时旧的慢响应不得覆盖新结果（与 KLine/Messages 同一模式）
let searchSeq = 0

const fetchPage = async (pageNo) => {
  const payload = {
    keyword: form.keyword || null,
    symbol: form.symbol || null,
    types: form.types.length ? form.types : null,
    days: form.days,
    scope: form.scope || null,
    page: pageNo,
    size: PAGE_SIZE,
  }
  const res = await searchNews(payload)
  const data = res.data || {}
  return {
    list: data.content || [],
    totalPages: data.totalPages || 0,
    totalElements: data.totalElements || 0,
  }
}

const load = async () => {
  const seq = ++searchSeq
  loading.value = true
  error.value = ''
  try {
    const { list, totalPages: pages, totalElements: total } = await fetchPage(0)
    if (seq !== searchSeq) return
    results.value = list
    page.value = 0
    totalPages.value = pages
    totalElements.value = total
    hasMore.value = pages > 1
    searched.value = true
  } catch (e) {
    if (seq !== searchSeq) return
    error.value = e?.response?.data?.message || '搜索失败，请稍后重试'
  } finally {
    if (seq === searchSeq) loading.value = false
  }
}

const loadMore = async () => {
  if (loadingMore.value || !hasMore.value) return
  const seq = searchSeq
  loadingMore.value = true
  try {
    const next = page.value + 1
    const { list, totalPages: pages } = await fetchPage(next)
    if (seq !== searchSeq) return
    results.value = [...results.value, ...list]
    page.value = next
    totalPages.value = pages
    hasMore.value = next < pages - 1
  } catch {
    // 翻页失败：已有内容还在，不打断列表，下一次点"加载更多"重试即可
  } finally {
    loadingMore.value = false
  }
}

onMounted(() => {
  // 首屏直接给一页"最近 7 天"：搜索页最贵的不是查询，是"空状态不知道能搜到什么"
  load()
})
</script>

<style scoped>
.search-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 16px;
}
.form-row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 10px;
}
.field { display: flex; flex-direction: column; gap: 4px; }
.field-label { font-size: 12px; color: var(--color-text-secondary); }
.field input[type='search'], .field input[type='text'], .field select {
  padding: 8px 10px;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md, 8px);
  font-size: 14px;
  background: var(--color-bg-surface);
}
.keyword { flex: 2 1 220px; }
.symbol { flex: 1 1 140px; }
.days select { min-width: 100px; }
.levels { display: flex; align-items: center; gap: 8px; border: none; padding: 0; }
.levels .field-label { margin-right: 2px; }
.chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--color-text-secondary);
  cursor: pointer;
}
.primary-btn {
  padding: 9px 18px;
  border: none;
  border-radius: var(--radius-md, 8px);
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  font-size: 14px;
  cursor: pointer;
}
.primary-btn:disabled { opacity: 0.6; cursor: default; }

.result-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.result-card {
  padding: 12px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 8px);
  background: var(--color-bg-surface);
}
.meta-line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; }
.meta-line .source { color: var(--color-text-secondary); display: inline-flex; align-items: center; gap: 4px; }
.meta-line .time { color: var(--color-text-muted); }
.meta-line .fresh { color: var(--color-text-muted); }
.credibility {
  padding: 1px 6px;
  border: 1px solid currentColor;
  border-radius: var(--radius-pill);
  font-size: 11px;
  cursor: help;
}
.credibility.cred-good { color: var(--color-accent); }
.credibility.cred-mid { color: var(--color-text-muted); }
.credibility.cred-low { color: var(--color-warning); }
.direction { margin-left: auto; padding: 1px 6px; border: 1px solid currentColor; border-radius: var(--radius-pill); font-size: 11px; }
.direction.bull { color: var(--color-gain); }
.direction.bear { color: var(--color-loss); }
.direction.flat { color: var(--color-text-muted); }
.title { margin: 6px 0 0; font-size: 14px; line-height: 1.5; color: var(--color-text-primary); }
.title a { color: inherit; text-decoration: none; }
.title a:hover { text-decoration: underline; }
.rumor {
  margin: 6px 0 0;
  padding: 6px 8px;
  border-radius: var(--radius-sm, 6px);
  font-size: 12px;
  background: var(--color-warning-soft);
  color: var(--color-warning);
}
.summary { margin: 4px 0 0; font-size: 13px; line-height: 1.6; color: var(--color-text-secondary); }
.summary.muted { color: var(--color-text-muted); }
.reasons { margin: 6px 0 0; font-size: 12px; color: var(--color-text-muted); }
.load-more {
  margin: 14px auto 0;
  display: block;
  padding: 8px 22px;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-pill);
  background: var(--color-bg-surface);
  font-size: 13px;
  cursor: pointer;
}
.notice { margin-top: 18px; font-size: 12px; color: var(--color-text-muted); }
.error { color: var(--color-danger); }
.empty { color: var(--color-text-muted); padding: 24px 0; text-align: center; }
</style>
