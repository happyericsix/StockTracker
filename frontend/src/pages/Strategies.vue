<script setup>
/**
 * 策略库。逻辑自旧版原样移植（零漂移）：模拟盘状态一次取回、
 * 删除就地二次确认（焦点管理 + 双击防误删）、回测结果原文展示。
 */
import { ref, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import {
  listStrategies, runBacktest, startPaper, stopPaper, deleteStrategy, getPaperOverview,
} from '../api/strategy.js'
import Badge from '../components/ui/badge.vue'
import Button from '../components/ui/button.vue'
import Empty from '../components/ui/empty.vue'
import Skeleton from '../components/ui/skeleton.vue'
import BlockCard from '../components/ui/block-card.vue'
import { BookOpen } from 'lucide-vue-next'

const router = useRouter()
const strategies = ref([])
const loading = ref(false)
const error = ref('')
const backtestStatus = ref('')
const backtestOutput = ref('')
const backtestLoadingId = ref(null)
const paperLoadingId = ref(null)
/** 模拟盘状态：strategyId → 总览条目 */
const paperInfo = ref({})
const pendingDeleteId = ref(null)
const confirmButton = ref(null)

// 只认后端返回的中文业务提示，其次是本地 fallback（不回落 axios 英文原文）
const errorMessage = (e, fallback) => e?.friendlyMessage || e?.response?.data?.message || fallback

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
    const data = res.data
    strategies.value = Array.isArray(data) ? data : []
  } catch (e) {
    error.value = errorMessage(e, '策略列表加载失败，请检查网络后重试')
    strategies.value = []
  } finally {
    loading.value = false
  }
  await loadPaperInfo()
}

/** 模拟盘状态（跨策略一次取回）；读失败不挡列表，降级为不显示 */
const loadPaperInfo = async () => {
  try {
    const res = await getPaperOverview()
    const list = Array.isArray(res.data) ? res.data : []
    const map = {}
    for (const item of list) {
      map[item.strategyId] = item
    }
    paperInfo.value = map
  } catch {
    paperInfo.value = {}
  }
}

/** 列表里的一句话总览：还没评估过就直说，不摆 N/A */
const paperSummary = (item) => {
  const info = paperInfo.value[item.id]
  if (!info) return ''
  if (!info.paperEnabled) return ''
  if (!info.evaluated) {
    return info.decisionMode === 'agent'
      ? '还没被评估过：委员会只在日线结算（交易日 15:30）决策'
      : '还没被评估过：等下一次评估'
  }
  const parts = [`净值 ${formatMoney(info.equity)}`]
  if (info.summary?.returnPct !== null && info.summary?.returnPct !== undefined) {
    parts.push(`期间 ${formatPct(info.summary.returnPct)}`)
  }
  if (info.summary?.excessVsBuyAndHoldPct !== null && info.summary?.excessVsBuyAndHoldPct !== undefined) {
    parts.push(`超额 ${formatPct(info.summary.excessVsBuyAndHoldPct)}`)
  }
  return parts.join(' · ')
}

const modeLabel = (mode) => (mode === 'agent' ? '多角色委员会' : '规则引擎')

const formatMoney = (value) => {
  if (value === null || value === undefined) return 'N/A'
  const n = Number(value)
  if (Number.isNaN(n)) return 'N/A'
  return n.toLocaleString('zh-CN', { style: 'currency', currency: 'CNY' })
}

const formatPct = (value) => {
  if (value === null || value === undefined) return 'N/A'
  const n = Number(value)
  if (Number.isNaN(n)) return 'N/A'
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

const goDetail = (id) => router.push(`/strategies/${id}`)

const handleBacktest = async (item) => {
  backtestStatus.value = ''
  backtestOutput.value = ''
  backtestLoadingId.value = item.id
  try {
    const res = await runBacktest(item.id)
    const data = res.data
    backtestOutput.value = JSON.stringify(data, null, 2)
    backtestStatus.value = `回测完成：${item.name}`
  } catch (e) {
    backtestStatus.value = `回测失败：${item.name}`
    backtestOutput.value = errorMessage(e, '回测失败，请稍后重试')
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

// 删除就地二次确认：v-for 里的函数 ref（忽略 null 回调，避免覆盖新引用）
const setConfirmButton = (el) => {
  if (el) confirmButton.value = el
}

const askRemove = async (item) => {
  pendingDeleteId.value = item.id
  // 确认行渲染完就把焦点交给「确认删除」，键盘用户不丢位置
  await nextTick()
  confirmButton.value?.focus()
}

const cancelRemove = () => {
  pendingDeleteId.value = null
}

const confirmRemove = (item, event) => {
  // 鼠标连击的第二次点击（detail > 1）不算确认，避免习惯性双击删掉策略
  if (event && event.detail > 1) return
  remove(item)
}

const remove = async (item) => {
  pendingDeleteId.value = null
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
  <div>
    <div class="mb-5">
      <h1 class="text-[20px] font-semibold text-ink">策略库</h1>
    </div>

    <p v-if="error" class="mb-3 text-[13px] text-up" role="alert">{{ error }}</p>

    <!-- 状态互斥：加载中 → 出错 → 空 → 有数据 -->
    <div v-if="loading" class="space-y-3">
      <div v-for="i in 3" :key="i" class="space-y-2.5 rounded-xl border border-border bg-surface p-4">
        <Skeleton width="38%" height="17px" />
        <Skeleton width="55%" height="13px" />
        <Skeleton width="70%" height="13px" />
      </div>
    </div>

    <Empty v-else-if="!error && strategies.length === 0" title="暂无策略" desc="模拟盘和回测都以策略为单位，先在详情页创建一条规则开始。" size="lg">
      <template #icon><BookOpen :size="22" :stroke-width="1.5" /></template>
    </Empty>

    <!-- 区块：策略卡网格（2026-10-15 区块化：单列 → 自适应网格，与模拟盘页同节奏） -->
    <BlockCard v-else-if="strategies.length > 0" title="我的策略" collapsible storage-key="strat-list" body-class="p-4 pt-3">
      <template #actions>
        <span class="num text-[12px] text-ink-3">{{ strategies.length }} 条</span>
      </template>
      <ul class="grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(300px,1fr))]">
        <li
          v-for="item in strategies"
          :key="item.id"
          class="flex flex-col rounded-xl border border-border bg-surface-2 p-4 transition-colors hover:border-ink-3"
        >
        <!-- 进入详情 = 原生 button（键盘可达），按钮内部只用行内元素 -->
        <button type="button" class="block flex-grow text-start" @click="goDetail(item.id)">
          <span class="flex flex-wrap items-center gap-2">
            <strong class="text-[15.5px] font-semibold text-ink">{{ item.name }}</strong>
            <span class="num text-[12px] font-normal text-ink-3">{{ item.symbol }}</span>
          </span>
          <span class="mt-1.5 flex flex-wrap gap-1.5">
            <Badge :tone="item.paperEnabled ? 'down' : 'neutral'">
              {{ item.paperEnabled ? '模拟盘中' : '未启动' }}
            </Badge>
            <!-- 决策来源：两条路的结论含义不同，列表里就要能区分 -->
            <Badge v-if="paperInfo[item.id]" :tone="paperInfo[item.id].decisionMode === 'agent' ? 'accent' : 'neutral'">
              {{ modeLabel(paperInfo[item.id].decisionMode) }}
            </Badge>
          </span>
          <span class="num mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[12px] text-ink-3">
            <span>更新: {{ formatTime(item.updatedAt) }}</span>
            <span v-if="item.lastBacktestAt">回测: {{ formatTime(item.lastBacktestAt) }}</span>
          </span>
          <!-- 在跑什么、赚没赚：不点进详情也要看得见 -->
          <span v-if="paperSummary(item)" class="num mt-1.5 block text-[12px] leading-relaxed text-ink-2">{{ paperSummary(item) }}</span>
          <span v-if="paperInfo[item.id]?.lastSettlement" class="num mt-0.5 block line-clamp-2 text-[12px] leading-relaxed text-ink-2">
            最近结算（{{ paperInfo[item.id].lastSettlement.tradeDate }}）：{{ paperInfo[item.id].lastSettlement.sentence }}
          </span>
        </button>

        <div class="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
          <Button size="sm" variant="primary" @click="goDetail(item.id)">详情</Button>
          <Button size="sm" :loading="backtestLoadingId === item.id" @click="handleBacktest(item)">回测</Button>
          <Button size="sm" :loading="paperLoadingId === item.id" @click="togglePaper(item)">
            {{ item.paperEnabled ? '停止' : '启动' }}
          </Button>

          <!-- 删除的二次确认：就地展开，不弹窗 -->
          <template v-if="pendingDeleteId === item.id">
            <span class="text-[12.5px] text-ink-2">删除后无法恢复，确认？</span>
            <Button :ref="setConfirmButton" size="sm" variant="danger" @click="confirmRemove(item, $event)">确认删除</Button>
            <Button size="sm" @click="cancelRemove">取消</Button>
          </template>
          <Button v-else size="sm" variant="danger" @click="askRemove(item)">删除</Button>
        </div>
        </li>
      </ul>
    </BlockCard>

    <!-- 回测结果：墨色终端面板 -->
    <section v-if="backtestStatus || backtestOutput" class="mt-5 rounded-xl bg-sidebar p-4">
      <h2 class="mb-2.5 text-[14px] font-semibold text-white">{{ backtestStatus || '回测结果' }}</h2>
      <pre class="num m-0 max-h-[320px] overflow-auto whitespace-pre-wrap break-words text-[12px] leading-relaxed text-sidebar-dim">{{ backtestOutput }}</pre>
    </section>
  </div>
</template>
