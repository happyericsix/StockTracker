<template>
  <div class="page">
    <div class="page-head">
      <h1>资讯雷达 <InfoTip label="可信度与免责说明" text="可信度评估的是传播链路（信源/措辞/印证），判不了事实真伪；AI 解读仅供参考，不构成投资建议。" /></h1>
    </div>

    <!-- 信息流切换：大盘默认在首位；自选股一排即点即看；关键词搜索是兜底，
         折叠在右侧（没有人打开页面是为了"先填一个表单"） -->
    <div class="feed-tabs" role="tablist" aria-label="资讯流">
      <button
        type="button"
        class="feed-chip"
        :class="{ active: mode === 'market' }"
        role="tab"
        :aria-selected="mode === 'market'"
        @click="selectMarket"
      >大盘</button>
      <button
        v-for="fav in favorites"
        :key="fav.symbol"
        type="button"
        class="feed-chip"
        :class="{ active: mode === 'stock' && activeSymbol === fav.symbol }"
        role="tab"
        :aria-selected="mode === 'stock' && activeSymbol === fav.symbol"
        @click="selectStock(fav.symbol)"
      >{{ chipLabel(fav) }}</button>
      <button
        type="button"
        class="feed-chip search-toggle"
        :class="{ active: mode === 'search' }"
        :aria-expanded="searchOpen"
        @click="toggleSearch"
      >搜索<template v-if="mode === 'search' && form.keyword">「{{ form.keyword }}」</template></button>
    </div>

    <p v-if="!favorites.length && !favoritesLoading" class="tabs-hint">
      还没有自选股。去首页把关心的股票加进自选，这里就能一键看它的资讯。
    </p>

    <!-- 关键词搜索（兜底）：折叠面板，只在明确要搜时展开 -->
    <form v-if="searchOpen" class="search-form" @submit.prevent="load">
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
        <button type="submit" class="primary-btn" :disabled="loading">
          {{ loading ? '搜索中…' : '搜索' }}
        </button>
      </div>
    </form>

    <!-- 个股标签下的散户情绪：旁路信息，拿不到就整行隐藏 -->
    <div v-if="mode === 'stock' && sentiment" class="sentiment-line" aria-label="散户情绪">
      <span class="sentiment-metrics num">
        <span v-if="sentiment.desire?.latest != null">
          参与意愿 {{ sentiment.desire.latest.toFixed(0) }}<template v-if="sentiment.desire.change != null">（{{ sentiment.desire.change > 0 ? '+' : '' }}{{ sentiment.desire.change.toFixed(1) }}）</template>
        </span>
        <span v-if="sentiment.focus?.latest != null">关注 {{ sentiment.focus.latest.toFixed(0) }}</span>
        <span v-if="sentiment.score?.latest != null">千股千评 {{ sentiment.score.latest.toFixed(0) }}</span>
      </span>
      <span v-if="sentimentVerdict" class="sentiment-verdict">{{ sentimentVerdict }}</span>
    </div>

    <!-- 「当前怎么看」：结合这批事件的整体判断（与 K 线页同一接口）。
         拿不到时整块不显示 —— "这次给不出结论"是合法状态，不占位不空话 -->
    <div v-if="mode === 'stock' && stockRead" class="stock-read">
      <p class="stock-read-label">当前怎么看</p>
      <p class="stock-read-text">{{ stockRead }}</p>
    </div>
    <p v-else-if="mode === 'stock' && stockReadLoading" class="progress-line" role="status">
      正在综合近期资讯生成判断…（约需几秒）
    </p>

    <!-- 大盘首次空库：增量刷新要把上游抓一遍（十几秒），把状态说出来 -->
    <p v-if="marketRefreshing" class="progress-line" role="status">
      首次查看，正在同步大盘资讯，可能需要十几秒…
    </p>
    <!-- 个股流的后台解读进度：没有这句，半截列表会被当成坏数据 -->
    <p v-else-if="analyzing" class="progress-line" role="status">
      正在解读 {{ analyzedCount }}/{{ analyzedCount + pendingCount }} 条…
    </p>

      <p v-if="error" class="error" role="alert">
        {{ error }}
        <button type="button" class="inline-action" @click="load">重试</button>
      </p>

      <div v-if="!loading && !error && searched && results.length === 0" class="empty">
        {{ emptyHint }}
      </div>

      <!-- 文件夹切换：浏览流（大盘/个股）按分类分组，搜索结果保持平铺 -->
      <div v-if="folderChips.length > 1" class="folder-tabs" role="tablist" aria-label="资讯分类">
        <button
          type="button"
          class="folder-chip"
          :class="{ active: !activeCategory }"
          @click="activeCategory = ''"
        >全部 {{ results.length }}</button>
        <button
          v-for="chip in folderChips"
          :key="chip.name"
          type="button"
          class="folder-chip"
          :class="{ active: activeCategory === chip.name }"
          @click="activeCategory = chip.name"
        >{{ chip.name }} {{ chip.count }}</button>
      </div>

      <!-- 结果区块：与个股页事件卡同一套信息层级（信源/时间/可信度 → 标题 → 警示 → 解读）。
           统一走 sections 渲染：分组浏览=多个文件夹区块，选定文件夹/搜索=单个区块。 -->
      <section v-for="section in displaySections" :key="section.key" class="folder">
        <h2 v-if="section.category" class="folder-title">
          {{ section.category }} <span class="folder-count num">{{ section.items.length }}</span>
        </h2>
        <ul class="result-list">
          <li v-for="event in section.items" :key="event.id" class="result-card">
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
              <!-- 标题点击原地展开全文（去链接化阅读）；原文链接降级为展开区里的次操作 -->
              <button
                type="button"
                class="title-toggle"
                :aria-expanded="expandedId === event.id"
                @click="toggleBody(event)"
              >
                <AppIcon
                  name="chevron-right"
                  :size="14"
                  :stroke-width="2"
                  class="title-caret"
                  :class="{ open: expandedId === event.id }"
                />
                {{ event.title }}
              </button>
              <button
                v-if="event.symbol"
                type="button"
                class="inline-action"
                @click="goChart(event.symbol)"
              >看K线</button>
            </p>
            <!-- 展开的原文：抓取中/失败/正文三态，失败时原文链接升为主要出路 -->
            <div v-if="expandedId === event.id" class="event-body">
              <p v-if="bodyState(event) === 'loading'" class="body-status">正在抓取原文…</p>
              <p v-else-if="bodyState(event) === 'error'" class="body-status" role="alert">
                {{ bodyErrors[event.id] }}。
                <a v-if="event.url" :href="event.url" target="_blank" rel="noopener noreferrer">在新窗口看原文</a>
              </p>
              <template v-else>
                <div class="body-text">{{ bodyText(event) }}</div>
                <a v-if="event.url" class="source-link" :href="event.url" target="_blank" rel="noopener noreferrer">查看原文</a>
              </template>
            </div>
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
      </section>

      <button
        v-if="hasMore"
        type="button"
        class="load-more"
        :disabled="loadingMore"
        @click="loadMore"
        >{{ loadingMore ? '加载中…' : '加载更多' }}</button>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import InfoTip from '../components/InfoTip.vue'
import { searchNews, getStockEvents, refreshNews, getEventBody, getStockRead } from '../api/news.js'
import { getFavorites, getFavoritesBrief } from '../api/stock.js'
import { getSentiment } from '../api/market.js'

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
})

// ---------- 信息流模式：大盘（默认）/ 个股 / 关键词搜索（兜底） ----------
const mode = ref('market')
const activeSymbol = ref('')
const searchOpen = ref(false)

// ---------- 自选股（标签条的数据源） ----------
const favorites = ref([])
const favoritesLoading = ref(true)
const loadFavorites = async () => {
  try {
    const res = await getFavorites()
    // 主路径带名称与实时价；行情网关（Redis 缓存）不可用时会 500 ——
    // 降级到纯库查询的代码列表，标签条显示剥前缀的代码总比整条消失好
    if (Array.isArray(res.data)) {
      favorites.value = res.data
      return
    }
    throw new Error('unexpected shape')
  } catch {
    try {
      const brief = await getFavoritesBrief()
      favorites.value = (Array.isArray(brief.data) ? brief.data : [])
        .map((symbol) => ({ symbol, name: '' }))
    } catch {
      favorites.value = []
    }
  } finally {
    favoritesLoading.value = false
  }
}
const chipLabel = (fav) => fav.name || String(fav.symbol || '').replace(/^(SH|SZ|BJ)/i, '')

// ---------- 个股标签下的散户情绪（旁路：拿不到就整行隐藏） ----------
const sentiment = ref(null)
let sentimentSeq = 0
const loadSentiment = async (symbol) => {
  const seq = ++sentimentSeq
  sentiment.value = null
  try {
    const res = await getSentiment(symbol)
    if (seq === sentimentSeq) sentiment.value = res.data?.data || null
  } catch {
    if (seq === sentimentSeq) sentiment.value = null
  }
}
const sentimentVerdict = computed(() => {
  const v = sentiment.value?.validation
  if (!v) return ''
  return v.verdict || ''
})

// ---------- 「当前怎么看」：读完这批事件后的一段连贯整体判断 ----------
// 逐条方向标签把"所以呢"的综合责任推回给了用户；这一块才是
// "结合整体新闻给出怎么看"的答案。与 K 线页同一接口同一语义。
const stockRead = ref('')
const stockReadLoading = ref(false)
let readSeq = 0
const loadStockRead = async (symbol) => {
  const seq = ++readSeq
  stockRead.value = ''
  stockReadLoading.value = true
  try {
    // 与事件流并行：后端 /stock-read 内部会等在途补解读结束再合成，
    // 顺序发会让用户等两次（先解读、再合成）
    const res = await getStockRead(symbol, STOCK_DAYS)
    if (seq !== readSeq) return
    stockRead.value = typeof res?.data === 'string' ? res.data : ''
  } catch {
    // 综合判断是增强信息：拿不到就整块不显示（后端 data=null 是合法状态）
    if (seq === readSeq) stockRead.value = ''
  } finally {
    if (seq === readSeq) stockReadLoading.value = false
  }
}

const PAGE_SIZE = 20
// 个股流走 /news/events 时间轴：窗口给 30 天（比 K 线页的 90 天克制，
// 这里是"最近发生了什么"，不是"复盘全历史"）
const STOCK_DAYS = 30
const results = ref([])
const page = ref(0)
const totalPages = ref(0)
const totalElements = ref(0)
const loading = ref(false)
const loadingMore = ref(false)
const error = ref('')
const searched = ref(false)
// 个股流的后台解读进度（与 K 线页同一套字段）
const analyzing = ref(false)
const analyzedCount = ref(0)
const pendingCount = ref(0)
// 大盘流首次为空时的自动增量刷新（一次会话只触发一次）
const marketRefreshing = ref(false)
let marketRefreshed = false

const hasMore = ref(false)

const emptyHint = computed(() => {
  if (mode.value === 'stock') return '这只股票近 30 天没有公告、媒体或研报记录。'
  if (mode.value === 'market') return '暂时取不到大盘快讯，稍后再试。'
  return '没有命中的资讯。可以试试更短的关键词、放宽时间窗口，或勾掉信源过滤。'
})

const sourceMeta = (level) => SOURCE_META[level] || { icon: 'doc', label: '资讯' }

// ---------- 文件夹（分类分组）：仅浏览流启用，搜索结果保持平铺 ----------
const CATEGORY_ORDER = ['公司动态', '业绩财务', '行业关联', '宏观经济', '政策监管', '地缘政治', '市场快讯']
const activeCategory = ref('')

const groupedResults = computed(() => {
  const groups = []
  for (const category of CATEGORY_ORDER) {
    const items = results.value.filter((e) => e.category === category)
    if (items.length) groups.push({ category, items })
  }
  // 未分类（极早期数据没回填/模型表外值）兜底进"其他"，不让条目凭空消失
  const rest = results.value.filter((e) => !CATEGORY_ORDER.includes(e.category))
  if (rest.length) groups.push({ category: '其他', items: rest })
  return groups
})

const folderChips = computed(() =>
  mode.value === 'search' ? [] : groupedResults.value.map((g) => ({ name: g.category, count: g.items.length })))

const displaySections = computed(() => {
  if (!results.value.length) return []
  if (mode.value === 'search') {
    return [{ key: 'flat', category: '', items: results.value }]
  }
  if (activeCategory.value) {
    const group = groupedResults.value.find((g) => g.category === activeCategory.value)
    return group ? [{ key: group.category, category: group.category, items: group.items }] : []
  }
  return groupedResults.value.map((g) => ({ key: g.category, category: g.category, items: g.items }))
})

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

// ---------- 原文展开（懒抓取 + 预热） ----------
const expandedId = ref(null)
const bodies = reactive({})
const bodyErrors = reactive({})
const bodyLoading = reactive({})

const toggleBody = async (event) => {
  if (expandedId.value === event.id) {
    expandedId.value = null
    return
  }
  expandedId.value = event.id
  // 已有正文（库里的/预热到的/公告补抓进 content 的）直接展示，不发请求
  if (bodyText(event) || bodyErrors[event.id] || !event.url) return
  bodyLoading[event.id] = true
  try {
    const res = await getEventBody(event.id)
    bodies[event.id] = res.data
  } catch (e) {
    bodyErrors[event.id] = e?.response?.data?.message || '原文抓取失败'
  } finally {
    bodyLoading[event.id] = false
  }
}

const bodyText = (event) =>
  bodies[event.id] || event.body
  // 公告的正文由 enrich 管线直接写进 content（本就没有可点开的原文页）；
  // 够长才算"全文"，300 字摘要不算
  || (event.content && event.content.length >= 400 ? event.content : '')

const bodyState = (event) => {
  if (bodyLoading[event.id]) return 'loading'
  if (bodyErrors[event.id]) return 'error'
  return bodyText(event) ? 'ok' : 'none'
}

/** 静默预热浏览流的前几条：用户点开时正文已经在路上/已在库里 */
const prefetchBodies = (items) => {
  items
    .filter((e) => e.url && !bodyText(e) && !bodyErrors[e.id] && !bodies[e.id])
    .slice(0, 3)
    .forEach((e) => {
      getEventBody(e.id).then((res) => { bodies[e.id] = res.data }).catch(() => {})
    })
}

// 竞态防护：快速切标签时旧的慢响应不得覆盖新结果（与 KLine/Messages 同一模式）
let searchSeq = 0

const fetchPage = async (pageNo) => {
  let payload
  if (mode.value === 'market') {
    // 大盘流：只取媒体快讯（无标的时的媒体源=全球快讯）。
    // 不带公告源：当日全市场公告是随机公司的法定披露，混进"大盘"全是噪音。
    payload = { symbol: null, keyword: null, types: [2], days: 3, page: pageNo, size: PAGE_SIZE }
  } else {
    payload = {
      keyword: form.keyword || null,
      symbol: form.symbol || null,
      types: form.types.length ? form.types : null,
      days: form.days,
      page: pageNo,
      size: PAGE_SIZE,
    }
  }
  const res = await searchNews(payload)
  const data = res.data || {}
  return {
    list: data.content || [],
    totalPages: data.totalPages || 0,
    totalElements: data.totalElements || 0,
  }
}

// ---------- 个股流：/news/events 时间轴（读库 + 后台抓取与解读） ----------
const applyStockData = (data) => {
  results.value = data.events || []
  analyzing.value = !!data.analyzing
  analyzedCount.value = data.analyzedCount || 0
  pendingCount.value = data.pendingCount || 0
  hasMore.value = false
  prefetchBodies(results.value)
}

let analyzeTimer = null
const stopAnalysisPolling = () => {
  if (analyzeTimer) { clearTimeout(analyzeTimer); analyzeTimer = null }
  analyzing.value = false
}
const pollAnalysis = () => {
  stopAnalysisPolling()
  analyzeTimer = setTimeout(async () => {
    if (mode.value !== 'stock' || !activeSymbol.value) return
    try {
      const res = await getStockEvents(activeSymbol.value, STOCK_DAYS)
      if (mode.value !== 'stock') return
      applyStockData(res.data || {})
      if ((res.data || {}).analyzing) pollAnalysis()
    } catch {
      stopAnalysisPolling()
    }
  }, 3000)
}

const loadStock = async () => {
  const seq = ++searchSeq
  loading.value = true
  error.value = ''
  try {
    const res = await getStockEvents(activeSymbol.value, STOCK_DAYS)
    if (seq !== searchSeq) return
    applyStockData(res.data || {})
    searched.value = true
    if ((res.data || {}).analyzing) pollAnalysis()
  } catch (e) {
    if (seq !== searchSeq) return
    error.value = e?.response?.data?.message || '资讯加载失败，请稍后重试'
  } finally {
    if (seq === searchSeq) loading.value = false
  }
}

const load = async () => {
  if (mode.value === 'stock') {
    stopAnalysisPolling()
    return loadStock()
  }
  const seq = ++searchSeq
  loading.value = true
  error.value = ''
  try {
    const { list, totalPages: pages, totalElements: total } = await fetchPage(0)
    if (seq !== searchSeq) return
    // 大盘流空库：触发一次增量刷新再查（上游抓取要十几秒，把状态说出来）
    if (mode.value === 'market' && !list.length && !marketRefreshed) {
      marketRefreshed = true
      marketRefreshing.value = true
      try {
        await refreshNews()
      } catch {
        // 刷新失败：按空结果展示，重试按钮仍可用
      }
      marketRefreshing.value = false
      if (seq !== searchSeq) return
      const retry = await fetchPage(0)
      if (seq !== searchSeq) return
      results.value = retry.list
      totalPages.value = retry.totalPages
      totalElements.value = retry.totalElements
      hasMore.value = retry.totalPages > 1
      searched.value = true
      loading.value = false
      return
    }
    results.value = list
    page.value = 0
    totalPages.value = pages
    totalElements.value = total
    hasMore.value = pages > 1
    searched.value = true
    prefetchBodies(list)
  } catch (e) {
    if (seq !== searchSeq) return
    error.value = e?.response?.data?.message || '加载失败，请稍后重试'
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

// ---------- 标签切换 ----------
const selectMarket = () => {
  if (mode.value === 'market') return
  stopAnalysisPolling()
  mode.value = 'market'
  activeSymbol.value = ''
  activeCategory.value = ''
  sentiment.value = null
  load()
}
const selectStock = (symbol) => {
  if (mode.value === 'stock' && activeSymbol.value === symbol) return
  if (mode.value === 'stock') stopAnalysisPolling()
  mode.value = 'stock'
  activeSymbol.value = symbol
  activeCategory.value = ''
  load()
  loadSentiment(symbol)
  loadStockRead(symbol)
}
const toggleSearch = () => {
  searchOpen.value = !searchOpen.value
  if (searchOpen.value) mode.value = 'search'
  else if (mode.value === 'search') selectMarket()
  // 关闭面板回到大盘；提交搜索时 load() 已把 mode 留在 search
}

onMounted(() => {
  loadFavorites()
  // 首屏直接给大盘快讯：这个页面的第一眼不该是一张空表单
  load()
})
onUnmounted(stopAnalysisPolling)
</script>

<style scoped>
/* ---------- 信息流标签条：大盘 / 自选股 / 搜索兜底 ---------- */
.feed-tabs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
}
.feed-chip {
  padding: 6px 14px;
  border: 1px solid var(--color-border-strong);
  border-radius: 999px;
  background: var(--color-bg-surface);
  color: var(--color-text-secondary);
  font: var(--font-ui);
  cursor: pointer;
  transition: background-color var(--duration-fast) var(--ease-out),
              color var(--duration-fast) var(--ease-out),
              border-color var(--duration-fast) var(--ease-out),
              transform 160ms var(--ease-out-strong);
}
.feed-chip:hover { background: var(--color-bg-subtle); color: var(--color-text-primary); }
.feed-chip:active { transform: scale(0.97); }
.feed-chip.active {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
  color: var(--color-accent);
}
/* 搜索兜底入口贴右：与常用标签拉开距离，"兜底"的位置本身就是信息 */
.search-toggle { margin-left: auto; }
.tabs-hint {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--color-text-muted);
}
/* ---------- 文件夹（分类分组） ---------- */
.folder-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
}
.folder-chip {
  padding: 4px 12px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: background-color var(--duration-fast) var(--ease-out),
              color var(--duration-fast) var(--ease-out),
              border-color var(--duration-fast) var(--ease-out);
}
.folder-chip:hover { color: var(--color-text-primary); border-color: var(--color-border-strong); }
.folder-chip.active {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
  color: var(--color-accent);
}
.folder { margin-bottom: 4px; }
.folder-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin: 14px 0 8px;
  font: var(--font-heading);
  color: var(--color-text-primary);
}
.folder-count {
  font-size: 12px;
  font-weight: 400;
  color: var(--color-text-muted);
}
/* ---------- 原文手风琴 ---------- */
.title-toggle {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--color-text-primary);
  font: inherit;
  font-weight: 600;
  text-align: left;
  cursor: pointer;
}
.title-toggle:hover { color: var(--color-accent); }
.title-caret {
  flex: none;
  align-self: center;
  transition: transform var(--duration-fast) var(--ease-out);
}
.title-caret.open { transform: rotate(90deg); }
.event-body {
  margin: 8px 0 0;
  padding: 10px 12px;
  border-radius: var(--radius-lg);
  background: var(--color-bg-subtle);
}
.body-status { margin: 0; font-size: 13px; color: var(--color-text-muted); }
.body-status a { color: var(--color-accent); }
.body-text {
  max-height: 480px;
  overflow-y: auto;
  font-size: 14px;
  line-height: 1.8;
  color: var(--color-text-secondary);
  white-space: pre-line;
}
.source-link {
  display: inline-block;
  margin-top: 8px;
  font-size: 12px;
  color: var(--color-text-muted);
}
.source-link:hover { color: var(--color-accent); }
/* ---------- 个股情绪行（旁路信息） ---------- */
.sentiment-line {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 12px;
  margin-bottom: 12px;
  padding: 8px 12px;
  border-radius: var(--radius-lg);
  background: var(--color-bg-subtle);
  font-size: 12px;
  color: var(--color-text-secondary);
}
.sentiment-metrics { display: inline-flex; gap: 12px; color: var(--color-text-primary); }
.sentiment-verdict { color: var(--color-text-muted); }
.stock-read {
  margin-bottom: 12px;
  padding: 12px 14px;
  border-radius: var(--radius-lg);
  background: var(--color-accent-soft);
}
.stock-read-label {
  margin: 0 0 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--color-accent);
}
.stock-read-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text-primary);
}
.progress-line {
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--color-text-muted);
}

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
.error { color: var(--color-danger); }
.empty { color: var(--color-text-muted); padding: 24px 0; text-align: center; }
</style>
