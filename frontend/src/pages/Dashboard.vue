<script setup>
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getStock, getFavorites, addFavorite, deleteFavorite } from '../api/stock.js'
import StockSearchInput from '../components/StockSearchInput.vue'
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

const searchInputRef = ref(null)

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
    error.value = '查询失败，请检查股票代码'
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
    addError.value = '买入价需为正数'
    return
  }
  if (qtyRaw && (isNaN(qtyRaw) || parseInt(qtyRaw) <= 0)) {
    addError.value = '持有数量需为正整数'
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
    addError.value = '添加失败，请重试'
  }
}

const remove = async (sym) => {
  try {
    await deleteFavorite(sym)
    await loadFavorites()
  } catch (e) {
    error.value = '删除失败，请重试'
  }
}

const goDetail = (sym) => router.push('/chart/' + sym)

const goAssistant = () => router.push('/assistant')
const goStrategies = () => router.push('/strategies')
const goMessages = () => router.push('/messages')
const goAlerts = () => router.push('/alerts')
const goAddAlert = (sym) => router.push({ path: '/alerts', query: { symbol: sym, new: '1' } })
const goProfile = () => router.push('/profile')

onMounted(() => {
  loadFavorites()
  messageBus.connect()
  messageBus.refreshUnread()
})
</script>

<template>
  <div class="app-layout">
    <header>
      <h1>Stock Tracker</h1>
      <div class="header-actions">
        <button class="nav-btn assistant-nav" @click="goAssistant">🤖 智能助手</button>
        <button class="nav-btn badge-btn" @click="goMessages">
          消息
          <span v-if="messageBus.unread > 0" class="unread-badge">{{ messageBus.unread > 99 ? '99+' : messageBus.unread }}</span>
        </button>
        <button class="nav-btn" @click="goAlerts">预警</button>
        <button class="nav-btn" @click="goProfile">我的</button>
      </div>
    </header>
    <main>
      <section class="feature-grid">
        <div class="feature-card assistant-entry" @click="goAssistant">
          <div class="feature-icon">🤖</div>
          <div class="feature-copy">
          <strong>智能助手</strong>
          <span>自然语言查行情、生成交易策略、回测与模拟盘</span>
          </div>
          <span class="feature-arrow">→</span>
        </div>

        <div class="feature-card strategy-entry" @click="goStrategies">
          <div class="feature-icon">📚</div>
          <div class="feature-copy">
            <strong>策略库</strong>
            <span>统一管理策略、回测与模拟盘</span>
          </div>
          <span class="feature-arrow">→</span>
        </div>
      </section>

      <section class="search-section">
        <StockSearchInput ref="searchInputRef" v-model="symbol" />
        <button @click="search" :disabled="loading">{{ loading ? '查询中...' : '查询' }}</button>
      </section>

      <p v-if="error" class="error">{{ error }}</p>

      <div v-if="stockData" class="stock-card">
        <div class="price-main">
          <span class="symbol">{{ stockData.name || stockData.symbol || symbol.toUpperCase() }}<small class="symbol-code">{{ stockData.name ? stockData.symbol : "" }}</small></span>
          <span class="price">${{ stockData.price || "N/A" }}</span>
        </div>
        <p class="update-time">更新: {{ stockData.lastUpdated || 'N/A' }}</p>
        <div class="buy-inputs">
          <input v-model="buyPrice" placeholder="买入价（选填）" class="buy-input" />
          <input v-model="quantity" placeholder="持有数量（选填）" class="buy-input" />
          <button class="fav-btn" @click="add(stockData.symbol || symbol.toUpperCase())">+ 添加自选</button>
        </div>
      </div>

      <p v-if="addError" class="error">{{ addError }}</p>

      <section class="favorites">
        <h2>自选股</h2>
        <div v-if="!favoritesLoaded" class="empty">加载中...</div>
        <div v-else-if="favorites.length === 0" class="empty">暂无自选股</div>
        <div v-for="item in favorites" :key="item.symbol" class="fav-item" @click="goDetail(item.symbol)">
          <div class="fav-info">
            <strong>{{ item.name || item.symbol }}<small class="symbol-code">{{ item.name ? item.symbol : "" }}</small></strong>
            <span class="fav-price">${{ item.price || "N/A" }}</span>
          </div>
          <div class="fav-actions">
            <button class="alert-btn" @click.stop="goAddAlert(item.symbol)" title="为 {{ item.name || item.symbol }} 添加预警">+ 预警</button>
            <button class="del-btn" @click.stop="remove(item.symbol)">删除</button>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.app-layout { min-height: 100vh; background: #f0f2f5; }
header { background: #1a1a2e; color: white; padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; }
header h1 { margin: 0; font-size: 20px; }
.header-actions { display: flex; gap: 8px; }
.nav-btn { background: rgba(255,255,255,0.15); border: none; color: white; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-size: 13px; transition: background 0.2s; }
.nav-btn:hover { background: rgba(255,255,255,0.25); }
.assistant-nav { background: #1677ff; color: white; font-weight: 600; }
.assistant-nav:hover { background: #4096ff; }
.badge-btn { position: relative; }
.unread-badge {
  position: absolute;
  top: -6px;
  right: -6px;
  background: #ff4d4f;
  color: white;
  font-size: 10px;
  line-height: 1;
  padding: 3px 5px;
  border-radius: 9px;
  min-width: 16px;
  text-align: center;
}
main { max-width: 760px; margin: 0 auto; padding: 24px 16px; }

.feature-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 20px;
}
.feature-card {
  display: flex;
  align-items: center;
  gap: 14px;
  color: white;
  border-radius: 12px;
  padding: 16px 18px;
  cursor: pointer;
  transition: transform 0.2s, box-shadow 0.2s;
}
.feature-card:hover { transform: translateY(-1px); }
.assistant-entry {
  background: linear-gradient(135deg, #1677ff 0%, #69b1ff 100%);
  box-shadow: 0 8px 20px rgba(22, 119, 255, 0.22);
}
.assistant-entry:hover { box-shadow: 0 10px 24px rgba(22, 119, 255, 0.3); }
.strategy-entry {
  background: linear-gradient(135deg, #722ed1 0%, #b37feb 100%);
  box-shadow: 0 8px 20px rgba(114, 46, 209, 0.22);
}
.strategy-entry:hover { box-shadow: 0 10px 24px rgba(114, 46, 209, 0.3); }
.feature-icon {
  width: 46px;
  height: 46px;
  border-radius: 12px;
  background: rgba(255,255,255,0.22);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  flex-shrink: 0;
}
.feature-copy { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.feature-copy strong { font-size: 17px; }
.feature-copy span { font-size: 12px; opacity: 0.9; }
.feature-arrow { margin-left: auto; font-size: 22px; opacity: 0.9; }

@media (max-width: 640px) {
  .feature-grid { grid-template-columns: 1fr; }
}

.search-section { display: flex; gap: 8px; margin-bottom: 20px; align-items: flex-start; }
.search-section button { padding: 10px 20px; background: #1677ff; color: white; border: none; border-radius: 4px; cursor: pointer; white-space: nowrap; flex-shrink: 0; }
.stock-card { background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.1); }
.price-main { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.symbol { font-size: 24px; font-weight: bold; display: flex; align-items: baseline; gap: 8px; }
.price { font-size: 28px; font-weight: bold; color: #52c41a; }
.update-time { color: #888; font-size: 13px; margin-bottom: 12px; }
.buy-inputs { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.buy-input { flex: 1; min-width: 120px; padding: 8px 10px; border: 1px solid #d9d9d9; border-radius: 4px; font-size: 13px; }
.buy-input:focus { outline: none; border-color: #4096ff; }
.fav-btn { padding: 8px 16px; background: #52c41a; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; white-space: nowrap; }
.fav-btn:hover { background: #73d13d; }
.favorites h2 { font-size: 18px; margin-bottom: 12px; }
.empty { color: #999; text-align: center; padding: 32px; }
.fav-item { background: white; border-radius: 6px; padding: 12px 16px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 1px 4px rgba(0,0,0,0.08); cursor: pointer; }
.fav-info { display: flex; align-items: center; }
.fav-info strong { display: flex; align-items: baseline; gap: 8px; }
.symbol-code { font-size: 13px; color: #999; font-weight: normal; }
.fav-actions { display: flex; gap: 8px; }
.fav-price { margin-left: 12px; color: #52c41a; font-weight: bold; }
.alert-btn { padding: 4px 12px; background: #fa8c16; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; }
.alert-btn:hover { background: #ffa940; }
.del-btn { padding: 4px 12px; background: #ff4d4f; color: white; border: none; border-radius: 4px; cursor: pointer; }
.error { color: #ff4d4f; margin-bottom: 12px; }
</style>
