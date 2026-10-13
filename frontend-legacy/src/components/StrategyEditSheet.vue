<script setup>
import { ref, reactive, watch, computed } from 'vue'
import { getStrategy, updateStrategy } from '../api/strategy.js'
import AppIcon from './AppIcon.vue'

/**
 * 策略编辑面板（2026-10-10）：把 configJson 的 DSL 表单化，让"看到哨兵观察 → 调整规则"
 * 成为一条直线，而不是"让 AI 重新生成一条"。
 *
 * 两条硬边界（与后端一致）：
 * 1. 标的不可改 —— 账户、成交历史、验证结论都挂在原标的上，换票=新建策略；
 * 2. 保存前走后端的 DSL 校验，改坏的规则被挡在落库之前（错误原文展示）。
 *
 * 口径纪律：规则改过之后，之前的净值与之后**不可直接比较**（页面提示 + 后端
 * configUpdatedAt 打点，与 decisionModeSince 同一条纪律）。
 */
const props = defineProps({
  open: { type: Boolean, default: false },
  strategyId: { type: [Number, String], default: null },
})
const emit = defineEmits(['close', 'saved'])

// 条件类型注册表：label 给人看，params 决定这一行显示哪些输入框
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
// 条件组：reactive 一体结构。此前用"独立 ref + v-for 内联对象包着 ref"的写法，
// 模板里 sec.logic.value 被自动解包成 undefined —— 下拉框显示为空（用户实报）。
// 一个 reactive 对象 + 按组遍历，绑定天然可靠。
const condGroups = reactive({
  entry: { logic: 'all', rows: [] },
  exit: { logic: 'any', rows: [] },
})
const SECTION_TITLE = { entry: '入场条件', exit: '出场条件（含风控）' }
/** 原始 config 里不进表单的部分（schema_version/symbol/data/risk/initial_capital）：
 *  保存时原样带回 —— 编辑面板只改它教得会的字段，其余一字不动。 */
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
      <div v-if="open" class="sheet-mask" @click.self="emit('close')">
        <aside class="edit-sheet" role="dialog" aria-label="调整策略规则">
          <header class="sheet-head">
            <div>
              <h2>调整策略规则</h2>
              <p class="sheet-sub">{{ name }} · {{ symbol }}（标的不可更改）</p>
            </div>
            <button type="button" class="close-btn" aria-label="关闭" @click="emit('close')">
              <AppIcon name="close" :size="18" :stroke-width="2" />
            </button>
          </header>

          <div class="sheet-body">
            <p v-if="loading" class="hint">加载策略中…</p>
            <template v-else>
              <div class="field-row two">
                <label class="field">
                  <span>策略名称</span>
                  <input v-model="name" type="text" maxlength="40" />
                </label>
                <label class="field">
                  <span>仓位（买入用现金的 %）</span>
                  <input v-model.number="sizePct" type="number" min="1" max="100" step="1" />
                  <!-- 满仓默认值陷阱（2026-10-12 用户实例）：创建策略时没人指出 100% 是默认值，
                       首笔买入后才发现现金只剩几百。满仓 = 个股波动 1:1 传导到账户，说清楚再存。 -->
                  <em v-if="Number(sizePct) >= 100" class="field-note warn">
                    满仓：买入后个股每跌 1%，账户约跌 1%，没有现金缓冲。确认这是你的选择，不是没改过的默认值。
                  </em>
                </label>
              </div>

              <section v-for="(group, key) in condGroups" :key="key" class="cond-section">
                <div class="sec-head">
                  <h3>{{ SECTION_TITLE[key] }}</h3>
                  <!-- 只留下拉框：此前旁边又摆了一遍"任一满足"文字，同一状态显示两份（用户反馈）。 -->
                  <select v-model="group.logic" class="logic-select" :aria-label="SECTION_TITLE[key] + '的组合方式'">
                    <option value="all">全部满足</option>
                    <option value="any">任一满足</option>
                  </select>
                </div>
                <div v-for="(row, i) in group.rows" :key="i" class="cond-row">
                  <select v-model="row.type" class="type-select">
                    <optgroup v-for="g in TYPE_GROUPS" :key="g" :label="g">
                      <option v-for="(m, t) in CONDITION_META" :key="t" :value="t">{{ m.label }}</option>
                    </optgroup>
                  </select>
                  <template v-if="show(row, 'direction')">
                    <select v-model="row.direction" class="param-select">
                      <option value="above">上穿 / 上方</option>
                      <option value="below">下穿 / 下方</option>
                    </select>
                  </template>
                  <label v-if="show(row, 'window')" class="param">
                    <span>均线</span><input v-model.number="row.window" type="number" min="2" max="500" />
                    <span>日</span>
                  </label>
                  <label v-if="show(row, 'fast')" class="param">
                    <span>快线</span><input v-model.number="row.fast" type="number" min="2" max="300" />
                  </label>
                  <label v-if="show(row, 'slow')" class="param">
                    <span>慢线</span><input v-model.number="row.slow" type="number" min="3" max="500" />
                  </label>
                  <label v-if="show(row, 'value')" class="param">
                    <span>值</span><input v-model.number="row.value" type="number" step="0.1" />
                  </label>
                  <button type="button" class="row-del" aria-label="删除这条条件" @click="removeRow(group.rows, i)">✕</button>
                </div>
                <button type="button" class="add-btn" @click="addRow(group.rows)">＋ 添加条件</button>
              </section>

              <p class="hint">
                保存后自下一次评估起生效；已持仓与历史记录不变。
                规则修改前后的净值<b>口径不同、不可直接比较</b>（页面会标注调整时间）。
                止损填负数（如 -8 表示亏 8%），止盈/跟踪止盈填正数。
              </p>
              <p v-if="error" class="error" role="alert">{{ error }}</p>
            </template>
          </div>

          <footer class="sheet-foot">
            <button type="button" class="btn ghost" @click="emit('close')">取消</button>
            <button type="button" class="btn primary" :disabled="dirty" @click="save">
              {{ saving ? '校验并保存中…' : '保存（自动校验）' }}
            </button>
          </footer>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.sheet-mask {
  position: fixed; inset: 0; background: var(--color-overlay);
  display: flex; justify-content: flex-end; z-index: 1100;
}
.edit-sheet {
  width: min(560px, 100%);
  height: 100dvh; background: var(--color-bg-surface);
  display: flex; flex-direction: column;
  box-shadow: var(--shadow-3, 0 8px 30px rgba(0,0,0,.25));
}
@media (max-width: 639px) {
  .sheet-mask { align-items: flex-end; }
  .edit-sheet { width: 100%; height: 92dvh; border-radius: var(--radius-lg) var(--radius-lg) 0 0; }
}
.sheet-head {
  display: flex; justify-content: space-between; align-items: flex-start;
  padding: 16px 20px 12px; border-bottom: 1px solid var(--color-border);
}
.sheet-head h2 { margin: 0; font-size: 16px; }
.sheet-sub { margin: 4px 0 0; font-size: 12.5px; color: var(--color-text-tertiary); }
.close-btn {
  background: none; border: none; cursor: pointer; color: var(--color-text-secondary);
  padding: 4px; border-radius: var(--radius-sm);
}
.sheet-body { flex: 1; overflow-y: auto; padding: 14px 20px; }
.field-row.two { display: grid; grid-template-columns: 1.4fr 1fr; gap: 12px; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 12.5px; color: var(--color-text-secondary); }
.field input {
  padding: 8px 10px; border: 1px solid var(--color-border); border-radius: var(--radius-sm);
  font: inherit; background: var(--color-bg); color: var(--color-text-primary);
}
.cond-section { margin-top: 18px; }
.sec-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.sec-head h3 { margin: 0; font-size: 13.5px; }
.logic-select, .type-select, .param-select {
  padding: 6px 8px; border: 1px solid var(--color-border); border-radius: var(--radius-sm);
  background: var(--color-bg); color: var(--color-text-primary); font-size: 12.5px;
}
.cond-row {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  padding: 8px; border: 1px solid var(--color-border);
  border-radius: var(--radius-sm); margin-bottom: 8px; background: var(--color-bg-subtle);
}
.type-select { flex: none; min-width: 150px; }
.param { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; color: var(--color-text-secondary); }
.param input {
  width: 68px; padding: 5px 6px; border: 1px solid var(--color-border);
  border-radius: var(--radius-sm); font: inherit; background: var(--color-bg);
  color: var(--color-text-primary);
}
.row-del {
  margin-left: auto; background: none; border: none; cursor: pointer;
  color: var(--color-text-tertiary); font-size: 13px; padding: 2px 6px;
}
.row-del:hover { color: var(--color-danger); }
.add-btn {
  background: none; border: 1px dashed var(--color-border); border-radius: var(--radius-sm);
  color: var(--color-text-secondary); font-size: 12.5px; padding: 7px 10px; cursor: pointer; width: 100%;
}
.hint { font-size: 12px; color: var(--color-text-tertiary); line-height: 1.7; margin: 14px 0 0; }
/* 字段下的小注：warn 变体用风险色（满仓警示），普通变体留给将来的说明文字 */
.field-note { font-size: 12px; line-height: 1.6; font-style: normal; margin-top: 2px; }
.field-note.warn { color: var(--color-danger); }
.error { font-size: 12.5px; color: var(--color-danger); margin: 10px 0 0; }
.sheet-foot {
  display: flex; justify-content: flex-end; gap: 10px;
  padding: 12px 20px calc(12px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--color-border);
}
.btn { padding: 9px 18px; border-radius: var(--radius-sm); font: inherit; cursor: pointer; border: 1px solid var(--color-border); }
.btn.ghost { background: none; color: var(--color-text-secondary); }
.btn.primary { background: var(--color-accent); border-color: var(--color-accent); color: var(--color-text-on-accent); }
.btn.primary:disabled { opacity: .6; cursor: default; }

.sheet-enter-active, .sheet-leave-active { transition: opacity .18s ease, transform .22s ease; }
.sheet-enter-from, .sheet-leave-to { opacity: 0; }
@media (min-width: 640px) {
  .sheet-enter-from .edit-sheet, .sheet-leave-to .edit-sheet { transform: translateX(30px); }
}
@media (max-width: 639px) {
  .sheet-enter-from .edit-sheet, .sheet-leave-to .edit-sheet { transform: translateY(40px); }
}
</style>
