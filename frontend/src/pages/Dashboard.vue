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
import StockSearchInput from '../components/StockSearchInput.vue'
import InfoTip from '../components/InfoTip.vue'
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
  favoritesLoaded.value = false
  try {
    const res = await getFavorites()
    favorites.value = res.data
  } catch {
    favorites.value = []
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

onMounted(() => {
  loadFavorites()
  loadThsStatus()
  void market.load()
  messageBus.connect()
  messageBus.refreshUnread()
  unsubscribeReconnect = messageBus.subscribeReconnect(() => messageBus.refreshUnread())
  quoteBus.connect()
})

onBeforeUnmount(() => {
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

    <!-- 同花顺绑定：已绑定给同步入口，未绑定给绑定引导 -->
    <section
      v-if="thsBound === true"
      class="mb-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-xl border border-border bg-surface px-4 py-3"
    >
      <div class="min-w-0">
        <p class="flex flex-wrap items-center gap-2 text-[13.5px] font-medium text-ink">
          <span class="h-2 w-2 rounded-full bg-down" aria-hidden="true"></span>
          同花顺账号已绑定
          <span v-if="thsExpired" class="text-[12px] font-normal text-warn">凭证可能已过期，建议重新绑定</span>
        </p>
        <p class="mt-0.5 text-[12.5px] text-ink-2">
          <span v-if="thsLastSync">上次同步：<span class="num">{{ String(thsLastSync).replace('T', ' ').slice(0, 19) }}</span></span>
          <span v-else>尚未同步过自选股</span>
          <span v-if="thsLastError" class="text-up"> · {{ thsLastError }}</span>
        </p>
      </div>
      <div class="flex shrink-0 gap-2">
        <Button size="sm" variant="primary" :loading="syncing" @click="doSync">重新同步</Button>
        <Button size="sm" @click="openBind">重新绑定</Button>
      </div>
    </section>

    <section
      v-else-if="thsBound === false"
      class="mb-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-xl border border-border bg-surface px-4 py-3"
    >
      <p class="flex items-center gap-2 text-[13.5px] font-medium text-ink">
        <span class="h-2 w-2 rounded-full bg-ink-3" aria-hidden="true"></span>
        绑定同花顺账号
        <InfoTip label="绑定说明" text="绑定后自动同步你手机同花顺里的自选股，之后也可以直接扫码登录，免记密码。" />
      </p>
      <Button size="sm" variant="primary" @click="openBind">去绑定</Button>
    </section>

    <p v-if="syncMsg" class="mb-4 text-[12.5px] text-accent" role="status">{{ syncMsg }}</p>

    <section class="mb-4 flex items-start gap-2">
      <StockSearchInput ref="searchInputRef" v-model="symbol" />
      <Button variant="primary" :loading="loading" @click="search">查询</Button>
    </section>

    <p v-if="error" class="mb-3 text-[13px] text-up" role="alert">{{ error }}</p>

    <!-- 行情卡：行情在上、操作在下，单列堆叠（DOM 顺序 = 阅读顺序） -->
    <article v-if="stockData" class="mb-5 rounded-xl border border-border bg-surface p-5">
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

      <form class="mt-5 grid max-w-[360px] gap-2" @submit.prevent="add(stockData.symbol || symbol.toUpperCase())">
        <input
          v-model="buyPrice"
          aria-label="买入价（选填）"
          placeholder="买入价（选填）"
          inputmode="decimal"
          class="num h-[34px] w-full rounded-lg border border-border bg-surface px-3 text-[13px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
        />
        <input
          v-model="quantity"
          aria-label="持有数量（选填）"
          placeholder="持有数量（选填）"
          inputmode="numeric"
          class="num h-[34px] w-full rounded-lg border border-border bg-surface px-3 text-[13px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
        />
        <Button type="submit" variant="primary">添加自选</Button>
      </form>
    </article>

    <p v-if="addError" class="mb-3 text-[13px] text-up" role="alert">{{ addError }}</p>

    <!-- 自选股 -->
    <section>
      <h2 class="mb-3.5 flex items-baseline gap-2 text-[16px] font-semibold text-ink">
        自选股
        <span v-if="favorites.length" class="num text-[12px] font-normal text-ink-3">{{ favorites.length }}</span>
      </h2>

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
    </section>

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
