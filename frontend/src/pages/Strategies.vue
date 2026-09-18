
<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import {
  listStrategies,
  runBacktest,
  startPaper,
  stopPaper,
  deleteStrategy,
  getPaperOverview
} from '../api/strategy.js'

const router = useRouter()
const strategies = ref([])
const loading = ref(false)
const error = ref('')
const backtestStatus = ref('')
const backtestOutput = ref('')
const backtestLoadingId = ref(null)
const paperLoadingId = ref(null)
/** 模拟盘状态：strategyId → 总览条目（一次请求取回所有策略）。 */
const paperInfo = ref({})
const pendingDeleteId = ref(null) // 正在就地二次确认删除的策略 id
const confirmButton = ref(null)

// 响应形状（Result 信封 vs 裸 DTO）由 api/request.js 的响应拦截器统一处理，
// 信封会被剥掉，所以这里一律直接读 res.data —— 不再需要本地 unwrap()。

// 只认后端返回的中文业务提示，其次是本地的 fallback。
// 不再回落到 e?.message —— 那是 axios 的英文原文
// （如 "Request failed with status code 500"），会直接泄漏给用户且没说怎么恢复。
const errorMessage = (e, fallback) =>
  e?.response?.data?.message || fallback

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

/**
 * 模拟盘状态（跨策略一次取回）：列表里直接显示**决策来源 / 净值与收益 / 今天动没动**。
 *
 * <p>不点进详情页就看不到"这条在跑什么、赚没赚"，正是"用户不知道有这个功能"的一半原因。
 * 读失败**不挡住列表**：策略列表本身已经加载好了，模拟盘那几行字降级为不显示即可。
 */
const loadPaperInfo = async () => {
  try {
    const res = await getPaperOverview()
    const list = Array.isArray(res.data) ? res.data : []
    const map = {}
    for (const item of list) {
      map[item.strategyId] = item
    }
    paperInfo.value = map
  } catch (e) {
    paperInfo.value = {}
  }
}

/** 列表里的一句话总览：还没评估过就直说，不摆 N/A。 */
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
const goDashboard = () => router.push('/dashboard')
const goPaper = () => router.push('/paper')

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

// 删除是不可撤销的破坏性操作，必须二次确认。
// 这里不用 window.confirm（无法样式化、部分内嵌环境会直接屏蔽），
// 也不引入任何组件库，直接在卡片里就地展开「确认删除 / 取消」。
// 用函数 ref 记住确认按钮：同一时刻只会渲染一个（v-if 保证），
// v-for 里的字符串 ref 会变成数组，拿不到单个 DOM 节点。
const setConfirmButton = (el) => {
  // 元素卸载时会回调 null；忽略它，避免在"确认行从 A 挪到 B"时
  // 用 null 覆盖掉刚挂上的新按钮引用（focus() 打在已经卸载的节点上是无害的空操作）
  if (el) confirmButton.value = el
}

const askRemove = async (item) => {
  pendingDeleteId.value = item.id
  // 确认行渲染完就把焦点交给「确认删除」，
  // 否则原按钮被替换后焦点会掉回 body，键盘用户会丢失位置。
  await nextTick()
  confirmButton.value?.focus()
}

const cancelRemove = () => {
  pendingDeleteId.value = null
}

const confirmRemove = (item, event) => {
  // 「确认删除」与被替换掉的「删除」位置相同，
  // 鼠标连击的第二次点击（detail > 1）不算确认，避免习惯性双击把策略删掉。
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
  <div class="app-layout">
    <header>
      <h1>策略库</h1>
      <div class="header-actions">
        <button type="button" class="nav-btn" @click="goPaper">模拟盘总览</button>
        <button type="button" class="nav-btn" @click="goDashboard">← 返回</button>
      </div>
    </header>

    <main>
      <p v-if="error" class="error" role="alert">{{ error }}</p>

      <!-- 状态互斥，可见顺序为：加载中 → 出错（上面的 error）→ 空 → 有数据。
           加载失败时列表必为空，所以空状态要排除 error：
           否则会同时出现"没拿到数据"和"暂无策略"，让用户误以为确实没有策略。 -->
      <div v-if="loading" class="empty">加载中...</div>
      <div v-else-if="!error && strategies.length === 0" class="empty">暂无策略</div>

      <section v-else-if="strategies.length > 0" class="strategy-list">
        <div v-for="item in strategies" :key="item.id" class="strategy-card">
          <!-- 「进入详情」是个动作：用原生 button 才能被 Tab 到、用 Enter/Space 激活。
               原先的 <div @click> 指针可达但键盘完全到不了。
               按钮内部只用行内元素（span），避免 button 里嵌块级元素。 -->
          <button type="button" class="strategy-info" @click="goDetail(item.id)">
            <span class="strategy-title">
              <strong>{{ item.name }}</strong>
              <span class="symbol-code">{{ item.symbol }}</span>
              <span class="paper-badge" :class="{ enabled: item.paperEnabled }">
                {{ item.paperEnabled ? '模拟盘中' : '未启动' }}
              </span>
              <!-- 决策来源：两条路的结论含义不同（规则条件成立 vs 委员会吵出来的），
                   列表里就要能区分，不然得逐条点进去才知道 -->
              <span
                v-if="paperInfo[item.id]"
                class="paper-badge mode"
                :class="{ agent: paperInfo[item.id].decisionMode === 'agent' }"
              >
                {{ modeLabel(paperInfo[item.id].decisionMode) }}
              </span>
            </span>
            <span class="strategy-meta num">
              <span>更新: {{ formatTime(item.updatedAt) }}</span>
              <span v-if="item.lastBacktestAt">最近回测: {{ formatTime(item.lastBacktestAt) }}</span>
            </span>
            <!-- 在跑什么、赚没赚：不点进详情也要看得见 -->
            <span v-if="paperSummary(item)" class="strategy-paper num">{{ paperSummary(item) }}</span>
            <span v-if="paperInfo[item.id]?.lastSettlement" class="strategy-paper num">
              最近一次结算 {{ paperInfo[item.id].lastSettlement.tradeDate }}：{{
                paperInfo[item.id].lastSettlement.sentence
              }}
            </span>
          </button>

          <div class="strategy-actions">
            <button type="button" class="btn-detail" @click="goDetail(item.id)">详情</button>
            <button
              type="button"
              class="btn-backtest"
              :disabled="backtestLoadingId === item.id"
              @click="handleBacktest(item)"
            >
              {{ backtestLoadingId === item.id ? '回测中...' : '回测' }}
            </button>
            <button
              type="button"
              class="btn-paper"
              :class="{ running: item.paperEnabled }"
              :disabled="paperLoadingId === item.id"
              @click="togglePaper(item)"
            >
              {{ item.paperEnabled ? '停止模拟盘' : '启动模拟盘' }}
            </button>

            <!-- 删除的二次确认：就地展开，不弹窗。破坏性动作要先说明后果再执行。 -->
            <template v-if="pendingDeleteId === item.id">
              <span class="confirm-hint">删除后无法恢复，确认删除？</span>
              <button
                :ref="setConfirmButton"
                type="button"
                class="btn-delete"
                @click="confirmRemove(item, $event)"
              >
                确认删除
              </button>
              <button type="button" class="btn-cancel" @click="cancelRemove">取消</button>
            </template>
            <button v-else type="button" class="btn-delete" @click="askRemove(item)">删除</button>
          </div>
        </div>
      </section>

      <section v-if="backtestStatus || backtestOutput" class="backtest-result">
        <h2>{{ backtestStatus || '回测结果' }}</h2>
        <pre class="num">{{ backtestOutput }}</pre>
      </section>
    </main>
  </div>
</template>

<style scoped>
.app-layout {
  /* 移动浏览器地址栏会算进 100vh，底部会被顶出可视区；补 100dvh 兜底 */
  min-height: 100vh;
  min-height: 100dvh;
  background: var(--color-bg-page);
}
header {
  background: var(--color-bg-inverse);
  color: var(--color-text-inverse);
  padding: 16px 24px;
  /* iOS 独立模式（index.html 声明了 black-translucent）内容会顶到状态栏下，补顶部安全区 */
  padding-top: calc(16px + env(safe-area-inset-top, 0px));
  display: flex;
  justify-content: space-between;
  align-items: center;
}
header h1 { margin: 0; font-size: 20px; }
.header-actions { display: flex; gap: 8px; }
.nav-btn {
  /* 深色导航上的半透明胶囊：语义层没有"深底上的浮起表面"这个角色，
     用 color-mix 从 --color-text-inverse 派生，避免写死半透明白色。
     不支持 color-mix 时该声明失效、背景回落为透明，白字直接压在深色导航上
     仍是 17.06:1，不会出现读不清的文字。 */
  background: color-mix(in srgb, var(--color-text-inverse) 15%, transparent);
  border: none;
  color: var(--color-text-inverse);
  padding: 6px 14px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
}
.nav-btn:hover { background: color-mix(in srgb, var(--color-text-inverse) 25%, transparent); }

main { max-width: 820px; margin: 0 auto; padding: 24px 16px; }
.strategy-list { display: flex; flex-direction: column; gap: 12px; }
.strategy-card {
  background: var(--color-bg-surface);
  border-radius: var(--radius-lg);
  padding: 14px 18px;
  box-shadow: var(--shadow-1);
}
/* 整块可点击的"进入详情"区域改成原生 button，需要显式重置按钮默认样式 */
.strategy-info {
  display: block;
  width: 100%;
  background: none;
  border: none;
  padding: 0;
  font: inherit;
  color: inherit;
  text-align: inherit;
  cursor: pointer;
}
.strategy-title { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.strategy-title strong { font-size: 16px; }
.symbol-code { font-size: 13px; color: var(--color-text-muted); font-weight: normal; }
/* 徽章文字对浅底 4.97 / 4.98:1 达标；状态另有文字（模拟盘中/未启动），不靠颜色单独表意。
   语义层没有"危险/成功的浅色底"令牌，底色统一用 --color-bg-subtle，色相交给文字与边框。 */
.paper-badge {
  font-size: 11px;
  padding: 2px 7px;
  border-radius: var(--radius-pill);
  background: var(--color-bg-subtle);
  /* "未启动"是普通关闭态而非错误：原来用危险红会把中性状态渲染成故障提示
     （危险色用在非破坏性状态上）。关闭态用中性色，只有启用才用成功色。 */
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border-strong);
}
.paper-badge.enabled {
  color: var(--color-success);
  background: var(--color-success-soft);
  border-color: var(--color-success-mark);
}
/* 决策来源：不是"好/坏"，是"谁做的决定"，所以用主色而不是成功/危险色 */
.paper-badge.mode.agent {
  color: var(--color-accent);
  border-color: var(--color-accent);
}
/* 模拟盘那两行小字（净值/收益、最近一次结算）：整块都是 button，
   所以样式作用在行内元素上，且必须允许换行（长句子不能被裁掉） */
.strategy-paper {
  display: block;
  margin-top: 6px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.6;
  text-align: left;
}
.strategy-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 6px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.strategy-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border);
}
.strategy-actions button {
  padding: 5px 12px;
  border: none;
  border-radius: var(--radius-sm);
  font-size: 13px;
  cursor: pointer;
}
.strategy-actions button:disabled { opacity: 0.5; cursor: not-allowed; }
/* 白字对 --color-accent 为 6.16:1（旧品牌蓝只有 4.10:1，不达 AA） */
.btn-detail { background: var(--color-accent); color: var(--color-text-on-accent); }
/* 回测沿用原来的紫色：语义层没有"次级动作"色，只能取原始色板 --c-purple-700，白字 6.94:1 */
.btn-backtest { background: var(--c-purple-700); color: var(--color-text-on-accent); }
/* 旧的橙色、绿色配白字只有 2.38:1 / 2.27:1，都不达 AA */
.btn-paper { background: var(--color-warning); color: var(--color-text-on-accent); }
.btn-paper.running { background: var(--color-success); }
/* 危险色用在破坏性动作（删除）上，语义正确 */
.btn-delete { background: var(--color-danger); color: var(--color-text-on-accent); }
/* 取消是中性动作，用中性色而不是危险色。
   这里的选择器要比上面的 `.strategy-actions button`（border: none）更具体，
   否则边框会被那条规则覆盖掉。 */
.strategy-actions .btn-cancel {
  background: var(--color-bg-subtle);
  color: var(--color-text-primary);
  /* 控件边界需 ≥3:1（WCAG 1.4.11） */
  border: 1px solid var(--color-border-control);
}
.confirm-hint { font-size: 13px; color: var(--color-text-secondary); }

/* 旧灰字对灰底仅 2.54:1 */
.empty { color: var(--color-text-muted); text-align: center; padding: 48px 16px; font-size: 14px; }
.error { color: var(--color-danger); font-size: 13px; margin-bottom: 12px; }

.backtest-result {
  margin-top: 20px;
  background: var(--color-bg-inverse);
  border-radius: var(--radius-lg);
  padding: 16px 18px;
  box-shadow: var(--shadow-1);
}
.backtest-result h2 { margin: 0 0 10px; font-size: 15px; color: var(--color-text-inverse); }
.backtest-result pre {
  margin: 0;
  color: var(--color-text-inverse-muted);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow: auto;
}
</style>
