<script setup>

import { ref, onMounted } from 'vue'

import { useRouter, useRoute } from 'vue-router'

import { getAlerts, addAlert, updateAlert, deleteAlert } from '../api/alerts.js'



const router = useRouter()
const route = useRoute()



const alerts = ref([])

const loading = ref(false)

const error = ref('')

const success = ref('')

// 字段级错误(后端 400 校验失败时填充),key 是字段名
const fieldErrors = ref({})



// ===== 错误处理工具函数 =====

// 把后端 message 解析成 { field: msg } map
// 后端 GlobalExceptionHandler 格式: "threshold: 阈值范围不合法(...); symbol: 股票代码不能为空"
const parseFieldErrors = (message) => {
  const out = {}
  if (!message) return out
  message.split(';').forEach(seg => {
    const m = seg.trim().match(/^([a-zA-Z_][\w]*)\s*:\s*(.+)$/)
    if (m) out[m[1]] = m[2].trim()
  })
  return out
}

// 分类 API 错误
// 返回: { retryable, status, message, fieldErrors }
//   - retryable: true 表示 5xx/网络错(可走本地兜底)
//   - fieldErrors: 仅 4xx 且能从 message 解析出字段错误时填充
const classifyError = (e) => {
  const status = e.response?.status
  const rawMsg = e.response?.data?.message
  if (status >= 400 && status < 500) {
    return {
      retryable: false,
      status,
      message: rawMsg || `请求被拒绝 (${status})`,
      fieldErrors: parseFieldErrors(rawMsg)
    }
  }
  return {
    retryable: true,
    status,
    message: rawMsg || '后端暂不可用',
    fieldErrors: {}
  }
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
  { value: 'trailing_take_profit', label: '跟踪止盈(回撤%)', desc: '从跟踪期最高价回撤设定%时触发，创新高自动重开新一轮' }

]



// 添加/编辑表单

const showForm = ref(false)

const editingId = ref(null)

const form = ref({
  symbol: '',
  conditionType: 'price_above',
  threshold: '',
  cooldownMinutes: '',
  resetRatio: '',
  reArmHours: ''
})



const resetForm = () => {

  form.value = {
    symbol: '',
    conditionType: 'price_above',
    threshold: '',
    cooldownMinutes: '',
    resetRatio: '',
    reArmHours: ''
  }

  editingId.value = null

  showForm.value = false

  error.value = ''
  fieldErrors.value = {}

}



// 表单校验 — 规则与后端 AlertRequest 的 @Valid 注解保持一致
// 返回字段级错误 map,空对象表示通过
// 业务规则集中在表单层,无效数据不应进入后端
const validateForm = () => {
  const errs = {}

  // 1) symbol
  const symbol = (form.value.symbol || '').trim()
  if (!symbol) errs.symbol = '股票代码不能为空'
  else if (!/^[A-Za-z0-9.-]{1,10}$/.test(symbol)) errs.symbol = '股票代码格式不正确(仅允许字母数字.-,最长 10 位)'

  // 2) conditionType
  if (!form.value.conditionType) errs.conditionType = '请选择条件类型'

  // 3) threshold
  const tRaw = form.value.threshold
  if (tRaw === '' || tRaw == null) {
    errs.threshold = '请输入阈值'
  } else {
    const t = parseFloat(tRaw)
    if (Number.isNaN(t)) {
      errs.threshold = '阈值必须是数字'
    } else {
      const ct = form.value.conditionType
      if ((ct === 'price_above' || ct === 'price_below') && t <= 0) {
        errs.threshold = '价格阈值必须大于 0'
      }
      if ((ct === 'rsi_overbought' || ct === 'rsi_oversold' || ct === 'trailing_take_profit')
          && (t <= 0 || t >= 100)) {
        errs.threshold = 'RSI/回撤阈值需在 (0, 100) 之间'
      }
    }
  }

  // 4) v1 边沿触发参数(可选,留空用后端默认)
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

  // 后端统一存 pnl_percent，根据正负号还原前端下拉选项
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

    threshold: String(displayThreshold)

  }

  editingId.value = item.id

  showForm.value = true

}



const submitForm = async () => {

  error.value = ''
  fieldErrors.value = {}

  // 前端校验
  const errs = validateForm()
  if (Object.keys(errs).length > 0) {
    fieldErrors.value = errs
    error.value = '表单有错误,请检查下方标红字段'
    return
  }

  const threshold = parseFloat(form.value.threshold)

  loading.value = true

  try {

    // 只在用户填了值时才带这些字段(后端 DTO 缺省值会更合理)
    const payload = {
      symbol: form.value.symbol.trim().toUpperCase(),
      conditionType: form.value.conditionType,
      threshold
    }
    if (form.value.cooldownMinutes !== '' && form.value.cooldownMinutes != null) {
      payload.cooldownMinutes = parseInt(form.value.cooldownMinutes, 10)
    }
    if (form.value.resetRatio !== '' && form.value.resetRatio != null) {
      payload.resetRatio = parseFloat(form.value.resetRatio)
    }
    if (form.value.reArmHours !== '' && form.value.reArmHours != null) {
      payload.reArmHours = parseInt(form.value.reArmHours, 10)
    }

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

    // 用工具函数统一处理
    const cls = classifyError(e)
    if (cls.retryable) {
      // 5xx / 网络:后端真挂,才走本地兜底
      error.value = cls.message + ',已暂存到本地'
      await saveLocalFallback()
      resetForm()
    } else {
      // 4xx:用户错误,不兜底,显示字段级错误让用户改
      error.value = cls.message
      fieldErrors.value = cls.fieldErrors
    }

  } finally {

    loading.value = false

  }

}



const handleToggle = async (item) => {

  const previous = item.enabled
  const next = !previous
  // 乐观切换 UI,失败时回滚
  item.enabled = next
  try {
    await updateAlert(item.id, { enabled: next })
  } catch (e) {
    const cls = classifyError(e)
    if (cls.retryable) {
      // 5xx/网络:本地切换 + 标记
      item._localOnly = true
      saveLocal()
      error.value = `后端暂不可用,已本地切换 ${item.name || item.symbol} 的开关状态`
    } else {
      // 4xx:回滚到原状态,显示后端错误
      item.enabled = previous
      error.value = `切换失败: ${cls.message}`
    }
  }

}



const handleDelete = async (item) => {

  if (!confirm(`确认删除 ${item.name || item.symbol} 的预警？`)) return

  const previousList = alerts.value.slice()
  // 乐观删除 UI,失败时回滚
  alerts.value = alerts.value.filter(a => a.id !== item.id)
  try {
    await deleteAlert(item.id)
  } catch (e) {
    const cls = classifyError(e)
    if (cls.retryable) {
      // 5xx/网络:本地删除 + 标记为本地态
      item._localOnly = true
      alerts.value.push(item)
      saveLocal()
      error.value = `后端暂不可用,已在本地暂存删除 ${item.name || item.symbol}(未真正同步)`
    } else {
      // 4xx:回滚列表,显示后端错误
      alerts.value = previousList
      error.value = `删除失败: ${cls.message}`
    }
  }

}



const loadAlerts = async () => {

  try {

    const res = await getAlerts()

    alerts.value = (res.data || []).map(a => ({ ...a, enabled: a.enabled !== false }))

  } catch (e) {

    const stored = localStorage.getItem('alerts_data')

    if (stored) {

      try { alerts.value = JSON.parse(stored) } catch { alerts.value = [] }

    } else {

      alerts.value = []

    }

  }

}



const saveLocal = () => {

  try {

    localStorage.setItem('alerts_data', JSON.stringify(alerts.value))

  } catch {}

}



const saveLocalFallback = async () => {

  // 仅在后端 5xx/网络错误时调用 — 给本地缓存加 _localOnly 标记,
  // 方便 UI 区分"真同步成功" vs "暂存本地未同步"
  const thresholdNum = parseFloat(form.value.threshold)
  const cooldownNum = form.value.cooldownMinutes !== '' && form.value.cooldownMinutes != null
      ? parseInt(form.value.cooldownMinutes, 10) : undefined
  const ratioNum = form.value.resetRatio !== '' && form.value.resetRatio != null
      ? parseFloat(form.value.resetRatio) : undefined
  const reArmNum = form.value.reArmHours !== '' && form.value.reArmHours != null
      ? parseInt(form.value.reArmHours, 10) : undefined

  if (editingId.value) {

    const idx = alerts.value.findIndex(a => a.id === editingId.value)

    if (idx >= 0) {

      alerts.value[idx] = {
        ...alerts.value[idx],
        ...form.value,
        threshold: thresholdNum,
        cooldownMinutes: cooldownNum,
        resetRatio: ratioNum,
        reArmHours: reArmNum,
        _localOnly: true
      }

    }

  } else {

    alerts.value.push({

      id: `local-${Date.now()}`,

      ...form.value,

      threshold: thresholdNum,

      cooldownMinutes: cooldownNum,

      resetRatio: ratioNum,

      reArmHours: reArmNum,

      enabled: true,

      _localOnly: true   // 标识:后端没保存,仅本地

    })

  }

  saveLocal()

  await loadAlerts()

}



const getConditionLabel = (type, threshold) => {

  // 后端统一存 pnl_percent，根据正负号显示止盈/止损
  if (type === 'pnl_percent') {

    return (threshold ?? 0) < 0 ? '止损%' : '止盈%'

  }

  return conditionTypes.find(c => c.value === type)?.label || type

}



const getConditionDesc = (type) => {

  if (type === 'pnl_profit' || type === 'pnl_loss') {

    return conditionTypes.find(c => c.value === type)?.desc || ''

  }

  return conditionTypes.find(c => c.value === type)?.desc || ''

}



const goBack = () => router.push('/dashboard')



// 启动时:从 URL query 预填(从 Dashboard 的"+预警"按钮跳转过来)
onMounted(async () => {
  await loadAlerts()

  const symbol = (route.query.symbol || '').toString().trim().toUpperCase()
  const wantsNew = route.query.new === '1'
  if (!wantsNew || !symbol) return

  // 查该 symbol 是否已有 alert
  const existing = alerts.value.filter(a => a.symbol === symbol)
  if (existing.length > 0) {
    const ok = confirm(`${existing[0]?.name || symbol} 已有 ${existing.length} 条预警,继续新建一条吗?\n(取消则跳到列表查看已有预警)`)
    if (!ok) {
      // 清掉 query,避免刷新又触发
      router.replace({ path: '/alerts' })
      return
    }
  }

  // 打开新表单,预填 symbol
  resetForm()
  form.value.symbol = symbol
  showForm.value = true
  // 清掉 query,刷新不再触发
  router.replace({ path: '/alerts' })
})

</script>



<template>

  <div class="alerts-page">

    <header>

      <button class="back-btn" @click="goBack">← 返回</button>

      <h1>预警设置</h1>

      <button class="add-btn" @click="openAdd">+ 添加预警</button>

    </header>

    <main>

      <!-- 添加/编辑表单 -->

      <div v-if="showForm" class="form-card">

        <h3>{{ editingId ? '编辑预警' : '添加预警' }}</h3>

        <div class="form-row">

          <label class="form-field" :class="{ 'has-error': fieldErrors.symbol }">
            <input v-model="form.symbol" placeholder="股票代码（AAPL / 600519 / 00700）" :disabled="!!editingId" />
            <span v-if="fieldErrors.symbol" class="field-error">{{ fieldErrors.symbol }}</span>
          </label>

          <label class="form-field" :class="{ 'has-error': fieldErrors.conditionType }">
            <select v-model="form.conditionType">
              <option v-for="ct in conditionTypes" :key="ct.value" :value="ct.value">
                {{ ct.label }}
              </option>
            </select>
            <span v-if="fieldErrors.conditionType" class="field-error">{{ fieldErrors.conditionType }}</span>
          </label>

          <label class="form-field" :class="{ 'has-error': fieldErrors.threshold }">
            <input v-model="form.threshold" type="number" step="0.01" :placeholder="getConditionDesc(form.conditionType)" />
            <span v-if="fieldErrors.threshold" class="field-error">{{ fieldErrors.threshold }}</span>
          </label>

        </div>

        <details class="form-advanced">
          <summary>高级设置（v1 边沿触发参数，留空使用默认值）</summary>
          <div class="form-row form-row-advanced">
            <label class="form-field" :class="{ 'has-error': fieldErrors.cooldownMinutes }">
              <span class="field-label">冷却（分钟，默认 5）</span>
              <input v-model="form.cooldownMinutes" type="number" min="0" max="1440" placeholder="5" />
              <span v-if="fieldErrors.cooldownMinutes" class="field-error">{{ fieldErrors.cooldownMinutes }}</span>
            </label>
            <label class="form-field" :class="{ 'has-error': fieldErrors.resetRatio }">
              <span class="field-label">重置比率（0~1，默认 0.5）</span>
              <input v-model="form.resetRatio" type="number" step="0.05" min="0" max="1" placeholder="0.5" />
              <span v-if="fieldErrors.resetRatio" class="field-error">{{ fieldErrors.resetRatio }}</span>
            </label>
            <label class="form-field" :class="{ 'has-error': fieldErrors.reArmHours }">
              <span class="field-label">重置小时（默认 2）</span>
              <input v-model="form.reArmHours" type="number" min="1" max="720" placeholder="2" />
              <span v-if="fieldErrors.reArmHours" class="field-error">{{ fieldErrors.reArmHours }}</span>
            </label>
          </div>
        </details>

        <p class="condition-desc">{{ getConditionDesc(form.conditionType) }}</p>

        <p v-if="error" class="error">{{ error }}</p>

        <div class="form-actions">

          <button class="btn btn-text" @click="resetForm">取消</button>

          <button class="btn btn-primary" @click="submitForm" :disabled="loading">

            {{ loading ? '保存中...' : '保存' }}

          </button>

        </div>

      </div>



      <p v-if="success" class="success">{{ success }}</p>



      <!-- 预警列表 -->

      <div v-if="alerts.length === 0" class="empty">暂无预警，点击右上角添加</div>



      <div class="alerts-list">

        <div v-for="item in alerts" :key="item.id" class="alert-card" :class="{ disabled: !item.enabled }">

          <div class="alert-main">

            <div class="alert-left">

              <div class="alert-symbol">
                {{ item.name || item.symbol }}
                <small v-if="item.name" class="alert-symbol-code">{{ item.symbol }}</small>
                <span v-if="item._localOnly" class="local-badge" title="仅本地暂存，后端未同步">本地</span>
              </div>

              <div class="alert-condition">{{ getConditionLabel(item.conditionType, item.threshold) }}</div>

            </div>

            <div class="alert-right">

              <div class="alert-threshold">阈值: {{ item.threshold }}</div>
              <div v-if="item.conditionType === 'trailing_take_profit' && item.highWatermark" class="alert-hwm">
                最高点: {{ Number(item.highWatermark).toFixed(2) }}
              </div>

              <label class="toggle-switch">

                <input type="checkbox" :checked="item.enabled" @change="handleToggle(item)" />

                <span class="toggle-slider"></span>

              </label>

            </div>

          </div>

          <div class="alert-actions">

            <button class="btn btn-sm btn-outline" @click="openEdit(item)">编辑</button>

            <button class="btn btn-sm btn-danger-outline" @click="handleDelete(item)">删除</button>

          </div>

        </div>

      </div>

    </main>

  </div>

</template>



<style scoped>

.alerts-page { min-height: 100vh; background: #f0f2f5; }

header { background: #1a1a2e; color: white; padding: 16px 24px; display: flex; align-items: center; gap: 16px; }

header h1 { margin: 0; font-size: 20px; flex: 1; }

.back-btn { background: transparent; border: 1px solid white; color: white; padding: 6px 16px; border-radius: 4px; cursor: pointer; }

.add-btn { background: #fa8c16; border: none; color: white; padding: 6px 16px; border-radius: 4px; cursor: pointer; font-size: 14px; }

.add-btn:hover { background: #ffa940; }

main { max-width: 700px; margin: 0 auto; padding: 24px 16px; }



.form-card { background: white; border-radius: 8px; padding: 24px; margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }

.form-card h3 { margin-bottom: 16px; font-size: 16px; }

.form-row { display: flex; flex-direction: column; gap: 10px; }

.form-row input, .form-row select { padding: 10px 12px; border: 1px solid #d9d9d9; border-radius: 4px; font-size: 14px; }

.form-row input:focus, .form-row select:focus { outline: none; border-color: #4096ff; }

.form-row select { background: white; cursor: pointer; }

.condition-desc { font-size: 12px; color: #999; margin-top: 8px; }

.form-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px; }



.alerts-list { display: flex; flex-direction: column; gap: 12px; }

.alert-card { background: white; border-radius: 8px; padding: 16px 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); transition: opacity 0.2s; }

.alert-card.disabled { opacity: 0.5; }

.alert-main { display: flex; justify-content: space-between; align-items: center; }

.alert-left { display: flex; flex-direction: column; gap: 4px; }

.alert-symbol { font-size: 18px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
.alert-symbol-code { font-size: 12px; color: #999; font-weight: normal; }

.local-badge {
  font-size: 11px; font-weight: 500; padding: 2px 6px;
  background: #fff7e6; color: #fa8c16;
  border: 1px solid #ffd591; border-radius: 4px;
}

.form-advanced {
  margin-top: 12px; padding: 8px 0;
  border-top: 1px dashed #e8e8e8;
}
.form-advanced summary {
  cursor: pointer; font-size: 13px; color: #666;
  user-select: none;
}
.form-advanced summary:hover { color: #1677ff; }
.form-row-advanced { margin-top: 10px; }

.form-field {
  display: flex; flex-direction: column; gap: 4px;
  font-size: 12px; color: #666;
}
.form-field > .field-label { font-size: 12px; color: #666; }
.form-field > input,
.form-field > select {
  padding: 10px 12px; border: 1px solid #d9d9d9;
  border-radius: 4px; font-size: 14px;
  background: white;
  color: #333;  /* 显式深色,避免继承父级灰色导致文字看不清 */
  font-family: inherit;
  /* 关键:让 select 用浏览器默认外观,避免我们覆盖导致 dropdown 不显示 */
  appearance: auto;
  -webkit-appearance: auto;
  -moz-appearance: auto;
}
/* number input 的上下箭头(spinner) 强制可见且可点 */
.form-field > input[type="number"] {
  -moz-appearance: textfield;
}
.form-field > input[type="number"]::-webkit-inner-spin-button,
.form-field > input[type="number"]::-webkit-outer-spin-button {
  opacity: 1;
  cursor: pointer;
  height: 24px;
  width: 14px;
}
.form-field > input:focus,
.form-field > select:focus { outline: none; border-color: #4096ff; }
.form-field.has-error > input,
.form-field.has-error > select {
  border-color: #ff4d4f;
  background: #fff2f0;
}
.field-error {
  font-size: 12px; color: #ff4d4f;
  line-height: 1.4;
}

.alert-condition { font-size: 13px; color: #888; }

.alert-right { display: flex; align-items: center; gap: 16px; }

.alert-threshold { font-size: 16px; font-weight: 600; color: #fa8c16; }

.alert-hwm { font-size: 12px; color: #999; margin-top: 2px; }

.alert-actions { display: flex; gap: 8px; margin-top: 10px; padding-top: 10px; border-top: 1px solid #f0f0f0; }



/* Toggle Switch */

.toggle-switch { position: relative; display: inline-block; width: 44px; height: 24px; }

.toggle-switch input { opacity: 0; width: 0; height: 0; }

.toggle-slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #ccc; transition: 0.3s; border-radius: 24px; }

.toggle-slider:before { position: absolute; content: ""; height: 18px; width: 18px; left: 3px; bottom: 3px; background-color: white; transition: 0.3s; border-radius: 50%; }

.toggle-switch input:checked + .toggle-slider { background-color: #52c41a; }

.toggle-switch input:checked + .toggle-slider:before { transform: translateX(20px); }



.btn { padding: 8px 16px; border: none; border-radius: 4px; font-size: 13px; cursor: pointer; transition: all 0.2s; }

.btn:disabled { opacity: 0.5; cursor: not-allowed; }

.btn-primary { background: #1677ff; color: white; }

.btn-primary:hover:not(:disabled) { background: #4096ff; }

.btn-text { background: transparent; color: #666; }

.btn-text:hover { color: #333; }

.btn-sm { padding: 4px 12px; font-size: 12px; }

.btn-outline { background: transparent; border: 1px solid #d9d9d9; color: #555; }

.btn-outline:hover { border-color: #1677ff; color: #1677ff; }

.btn-danger-outline { background: transparent; border: 1px solid #ffccc7; color: #ff4d4f; }

.btn-danger-outline:hover { background: #fff2f0; }



.empty { color: #999; text-align: center; padding: 48px 16px; font-size: 14px; }

.error { color: #ff4d4f; font-size: 13px; margin-bottom: 8px; }

.success { color: #52c41a; font-size: 13px; margin-bottom: 16px; text-align: center; }

</style>

