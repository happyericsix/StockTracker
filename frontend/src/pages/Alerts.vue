<script setup>

import { ref, onMounted } from 'vue'

import { useRouter, useRoute } from 'vue-router'

import { getAlerts, addAlert, updateAlert, deleteAlert } from '../api/alerts.js'
import AppIcon from '../components/AppIcon.vue'



const router = useRouter()
const route = useRoute()



const alerts = ref([])

const loading = ref(false)

// 表单校验错误：显示在表单卡内部（表单开着时用户就在旁边）
const error = ref('')

// 操作类错误（切换开关、删除、提交后表单已关闭的情况）挂在页面级。
// 不能和表单校验错误共用一个状态 —— 表单卡里的 <p class="error"> 会随表单一起被
// v-if 卸载，而提交走本地兜底时会 resetForm() 关掉表单，那条错误就永远看不到了。
const opError = ref('')

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
// 注意 status 优先取 businessCode：Result.error(...) 是**带 HTTP 200** 返回的，
// 直接用 HTTP 状态会把 4xx 类业务拒绝误判成"后端不可用"从而走错分支
// （见 api/request.js 的响应拦截器）。
const classifyError = (e) => {
  const status = e.businessCode ?? e.response?.status
  const rawMsg = e.response?.data?.message
  if (status >= 400 && status < 500) {
    return {
      retryable: false,
      status,
      message: rawMsg || `请求被拒绝（${status}），请检查填写内容后重试`,
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

    threshold: String(displayThreshold),

    // 高级参数回填：后端"不传保留原值",但表单若显示成空占位,
    // 用户会误以为当前就是默认值(5/0.5/2),保存时无从核对
    cooldownMinutes: item.cooldownMinutes != null ? String(item.cooldownMinutes) : '',

    resetRatio: item.resetRatio != null ? String(item.resetRatio) : '',

    reArmHours: item.reArmHours != null ? String(item.reArmHours) : ''

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
      // 注意：下面 resetForm() 会关掉表单，所以这条必须挂在页面级
      opError.value = cls.message + ',已暂存到本地'
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

  opError.value = ''
  const previous = item.enabled
  const next = !previous
  // 乐观切换 UI,失败时回滚
  item.enabled = next
  try {
    // PUT 是全量校验(AlertRequest 的 symbol/threshold 都是必填),只发 {enabled} 必 400。
    // item 里的 conditionType/threshold 是后端归一后的规范值,原样带回等于"不变只改开关"。
    await updateAlert(item.id, {
      symbol: item.symbol,
      conditionType: item.conditionType,
      threshold: item.threshold,
      enabled: next
    })
  } catch (e) {
    const cls = classifyError(e)
    if (cls.retryable) {
      // 5xx/网络:本地切换 + 标记
      item._localOnly = true
      saveLocal()
      opError.value = `后端暂不可用,已本地切换 ${item.name || item.symbol} 的开关状态`
    } else {
      // 4xx:回滚到原状态,显示后端错误
      item.enabled = previous
      opError.value = `切换失败: ${cls.message}`
    }
  }

}



const handleDelete = async (item) => {

  if (!confirm(`确认删除 ${item.name || item.symbol} 的预警？`)) return

  opError.value = ''
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
      opError.value = `后端暂不可用,已在本地暂存删除 ${item.name || item.symbol}(未真正同步)`
    } else {
      // 4xx:回滚列表,显示后端错误
      alerts.value = previousList
      opError.value = `删除失败: ${cls.message}`
    }
  }

}



const loadAlerts = async () => {

  try {

    const res = await getAlerts()

    alerts.value = (res.data || []).map(a => ({ ...a, enabled: a.enabled !== false }))

  } catch (e) {

    // 静默回退到本地缓存会让用户以为"确实没有预警"，所以必须说出来
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
  // 不再 loadAlerts()：此刻后端 5xx/网络故障才走到这里,
  // 重拉要么又失败(白费一次请求、还把更明确的 opError 覆盖成"加载失败"),
  // 要么后端恰好恢复——服务端列表(不含刚暂存的条目)会把本地兜底冲掉,
  // 且没有任何后续同步机制,条目就永远丢了。

}



const getConditionLabel = (type, threshold) => {

  // 后端统一存 pnl_percent，根据正负号显示止盈/止损
  if (type === 'pnl_percent') {

    return (threshold ?? 0) < 0 ? '止损%' : '止盈%'

  }

  return conditionTypes.find(c => c.value === type)?.label || type

}



const getConditionDesc = (type) => {

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

      <button class="back-btn" @click="goBack"><AppIcon name="chevron-left" :size="16" :stroke-width="2" /> 返回</button>

      <h1>预警设置</h1>

      <button class="add-btn" @click="openAdd">+ 添加预警</button>

    </header>

    <main>

      <!-- 添加/编辑表单 -->

      <div v-if="showForm" class="form-card">

        <!-- 二级标题：原来是 h3，导致标题大纲从 h1 直接跳到 h3（跳级） -->
        <h2>{{ editingId ? '编辑预警' : '添加预警' }}</h2>

        <div class="form-row">

          <label class="form-field" :class="{ 'has-error': fieldErrors.symbol }">
            <!-- 这三个字段视觉上靠 placeholder 充当标签（输入后即消失），
                 所以补 aria-label 给可访问名称 -->
            <input v-model="form.symbol" aria-label="股票代码" placeholder="股票代码（AAPL / 600519 / 00700）" :disabled="!!editingId" />
            <span v-if="fieldErrors.symbol" class="field-error">{{ fieldErrors.symbol }}</span>
          </label>

          <label class="form-field" :class="{ 'has-error': fieldErrors.conditionType }">
            <select v-model="form.conditionType" aria-label="条件类型">
              <option v-for="ct in conditionTypes" :key="ct.value" :value="ct.value">
                {{ ct.label }}
              </option>
            </select>
            <span v-if="fieldErrors.conditionType" class="field-error">{{ fieldErrors.conditionType }}</span>
          </label>

          <label class="form-field" :class="{ 'has-error': fieldErrors.threshold }">
            <input v-model="form.threshold" class="num" aria-label="阈值" type="number" step="0.01" :placeholder="getConditionDesc(form.conditionType)" />
            <span v-if="fieldErrors.threshold" class="field-error">{{ fieldErrors.threshold }}</span>
          </label>

        </div>

        <details class="form-advanced">
          <summary>高级设置（v1 边沿触发参数，留空使用默认值）</summary>
          <div class="form-row form-row-advanced">
            <label class="form-field" :class="{ 'has-error': fieldErrors.cooldownMinutes }">
              <span class="field-label">冷却（分钟，默认 5）</span>
              <input v-model="form.cooldownMinutes" class="num" type="number" min="0" max="1440" placeholder="5" />
              <span v-if="fieldErrors.cooldownMinutes" class="field-error">{{ fieldErrors.cooldownMinutes }}</span>
            </label>
            <label class="form-field" :class="{ 'has-error': fieldErrors.resetRatio }">
              <span class="field-label">重置比率（0~1，默认 0.5）</span>
              <input v-model="form.resetRatio" class="num" type="number" step="0.05" min="0" max="1" placeholder="0.5" />
              <span v-if="fieldErrors.resetRatio" class="field-error">{{ fieldErrors.resetRatio }}</span>
            </label>
            <label class="form-field" :class="{ 'has-error': fieldErrors.reArmHours }">
              <span class="field-label">重置小时（默认 2）</span>
              <input v-model="form.reArmHours" class="num" type="number" min="1" max="720" placeholder="2" />
              <span v-if="fieldErrors.reArmHours" class="field-error">{{ fieldErrors.reArmHours }}</span>
            </label>
          </div>
        </details>

        <p class="condition-desc">{{ getConditionDesc(form.conditionType) }}</p>

        <p v-if="error" class="error" role="alert">{{ error }}</p>

        <div class="form-actions">

          <button class="btn btn-text" @click="resetForm">取消</button>

          <button class="btn btn-primary" @click="submitForm" :disabled="loading">

            {{ loading ? '保存中...' : '保存' }}

          </button>

        </div>

      </div>



      <!-- 页面级操作错误：与表单校验错误分开，
           否则切换/删除失败时（表单是关着的）用户看不到任何反馈 -->
      <p v-if="opError" class="error" role="alert">{{ opError }}</p>

      <p v-if="success" class="success" role="status">{{ success }}</p>



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

              <div class="alert-threshold num">阈值: {{ item.threshold }}</div>
              <div v-if="item.conditionType === 'trailing_take_profit' && item.highWatermark" class="alert-hwm num">
                最高点: {{ Number(item.highWatermark).toFixed(2) }}
              </div>

              <label class="toggle-switch">

                <input
                  type="checkbox"
                  :checked="item.enabled"
                  :aria-label="`${item.name || item.symbol} 预警开关`"
                  @change="handleToggle(item)"
                />

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
.alerts-page {
  min-height: 100vh;
  /* 移动端地址栏高度算进 100vh，会顶出底部，补 dvh 兜底 */
  min-height: 100dvh;
  background: var(--color-bg-page);
}

/* 用子选择器限定到页面级 chrome。裸元素选择器会命中组件里**任何** header/main ——
   Dashboard 那次"行情卡头部白字压深蓝、对比度 1:1"就是这么来的（见那里的注释）。
   这里目前只有一个页面级 header/main，但那是"碰巧没事"，不是"构造上没事"。 */
.alerts-page > header {
  background: var(--color-bg-inverse);
  color: var(--color-text-inverse);
  /* iOS 独立模式（black-translucent）内容会顶到状态栏下，让出顶部安全区 */
  padding: calc(var(--space-4) + env(safe-area-inset-top, 0px)) var(--space-5) var(--space-4);
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.alerts-page > header h1 {
  margin: 0;
  font: var(--font-heading);
  color: var(--color-text-inverse);
  flex: 1;
}

.back-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  background: transparent;
  border: 1px solid var(--color-text-inverse);
  color: var(--color-text-inverse);
  padding: 6px var(--space-4);
  border-radius: var(--radius-sm);
  font: var(--font-ui);
  cursor: pointer;
}

/* 原 #fa8c16 配白字只有 2.38:1（严重不达标），换警示色 5.43:1 */
.add-btn {
  background: var(--color-warning);
  border: none;
  color: var(--color-text-on-accent);
  padding: 6px var(--space-4);
  border-radius: var(--radius-sm);
  font: var(--font-ui);
  font-weight: 500;
  cursor: pointer;
}

/* 悬停不再换更浅的橙（#ffa940 配白字只有 3.5:1），改为整体压暗，文字对比度只增不减 */
.add-btn:hover { filter: brightness(0.88); }

.alerts-page > main {
  max-width: 700px;
  margin: 0 auto;
  padding: var(--space-5) var(--space-4);
}

/* ---------- 表单卡 ---------- */
.form-card {
  background: var(--color-bg-surface);
  border-radius: var(--radius-3xl);
  padding: var(--space-5);
  margin-bottom: var(--space-5);
  box-shadow: var(--shadow-1);
}

.form-card h2 {
  font: var(--font-heading);
  color: var(--color-text-primary);
  margin-bottom: var(--space-4);
}

.form-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

/* 输入框 16px（--font-body）：小于 16px 时 iOS Safari 会把整页放大。
   原值 14px 在 iPhone 上点一下输入框整页就跳一下。 */
.form-row input,
.form-row select {
  padding: var(--space-3);
  border: 1px solid var(--color-border-control);
  border-radius: var(--radius-sm);
  font: var(--font-body);
}

.form-row input:focus-visible,
.form-row select:focus-visible { border-color: var(--color-accent); }

.form-row select { background: var(--color-bg-surface); cursor: pointer; }

.condition-desc {
  font: var(--font-caption);
  color: var(--color-text-muted);
  margin-top: var(--space-2);
}

.form-actions {
  display: flex;
  gap: var(--space-2);
  justify-content: flex-end;
  margin-top: var(--space-4);
}

/* ---------- 预警列表 ---------- */
.alerts-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.alert-card {
  background: var(--color-bg-surface);
  border-radius: var(--radius-3xl);
  padding: var(--space-5);
  box-shadow: var(--shadow-1);
}

/* 停用态**不用** opacity。整卡半透明会把卡里所有文字一起拖到 4.5:1 以下
   （白底上 #1a1a2e 加 50% 透明度实测约 3.0:1），而"哪只股票、什么条件"仍然是
   用户要读的内容，不是装饰。改成"表面变浅 + 标的降一档字号色"，
   文字对比度不退；开关自身的状态由 .toggle-slider 表达，不靠整卡变淡。 */
.alert-card.disabled { background: var(--color-bg-subtle); }

.alert-card.disabled .alert-symbol { color: var(--color-text-secondary); }

.alert-main {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-4);
}

.alert-left {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.alert-symbol {
  font: var(--font-heading);
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.alert-symbol-code {
  font: var(--font-caption);
  color: var(--color-text-muted);
  font-weight: 400;
}

/* 原来是 #fff7e6 底 + #fa8c16 文字（对浅底约 2.4:1）。恢复米黄底但把文字换成达标的警示色。
   字号从 11px 提到 12px（--font-micro）：11px 已低于小字号下限。 */
.local-badge {
  font: var(--font-micro);
  padding: 2px 6px;
  background: var(--color-warning-soft);
  color: var(--color-warning);
  border: 1px solid var(--color-warning-mark);
  border-radius: var(--radius-sm);
}

.form-advanced {
  margin-top: var(--space-3);
  padding: var(--space-2) 0;
  border-top: 1px dashed var(--color-border);
}

.form-advanced summary {
  cursor: pointer;
  font: var(--font-caption);
  color: var(--color-text-muted);
  user-select: none;
}

.form-advanced summary:hover { color: var(--color-accent); }

.form-row-advanced { margin-top: var(--space-3); }

.form-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  font: var(--font-caption);
  color: var(--color-text-muted);
}

.form-field > .field-label {
  font: var(--font-caption);
  color: var(--color-text-muted);
}

.form-field > input,
.form-field > select {
  padding: var(--space-3);
  border: 1px solid var(--color-border-control);
  border-radius: var(--radius-sm);
  font: var(--font-body);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);  /* 显式深色,避免继承父级灰色导致文字看不清 */
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

.form-field > input:focus-visible,
.form-field > select:focus-visible { border-color: var(--color-accent); }

/* 原来只有边框变色，扫读时不够醒目；恢复原有的浅红底做整块标红。
   文字/边框用 --color-danger（对浅红底 5.07:1），底色用 --color-danger-soft */
.form-field.has-error > input,
.form-field.has-error > select {
  border-color: var(--color-danger);
  background: var(--color-danger-soft);
}

.field-error {
  font: var(--font-caption);
  color: var(--color-danger);
  line-height: 1.4;
}

.alert-condition { font: var(--font-caption); color: var(--color-text-secondary); }

.alert-right { display: flex; align-items: center; gap: var(--space-4); }

.alert-threshold {
  font: var(--font-body);
  font-weight: 600;
  color: var(--color-warning);
}

.alert-hwm { font: var(--font-caption); color: var(--color-text-muted); margin-top: 2px; }

.alert-actions {
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}

/* ---------- 开关 ---------- */
.toggle-switch {
  position: relative;
  display: inline-block;
  width: 44px;
  height: 24px;
  flex-shrink: 0;
}

.toggle-switch input { opacity: 0; width: 0; height: 0; }

.toggle-slider {
  position: absolute;
  cursor: pointer;
  top: 0; left: 0; right: 0; bottom: 0;
  background-color: var(--color-border-control);
  transition-property: background-color;
  transition-duration: var(--duration-base);
  transition-timing-function: var(--ease-out);
  border-radius: var(--radius-pill);
}

.toggle-slider:before {
  position: absolute;
  content: "";
  height: 18px;
  width: 18px;
  left: 3px;
  bottom: 3px;
  background-color: var(--color-bg-surface);
  transition-property: transform;
  transition-duration: var(--duration-base);
  transition-timing-function: var(--ease-out);
  border-radius: 50%;
}

.toggle-switch input:checked + .toggle-slider { background-color: var(--color-success); }

.toggle-switch input:checked + .toggle-slider:before { transform: translateX(20px); }

/* 开关是自定义控件，原生焦点环被 opacity:0 的 input 带走了，这里必须显式给 */
.toggle-switch input:focus-visible + .toggle-slider {
  outline: 2px solid var(--color-focus-ring);
  outline-offset: 2px;
}

/* ---------- 按钮 ---------- */
/* 原来是 transition: all —— 它会把**所有**可动画属性一起过渡（包括布局属性），
   既浪费性能又会带来意外动画。只声明真正会变的那几个颜色属性。 */
.btn {
  padding: var(--space-2) var(--space-4);
  border: none;
  border-radius: var(--radius-sm);
  font: var(--font-caption);
  cursor: pointer;
  transition-property: background-color, border-color, color;
  transition-duration: var(--duration-base);
  transition-timing-function: var(--ease-out);
}

.btn:disabled { opacity: 0.5; cursor: not-allowed; }

.btn-primary { background: var(--color-accent); color: var(--color-text-on-accent); }

.btn-primary:hover:not(:disabled) { background: var(--color-accent-hover); }

.btn-text { background: transparent; color: var(--color-text-muted); }

.btn-text:hover { color: var(--color-text-primary); }

/* 字号不再单独降到 12px，继承 .btn 的 13px（--font-caption）—— 12px 已在小字号下限上 */
.btn-sm { padding: var(--space-1) var(--space-3); }

/* 按钮边界同属控件边界，也要 ≥3:1（原 #d9d9d9 仅 1.41:1） */
.btn-outline { background: transparent; border: 1px solid var(--color-border-control); color: var(--color-text-secondary); }

.btn-outline:hover { border-color: var(--color-accent); color: var(--color-accent); }

/* 原 #ffccc7 边框对白仅 1.4:1、#ff4d4f 文字 3.19:1，都换危险色 */
.btn-danger-outline { background: transparent; border: 1px solid var(--color-danger); color: var(--color-danger); }

.btn-danger-outline:hover { background: var(--color-bg-subtle); }

/* ---------- 空态与状态文案 ---------- */
/* 原 #999 对灰底 2.54:1。空态是空页面上**唯一**的内容，所以给它一个和卡片同级的表面，
   而不是一行浮在灰底上的小字。 */
.empty {
  font: var(--font-ui);
  color: var(--color-text-muted);
  text-align: center;
  padding: var(--space-6) var(--space-4);
  background: var(--color-bg-surface);
  border-radius: var(--radius-3xl);
  box-shadow: var(--shadow-1);
}

.error { font: var(--font-caption); color: var(--color-danger); margin-bottom: var(--space-2); }

/* 原 #52c41a 作文字对灰底约 2.2:1 */
.success {
  font: var(--font-caption);
  color: var(--color-success);
  margin-bottom: var(--space-4);
  text-align: center;
}
</style>

