<script setup>
/**
 * 首页：自选行情。逻辑自旧版原样移植（零漂移），视觉换墨阶。
 *
 * 关键口径（注释即契约）：
 * - 休市提示是整页最该先说清的事：休市日行情接口回的仍是上一交易日收盘价，
 *   句子由后端拼好，前端只决定版式。
 * - 涨跌方向不猜：取不到就中性；方向不能只靠颜色，+/- 数值同屏。
 * - 价格主数字不染色（同一屏只有涨跌胶囊那一处红绿结论）。
 * - 快车道：SSE 推价优先，涨跌幅跟着推送价用昨收现算。
 */
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { getStock, getFavorites, addFavorite, deleteFavorite } from '../api/stock.js'
import { getFundFlow } from '../api/market.js'
import * as echarts from 'echarts'
import StockSearchInput from '../components/StockSearchInput.vue'
import BlockCard from '../components/ui/block-card.vue'
import QrLogin from '../components/QrLogin.vue'
import Badge from '../components/ui/badge.vue'
import Button from '../components/ui/button.vue'
import Modal from '../components/ui/modal.vue'
import Empty from '../components/ui/empty.vue'
import Skeleton from '../components/ui/skeleton.vue'
import { Watch } from 'lucide-vue-next'
import { getStatus, syncFavorites } from '../api/ths.js'
import { prefetchStockNews } from '../api/news.js'
import { useMarketStatus } from '../composables/useMarketStatus.js'
import { messageBus } from '../composables/messageBus.js'
import { quoteBus } from '../composables/quoteBus.js'
import { toNum, signed, dirTone, dirOf } from '../utils/format.js'
import { swrRead, swrWrite, accountKey } from '../utils/swrCache.js'

const router = useRouter()
const market = useMarketStatus()
const marketNotice = market.restNotice
const restNoticeParts = market.restNoticeParts

const symbol = ref('')
const stockData = ref(null)
const favorites = ref([])
const loading = ref(false)
const error = ref('')
const favoritesLoaded = ref(false)
const addError = ref('')
const buyPrice = ref('')
const quantity = ref('')
let unsubscribeReconnect = null

const searchInputRef = ref(null)

/** 自选股区的状态文案（长期存在的 role="status" 容器，文本变化才播报） */
const favoritesStatus = computed(() => {
  if (!favoritesLoaded.value) return '正在加载自选股…'
  return favorites.value.length === 0 ? '暂无自选股' : ''
})

const quoteChange = computed(() => {
  const amount = signed(stockData.value?.change)
  const pct = signed(stockData.value?.changePercent, '%')
  if (!amount && !pct) return null
  return {
    text: [amount, pct].filter(Boolean).join(' '),
    tone: dirTone(stockData.value?.change ?? stockData.value?.changePercent),
  }
})

/** 自选股行里的涨跌幅文案（没数据时返回空串，模板据此不渲染） */
const favPct = (item) => signed(favChangePct(item), '%')

/** 快车道：SSE 推来的最新价优先；没推送过（休市/刚进页面）用 HTTP 初载的值 */
const favPrice = (item) => quoteBus.priceOf(item.symbol) ?? item.price

const favChangePct = (item) => {
  const live = quoteBus.priceOf(item.symbol)
  const prev = toNum(item.previousClose)
  if (live !== undefined && prev !== null && prev > 0) {
    return (live / prev - 1) * 100
  }
  return item.changePercent
}

/** 行情卡右上角那行字（口径判断在 utils/marketStatus.js，有独立用例） */
const quoteDateText = computed(() => market.quoteDateTextFor(stockData.value?.lastUpdated))

// ---- 同花顺绑定状态 ----
const thsBound = ref(null)
const thsExpired = ref(false)
const thsLastSync = ref(null)
const thsLastError = ref('')
const showBindModal = ref(false)
const syncMsg = ref('')
const syncing = ref(false)

const loadThsStatus = async () => {
  try {
    const res = await getStatus()
    const d = res.data
    thsBound.value = !!d.bound
    thsExpired.value = !!d.expired
    thsLastSync.value = d.lastSyncAt
    thsLastError.value = d.lastError || ''
  } catch {
    thsBound.value = false
  }
}

const openBind = () => {
  showBindModal.value = true
  syncMsg.value = ''
}

const closeBind = () => {
  showBindModal.value = false
  loadThsStatus()
}

/** 扫码绑定成功后：自动同步一次自选股 */
const onBindSuccess = async () => {
  syncMsg.value = '绑定成功，正在同步自选股…'
  try {
    const res = await syncFavorites()
    const d = res.data
    syncMsg.value = `同步完成：新增 ${d.added} 只，已存在 ${d.unchanged} 只`
    await loadFavorites()
    await loadThsStatus()
  } catch (e) {
    syncMsg.value = e.response?.data?.message || '绑定成功，但同步失败，可稍后手动重试'
  }
}

/** 已绑定用户手动重新同步 */
const doSync = async () => {
  syncing.value = true
  syncMsg.value = ''
  try {
    const res = await syncFavorites()
    const d = res.data
    syncMsg.value = `同步完成：新增 ${d.added} 只，已存在 ${d.unchanged} 只`
    await loadFavorites()
    await loadThsStatus()
  } catch (e) {
    syncMsg.value = e.response?.data?.message || '同步失败，请检查网络后重试'
  } finally {
    syncing.value = false
  }
}

const search = async () => {
  const query = symbol.value.trim()
  if (!query) return
  loading.value = true
  error.value = ''
  try {
    const res = await getStock(query.toUpperCase())
    stockData.value = res.data
    searchInputRef.value?.recordSearch(query.toUpperCase(), res.data?.name || query.toUpperCase())
  } catch {
    error.value = '查询失败，请检查股票代码是否正确'
    stockData.value = null
  } finally {
    loading.value = false
  }
}

const loadFavorites = async () => {
  // SWR 首屏（2026-10-24）：首次挂载先按账号分键读快照秒出（可能是上一会话的
  // 旧列表），网络回来原地换新——首页最大的一块不再从骨架屏起步。换账号无串台：
  // 键里带账号（JWT sub）。会话内的增删重载不读快照：内存里已是最新，读旧的反而错。
  if (!favorites.value.length) {
    const cached = swrRead(accountKey('favorites'), 24 * 3600 * 1000)
    if (Array.isArray(cached) && cached.length) {
      favorites.value = cached
      favoritesLoaded.value = true
    } else {
      favoritesLoaded.value = false
    }
  }
  try {
    const res = await getFavorites()
    favorites.value = res.data
    if (Array.isArray(res.data) && res.data.length) swrWrite(accountKey('favorites'), res.data)
  } catch {
    // 拉取失败但有快照：保留快照展示（SSE 推价仍在打补丁），没有快照才回空态
    if (!favorites.value.length) favorites.value = []
  } finally {
    favoritesLoaded.value = true
  }
}

const add = async (sym) => {
  addError.value = ''
  const bpRaw = buyPrice.value.trim()
  const qtyRaw = quantity.value.trim()

  if (bpRaw && (isNaN(bpRaw) || parseFloat(bpRaw) <= 0)) {
    addError.value = '买入价需填写大于 0 的数字，例如 1680.50'
    return
  }
  if (qtyRaw && (isNaN(qtyRaw) || parseInt(qtyRaw) <= 0)) {
    addError.value = '持有数量需填写大于 0 的整数，例如 100'
    return
  }

  const bp = bpRaw ? parseFloat(bpRaw) : null
  const qty = qtyRaw ? parseInt(qtyRaw) : null

  try {
    await addFavorite(sym, bp, qty)
    buyPrice.value = ''
    quantity.value = ''
    await loadFavorites()
  } catch {
    addError.value = '添加失败，请检查网络后重试'
  }
}

const remove = async (sym) => {
  // 破坏性动作先确认；后端删除自选会连带删掉该股票的全部预警，必须说出来
  const item = favorites.value.find((f) => f.symbol === sym)
  const label = item?.name ? `${item.name}（${sym}）` : sym
  if (!window.confirm(`确定删除 ${label} 吗？该股票的预警也会一并删除，自选需要重新添加。`)) return

  try {
    await deleteFavorite(sym)
    await loadFavorites()
  } catch {
    error.value = '删除失败，请检查网络后重试'
  }
}

const goDetail = (sym) => {
  // 进个股页之前先预热它的资讯（预取失败无所谓，刻意不 await）
  prefetchStockNews(sym)
  router.push('/chart/' + sym)
}

const goAddAlert = (sym) => router.push({ path: '/alerts', query: { symbol: sym, new: '1' } })

// ---------- 资金流向（大盘主力净流入，日频） ----------
const flowRows = ref([])
const flowError = ref(false)
// 首次加载的骨架态：数据没回来前卡片不再空着，后续轮询原地换数据不闪骨架
const flowLoading = ref(true)
// 降级态：本次取数失败但手里还有快照——照常展示（诚实标注截至日期），恢复后自动换新
const flowDegraded = ref(false)
const flowChartEl = ref(null)
let flowChart = null

// localStorage 快照兜底（2026-10-19）：东财端点间歇性抽风时，资金卡从"整卡消失"
// 变成"展示最近一次成功的快照"。快照超过 12 天不救——宁可走错误态+重试，
// 也不把一个月前的资金流留在首页当参考（12 天覆盖春节/国庆长假的最长休市）。
const FLOW_SNAPSHOT_KEY = 'dash-fund-flow-snapshot'
const FLOW_SNAPSHOT_MAX_AGE_MS = 12 * 24 * 3600 * 1000

const snapshotFreshEnough = (items) => {
  const last = items?.[items.length - 1]?.date
  const t = last ? new Date(`${last}T23:59:59+08:00`).getTime() : 0
  return Number.isFinite(t) && Date.now() - t < FLOW_SNAPSHOT_MAX_AGE_MS
}

const hydrateFlowSnapshot = () => {
  try {
    const raw = localStorage.getItem(FLOW_SNAPSHOT_KEY)
    if (!raw) return
    const items = JSON.parse(raw)
    if (Array.isArray(items) && items.length && snapshotFreshEnough(items)) {
      flowRows.value = items
    }
  } catch { /* 坏快照当没有 */ }
}

const persistFlowSnapshot = () => {
  try {
    localStorage.setItem(FLOW_SNAPSHOT_KEY, JSON.stringify(flowRows.value))
  } catch { /* 隐私模式/存储满：跳过 */ }
}
// 视图切换：主力净流入 / 四档分解（超大/大/中/小单堆叠——看"主力撤、散户接"的结构）
const flowView = ref('main')

const flowLatest = computed(() => flowRows.value[flowRows.value.length - 1] || null)
const flowToneClass = (v) => (v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-ink-2')
// 四档分解需要**历史**档位数据（东财口径才有；腾讯 fallback 源只有当日快照，历史档位为 null）。
// 缺历史档位时收起该视图——一张只有一根柱子的堆叠图比没有这个视图更误导
const flowTiersFull = computed(() =>
  flowRows.value.filter((r) => r.super_net_yi != null).length >= 15)

// canvas 里 CSS 变量不生效（echarts 画的是位图）：取计算后的令牌值，
// 暗色切换时 onBeforeUnmount 销毁、下次进页重建，颜色自然跟随主题
const cssToken = (name, fallback) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback

const loadFundFlow = async () => {
  // 手里没数据（首开/重试）：先回骨架；有快照：原地静默刷新，不闪内容
  if (!flowLatest.value) {
    flowError.value = false
    flowLoading.value = true
  }
  flowDegraded.value = false
  let items = []
  try {
    const res = await getFundFlow(60)
    // Result.error(503) 是 HTTP 200 信封，拦截器已转 reject——空 items 与抛错都走兜底
    if (Array.isArray(res.data?.items)) items = res.data.items
  } catch { /* 上游失败：走快照兜底，不隐藏卡片 */ }
  if (items.length) {
    flowRows.value = items
    persistFlowSnapshot()
  }
  // 数据源换了口径（东财→腾讯）时，"四档分解"可能不再可用：把视图收回主力
  if (!flowTiersFull.value && flowView.value === 'tiers') flowView.value = 'main'
  flowLoading.value = false
  const haveData = !!flowLatest.value
  flowError.value = !haveData                        // 彻底没数据：错误态（带重试），不再整卡消失
  flowDegraded.value = haveData && !items.length     // 取数失败但有快照：降级展示
  renderFlowChart()
}

const FLOW_TIERS = [
  { key: 'super_net_yi', name: '超大单' },
  { key: 'big_net_yi', name: '大单' },
  { key: 'mid_net_yi', name: '中单' },
  { key: 'small_net_yi', name: '小单' },
]

const renderFlowChart = () => {
  if (!flowChartEl.value || !flowRows.value.length) return
  if (!flowChart) flowChart = echarts.init(flowChartEl.value)
  const rows = flowRows.value.slice(-30)
  const axis = {
    xAxis: { type: 'category', data: rows.map((r) => r.date.slice(5)),
             axisLabel: { fontSize: 10, color: cssToken('--ink-3', '#8b92a5') } },
    yAxis: { type: 'value', axisLabel: { fontSize: 10, color: cssToken('--ink-3', '#8b92a5') },
             splitLine: { lineStyle: { color: cssToken('--border', '#e5e7eb') } } },
  }
  if (flowView.value === 'main') {
    flowChart.setOption({
      grid: { left: 44, right: 8, top: 8, bottom: 20 },
      tooltip: {
        trigger: 'axis',
        formatter: (params) => {
          const p = params[0]
          return `${p.name}<br/>主力净流入 <b>${p.value > 0 ? '+' : ''}${p.value}</b> 亿元`
        },
      },
      ...axis,
      series: [{
        type: 'bar', data: rows.map((r) => r.main_net_yi),
        itemStyle: {
          color: (p) => (p.value >= 0 ? cssToken('--up', '#d92c2c')
                                      : cssToken('--down', '#0e9668')),
        },
        barMaxWidth: 14,
      }],
    }, true)
  } else {
    flowChart.setOption({
      grid: { left: 44, right: 8, top: 22, bottom: 20 },
      tooltip: {
        trigger: 'axis',
        formatter: (params) => {
          const lines = params.map((p) =>
            `${p.marker}${p.seriesName}　<b>${p.value > 0 ? '+' : ''}${p.value}</b> 亿`)
          return `${params[0].name}<br/>` + lines.join('<br/>')
        },
      },
      legend: {
        top: 0, right: 0, itemWidth: 10, itemHeight: 10,
        textStyle: { fontSize: 10.5, color: cssToken('--ink-2', '#5b6472') },
      },
      ...axis,
      series: FLOW_TIERS.map((tier) => ({
        name: tier.name, type: 'bar', stack: 'flow',
        data: rows.map((r) => r[tier.key]),
        barMaxWidth: 14,
        itemStyle: {
          color: (p) => (p.value >= 0 ? cssToken('--up', '#d92c2c')
                                      : cssToken('--down', '#0e9668')),
          opacity: 0.85,
        },
      })),
    }, true)
  }
}

const switchFlowView = (view) => {
  if (flowView.value === view) return
  flowView.value = view
  renderFlowChart()
}

// 交易时段自动轮询资金卡（5 分钟）：日频数据只有"今日"这根在动，
// 休市/盘后轮询只会拿到同一份——只在盘中动，其余时段零请求。
// 错误态（连快照都没有）例外：错误态不自愈的话，留着页面打开的用户永远看不到恢复。
const { status: flowMarketStatus } = useMarketStatus()
let flowPollTimer = null
const flowMarketOpen = () =>
  ['auction', 'morning', 'noon_break', 'afternoon'].includes(flowMarketStatus.value?.phase)
const startFlowPolling = () => {
  if (flowPollTimer) return
  flowPollTimer = setInterval(() => {
    if (flowMarketOpen() || flowError.value) loadFundFlow()
  }, 5 * 60 * 1000)
}

onMounted(() => {
  loadFavorites()
  loadThsStatus()
  hydrateFlowSnapshot()   // 先用快照秒出内容，再让 loadFundFlow 静默换新
  loadFundFlow()
  startFlowPolling()
  void market.load()
  messageBus.connect()
  messageBus.refreshUnread()
  unsubscribeReconnect = messageBus.subscribeReconnect(() => messageBus.refreshUnread())
  quoteBus.connect()
})

onBeforeUnmount(() => {
  if (flowChart) { flowChart.dispose(); flowChart = null }
  if (flowPollTimer) { clearInterval(flowPollTimer); flowPollTimer = null }
  showBindModal.value = false
  if (unsubscribeReconnect) unsubscribeReconnect()
})
</script>

<template>
  <div>
    <div class="mb-5">
      <h1 class="text-[20px] font-semibold text-ink">自选行情</h1>
    </div>

    <!-- 休市口径提示：徽章说状态、正文说事实、副行补一句"这些价格是哪天的" -->
    <p
      v-if="marketNotice"
      class="mb-4 flex flex-wrap items-center gap-x-2.5 gap-y-1 rounded-xl border border-border bg-surface px-4 py-3"
      role="status"
    >
      <Badge tone="warn">{{ marketNotice.badge }}</Badge>
      <span class="text-[13px] text-ink">
        <template v-for="(part, i) in restNoticeParts" :key="i">
          <span v-if="i" class="mx-1 text-ink-3">·</span>{{ part }}
        </template>
      </span>
      <span v-if="marketNotice.detail" class="w-full text-[12px] text-ink-2">{{ marketNotice.detail }}</span>
    </p>

    <p v-if="syncMsg" class="mb-4 text-[12.5px] text-accent" role="status">{{ syncMsg }}</p>

    <!-- 双栏区块（lg+）：左=行情查询，右=同花顺同步；窄屏单列堆叠 -->
    <div class="grid items-start gap-3 lg:[grid-template-columns:minmax(0,1fr)_340px]">
      <!-- 区块：行情查询 -->
      <BlockCard title="行情查询" hint="输入代码或名称查询个股；加入自选后价格随快车道实时刷新。" collapsible storage-key="dash-quote" body-class="p-4 pt-3">
        <section class="flex items-start gap-2">
          <StockSearchInput ref="searchInputRef" v-model="symbol" />
          <Button variant="primary" :loading="loading" @click="search">查询</Button>
        </section>

        <p v-if="error" class="mt-3 text-[13px] text-up" role="alert">{{ error }}</p>

        <!-- 查询结果（区块内）：行情在上、操作在下 -->
        <article v-if="stockData" class="mt-4 rounded-lg border border-border bg-surface-2 p-4">
          <header class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <h2 class="flex flex-wrap items-baseline gap-2 text-[16px] font-semibold text-ink">
              {{ stockData.name || stockData.symbol || symbol.toUpperCase() }}
              <span v-if="stockData.name" class="num text-[12px] font-normal text-ink-3">{{ stockData.symbol }}</span>
            </h2>
            <p class="num text-[12px] text-ink-3">{{ quoteDateText }}</p>
          </header>

          <p class="num mt-2.5 text-[28px] font-semibold leading-tight text-ink">¥{{ stockData.price || 'N/A' }}</p>

          <p class="mt-1.5 flex flex-wrap items-center gap-3">
            <Badge v-if="quoteChange" :tone="quoteChange.tone" class="num">{{ quoteChange.text }}</Badge>
            <span v-if="stockData.previousClose" class="num text-[12px] text-ink-2">昨收 {{ stockData.previousClose }}</span>
          </p>

          <form class="mt-4 grid max-w-[360px] gap-2" @submit.prevent="add(stockData.symbol || symbol.toUpperCase())">
            <input
              v-model="buyPrice"
              aria-label="买入价（选填）"
              placeholder="买入价（选填）"
              inputmode="decimal"
              class="num h-[34px] w-full rounded-lg border border-border bg-bg px-3 text-[13px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
            />
            <input
              v-model="quantity"
              aria-label="持有数量（选填）"
              placeholder="持有数量（选填）"
              inputmode="numeric"
              class="num h-[34px] w-full rounded-lg border border-border bg-bg px-3 text-[13px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
            />
            <Button type="submit" variant="primary">添加自选</Button>
          </form>
        </article>
        <p v-else class="mt-3 text-[12.5px] leading-relaxed text-ink-3">
          查询后在卡片里显示现价与涨跌，可直接加入自选。
        </p>
        <p v-if="addError" class="mt-2 text-[13px] text-up" role="alert">{{ addError }}</p>
      </BlockCard>

      <!-- 区块：同花顺同步 -->
      <!-- 资金卡挂右栏：与同花顺同步同列，首屏不挤行情查询 -->
      <BlockCard
        v-if="thsBound === true || thsBound === false"
        title="同花顺同步"
        hint="绑定后自动同步你手机同花顺里的自选股，之后也可以直接扫码登录，免记密码。"
        collapsible
        storage-key="dash-ths-sync"
        body-class="p-4 pt-3"
      >
        <template v-if="thsBound" #actions>
          <Button size="sm" :loading="syncing" @click="doSync">同步</Button>
        </template>
        <div v-if="thsBound" class="min-w-0">
          <p class="flex flex-wrap items-center gap-2 text-[13.5px] font-medium text-ink">
            <span class="h-2 w-2 rounded-full bg-down" aria-hidden="true"></span>
            账号已绑定
            <span v-if="thsExpired" class="text-[12px] font-normal text-warn">凭证可能已过期，建议重新绑定</span>
          </p>
          <p class="mt-0.5 text-[12.5px] text-ink-2">
            <span v-if="thsLastSync">上次同步：<span class="num">{{ String(thsLastSync).replace('T', ' ').slice(0, 19) }}</span></span>
            <span v-else>尚未同步过自选股</span>
            <span v-if="thsLastError" class="text-up"> · {{ thsLastError }}</span>
          </p>
          <Button size="sm" class="mt-2.5" @click="openBind">重新绑定</Button>
        </div>
        <div v-else class="flex flex-col items-start gap-2">
          <p class="text-[13px] leading-relaxed text-ink-2">绑定同花顺账号后，手机里的自选股会自动同步到这里。</p>
          <Button size="sm" variant="primary" @click="openBind">去绑定</Button>
        </div>
      </BlockCard>
    </div>

    <!-- 区块：资金流向（大盘主力净流入，日频盘后更新；净流入红、净流出绿） -->
    <!-- 永不移除（2026-10-19）：取数失败先救快照、救不了给重试——之前 v-if 直接把
         整卡摘掉，上游（东财）一抽风首页就少一块，用户只看到"有时候没有" -->
    <BlockCard
      class="mt-3"
      title="资金流向"
      hint="大盘主力资金（超大单+大单）净流入，正=流入红、负=流出绿；日频，收盘后更新当日值。"
      collapsible
      storage-key="dash-fund-flow"
      body-class="p-4 pt-3"
    >
      <template #actions>
        <div class="flex items-center gap-2">
          <div class="flex rounded-lg border border-border p-0.5" role="tablist" aria-label="资金图视图">
            <button
              v-for="v in [{ key: 'main', label: '主力' }, ...(flowTiersFull ? [{ key: 'tiers', label: '四档分解' }] : [])]"
              :key="v.key"
              type="button"
              role="tab"
              :aria-selected="flowView === v.key"
              class="h-[22px] rounded-md px-2 text-[11.5px] transition-colors max-sm:h-[32px] max-sm:px-2.5"
              :class="flowView === v.key ? 'bg-accent text-on-accent' : 'text-ink-3 hover:text-ink'"
              @click="switchFlowView(v.key)"
            >{{ v.label }}</button>
          </div>
          <span v-if="flowLatest" class="num hidden text-[12px] text-ink-3 sm:inline">截至 {{ flowLatest.date.slice(5) }}</span>
        </div>
      </template>
      <!-- 骨架：拼出"数字行 + 图"的形状，高度与正式内容一致，数据落地时不跳版 -->
      <div v-if="flowLoading && !flowLatest" class="flex flex-col gap-3" role="status" aria-label="正在加载资金流向">
        <div class="flex items-baseline gap-4">
          <Skeleton width="110px" height="14px" />
          <Skeleton width="84px" height="26px" />
        </div>
        <Skeleton width="100%" height="180px" />
      </div>
      <!-- 错误态：连快照都没有时给"暂不可用 + 重试"，而不是把卡摘掉 -->
      <div v-else-if="!flowLatest" role="alert">
        <div class="flex flex-wrap items-center justify-between gap-3 py-4">
          <p class="text-[13px] text-ink-2">
            资金流向暂不可用<span class="ml-1.5 text-[12px] text-ink-3">上游数据源间歇性不可达，稍后自动重试</span>
          </p>
          <Button size="sm" @click="loadFundFlow">重试</Button>
        </div>
      </div>
      <div v-if="flowLatest" class="flex flex-col gap-3">
        <p v-if="flowDegraded" class="text-[11.5px] text-ink-3" role="status">
          数据源暂不可用，展示截至 {{ flowLatest.date }} 的缓存快照，恢复后自动更新
        </p>
        <div class="flex flex-wrap items-baseline gap-x-5 gap-y-1.5">
          <p class="flex items-baseline gap-1.5">
            <span class="text-[12.5px] text-ink-2">今日主力净流入</span>
            <span class="num text-[22px] font-semibold" :class="flowToneClass(flowLatest.main_net_yi)">
              {{ flowLatest.main_net_yi > 0 ? '+' : '' }}{{ flowLatest.main_net_yi }} 亿
            </span>
          </p>
          <p class="flex flex-wrap items-baseline gap-x-3 text-[12px]">
            <span v-if="flowLatest.super_net_yi != null" class="text-ink-2">超大单
              <span class="num font-medium" :class="flowToneClass(flowLatest.super_net_yi)">{{ flowLatest.super_net_yi }}</span> 亿</span>
            <span v-if="flowLatest.big_net_yi != null" class="text-ink-2">大单
              <span class="num font-medium" :class="flowToneClass(flowLatest.big_net_yi)">{{ flowLatest.big_net_yi }}</span> 亿</span>
            <span v-if="flowLatest.mid_net_yi != null" class="text-ink-2">中单
              <span class="num font-medium" :class="flowToneClass(flowLatest.mid_net_yi)">{{ flowLatest.mid_net_yi }}</span> 亿</span>
            <span v-if="flowLatest.small_net_yi != null" class="text-ink-2">小单
              <span class="num font-medium" :class="flowToneClass(flowLatest.small_net_yi)">{{ flowLatest.small_net_yi }}</span> 亿</span>
          </p>
        </div>
        <div ref="flowChartEl" class="h-[180px] w-full" role="img" aria-label="近 30 日大盘主力净流入柱状图"></div>
      </div>
    </BlockCard>

    <!-- 区块：自选股 -->
    <BlockCard class="mt-3" title="自选股" hint="点卡片进 K 线详情；价格盘中 3 秒一跳（快车道），预警与删除在卡片底部。" collapsible storage-key="dash-favorites" body-class="p-4 pt-3">
      <template v-if="favorites.length" #actions>
        <span class="num text-[12px] text-ink-3">{{ favorites.length }} 只</span>
      </template>

      <!-- 加载骨架：拼出卡片的形状 -->
      <div v-if="!favoritesLoaded" class="grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(240px,1fr))]">
        <div v-for="i in 4" :key="i" class="space-y-2.5 rounded-xl border border-border bg-surface p-4">
          <Skeleton width="45%" height="14px" />
          <Skeleton width="30%" height="22px" />
          <Skeleton width="60%" height="13px" />
        </div>
      </div>

      <Empty
        v-else-if="favorites.length === 0"
        title="还没有自选股"
        desc="在上方搜索框输入代码或名称（如 600519、贵州茅台），查询后即可加入自选。"
      >
        <template #icon><Watch :size="20" :stroke-width="1.5" /></template>
      </Empty>

      <p v-else-if="favoritesStatus" class="text-[13px] text-ink-3" role="status">{{ favoritesStatus }}</p>

      <ul v-if="favoritesLoaded && favorites.length" class="grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(240px,1fr))]">
        <li
          v-for="item in favorites"
          :key="item.symbol"
          class="rounded-xl border border-border bg-surface p-4 transition-colors hover:border-ink-3"
        >
          <!-- 整张卡是"进入详情"的动作：真按钮铺满，卡内不嵌第二层交互 -->
          <button type="button" class="block w-full text-start" @click="goDetail(item.symbol)">
            <p class="flex flex-wrap items-baseline gap-2">
              <span class="text-[13.5px] font-semibold text-ink">{{ item.name || item.symbol }}</span>
              <span v-if="item.name" class="num text-[11.5px] text-ink-3">{{ item.symbol }}</span>
            </p>
            <p class="num mt-2 text-[20px] font-semibold leading-tight text-ink">¥{{ favPrice(item) || 'N/A' }}</p>
            <p class="mt-1.5 flex flex-wrap items-center gap-2">
              <!-- 无涨跌数据给中性破折号：位置稳定，区分"平盘/无数据"与"未加载" -->
              <Badge :tone="dirTone(favChangePct(item))" class="num">{{ favPct(item) || '—' }}</Badge>
              <span v-if="item.previousClose" class="num text-[11.5px] text-ink-3">昨收 {{ item.previousClose }}</span>
            </p>
          </button>
          <div class="mt-3 flex gap-1 border-t border-border pt-2.5">
            <button
              type="button"
              class="rounded-md px-2 py-1 text-[12px] text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
              :aria-label="`为 ${item.name || item.symbol} 添加预警`"
              @click="goAddAlert(item.symbol)"
            >预警</button>
            <button
              type="button"
              class="rounded-md px-2 py-1 text-[12px] text-ink-2 transition-colors hover:bg-up-soft hover:text-up"
              :aria-label="`从自选股删除 ${item.name || item.symbol}`"
              @click="remove(item.symbol)"
            >删除</button>
          </div>
        </li>
      </ul>
    </BlockCard>

    <!-- 绑定同花顺（mode=bind：带 JWT，绑定到当前登录账号） -->
    <Modal :open="showBindModal" title="绑定同花顺账号" @close="closeBind">
      <p class="mb-4 text-center text-[12.5px] leading-relaxed text-ink-2">
        用手机同花顺 App 扫码，确认后即绑定到当前账号。<br />
        绑定后会自动同步你的自选股，以后也可以直接用扫码登录。
      </p>
      <QrLogin mode="bind" @success="onBindSuccess" />
      <p v-if="syncMsg" class="mt-3 text-center text-[12.5px] text-accent" role="status">{{ syncMsg }}</p>
    </Modal>
  </div>
</template>
