<script setup>
/**
 * 策略只读分享页（/share/:token）。独立于 AppShell：访问者不需要登录。
 * 只读后端脱敏过的公开视图，无用户名、无编辑入口。逻辑自旧版原样移植。
 */
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { CandlestickChart } from 'lucide-vue-next'
import { getSharedStrategy } from '../api/strategy.js'
import Badge from '../components/ui/badge.vue'
import BeianFooter from '../components/BeianFooter.vue'
import { dirTextClass } from '../utils/format.js'

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
  <div class="min-h-screen min-h-[100dvh] pb-12">
    <header class="flex items-center gap-2.5 border-b border-border bg-surface px-5 py-3.5">
      <span class="flex items-center gap-2 font-semibold text-ink">
        <span class="flex h-7 w-7 items-center justify-center rounded-lg bg-accent-soft text-accent">
          <CandlestickChart :size="16" :stroke-width="1.75" aria-hidden="true" />
        </span>
        Stock Tracker
      </span>
      <Badge tone="accent">只读分享</Badge>
    </header>

    <main class="mx-auto max-w-[720px] px-4 pt-5 sm:px-5">
      <p v-if="loading" class="py-10 text-center text-[13.5px] text-ink-2">加载中…</p>

      <div v-else-if="error" class="rounded-xl border border-border bg-surface py-10 text-center">
        <p class="text-[15px] font-semibold text-ink">{{ error }}</p>
        <p class="mt-1.5 text-[13px] text-ink-3">链接可能已被主人撤回，或者抄漏了几位。</p>
      </div>

      <template v-else-if="data">
        <section class="mb-3.5 rounded-xl border border-border bg-surface p-5">
          <div class="flex flex-wrap items-center gap-2.5">
            <h1 class="text-[20px] font-semibold text-ink">{{ data.name }}</h1>
            <span class="num text-[13px] text-ink-3">{{ data.symbol }}</span>
            <Badge :tone="data.decisionMode === 'agent' ? 'accent' : 'neutral'">{{ modeLabel(data.decisionMode) }}</Badge>
          </div>
          <p class="num mt-2 text-[12.5px] text-ink-3">
            创建于 {{ data.createdAt }} · 模拟盘{{ data.paperEnabled ? '运行中' : '未运行' }}
          </p>
        </section>

        <section v-if="data.paper" class="mb-3.5 rounded-xl border border-border bg-surface p-5">
          <h2 class="mb-3 text-[15px] font-semibold text-ink">模拟盘表现</h2>
          <div class="num grid gap-x-6 gap-y-2.5 [grid-template-columns:repeat(auto-fit,minmax(140px,1fr))]">
            <div class="flex flex-col gap-0.5"><span class="text-[12px] text-ink-2">初始资金</span><strong class="text-[16px] font-semibold text-ink">{{ formatMoney(data.paper.initialCapital) }}</strong></div>
            <div class="flex flex-col gap-0.5"><span class="text-[12px] text-ink-2">当前净值</span><strong class="text-[16px] font-semibold text-ink">{{ formatMoney(data.paper.equity) }}</strong></div>
            <div v-if="data.paper.summary" class="flex flex-col gap-0.5">
              <span class="text-[12px] text-ink-2">区间收益</span>
              <strong class="text-[16px] font-semibold" :class="dirTextClass(data.paper.summary.returnPct)">{{ formatPct(data.paper.summary.returnPct) }}</strong>
            </div>
            <div v-if="data.paper.summary" class="flex flex-col gap-0.5">
              <span class="text-[12px] text-ink-2">最大回撤</span>
              <strong class="text-[16px] font-semibold text-down">{{ formatPct(data.paper.summary.maxDrawdownPct) }}</strong>
            </div>
          </div>
          <p v-if="data.paper.summary?.firstDate && data.paper.summary?.lastDate" class="num mt-3 text-[12.5px] text-ink-3">
            {{ data.paper.summary.firstDate }} ~ {{ data.paper.summary.lastDate }} · 共 {{ data.paper.summary.days }} 个交易日 · {{ data.paper.tradeCount }} 笔成交
          </p>
          <p v-else class="mt-3 text-[12.5px] text-ink-3">还没有结算记录（刚开跑，等下一个交易日 15:30）。</p>

          <details v-if="(data.paper.recentTrades || []).length" class="mt-3 overflow-x-auto">
            <summary class="cursor-pointer text-[13px] text-ink-2">最近 {{ data.paper.recentTrades.length }} 笔成交</summary>
            <table class="num mt-2 w-full min-w-[400px] text-[13px]">
              <thead>
                <tr class="text-left text-ink-2">
                  <th class="py-1 pr-2 font-medium">日期</th><th class="pr-2 font-medium">方向</th>
                  <th class="pr-2 font-medium">价格</th><th class="font-medium">股数</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(t, i) in data.paper.recentTrades" :key="i" class="border-t border-border">
                  <td class="py-1.5 pr-2 text-ink">{{ t.date }}</td>
                  <td class="pr-2" :class="String(t.side).toLowerCase() === 'buy' ? 'text-up' : 'text-down'">{{ sideLabel(t.side) }}</td>
                  <td class="pr-2 text-ink">{{ t.price }}</td>
                  <td class="text-ink">{{ t.shares }}</td>
                </tr>
              </tbody>
            </table>
          </details>
        </section>

        <p class="mt-2 text-center text-[12px] text-ink-3">数据来自 Stock Tracker 模拟盘，仅供交流，不构成投资建议。</p>
      </template>
    </main>
    <!-- 备案合规：分享页在鉴权壳外，也要展示备案号 -->
    <BeianFooter />
  </div>
</template>
