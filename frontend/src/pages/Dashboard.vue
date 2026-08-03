<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getStock, getFavorites, addFavorite, deleteFavorite } from '../api/stock.js'
import StockSearchInput from '../components/StockSearchInput.vue'

const router = useRouter()
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
    // 记录到搜索历史（仅记录代码，名称由 autocomplete 选择时带）
    searchInputRef.value?.recordSearch(query.toUpperCase(), query.toUpperCase())
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

const goDetail = (sym) => router.push('/stock/' + sym)

const logout = () => {
  localStorage.removeItem('token')
  router.push('/login')
}

const goBindQq = () => router.push('/bind-qq')

onMounted(loadFavorites)
</script>

<template>
  <div class="app-layout">
    <header>
      <h1>Stock Tracker</h1>
      <div class="header-actions">
        <button class="bind-btn" @click="goBindQq">🔗 绑定QQ</button>
        <button class="logout-btn" @click="logout">退出</button>
      </div>
    </header>
    <main>
      <section class="search-section">
        <StockSearchInput ref="searchInputRef" v-model="symbol" />
        <button @click="search" :disabled="loading">{{ loading ? '查询中...' : '查询' }}</button>
      </section>

      <p v-if="error" class="error">{{ error }}</p>

      <div v-if="stockData" class="stock-card">
        <div class="price-main">
          <span class="symbol">{{ stockData.symbol || symbol.toUpperCase() }}</span>
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
          <div>
            <strong>{{ item.symbol }}</strong>
            <span class="fav-price">${{ item.price || "N/A" }}</span>
          </div>
          <button class="del-btn" @click.stop="remove(item.symbol)">删除</button>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.app-layout { min-height: 100vh; background: #f0f2f5; }
header { background: #1a1a2e; color: white; padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; }
header h1 { margin: 0; font-size: 20px; }
.header-actions { display: flex; gap: 12px; }
.bind-btn { background: #722ed1; border: none; color: white; padding: 6px 16px; border-radius: 4px; cursor: pointer; }
.bind-btn:hover { background: #9254de; }
.logout-btn { background: transparent; border: 1px solid white; color: white; padding: 6px 16px; border-radius: 4px; cursor: pointer; }
main { max-width: 640px; margin: 0 auto; padding: 24px 16px; }

.search-section { display: flex; gap: 8px; margin-bottom: 20px; align-items: flex-start; }
.search-section button { padding: 10px 20px; background: #1677ff; color: white; border: none; border-radius: 4px; cursor: pointer; white-space: nowrap; flex-shrink: 0; }
.stock-card { background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.1); }
.price-main { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.symbol { font-size: 24px; font-weight: bold; }
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
.fav-price { margin-left: 12px; color: #52c41a; font-weight: bold; }
.del-btn { padding: 4px 12px; background: #ff4d4f; color: white; border: none; border-radius: 4px; cursor: pointer; }
.error { color: #ff4d4f; margin-bottom: 12px; }
</style>