<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { getSharedStrategy } from '../api/strategy.js'

/**
 * 策略只读分享页（/share/:token）。
 *
 * 独立于 AppShell：访问者不需要登录（分享的意义就是发给没账号的人看），
 * 所以它挂在鉴权布局之外（router 里 /share/:token 与 /login 平级）。
 * 页面只读后端脱敏过的公开视图：策略名/标的/决策方式/模拟盘表现 ——
 * 没有用户名，没有编辑入口，也没有"登录去创建自己的"以外的任何跳转。
 */
const route = useRoute()
const data = ref(null)
const error = ref('')
const loading = ref(true)

const modeLabel = (mode) => (mode === 'agent' ? '多角色委员会决策' : '规则引擎')
const sideLabel = (side) => (String(side || '').toLowerCase() === 'buy' ? '买入' : '卖出')

const formatMoney = (v) => {
  const n = Number(v)
  if (v == null || Number.isNaN(n)) return '—'
  return n.toLocaleString('zh-CN', { style: 'currency', currency: 'CNY' })
}
const formatPct = (v) => {
  const n = Number(v)
  if (v == null || Number.isNaN(n)) return '—'
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

onMounted(async () => {
  try {
    const res = await getSharedStrategy(route.params.token)
    data.value = res.data || null
  } catch (e) {
    error.value = e?.response?.data?.message || '链接不存在或已失效'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="share-page">
    <header class="share-header">
      <span class="brand">Stock Tracker</span>
      <span class="tag">只读分享</span>
    </header>

    <main class="share-main">
      <p v-if="loading" class="status">加载中…</p>

      <div v-else-if="error" class="card status-card">
        <p class="err">{{ error }}</p>
        <p class="sub">链接可能已被主人撤回，或者抄漏了几位。</p>
      </div>

      <template v-else-if="data">
        <section class="card">
          <div class="head">
            <h1>{{ data.name }}</h1>
            <span class="symbol num">{{ data.symbol }}</span>
            <span class="badge">{{ modeLabel(data.decisionMode) }}</span>
          </div>
          <p class="meta">创建于 {{ data.createdAt }} · 模拟盘{{ data.paperEnabled ? '运行中' : '未运行' }}</p>
        </section>

        <section v-if="data.paper" class="card">
          <h2>模拟盘表现</h2>
          <div class="grid num">
            <div><span>初始资金</span><strong>{{ formatMoney(data.paper.initialCapital) }}</strong></div>
            <div><span>当前净值</span><strong>{{ formatMoney(data.paper.equity) }}</strong></div>
            <div v-if="data.paper.summary">
              <span>区间收益</span>
              <strong>{{ formatPct(data.paper.summary.returnPct) }}</strong>
            </div>
            <div v-if="data.paper.summary">
              <span>最大回撤</span>
              <strong>{{ formatPct(data.paper.summary.maxDrawdownPct) }}</strong>
            </div>
          </div>
          <p v-if="data.paper.summary?.firstDate && data.paper.summary?.lastDate" class="period">
            {{ data.paper.summary.firstDate }} ~ {{ data.paper.summary.lastDate }} · 共 {{ data.paper.summary.days }} 个交易日 · {{ data.paper.tradeCount }} 笔成交
          </p>
          <p v-else class="period">还没有结算记录（刚开跑，等下一个交易日 15:30）。</p>

          <details v-if="(data.paper.recentTrades || []).length" class="trades">
            <summary>最近 {{ data.paper.recentTrades.length }} 笔成交</summary>
            <table>
              <thead><tr><th>日期</th><th>方向</th><th>价格</th><th>股数</th></tr></thead>
              <tbody>
                <tr v-for="(t, i) in data.paper.recentTrades" :key="i">
                  <td class="num">{{ t.date }}</td>
                  <td :class="String(t.side).toLowerCase()">{{ sideLabel(t.side) }}</td>
                  <td class="num">{{ t.price }}</td>
                  <td class="num">{{ t.shares }}</td>
                </tr>
              </tbody>
            </table>
          </details>
        </section>

        <p class="footer-note">数据来自 Stock Tracker 模拟盘，仅供交流，不构成投资建议。</p>
      </template>
    </main>
  </div>
</template>

<style scoped>
.share-page {
  min-height: 100dvh;
  background: var(--color-bg-page);
  padding: 0 0 48px;
}
.share-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 20px;
  background: var(--color-bg-surface);
  border-bottom: 1px solid var(--color-border);
}
.brand { font-weight: 700; color: var(--color-text-primary); }
.tag { font-size: 12px; padding: 2px 10px; border-radius: var(--radius-pill); background: var(--color-accent-soft); color: var(--color-accent); }
.share-main { max-width: 720px; margin: 0 auto; padding: 20px 16px; }
.status { text-align: center; color: var(--color-text-muted); padding: 40px 0; }
.card { background: var(--color-bg-surface); border-radius: var(--radius-lg); padding: 20px; margin-bottom: 14px; box-shadow: var(--shadow-1); }
.status-card { text-align: center; }
.err { margin: 0 0 6px; font-size: 15px; color: var(--color-text-primary); }
.sub { margin: 0; font-size: 13px; color: var(--color-text-muted); }
.head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.head h1 { margin: 0; font-size: 20px; }
.symbol { font-size: 13px; color: var(--color-text-muted); }
.badge { font-size: 12px; padding: 2px 10px; border-radius: var(--radius-pill); border: 1px solid var(--color-border-strong); color: var(--color-text-secondary); }
.meta { margin: 8px 0 0; font-size: 12.5px; color: var(--color-text-muted); }
.card h2 { margin: 0 0 12px; font-size: 15px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
.grid > div { display: flex; flex-direction: column; gap: 4px; }
.grid span { font-size: 12px; color: var(--color-text-secondary); }
.grid strong { font-size: 16px; }
.period { margin: 12px 0 0; font-size: 12.5px; color: var(--color-text-muted); }
.trades { margin-top: 12px; overflow-x: auto; }
.trades summary { cursor: pointer; font-size: 13px; color: var(--color-text-secondary); }
.trades table { width: 100%; min-width: 400px; border-collapse: collapse; margin-top: 8px; font-size: 13px; }
.trades th, .trades td { text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--color-border); }
.trades th { color: var(--color-text-secondary); font-weight: 500; }
td.buy { color: var(--color-gain); }
td.sell { color: var(--color-loss); }
.footer-note { text-align: center; font-size: 12px; color: var(--color-text-muted); margin-top: 8px; }
</style>
