<script setup>
/**
 * 资讯雷达：今日信息面 + 要闻专题 + 大盘/个股/搜索三态信息流 + 沉浸阅读器。
 * 逻辑自旧版原样移植（零漂移）：频道口径、新消息红点轮询、个股解读进度轮询、
 * 正文懒抓 + 预取、乐观分页（searchSeq 竞态防护）。
 */
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ChevronRight, X, AlertTriangle, Radar } from 'lucide-vue-next'
import ChatDrawer from '../components/ChatDrawer.vue'
import Badge from '../components/ui/badge.vue'
import Button from '../components/ui/button.vue'
import Empty from '../components/ui/empty.vue'
import Skeleton from '../components/ui/skeleton.vue'
import {
  searchNews, getStockEvents, refreshNews, getEventBody,
  getMarketRead, getNewsTopics, getTopicDetail, countNewsSince, getHotRank,
} from '../api/news.js'
import { getFavorites, getFavoritesBrief } from '../api/stock.js'

const router = useRouter()
const chatOpen = ref(false)
const chatFocus = ref(null)
const askAbout = (event) => { chatFocus.value = event; chatOpen.value = true }

const DAY_OPTIONS = [3, 7, 30, 90]
const form = reactive({ keyword: '', symbol: '', types: [], days: 7 })
const mode = ref('market')
const activeSymbol = ref('')

// ---- 热榜（东财个股人气榜，2026-10-16）：瞬态视图，5 分钟粒度足够，不做轮询 ----
// 2026-10-30 加"展开更多"：默认 20 条，一键展开到 50（Java/Python 两侧的上限）。
// 上游拉的是全榜 + 5 分钟缓存，展开不产生额外取数成本。
const HOT_PAGE_SIZE = 20
const HOT_MAX = 50
const hotRank = ref([])
const hotError = ref('')
const hotLimit = ref(HOT_PAGE_SIZE)
const hotLoading = ref(false)
const fetchHot = async () => {
  hotLoading.value = true
  hotError.value = ''
  try {
    const res = await getHotRank(hotLimit.value)
    hotRank.value = Array.isArray(res.data?.items) ? res.data.items : []
    if (!hotRank.value.length) hotError.value = '热榜暂不可用，稍后再试'
  } catch {
    hotError.value = '热榜暂不可用，稍后再试'
  } finally {
    hotLoading.value = false
  }
}
const selectHot = () => {
  if (mode.value === 'hot') return
  stopAnalysisPolling()
  mode.value = 'hot'
  activeSymbol.value = ''
  // 让位时把新闻列表的分页状态收干净：否则"加载更多"按钮会泄漏进热榜视图，
  // 点了还会以空表单发一次搜索请求（结果无处渲染）—— 2026-10-29 实发
  hasMore.value = false
  results.value = []
  if (hotRank.value.length) return       // 已有数据不重复拉（热榜 5 分钟缓存，回切无需刷新）
  hotLimit.value = HOT_PAGE_SIZE
  fetchHot()
}
const loadMoreHot = () => {
  if (hotLoading.value || hotLimit.value >= HOT_MAX) return
  hotLimit.value = HOT_MAX               // 一次展开到上限：榜单是排名序列，分页没有意义
  fetchHot()
}

const CHANNELS = {
  all: { label: '全部', types: [2, 4], days: 3 },
  wire: { label: '快讯', types: [2], days: 3 },
  notice: { label: '公告', types: [1], days: 3 },
  report: { label: '研报', types: [3], days: 30 },
  forum: { label: '舆情', types: [4], days: 3 },
}
const channel = ref('all')
const selectChannel = (key) => { if (channel.value !== key) { channel.value = key; load() } }

const favorites = ref([])
const loadFavorites = async () => {
  try { const res = await getFavorites(); if (Array.isArray(res.data)) { favorites.value = res.data; return } } catch {}
  try { const b = await getFavoritesBrief(); favorites.value = (Array.isArray(b.data) ? b.data : []).map((s) => ({ symbol: s, name: '' })) } catch { favorites.value = [] }
}
const chipLabel = (fav) => fav.name || String(fav.symbol || '').replace(/^(SH|SZ|BJ)/i, '')

const PAGE_SIZE = 20
const results = ref([])
const page = ref(0)
const loading = ref(false)
const loadingMore = ref(false)
const error = ref('')
const searched = ref(false)
const marketRefreshing = ref(false)
let marketRefreshed = false
const hasMore = ref(false)

const emptyHint = computed(() => {
  if (mode.value === 'stock') return '这只股票近 30 天没有相关资讯。'
  if (mode.value === 'market') return '暂时取不到资讯。'
  return '没有命中的资讯。'
})

const directionTone = (d) => (d === '利好' ? 'up' : d === '利空' ? 'down' : 'flat')
const formatTime = (e) => { if (!e?.publishedAt) return ''; const t = String(e.publishedAt).replace('T', ' '); return e.dateOnly ? t.slice(0, 10) : t.slice(0, 16) }
const formatDateOnly = (e) => { if (!e?.publishedAt) return ''; const t = String(e.publishedAt); return `${parseInt(t.slice(5, 7))}月${parseInt(t.slice(8, 10))}日` }
const goChart = (s) => router.push('/chart/' + s)

const readerEvent = ref(null)
const readerRelated = ref([])
const bodies = reactive({})
const bodyErrors = reactive({})
const bodyLoading = reactive({})

const openReader = async (event) => {
  readerEvent.value = event; readerRelated.value = []
  if (bodyText(event) || bodyErrors[event.id] || !event.url) return
  bodyLoading[event.id] = true
  try { const res = await getEventBody(event.id); bodies[event.id] = res.data }
  catch (e) { bodyErrors[event.id] = e?.response?.data?.message || '原文抓取失败' }
  finally { bodyLoading[event.id] = false }
  if (event.symbol) { try { const res = await getStockEvents(event.symbol, 7); readerRelated.value = (res.data?.events || []).filter((e) => e.id !== event.id).slice(0, 5) } catch {} }
}
const closeReader = () => { readerEvent.value = null; readerRelated.value = [] }

const bodyText = (e) => bodies[e.id] || e.body || cleanWireContent(e.content)
const cleanWireContent = (raw) => {
  if (!raw || raw.length < 10) return ''   // 10 字：短讯/龙虎榜解读也是正文（30 会把它们挡成"无正文"）
  let t = raw
  const b = t.match(/^【[^】]{4,}】/)
  if (b) t = t.slice(b[0].length)
  return t.replace(/\r/g, '').replace(/([^\n])\n([^\n])/g, '$1$2').replace(/ {2,}/g, ' ').trim().length >= 30 ? t.trim() : ''
}
const bodyState = (e) => (bodyLoading[e.id] ? 'loading' : bodyErrors[e.id] ? 'error' : bodyText(e) ? 'ok' : 'none')
// 预览三段回退：AI 解读 → 原文摘要 → 空。解读没跑到/没跑的条目也要有一行内容可读，
// 否则列表里只剩标题，看起来像"看不到内容"（2026-10-16 用户反馈）
const interpretationOf = (e) => {
  if (e.analyzed && e.plainSummary) return e.plainSummary
  const raw = String(e.content || '').trim()
  if (raw.length >= 10) return raw.length > 110 ? raw.slice(0, 110) + '…' : raw
  return ''
}
const prefetchBodies = (items) => { items.filter((e) => e.url && !bodyText(e) && !bodyErrors[e.id] && !bodies[e.id]).slice(0, 3).forEach((e) => { getEventBody(e.id).then((r) => { bodies[e.id] = r.data }).catch(() => {}) }) }

const CATEGORY_ORDER = ['公司动态', '业绩财务', '行业关联', '宏观经济', '政策监管', '地缘政治', '舆论热度', '市场快讯']
const groupedResults = computed(() => {
  const groups = []
  for (const c of CATEGORY_ORDER) { const items = results.value.filter((e) => e.category === c); if (items.length) groups.push({ category: c, items }) }
  const rest = results.value.filter((e) => !CATEGORY_ORDER.includes(e.category))
  if (rest.length) groups.push({ category: '其他', items: rest })
  return groups
})
const displaySections = computed(() => {
  if (mode.value === 'hot') return []   // 热榜模式下新闻列表让位（热榜自己就是内容）
  if (!results.value.length) return []
  if (mode.value === 'search') return [{ key: 'flat', category: '', items: results.value }]
  return groupedResults.value.map((g) => ({ key: g.category, category: g.category, items: g.items }))
})

let searchSeq = 0
const fetchPage = async (pageNo) => {
  let payload
  if (mode.value === 'market') { const ch = CHANNELS[channel.value] || CHANNELS.all; payload = { symbol: null, keyword: null, types: ch.types, days: ch.days, page: pageNo, size: PAGE_SIZE } }
  else { payload = { keyword: form.keyword || null, symbol: form.symbol || null, types: form.types.length ? form.types : null, days: form.days, page: pageNo, size: PAGE_SIZE } }
  const res = await searchNews(payload)
  const d = res.data || {}
  return { list: d.content || [], totalPages: d.totalPages || 0 }
}

const applyStockData = (data) => { results.value = data.events || []; hasMore.value = false }
let analyzeTimer = null
const stopAnalysisPolling = () => { if (analyzeTimer) { clearTimeout(analyzeTimer); analyzeTimer = null } }
const pollAnalysis = () => { stopAnalysisPolling(); analyzeTimer = setTimeout(async () => { if (mode.value !== 'stock' || !activeSymbol.value) return; try { const res = await getStockEvents(activeSymbol.value, 30); if (mode.value === 'stock') { applyStockData(res.data || {}); if ((res.data || {}).analyzing) pollAnalysis() } } catch { stopAnalysisPolling() } }, 3000) }

const LAST_SEEN_KEY = 'news.lastSeenAt'
let lastSeenAt = ''
try { lastSeenAt = localStorage.getItem(LAST_SEEN_KEY) || '' } catch {}
if (!lastSeenAt) { lastSeenAt = new Date().toISOString().slice(0, 19); try { localStorage.setItem(LAST_SEEN_KEY, lastSeenAt) } catch {} }
const newCount = ref(0)
const pollNewCount = () => { countNewsSince(lastSeenAt).then((r) => { newCount.value = Number(r.data) || 0 }).catch(() => {}) }
const loadNewest = () => { newCount.value = 0; load() }
let newCountTimer = null
const markSeen = (list) => { try { const newest = list.map((e) => String(e.publishedAt || '')).filter(Boolean).sort().pop(); if (newest && newest > lastSeenAt) lastSeenAt = newest; localStorage.setItem(LAST_SEEN_KEY, lastSeenAt) } catch {}; newCount.value = 0 }

const load = async () => {
  if (mode.value === 'stock') { stopAnalysisPolling(); return loadStock() }
  const seq = ++searchSeq; loading.value = true; error.value = ''
  try {
    const { list, totalPages: pages } = await fetchPage(0)
    if (seq !== searchSeq) return
    if (mode.value === 'market' && !list.length && !marketRefreshed) {
      marketRefreshed = true; marketRefreshing.value = true
      try { await refreshNews() } catch {}
      marketRefreshing.value = false
      if (seq !== searchSeq) return
      const retry = await fetchPage(0)
      if (seq !== searchSeq) return
      results.value = retry.list; hasMore.value = retry.totalPages > 1; searched.value = true; markSeen(retry.list); return
    }
    results.value = list; page.value = 0; hasMore.value = pages > 1; searched.value = true; prefetchBodies(list); markSeen(list)
  } catch (e) { if (seq === searchSeq) error.value = e?.response?.data?.message || '加载失败' }
  finally { if (seq === searchSeq) loading.value = false }
}
const loadStock = async () => {
  const seq = ++searchSeq; loading.value = true; error.value = ''
  try { const res = await getStockEvents(activeSymbol.value, 30); if (seq !== searchSeq) return; applyStockData(res.data || {}); searched.value = true; if ((res.data || {}).analyzing) pollAnalysis() }
  catch (e) { if (seq === searchSeq) error.value = e?.response?.data?.message || '加载失败' }
  finally { if (seq === searchSeq) loading.value = false }
}
const loadMore = async () => { if (loadingMore.value || !hasMore.value) return; const seq = searchSeq; loadingMore.value = true; try { const next = page.value + 1; const { list, totalPages: pages } = await fetchPage(next); if (seq !== searchSeq) return; results.value = [...results.value, ...list]; page.value = next; hasMore.value = next < pages - 1 } catch {} finally { loadingMore.value = false } }

const selectMarket = () => { if (mode.value === 'market') return; stopAnalysisPolling(); mode.value = 'market'; activeSymbol.value = ''; load(); loadTopics('') }
const selectStock = (s) => { if (mode.value === 'stock' && activeSymbol.value === s) return; stopAnalysisPolling(); mode.value = 'stock'; activeSymbol.value = s; load(); loadTopics(s) }
// 搜索是常驻的独立搜索栏（不再藏在切换行里）：提交即进入搜索态；
// 切回大盘/个股就是退出搜索，不需要单独的"关闭搜索"动作
const submitSearch = () => { mode.value = 'search'; load() }

const marketRead = ref(null)
const marketReadAt = ref('')
// 今日信息面的三段式呈现（2026-10-28 体验重订）：
//   localStorage 缓存秒显（SWR）→ 骨架占位（第一次/缓存空）→ SSE 流式逐段流入。
// 修的是"每次进来都要等 LLM 生成，然后整块弹出把页面顶下去"的突兀感。
const marketReadSource = ref('')     // 'cache' | 'live'：缓存内容标注来源，时间戳旁边明示
const marketStreaming = ref('')      // 正在流入的 read 前缀（只有还没有可显内容时才用）
const marketPending = ref(false)     // 流未收线（决定骨架是否占位）
let marketEs = null
const MARKET_CACHE_KEY = 'news.marketRead.v1'

const loadCachedMarketRead = () => {
  try {
    const cached = JSON.parse(localStorage.getItem(MARKET_CACHE_KEY) || 'null')
    if (cached?.payload?.read) {
      marketRead.value = cached.payload
      marketReadSource.value = 'cache'
      const t = String(cached.savedAt || '').replace('T', ' ')
      marketReadAt.value = t ? t.slice(11, 16) : ''
    }
  } catch {}
}
const saveCachedMarketRead = (payload) => {
  try { localStorage.setItem(MARKET_CACHE_KEY, JSON.stringify({ savedAt: new Date().toISOString().slice(0, 19), payload })) } catch {}
}
const finishMarketRead = (data) => {
  marketStreaming.value = ''
  marketPending.value = false
  if (data && data.ok && data.read) {
    // 权威结果覆盖一切中间态（流式前缀可能被非流式重试推翻）
    marketRead.value = data
    marketReadSource.value = 'live'
    marketReadAt.value = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    saveCachedMarketRead(data)
  } else if (!marketRead.value) {
    // 拿不到且没有缓存可兜底：收起整块（与旧行为一致——"给不出结论"是合法状态）
    marketPending.value = false
  }
}
/** REST 兜底：SSE 通道整个不可用时回到旧的一次性加载 */
const loadMarketRead = async () => {
  try {
    const res = await getMarketRead()
    if (res.data?.read) { marketRead.value = res.data; marketReadSource.value = 'live'; marketReadAt.value = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }); saveCachedMarketRead(res.data) }
    else if (!marketRead.value) marketRead.value = null
  } catch { if (!marketRead.value) marketRead.value = null }
  finally { marketPending.value = false; marketStreaming.value = '' }
}
const openMarketReadStream = () => {
  const token = localStorage.getItem('token')
  if (!token) return
  marketPending.value = true
  try {
    marketEs = new EventSource(`/api/v1/news/market-read/stream?token=${encodeURIComponent(token)}`)
  } catch { loadMarketRead(); return }
  // 已有缓存内容时不做打字机（每次进来都重放一遍很吵）；流式前缀只在"没东西可显"时呈现
  marketEs.addEventListener('delta', (e) => {
    if (marketRead.value) return
    try { marketStreaming.value += JSON.parse(e.data) } catch {}
  })
  marketEs.addEventListener('result', (e) => {
    let data = null
    try { data = JSON.parse(e.data) } catch {}
    marketEs && marketEs.close(); marketEs = null
    finishMarketRead(data)
  })
  marketEs.onerror = () => {
    // 一次性流：不自动重连（EventSource 默认会重连，这里明确关掉），失败走 REST 兜底
    marketEs && marketEs.close(); marketEs = null
    if (!marketRead.value) loadMarketRead()
    else { marketPending.value = false; marketStreaming.value = '' }
  }
}

const topics = ref([])
const topicsLoading = ref(false)
let topicsSeq = 0
const loadTopics = async (symbol = '') => {
  const seq = ++topicsSeq; topicsLoading.value = true
  try { const res = await getNewsTopics(7, symbol); if (seq === topicsSeq) topics.value = Array.isArray(res.data) ? res.data : [] } catch { if (seq === topicsSeq) topics.value = [] }
  finally { if (seq === topicsSeq) topicsLoading.value = false }
}
const visibleTopics = computed(() => topics.value.slice(0, 5))
const firstSentence = (t) => { const c = String(t || '').trim(); const m = c.match(/^[^。！？；]{2,40}[。！？；]?/); return m ? m[0] : c.slice(0, 40) }
const topicDigest = (t) => t.digest || firstSentence(t.summary)
const expandedTopicId = ref(null)
const topicMembers = reactive({})
const topicLoading = reactive({})
const toggleTopic = async (topic) => {
  if (expandedTopicId.value === topic.id) { expandedTopicId.value = null; return }
  expandedTopicId.value = topic.id
  if (topicMembers[topic.id]) return
  topicLoading[topic.id] = true
  try { const res = await getTopicDetail(topic.id); topicMembers[topic.id] = res.data?.events || [] } catch { topicMembers[topic.id] = [] } finally { topicLoading[topic.id] = false }
}

onMounted(() => { loadFavorites(); loadCachedMarketRead(); openMarketReadStream(); loadTopics(''); load(); pollNewCount(); newCountTimer = setInterval(pollNewCount, 60000) })
onUnmounted(() => { stopAnalysisPolling(); if (newCountTimer) clearInterval(newCountTimer); if (marketEs) { marketEs.close(); marketEs = null } })
</script>

<template>
  <div>
    <div class="mb-5">
      <h1 class="text-[20px] font-semibold text-ink">资讯雷达</h1>
      <p class="mt-0.5 text-[12.5px] text-ink-3">AI 为你过滤噪音 · 不构成投资建议</p>
    </div>

    <!-- 今日信息面：位置先占住（缓存秒显/骨架），内容流式流入 —— 不再整块弹出（2026-10-28） -->
    <section
      v-if="mode !== 'hot' && (marketRead || marketStreaming || marketPending)"
      class="mb-4 rounded-xl border border-border bg-surface p-4"
    >
      <div class="mb-2 flex items-baseline justify-between">
        <h2 class="text-[14px] font-semibold text-ink">今日信息面</h2>
        <span v-if="marketReadAt" class="num text-[12px] text-ink-3">
          {{ marketReadAt }}<template v-if="marketReadSource === 'cache'"> · 缓存</template>
        </span>
      </div>

      <!-- 有内容（缓存秒显或流式收线后的权威结果）：静态呈现 -->
      <template v-if="marketRead">
        <p class="text-[13.5px] leading-7 text-ink">{{ marketRead.read }}</p>
        <div v-if="marketRead.themes && marketRead.themes.length" class="mt-2.5 grid gap-1.5">
          <div v-for="t in marketRead.themes" :key="t.topic" class="flex gap-2.5 text-[13px] leading-relaxed">
            <span class="w-28 shrink-0 text-ink-2">{{ t.topic }}<template v-if="t.count"> · {{ t.count }} 条</template></span>
            <span class="min-w-0 text-ink">{{ t.line }}</span>
          </div>
        </div>
        <p v-if="marketRead.conflicts" class="mt-2 flex items-start gap-1.5 text-[12.5px] text-ink-2">
          <AlertTriangle :size="14" :stroke-width="1.75" class="mt-0.5 shrink-0 text-warn" aria-hidden="true" />{{ marketRead.conflicts }}
        </p>
      </template>

      <!-- 正在流入（首次/缓存空）：read 前缀逐段出现 -->
      <p v-else-if="marketStreaming" class="text-[13.5px] leading-7 text-ink">
        {{ marketStreaming }}<span class="animate-pulse text-ink-3" aria-hidden="true">▍</span>
      </p>

      <!-- 还没有任何内容：骨架占位，位置先显示出来 -->
      <div v-else class="space-y-2.5" role="status" aria-label="正在生成今日信息面">
        <Skeleton width="58%" height="14px" />
        <Skeleton width="94%" height="14px" />
        <Skeleton width="80%" height="14px" />
        <p class="pt-0.5 text-[12px] text-ink-3">正在生成今日信息面…</p>
      </div>
    </section>

    <!-- 新消息横幅 -->
    <button
      v-if="mode === 'market' && newCount > 0"
      type="button"
      class="mb-3 w-full rounded-lg border border-accent bg-accent-soft py-2 text-[13px] text-accent transition-colors hover:bg-accent hover:text-on-accent"
      @click="loadNewest"
    >
      有 {{ newCount }} 条新资讯 · 点击载入
    </button>

    <!-- 信息流切换：大盘 / 自选个股 / 热榜（搜索独立成常驻搜索栏，不挤在切换行里） -->
    <div class="mb-3 flex flex-wrap items-center gap-2" role="tablist" aria-label="资讯流">
      <button
        type="button"
        class="h-[30px] rounded-full border px-3.5 text-[13px] transition-colors"
        :class="mode === 'market' ? 'border-accent bg-accent text-on-accent' : 'border-border bg-surface text-ink-2 hover:bg-surface-2 hover:text-ink'"
        @click="selectMarket"
      >大盘</button>
      <button
        v-for="fav in favorites"
        :key="fav.symbol"
        type="button"
        class="h-[30px] rounded-full border px-3.5 text-[13px] transition-colors"
        :class="mode === 'stock' && activeSymbol === fav.symbol ? 'border-accent bg-accent text-on-accent' : 'border-border bg-surface text-ink-2 hover:bg-surface-2 hover:text-ink'"
        @click="selectStock(fav.symbol)"
      >{{ chipLabel(fav) }}</button>
      <button
        type="button"
        class="h-[30px] rounded-full border px-3.5 text-[13px] transition-colors"
        :class="mode === 'hot' ? 'border-accent bg-accent text-on-accent' : 'border-border bg-surface text-ink-2 hover:bg-surface-2 hover:text-ink'"
        @click="selectHot"
      >热榜</button>
    </div>

    <!-- 搜索栏（常驻独立）：提交即进入搜索态；点大盘/个股切回信息流 -->
    <form
      class="mb-4 flex flex-wrap gap-2.5 rounded-xl border bg-surface p-3 transition-colors"
      :class="mode === 'search' ? 'border-accent' : 'border-border'"
      @submit.prevent="submitSearch"
    >
      <input
        v-model.trim="form.keyword"
        type="search"
        placeholder="关键词（如：回购 / 重组）"
        class="h-[34px] min-w-[180px] flex-1 rounded-lg border border-border bg-bg px-3 text-[13px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
      >
      <input
        v-model.trim="form.symbol"
        type="text"
        placeholder="600519"
        class="num h-[34px] w-[100px] rounded-lg border border-border bg-bg px-3 text-[13px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
      >
      <select
        v-model.number="form.days"
        class="h-[34px] rounded-lg border border-border bg-bg px-2.5 text-[13px] text-ink focus:border-accent focus:outline-none"
      >
        <option v-for="d in DAY_OPTIONS" :key="d" :value="d">{{ d }} 天内</option>
      </select>
      <Button type="submit" variant="primary" :loading="loading">搜索</Button>
    </form>

    <!-- 要闻专题：加载中也占位（骨架），不再弹入（2026-10-29 移到切换/搜索栏之下） -->
    <section v-if="mode !== 'hot' && (visibleTopics.length || topicsLoading)" class="mb-4 rounded-xl border border-border bg-surface p-4">
      <h2 class="mb-1 text-[14px] font-semibold text-ink">要闻专题</h2>
      <div v-if="!visibleTopics.length && topicsLoading" class="space-y-3 py-1.5" role="status" aria-label="正在加载要闻专题">
        <div v-for="i in 3" :key="i" class="space-y-1.5">
          <Skeleton :width="i === 2 ? '62%' : '45%'" height="13px" />
          <Skeleton width="88%" height="12px" />
        </div>
      </div>
      <article v-for="topic in visibleTopics" :key="topic.id" class="border-b border-border py-2.5 last:border-b-0">
        <button type="button" class="flex w-full items-start justify-between gap-3 text-start" @click="toggleTopic(topic)">
          <span class="flex items-start gap-1.5 text-[13.5px] font-medium leading-relaxed text-ink">
            <ChevronRight
              :size="13"
              :stroke-width="2"
              class="mt-1 shrink-0 transition-transform duration-150"
              :class="expandedTopicId === topic.id ? 'rotate-90' : ''"
              aria-hidden="true"
            />{{ topic.title }}
          </span>
          <span class="num flex shrink-0 items-center gap-1.5 pt-px text-[12px] text-ink-3">
            <Badge v-if="topic.direction" :tone="directionTone(topic.direction)">{{ topic.direction }}</Badge>
            {{ topic.eventCount }} 条<template v-if="topic.sourceCount > 1"> · {{ topic.sourceCount }} 源</template>
          </span>
        </button>
        <p class="mt-1 cursor-pointer pl-[18.5px] text-[12.5px] leading-relaxed text-ink-2" @click="toggleTopic(topic)">{{ topicDigest(topic) }}</p>
        <div v-if="expandedTopicId === topic.id" class="mt-2 border-t border-dashed border-border pl-[18.5px] pt-2">
          <p class="mb-2 text-[13px] leading-7 text-ink-2">{{ topic.summary }}</p>
          <div v-if="topicLoading[topic.id]" class="text-[12.5px] text-ink-3">加载报道中…</div>
          <ul v-else class="flex flex-col gap-1.5">
            <li v-for="ev in topicMembers[topic.id] || []" :key="ev.id" class="flex items-baseline gap-2 text-[12.5px]">
              <span class="num shrink-0 text-[11.5px] text-ink-3">{{ formatTime(ev) }}</span>
              <span class="shrink-0 text-[11.5px] text-ink-3">{{ ev.sourceName }}</span>
              <span class="text-ink-2">{{ ev.title }}</span>
            </li>
          </ul>
        </div>
      </article>
    </section>

    <!-- 热榜（东财个股人气榜）：点一行直达该股资讯 -->
    <section v-if="mode === 'hot'" class="rounded-xl border border-border bg-surface p-4">
      <div class="mb-1 flex items-baseline justify-between">
        <h2 class="text-[14px] font-semibold text-ink">人气榜</h2>
        <span class="text-[11.5px] text-ink-3">市场关注度排名，非买卖建议 · 5 分钟更新</span>
      </div>
      <p v-if="hotError" class="py-4 text-[13px] text-ink-2">{{ hotError }}</p>
      <ul v-else>
        <li
          v-for="item in hotRank"
          :key="item.symbol"
          class="flex cursor-pointer items-center gap-3 border-b border-border py-2.5 last:border-b-0 hover:bg-surface-2/60"
          @click="selectStock(item.symbol)"
        >
          <span class="num w-7 shrink-0 text-center text-[13px] font-semibold"
                :class="item.rank <= 3 ? 'text-accent' : 'text-ink-3'">{{ item.rank }}</span>
          <span class="min-w-0 flex-1 truncate text-[13.5px] text-ink">
            {{ item.name }}
            <span class="num ml-1 text-[11.5px] text-ink-3">{{ String(item.symbol || '').replace(/^(SH|SZ|BJ)/, '') }}</span>
          </span>
          <span class="num shrink-0 text-[13px] text-ink-2">{{ item.price }}</span>
          <span
            class="num w-[64px] shrink-0 text-right text-[13px] font-medium"
            :class="item.change_pct > 0 ? 'text-up' : item.change_pct < 0 ? 'text-down' : 'text-ink-3'"
          >{{ item.change_pct > 0 ? '+' : '' }}{{ item.change_pct }}%</span>
        </li>
      </ul>
      <!-- 热榜自己的展开：默认 20 → 上限 50（上游拉全榜 5 分钟缓存，展开零额外成本） -->
      <Button
        v-if="!hotError && hotRank.length && hotLimit < HOT_MAX"
        variant="secondary"
        class="mx-auto my-3 block"
        :loading="hotLoading"
        @click="loadMoreHot"
      >展开到前 {{ HOT_MAX }} 名</Button>
    </section>

    <!-- 频道 -->
    <div v-if="mode === 'market'" class="mb-3 flex flex-wrap gap-1">
      <button
        v-for="(ch, key) in CHANNELS"
        :key="key"
        type="button"
        class="h-[26px] rounded-full px-3 text-[12.5px] transition-colors max-sm:h-[34px]"
        :class="channel === key ? 'bg-accent text-on-accent' : 'text-ink-3 hover:bg-surface-2 hover:text-ink-2'"
        @click="selectChannel(key)"
      >{{ ch.label }}</button>
    </div>

    <!-- 状态 -->
    <p v-if="marketRefreshing" class="my-2 text-[13px] text-ink-2" role="status">首次查看，正在同步资讯…</p>
    <p v-if="error" class="my-2 text-[13px] text-up" role="alert">
      {{ error }}
      <button type="button" class="ml-1 text-accent underline underline-offset-2" @click="load">重试</button>
    </p>

    <!-- 加载骨架 -->
    <div v-if="loading && !results.length && !error" class="space-y-1">
      <div v-for="i in 3" :key="i" class="space-y-2 border-b border-border py-4.5">
        <Skeleton width="18%" height="12px" />
        <Skeleton width="70%" height="16px" />
        <Skeleton width="45%" height="13px" />
      </div>
    </div>

    <Empty v-else-if="mode !== 'hot' && !loading && !error && searched && results.length === 0" :title="emptyHint">
      <template #icon><Radar :size="20" :stroke-width="1.5" /></template>
    </Empty>

    <!-- 新闻列表（displaySections 在热榜模式下为空，列表自然让位） -->
    <section v-for="sec in displaySections" :key="sec.key">
      <h2 v-if="sec.category" class="mt-4 mb-1 flex items-baseline gap-2 text-[12px] font-semibold text-ink-2">
        {{ sec.category }} <span class="num font-normal text-ink-3">{{ sec.items.length }}</span>
      </h2>
      <ul>
        <li
          v-for="event in sec.items"
          :key="event.id"
          class="cursor-pointer border-b border-border px-1 py-4 transition-colors hover:bg-surface-2/60"
          @click="openReader(event)"
        >
          <p class="num mb-1 text-[11.5px] text-ink-3">{{ formatDateOnly(event) }}</p>
          <h3 class="mb-1 text-[15.5px] font-semibold leading-snug text-ink">{{ event.title }}</h3>
          <p v-if="interpretationOf(event)" class="mb-1.5 line-clamp-2 text-[13px] leading-relaxed text-ink-2">{{ interpretationOf(event) }}</p>
          <div class="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[12px] text-ink-3">
            <Badge v-if="event.direction" :tone="directionTone(event.direction)">{{ event.direction }}</Badge>
            <span class="text-ink-2">{{ event.sourceName }}</span>
            <span class="num">{{ formatTime(event) }}</span>
            <Badge v-if="event.credibilityGrade" :tone="event.credibilityGrade === '高' || event.credibilityGrade === '较高' ? 'accent' : 'neutral'">
              {{ event.credibilityGrade }}
            </Badge>
            <Badge v-if="event.rumorFlag" tone="warn">传闻</Badge>
          </div>
        </li>
      </ul>
    </section>

    <!-- 加载更多只属于新闻列表：热榜视图里不显示（热榜固定 20 条无分页） -->
    <Button v-if="hasMore && mode !== 'hot'" variant="secondary" class="mx-auto my-5 block" :loading="loadingMore" @click="loadMore">
      加载更多
    </Button>

    <!-- 沉浸式阅读器 -->
    <Teleport to="body">
      <div
        v-if="readerEvent"
        class="fixed inset-0 z-50 flex items-end justify-center p-2 sm:items-center sm:p-5"
        :style="{ background: 'var(--overlay)' }"
        @click.self="closeReader"
      >
        <article
          class="relative grid max-h-[94dvh] w-full gap-7 overflow-y-auto rounded-t-2xl border border-border bg-surface p-6 sm:max-h-[88dvh] sm:w-[min(880px,100%)] sm:rounded-xl sm:p-8 md:grid-cols-[1fr_260px]"
          role="dialog"
          aria-modal="true"
          :aria-label="readerEvent.title"
          style="box-shadow: var(--shadow-pop)"
        >
          <button
            type="button"
            class="absolute right-3 top-3 flex h-9 w-9 items-center justify-center rounded-lg text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
            aria-label="关闭"
            @click="closeReader"
          >
            <X :size="18" :stroke-width="2" aria-hidden="true" />
          </button>

          <div class="min-w-0">
            <p class="num mb-1.5 text-[12px] text-ink-3">{{ formatDateOnly(readerEvent) }}</p>
            <h1 class="mb-2.5 pr-10 text-[20px] font-bold leading-snug text-ink">{{ readerEvent.title }}</h1>
            <div class="num mb-4 flex items-center gap-2.5 border-b border-border pb-3.5 text-[12.5px] text-ink-2">
              <span>{{ readerEvent.sourceName }}</span>
              <span>{{ formatTime(readerEvent) }}</span>
            </div>

            <div class="mb-4">
              <p v-if="bodyState(readerEvent) === 'loading'" class="text-[13.5px] text-ink-2">正在获取全文…</p>
              <template v-else-if="bodyState(readerEvent) === 'error'">
                <p class="text-[13.5px] text-ink-2">
                  {{ bodyErrors[readerEvent.id] || '全文暂未抓到' }}
                  <a v-if="readerEvent.url" :href="readerEvent.url" target="_blank" rel="noopener noreferrer" class="text-accent underline underline-offset-2">去原文</a>
                </p>
                <div v-if="readerEvent.content" class="mt-2.5 whitespace-pre-line rounded-lg bg-surface-2 p-3 text-[13px] leading-7 text-ink-2">{{ cleanWireContent(readerEvent.content) }}</div>
              </template>
              <div v-else class="whitespace-pre-line break-words text-[14.5px] leading-8 text-ink">{{ bodyText(readerEvent) }}</div>
            </div>

            <div class="flex flex-wrap items-center justify-between gap-3">
              <a
                v-if="readerEvent.url"
                class="text-[13px] text-accent underline underline-offset-2"
                :href="readerEvent.url"
                target="_blank"
                rel="noopener noreferrer"
              >查看原文 ↗</a>
              <div class="flex gap-2">
                <Button v-if="readerEvent.symbol" size="sm" @click="goChart(readerEvent.symbol)">看K线</Button>
                <Button size="sm" @click="askAbout(readerEvent)">问 AI</Button>
              </div>
            </div>

            <div v-if="readerRelated.length" class="mt-5 border-t border-border pt-4">
              <span class="mb-2.5 block text-[11.5px] font-semibold text-ink-2">相关报道</span>
              <ul class="flex flex-col gap-0.5">
                <li
                  v-for="rel in readerRelated"
                  :key="rel.id"
                  class="flex cursor-pointer items-baseline gap-2.5 rounded-lg px-2 py-1.5 transition-colors hover:bg-surface-2"
                  @click.stop="openReader(rel)"
                >
                  <span class="num shrink-0 text-[11.5px] text-ink-3">{{ formatTime(rel) }}</span>
                  <span class="text-[12.5px] leading-relaxed text-ink-2">{{ rel.title }}</span>
                </li>
              </ul>
            </div>
          </div>

          <aside
            v-if="readerEvent.direction || interpretationOf(readerEvent)"
            class="h-max rounded-xl bg-surface-2 p-4 md:sticky md:top-0"
          >
            <span class="mb-2 block text-[11.5px] font-semibold text-ink-2">AI 解读</span>
            <div v-if="readerEvent.direction" class="mb-2 flex items-center gap-2">
              <Badge :tone="directionTone(readerEvent.direction)">{{ readerEvent.direction }}</Badge>
              <span v-if="readerEvent.confidence" class="num text-[12px] text-ink-2">{{ Math.round(readerEvent.confidence * 100) }}%</span>
            </div>
            <p v-if="interpretationOf(readerEvent)" class="text-[12.5px] leading-relaxed text-ink-2">{{ interpretationOf(readerEvent) }}</p>
            <p class="mt-2.5 border-t border-border pt-2 text-[11px] text-ink-3">仅供参考 · 不构成投资建议</p>
          </aside>
        </article>
      </div>
    </Teleport>

    <ChatDrawer :open="chatOpen" :focus-event="chatFocus" @close="chatOpen = false" />
  </div>
</template>
