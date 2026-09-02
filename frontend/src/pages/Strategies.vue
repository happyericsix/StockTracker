
<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  listStrategies,
  runBacktest,
  startPaper,
  stopPaper,
  deleteStrategy
} from '../api/strategy.js'

const router = useRouter()
const strategies = ref([])
const loading = ref(false)
const error = ref('')
const backtestStatus = ref('')
const backtestOutput = ref('')
const backtestLoadingId = ref(null)
const paperLoadingId = ref(null)

const unwrap = (res) => {
  const payload = res?.data
  if (payload && typeof payload === 'object' && 'data' in payload) return payload.data
  return payload
}

const errorMessage = (e, fallback) =>
  e?.response?.data?.message || e?.message || fallback

const formatTime = (iso) => {
  if (!iso) return 'N/A'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return 'N/A'
  return d.toLocaleString('zh-CN', { hour12: false })
}

const loadStrategies = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await listStrategies()
    const data = unwrap(res)
    strategies.value = Array.isArray(data) ? data : []
  } catch (e) {
    error.value = errorMessage(e, '策略列表加载失败')
    strategies.value = []
  } finally {
    loading.value = false
  }
}

const goDetail = (id) => router.push(`/strategies/${id}`)
const goDashboard = () => router.push('/dashboard')

const handleBacktest = async (item) => {
  backtestStatus.value = ''
  backtestOutput.value = ''
  backtestLoadingId.value = item.id
  try {
    const res = await runBacktest(item.id)
    const data = unwrap(res)
    backtestOutput.value = JSON.stringify(data, null, 2)
    backtestStatus.value = `回测完成：${item.name}`
  } catch (e) {
    backtestStatus.value = `回测失败：${item.name}`
    backtestOutput.value = errorMessage(e, '请稍后重试')
  } finally {
    backtestLoadingId.value = null
  }
}

const togglePaper = async (item) => {
  paperLoadingId.value = item.id
  error.value = ''
  try {
    if (item.paperEnabled) {
      await stopPaper(item.id)
    } else {
      await startPaper(item.id)
    }
    await loadStrategies()
  } catch (e) {
    error.value = errorMessage(e, '模拟盘操作失败，请稍后重试')
  } finally {
    paperLoadingId.value = null
  }
}

const remove = async (item) => {
  if (!window.confirm(`确认删除策略「${item.name}」？`)) return
  error.value = ''
  try {
    await deleteStrategy(item.id)
    await loadStrategies()
  } catch (e) {
    error.value = errorMessage(e, '删除失败，请稍后重试')
  }
}

onMounted(loadStrategies)
</script>

<template>
  <div class="app-layout">
    <header>
      <h1>策略库</h1>
      <div class="header-actions">
        <button class="nav-btn" @click="goDashboard">← 返回</button>
      </div>
    </header>

    <main>
      <p v-if="error" class="error">{{ error }}</p>

      <div v-if="loading" class="empty">加载中...</div>
      <div v-else-if="strategies.length === 0" class="empty">暂无策略</div>

      <section v-else class="strategy-list">
        <div v-for="item in strategies" :key="item.id" class="strategy-card">
          <div class="strategy-info" @click="goDetail(item.id)">
            <div class="strategy-title">
              <strong>{{ item.name }}</strong>
              <span class="symbol-code">{{ item.symbol }}</span>
              <span class="paper-badge" :class="{ enabled: item.paperEnabled }">
                {{ item.paperEnabled ? '模拟盘中' : '未启动' }}
              </span>
            </div>
            <div class="strategy-meta">
              <span>更新: {{ formatTime(item.updatedAt) }}</span>
              <span v-if="item.lastBacktestAt">最近回测: {{ formatTime(item.lastBacktestAt) }}</span>
            </div>
          </div>

          <div class="strategy-actions">
            <button class="btn-detail" @click="goDetail(item.id)">详情</button>
            <button
              class="btn-backtest"
              :disabled="backtestLoadingId === item.id"
              @click="handleBacktest(item)"
            >
              {{ backtestLoadingId === item.id ? '回测中...' : '回测' }}
            </button>
            <button
              class="btn-paper"
              :class="{ running: item.paperEnabled }"
              :disabled="paperLoadingId === item.id"
              @click="togglePaper(item)"
            >
              {{ item.paperEnabled ? '停止模拟盘' : '启动模拟盘' }}
            </button>
            <button class="btn-delete" @click="remove(item)">删除</button>
          </div>
        </div>
      </section>

      <section v-if="backtestStatus || backtestOutput" class="backtest-result">
        <h2>{{ backtestStatus || '回测结果' }}</h2>
        <pre>{{ backtestOutput }}</pre>
      </section>
    </main>
  </div>
</template>

<style scoped>
.app-layout { min-height: 100vh; background: #f0f2f5; }
header {
  background: #1a1a2e;
  color: white;
  padding: 16px 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
header h1 { margin: 0; font-size: 20px; }
.header-actions { display: flex; gap: 8px; }
.nav-btn {
  background: rgba(255,255,255,0.15);
  border: none;
  color: white;
  padding: 6px 14px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
}
.nav-btn:hover { background: rgba(255,255,255,0.25); }

main { max-width: 820px; margin: 0 auto; padding: 24px 16px; }
.strategy-list { display: flex; flex-direction: column; gap: 12px; }
.strategy-card {
  background: white;
  border-radius: 8px;
  padding: 14px 18px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.08);
}
.strategy-info { cursor: pointer; }
.strategy-title { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.strategy-title strong { font-size: 16px; }
.symbol-code { font-size: 13px; color: #999; font-weight: normal; }
.paper-badge {
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 10px;
  background: #fff1f0;
  color: #ff4d4f;
  border: 1px solid #ffa39e;
}
.paper-badge.enabled {
  background: #f6ffed;
  color: #52c41a;
  border-color: #b7eb8f;
}
.strategy-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 6px;
  color: #888;
  font-size: 12px;
}
.strategy-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #f0f0f0;
}
.strategy-actions button {
  padding: 5px 12px;
  border: none;
  border-radius: 4px;
  font-size: 13px;
  cursor: pointer;
}
.strategy-actions button:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-detail { background: #1677ff; color: white; }
.btn-backtest { background: #722ed1; color: white; }
.btn-paper { background: #fa8c16; color: white; }
.btn-paper.running { background: #52c41a; }
.btn-delete { background: #ff4d4f; color: white; }

.empty { color: #999; text-align: center; padding: 48px 16px; font-size: 14px; }
.error { color: #ff4d4f; font-size: 13px; margin-bottom: 12px; }

.backtest-result {
  margin-top: 20px;
  background: #1f1f2e;
  border-radius: 8px;
  padding: 16px 18px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.1);
}
.backtest-result h2 { margin: 0 0 10px; font-size: 15px; color: #d9d9e8; }
.backtest-result pre {
  margin: 0;
  color: #e6e6f0;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow: auto;
}
</style>
