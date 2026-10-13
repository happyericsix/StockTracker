<template>
  <div class="page">
    <div class="page-head">
      <h1>资讯雷达</h1>
      <p class="page-sub">AI 为你过滤噪音 · 不构成投资建议</p>
    </div>

    <!-- 今日信息面 -->
    <section v-if="marketRead" class="market-read card">
      <div class="market-read-head">
        <h2>今日信息面</h2>
        <span class="market-read-time num">{{ marketReadAt }}</span>
      </div>
      <p class="market-read-text">{{ marketRead.read }}</p>
      <div v-if="marketRead.themes && marketRead.themes.length" class="market-themes">
        <div v-for="t in marketRead.themes" :key="t.topic" class="market-theme">
          <span class="theme-name">{{ t.topic }}<template v-if="t.count"> · {{ t.count }} 条</template></span>
          <span class="theme-line">{{ t.line }}</span>
        </div>
      </div>
      <p v-if="marketRead.conflicts" class="market-conflicts">⚠ {{ marketRead.conflicts }}</p>
    </section>

    <!-- 要闻专题 -->
    <section v-if="visibleTopics.length" class="topics card">
      <div class="topics-head"><h2>要闻专题</h2></div>
      <article v-for="topic in visibleTopics" :key="topic.id" class="topic-card">
        <button type="button" class="topic-row" @click="toggleTopic(topic)">
          <span class="topic-title">
            <AppIcon name="chevron-right" :size="13" :stroke-width="2" class="title-caret" :class="{ open: expandedTopicId === topic.id }" />{{ topic.title }}</span>
          <span class="topic-meta num">
            <span v-if="topic.direction" class="direction" :class="directionClass(topic.direction)">{{ topic.direction }}</span>
            {{ topic.eventCount }} 条<template v-if="topic.sourceCount > 1"> · {{ topic.sourceCount }} 源</template>
          </span>
        </button>
        <p class="topic-digest" @click="toggleTopic(topic)">{{ topicDigest(topic) }}</p>
        <div v-if="expandedTopicId === topic.id" class="topic-detail">
          <p class="topic-story">{{ topic.summary }}</p>
          <div v-if="topicLoading[topic.id]" class="body-status">加载报道中…</div>
          <ul v-else class="topic-members">
            <li v-for="ev in topicMembers[topic.id] || []" :key="ev.id" class="topic-member">
              <span class="time num">{{ formatTime(ev) }}</span>
              <span class="member-source">{{ ev.sourceName }}</span>
              <span class="member-title">{{ ev.title }}</span>
            </li>
          </ul>
        </div>
      </article>
    </section>

    <!-- 新消息横幅 -->
    <button v-if="mode === 'market' && newCount > 0" type="button" class="new-alert" @click="loadNewest">
      有 {{ newCount }} 条新资讯 · 点击载入
    </button>

    <!-- 信息流切换 -->
    <div class="feed-tabs" role="tablist" aria-label="资讯流">
      <button type="button" class="feed-chip" :class="{ active: mode === 'market' }" @click="selectMarket">大盘</button>
      <button v-for="fav in favorites" :key="fav.symbol" type="button" class="feed-chip"
        :class="{ active: mode === 'stock' && activeSymbol === fav.symbol }" @click="selectStock(fav.symbol)">{{ chipLabel(fav) }}</button>
      <button type="button" class="feed-chip search-toggle" :class="{ active: mode === 'search' }" @click="toggleSearch">搜索</button>
    </div>

    <!-- 频道 -->
    <div v-if="mode === 'market'" class="channel-tabs">
      <button v-for="(ch, key) in CHANNELS" :key="key" type="button" class="channel-chip"
        :class="{ active: channel === key }" @click="selectChannel(key)">{{ ch.label }}</button>
    </div>

    <!-- 搜索面板 -->
    <form v-if="searchOpen" class="search-form" @submit.prevent="load">
      <input v-model.trim="form.keyword" type="search" placeholder="关键词（如：回购 / 重组）" class="search-input keyword" />
      <input v-model.trim="form.symbol" type="text" placeholder="600519" class="search-input symbol" />
      <select v-model.number="form.days" class="search-select">
        <option v-for="d in DAY_OPTIONS" :key="d" :value="d">{{ d }} 天内</option>
      </select>
      <button type="submit" class="primary-btn" :disabled="loading">{{ loading ? '搜索中…' : '搜索' }}</button>
    </form>

    <!-- 状态 -->
    <p v-if="marketRefreshing" class="progress-line" role="status">首次查看，正在同步资讯…</p>
    <p v-if="error" class="error" role="alert">{{ error }} <button type="button" class="inline-action" @click="load">重试</button></p>
    <div v-if="!loading && !error && searched && results.length === 0" class="empty">{{ emptyHint }}</div>

    <!-- 新闻列表（苹果 Newsroom 风） -->
    <section v-for="sec in displaySections" :key="sec.key" class="folder">
      <h2 v-if="sec.category" class="folder-title">{{ sec.category }} <span class="folder-count num">{{ sec.items.length }}</span></h2>
      <ul class="result-list">
        <li v-for="event in sec.items" :key="event.id" class="news-card" @click="openReader(event)">
          <p class="card-date num">{{ formatDateOnly(event) }}</p>
          <h3 class="card-title">{{ event.title }}</h3>
          <p v-if="interpretationOf(event)" class="card-summary">{{ interpretationOf(event) }}</p>
          <div class="card-meta">
            <span v-if="event.direction" class="direction" :class="directionClass(event.direction)">{{ event.direction }}</span>
            <span class="card-source">{{ event.sourceName }}</span>
            <span class="card-time num">{{ formatTime(event) }}</span>
            <span v-if="event.credibilityGrade" class="credibility" :class="credibilityClass(event.credibilityGrade)">{{ event.credibilityGrade }}</span>
            <span v-if="event.rumorFlag" class="rumor-tag">传闻</span>
          </div>
        </li>
      </ul>
    </section>

    <button v-if="hasMore" type="button" class="load-more" :disabled="loadingMore" @click="loadMore">
      {{ loadingMore ? '加载中…' : '加载更多' }}
    </button>

    <!-- 沉浸式阅读页 -->
    <Teleport to="body">
      <div v-if="readerEvent" class="reader-overlay" @click.self="closeReader">
        <article class="reader-page" role="dialog" aria-modal="true">
          <button type="button" class="reader-close" aria-label="关闭" @click="closeReader">
            <AppIcon name="close" :size="18" :stroke-width="2" />
          </button>
          <div class="reader-main">
            <p class="reader-date num">{{ formatDateOnly(readerEvent) }}</p>
            <h1 class="reader-title">{{ readerEvent.title }}</h1>
            <div class="reader-meta">
              <span>{{ readerEvent.sourceName }}</span>
              <span class="num">{{ formatTime(readerEvent) }}</span>
            </div>
            <div class="reader-body">
              <p v-if="bodyState(readerEvent) === 'loading'" class="body-status">正在获取全文…</p>
              <template v-else-if="bodyState(readerEvent) === 'error'">
                <p class="body-status">{{ bodyErrors[readerEvent.id] || '全文暂未抓到' }}
                  <a v-if="readerEvent.url" :href="readerEvent.url" target="_blank" rel="noopener noreferrer">去原文</a>
                </p>
                <div v-if="readerEvent.content" class="reader-content-alt">{{ cleanWireContent(readerEvent.content) }}</div>
              </template>
              <div v-else class="reader-content">{{ bodyText(readerEvent) }}</div>
            </div>
            <div class="reader-footer">
              <a v-if="readerEvent.url" class="source-link" :href="readerEvent.url" target="_blank" rel="noopener noreferrer">查看原文 ↗</a>
              <div class="reader-actions">
                <button v-if="readerEvent.symbol" type="button" class="reader-btn" @click="goChart(readerEvent.symbol)">看K线</button>
                <button type="button" class="reader-btn" @click="askAbout(readerEvent)">问 AI</button>
              </div>
            </div>
            <div v-if="readerRelated.length" class="reader-related">
              <span class="related-label">相关报道</span>
              <ul class="related-list">
                <li v-for="rel in readerRelated" :key="rel.id" class="related-item" @click.stop="openReader(rel)">
                  <span class="related-time num">{{ formatTime(rel) }}</span>
                  <span class="related-title">{{ rel.title }}</span>
                </li>
              </ul>
            </div>
          </div>
          <aside v-if="readerEvent.direction || interpretationOf(readerEvent)" class="reader-aside">
            <span class="aside-label">AI 解读</span>
            <div v-if="readerEvent.direction" class="aside-direction">
              <span class="direction" :class="directionClass(readerEvent.direction)">{{ readerEvent.direction }}</span>
              <span v-if="readerEvent.confidence" class="num">{{ Math.round(readerEvent.confidence * 100) }}%</span>
            </div>
            <p v-if="interpretationOf(readerEvent)" class="aside-text">{{ interpretationOf(readerEvent) }}</p>
            <p class="aside-disclaimer">仅供参考 · 不构成投资建议</p>
          </aside>
        </article>
      </div>
    </Teleport>

    <ChatDrawer :open="chatOpen" :focus-event="chatFocus" @close="chatOpen = false" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import ChatDrawer from '../components/ChatDrawer.vue'
import { searchNews, getStockEvents, refreshNews, getEventBody, getStockRead, getMarketRead, getNewsTopics, getTopicDetail, countNewsSince } from '../api/news.js'
import { getFavorites, getFavoritesBrief } from '../api/stock.js'
import { getSentiment } from '../api/market.js'

const router = useRouter()
const chatOpen = ref(false)
const chatFocus = ref(null)
const askAbout = (event) => { chatFocus.value = event; chatOpen.value = true }

const DAY_OPTIONS = [3, 7, 30, 90]
const form = reactive({ keyword: '', symbol: '', types: [], days: 7 })
const mode = ref('market')
const activeSymbol = ref('')
const searchOpen = ref(false)

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
  try { const b = await getFavoritesBrief(); favorites.value = (Array.isArray(b.data) ? b.data : []).map(s => ({ symbol: s, name: '' })) } catch { favorites.value = [] }
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

const directionClass = (d) => d === '利好' ? 'bull' : d === '利空' ? 'bear' : 'flat'
const credibilityClass = (g) => g === '高' || g === '较高' ? 'cred-good' : 'cred-mid'
const formatTime = (e) => { if (!e?.publishedAt) return ''; const t = String(e.publishedAt).replace('T',' '); return e.dateOnly ? t.slice(0,10) : t.slice(0,16) }
const formatDateOnly = (e) => { if (!e?.publishedAt) return ''; const t = String(e.publishedAt); return `${parseInt(t.slice(5,7))}月${parseInt(t.slice(8,10))}日` }
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
  if (event.symbol) { try { const res = await getStockEvents(event.symbol, 7); readerRelated.value = (res.data?.events || []).filter(e => e.id !== event.id).slice(0,5) } catch {} }
}
const closeReader = () => { readerEvent.value = null; readerRelated.value = [] }

const bodyText = (e) => bodies[e.id] || e.body || cleanWireContent(e.content)
const cleanWireContent = (raw) => {
  if (!raw || raw.length < 30) return ''
  let t = raw
  const b = t.match(/^【[^】]{4,}】/)
  if (b) t = t.slice(b[0].length)
  return t.replace(/\r/g,'').replace(/([^\n])\n([^\n])/g,'$1$2').replace(/ {2,}/g,' ').trim().length >= 30 ? t.trim() : ''
}
const bodyState = (e) => bodyLoading[e.id] ? 'loading' : bodyErrors[e.id] ? 'error' : bodyText(e) ? 'ok' : 'none'
const interpretationOf = (e) => (!e.analyzed || !e.plainSummary) ? '' : e.plainSummary
const prefetchBodies = (items) => { items.filter(e => e.url && !bodyText(e) && !bodyErrors[e.id] && !bodies[e.id]).slice(0,3).forEach(e => { getEventBody(e.id).then(r => bodies[e.id] = r.data).catch(() => {}) }) }

const CATEGORY_ORDER = ['公司动态','业绩财务','行业关联','宏观经济','政策监管','地缘政治','舆论热度','市场快讯']
const activeCategory = ref('')
const groupedResults = computed(() => {
  const groups = []
  for (const c of CATEGORY_ORDER) { const items = results.value.filter(e => e.category === c); if (items.length) groups.push({ category: c, items }) }
  const rest = results.value.filter(e => !CATEGORY_ORDER.includes(e.category))
  if (rest.length) groups.push({ category: '其他', items: rest })
  return groups
})
const displaySections = computed(() => {
  if (!results.value.length) return []
  if (mode.value === 'search') return [{ key: 'flat', category: '', items: results.value }]
  return groupedResults.value.map(g => ({ key: g.category, category: g.category, items: g.items }))
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
if (!lastSeenAt) { lastSeenAt = new Date().toISOString().slice(0,19); try { localStorage.setItem(LAST_SEEN_KEY, lastSeenAt) } catch {} }
const newCount = ref(0)
const pollNewCount = () => { countNewsSince(lastSeenAt).then(r => newCount.value = Number(r.data) || 0).catch(() => {}) }
const loadNewest = () => { newCount.value = 0; load() }
let newCountTimer = null
const markSeen = (list) => { try { const newest = list.map(e => String(e.publishedAt || '')).filter(Boolean).sort().pop(); if (newest && newest > lastSeenAt) lastSeenAt = newest; localStorage.setItem(LAST_SEEN_KEY, lastSeenAt) } catch {}; newCount.value = 0 }

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
const toggleSearch = () => { searchOpen.value = !searchOpen.value; if (searchOpen.value) mode.value = 'search'; else if (mode.value === 'search') selectMarket() }

const marketRead = ref(null)
const marketReadAt = ref('')
const loadMarketRead = async () => { try { const res = await getMarketRead(); if (res.data?.read) { marketRead.value = res.data; marketReadAt.value = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) } else marketRead.value = null } catch { marketRead.value = null } }

const topics = ref([])
let topicsSeq = 0
const loadTopics = async (symbol = '') => { const seq = ++topicsSeq; try { const res = await getNewsTopics(7, symbol); if (seq === topicsSeq) topics.value = Array.isArray(res.data) ? res.data : [] } catch { topics.value = [] } }
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

onMounted(() => { loadFavorites(); loadMarketRead(); loadTopics(''); load(); pollNewCount(); newCountTimer = setInterval(pollNewCount, 60000) })
onUnmounted(() => { stopAnalysisPolling(); if (newCountTimer) clearInterval(newCountTimer) })
</script>

<style scoped>
.page-head h1 { font-size: 24px; font-weight: 700; }
.page-sub { margin: 4px 0 0; font-size: 13px; color: var(--color-text-muted); }

/* 今日信息面 */
.market-read { margin-bottom: 16px; padding: 14px 16px; }
.market-read-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 8px; }
.market-read-head h2 { font-size: 15px; margin: 0; }
.market-read-time { font-size: 12px; color: var(--color-text-muted); }
.market-read-text { margin: 0 0 10px; font-size: 13.5px; line-height: 1.7; }
.market-themes { display: grid; gap: 6px; margin-bottom: 8px; }
.market-theme { display: flex; gap: 10px; font-size: 13px; line-height: 1.6; }
.market-theme .theme-name { flex: none; color: var(--color-text-secondary); }
.market-theme .theme-line { color: var(--color-text-primary); min-width: 0; }
.market-conflicts { margin: 0; font-size: 12.5px; color: var(--color-text-muted); }

/* 要闻专题 */
.topics { margin-bottom: 16px; padding: 14px 16px; }
.topics-head h2 { font-size: 15px; margin: 0 0 10px; }
.topic-card { border-bottom: 1px solid var(--color-border); padding: 10px 0; }
.topic-card:last-of-type { border-bottom: none; }
.topic-row { display: flex; width: 100%; align-items: flex-start; justify-content: space-between; gap: 12px; background: none; border: none; cursor: pointer; padding: 0; text-align: left; }
.topic-title { display: flex; align-items: center; gap: 6px; font-size: 14px; font-weight: 500; color: var(--color-text-primary); line-height: 1.5; }
.title-caret { flex: none; transition: transform var(--duration-fast) var(--ease-out); }
.title-caret.open { transform: rotate(90deg); }
.topic-meta { display: flex; align-items: center; gap: 6px; flex: none; font-size: 12px; color: var(--color-text-muted); }
.topic-digest { margin: 4px 0 0; font-size: 13px; color: var(--color-text-secondary); line-height: 1.6; cursor: pointer; }
.topic-detail { margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--color-border); }
.topic-story { margin: 0 0 8px; font-size: 13.5px; line-height: 1.7; color: var(--color-text-secondary); }
.topic-members { display: flex; flex-direction: column; gap: 6px; }
.topic-member { display: flex; align-items: baseline; gap: 8px; font-size: 13px; }
.topic-member .time { flex: none; font-size: 12px; color: var(--color-text-muted); }
.topic-member .member-source { flex: none; color: var(--color-text-muted); font-size: 12px; }
.topic-member .member-title { color: var(--color-text-secondary); }

/* 新消息横幅 */
.new-alert { display: block; width: 100%; margin: 10px 0 12px; padding: 8px 14px; border: 1px solid var(--color-accent); border-radius: 8px; background: var(--color-accent-soft); color: var(--color-accent); font-size: 13px; cursor: pointer; text-align: center; }

/* 信息流切换 */
.feed-tabs { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 12px; }
.feed-chip { padding: 6px 14px; border: 1px solid var(--color-border-strong); border-radius: 999px; background: var(--color-bg-surface); color: var(--color-text-secondary); font-size: 13px; cursor: pointer; transition: all var(--duration-fast) var(--ease-out); }
.feed-chip:hover { background: var(--color-bg-subtle); color: var(--color-text-primary); }
.feed-chip.active { border-color: var(--color-bg-inverse); background: var(--color-bg-inverse); color: #fff; }
.feed-chip:active { transform: scale(0.97); }

/* 频道 */
.channel-tabs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.channel-chip { padding: 4px 12px; border-radius: 999px; border: none; background: transparent; color: var(--color-text-muted); font-size: 12.5px; cursor: pointer; }
.channel-chip.active { background: var(--color-accent); color: #fff; }
.channel-chip:hover:not(.active) { background: var(--color-bg-subtle); }

/* 搜索 */
.search-form { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; padding: 12px; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-bg-surface); }
.search-input, .search-select { padding: 8px 12px; border: 1px solid var(--color-border); border-radius: var(--radius-sm); font-size: 13px; background: var(--color-bg); color: var(--color-text-primary); }
.search-input:focus, .search-select:focus { outline: none; border-color: var(--color-accent); }
.search-input.keyword { flex: 1; min-width: 180px; }
.search-input.symbol { width: 100px; }
.primary-btn { padding: 8px 18px; border: none; border-radius: var(--radius-sm); background: var(--color-accent); color: #fff; font-size: 13px; cursor: pointer; }
.primary-btn:disabled { opacity: 0.6; cursor: default; }

/* 状态 */
.progress-line { font-size: 13px; color: var(--color-text-muted); margin: 8px 0; }
.error { color: var(--color-loss); font-size: 13px; margin: 8px 0; }
.empty { color: var(--color-text-muted); padding: 32px 0; text-align: center; font-size: 14px; }

/* 新闻列表（苹果 Newsroom 风） */
.folder { margin-bottom: 8px; }
.folder-title { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: var(--color-text-muted); margin: 12px 0 8px; }
.folder-count { font-weight: 400; }
.result-list { list-style: none; margin: 0; padding: 0; }
.news-card { padding: 18px 4px; border-bottom: 1px solid var(--color-border); cursor: pointer; transition: background var(--duration-fast) var(--ease-out); }
.news-card:hover { background: var(--color-bg-subtle); }
.card-date { margin: 0 0 4px; font-size: 12px; color: var(--color-text-muted); }
.card-title { margin: 0 0 4px; font-size: 16px; font-weight: 600; line-height: 1.45; color: var(--color-text-primary); }
.card-summary { margin: 0 0 6px; font-size: 13.5px; line-height: 1.55; color: var(--color-text-secondary); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.card-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; font-size: 12px; color: var(--color-text-muted); }
.card-source { color: var(--color-text-secondary); }
.direction { padding: 1px 6px; border: 1px solid currentColor; border-radius: 999px; font-size: 11px; }
.direction.bull { color: var(--color-gain); }
.direction.bear { color: var(--color-loss); }
.direction.flat { color: var(--color-text-muted); }
.credibility { padding: 1px 6px; border: 1px solid currentColor; border-radius: 999px; font-size: 11px; }
.credibility.cred-good { color: var(--color-accent); }
.credibility.cred-mid { color: var(--color-text-muted); }
.rumor-tag { padding: 1px 6px; border: 1px solid var(--color-warning); border-radius: 999px; font-size: 11px; color: var(--color-warning); }

.load-more { display: block; margin: 16px auto 24px; padding: 8px 22px; border: 1px solid var(--color-border-strong); border-radius: 999px; background: var(--color-bg-surface); font-size: 13px; cursor: pointer; color: var(--color-text-secondary); }
.load-more:hover { background: var(--color-bg-subtle); }

/* 沉浸式阅读页 */
.reader-overlay { position: fixed; inset: 0; z-index: 60; background: var(--color-overlay); display: flex; align-items: center; justify-content: center; padding: 20px; }
.reader-page { position: relative; display: grid; grid-template-columns: 1fr 260px; gap: 28px; width: min(880px, 100%); max-height: min(88vh, 900px); overflow-y: auto; background: var(--color-bg-surface); border: 1px solid var(--color-border); border-radius: var(--radius-lg); padding: 32px 36px 24px; box-shadow: 0 20px 25px -5px rgba(0,0,0,0.1), 0 10px 10px -5px rgba(0,0,0,0.04); }
.reader-close { position: absolute; top: 12px; right: 12px; border: none; background: none; cursor: pointer; color: var(--color-text-muted); padding: 8px; border-radius: 999px; display: flex; align-items: center; justify-content: center; }
.reader-close:hover { color: var(--color-text-primary); background: var(--color-bg-subtle); }
.reader-main { min-width: 0; }
.reader-date { margin: 0 0 6px; font-size: 12px; color: var(--color-text-muted); }
.reader-title { margin: 0 0 10px; font-size: 22px; font-weight: 700; line-height: 1.35; color: var(--color-text-primary); }
.reader-meta { display: flex; align-items: center; gap: 10px; margin-bottom: 18px; padding-bottom: 14px; border-bottom: 1px solid var(--color-border); font-size: 13px; color: var(--color-text-muted); }
.reader-body { margin-bottom: 18px; }
.body-status { font-size: 14px; color: var(--color-text-muted); }
.body-status a { color: var(--color-accent); }
.reader-content { font-size: 15px; line-height: 1.85; color: var(--color-text-primary); white-space: pre-line; word-break: break-word; }
.reader-content-alt { margin-top: 10px; padding: 12px; border-radius: var(--radius-md); background: var(--color-bg-subtle); font-size: 13.5px; line-height: 1.8; color: var(--color-text-secondary); }
.reader-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.source-link { font-size: 13px; color: var(--color-accent); }
.reader-actions { display: flex; gap: 8px; }
.reader-btn { padding: 6px 14px; border: 1px solid var(--color-border-strong); border-radius: 999px; background: var(--color-bg-surface); font-size: 13px; cursor: pointer; color: var(--color-text-secondary); }
.reader-btn:hover { background: var(--color-bg-subtle); color: var(--color-text-primary); }

.reader-related { margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--color-border); }
.related-label { display: block; margin-bottom: 10px; font-size: 11px; font-weight: 600; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: 0.5px; }
.related-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.related-item { display: flex; align-items: baseline; gap: 10px; padding: 6px 8px; border-radius: var(--radius-sm); cursor: pointer; }
.related-item:hover { background: var(--color-bg-subtle); }
.related-time { flex: none; font-size: 12px; color: var(--color-text-muted); }
.related-title { font-size: 13px; color: var(--color-text-secondary); line-height: 1.5; }

.reader-aside { align-self: start; position: sticky; top: 0; padding: 16px 14px; background: var(--color-bg-subtle); border-radius: var(--radius-md); }
.aside-label { display: block; margin-bottom: 8px; font-size: 11px; font-weight: 600; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: 0.5px; }
.aside-direction { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.aside-text { margin: 0; font-size: 13px; line-height: 1.7; color: var(--color-text-secondary); }
.aside-disclaimer { margin: 10px 0 0; padding-top: 8px; border-top: 1px solid var(--color-border); font-size: 11px; color: var(--color-text-muted); }

@media (max-width: 767px) {
  .reader-overlay { padding: 8px; align-items: flex-end; }
  .reader-page { grid-template-columns: 1fr; gap: 0; padding: 24px 16px 16px; max-height: 94vh; }
  .reader-aside { position: static; margin-top: 16px; }
  .reader-title { font-size: 19px; }
}
</style>
