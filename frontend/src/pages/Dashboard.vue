<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getStock, getFavorites, addFavorite, deleteFavorite } from '../api/stock.js'
import StockSearchInput from '../components/StockSearchInput.vue'
import AppIcon from '../components/AppIcon.vue'
import QrLogin from '../components/QrLogin.vue'
import { getStatus, syncFavorites } from '../api/ths.js'
import { prefetchStockNews } from '../api/news.js'
import { useMarketStatus } from '../composables/useMarketStatus.js'
import { messageBus } from '../composables/messageBus.js'

const router = useRouter()
const route = useRoute()

/**
 * 市场状态：今天开不开市、休到哪天、上面那些价格是哪一天的。
 *
 * 用户的原话："明明是在休息日，却显示昨收，这里难道不应该提醒用户几天休息吗"。
 * 休市日行情接口回的仍是上一交易日的收盘价与涨跌幅 —— 页面不把这件事说出来，
 * 用户唯一的理解就是"这是今天的价格"。
 */
const market = useMarketStatus()
// 模板里只有顶层绑定会自动解包；`market.restNotice` 是 ref，单独取一个名字
const marketNotice = market.restNotice

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

/**
 * 自选股区的状态文案。
 * 用一个**长期存在**的 role="status" 容器承载它，而不是三个互相排斥的 v-if 分支：
 * 元素被创建/销毁时读屏软件不会播报，文本变化才会。
 */
const favoritesStatus = computed(() => {
  if (!favoritesLoaded.value) return '正在加载自选股…'
  return favorites.value.length === 0 ? '暂无自选股' : ''
})

/**
 * 涨跌方向 —— 数据来自后端：Python 数据源（akshare/腾讯行情）解析出「昨收」「涨跌额」「涨跌幅」，
 * 经 Java 的 StockResponse 透传到前端（字段名 change / changePercent / previousClose）。
 *
 * 两条纪律：
 *  1. 取不到就返回 null，**不猜方向**（宁可用中性色，也不要编一个涨/跌出来）。
 *  2. 方向不能只靠颜色 —— 下面同时把带符号的数值显示出来（+12.34 / -5.60），
 *     颜色只是强化。这是 better-accessibility 的硬性要求。
 */
const toNum = (v) => {
  if (v === null || v === undefined) return null
  const s = String(v).trim()
  if (s === '' || s === '-' || s === '--' || s === 'N/A') return null
  const n = Number(s)
  return Number.isFinite(n) ? n : null
}

/** 与 StrategyDetail 的涨跌处理同一套：红涨绿跌（A 股约定） */
const dirClass = (v) => {
  const n = toNum(v)
  if (n === null || n === 0) return 'is-flat'
  return n > 0 ? 'is-up' : 'is-down'
}

/** 带符号格式化；四舍五入到 0 的按平盘显示，避免出现 "-0.00" */
const signed = (v, suffix = '') => {
  const n = toNum(v)
  if (n === null) return ''
  const r = Math.round(n * 100) / 100
  return (r > 0 ? '+' : '') + r.toFixed(2) + suffix
}

/** 卡片上的涨跌：额与幅都可能有、也可能只有一个 */
const quoteChange = computed(() => {
  const amount = signed(stockData.value?.change)
  const pct = signed(stockData.value?.changePercent, '%')
  if (!amount && !pct) return null
  return {
    text: [amount, pct].filter(Boolean).join(' '),
    cls: dirClass(stockData.value?.change ?? stockData.value?.changePercent),
  }
})

/** 自选股行里的涨跌幅文案（没数据时返回空串，模板据此不渲染） */
const favPct = (item) => signed(item.changePercent, '%')

/**
 * 行情卡右上角那行字。
 *
 * <h3>为什么不能直接写"更新 {lastUpdated}"</h3>
 * 休市日行情接口回的仍是上一交易日的收盘价（`lastUpdated` 现在由交易日历给出，
 * 是**那个交易日**而不是今天）。原样渲染成"更新 2026-09-18"会被读成
 * "数据更新于 09-18"，而真实含义是"这个价是 09-18 收盘的"——
 * 后者才是用户判断"能不能拿它做决策"的依据。
 *
 * <p>判断全在 `utils/marketStatus.js` 的 `quoteDateTextFor`（纯函数，有独立用例）：
 * 休市/未开盘 → "最近交易日 09-18（周五）收盘"；盘中 → 沿用行情源的时间戳。
 */
const quoteDateText = computed(() => market.quoteDateTextFor(stockData.value?.lastUpdated))

// ---- 同花顺绑定状态 ----
// bound=null 表示还没查出来（先不显示卡片，避免闪一下）
const thsBound = ref(null)
const thsExpired = ref(false)
const thsLastSync = ref(null)
const thsLastError = ref('')
const showBindModal = ref(false)
const syncMsg = ref('')
const syncing = ref(false)

/** 查询绑定状态；失败就当作未绑定处理（不让它影响首页其他功能） */
const loadThsStatus = async () => {
  try {
    const res = await getStatus()
    const d = res.data
    thsBound.value = !!d.bound
    thsExpired.value = !!d.expired
    thsLastSync.value = d.lastSyncAt
    thsLastError.value = d.lastError || ''
  } catch (e) {
    // 401 会被 request.js 拦截器处理（跳登录页）；其它错误静默
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
  } catch (e) {
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
  } catch (e) {
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
  } catch (e) {
    addError.value = '添加失败，请检查网络后重试'
  }
}

const remove = async (sym) => {
  // 删除自选股是破坏性动作：没有撤销，所以必须先确认，并在文案里点明后果。
  // 用词跟随全站既有术语「删除」（Alerts、Strategies 都用「删除」）——
  // 单点改成「移除」会让同一个动作在应用里出现两种说法。
  const item = favorites.value.find((f) => f.symbol === sym)
  const label = item?.name ? `${item.name}（${sym}）` : sym
  // 后端删除自选成功时会**连带删除该股票的全部预警**
  // （FavoriteController#deleteFavoriteStocks → AlertService.deleteByUserAndSymbol）。
  // 确认文案必须把这个后果说出来，否则用户不知道预警也一起没了。
  // 不写"共 N 条"是因为首页并不持有预警列表 —— 编一个数字比不写更糟。
  if (!window.confirm(`确定删除 ${label} 吗？该股票的预警也会一并删除，自选需要重新添加。`)) return

  try {
    await deleteFavorite(sym)
    await loadFavorites()
  } catch (e) {
    error.value = '删除失败，请检查网络后重试'
  }
}

const goDetail = (sym) => {
  // 进个股页之前**先预热它的资讯**：那个页面最慢的一环是"后端临时去抓上游 +
  // 补解读"，而用户在这一刻已经用点击表明了他要看这只票。
  // 刻意不 await：预取失败完全无所谓（真正进页面时会照常再拉一次），
  // 而等它就等于"点了没反应"。
  prefetchStockNews(sym)
  router.push('/chart/' + sym)
}

const goAddAlert = (sym) => router.push({ path: '/alerts', query: { symbol: sym, new: '1' } })

onMounted(() => {
  loadFavorites()
  loadThsStatus()
  // 市场状态与其他几项并行：它只用来把"这些价格是哪天的"说清楚，不该拖慢首页
  void market.load()
  messageBus.connect()
  messageBus.refreshUnread()
  // 断线期间错过的消息不会补发，重连后同步一次未读数（角标才不会少）
  unsubscribeReconnect = messageBus.subscribeReconnect(() => messageBus.refreshUnread())
})

// 组件卸载时确保弹窗状态复位（避免残留遮罩）
onBeforeUnmount(() => {
  showBindModal.value = false
  if (unsubscribeReconnect) unsubscribeReconnect()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>自选行情</h1>
    </div>

      <!-- 休市提示：整页最该先说清的一件事。
           休市日行情接口返回的仍是上一交易日的收盘价与涨跌幅，而"昨收"这个词
           本身说明不了"今天不开市"—— 用户报的正是这个。
           句子（连休几天、哪天回来）由后端拼好，这里只决定版式：
           徽章说状态、正文说事实、副行补一句"页面上这些价格是哪天的"。 -->
      <p v-if="marketNotice" class="market-notice" :class="`tone-${marketNotice.tone}`" role="status">
        <span class="market-badge">{{ marketNotice.badge }}</span>
        <span class="market-headline">{{ marketNotice.headline }}</span>
        <span v-if="marketNotice.detail" class="market-detail">{{ marketNotice.detail }}</span>
      </p>

      <!-- ========== 同花顺绑定 ==========
           已绑定 → 显示同步状态 + 重新同步按钮
           未绑定 → 提示绑定（扫码登录进来的用户会自动是已绑定状态，
                    所以这个卡片对他们来说直接就是"已绑定"，不用再绑） -->
      <section v-if="thsBound === true" class="ths-card">
        <div class="ths-info">
          <div class="ths-title">
            <span class="ths-dot ok" aria-hidden="true"></span>
            同花顺账号已绑定
            <span v-if="thsExpired" class="ths-warn">凭证可能已过期，建议重新绑定</span>
          </div>
          <div class="ths-sub">
            <span v-if="thsLastSync">上次同步：<span class="num">{{ String(thsLastSync).replace('T', ' ').slice(0, 19) }}</span></span>
            <span v-else>尚未同步过自选股</span>
            <span v-if="thsLastError" class="ths-err">· {{ thsLastError }}</span>
          </div>
        </div>
        <div class="ths-actions">
          <button type="button" class="ths-btn primary" :disabled="syncing" @click="doSync">
            {{ syncing ? '同步中…' : '重新同步' }}
          </button>
          <button type="button" class="ths-btn" @click="openBind">重新绑定</button>
        </div>
      </section>

      <section v-else-if="thsBound === false" class="ths-card">
        <div class="ths-info">
          <div class="ths-title">
            <span class="ths-dot" aria-hidden="true"></span>
            绑定同花顺账号
          </div>
          <div class="ths-sub">
            绑定后自动同步你手机同花顺里的自选股，并支持以后直接扫码登录，免记密码。
          </div>
        </div>
        <div class="ths-actions">
          <button type="button" class="ths-btn primary" @click="openBind">去绑定</button>
        </div>
      </section>

      <p v-if="syncMsg" class="ths-msg" role="status">{{ syncMsg }}</p>

      <section class="search-section">
        <StockSearchInput ref="searchInputRef" v-model="symbol" />
        <button type="button" class="search-btn" @click="search" :disabled="loading">
          {{ loading ? '查询中…' : '查询' }}
        </button>
      </section>

      <p v-if="error" class="error" role="alert">{{ error }}</p>

      <!-- 行情卡（宽松方向）：行情在上、操作在下，单列堆叠。
           原先是"左行情 / 右操作"两栏，外加一个容器查询负责在窄处折叠 ——
           单列在任何宽度都成立，所以那个断点连同它的边界情况一起没了。
           DOM 顺序 = 阅读顺序：先行情，后操作。 -->
      <article v-if="stockData" class="quote-card">
        <header class="quote-head">
          <h2 class="quote-name">
            {{ stockData.name || stockData.symbol || symbol.toUpperCase() }}
            <span v-if="stockData.name" class="quote-code num">{{ stockData.symbol }}</span>
          </h2>
          <p class="quote-updated num">{{ quoteDateText }}</p>
        </header>

        <p class="quote-price num">¥{{ stockData.price || 'N/A' }}</p>

        <!-- 带符号的涨跌额/幅是"方向"的冗余提示通道；颜色只是强化，
             所以即使完全看不到颜色，+/− 也把方向说清楚了 -->
        <p class="quote-delta">
          <span v-if="quoteChange" class="delta-pill num" :class="quoteChange.cls">{{ quoteChange.text }}</span>
          <span v-if="stockData.previousClose" class="quote-prev num">昨收 {{ stockData.previousClose }}</span>
        </p>

        <!-- 用 form 承载操作区：这样在任一输入框里按回车就能提交（原生表单语义），
             不必额外绑 keydown。按钮改成 type=submit。 -->
        <form class="quote-form" @submit.prevent="add(stockData.symbol || symbol.toUpperCase())">
          <input
            v-model="buyPrice"
            id="buy-price"
            aria-label="买入价（选填）"
            placeholder="买入价（选填）"
            inputmode="decimal"
            class="field num"
          />
          <input
            v-model="quantity"
            id="buy-quantity"
            aria-label="持有数量（选填）"
            placeholder="持有数量（选填）"
            inputmode="numeric"
            class="field num"
          />
          <button type="submit" class="btn-primary">添加自选</button>
        </form>
      </article>

      <p v-if="addError" class="error" role="alert">{{ addError }}</p>

      <section class="favorites">
        <h2 class="favorites-title">
          自选股
          <span v-if="favorites.length" class="fav-count num">{{ favorites.length }}</span>
        </h2>
        <!-- 长期存在的状态区：文本变化会被读屏软件播报（加载完成、列表变空） -->
        <p class="empty" role="status" :class="{ 'empty-idle': !favoritesStatus }">{{ favoritesStatus }}</p>

        <ul class="fav-grid">
          <li v-for="item in favorites" :key="item.symbol" class="fav-card">
            <!-- 整张卡是"进入详情"的动作 → 真按钮，键盘可达，命中区铺满卡片。
                 卡里不再嵌套"价格/涨跌"的独立控件，所以不存在按钮套按钮。 -->
            <button type="button" class="fav-main" @click="goDetail(item.symbol)">
              <span class="fav-id">
                <span class="fav-name">{{ item.name || item.symbol }}</span>
                <span v-if="item.name" class="fav-code num">{{ item.symbol }}</span>
              </span>
              <span class="fav-price num">¥{{ item.price || 'N/A' }}</span>
              <span class="fav-foot">
                <!-- 没有涨跌数据时给一个中性的破折号，而不是让这一格空掉 ——
                     位置稳定，用户也能一眼分辨"平盘/无数据"与"还没加载" -->
                <span class="delta-pill num" :class="dirClass(item.changePercent)">
                  {{ favPct(item) || '—' }}
                </span>
                <span v-if="item.previousClose" class="fav-prev num">昨收 {{ item.previousClose }}</span>
              </span>
            </button>
            <div class="fav-actions">
              <button
                type="button"
                class="btn-quiet"
                :aria-label="`为 ${item.name || item.symbol} 添加预警`"
                @click="goAddAlert(item.symbol)"
              >预警</button>
              <button
                type="button"
                class="btn-quiet danger"
                :aria-label="`从自选股删除 ${item.name || item.symbol}`"
                @click="remove(item.symbol)"
              >删除</button>
            </div>
          </li>
        </ul>
      </section>

    <!-- ========== 绑定同花顺弹窗（mode=bind：带 JWT，绑定到当前登录账号） ========== -->
    <div v-if="showBindModal" class="modal-mask" @click.self="closeBind">
      <div class="modal-box" role="dialog" aria-modal="true" aria-labelledby="bind-modal-title">
        <div class="modal-head">
          <h3 id="bind-modal-title">绑定同花顺账号</h3>
          <button type="button" class="modal-close" @click="closeBind" aria-label="关闭绑定弹窗">
            <span aria-hidden="true">×</span>
          </button>
        </div>
        <p class="modal-desc">
          用手机同花顺 App 扫码，确认后即绑定到当前账号。<br />
          绑定后会自动同步你的自选股，以后也可以直接用扫码登录。
        </p>
        <QrLogin mode="bind" @success="onBindSuccess" />
        <p v-if="syncMsg" class="ths-msg" role="status">{{ syncMsg }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 页面骨架（.page/.page-head）与导航已迁入 AppShell + style.css 全局层 */

.search-section {
  display: flex;
  gap: var(--space-2);
  margin-bottom: var(--space-5);
  align-items: flex-start;
}

.search-btn {
  padding: 10px 20px;
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border: none;
  border-radius: var(--radius-sm);
  font: var(--font-ui);
  font-weight: 500;
  white-space: nowrap;
  flex-shrink: 0;
  transition: background-color var(--duration-fast) var(--ease-out);
}

.search-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
}

.search-btn:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

/* ============================================================================
 * 行情信息块 —— 「宽松」方向
 *
 * 密度轴取最低位：行情卡单列堆叠、自选股是卡片网格、价格是每张卡的主角。
 * 这一档的"松"是设计决定，不是留白不够：组内 8px / 组间 24px，
 * 组间达到组内的 3×，稳过 better-layout 的 2× 下限。
 * ========================================================================== */

/* 涨跌软底胶囊：宽松这一档用软底承载"结论"。
   文字色用 --color-gain / --color-loss（对浅底实测 5.07:1 / 5.44:1，都过 AA）。
   平盘与"无数据"保持中性 —— 把没有方向画成红或绿，等于编了一个不存在的方向。 */
.delta-pill {
  display: inline-flex;
  align-items: center;
  border-radius: var(--radius-pill);
  padding: var(--space-1) var(--space-3);
  font: var(--font-caption);
  font-weight: 600;
  white-space: nowrap;
  background: var(--color-bg-subtle);
  color: var(--color-text-secondary);
}

.delta-pill.is-up {
  background: var(--color-gain-soft);
  color: var(--color-gain);
}

.delta-pill.is-down {
  background: var(--color-loss-soft);
  color: var(--color-loss);
}

/* ---------- 搜索结果：行情卡 ---------- */
.quote-card {
  background: var(--color-bg-surface);
  /* 20px 圆角配 16px 内距 → 内部控件该用 4px 圆角，同心（better-ui/surfaces.md） */
  border-radius: var(--radius-3xl);
  padding: var(--space-5);
  margin-bottom: var(--space-5);
  box-shadow: var(--shadow-1);
}

.quote-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.quote-name {
  font: var(--font-heading);
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  flex-wrap: wrap;
  min-width: 0;
  color: var(--color-text-primary);
}

.quote-code,
.quote-updated {
  font: var(--font-caption);
  color: var(--color-text-muted);
}

/* 一屏只有这一个数字走 display 档（36px）—— 它是这个页面唯一的主角。
   价格配色由 .delta-pill 按方向给；这里不染色，避免同一屏出现两处同义的红/绿。 */
.quote-price {
  font: var(--font-display);
  letter-spacing: -0.01em;
  margin-top: var(--space-3);
  color: var(--color-text-primary);
}

.quote-delta {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-top: var(--space-2);
}

.quote-prev {
  font: var(--font-caption);
  color: var(--color-text-secondary);
}

/* 单列堆叠：这一档的"松"首先体现在这里 —— 控件不挤在同一行。
   限宽 360px 是给表单定"测量长度"，表单不该跟着卡片无限变宽。 */
.quote-form {
  display: grid;
  gap: var(--space-2);
  margin-top: var(--space-5);
  max-width: 360px;
}

.field {
  width: 100%;
  padding: var(--space-3);
  border: 1px solid var(--color-border-control);
  border-radius: var(--radius-sm);
  /* 16px 是 iOS 的下限：输入框文字小于 16px，Safari 会把整页放大。
     所以这里用 --font-body 而不是 --font-ui —— 视觉一致性让位给"不要乱缩放"。 */
  font: var(--font-body);
  background: var(--color-bg-surface);
}

.btn-primary {
  padding: var(--space-3) var(--space-5);
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border: none;
  border-radius: var(--radius-sm);
  font: var(--font-ui);
  font-weight: 500;
  transition: background-color var(--duration-fast) var(--ease-out);
}

.btn-primary:hover {
  background: var(--color-accent-hover);
}

/* ---------- 自选股：卡片网格 ---------- */
.favorites-title {
  font: var(--font-heading);
  color: var(--color-text-primary);
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
}

.fav-count {
  font: var(--font-caption);
  color: var(--color-text-muted);
}

.empty {
  font: var(--font-ui);
  color: var(--color-text-muted);
  text-align: center;
  padding: var(--space-6);
  background: var(--color-bg-surface);
  border-radius: var(--radius-3xl);
  box-shadow: var(--shadow-1);
}

/* 无文案时收起盒子但不移出无障碍树，保持 role="status" 稳定 */
.empty-idle {
  padding: 0;
  background: none;
  box-shadow: none;
}

.fav-grid {
  list-style: none;
  display: grid;
  /* 240px 是内容给的下限：低于它，"名称 + 代码"一行放不下就会折行 */
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--space-5);
}

.fav-card {
  background: var(--color-bg-surface);
  border-radius: var(--radius-3xl);
  padding: var(--space-4);
  box-shadow: var(--shadow-1);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  transition-property: box-shadow;
  transition-duration: 150ms;
  transition-timing-function: var(--ease-out);
}

/* 悬停加深一档深度而不是位移：这张卡本身是按钮，位移会让文字跟着抖 */
.fav-card:hover {
  box-shadow: var(--shadow-2);
}

/* 整张卡是"进入详情"的动作 → 真 button，键盘可达，命中区铺满卡片。
   卡内不再嵌第二层交互元素，所以不存在按钮套按钮。 */
.fav-main {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-2);
  width: 100%;
  background: none;
  border: none;
  font: inherit;
  color: inherit;
  text-align: start;
  padding: 0;
  border-radius: var(--radius-sm);
}

.fav-id {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  flex-wrap: wrap;
  min-width: 0;
}

.fav-name {
  font: var(--font-ui);
  font-weight: 600;
  color: var(--color-text-primary);
}

.fav-code,
.fav-prev {
  font: var(--font-caption);
  color: var(--color-text-muted);
}

/* 卡片里价格走 title 档（24px）。变体里手写的是 28px，这里回到字阶上 ——
   用 24px 而不是 28px，正是"有一套字阶"和"每次都手挑一个数"的区别。 */
.fav-price {
  font: var(--font-title);
  letter-spacing: -0.01em;
  color: var(--color-text-primary);
}

.fav-foot {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

/* 操作常驻可见（键盘可达，也不需要悬停才知道它存在），但退到最弱的一档：
   用一条分隔线说明"这是附加动作"，而不是靠彩色实心按钮抢注意力。 */
.fav-actions {
  display: flex;
  gap: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px solid var(--color-border);
  margin-top: var(--space-1);
}

.btn-quiet {
  /* 32px 命中区，远高于 WCAG 2.5.8 的 24px 下限 —— 这一档不需要把它压到最小 */
  min-height: 32px;
  padding: var(--space-1) var(--space-2);
  background: none;
  border: none;
  border-radius: var(--radius-sm);
  font: var(--font-caption);
  color: var(--color-text-secondary);
  transition-property: background-color, color;
  transition-duration: var(--duration-fast);
  transition-timing-function: var(--ease-out);
}

.btn-quiet:hover {
  background: var(--color-bg-subtle);
  color: var(--color-text-primary);
}

.btn-quiet.danger:hover {
  background: var(--color-danger-soft);
  color: var(--color-danger);
}

.error {
  font: var(--font-ui);
  color: var(--color-danger);
  margin-bottom: var(--space-3);
}

/* ---- 同花顺绑定卡片 ---- */
.ths-card {
  background: var(--color-bg-surface);
  border-radius: var(--radius-lg);
  padding: 14px 18px;
  margin-bottom: 20px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  box-shadow: var(--shadow-1);
}

.ths-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.ths-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-warning-mark);
  display: inline-block;
}

.ths-dot.ok {
  background: var(--color-success-mark);
}

.ths-warn {
  font-size: 12px;
  font-weight: 400;
  color: var(--color-warning);
}

.ths-sub {
  font-size: 13px;
  color: var(--color-text-secondary);
  margin-top: 4px;
  line-height: 1.5;
}

.ths-err {
  color: var(--color-danger);
}

.ths-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.ths-btn {
  padding: 7px 16px;
  background: var(--color-bg-surface);
  color: var(--color-accent);
  border: 1px solid var(--color-accent);
  border-radius: var(--radius-sm);
  font-size: 13px;
  white-space: nowrap;
  transition: background-color var(--duration-fast) var(--ease-out);
}

.ths-btn:hover {
  background: var(--color-accent-soft);
}

.ths-btn.primary {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
}

.ths-btn.primary:hover {
  background: var(--color-accent-hover);
}

.ths-btn:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.ths-msg {
  font-size: 13px;
  color: var(--color-accent);
  margin: -8px 0 16px;
}

/* ---- 绑定弹窗 ---- */
.modal-mask {
  position: fixed;
  inset: 0;
  background: var(--color-overlay);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  z-index: 1000;
}

.modal-box {
  background: var(--color-bg-surface);
  border-radius: var(--radius-lg);
  padding: 24px;
  /* 旧值固定 width:340px，在 320px 视口下横向溢出 20px */
  width: min(340px, 100%);
  display: flex;
  flex-direction: column;
  align-items: center;
  max-height: calc(100dvh - 32px);
  overflow-y: auto;
}

.modal-head {
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.modal-head h3 {
  margin: 0;
  font-size: 16px;
  color: var(--color-text-primary);
}

.modal-close {
  background: none;
  border: none;
  font-size: 22px;
  color: var(--color-text-secondary);
  line-height: 1;
  /* 命中区 >=24×24（WCAG 2.5.8 AA） */
  min-width: 32px;
  min-height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
}

.modal-close:hover {
  color: var(--color-text-primary);
}

.modal-desc {
  font-size: 13px;
  color: var(--color-text-secondary);
  text-align: center;
  line-height: 1.6;
  margin: 12px 0 18px;
}
</style>
