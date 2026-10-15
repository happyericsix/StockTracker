<script setup>
/**
 * 策略编辑面板：把 configJson 的 DSL 表单化。逻辑自旧版原样移植（零漂移）。
 *
 * 两条硬边界（与后端一致）：
 * 1. 标的不可改 —— 账户、成交历史、验证结论都挂在原标的上，换票=新建策略；
 * 2. 保存前走后端的 DSL 校验，改坏的规则被挡在落库之前（错误原文展示）。
 * 口径纪律：规则改过之后，之前/之后的净值不可直接比较（configUpdatedAt 打点）。
 */
import { ref, reactive, watch, computed } from 'vue'
import { X, Plus } from 'lucide-vue-next'
import { getStrategy, updateStrategy } from '../api/strategy.js'
import Button from './ui/button.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  strategyId: { type: [Number, String], default: null },
})
const emit = defineEmits(['close', 'saved'])

// 条件类型注册表（14 种，与后端 strategy_schema 一一对应）
const CONDITION_META = {
  price_cross_ma:   { label: '价格穿越均线', group: '趋势', params: ['direction', 'window'] },
  price_above_ma:   { label: '价格站上均线', group: '趋势', params: ['window'] },
  price_below_ma:   { label: '价格跌破均线', group: '趋势', params: ['window'] },
  ma_cross:         { label: '均线交叉', group: '趋势', params: ['fast', 'slow'] },
  macd_cross:       { label: 'MACD 交叉', group: '动量', params: [] },
  rsi_above:        { label: 'RSI 高于', group: '动量', params: ['value'] },
  rsi_below:        { label: 'RSI 低于', group: '动量', params: ['value'] },
  volume_ratio:     { label: '量比（放量/缩量）', group: '动量', params: ['direction', 'window', 'value'] },
  price_above:      { label: '价格高于（元）', group: '价位', params: ['value'] },
  price_below:      { label: '价格低于（元）', group: '价位', params: ['value'] },
  stop_loss_pct:    { label: '止损（%）', group: '风控', params: ['value'] },
  take_profit_pct:  { label: '止盈（%）', group: '风控', params: ['value'] },
  trailing_stop_pct:{ label: '跟踪止盈（%）', group: '风控', params: ['value'] },
  bias_pct:         { label: '乖离率≤阈值（防追高）', group: '风控', params: ['window', 'value'] },
}
const TYPE_GROUPS = ['趋势', '动量', '价位', '风控']

const loading = ref(false)
const saving = ref(false)
const error = ref('')
const name = ref('')
const symbol = ref('')
const sizePct = ref(100)
const slippagePct = ref(0.1)
// 一个 reactive 对象 + 按组遍历，绑定可靠（避免模板里 ref 嵌套解包陷阱）
const condGroups = reactive({
  entry: { logic: 'all', rows: [] },
  exit: { logic: 'any', rows: [] },
})
const SECTION_TITLE = { entry: '入场条件', exit: '出场条件（含风控）' }
/** 原始 config 里不进表单的部分：保存时原样带回，一字不动 */
let keepFields = {}

const meta = (row) => CONDITION_META[row.type] || { label: row.type, params: [], group: '其他' }
const show = (row, param) => meta(row).params.includes(param)

const cloneRows = (rows) => (rows || []).map((r) => ({ ...r }))

const load = async () => {
  if (!props.strategyId) return
  loading.value = true
  error.value = ''
  try {
    const res = await getStrategy(props.strategyId)
    const s = res.data
    const cfg = JSON.parse(s.configJson || '{}')
    name.value = s.name || ''
    symbol.value = s.symbol || cfg.symbol || ''
    sizePct.value = cfg.position?.size_pct ?? 100
    slippagePct.value = cfg.risk?.slippage_pct ?? 0.1
    condGroups.entry.logic = cfg.entry?.logic === 'any' ? 'any' : 'all'
    condGroups.exit.logic = cfg.exit?.logic === 'any' ? 'any' : 'all'
    condGroups.entry.rows = cloneRows(cfg.entry?.conditions)
    condGroups.exit.rows = cloneRows(cfg.exit?.conditions)
    keepFields = {
      schema_version: cfg.schema_version,
      initial_capital: cfg.initial_capital,
      data: cfg.data,
      risk: cfg.risk,
    }
  } catch (e) {
    error.value = e?.response?.data?.message || '策略加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

watch(() => props.open, (open) => { if (open) load() })

const removeRow = (list, index) => { list.splice(index, 1) }
const addRow = (list) => {
  list.push({ type: 'price_cross_ma', direction: 'above', window: 60 })
}

const buildConfig = () => JSON.stringify({
  ...keepFields,
  symbol: symbol.value,
  name: name.value,
  // risk 整体来自 keepFields，但滑点有独立输入位——显式覆盖，避免两处打架
  risk: { ...(keepFields.risk || {}), slippage_pct: Number(slippagePct.value) },
  position: { type: 'percent', size_pct: Number(sizePct.value) },
  entry: { logic: condGroups.entry.logic, conditions: condGroups.entry.rows },
  exit: { logic: condGroups.exit.logic, conditions: condGroups.exit.rows },
})

const dirty = computed(() => saving.value || loading.value)

const save = async () => {
  if (dirty.value) return
  if (!name.value.trim()) { error.value = '策略名称不能为空'; return }
  if (!(Number(sizePct.value) > 0 && Number(sizePct.value) <= 100)) {
    error.value = '仓位必须是 (0, 100] 之间的百分比'; return
  }
  if (!(Number(slippagePct.value) >= 0 && Number(slippagePct.value) <= 2)) {
    error.value = '滑点必须是 [0, 2] 之间的百分数（0.1 = 千分之一）'; return
  }
  if (!condGroups.entry.rows.length) { error.value = '至少保留一条入场条件'; return }
  if (!condGroups.exit.rows.length) { error.value = '至少保留一条出场条件（建议含止损）'; return }
  saving.value = true
  error.value = ''
  try {
    await updateStrategy(props.strategyId, { name: name.value.trim(), configJson: buildConfig() })
    emit('saved')
    emit('close')
  } catch (e) {
    error.value = e?.response?.data?.message || '保存失败，请稍后重试'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition name="sheet">
      <div
        v-if="open"
        class="fixed inset-0 z-50 flex max-sm:items-end sm:justify-end"
        :style="{ background: 'var(--overlay)' }"
        @click.self="emit('close')"
      >
        <aside
          class="flex h-[92dvh] w-full flex-col rounded-t-2xl border border-border bg-surface sm:h-dvh sm:w-[min(560px,100%)] sm:rounded-none sm:border-y-0 sm:border-l"
          role="dialog"
          aria-label="调整策略规则"
          style="box-shadow: var(--shadow-pop)"
        >
          <header class="flex items-start justify-between gap-3 border-b border-border px-5 pb-3 pt-4">
            <div>
              <h2 class="text-[16px] font-semibold text-ink">调整策略规则</h2>
              <p class="num mt-1 text-[12.5px] text-ink-3">{{ name }} · {{ symbol }}（标的不可更改）</p>
            </div>
            <button
              type="button"
              class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
              aria-label="关闭"
              @click="emit('close')"
            >
              <X :size="18" :stroke-width="2" aria-hidden="true" />
            </button>
          </header>

          <div class="flex-1 overflow-y-auto px-5 py-3.5">
            <p v-if="loading" class="text-[13px] text-ink-2">加载策略中…</p>
            <template v-else>
              <div class="grid gap-3 [grid-template-columns:1.4fr_1fr]">
                <label class="flex flex-col gap-1 text-[12.5px] text-ink-2">
                  策略名称
                  <input
                    v-model="name"
                    type="text"
                    maxlength="40"
                    class="h-[34px] rounded-lg border border-border bg-bg px-2.5 text-[13px] text-ink focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
                  >
                </label>
                <label class="flex flex-col gap-1 text-[12.5px] text-ink-2">
                  仓位（买入用现金的 %）
                  <input
                    v-model.number="sizePct"
                    type="number"
                    min="1"
                    max="100"
                    step="1"
                    class="num h-[34px] rounded-lg border border-border bg-bg px-2.5 text-[13px] text-ink focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
                  >
                  <!-- 满仓默认值陷阱：满仓=个股波动 1:1 传导到账户，说清楚再存 -->
                  <em v-if="Number(sizePct) >= 100" class="mt-0.5 text-[12px] not-italic leading-relaxed text-up">
                    满仓：买入后个股每跌 1%，账户约跌 1%，没有现金缓冲。确认这是你的选择，不是没改过的默认值。
                  </em>
                </label>
              </div>

              <label class="mt-3 flex flex-col gap-1 text-[12.5px] text-ink-2">
                  滑点模拟（成交价向不利方向偏移的 %）
                <input
                  v-model.number="slippagePct"
                  type="number"
                  min="0"
                  max="2"
                  step="0.01"
                  class="num h-[34px] w-[220px] rounded-lg border border-border bg-bg px-2.5 text-[13px] text-ink focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
                >
                <!-- 滑点没有官方值，是撮合近似：说清它模拟什么、怎么选 -->
                <em class="mt-0.5 text-[12px] not-italic leading-relaxed text-ink-3">
                  模拟"下单到成交之间价格向不利方向的移动"。流动性好的大盘股 1 跳价差约
                  0.1～0.2%，0.1 是保守默认；想更贴近盘口可调小、想更保守可调大。
                  佣金/印花税/过户费不用设——按官方费率表和你档案里的真实佣金自动算。
                </em>
              </label>

              <section v-for="(group, key) in condGroups" :key="key" class="mt-4.5">
                <div class="mb-2 flex items-center justify-between gap-3">
                  <h3 class="text-[13.5px] font-semibold text-ink">{{ SECTION_TITLE[key] }}</h3>
                  <select
                    v-model="group.logic"
                    class="h-[30px] rounded-lg border border-border bg-bg px-2 text-[12.5px] text-ink focus:border-accent focus:outline-none"
                    :aria-label="SECTION_TITLE[key] + '的组合方式'"
                  >
                    <option value="all">全部满足</option>
                    <option value="any">任一满足</option>
                  </select>
                </div>
                <div
                  v-for="(row, i) in group.rows"
                  :key="i"
                  class="mb-2 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-surface-2 p-2"
                >
                  <select
                    v-model="row.type"
                    class="h-[30px] min-w-[150px] rounded-lg border border-border bg-bg px-2 text-[12.5px] text-ink focus:border-accent focus:outline-none"
                  >
                    <optgroup v-for="g in TYPE_GROUPS" :key="g" :label="g">
                      <option v-for="(m, t) in CONDITION_META" :key="t" :value="t">{{ m.label }}</option>
                    </optgroup>
                  </select>
                  <select
                    v-if="show(row, 'direction')"
                    v-model="row.direction"
                    class="h-[30px] rounded-lg border border-border bg-bg px-2 text-[12.5px] text-ink focus:border-accent focus:outline-none"
                  >
                    <option value="above">上穿 / 上方</option>
                    <option value="below">下穿 / 下方</option>
                  </select>
                  <label v-if="show(row, 'window')" class="flex items-center gap-1 text-[12px] text-ink-2">
                    均线<input v-model.number="row.window" type="number" min="2" max="500" class="num h-[30px] w-[64px] rounded-lg border border-border bg-bg px-2 text-[12.5px] text-ink focus:border-accent focus:outline-none">日
                  </label>
                  <label v-if="show(row, 'fast')" class="flex items-center gap-1 text-[12px] text-ink-2">
                    快线<input v-model.number="row.fast" type="number" min="2" max="300" class="num h-[30px] w-[64px] rounded-lg border border-border bg-bg px-2 text-[12.5px] text-ink focus:border-accent focus:outline-none">
                  </label>
                  <label v-if="show(row, 'slow')" class="flex items-center gap-1 text-[12px] text-ink-2">
                    慢线<input v-model.number="row.slow" type="number" min="3" max="500" class="num h-[30px] w-[64px] rounded-lg border border-border bg-bg px-2 text-[12.5px] text-ink focus:border-accent focus:outline-none">
                  </label>
                  <label v-if="show(row, 'value')" class="flex items-center gap-1 text-[12px] text-ink-2">
                    值<input v-model.number="row.value" type="number" step="0.1" class="num h-[30px] w-[68px] rounded-lg border border-border bg-bg px-2 text-[12.5px] text-ink focus:border-accent focus:outline-none">
                  </label>
                  <button
                    type="button"
                    class="ml-auto flex h-7 w-7 items-center justify-center rounded-md text-ink-3 transition-colors hover:bg-up-soft hover:text-up"
                    aria-label="删除这条条件"
                    @click="removeRow(group.rows, i)"
                  >
                    <X :size="13" :stroke-width="2" aria-hidden="true" />
                  </button>
                </div>
                <button
                  type="button"
                  class="flex h-[34px] w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-border text-[12.5px] text-ink-2 transition-colors hover:border-accent hover:text-accent"
                  @click="addRow(group.rows)"
                >
                  <Plus :size="14" :stroke-width="2" aria-hidden="true" /> 添加条件
                </button>
              </section>

              <p class="mt-3.5 text-[12px] leading-relaxed text-ink-3">
                保存后自下一次评估起生效；已持仓与历史记录不变。
                规则修改前后的净值<b class="font-medium text-ink-2">口径不同、不可直接比较</b>（页面会标注调整时间）。
                止损填负数（如 -8 表示亏 8%），止盈/跟踪止盈填正数。
              </p>
              <p v-if="error" class="mt-2.5 text-[12.5px] text-up" role="alert">{{ error }}</p>
            </template>
          </div>

          <footer class="flex justify-end gap-2.5 border-t border-border px-5 pb-[calc(12px+env(safe-area-inset-bottom))] pt-3">
            <Button @click="emit('close')">取消</Button>
            <Button variant="primary" :disabled="dirty" :loading="saving" @click="save">
              {{ saving ? '校验并保存中…' : '保存（自动校验）' }}
            </Button>
          </footer>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.sheet-enter-active, .sheet-leave-active { transition: opacity 0.18s ease; }
.sheet-enter-active aside, .sheet-leave-active aside { transition: transform 0.22s cubic-bezier(0.2, 0, 0, 1); }
.sheet-enter-from, .sheet-leave-to { opacity: 0; }
@media (min-width: 640px) {
  .sheet-enter-from aside, .sheet-leave-to aside { transform: translateX(30px); }
}
@media (max-width: 639px) {
  .sheet-enter-from aside, .sheet-leave-to aside { transform: translateY(40px); }
}
</style>
