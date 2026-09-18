<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getStock, getFavorites, addFavorite, deleteFavorite } from '../api/stock.js'
import StockSearchInput from '../components/StockSearchInput.vue'
import QrLogin from '../components/QrLogin.vue'
import { getStatus, syncFavorites } from '../api/ths.js'
import { getPaperOverview } from '../api/strategy.js'
import { messageBus } from '../composables/messageBus.js'

const router = useRouter()
const route = useRoute()
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
  if (!window.confirm(`确定删除 ${label} 吗？删除后需要重新添加。`)) return

  try {
    await deleteFavorite(sym)
    await loadFavorites()
  } catch (e) {
    error.value = '删除失败，请检查网络后重试'
  }
}

const goDetail = (sym) => router.push('/chart/' + sym)

/**
 * 模拟盘状态：首页必须能一眼看出"它在跑"。
 *
 * <p>以前的失败方式不是算错，而是**用户根本不知道有这个功能** ——
 * 模拟盘藏在"策略库 → 某条策略 → 滚到底"，全站只有两行副标题提过它。
 * 所以这里在首页就把"运行中几条、今天结算了没、下次什么时候"摆出来。
 *
 * <p>读失败**不打扰**首页：静默降级为不显示那张卡的副标题，而不是弹一个错误 ——
 * 首页已经有很多别的信息，模拟盘状态只是"锦上添花"的那一块。
 */
const paperStatus = ref(null)

const loadPaperStatus = async () => {
  try {
    const res = await getPaperOverview()
    const list = Array.isArray(res.data) ? res.data : []
    const running = list.filter((item) => item.paperEnabled)
    const today = new Date().toLocaleDateString('sv-SE')
    paperStatus.value = {
      running: running.length,
      total: list.length,
      settledToday: running.filter((item) => item.lastSettlement?.tradeDate === today).length,
      nextNote: running[0]?.nextEvaluationNote || ''
    }
  } catch (e) {
    paperStatus.value = null
  }
}

const goAssistant = () => router.push('/assistant')
const goStrategies = () => router.push('/strategies')
const goPaper = () => router.push('/paper')
const goMemory = () => router.push('/memory')
const goMessages = () => router.push('/messages')
const goAlerts = () => router.push('/alerts')
const goAddAlert = (sym) => router.push({ path: '/alerts', query: { symbol: sym, new: '1' } })
const goProfile = () => router.push('/profile')

onMounted(() => {
  loadFavorites()
  loadThsStatus()
  loadPaperStatus()
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
  <div class="app-layout">
    <header>
      <h1>Stock Tracker</h1>
      <div class="header-actions">
        <button type="button" class="nav-btn assistant-nav" @click="goAssistant">
          <span aria-hidden="true">🤖</span> 智能助手
        </button>
        <button type="button" class="nav-btn badge-btn" @click="goMessages">
          消息
          <span
            v-if="messageBus.unread > 0"
            class="unread-badge num"
            :aria-label="`${messageBus.unread} 条未读消息`"
          >{{ messageBus.unread > 99 ? '99+' : messageBus.unread }}</span>
        </button>
        <button type="button" class="nav-btn" @click="goPaper">
          模拟盘
          <span v-if="paperStatus && paperStatus.running > 0" class="unread-badge num"
            :aria-label="`${paperStatus.running} 条模拟盘在跑`">{{ paperStatus.running }}</span>
        </button>
        <button type="button" class="nav-btn" @click="goAlerts">预警</button>
        <button type="button" class="nav-btn" @click="goProfile">我的</button>
      </div>
    </header>
    <main>
      <section class="feature-grid">
        <button type="button" class="feature-card assistant-entry" @click="goAssistant">
          <span class="feature-icon" aria-hidden="true">🤖</span>
          <span class="feature-copy">
            <strong>智能助手</strong>
            <span>自然语言查行情、生成交易策略、回测与模拟盘</span>
          </span>
          <span class="feature-arrow" aria-hidden="true">→</span>
        </button>

        <button type="button" class="feature-card strategy-entry" @click="goStrategies">
          <span class="feature-icon" aria-hidden="true">📚</span>
          <span class="feature-copy">
            <strong>策略库</strong>
            <span>统一管理策略、回测与模拟盘</span>
          </span>
          <span class="feature-arrow" aria-hidden="true">→</span>
        </button>

        <button type="button" class="feature-card paper-entry" @click="goPaper">
          <span class="feature-icon" aria-hidden="true">📈</span>
          <span class="feature-copy">
            <strong>模拟盘</strong>
            <!-- 有状态就报状态：一眼看出"它在跑、今天结算了没、下次什么时候" -->
            <span v-if="paperStatus && paperStatus.running > 0">
              运行中 {{ paperStatus.running }} 条 · 今日已结算 {{ paperStatus.settledToday }} 条<template
                v-if="paperStatus.nextNote"
              ><br />{{ paperStatus.nextNote }}</template>
            </span>
            <span v-else-if="paperStatus && paperStatus.total > 0">
              还没有运行中的模拟盘 —— 打开看看哪条策略可以启动
            </span>
            <span v-else>用虚拟资金按真实规则跑策略：每天 15:30 结算一次</span>
          </span>
          <span class="feature-arrow" aria-hidden="true">→</span>
        </button>

        <button type="button" class="feature-card memory-entry" @click="goMemory">
          <span class="feature-icon" aria-hidden="true">🧠</span>
          <span class="feature-copy">
            <strong>记忆</strong>
            <span>看看助手记住了你什么，随时撤回</span>
          </span>
          <span class="feature-arrow" aria-hidden="true">→</span>
        </button>
      </section>

      <!-- ========== 同花顺绑定 ==========
           已绑定 → 显示同步状态 + 重新同步按钮
           未绑定 → 提示绑定（扫码登录进来的用户会自动是已绑定状态，
                    所以这个卡片对他们来说直接就是"已绑定"，不用再绑） -->
      <section v-if="thsBound === true" class="ths-card bound">
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

      <!-- 行情卡片：分栏结构 —— 左栏行情（名称/代码、价格、更新时间），右栏操作（两个输入 + 按钮）。
           折叠用容器查询而非媒体查询：阈值取的是"这张卡自己的宽度够不够放下两栏"，
           将来把它放进更窄的栏里也依然正确。DOM 顺序 = 阅读顺序：先行情，后操作。 -->
      <div v-if="stockData" class="stock-card">
        <div class="card-split">
          <div class="quote-col">
            <span class="symbol">
              {{ stockData.name || stockData.symbol || symbol.toUpperCase() }}
              <small v-if="stockData.name" class="symbol-code">{{ stockData.symbol }}</small>
            </span>
            <span class="price-row" :class="quoteChange?.cls">
              <span class="price num">¥{{ stockData.price || 'N/A' }}</span>
              <!-- 带符号的涨跌额/幅是"方向"的冗余提示通道；颜色只是强化，
                   所以即使完全看不到颜色，+/− 也把方向说清楚了 -->
              <span v-if="quoteChange" class="price-change num">{{ quoteChange.text }}</span>
            </span>
            <p class="update-time num">更新: {{ stockData.lastUpdated || 'N/A' }}</p>
          </div>

          <!-- 用 form 承载操作区：这样在任一输入框里按回车就能提交（原生表单语义），
               不必额外绑 keydown。按钮改成 type=submit。 -->
          <form class="action-col" @submit.prevent="add(stockData.symbol || symbol.toUpperCase())">
            <input
              v-model="buyPrice"
              id="buy-price"
              aria-label="买入价（选填）"
              placeholder="买入价（选填）"
              inputmode="decimal"
              class="buy-input num"
            />
            <input
              v-model="quantity"
              id="buy-quantity"
              aria-label="持有数量（选填）"
              placeholder="持有数量（选填）"
              inputmode="numeric"
              class="buy-input num"
            />
            <button type="submit" class="fav-btn">+ 添加自选</button>
          </form>
        </div>
      </div>

      <p v-if="addError" class="error" role="alert">{{ addError }}</p>

      <section class="favorites">
        <h2>自选股</h2>
        <!-- 长期存在的状态区：文本变化会被读屏软件播报（加载完成、列表变空） -->
        <p class="empty" role="status" :class="{ 'empty-idle': !favoritesStatus }">{{ favoritesStatus }}</p>
        <div v-for="item in favorites" :key="item.symbol" class="fav-item">
          <button type="button" class="fav-info" @click="goDetail(item.symbol)">
            <strong>
              {{ item.name || item.symbol }}
              <small v-if="item.name" class="symbol-code">{{ item.symbol }}</small>
            </strong>
            <span class="fav-right" :class="dirClass(item.changePercent)">
              <span class="fav-price num">¥{{ item.price || 'N/A' }}</span>
              <span v-if="favPct(item)" class="fav-change num">{{ favPct(item) }}</span>
            </span>
          </button>
          <div class="fav-actions">
            <button
              type="button"
              class="alert-btn"
              :aria-label="`为 ${item.name || item.symbol} 添加预警`"
              @click="goAddAlert(item.symbol)"
            >+ 预警</button>
            <button
              type="button"
              class="del-btn"
              :aria-label="`从自选股删除 ${item.name || item.symbol}`"
              @click="remove(item.symbol)"
            >删除</button>
          </div>
        </div>
      </section>
    </main>

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
.app-layout {
  /* 100dvh 兜底：移动浏览器地址栏会算进 100vh，导致内容底部被顶出可视区 */
  min-height: 100vh;
  min-height: 100dvh;
  background: var(--color-bg-page);
}

header {
  background: var(--color-bg-inverse);
  color: var(--color-text-inverse);
  /* iOS 独立模式下内容会顶到状态栏底下，让出安全区 */
  padding: calc(16px + env(safe-area-inset-top, 0px)) 24px 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

header h1 {
  margin: 0;
  font-size: 20px;
  /* 显式声明：不要依赖从 header 继承（旧脚手架模板里的 h1 颜色规则会把它覆盖成近黑色） */
  color: var(--color-text-inverse);
}

.header-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.nav-btn {
  background: rgb(255 255 255 / 0.15);
  border: none;
  color: var(--color-text-inverse);
  padding: 6px 14px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  transition: background-color var(--duration-base) var(--ease-out);
}

.nav-btn:hover {
  background: rgb(255 255 255 / 0.25);
}

.assistant-nav {
  background: var(--color-accent);
  font-weight: 600;
}

.assistant-nav:hover {
  background: var(--color-accent-hover);
}

.badge-btn {
  position: relative;
}

.unread-badge {
  position: absolute;
  top: -6px;
  right: -6px;
  background: var(--color-danger);
  color: var(--color-text-on-accent);
  font-size: 10px;
  line-height: 1;
  padding: 3px 5px;
  border-radius: var(--radius-pill);
  min-width: 16px;
  text-align: center;
}

main {
  max-width: 760px;
  margin: 0 auto;
  padding: 24px 16px;
  width: 100%;
}

.feature-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 20px;
}

/* 卡片是动作 → 用真正的 button，键盘可达 */
.feature-card {
  display: flex;
  align-items: center;
  gap: 14px;
  color: var(--color-text-on-accent);
  border: none;
  border-radius: var(--radius-xl);
  padding: 16px 18px;
  font: inherit;
  text-align: left;
  transition: transform var(--duration-base) var(--ease-out),
    box-shadow var(--duration-base) var(--ease-out);
}

.feature-card:hover {
  transform: translateY(-1px);
}

/* 渐变浅端要保证白字 >=4.5:1。旧值 #1677ff→#69b1ff 在浅端只有 2.25:1，
   策略卡旧值 #722ed1→#b37feb 浅端只有 2.94:1 */
.assistant-entry {
  background: linear-gradient(135deg, var(--c-blue-800) 0%, var(--c-blue-700) 100%);
  box-shadow: 0 8px 20px rgb(0 62 179 / 0.22);
}

.assistant-entry:hover {
  box-shadow: 0 10px 24px rgb(0 62 179 / 0.3);
}

.strategy-entry {
  background: linear-gradient(135deg, var(--c-purple-800) 0%, var(--c-purple-700) 100%);
  box-shadow: 0 8px 20px rgb(83 29 171 / 0.22);
}

.strategy-entry:hover {
  box-shadow: 0 10px 24px rgb(83 29 171 / 0.3);
}

/* 记忆卡：沿用项目原始色板里的绿色，深端 #135200 配白字 9.40:1、
   浅端 #237804 配白字 5.59:1，两端都过 AA（渐变浅端最容易踩这个坑） */
.memory-entry {
  background: linear-gradient(135deg, var(--c-green-800) 0%, var(--c-green-700) 100%);
  box-shadow: 0 8px 20px rgb(19 82 0 / 0.22);
}

.memory-entry:hover {
  box-shadow: 0 10px 24px rgb(19 82 0 / 0.3);
}

.feature-icon {
  width: 46px;
  height: 46px;
  border-radius: var(--radius-xl);
  background: rgb(255 255 255 / 0.22);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  flex-shrink: 0;
}

.feature-copy {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.feature-copy strong {
  font-size: 17px;
}

.feature-copy span {
  font-size: 12px;
}

.feature-arrow {
  margin-left: auto;
  font-size: 22px;
}

@media (max-width: 640px) {
  .feature-grid {
    grid-template-columns: 1fr;
  }
}

.search-section {
  display: flex;
  gap: 8px;
  margin-bottom: 20px;
  align-items: flex-start;
}

.search-btn {
  padding: 10px 20px;
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border: none;
  border-radius: var(--radius-sm);
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

.stock-card {
  background: var(--color-bg-surface);
  border-radius: var(--radius-lg);
  padding: 20px;
  margin-bottom: 20px;
  box-shadow: var(--shadow-1);
  /* 折叠阈值按"卡片自己的宽度"判断，不按视口 —— 见下方 @container */
  container-type: inline-size;
}

/* 两栏：左行情 / 右操作 */
.card-split {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 24px;
  align-items: start;
}

.quote-col {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.action-col {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.symbol {
  font-size: 24px;
  font-weight: bold;
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
  min-width: 0;
}

/* 价格配色 = 涨跌方向（A 股约定红涨绿跌）。
   方向来自后端透传的 change / changePercent（源头是 Python 数据源解析的腾讯行情）。
   中性色是"没有涨跌信息"时的兜底，不表示任何一种方向。
   颜色只是强化：带符号的涨跌额/幅就显示在价格旁边，方向不靠颜色单独承载。
   旧值 #52c41a 对白仅 2.27:1 不达标，且绿色同时表示"绑定成功"，一色两义 —— 已废弃。 */
.price-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
  color: var(--color-text-primary);
}

.price-row.is-up {
  color: var(--color-gain);
}

.price-row.is-down {
  color: var(--color-loss);
}

/* 价格本身只保留字号与字重表达重要性，颜色由 .price-row 按方向给 */
.price {
  font-size: 28px;
  font-weight: bold;
}

.price-change {
  font-size: 15px;
  font-weight: 600;
}

.update-time {
  color: var(--color-text-secondary);
  font-size: 13px;
  margin-top: 8px;
}

.buy-input {
  width: 100%;
  min-width: 120px;
  padding: 8px 10px;
  /* 控件边界需 >=3:1（WCAG 1.4.11），旧值 #d9d9d9 对白仅 1.41:1 */
  border: 1px solid var(--color-border-control);
  border-radius: var(--radius-sm);
  font-size: 13px;
}

.fav-btn {
  margin-top: 8px;
  width: 100%;
  padding: 10px 16px;
  background: var(--color-success);
  color: var(--color-text-on-accent);
  border: none;
  border-radius: var(--radius-sm);
  font-size: 14px;
  white-space: nowrap;
  transition: background-color var(--duration-fast) var(--ease-out);
}

.fav-btn:hover {
  /* hover 必须更深而不是更浅：白色文字在浅绿 #389e0d 上只有 3.46:1 */
  background: var(--color-success-hover);
}

/* 折叠成一栏 = 堆叠。阈值来自内容：左栏「名称+代码」单行约需 150px，
   右栏输入框要放下「买入价（选填）」占位符约需 140px，加 24px 栏距 ≈ 314px，
   留出余量取 420px。
   注意：容器查询量的是**内容盒**（已扣掉卡片自身的 20px 内距），
   所以 420px 这一档对应卡片宽约 460px、视口约 492px。
   实测：视口 480（卡片 448 / 内容盒 408）已折叠，视口 760（卡片 728 / 内容盒 688）是两栏。 */
@container (max-width: 420px) {
  .card-split {
    grid-template-columns: minmax(0, 1fr);
    gap: 0;
  }

  .update-time {
    margin-top: 12px;
  }

  .action-col {
    margin-top: 20px;
  }
}

.favorites h2 {
  font-size: 18px;
  margin-bottom: 12px;
  color: var(--color-text-primary);
}

.empty {
  color: var(--color-text-muted);
  text-align: center;
  padding: 32px;
}

/* 无文案时收起盒子但不移出无障碍树，保持 role="status" 稳定 */
.empty-idle {
  padding: 0;
}

.fav-item {
  background: var(--color-bg-surface);
  border-radius: var(--radius-md);
  padding: 12px 16px;
  margin-bottom: 8px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  box-shadow: var(--shadow-1);
}

/* 行内的"进入详情"是一个动作 → 真正的 button，键盘可达 */
.fav-info {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex: 1;
  min-width: 0;
  gap: 12px;
  background: none;
  border: none;
  font: inherit;
  color: inherit;
  text-align: left;
  padding: 2px 0;
  border-radius: var(--radius-sm);
}

.fav-info strong {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.symbol-code {
  font-size: 13px;
  color: var(--color-text-muted);
  font-weight: normal;
}

.fav-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

/* 自选股行右侧：价格 + 涨跌幅。颜色由 .fav-right 按方向统一给，
   带符号的百分比是"方向"的冗余提示通道 —— 与卡片同一套规则 */
.fav-right {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-shrink: 0;
  color: var(--color-text-primary);
}

.fav-right.is-up {
  color: var(--color-gain);
}

.fav-right.is-down {
  color: var(--color-loss);
}

.fav-price {
  font-weight: bold;
}

.fav-change {
  font-size: 13px;
  font-weight: 600;
}

.alert-btn {
  padding: 6px 12px;
  background: var(--color-warning);
  color: var(--color-text-on-accent);
  border: none;
  border-radius: var(--radius-sm);
  font-size: 13px;
  transition: background-color var(--duration-fast) var(--ease-out);
}

.alert-btn:hover {
  background: var(--color-warning-hover);
}

.del-btn {
  padding: 6px 12px;
  background: var(--color-danger);
  color: var(--color-text-on-accent);
  border: none;
  border-radius: var(--radius-sm);
  font-size: 13px;
  transition: background-color var(--duration-fast) var(--ease-out);
}

.del-btn:hover {
  background: var(--color-danger-hover);
}

.error {
  color: var(--color-danger);
  margin-bottom: 12px;
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
  border-left: 3px solid var(--color-warning-mark);
}

.ths-card.bound {
  border-left-color: var(--color-success-mark);
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
