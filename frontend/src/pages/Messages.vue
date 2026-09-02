<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { getMessages, markRead, markAllRead } from '../api/messages.js'
import { messageBus } from '../composables/messageBus.js'

const router = useRouter()

// 消息分页状态
const messages = ref([])
const page = ref(0)
const size = 20
const totalPages = ref(0)
const totalElements = ref(0)
const loading = ref(false)
const loadingMore = ref(false)
const hasMore = ref(true)
let unsubscribe = null

const activeTab = ref('all')

// 过滤器（仅预警 tab 用）
const symbolFilter = ref('')        // 文本输入
const timeRange = ref('all')         // '7d' / '30d' / '90d' / 'all'

const tabs = [
  { key: 'all', label: '全部' },
  { key: 'alert', label: '预警' },
  { key: 'chat', label: '聊天' }
]

const timeRanges = [
  { key: '7d', label: '最近 7 天', days: 7 },
  { key: '30d', label: '最近 30 天', days: 30 },
  { key: '90d', label: '最近 90 天', days: 90 },
  { key: 'all', label: '全部时间', days: null }
]

// 类型 → 中文标签 + 图标
const typeMeta = (type) => {
  switch (type) {
    case 'ALERT': return { icon: '🔔', label: '预警提醒' }
    case 'CHAT_BOT': return { icon: '🤖', label: '智能助手' }
    case 'CHAT_USER': return { icon: '💬', label: '我' }
    default: return { icon: '📌', label: '系统通知' }
  }
}

const CONDITION_LABELS = {
  price_above: '📈 价格突破',
  price_below: '📉 价格跌破',
  pnl_percent: '💰 盈亏百分比',
  rsi_overbought: '🔥 RSI 超买',
  rsi_oversold: '❄️ RSI 超卖',
  macd_golden_cross: '✨ MACD 金叉',
  macd_death_cross: '💀 MACD 死叉'
}

const parseMetadata = (m) => {
  if (m.type !== 'ALERT' || !m.metadata) return null
  try {
    const meta = JSON.parse(m.metadata)
    return {
      conditionType: meta.conditionType || '',
      conditionLabel: CONDITION_LABELS[meta.conditionType] || meta.conditionType || '未知',
      triggerPrice: typeof meta.triggerPrice === 'number' ? meta.triggerPrice : null,
      triggerValue: typeof meta.triggerValue === 'number' ? meta.triggerValue : null,
      threshold: typeof meta.threshold === 'number' ? meta.threshold : null
    }
  } catch {
    return null
  }
}

const fmt = (n) => (n == null ? '-' : Number(n).toFixed(2))

const formatTime = (iso) => {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  return sameDay ? hm : `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`
}

// 客户端 tab 过滤（type）
const filtered = computed(() => {
  if (activeTab.value === 'alert') {
    return messages.value.filter((m) => m.type === 'ALERT')
  }
  if (activeTab.value === 'chat') {
    return messages.value.filter((m) => m.type === 'CHAT_BOT' || m.type === 'CHAT_USER')
  }
  return messages.value
})

// 客户端 symbol 过滤（仅预警 tab）
const symbolFiltered = computed(() => {
  if (activeTab.value !== 'alert') return filtered.value
  const q = symbolFilter.value.trim().toUpperCase()
  if (!q) return filtered.value
  return filtered.value.filter((m) => (m.relatedSymbol || '').toUpperCase().includes(q))
})

// 从已加载消息里提取出现过的 symbol 列表（用于 symbol 自动补全）
const knownSymbols = computed(() => {
  const set = new Set()
  messages.value.forEach((m) => {
    if (m.relatedSymbol) set.add(m.relatedSymbol)
  })
  return Array.from(set).sort()
})

// 切换 tab 时，根据 tab 类型决定要不要传 type 给后端
const load = async () => {
  loading.value = true
  page.value = 0
  messages.value = []
  hasMore.value = true
  await loadPage()
  loading.value = false
}

const loadPage = async () => {
  if (loadingMore.value) return
  loadingMore.value = true
  try {
    const params = { page: page.value, size }
    if (activeTab.value === 'alert') params.type = 'ALERT'
    if (activeTab.value === 'chat') params.type = 'CHAT_USER,CHAT_BOT'  // 暂不支持 IN
    // 简化为：tab=chat 传 null 让后端全量返回，客户端过滤
    if (activeTab.value === 'chat') delete params.type

    // 时间范围
    const range = timeRanges.find((r) => r.key === timeRange.value)
    if (range && range.days) {
      const since = new Date(Date.now() - range.days * 24 * 60 * 60 * 1000)
      params.since = since.toISOString().slice(0, 19)  // YYYY-MM-DDTHH:mm:ss
    }

    const res = await getMessages(params)
    const data = res.data || {}
    const list = data.content || []
    messages.value = page.value === 0 ? list : [...messages.value, ...list]
    totalPages.value = data.totalPages || 0
    totalElements.value = data.totalElements || 0
    hasMore.value = page.value < (data.totalPages || 0) - 1
  } catch (e) {
    // 静默失败
  } finally {
    loadingMore.value = false
  }
}

const loadMore = async () => {
  if (!hasMore.value || loadingMore.value) return
  page.value += 1
  await loadPage()
}

const onBusMessage = (msg) => {
  if (!msg || !msg.id) return
  const idx = messages.value.findIndex((m) => m.id === msg.id)
  if (idx >= 0) {
    messages.value[idx] = msg
  } else {
    messages.value.unshift(msg)
    totalElements.value += 1
  }
}

const handleRead = async (m) => {
  if (m.read) return
  m.read = true
  messageBus.unread = Math.max(0, messageBus.unread - 1)
  try {
    await markRead(m.id)
  } catch (e) {
    m.read = false
    messageBus.unread += 1
  }
}

const handleReadAll = async () => {
  const hadUnread = messages.value.some((m) => !m.read)
  if (!hadUnread) return
  messages.value.forEach((m) => { m.read = true })
  messageBus.unread = 0
  try {
    await markAllRead()
  } catch (e) {}
}

// 切 tab / 切过滤器时重载
const onTabChange = () => load()
const onTimeRangeChange = () => load()
const onSymbolFilterChange = () => {
  // 仅客户端过滤，不重新请求
}

// 跳 K 线图
const viewChart = (symbol) => {
  if (!symbol) return
  router.push(`/chart/${encodeURIComponent(symbol.toUpperCase())}`)
}

const goChat = () => router.push('/assistant')

// 滚动到底自动加载
const mainRef = ref(null)
const onScroll = async (e) => {
  const el = e.target
  if (!el) return
  if (el.scrollHeight - el.scrollTop - el.clientHeight < 100) {
    await loadMore()
  }
}

onMounted(() => {
  load()
  messageBus.connect()
  unsubscribe = messageBus.subscribe(onBusMessage)
})

onUnmounted(() => {
  if (unsubscribe) unsubscribe()
})
</script>

<template>
  <div class="messages-page">
    <header class="page-header">
      <button class="back-btn" @click="router.push('/dashboard')">← 返回</button>
      <h1>消息中心</h1>
      <button class="read-all-btn" @click="handleReadAll">全部已读</button>
    </header>

    <div class="tabs">
      <button
        v-for="t in tabs"
        :key="t.key"
        class="tab"
        :class="{ active: activeTab === t.key }"
        @click="activeTab = t.key; onTabChange()"
      >
        {{ t.label }}
      </button>
    </div>

    <!-- 预警 tab：symbol 过滤 + 时间范围 -->
    <div v-if="activeTab === 'alert'" class="filter-bar">
      <div class="filter-row">
        <input
          v-model="symbolFilter"
          @input="onSymbolFilterChange"
          placeholder="按股票代码过滤（如 AAPL）"
          list="known-symbols"
          class="symbol-input"
        />
        <datalist id="known-symbols">
          <option v-for="s in knownSymbols" :key="s" :value="s" />
        </datalist>
      </div>
      <div class="filter-row time-row">
        <button
          v-for="r in timeRanges"
          :key="r.key"
          class="time-btn"
          :class="{ active: timeRange === r.key }"
          @click="timeRange = r.key; onTimeRangeChange()"
        >
          {{ r.label }}
        </button>
      </div>
    </div>

    <main ref="mainRef" @scroll="onScroll">
      <div v-if="loading" class="empty">加载中...</div>
      <div v-else-if="symbolFiltered.length === 0" class="empty">
        暂无消息<span v-if="symbolFilter">（已过滤）</span>
      </div>

      <div
        v-for="m in symbolFiltered"
        :key="m.id"
        class="msg-card"
        :class="{ unread: !m.read }"
        @click="handleRead(m)"
      >
        <div class="msg-icon">{{ typeMeta(m.type).icon }}</div>
        <div class="msg-main">
          <div class="msg-top">
            <span class="msg-label">{{ typeMeta(m.type).label }}</span>
            <span class="msg-time">{{ formatTime(m.createdAt) }}</span>
          </div>
          <div class="msg-content">{{ m.content }}</div>

          <!-- 预警：symbol 标签 + K 线跳转 -->
          <div v-if="m.type === 'ALERT' && m.relatedSymbol" class="msg-symbol-row">
            <span class="msg-symbol">#{{ m.relatedSymbolName || m.relatedSymbol }}</span>
            <button class="chart-link" @click.stop="viewChart(m.relatedSymbol)">
              📊 看 K 线
            </button>
          </div>

          <!-- 聊天：symbol 标签（无跳转） -->
          <div v-else-if="m.relatedSymbol" class="msg-symbol">#{{ m.relatedSymbolName || m.relatedSymbol }}</div>

          <!-- ALERT metadata 详情 -->
          <div v-if="m.type === 'ALERT' && parseMetadata(m)" class="msg-alert-detail">
            <span class="alert-tag">{{ parseMetadata(m).conditionLabel }}</span>
            <span v-if="parseMetadata(m).triggerPrice != null" class="alert-item">
              触发价 <b>{{ fmt(parseMetadata(m).triggerPrice) }}</b>
            </span>
            <span v-if="parseMetadata(m).triggerValue != null" class="alert-item">
              指标值 <b>{{ fmt(parseMetadata(m).triggerValue) }}</b>
            </span>
            <span v-if="parseMetadata(m).threshold != null" class="alert-item">
              阈值 <b>{{ fmt(parseMetadata(m).threshold) }}</b>
            </span>
          </div>
        </div>
        <span v-if="!m.read" class="unread-dot"></span>
      </div>

      <div v-if="loadingMore" class="loading-more">加载中...</div>
      <div v-else-if="!hasMore && symbolFiltered.length > 0" class="no-more">
        共 {{ totalElements }} 条，已加载完<span v-if="symbolFilter">（已过滤显示 {{ symbolFiltered.length }}）</span>
      </div>

      <button class="chat-entry" @click="goChat">
        <span>💬 去和智能助手聊聊</span>
        <span class="arrow">›</span>
      </button>
    </main>
  </div>
</template>

<style scoped>
.messages-page { min-height: 100vh; background: #f0f2f5; display: flex; flex-direction: column; }
.page-header {
  background: #1a1a2e;
  color: white;
  padding: 14px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}
.page-header h1 { margin: 0; font-size: 18px; flex: 1; }
.back-btn, .read-all-btn {
  background: transparent;
  border: 1px solid rgba(255,255,255,0.4);
  color: white;
  padding: 6px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
}

.tabs {
  display: flex;
  background: white;
  border-bottom: 1px solid #e8e8e8;
  flex-shrink: 0;
}
.tab {
  flex: 1;
  padding: 11px 0;
  background: none;
  border: none;
  font-size: 14px;
  color: #666;
  cursor: pointer;
  border-bottom: 2px solid transparent;
}
.tab.active {
  color: #1677ff;
  border-bottom-color: #1677ff;
  font-weight: 600;
}

/* 预警过滤器 */
.filter-bar {
  background: white;
  padding: 10px 16px;
  border-bottom: 1px solid #f0f0f0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.filter-row { display: flex; gap: 8px; align-items: center; }
.symbol-input {
  flex: 1;
  padding: 7px 10px;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  font-size: 13px;
}
.symbol-input:focus { outline: none; border-color: #1677ff; }
.time-row { flex-wrap: wrap; }
.time-btn {
  padding: 4px 10px;
  background: #f5f5f5;
  border: 1px solid transparent;
  border-radius: 12px;
  font-size: 12px;
  color: #666;
  cursor: pointer;
}
.time-btn.active {
  background: #1677ff;
  color: white;
  border-color: #1677ff;
}

main {
  flex: 1;
  overflow-y: auto;
  max-width: 700px;
  width: 100%;
  margin: 0 auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  box-sizing: border-box;
}

.msg-card {
  background: white;
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  position: relative;
}
.msg-card.unread { background: #f0f7ff; }
.msg-icon { font-size: 22px; flex-shrink: 0; }
.msg-main { flex: 1; min-width: 0; }
.msg-top { display: flex; justify-content: space-between; align-items: center; }
.msg-label { font-size: 12px; color: #1677ff; font-weight: 600; }
.msg-time { font-size: 11px; color: #999; }
.msg-content {
  font-size: 14px;
  color: #333;
  margin-top: 4px;
  word-break: break-word;
  white-space: pre-wrap;
}
.msg-symbol { font-size: 11px; color: #fa8c16; margin-top: 3px; }
.msg-symbol-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 4px;
}
.chart-link {
  background: transparent;
  border: 1px solid #1677ff;
  color: #1677ff;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  cursor: pointer;
}
.chart-link:hover { background: #1677ff; color: white; }
.unread-dot {
  position: absolute;
  top: 12px;
  right: 12px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #ff4d4f;
}

.msg-alert-detail {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
  margin-top: 8px;
  font-size: 12px;
  color: #666;
  background: linear-gradient(135deg, #fff7e6 0%, #fffbe6 100%);
  border-left: 3px solid #fa8c16;
  padding: 6px 10px;
  border-radius: 4px;
}
.alert-tag {
  background: #fa8c16;
  color: white;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
}
.alert-item { color: #555; }
.alert-item b { color: #d4380d; font-weight: 600; margin-left: 2px; }

.loading-more, .no-more {
  text-align: center;
  color: #999;
  padding: 12px;
  font-size: 12px;
}

.chat-entry {
  margin-top: 6px;
  background: #1677ff;
  color: white;
  border: none;
  border-radius: 8px;
  padding: 13px 16px;
  font-size: 14px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.arrow { font-size: 18px; }
.empty { color: #999; text-align: center; padding: 48px 16px; font-size: 14px; }
</style>
