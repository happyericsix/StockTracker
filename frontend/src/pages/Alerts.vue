<script setup>
/**
 * 预警设置。逻辑自旧版原样移植：表单校验与后端 @Valid 同规、错误分类
 * （businessCode 优先于 HTTP 状态）、5xx/断网本地兜底（_localOnly 标记）、
 * 乐观切换/删除失败回滚、Dashboard 深链预填。
 */
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { Plus, Bell } from 'lucide-vue-next'
import { getAlerts, addAlert, updateAlert, deleteAlert } from '../api/alerts.js'
import Badge from '../components/ui/badge.vue'
import Button from '../components/ui/button.vue'
import Empty from '../components/ui/empty.vue'
import Skeleton from '../components/ui/skeleton.vue'

const router = useRouter()
const route = useRoute()

const alerts = ref([])
const loading = ref(false)
const error = ref('')      // 表单校验错误（表单卡内部）
const opError = ref('')    // 操作类错误（页面级，表单关着也要能看到）
const success = ref('')
const fieldErrors = ref({})

const parseFieldErrors = (message) => {
  const out = {}
  if (!message) return out
  message.split(';').forEach((seg) => {
    const m = seg.trim().match(/^([a-zA-Z_][\w]*)\s*:\s*(.+)$/)
    if (m) out[m[1]] = m[2].trim()
  })
  return out
}

// 4xx=用户错误回滚/改表单；5xx/网络=可走本地兜底。
// status 优先取 businessCode：Result.error 是带 HTTP 200 返回的
const classifyError = (e) => {
  const status = e.businessCode ?? e.response?.status
  const rawMsg = e.response?.data?.message
  if (status >= 400 && status < 500) {
    return { retryable: false, status, message: rawMsg || `请求被拒绝（${status}），请检查填写内容后重试`, fieldErrors: parseFieldErrors(rawMsg) }
  }
  return { retryable: true, status, message: rawMsg || '后端暂不可用', fieldErrors: {} }
}

const conditionTypes = [
  { value: 'rsi_overbought', label: 'RSI 超买', desc: 'RSI > 阈值时触发' },
  { value: 'rsi_oversold', label: 'RSI 超卖', desc: 'RSI < 阈值时触发' },
  { value: 'macd_golden_cross', label: 'MACD 金叉', desc: 'DIF 上穿 DEA' },
  { value: 'macd_death_cross', label: 'MACD 死叉', desc: 'DIF 下穿 DEA' },
  { value: 'price_above', label: '价格突破（向上）', desc: '价格 > 阈值时触发' },
  { value: 'price_below', label: '价格跌破（向下）', desc: '价格 < 阈值时触发' },
  { value: 'pnl_profit', label: '止盈%', desc: '盈利达到设定%时触发' },
  { value: 'pnl_loss', label: '止损%', desc: '亏损达到设定%时触发' },
  { value: 'trailing_take_profit', label: '跟踪止盈(回撤%)', desc: '从跟踪期最高价回撤设定%时触发，创新高自动重开新一轮' },
]

const showForm = ref(false)
const editingId = ref(null)
const form = ref({
  symbol: '', conditionType: 'price_above', threshold: '',
  cooldownMinutes: '', resetRatio: '', reArmHours: '',
})

const resetForm = () => {
  form.value = { symbol: '', conditionType: 'price_above', threshold: '', cooldownMinutes: '', resetRatio: '', reArmHours: '' }
  editingId.value = null
  showForm.value = false
  error.value = ''
  fieldErrors.value = {}
}

// 表单校验 — 与后端 AlertRequest 的 @Valid 同规
const validateForm = () => {
  const errs = {}
  const symbol = (form.value.symbol || '').trim()
  if (!symbol) errs.symbol = '股票代码不能为空'
  else if (!/^[A-Za-z0-9.-]{1,10}$/.test(symbol)) errs.symbol = '股票代码格式不正确(仅允许字母数字.-,最长 10 位)'

  if (!form.value.conditionType) errs.conditionType = '请选择条件类型'

  const tRaw = form.value.threshold
  if (tRaw === '' || tRaw == null) {
    errs.threshold = '请输入阈值'
  } else {
    const t = parseFloat(tRaw)
    if (Number.isNaN(t)) {
      errs.threshold = '阈值必须是数字'
    } else {
      const ct = form.value.conditionType
      if ((ct === 'price_above' || ct === 'price_below') && t <= 0) errs.threshold = '价格阈值必须大于 0'
      if ((ct === 'rsi_overbought' || ct === 'rsi_oversold' || ct === 'trailing_take_profit') && (t <= 0 || t >= 100)) {
        errs.threshold = 'RSI/回撤阈值需在 (0, 100) 之间'
      }
    }
  }

  if (form.value.cooldownMinutes !== '' && form.value.cooldownMinutes != null) {
    const c = parseFloat(form.value.cooldownMinutes)
    if (Number.isNaN(c) || c < 0) errs.cooldownMinutes = '冷却分钟数需为 ≥ 0 的数字'
    else if (c > 1440) errs.cooldownMinutes = '冷却分钟数不能超过 1440（24 小时）'
  }
  if (form.value.resetRatio !== '' && form.value.resetRatio != null) {
    const r = parseFloat(form.value.resetRatio)
    if (Number.isNaN(r) || r <= 0 || r > 1) errs.resetRatio = '重置比率需在 (0, 1] 之间'
  }
  if (form.value.reArmHours !== '' && form.value.reArmHours != null) {
    const h = parseFloat(form.value.reArmHours)
    if (Number.isNaN(h) || h < 1) errs.reArmHours = '重置小时数需为 ≥ 1 的数字'
    else if (h > 720) errs.reArmHours = '重置小时数不能超过 720（30 天）'
  }
  return errs
}

const openAdd = () => { resetForm(); showForm.value = true }

const openEdit = (item) => {
  const threshold = item.threshold ?? 0
  // 后端统一存 pnl_percent，按正负号还原前端下拉选项
  let conditionType = item.conditionType
  let displayThreshold = threshold
  if (item.conditionType === 'pnl_percent') {
    if (threshold < 0) {
      conditionType = 'pnl_loss'
      displayThreshold = Math.abs(threshold)
    } else {
      conditionType = 'pnl_profit'
    }
  }
  form.value = {
    symbol: item.symbol,
    conditionType,
    threshold: String(displayThreshold),
    // 高级参数回填：显示真实值，不显示成空占位冒充默认值
    cooldownMinutes: item.cooldownMinutes != null ? String(item.cooldownMinutes) : '',
    resetRatio: item.resetRatio != null ? String(item.resetRatio) : '',
    reArmHours: item.reArmHours != null ? String(item.reArmHours) : '',
  }
  editingId.value = item.id
  showForm.value = true
}

const submitForm = async () => {
  error.value = ''
  fieldErrors.value = {}
  const errs = validateForm()
  if (Object.keys(errs).length > 0) {
    fieldErrors.value = errs
    error.value = '表单有错误，请检查下方标红字段'
    return
  }

  const threshold = parseFloat(form.value.threshold)
  loading.value = true
  try {
    const payload = {
      symbol: form.value.symbol.trim().toUpperCase(),
      conditionType: form.value.conditionType,
      threshold,
    }
    if (form.value.cooldownMinutes !== '' && form.value.cooldownMinutes != null) payload.cooldownMinutes = parseInt(form.value.cooldownMinutes, 10)
    if (form.value.resetRatio !== '' && form.value.resetRatio != null) payload.resetRatio = parseFloat(form.value.resetRatio)
    if (form.value.reArmHours !== '' && form.value.reArmHours != null) payload.reArmHours = parseInt(form.value.reArmHours, 10)

    if (editingId.value) {
      await updateAlert(editingId.value, payload)
      success.value = '预警已更新'
    } else {
      await addAlert(payload)
      success.value = '预警已添加'
    }
    resetForm()
    await loadAlerts()
    setTimeout(() => { success.value = '' }, 2000)
  } catch (e) {
    const cls = classifyError(e)
    if (cls.retryable) {
      // 5xx / 网络：本地兜底（resetForm 会关表单，这条必须挂页面级）
      opError.value = cls.message + '，已暂存到本地'
      await saveLocalFallback()
      resetForm()
    } else {
      error.value = cls.message
      fieldErrors.value = cls.fieldErrors
    }
  } finally {
    loading.value = false
  }
}

const handleToggle = async (item) => {
  opError.value = ''
  const previous = item.enabled
  const next = !previous
  item.enabled = next // 乐观切换，失败回滚
  try {
    // PUT 是全量校验：item 里的规范值原样带回 = "不变只改开关"
    await updateAlert(item.id, {
      symbol: item.symbol,
      conditionType: item.conditionType,
      threshold: item.threshold,
      enabled: next,
    })
  } catch (e) {
    const cls = classifyError(e)
    if (cls.retryable) {
      item._localOnly = true
      saveLocal()
      opError.value = `后端暂不可用，已本地切换 ${item.name || item.symbol} 的开关状态`
    } else {
      item.enabled = previous
      opError.value = `切换失败： ${cls.message}`
    }
  }
}

const handleDelete = async (item) => {
  if (!confirm(`确认删除 ${item.name || item.symbol} 的预警？`)) return
  opError.value = ''
  const previousList = alerts.value.slice()
  alerts.value = alerts.value.filter((a) => a.id !== item.id)
  try {
    await deleteAlert(item.id)
  } catch (e) {
    const cls = classifyError(e)
    if (cls.retryable) {
      item._localOnly = true
      alerts.value.push(item)
      saveLocal()
      opError.value = `后端暂不可用，已在本地暂存删除 ${item.name || item.symbol}(未真正同步)`
    } else {
      alerts.value = previousList
      opError.value = `删除失败： ${cls.message}`
    }
  }
}

const loadAlerts = async () => {
  try {
    const res = await getAlerts()
    alerts.value = (res.data || []).map((a) => ({ ...a, enabled: a.enabled !== false }))
  } catch {
    // 静默回退本地缓存会被当成"确实没有预警"，必须说出来
    const stored = localStorage.getItem('alerts_data')
    if (stored) {
      try { alerts.value = JSON.parse(stored) } catch { alerts.value = [] }
      opError.value = '预警列表加载失败，当前显示的是本地暂存的数据，可能不是最新。'
    } else {
      alerts.value = []
      opError.value = '预警列表加载失败，请检查网络后重试。'
    }
  }
}

const saveLocal = () => {
  try {
    localStorage.setItem('alerts_data', JSON.stringify(alerts.value))
  } catch {}
}

const saveLocalFallback = async () => {
  const thresholdNum = parseFloat(form.value.threshold)
  const cooldownNum = form.value.cooldownMinutes !== '' && form.value.cooldownMinutes != null ? parseInt(form.value.cooldownMinutes, 10) : undefined
  const ratioNum = form.value.resetRatio !== '' && form.value.resetRatio != null ? parseFloat(form.value.resetRatio) : undefined
  const reArmNum = form.value.reArmHours !== '' && form.value.reArmHours != null ? parseInt(form.value.reArmHours, 10) : undefined

  if (editingId.value) {
    const idx = alerts.value.findIndex((a) => a.id === editingId.value)
    if (idx >= 0) {
      alerts.value[idx] = {
        ...alerts.value[idx], ...form.value,
        threshold: thresholdNum, cooldownMinutes: cooldownNum,
        resetRatio: ratioNum, reArmHours: reArmNum, _localOnly: true,
      }
    }
  } else {
    alerts.value.push({
      id: `local-${Date.now()}`, ...form.value,
      threshold: thresholdNum, cooldownMinutes: cooldownNum,
      resetRatio: ratioNum, reArmHours: reArmNum,
      enabled: true, _localOnly: true,
    })
  }
  saveLocal()
  // 不 loadAlerts()：此刻后端故障，重拉要么又失败、要么把本地兜底冲掉
}

const getConditionLabel = (type, threshold) => {
  if (type === 'pnl_percent') return (threshold ?? 0) < 0 ? '止损%' : '止盈%'
  return conditionTypes.find((c) => c.value === type)?.label || type
}

const getConditionDesc = (type) => conditionTypes.find((c) => c.value === type)?.desc || ''

// 启动时：从 URL query 预填（Dashboard 的"预警"按钮深链）
onMounted(async () => {
  await loadAlerts()
  const symbol = (route.query.symbol || '').toString().trim().toUpperCase()
  const wantsNew = route.query.new === '1'
  if (!wantsNew || !symbol) return
  const existing = alerts.value.filter((a) => a.symbol === symbol)
  if (existing.length > 0) {
    const ok = confirm(`${existing[0]?.name || symbol} 已有 ${existing.length} 条预警，继续新建一条吗?\n(取消则跳到列表查看已有预警)`)
    if (!ok) {
      router.replace({ path: '/alerts' })
      return
    }
  }
  resetForm()
  form.value.symbol = symbol
  showForm.value = true
  router.replace({ path: '/alerts' })
})
</script>

<template>
  <div>
    <div class="mb-5 flex items-center justify-between gap-3">
      <h1 class="text-[20px] font-semibold text-ink">预警设置</h1>
      <Button variant="primary" size="sm" @click="openAdd">
        <Plus :size="14" :stroke-width="2" aria-hidden="true" /> 添加预警
      </Button>
    </div>

    <!-- 添加/编辑表单 -->
    <div v-if="showForm" class="mb-5 rounded-xl border border-border bg-surface p-5">
      <h2 class="mb-3.5 text-[15px] font-semibold text-ink">{{ editingId ? '编辑预警' : '添加预警' }}</h2>

      <div class="flex flex-col gap-3">
        <label class="flex flex-col gap-1">
          <span class="text-[12px] text-ink-2">股票代码</span>
          <input
            v-model="form.symbol"
            aria-label="股票代码"
            placeholder="股票代码（AAPL / 600519 / 00700）"
            :disabled="!!editingId"
            class="num h-[34px] rounded-lg border bg-bg px-3 text-[13px] text-ink transition-colors placeholder:text-ink-3 focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)] disabled:opacity-60"
            :class="fieldErrors.symbol ? 'border-up bg-up-soft/50 focus:border-up' : 'border-border focus:border-accent'"
          >
          <span v-if="fieldErrors.symbol" class="text-[12px] text-up">{{ fieldErrors.symbol }}</span>
        </label>

        <label class="flex flex-col gap-1">
          <span class="text-[12px] text-ink-2">条件类型</span>
          <select
            v-model="form.conditionType"
            aria-label="条件类型"
            class="h-[34px] rounded-lg border border-border bg-bg px-2.5 text-[13px] text-ink focus:border-accent focus:outline-none"
          >
            <option v-for="ct in conditionTypes" :key="ct.value" :value="ct.value">{{ ct.label }}</option>
          </select>
          <span v-if="fieldErrors.conditionType" class="text-[12px] text-up">{{ fieldErrors.conditionType }}</span>
        </label>

        <label class="flex flex-col gap-1">
          <span class="text-[12px] text-ink-2">阈值</span>
          <input
            v-model="form.threshold"
            class="num h-[34px] rounded-lg border bg-bg px-3 text-[13px] text-ink placeholder:text-ink-3 focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
            :class="fieldErrors.threshold ? 'border-up bg-up-soft/50 focus:border-up' : 'border-border focus:border-accent'"
            aria-label="阈值"
            type="number"
            step="0.01"
            :placeholder="getConditionDesc(form.conditionType)"
          >
          <span v-if="fieldErrors.threshold" class="text-[12px] text-up">{{ fieldErrors.threshold }}</span>
        </label>
      </div>

      <details class="mt-3 border-t border-dashed border-border pt-2.5">
        <summary class="cursor-pointer select-none text-[12.5px] text-ink-3 transition-colors hover:text-accent">高级设置（v1 边沿触发参数，留空使用默认值）</summary>
        <div class="mt-3 grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(180px,1fr))]">
          <label class="flex flex-col gap-1">
            <span class="text-[12px] text-ink-2">冷却（分钟，默认 5）</span>
            <input v-model="form.cooldownMinutes" type="number" min="0" max="1440" placeholder="5" class="num h-[34px] rounded-lg border border-border bg-bg px-3 text-[13px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none">
            <span v-if="fieldErrors.cooldownMinutes" class="text-[12px] text-up">{{ fieldErrors.cooldownMinutes }}</span>
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-[12px] text-ink-2">重置比率（0~1，默认 0.5）</span>
            <input v-model="form.resetRatio" type="number" step="0.05" min="0" max="1" placeholder="0.5" class="num h-[34px] rounded-lg border border-border bg-bg px-3 text-[13px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none">
            <span v-if="fieldErrors.resetRatio" class="text-[12px] text-up">{{ fieldErrors.resetRatio }}</span>
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-[12px] text-ink-2">重置小时（默认 2）</span>
            <input v-model="form.reArmHours" type="number" min="1" max="720" placeholder="2" class="num h-[34px] rounded-lg border border-border bg-bg px-3 text-[13px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none">
            <span v-if="fieldErrors.reArmHours" class="text-[12px] text-up">{{ fieldErrors.reArmHours }}</span>
          </label>
        </div>
      </details>

      <p class="mt-2.5 text-[12px] text-ink-3">{{ getConditionDesc(form.conditionType) }}</p>

      <p v-if="error" class="mt-2.5 text-[12.5px] text-up" role="alert">{{ error }}</p>

      <div class="mt-4 flex justify-end gap-2">
        <Button variant="ghost" @click="resetForm">取消</Button>
        <Button variant="primary" :loading="loading" @click="submitForm">保存</Button>
      </div>
    </div>

    <p v-if="opError" class="mb-3 text-[13px] text-up" role="alert">{{ opError }}</p>
    <p v-if="success" class="mb-3 text-center text-[13px] text-down" role="status">{{ success }}</p>

    <!-- 预警列表 -->
    <div v-if="alerts.length === 0 && !opError" class="rounded-xl border border-border bg-surface">
      <Empty title="暂无预警" desc="右上角「添加预警」，价格、RSI、MACD、止盈止损都可以盯。">
        <template #icon><Bell :size="20" :stroke-width="1.5" /></template>
      </Empty>
    </div>

    <div class="flex flex-col gap-3">
      <div
        v-for="item in alerts"
        :key="item.id"
        class="rounded-xl border border-border p-4"
        :class="item.enabled ? 'bg-surface' : 'bg-surface-2/60'"
      >
        <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <div class="flex min-w-0 flex-col gap-0.5">
            <p class="flex flex-wrap items-center gap-2 text-[15px] font-semibold" :class="item.enabled ? 'text-ink' : 'text-ink-2'">
              {{ item.name || item.symbol }}
              <small v-if="item.name" class="num text-[12px] font-normal text-ink-3">{{ item.symbol }}</small>
              <Badge v-if="item._localOnly" tone="warn" title="仅本地暂存，后端未同步">本地</Badge>
            </p>
            <p class="text-[12.5px] text-ink-2">{{ getConditionLabel(item.conditionType, item.threshold) }}</p>
          </div>
          <div class="flex items-center gap-4">
            <div class="flex flex-col items-end gap-0.5">
              <span class="num text-[14px] font-semibold text-warn">阈值: {{ item.threshold }}</span>
              <span v-if="item.conditionType === 'trailing_take_profit' && item.highWatermark" class="num text-[12px] text-ink-3">
                最高点: {{ Number(item.highWatermark).toFixed(2) }}
              </span>
            </div>
            <label class="relative inline-block h-6 w-11 shrink-0">
              <input
                type="checkbox"
                class="peer h-0 w-0 opacity-0"
                :checked="item.enabled"
                :aria-label="`${item.name || item.symbol} 预警开关`"
                @change="handleToggle(item)"
              >
              <span class="absolute inset-0 cursor-pointer rounded-full bg-border transition-colors before:absolute before:bottom-[3px] before:left-[3px] before:h-[18px] before:w-[18px] before:rounded-full before:bg-white before:transition-transform peer-checked:bg-accent peer-checked:before:translate-x-[20px] peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-[color:var(--accent)]"></span>
            </label>
          </div>
        </div>

        <div class="mt-3 flex gap-2 border-t border-border pt-3">
          <Button size="sm" @click="openEdit(item)">编辑</Button>
          <Button size="sm" variant="danger" @click="handleDelete(item)">删除</Button>
        </div>
      </div>
    </div>
  </div>
</template>
