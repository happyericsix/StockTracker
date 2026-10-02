<script setup>
import { ref, onMounted, onUnmounted, nextTick, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getHistory, getMinuteKline, getStock } from '../api/stock.js'
import { getStockEvents, getStockRead } from '../api/news.js'
import { getSentiment } from '../api/market.js'
import { anchorDateFor as anchorOnAxis } from '../utils/newsAnchor.js'
import { useMarketStatus } from '../composables/useMarketStatus.js'
import AppIcon from '../components/AppIcon.vue'
import * as echarts from 'echarts'

const route = useRoute()
const router = useRouter()

// ============ 状态 ============
const symbol = ref((route.params.symbol || '600519').toUpperCase())

// 周期：day / week / month / minute
const period = ref('day')
const periods = [
  { label: '日K', value: 'day' },
  { label: '周K', value: 'week' },
  { label: '月K', value: 'month' },
  { label: '分时', value: 'minute' }
]

// 分钟 K 周期：1/5/15/30/60
const minutePeriod = ref(5)
const minuteOptions = [
  { label: '1分', value: 1 },
  { label: '5分', value: 5 },
  { label: '15分', value: 15 },
  { label: '30分', value: 30 },
  { label: '60分', value: 60 }
]

// K 线显示根数（不同周期给不同默认）
const range = ref(180)
const ranges = computed(() => {
  if (period.value === 'day') return [
    { label: '1月', value: 30 }, { label: '3月', value: 90 },
    { label: '6月', value: 180 }, { label: '1年', value: 365 },
    { label: '2年', value: 730 }, { label: '全部', value: 1000 }
  ]
  if (period.value === 'week') return [
    { label: '半年', value: 26 }, { label: '1年', value: 52 },
    { label: '3年', value: 156 }, { label: '5年', value: 260 },
    { label: '10年', value: 520 }, { label: '全部', value: 1000 }
  ]
  if (period.value === 'month') return [
    { label: '1年', value: 12 }, { label: '3年', value: 36 },
    { label: '5年', value: 60 }, { label: '10年', value: 120 },
    { label: '20年', value: 240 }, { label: '全部', value: 1000 }
  ]
  // minute
  return [
    { label: '1天', value: 48 }, { label: '2天', value: 96 },
    { label: '5天', value: 240 }, { label: '1周', value: 480 }
  ]
})

// 图表类型：candlestick / line / ohlc
const chartType = ref('candlestick')
const chartTypes = [
  { label: '蜡烛', value: 'candlestick' },
  { label: '折线', value: 'line' },
  { label: 'OHLC', value: 'ohlc' }
]

// 技术指标摘要（不画历史曲线，只显示当前值）
const indicators = ref(null)

const loading = ref(false)
const error = ref('')
const stockInfo = ref(null)
const lastUpdate = ref('')

// ============ 事件区（N5b）============
// 事件不是"与数据并列的另一块内容"，而是**对这张图的注解**：它锚定在时间轴上，
// 与下方事件流双向联动。设计依据与取舍见
// docs/superpowers/specs/2026-09-19-stock-detail-news-layout-design.md
const events = ref([])
const eventsError = ref('')
const eventsLoaded = ref(false)
// 后台还在抓取/补解读 → 继续轮询。**必须来自后端**，不能用"转了多久"猜：
// 猜的版本在"模型其实已经失败"时会永远转下去，而用户唯一的动作就是干等。
const eventsAnalyzing = ref(false)
const eventsAnalyzedCount = ref(0)
const eventsPendingCount = ref(0)
// 「当前怎么看」：后端读完这批事件之后给出的一段连贯判断。
// 它才是回答"所以呢"的那一块 —— 逐条方向标签把综合的责任推回给了用户，
// 而用户的原话是"你要去阅读实时的新闻去更新你的想法，而不是一个新闻一个想法"。
const stockRead = ref('')
const stockReadLoading = ref(false)
/** 第一次综合解读已经问过了（用来区分"还没问"与"问了但没拿到"） */
const stockReadAttempted = ref(false)
/** 已经补问过一次。不设这个的话，"后端确实给不出结论"会变成无限重试（每次都花钱） */
const stockReadRetried = ref(false)
const showAllEvents = ref(false)
const activeEventId = ref(null)
// 图表实际绘制用的交易日序列（切片之后）。事件锚点必须落在这条轴上 ——
// ECharts 的 markLine 拿一个轴上不存在的 xAxis 值会**直接不渲染**（不报错）。
const chartDates = ref([])
const EVENT_PREVIEW = 3

/**
 * 轮询间隔与上限。
 *
 * <p>间隔 2 秒是"看得见在动"的下限：再快只是浪费请求（后端一批解读要 3~6 秒），
 * 再慢会让"已解读 3/12"这种进度看起来像卡住了。
 *
 * <p>上限 40 次（≈80 秒）是**兜底而不是预期**：正常情况下 `analyzing` 一翻 false
 * 就停了。设上限是因为"后端说还在解读、但解读其实永远不会来"（模型挂了、
 * 线程池满了）这种情况真实存在 —— 那时前端必须自己收手，不能让用户看着一个
 * 转不完的圈。超时后不再轮询，但已经拿到的内容照常显示。
 */
const EVENT_POLL_INTERVAL_MS = 2000
const EVENT_POLL_MAX_ROUNDS = 40

/**
 * 市场状态：今天开不开市、图上的"更新"是哪一天。
 *
 * <p>与首页共用同一个模块级单例（`useMarketStatus`），所以从首页点进来时
 * **不会再发一次请求**。加这一层是因为原来图表的副标题写的是
 * "更新: <行情日>"，而休市日那个日期其实是**上一个交易日** ——
 * 用户看到"更新 2026-09-20"会以为这是今天的数据，而他看的其实是周五的收盘价。
 */
const market = useMarketStatus()
// 模板里只有**顶层**绑定会被自动解包。`market.restNotice` 是一个 ref，
// 直接写进模板会渲染成 "[object Object]" —— 所以单独取一个顶层名给模板用。
const marketNotice = market.restNotice

/**
 * 鼠标离事件竖线多近才算"指到它"（像素，左右各算）。
 *
 * 竖线本身只有 2px 宽，按 2px 判定等于要求用户瞄准；14px 差不多是"看着指向那条线"的
 * 直觉范围，同时相邻事件（日线图上通常隔几十像素）也不会互相抢。
 * 同一天有多条事件时只算作一条（线本身也只有一条）。
 */
const EVENT_HIT_TOLERANCE_PX = 14
// 个股页看的是"这只票的背景"，不是"最近一周的资讯流"。
// 实测：30 天窗口下 600519 只有 11 条（公告 0 / 媒体 9 / 研报 2），
// 放宽到 90 天是 24 条（公告 8 / 媒体 3 / 研报 13）—— 用户反馈"公告、研报一个都没有"
// 有一半就是窗口太窄造成的。
const EVENT_WINDOW_DAYS = 90
// 图上最多标几个事件：30 天窗口下事件可能很多，全标会把 K 线糊住。
// 按影响强度取前 N 个 —— 强度是"可能改变中期逻辑"的判据，比时间更值得占用图面。
const MARK_LIMIT = 12

const chartRef = ref(null)
let chart = null

// ============ 设计令牌读取 ============
// ECharts 在 canvas 上绘制，不认 CSS 变量，必须在 JS 里取出 style.css 语义令牌的
// 实际色值再用。结果做缓存，避免每次重绘都读一遍计算样式。
// 取不到时回退到令牌定义值，保证图表照常渲染。
const tokenCache = new Map()
const token = (name, fallback) => {
  if (!tokenCache.has(name)) {
    let value = fallback
    try {
      value = getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
    } catch {
      value = fallback
    }
    tokenCache.set(name, value)
  }
  return tokenCache.get(name)
}

const handleResize = () => chart?.resize()

/**
 * 鼠标落在哪条事件竖线附近。
 *
 * <h3>为什么不用 ECharts 的 markLine 事件</h3>
 * `chart.on('mouseover', params => params.componentType === 'markLine')` 看着更直接，
 * 但它依赖"ECharts 是否给 markLine 元素派发交互事件"这个我**无法在本环境验证**的前提
 * （没有浏览器自动化）。万一不派发，表现是"鼠标移上去没反应" —— 静默失效，
 * 而这恰恰是用户要解决的问题。
 *
 * 所以改成用**容器上的原生鼠标事件 + ECharts 的坐标换算**：命中判定完全由我们自己的
 * 数据决定（`markGroups` 就是画标记用的同一份），从构造上不可能与图上的线不一致。
 *
 * 三种情况都算"没指到任何一条"：没有画任何标记、指针不在绘图区内、离所有竖线都太远。
 */
const nearestMarkedEvent = (event) => {
  if (!chart || !chartRef.value || !markGroups.value.length) return null
  const rect = chartRef.value.getBoundingClientRect()
  const pixelX = event.clientX - rect.left
  const pixelY = event.clientY - rect.top
  // y 也要判：否则在工具栏/图例那一片高度上移动鼠标也会命中竖线
  if (!chart.containPixel({ gridIndex: 0 }, [pixelX, pixelY])) return null

  let best = null
  for (const group of markGroups.value) {
    const lineX = chart.convertToPixel({ xAxisIndex: 0 }, group.anchor)
    if (typeof lineX !== 'number' || Number.isNaN(lineX)) continue
    const distance = Math.abs(lineX - pixelX)
    if (distance <= EVENT_HIT_TOLERANCE_PX && (!best || distance < best.distance)) {
      // 返回**代表事件**：与竖线的颜色、点击跳转的目标同源，不会出现"指到蓝线、高亮另一条"
      best = { item: group.representative, distance }
    }
  }
  return best ? best.item : null
}

const onChartMouseMove = (event) => {
  const hit = nearestMarkedEvent(event)
  const id = hit ? hit.id : null
  if (hoveredEventId.value !== id) hoveredEventId.value = id
}

const onChartMouseLeave = () => {
  hoveredEventId.value = null
}

/** 点竖线：滚到列表里对应的那条，并把它设为选中。 */
const onChartClick = (event) => {
  const hit = nearestMarkedEvent(event)
  if (!hit) return
  focusEvent(hit)
}

const disposeChart = () => {
  if (chart) {
    chart.dispose()
    chart = null
  }
  window.removeEventListener('resize', handleResize)
}

// ============ 工具函数 ============
const calcMA = (data, n) => {
  const result = []
  for (let i = 0; i < data.length; i++) {
    if (i < n - 1) result.push('-')
    else {
      let sum = 0
      for (let j = i - n + 1; j <= i; j++) sum += +data[j].close
      result.push(+(sum / n).toFixed(2))
    }
  }
  return result
}

// 简易技术指标计算（基于已有 K 线，不调新接口）
const calcIndicators = (data) => {
  if (data.length < 20) return null
  const closes = data.map(d => +d.close)

  // RSI(14)
  const rsi = (() => {
    let gains = 0, losses = 0
    for (let i = closes.length - 14; i < closes.length; i++) {
      const diff = closes[i] - closes[i - 1]
      if (diff > 0) gains += diff
      else losses -= diff
    }
    if (gains + losses === 0) return 50
    const rs = gains / 14 / (losses / 14 || 1e-9)
    return +(100 - 100 / (1 + rs)).toFixed(2)
  })()

  // MACD(12, 26, 9)
  const ema = (arr, n) => {
    const k = 2 / (n + 1)
    const out = [arr[0]]
    for (let i = 1; i < arr.length; i++) out.push(arr[i] * k + out[i - 1] * (1 - k))
    return out
  }
  const ema12 = ema(closes, 12)
  const ema26 = ema(closes, 26)
  const dif = +(ema12[ema12.length - 1] - ema26[ema26.length - 1]).toFixed(3)
  // 简化：DIF/DEA 用最近 9 个 DIF 的 EMA 近似
  const difArr = ema12.map((v, i) => v - ema26[i])
  const deaArr = ema(difArr, 9)
  const dea = +deaArr[deaArr.length - 1].toFixed(3)
  const hist = +((dif - dea) * 2).toFixed(3)

  // 布林带(20, 2)
  const n = 20
  const slice = closes.slice(-n)
  const ma = slice.reduce((a, b) => a + b, 0) / n
  const variance = slice.reduce((a, b) => a + (b - ma) ** 2, 0) / n
  const std = Math.sqrt(variance)
  const upper = +(ma + 2 * std).toFixed(2)
  const middle = +ma.toFixed(2)
  const lower = +(ma - 2 * std).toFixed(2)

  return { rsi, macd: { dif, dea, hist }, bollinger: { upper, middle, lower } }
}

// ============ 事件区（N5b）：锚点、标记与归因 ============

// 只认后端返回的中文业务提示，其次是本地 fallback（与 Strategies.vue 同一约定）。
// 不回落到 e?.message —— 那是 axios 的英文原文，会直接泄漏给用户。
const errorMessage = (e, fallback) => e?.response?.data?.message || fallback


const SOURCE_META = {
  1: { icon: 'megaphone', label: '公告' },
  2: { icon: 'newspaper', label: '媒体' },
  3: { icon: 'doc', label: '研报' },
  4: { icon: 'globe', label: '舆情' }
}
const sourceMeta = (level) => SOURCE_META[level] || { icon: 'doc', label: '资讯' }

const eventCounts = computed(() => {
  const counts = { notice: 0, media: 0, report: 0 }
  for (const item of events.value) {
    if (item.sourceLevel === 1) counts.notice += 1
    else if (item.sourceLevel === 2) counts.media += 1
    else if (item.sourceLevel === 3) counts.report += 1
  }
  return counts
})

/**
 * 事件日期 → 图表 x 轴上的交易日。
 * 三条规则与它们的实测依据都在 `utils/newsAnchor.js` 里（那段逻辑有独立验证用例）。
 */
const anchorDateFor = (event) => anchorOnAxis(event.publishedAt, chartDates.value)

// 落在当前图表时间范围内的 (事件, 锚点日期) —— 这是"图上标记数 == 列表条数"的同一份口径。
const anchoredEvents = computed(() => {
  const dates = chartDates.value
  if (!dates.length) return []
  const first = dates[0]
  const last = dates[dates.length - 1]
  return events.value
    .map(event => ({ event, anchor: anchorDateFor(event) }))
    .filter(({ anchor }) => anchor && anchor >= first && anchor <= last)
})

const IMPACT_RANK = { high: 0, medium: 1, low: 2 }

/**
 * 图上实际会被标注的那些事件（按影响强度取前 `MARK_LIMIT` 条），**按日期分组**。
 *
 * 每组给出一个 `representative`（代表事件 = 该日期里**列表序号最小**的那条，也就是
 * 列表里最新的那条）。竖线的颜色、悬停显示的标题、点击跳转的目标全都用这个代表 ——
 * 三处必须同源，否则会出现"蓝线点进去高亮的是另一条"这种对不上的情况
 * （第一版就是这样：卡片颜色按列表序号取，竖线颜色按"该日期影响最高"取，两者可能不是同一条）。
 *
 * 它同时是**画标记**和**悬停命中**的唯一依据 —— 两处各算一份的话，
 * 会出现"看到的线上有一条、但鼠标移上去没反应"（或反过来）这类最难查的不一致。
 */
const markGroups = computed(() => {
  const ranked = [...anchoredEvents.value].sort(
    (a, b) => (IMPACT_RANK[a.event.impactLevel] ?? 3) - (IMPACT_RANK[b.event.impactLevel] ?? 3))
  const byDate = new Map()
  for (const { event, anchor } of ranked.slice(0, MARK_LIMIT)) {
    if (!byDate.has(anchor)) byDate.set(anchor, [])
    byDate.get(anchor).push(event)
  }
  const groups = []
  for (const [anchor, events] of byDate) {
    const representative = events.reduce((best, item) =>
      (eventNumberOf(item) || Number.MAX_SAFE_INTEGER) <
      (eventNumberOf(best) || Number.MAX_SAFE_INTEGER) ? item : best, events[0])
    groups.push({ anchor, events, representative })
  }
  return groups
})

const buildMarkLine = () => {
  const focusId = hoveredEventId.value ?? activeEventId.value
  const data = []
  for (const { anchor, events, representative } of markGroups.value) {
    const focused = events.some(e => e.id === focusId)
    const color = colorForEvent(representative)
    data.push({
      xAxis: anchor,
      eventDate: anchor,
      lineStyle: {
        color,
        type: directionLineTypeForGroup(events),
        width: focused ? 4 : 2,
        opacity: focused ? 1 : 0.75
      },
      // **不写标签**：第一版写的是"列表序号"，用户的反应是"线上为什么还有数字，看不懂" ——
      // 让人滚到列表里数到第 5 条才知道那是什么，等于把"这是哪条新闻"换成了另一道题。
      // 而且图上只标最重要的 12 条，编号不连续（1,2,3,5,7…），看起来更像随便的数值。
      label: { show: false }
    })
  }
  return { symbol: ['none', 'none'], silent: true, animation: false, data }
}

/** 只更新标记（merge 模式），不重画整张图 —— 重画会把用户的 dataZoom 区间重置掉。 */
const updateMarks = () => {
  if (!chart) return
  try {
    chart.setOption({ series: [{ markLine: buildMarkLine() }] })
  } catch (e) {
    // 标记是增强信息，出错绝不能影响图表本身
    console.warn('事件标记更新失败', e)
  }
}

const changeValue = computed(() => {
  const raw = stockInfo.value?.changePercent
  if (raw === null || raw === undefined || raw === '') return null
  const value = Number.parseFloat(raw)
  return Number.isFinite(value) ? value : null
})

/**
 * 图表副标题里"价格"那一项的回退值：行情接口拿不到时用最后一根 K 线的收盘价。
 * 提成 ref 是因为副标题现在要能**被重算**（见 `chartSubtext`）——
 * 原来它写在 `renderChart` 的局部变量里，只有重画整张图才会更新。
 */
const lastClosePrice = ref('')

/**
 * 图表副标题最前面那段"现在是什么状态"（未开盘 / 午间休市 / 已收盘 / 今日休市）。
 * 判断都在 `utils/marketStatus.js` 的 `sessionBadgeFor` 里（纯函数，有独立用例）。
 */
const sessionBadge = market.sessionBadge

/**
 * 图表副标题。做成 computed 的唯一理由是**它能随市场状态变化重算**：
 *
 * 「更新」那一项必须说清**这是哪一天的价**。休市日腾讯回的是上一交易日的收盘价，
 * 原来直接渲染行情源给的日期，用户看到"更新 2026-09-20"会以为这是今天的数据 ——
 * 而他看的其实是周五的收盘价（用户报的正是这个）。所以休市/未开盘时改写成
 * "最近交易日 09-18（周五）收盘"，并在前面补一句当前状态。
 */
const chartSubtext = computed(() => {
  const priceStr = stockInfo.value?.price
    ? `¥${stockInfo.value.price}`
    : (lastClosePrice.value ? `¥${lastClosePrice.value}` : 'N/A')
  const change = changeValue.value
  const updateLabel = market.quoteIsFromAnotherDay.value
    ? `最近交易日 ${market.quoteDateLabel.value} 收盘`
    : `更新: ${stockInfo.value?.lastUpdated || lastUpdate.value}`
  return [
    sessionBadge.value,
    `当前价: ${priceStr}`,
    change === null ? null : `涨跌: ${change >= 0 ? '+' : ''}${change.toFixed(2)}%`,
    updateLabel
  ].filter(Boolean).join('  |  ')
})

/**
 * 市场状态比图表晚到时，只更新标题（merge 模式），**不重画整张图** ——
 * 重画会把用户的 dataZoom 区间重置掉，而"我只是想知道这是哪天的价"不该有那种副作用。
 */
watch(chartSubtext, (value) => {
  if (!chart) return
  try {
    chart.setOption({ title: { subtext: value } })
  } catch (e) {
    // 副标题是增强信息，出错绝不能影响图表本身
    console.warn('副标题更新失败', e)
  }
})

/**
 * 一句话归因：**确定性规则，不是 LLM 输出**。
 * 可复现、可解释；用它回答"它为什么动"，而不是再写一段模型生成的文字。
 */
const attributionNote = computed(() => {
  if (!eventsLoaded.value) return ''
  const list = events.value
  if (!list.length) return '近 30 天无重大事件，波动大概率跟板块/大盘走'
  const bull = list.filter(e => e.direction === '利好').length
  const bear = list.filter(e => e.direction === '利空').length
  if (!bull && !bear) return '近期事件多为程序性披露，未指向明确方向'
  const change = changeValue.value
  if (change === null) return `近期事件偏${bull >= bear ? '多' : '空'}`
  if ((change >= 0 && bull > bear) || (change < 0 && bear > bull)) {
    return '与近期事件方向一致'
  }
  if ((change >= 0 && bear > bull) || (change < 0 && bull > bear)) {
    return '与近期事件方向相反，可能已提前反应或另有驱动'
  }
  return '多空事件相当，方向不明'
})

const previewEvents = computed(() =>
  showAllEvents.value ? events.value : events.value.slice(0, EVENT_PREVIEW))

// ============ 事件色板：图上的竖线 ↔ 列表里的条目 ============
//
// 用户的反馈："折线图上我看到你画了时间戳上对应新闻的具体节点，但是这样去看很麻烦……
// 你可以用图例去说明这个是一个新闻发生，同时区分事件的方式你可以使用不同颜色的线，
// 像是一根蓝线是对应新闻列中以蓝色为底色的一个新闻。"
//
// 颜色这一层表示**身份**（是哪一条），而不是方向；
// 方向改由**线型**承载（实线利好 / 虚线利空 / 点线未判），图上的信息不丢；
// 色板不含红绿，避免与"红=利好、绿=利空"撞义（见 style.css 的 --color-event-*）。
//
// ⚠️ 第一版还给竖线加了"列表序号"标签，用户直接反馈"线上为什么还有数字，看不懂" ——
// 让人滚到列表里数到第 5 条才知道那是什么，等于把问题换成了另一道题。
// 所以现在**线上不带任何文字**，靠"悬停竖线直接显示是哪条新闻"解决识别问题，
// 颜色只作粗定位（5 色循环，同色时靠悬停区分）。
const EVENT_COLOR_TOKENS = ['--color-event-1', '--color-event-2', '--color-event-3',
                            '--color-event-4', '--color-event-5']

/** 事件在列表里的序号（1 起）；找不到返回 0。颜色按它循环，所以同一条在两处颜色一致。 */
const eventNumberOf = (event) => {
  const index = events.value.findIndex(item => item.id === event.id)
  return index < 0 ? 0 : index + 1
}

/** 事件颜色：按列表序号取模，保证同一条在图上和列表里是同一个颜色。 */
const colorForNumber = (number) =>
  token(EVENT_COLOR_TOKENS[(Math.max(number, 1) - 1) % EVENT_COLOR_TOKENS.length], '#003eb3')

const colorForEvent = (event) => colorForNumber(eventNumberOf(event))

/** 方向 → 线型。颜色已经让给"身份"，方向就靠线型表达。 */
const directionLineType = (direction) => {
  if (direction === '利好') return 'solid'
  if (direction === '利空') return 'dashed'
  return 'dotted'
}

/** 一天里有多条事件时，线型取"第一条的方向"。 */
const directionLineTypeForGroup = (list) => directionLineType(list[0]?.direction)

// 悬停/选中的事件：图上竖线、图例下方的说明条、列表条目三处共用它来高亮。
const hoveredEventId = ref(null)

/** 当前"正在被指到"的事件：悬停优先，其次是点选。 */
const focusedEvent = computed(() => {
  const id = hoveredEventId.value ?? activeEventId.value
  if (id == null) return null
  return events.value.find(item => item.id === id) || null
})

/** 竖线被指到时，在图表下方显示"这是哪条新闻"。数字标签换成这个之后才真的省事。 */
const focusCaption = computed(() => {
  const event = focusedEvent.value
  if (!event) return ''
  const date = formatEventTime(event.publishedAt)
  const source = event.sourceName || sourceMeta(event.sourceLevel).label
  return `${event.title}　·　${source}　·　${date}　·　${directionLabel(event)}`
})

// 可信度徽章配色：沿用语义色板，不复用涨跌色（红=利好/绿=利空已有含义，
// 可信度再叠上去同色会打架）。分档缺失（未评估）时不渲染徽章而不是显示灰色"未知"。
const credibilityClass = (grade) => {
  if (grade === '高') return 'cred-high'
  if (grade === '较高') return 'cred-good'
  if (grade === '中') return 'cred-mid'
  return 'cred-low'
}

const directionClass = (direction) => {
  if (direction === '利好') return 'bull'
  if (direction === '利空') return 'bear'
  return 'flat'
}

const directionLabel = (event) => {
  // spec §6 的硬约束：direction 为 null 是"信息不足"，**不是**"中性"。
  // 用灰色"中性"糊过去会把"没判"说成"判了没影响"，是两件事。
  if (event.direction) return event.direction
  return event.analyzed ? '不判断方向' : '未解读'
}

const formatEventTime = (iso) => {
  const text = String(iso || '')
  if (!text) return '时间未知'
  return text.slice(5, 16).replace('T', ' ')
}

// 列表 → 图：点条目即切换选中（同一条再点一次取消），图上那条线随之加粗
const focusEvent = (event) => {
  activeEventId.value = activeEventId.value === event.id ? null : event.id
  updateMarks()
}

/** 列表条目被指到：显示说明条 + 把图上那条线加粗（指针在 DOM 上，重画安全）。 */
const hoverEvent = (event, on) => {
  hoveredEventId.value = on ? event.id : null
  updateMarks()
}

// 图 → 列表：选中并滚到对应条目。整段都用防御式写法 —— 联动是增强，
// 它出问题不该让页面报错（而且这里没有浏览器自动化可验证，只能靠代码本身稳）。
const focusEventAndReveal = (event) => {
  if (!event) return
  activeEventId.value = event.id
  showAllEvents.value = true          // 目标可能被折叠在"展开全部"后面
  updateMarks()
  nextTick(() => {
    try {
      document.getElementById(`event-${event.id}`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    } catch (e) {
      console.warn('事件定位失败', e)
    }
  })
}

// ============ 数据加载 ============

/**
 * 拉该股的事件时间轴。
 *
 * <h3>2026-09-20：这条链路现在是"列表先到、解读随后长出来"</h3>
 * 用户的原话：*"本来新闻获取的就慢，用户点进来过了几秒才有新闻内容，
 * 结果出来以后还要再等一会才有你的 ai 分析"*。
 *
 * <p>后端已经改成"立刻返回库内内容 + 后台补抓取与解读"，所以这里的职责变成三件：
 * ① 拿到第一批就渲染（不再等"全部就绪"）；
 * ② 只要后端说还在解读（`analyzing`）就每 2 秒问一次，把新落库的解读贴上来；
 * ③ 综合解读与它**并行**发起 —— 顺序发等于把两次等待叠起来，而用户等的正是这个。
 *
 * ⚠️ 与 K 线**并行且软失败**：资讯不该拖慢图表，更不该让图表失败。
 * 拿不到就明说拿不到（`eventsError`），而不是显示成"这段时间没有资讯"——
 * 两者的用户含义完全相反。
 */
const loadEvents = async (sym) => {
  stopEventPolling()
  events.value = []
  eventsError.value = ''
  eventsLoaded.value = false
  eventsAnalyzing.value = false
  eventsAnalyzedCount.value = 0
  eventsPendingCount.value = 0
  showAllEvents.value = false
  activeEventId.value = null
  stockRead.value = ''
  stockReadAttempted.value = false
  stockReadRetried.value = false

  // 综合解读与事件流并行：后端 `/stock-read` 内部会等"在途的补解读"结束再合成，
  // 所以并行不但安全，而且是它**能多早就多早**出现的唯一排法。
  // （顺序发的话，两次等待是叠加的：先等解读、再等合成。）
  void loadStockRead(sym)

  try {
    const res = await getStockEvents(sym, EVENT_WINDOW_DAYS)
    applyTimeline(res?.data)
  } catch (e) {
    eventsError.value = errorMessage(e, '资讯加载失败，稍后重试')
  } finally {
    eventsLoaded.value = true
    updateMarks()      // 事件到位后补标记（图表可能已经先画完了）
  }
  // 首屏就已有解读（热标的）时，"补问"这一支可能立刻成立
  maybeRetryStockRead()
  if (eventsAnalyzing.value) {
    startEventPolling(sym)
  }
}

/** 把后端那一版时间轴贴到界面上（首屏与每次轮询共用同一条路径）。 */
const applyTimeline = (data) => {
  // 兼容裸数组：改造前这条接口返回的就是 List<NewsEventResponse>。
  // 不为"旧后端 + 新前端"这种组合写太多代码，但也不该让它在页面上炸掉。
  const payload = Array.isArray(data) ? { events: data } : (data || {})
  events.value = Array.isArray(payload.events) ? payload.events : []
  eventsAnalyzing.value = payload.analyzing === true
  eventsAnalyzedCount.value = Number(payload.analyzedCount) || 0
  eventsPendingCount.value = Number(payload.pendingCount) || 0
}

/**
 * 第一次综合解读**空手而归**时，等解读落地之后补问一次。
 *
 * <h3>为什么必须有这一条（真机联调实测出来的）</h3>
 * 冷标的上后端要先把上游抓回来（实测 300750 约 6 秒）才能开始解读，而
 * `/stock-read` 的等待是有上限的（后端 10 秒）。超时那一刻库里**一条解读都没有**，
 * 后端按约定返回 `data = null`（"这次给不出结论"是合法状态）——
 * 于是用户看到一份完整的列表，却**永远**没有"当前怎么看"，因为前端只问了一次。
 *
 * <p>所以：第一次没拿到不算数。等轮询报出"有解读了 / 解读结束了"再问一次，
 * 那时结论才有依据。加了这一条之后，冷标的最坏情况从"永远没有结论"变成
 * "列表先出来，结论晚十几秒出现" —— 后者正是用户要的。
 *
 * <p>只补问一次（`stockReadRetried`），避免后端确实给不出结论时反复花钱。
 */
const maybeRetryStockRead = () => {
  if (stockRead.value || stockReadLoading.value || stockReadRetried.value) return
  if (!stockReadAttempted.value) return          // 第一次还没回来，谈不上"补问"
  if (eventsAnalyzing.value) return              // 还在补解读：此刻问也是白问
  if (!eventsAnalyzedCount.value) return         // 一条解读都没有：问了也只会拿到 null
  stockReadRetried.value = true
  void loadStockRead(symbol.value)
}

let eventPollTimer = null
let eventPollRounds = 0

const stopEventPolling = () => {
  if (eventPollTimer) {
    clearTimeout(eventPollTimer)
    eventPollTimer = null
  }
  eventPollRounds = 0
}

/**
 * 每 2 秒问一次"解读补到哪了"。
 *
 * <p>轮询用 `setTimeout` 递归而不是 `setInterval`：请求本身要时间，
 * 固定间隔会在慢的时候堆起一串并发请求，而且它们的返回顺序不保证 ——
 * 表现是"解读数字来回跳"。递归版保证同一时刻只有一个在途。
 */
const startEventPolling = (sym) => {
  stopEventPolling()
  const tick = async () => {
    eventPollRounds += 1
    if (eventPollRounds > EVENT_POLL_MAX_ROUNDS) {
      // 兜底收手：后端说还在解读、但已经过了 80 秒。继续轮询只会一直转圈。
      eventsAnalyzing.value = false
      // 收手之前再看一眼：这期间已经落库的解读仍然值得拿去问一次结论
      maybeRetryStockRead()
      return
    }
    try {
      const res = await getStockEvents(sym, EVENT_WINDOW_DAYS)
      // 用户可能已经切到别的股票：这一版数据必须丢掉，否则会把 A 的新闻贴到 B 的页面上
      if (sym !== symbol.value) return
      applyTimeline(res?.data)
      updateMarks()
    } catch (e) {
      // 轮询失败不改状态：已经有内容在屏幕上，一次网络抖动不该让它消失
      eventsAnalyzing.value = false
      maybeRetryStockRead()
      return
    }
    // 解读落地（或全部结束）的那一刻，是"补问一次结论"最合适的时机
    maybeRetryStockRead()
    if (eventsAnalyzing.value) {
      eventPollTimer = setTimeout(tick, EVENT_POLL_INTERVAL_MS)
    }
  }
  eventPollTimer = setTimeout(tick, EVENT_POLL_INTERVAL_MS)
}

/**
 * 个股「当前怎么看」。后端内部要先保证事件已经取到并解读过，所以这一步本身就偏慢
 * （首次一只新股票 3~6 秒），**单独一个 loading 态**，不挡事件列表的渲染。
 *
 * <p>2026-09-20：它现在与 {@link loadEvents} **并行**发起（原来是等事件就绪之后才发，
 * 等于把两次等待串起来），超时/失败照旧整块隐藏 —— "这次给不出结论"是合法状态。
 */
const loadStockRead = async (sym) => {
  stockReadLoading.value = true
  stockReadAttempted.value = true
  try {
    const res = await getStockRead(sym, EVENT_WINDOW_DAYS)
    if (sym !== symbol.value) return      // 已经切走，别把上一只票的结论贴上来
    stockRead.value = typeof res?.data === 'string' ? res.data : ''
  } catch (e) {
    // 综合解读是增强信息：拿不到就整块不显示，不报错打扰用户
    stockRead.value = ''
  } finally {
    stockReadLoading.value = false
    // 没拿到 + 解读已经落地 → 补问一次（见 maybeRetryStockRead 的说明）
    maybeRetryStockRead()
  }
}

// 竞态防护：快速切股/切周期/切显示范围时多个 loadData 并发，
// 慢的旧请求后到会把新图覆盖掉（旧代码只防了事件轮询，没防 K 线主链路）。
// 每次 loadData 自增序号，响应回来先确认自己仍是最新一次再动状态。
let loadDataSeq = 0

const sentiment = ref(null)

// 散户情绪（旁路）：与 K 线并行拉、软失败、竞态防护与 loadData 同款。
// 竞态序号共用 loadDataSeq——切股后旧情绪不得盖到新股票上。
const loadSentiment = async (sym, seq) => {
  try {
    const res = await getSentiment(sym)
    if (seq === loadDataSeq && res.data?.ok) {
      sentiment.value = res.data.data || null
    }
  } catch {
    if (seq === loadDataSeq) sentiment.value = null
  }
}

// 验证结论的展示文案：三条指数的 verdict 里挑最有信息量的一条说；
// 全是"样本不足"时如实说"还在攒数据"——这比藏起来好（用户会以为我们没做验证）
const sentimentVerdict = computed(() => {
  const v = sentiment.value?.validation
  if (!v) return '情绪对照验证暂不可用'
  const parts = []
  if (v.focus?.verdict && v.focus.verdict !== '样本不足') parts.push(`关注指数：${v.focus.verdict}`)
  if (v.score?.verdict && v.score.verdict !== '样本不足') parts.push(`评分：${v.score.verdict}`)
  if (v.desire?.verdict && v.desire.verdict !== '样本不足') parts.push(`参与意愿：${v.desire.verdict}`)
  if (parts.length) return parts[0]
  return '对照验证样本收集中（约需 20 个交易日），暂不下结论'
})

const loadData = async (sym) => {
  const seq = ++loadDataSeq
  const isStale = () => seq !== loadDataSeq
  error.value = ''
  loading.value = true
  stockInfo.value = null
  indicators.value = null
  lastUpdate.value = ''
  disposeChart()
  // 资讯与 K 线并行拉：图表不必等它（实测个股事件在库里没有时要走"实时兜底"，会慢）
  loadEvents(sym)
  loadSentiment(sym, seq)

  try {
    // 行情：分钟模式下用最近一个数据点的 close
    const stockRes = await getStock(sym).catch(() => null)
    if (isStale()) return
    stockInfo.value = stockRes?.data ?? null

    let list = []
    if (period.value === 'minute') {
      const res = await getMinuteKline(sym, minutePeriod.value).catch(() => null)
      if (isStale()) return
      // 后端返回的是 List<DailyStockResponse>（裸 DTO），拦截器不会动它，res.data 直接是 list
      const data = Array.isArray(res?.data) ? res.data : []
      list = data.map(r => ({
        date: r.date, open: r.open, close: r.close, high: r.high, low: r.low, volume: r.volume
      }))
    } else {
      // 拉 size=1000 让前端按 range 切；分页 size 给 1000 一次性拉够
      const historyRes = await getHistory(sym, 0, 1000, period.value).catch(() => null)
      if (isStale()) return
      list = historyRes?.data?.content || []
    }

    if (list.length === 0) {
      if (isStale()) return
      error.value = period.value === 'minute'
        ? '暂无分钟K线数据（仅支持 A 股，分钟 K 由 akshare 提供）'
        : '暂无 K 线数据，请检查股票代码或稍后重试'
      loading.value = false
      return
    }

    // 后端返回正序（旧->新），直接取最近 range 根
    const data = list.slice(-range.value)

    // 分钟 K 模式下没有实时 quote，stockInfo 拿不到 close
    if (period.value === 'minute' && data.length > 0) {
      const last = data[data.length - 1]
      stockInfo.value = stockInfo.value || {}
      stockInfo.value.price = last.close
    }

    indicators.value = calcIndicators(data)
    lastUpdate.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })

    await nextTick()
    if (isStale()) return
    if (!chartRef.value) return

    chart = echarts.init(chartRef.value)
    renderChart(sym, data)
    // 悬停/点击竖线的联动**不挂在 ECharts 的 markLine 事件上**，而是挂在容器的原生事件上
    // （见 nearestMarkedEvent 的说明：那个前提在本环境无法验证，静默失效的代价太大）。
    // 监听器在 onMounted 里只装一次，所以这里不重复 addEventListener。
    window.addEventListener('resize', handleResize)
  } catch (e) {
    if (isStale()) return
    // 只采用后端返回的消息；去掉 axios 的 e.message —— 那是英文
    // ("Request failed with status code 500")，会原样显示给用户。
    // 兜底文案必须说清怎么恢复，不能只有"失败了"。
    error.value = e?.response?.data?.message
      || 'K 线数据加载失败，请检查股票代码是否正确，或确认网络后重试'
  } finally {
    if (!isStale()) loading.value = false
  }
}

// ============ 图表渲染 ============
const renderChart = (sym, data) => {
  const dates = data.map(d => d.date)
  // 事件锚点要靠它做交易日吸附，必须在建 series 之前就位
  chartDates.value = dates
  const isMinute = period.value === 'minute'

  // 涨红跌绿（A 股习惯）。原值 #ff4d4f / #52c41a 对白只有 3.27 / 2.27，不达标。
  // 用 --color-gain / --color-loss 而不是 danger / success：涨跌是"行情方向"，
  // 与"成功/危险"是两个语义，虽然恰好落到同一批色相（对白 5.57 / 5.59）。
  const COLOR_UP = token('--color-gain', '#cf1322')
  const COLOR_DOWN = token('--color-loss', '#237804')

  // series 数据准备
  const series = []
  const legend = []

  if (chartType.value === 'candlestick') {
    const kline = data.map(d => [+d.open, +d.close, +d.low, +d.high])
    series.push({
      name: 'K线', type: 'candlestick', data: kline,
      tooltip: { valueFormatter: (v) => (Array.isArray(v) ? v.map(x => (+x).toFixed(3)).join(' / ') : (+v).toFixed(3)) },
      itemStyle: {
        color: COLOR_UP, color0: COLOR_DOWN,
        borderColor: COLOR_UP, borderColor0: COLOR_DOWN
      }
    })
    legend.push('K线')
  } else if (chartType.value === 'line') {
    const closes = data.map(d => +d.close)
    // 分时/分钟K的 close 就是该时间点的最新成交价（当时的价格）
    const lineName = isMinute ? '价格' : '收盘价'
    const lineColor = token('--color-accent', '#0958d9')
    series.push({
      name: lineName, type: 'line', data: closes,
      tooltip: { valueFormatter: (v) => (+v).toFixed(3) },
      smooth: true, showSymbol: false,
      lineStyle: { width: 2, color: lineColor },
      // 图例图标由两笔拼成：线段取 lineStyle.stroke，空心圆点取 itemStyle.fill。
      // 只写 lineStyle.color 时圆点会去 ECharts 默认色板拿色（#5070dd / #b6d634…），
      // 图例上就多出一个曲线里没有的颜色；tooltip 前的小色点同源，也一样跑偏。
      itemStyle: { color: lineColor },
      // 原 rgba(22,119,255,0.08) 是主色淡影；令牌层没有半透明主色，
      // 图表底色是白色，直接用主色浅底令牌等价
      areaStyle: { color: token('--color-accent-soft', '#e6f4ff') }
    })
    legend.push(lineName)
  } else { // ohlc
    const ohlc = data.map(d => [+d.open, +d.close, +d.low, +d.high])
    series.push({
      name: 'OHLC', type: 'candlestick', data: ohlc,
      tooltip: { valueFormatter: (v) => (Array.isArray(v) ? v.map(x => (+x).toFixed(3)).join(' / ') : (+v).toFixed(3)) },
      renderItem: (params, api) => {
        const open = api.value(0)
        const close = api.value(1)
        const low = api.value(2)
        const high = api.value(3)
        const halfWidth = Math.max(2, api.size([1, 0])[0] * 0.3)
        const isUp = close >= open
        const color = isUp ? COLOR_UP : COLOR_DOWN
        return {
          type: 'group',
          children: [
            // 高低竖线
            { type: 'line', shape: { x1: api.coord([api.value(0), low])[0], y1: api.coord([api.value(0), low])[1],
                                       x2: api.coord([api.value(0), high])[0], y2: api.coord([api.value(0), high])[1] },
              style: { stroke: color, lineWidth: 1 } },
            // open 水平线（左）
            { type: 'line', shape: { x1: api.coord([api.value(0), open])[0] - halfWidth, y1: api.coord([api.value(0), open])[1],
                                       x2: api.coord([api.value(0), open])[0], y2: api.coord([api.value(0), open])[1] },
              style: { stroke: color, lineWidth: 1 } },
            // close 水平线（右）
            { type: 'line', shape: { x1: api.coord([api.value(0), close])[0], y1: api.coord([api.value(0), close])[1],
                                       x2: api.coord([api.value(0), close])[0] + halfWidth, y2: api.coord([api.value(0), close])[1] },
              style: { stroke: color, lineWidth: 1 } }
          ]
        }
      }
    })
    legend.push('OHLC')
  }

  // 均线（仅日/周/月 K 显示，分钟 K 噪点多意义不大）
  if (!isMinute && data.length >= 5) {
    const ma5 = calcMA(data, 5)
    const ma10 = calcMA(data, 10)
    const ma20 = calcMA(data, 20)
    const ma60 = data.length >= 60 ? calcMA(data, 60) : null
    const push = (name, arr, color) => series.push({
      name, type: 'line', data: arr, smooth: true, showSymbol: false,
      tooltip: { valueFormatter: (v) => (+v).toFixed(3) },
      lineStyle: { width: 1, color },
      // 同上：图例圆点走 itemStyle.fill，不写就会变成色板色
      itemStyle: { color }
    })
    // 均线是"数据系列"色，走 --color-chart-* 这一组独立令牌
    // （不要借用表示状态的 warning-mark 等；原 MA60 的青色 #13c2c2 对白仅 2.21:1，
    //  连图形对象的 3:1 都不到，故换成 3.46:1 的绿）
    push('MA5', ma5, token('--color-chart-ma5', '#d46b08')); legend.push('MA5')
    push('MA10', ma10, token('--color-chart-ma10', '#0958d9')); legend.push('MA10')
    push('MA20', ma20, token('--color-chart-ma20', '#722ed1')); legend.push('MA20')
    if (ma60) { push('MA60', ma60, token('--color-chart-ma60', '#389e0d')); legend.push('MA60') }
  }

  // 布林带（仅日/周/月）
  if (!isMinute && data.length >= 20) {
    const n = 20
    const closes = data.map(d => +d.close)
    const upper = [], middle = [], lower = []
    for (let i = 0; i < closes.length; i++) {
      if (i < n - 1) { upper.push('-'); middle.push('-'); lower.push('-'); continue }
      const slice = closes.slice(i - n + 1, i + 1)
      const m = slice.reduce((a, b) => a + b, 0) / n
      const v = slice.reduce((a, b) => a + (b - m) ** 2, 0) / n
      const s = Math.sqrt(v)
      upper.push(+(m + 2 * s).toFixed(2))
      middle.push(+m.toFixed(2))
      lower.push(+(m - 2 * s).toFixed(2))
    }
    const bollFmt = { valueFormatter: (v) => (+v).toFixed(3) }
    // 布林带原为粉色 #eb2f96，对白 3.90:1 —— 满足图形对象的 3:1，
    // 所以它原本就是达标的，不该被换成中性灰（换掉会让布林带失去可辨识度）。
    // 现收进图表系列令牌组，1px 细线由 opacity 控制视觉权重。
    const bollBand = token('--color-chart-boll', '#eb2f96')
    const bollMid = token('--color-text-secondary', '#595959')
    series.push({ name: 'BOLL上', type: 'line', data: upper, showSymbol: false, tooltip: bollFmt, lineStyle: { width: 1, color: bollBand, opacity: 0.6 }, itemStyle: { color: bollBand } })
    series.push({ name: 'BOLL中', type: 'line', data: middle, showSymbol: false, tooltip: bollFmt, lineStyle: { width: 1, color: bollMid, type: 'dashed' }, itemStyle: { color: bollMid } })
    series.push({ name: 'BOLL下', type: 'line', data: lower, showSymbol: false, tooltip: bollFmt, lineStyle: { width: 1, color: bollBand, opacity: 0.6 }, itemStyle: { color: bollBand } })
    legend.push('BOLL上', 'BOLL中', 'BOLL下')
  }

  // 成交量
  const volumes = data.map((d, i) => [
    i, +d.volume || 0,
    +d.close >= +d.open ? 1 : -1
  ])

  const subPeriodLabel = isMinute ? `${minutePeriod.value}分K` : { day: '日K', week: '周K', month: '月K' }[period.value]
  const stockName = stockInfo.value?.name || sym
  // 副标题里的价格回退值要先就位：chartSubtext 是 computed，它在 setOption 那一刻被读
  lastClosePrice.value = data.length ? data[data.length - 1].close : ''

  // 事件标记挂在**第一条** series 上（K线/价格/OHLC，它才是主图）。
  // 挂到不存在的 series 上不会报错、只是什么都不显示，所以这里显式判断。
  if (series.length) series[0].markLine = buildMarkLine()

  // 副标题只放"读数"。事件条数与归因放在紧邻图表下方的事件区标题里：
  // ECharts 的 title 不会自动折行，塞长了会把图挤变形（而事件数与归因是整句话）。
  //
  // ⚠️ 内容走 `chartSubtext` 这个 computed，**不在这里就地拼**：
  // 市场状态是并行拉的，图表往往先画完、状态后到；就地拼的话那行字会一直停在
  // "更新 <某天>"，而它恰恰是本次要改对的那一句（见下面的 watch）。
  chart.setOption({
    title: {
      text: `${stockName} ${subPeriodLabel}`,
      subtext: chartSubtext.value,
      left: 'center',
      top: 0,
      textStyle: { fontSize: 16, fontWeight: 600 },
      subtextStyle: { fontSize: 12 }
    },
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross' },
      backgroundColor: token('--color-bg-inverse', '#1a1a2e'), borderWidth: 0,
      textStyle: { color: token('--color-text-inverse', '#ffffff'), fontSize: 12 }, confine: true
    },
    legend: { data: legend, top: 46, textStyle: { fontSize: 12 } },
    grid: [
      { left: 50, right: 20, top: 82, height: '58%' },
      { left: 50, right: 20, top: '76%', height: '12%' }
    ],
    xAxis: [
      { type: 'category', data: dates, scale: true, boundaryGap: false,
        axisLine: { onZero: false }, splitLine: { show: false },
        axisLabel: { formatter: (v) => v.substring(5), fontSize: 11 },
        axisPointer: { z: 100 }
      },
      { type: 'category', gridIndex: 1, data: dates, scale: true, boundaryGap: false,
        axisLine: { onZero: false }, axisTick: { show: false },
        axisLabel: { show: false }, splitLine: { show: false } }
    ],
    yAxis: [
      { scale: true, splitArea: { show: true }, splitLine: { lineStyle: { type: 'dashed' } } },
      { scale: true, gridIndex: 1, splitNumber: 2, axisLabel: { show: false },
        axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false } }
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 50, end: 100,
        moveOnMouseMove: false, zoomOnMouseWheel: true },
      { show: true, xAxisIndex: [0, 1], type: 'slider', top: '94%', height: 18, start: 50, end: 100,
        handleStyle: { color: token('--color-accent', '#0958d9') } }
    ],
    series: [
      ...series,
      { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volumes,
        itemStyle: { color: (p) => (p.data[2] > 0 ? COLOR_UP : COLOR_DOWN) } }
    ]
  }, { notMerge: true })
}

// ============ 事件 ============
const onSubmit = () => {
  const s = symbol.value.trim().toUpperCase()
  if (!s) return
  router.replace(`/chart/${encodeURIComponent(s)}`)
}

const onPeriodChange = (p) => {
  period.value = p
  // 切到分钟时给个默认 5 天 (5min K 一天约 48 根)
  if (p === 'minute' && range.value > 480) range.value = 240
  loadData(symbol.value)
}

const onMinuteChange = (m) => {
  minutePeriod.value = m
  loadData(symbol.value)
}

const onChartTypeChange = (t) => {
  chartType.value = t
  loadData(symbol.value)
}

const onRangeChange = (value) => {
  range.value = value
  loadData(symbol.value)
}

onMounted(() => {
  const container = chartRef.value
  // 只装一次：容器这个 DOM 节点在组件生命周期内不会换（ECharts 每次重画只是重绘 canvas）
  container?.addEventListener('mousemove', onChartMouseMove)
  container?.addEventListener('mouseleave', onChartMouseLeave)
  container?.addEventListener('click', onChartClick)
  // 市场状态与 K 线并行拉：它只用来把"这是哪天的数据"说清楚，不该拖慢图
  void market.load()
  loadData(route.params.symbol)
})

onUnmounted(() => {
  // 轮询必须停：它是一个活的定时器，组件销毁后继续跑会一直打后端，
  // 而且回填的是已经没人在看的 ref（用户切页几次就堆几个）
  stopEventPolling()
  const container = chartRef.value
  container?.removeEventListener('mousemove', onChartMouseMove)
  container?.removeEventListener('mouseleave', onChartMouseLeave)
  container?.removeEventListener('click', onChartClick)
  disposeChart()
})

watch(() => route.params.symbol, (newSym) => {
  if (newSym && newSym.toUpperCase() !== symbol.value) {
    symbol.value = newSym.toUpperCase()
    loadData(symbol.value)
  }
})

// 指标颜色辅助（同样走令牌，不留字面颜色）
// RSI 的超买/超卖是"值得注意的状态"，不是成功/失败：超买给警示色、超卖给主色。
// 原来用 danger/success 会和涨跌共用色相，让"绿=跌"与"绿=超卖"撞义。
// 中性值用主文字色（保持数据可读的权重），不借交互蓝——主色在本应用表示"可点击"。
// 状态含义另有「超买/超卖/中性」文字承载，颜色只是强化。
const rsiColor = (v) => v == null
  ? token('--color-text-muted', '#666666')
  : v >= 70 ? token('--color-warning', '#ad4e00') : v <= 30 ? token('--color-accent', '#0958d9') : token('--color-text-primary', '#1a1a2e')
// MACD 柱是"方向"（正负），与 K 线涨跌同语义 → 用 gain/loss
const macdColor = (h) => h == null
  ? token('--color-text-muted', '#666666')
  : h >= 0 ? token('--color-gain', '#cf1322') : token('--color-loss', '#237804')
</script>

<template>
  <div class="kline-page">
    <div class="page-head">
      <!-- 本页没有右侧动作，返回键与标题同组靠左（全局 .page-head 是 space-between，
           只有两个子元素时会把标题推到最右） -->
      <button type="button" class="btn quiet" @click="router.back()">← 返回</button>
      <h1>K 线图</h1>
    </div>

    <div class="toolbar">
      <form class="symbol-form" @submit.prevent="onSubmit">
        <!-- 视觉上不需要外露标签，用 aria-label 给可访问名称（placeholder 不能当标签） -->
        <input
          v-model="symbol"
          placeholder="输入股票代码（如 600519）"
          class="symbol-input"
          aria-label="股票代码"
        />
        <button type="submit" class="btn-primary">查看</button>
      </form>

      <div class="control-row">
        <div class="control-group">
          <span class="control-label">周期</span>
          <div class="btn-group">
            <button
              v-for="p in periods"
              :key="p.value"
              class="pill"
              :class="{ active: period === p.value }"
              @click="onPeriodChange(p.value)"
            >{{ p.label }}</button>
          </div>
        </div>

        <div v-if="period === 'minute'" class="control-group">
          <span class="control-label">分钟</span>
          <div class="btn-group">
            <button
              v-for="m in minuteOptions"
              :key="m.value"
              class="pill"
              :class="{ active: minutePeriod === m.value }"
              @click="onMinuteChange(m.value)"
            >{{ m.label }}</button>
          </div>
        </div>

        <div class="control-group">
          <span class="control-label">类型</span>
          <div class="btn-group">
            <button
              v-for="t in chartTypes"
              :key="t.value"
              class="pill"
              :class="{ active: chartType === t.value }"
              @click="onChartTypeChange(t.value)"
            >{{ t.label }}</button>
          </div>
        </div>
      </div>

      <div class="range-bar">
        <span class="control-label">显示</span>
        <button
          v-for="r in ranges"
          :key="r.value"
          class="range-btn"
          :class="{ active: range === r.value }"
          @click="onRangeChange(r.value)"
        >{{ r.label }}</button>
      </div>
    </div>

    <main>
      <!-- 休市提示：图上的"更新"是 09-18，不说清楚就会被读成今天的数据。
           句子由后端拼好（连休几天、哪天回来），这里只决定版式。 -->
      <p v-if="marketNotice" class="market-notice" :class="`tone-${marketNotice.tone}`" role="status">
        <span class="market-badge">{{ marketNotice.badge }}</span>
        <span class="market-headline">{{ marketNotice.headline }}</span>
        <span v-if="marketNotice.detail" class="market-detail">{{ marketNotice.detail }}</span>
      </p>

      <div class="chart-stack">
        <p v-if="error" class="error-msg" role="alert">{{ error }}</p>
        <div v-if="loading && !chart" class="empty-state">加载中...</div>
        <div ref="chartRef" class="chart-container"></div>

        <!-- 图例：说明图上的竖线是什么、颜色与编号怎么读、线型的含义。
             紧贴图表（而不是放进右侧事件栏）—— 看图的当场就该能读到它。
             放在 DOM 而不是 ECharts 的 legend 里有两个理由：
             ① markLine 不是 series，进不了 ECharts 图例（硬塞一个空 series 会得到一个
                点了没反应、还会误导人的图例项）；
             ② canvas 里的字读屏软件读不到，而"这条线是什么"恰恰是必须被读到的信息。 -->
        <div v-if="events.length && chartDates.length" class="event-legend">
          <span class="legend-title">图例</span>
          <span class="legend-item">
            <span class="legend-swatch" aria-hidden="true"></span>
            图中竖线＝事件发生时间（最多标注最重要的 {{ MARK_LIMIT }} 条），线色与下方条目的左侧色条一致
          </span>
          <span class="legend-item">
            <span class="legend-line solid" aria-hidden="true"></span>利好
            <span class="legend-line dashed" aria-hidden="true"></span>利空
            <span class="legend-line dotted" aria-hidden="true"></span>未判方向
          </span>
          <span class="legend-hint">把鼠标移到竖线上即可看到是哪条新闻</span>
        </div>
        <!-- 悬停/选中那条的说明条：取代了原来"线上写编号"的做法 ——
             让人滚到列表里数到第 5 条才知道那是什么，等于把问题换成了另一道题。 -->
        <p v-if="focusCaption" class="event-caption">{{ focusCaption }}</p>
      </div>

      <div v-if="indicators" class="indicator-panel">
        <div class="indicator">
          <span class="ind-label">RSI(14)</span>
          <span class="ind-value num" :style="{ color: rsiColor(indicators.rsi) }">{{ indicators.rsi }}</span>
          <span class="ind-hint">{{ indicators.rsi >= 70 ? '超买' : indicators.rsi <= 30 ? '超卖' : '中性' }}</span>
        </div>
        <div class="indicator">
          <span class="ind-label">MACD</span>
          <span class="ind-value num">
            <span style="color: var(--color-chart-ma10)">DIF {{ indicators.macd.dif }}</span>
            <span style="color: var(--color-chart-ma20); margin-left: 6px">DEA {{ indicators.macd.dea }}</span>
          </span>
          <span class="ind-hint num" :style="{ color: macdColor(indicators.macd.hist) }">
            HIST {{ indicators.macd.hist >= 0 ? '+' : '' }}{{ indicators.macd.hist }}
          </span>
        </div>
        <div class="indicator">
          <span class="ind-label">BOLL(20)</span>
          <span class="ind-value num" style="color: var(--color-text-secondary)">
            {{ indicators.bollinger.lower }} / {{ indicators.bollinger.middle }} / {{ indicators.bollinger.upper }}
          </span>
          <span class="ind-hint">下轨 / 中轨 / 上轨</span>
        </div>
      </div>

      <!--
        事件区。它在 DOM 里排在指标面板**之前**，但桌面端的实际位置由 main 的
        grid-template-areas 决定（手机单列：图 → 事件 → 指标；桌面两列：图/指标在左、事件在右）。
        靠 areas 而不是嵌套 wrapper：wrapper 会让"手机端事件必须在指标之前"这条无法满足。
      -->
      <aside class="event-section" aria-labelledby="event-heading">
        <!-- 散户情绪（股吧聚合指数）：先当被验证的假设展示——验证结论必须与数值同屏，
             拿不到数据/验证时整卡隐藏（情绪是旁路，绝不占位空白卡） -->
        <div v-if="sentiment" class="sentiment-card" aria-label="散户情绪">
          <div class="sentiment-head">
            <h3>散户情绪</h3>
            <span class="sentiment-tag">未验证信号</span>
          </div>
          <div class="sentiment-metrics num">
            <span v-if="sentiment.desire?.latest != null">
              参与意愿 {{ sentiment.desire.latest.toFixed(0) }}<template v-if="sentiment.desire.change != null">（{{ sentiment.desire.change > 0 ? '+' : '' }}{{ sentiment.desire.change.toFixed(1) }}）</template>
            </span>
            <span v-if="sentiment.focus?.latest != null">关注 {{ sentiment.focus.latest.toFixed(0) }}</span>
            <span v-if="sentiment.score?.latest != null">千股千评 {{ sentiment.score.latest.toFixed(0) }}</span>
          </div>
          <p class="sentiment-verdict">
            {{ sentimentVerdict }}
          </p>
        </div>

        <div class="event-head">
          <h2 id="event-heading">事件与解读</h2>
          <p v-if="eventsError" class="event-error" role="alert">{{ eventsError }}</p>
          <!-- 首屏：骨架屏而不是一行"加载中"。用户的原话是"点进来过了几秒才有新闻内容"，
               而那几秒里原来是整块空白 —— 骨架屏至少把"这里马上会有东西"说清楚。 -->
          <div v-else-if="!eventsLoaded" class="event-skeleton" role="status" aria-label="正在获取资讯">
            <span class="skeleton-title">正在获取近期资讯…</span>
            <span v-for="n in EVENT_PREVIEW" :key="`sk-${n}`" class="skeleton-card" aria-hidden="true">
              <span class="skeleton-line short"></span>
              <span class="skeleton-line"></span>
            </span>
          </div>
          <template v-else-if="events.length">
            <p class="event-counts">
              近 {{ EVENT_WINDOW_DAYS }} 天 {{ events.length }} 条（公告 {{ eventCounts.notice }} / 媒体 {{ eventCounts.media }} / 研报 {{ eventCounts.report }}）
            </p>
            <!-- 后台仍在补解读：把进度说出来。没有这一句，用户看到的是
                 "列表出来了但一半没有解读"，会以为是坏数据而不是"还在生成"。 -->
            <p v-if="eventsAnalyzing" class="event-progress" role="status">
              正在解读 {{ eventsAnalyzedCount }}/{{ Math.min(events.length, eventsAnalyzedCount + eventsPendingCount) }} 条…
            </p>
            <p v-if="attributionNote" class="attribution">{{ attributionNote }}</p>
          </template>
        </div>

        <!-- 「当前怎么看」：一整段连贯判断，回答"读完这些新闻，这只票现在是什么情况"。
             拿不到时整块不显示（后端约定 data=null 表示"这次给不出结论"，这是合法状态）。 -->
        <div v-if="stockRead" class="stock-read">
          <p class="stock-read-label">当前怎么看</p>
          <p class="stock-read-text">{{ stockRead }}</p>
        </div>
        <!-- 等待态也要有形状：它在等的是"走完一次模型往返"，说清在等什么比一个转圈有用 -->
        <p v-else-if="stockReadLoading && events.length" class="stock-read-loading" role="status">
          正在综合近期资讯生成判断…（约需几秒）
        </p>

        <!-- 空态要给结论，不能只说"暂无数据"：「没有消息」本身是一条信息 -->
        <p v-if="eventsLoaded && !eventsError && !events.length" class="event-empty">
          近 {{ EVENT_WINDOW_DAYS }} 天没有公告、媒体或研报记录。<br>
          没有可验证的事件驱动时，波动通常来自板块或大盘，而不是这只票自己的消息。
        </p>

        <ul v-else-if="events.length" class="event-list">
          <li
            v-for="(event, index) in previewEvents"
            :key="event.id"
            :id="`event-${event.id}`"
            class="event-card"
            :class="{ active: activeEventId === event.id, hovered: hoveredEventId === event.id }"
            :style="{ borderLeftColor: colorForNumber(index + 1) }"
            @click="focusEvent(event)"
            @mouseenter="hoverEvent(event, true)"
            @mouseleave="hoverEvent(event, false)"
          >
            <div class="event-line">
              <span class="event-source">
                <AppIcon :name="sourceMeta(event.sourceLevel).icon" :size="13" :stroke-width="2" />
                {{ event.sourceName || sourceMeta(event.sourceLevel).label }}
              </span>
              <span class="event-time num">{{ formatEventTime(event.publishedAt) }}</span>
              <span v-if="event.freshnessLabel" class="event-fresh num">{{ event.freshnessLabel }}</span>
              <span
                v-if="event.credibilityGrade"
                class="event-credibility"
                :class="credibilityClass(event.credibilityGrade)"
                :title="(event.credibilityReasons || []).join('；')"
              >可信 {{ event.credibilityGrade }}</span>
              <span class="event-direction" :class="directionClass(event.direction)">
                {{ directionLabel(event) }}
              </span>
            </div>
            <p class="event-title">
              <a v-if="event.url" :href="event.url" target="_blank" rel="noopener noreferrer" @click.stop>{{ event.title }}</a>
              <template v-else>{{ event.title }}</template>
            </p>
            <!-- 传闻警示必须放在标题与解读之间：它改变的是"下面这段 AI 解读该带着多大怀疑看" -->
            <p v-if="event.rumorFlag" class="event-rumor" role="alert">
              传闻特征明显，未经证实，请以官方公告为准
              <span v-if="(event.credibilityReasons || []).length" class="rumor-why">{{ event.credibilityReasons[0] }}</span>
            </p>
            <p v-if="event.analyzed" class="event-summary">{{ event.plainSummary }}</p>
            <!-- ⚠️ 「还在解读」和「信息不足，不判断方向」是**两件事**，颜色和文案都必须分开：
                 后者是模型读过之后给的结论（spec §6：direction=null 表示信息不足，不是中性），
                 前者只是还没轮到它。混为一谈等于把"没问"说成"问过了"——
                 而本轮改造把解读移到了后台，那一瞬间会同时出现很多条未解读的卡片，
                 说错的话用户会直接得出"AI 读不出东西"的结论。 -->
            <p v-else-if="eventsAnalyzing" class="event-summary muted pending">
              <span class="pending-dot" aria-hidden="true"></span>正在解读这条…
            </p>
            <p v-else class="event-summary muted">信息不足，不判断方向</p>
            <details v-if="event.risks?.length || event.opportunities?.length" class="event-detail" @click.stop>
              <summary>风险 / 机会</summary>
              <!-- 用文字标题而不是颜色来区分两组：本应用里 红=利好、绿=利空 已经有明确
                   含义，若再让"红=风险、绿=机会"（西式惯例）叠上去，同一个颜色就有了两层
                   互相冲突的语义。这里机会用涨色、风险用警示色，并且都带文字标签。 -->
              <div v-if="event.opportunities?.length">
                <p class="detail-label opp">机会</p>
                <ul class="opp-list">
                  <li v-for="(text, i) in event.opportunities" :key="`o${i}`">{{ text }}</li>
                </ul>
              </div>
              <div v-if="event.risks?.length">
                <p class="detail-label risk">风险</p>
                <ul class="risk-list">
                  <li v-for="(text, i) in event.risks" :key="`r${i}`">{{ text }}</li>
                </ul>
              </div>
            </details>
          </li>
        </ul>

        <button
          v-if="events.length > EVENT_PREVIEW"
          type="button"
          class="event-toggle"
          @click="showAllEvents = !showAllEvents"
        >
          {{ showAllEvents ? '收起' : `展开全部 ${events.length} 条` }}
        </button>

        <!-- spec §10：任何 AI 输出都要带免责标识 -->
        <p class="event-notice">AI 生成内容，仅供参考，不构成投资建议</p>
      </aside>
    </main>
  </div>
</template>

<style scoped>
/* 本页 page-head 只有"返回 + 标题"两个左组元素，没有右侧动作：
   覆盖全局的 space-between，避免标题被推到最右 */
.page-head { justify-content: flex-start; }
.kline-page {
  min-height: 100vh;
  /* 移动端地址栏高度算进 100vh，会顶出底部，补 dvh 兜底 */
  min-height: 100dvh;
  background: var(--color-bg-page);
  display: flex;
  flex-direction: column;
}


.toolbar {
  background: var(--color-bg-surface);
  padding: 12px 16px;
  border-bottom: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex-shrink: 0;
}
.symbol-form { display: flex; gap: 8px; }
.symbol-input {
  flex: 1;
  padding: 8px 12px;
  /* 输入控件边界要 ≥3:1（1.4.11），原 #d9d9d9 对白只有 1.41:1 */
  border: 1px solid var(--color-border-control);
  border-radius: var(--radius-sm);
  font-size: 14px;
}
/* 原来在这里写了 outline: none，只用 1px 边框变色代替焦点环（且那个颜色
   对白 2.99:1）。现在交给全局 :focus-visible（2px 主色环 + 偏移），
   这里只保留边框变色作第二通道。 */
.symbol-input:focus-visible { border-color: var(--color-accent); }
.btn-primary {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border: none;
  padding: 0 18px;
  border-radius: var(--radius-sm);
  font-size: 14px;
  cursor: pointer;
}
.btn-primary:hover { background: var(--color-accent-hover); }

.control-row { display: flex; gap: 16px; flex-wrap: wrap; align-items: center; }
.control-group { display: flex; align-items: center; gap: 6px; }
/* 原 #888 对白 3.54:1，不达 AA */
.control-label { font-size: 12px; color: var(--color-text-secondary); margin-right: 2px; }
.btn-group { display: flex; gap: 4px; }
.pill {
  padding: 4px 10px;
  background: var(--color-bg-subtle);
  border: 1px solid transparent;
  border-radius: var(--radius-xl);
  font-size: 12px;
  color: var(--color-text-muted);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.pill:hover { border-color: var(--color-accent); color: var(--color-accent); }
.pill.active {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
}

.range-bar { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.range-btn {
  padding: 4px 12px;
  background: var(--color-bg-subtle);
  border: 1px solid transparent;
  border-radius: var(--radius-xl);
  font-size: 12px;
  color: var(--color-text-muted);
  cursor: pointer;
}
/* 原 #1677ff + 白字只有 4.10:1，不达 AA */
.range-btn.active {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
}

main {
  flex: 1;
  max-width: 1100px;
  width: 100%;
  margin: 0 auto;
  padding: 12px 16px;
  box-sizing: border-box;
  /* 单列（手机）：图 → 事件 → 指标。
     事件排在指标**之前**是有意的：指标是"当前读数"，事件是"为什么动"，
     而"为什么"才是用户点进来的问题。桌面端再靠 areas 把事件挪到右侧栏。
     用 grid-template-areas 而不是嵌套 wrapper —— wrapper 无法同时满足
     "手机端事件在指标之前"和"桌面端事件在右侧栏"这两个要求。 */
  display: grid;
  gap: 8px;
  grid-template-columns: minmax(0, 1fr);
  grid-template-areas:
    "notice"
    "chart"
    "events"
    "indicators";
}
/* 休市提示跨整幅宽度、永远排在最前：它要说的是"下面所有数字是哪一天的"，
   放到任何一栏里都会变成"只对那一栏成立"。 */
main > .market-notice { grid-area: notice; }
.chart-stack {  grid-area: chart;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.chart-container {
  background: var(--color-bg-surface);
  border-radius: var(--radius-lg);
  padding: 8px;
  /* 原为写死的 560px：667px 高的手机上，图 + 工具栏就吃掉一整屏，
     事件区被推到第二屏之后，而"最近有什么消息"是第一眼问题。
     dvh 与页面其它地方的安全区处理同一口径。 */
  height: clamp(320px, 45dvh, 560px);
  box-shadow: var(--shadow-1);
}
/* 原 #ff4d4f 对浅灰底仅 2.91:1 */
.error-msg { color: var(--color-danger); text-align: center; padding: 24px; font-size: 14px; }
/* 原 #999 对灰底 2.54:1 */
.empty-state { color: var(--color-text-muted); text-align: center; padding: 48px 24px; font-size: 14px; }

.indicator-panel {
  grid-area: indicators;
  background: var(--color-bg-surface);
  border-radius: var(--radius-lg);
  padding: 12px 16px;
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  box-shadow: var(--shadow-1);
}
.indicator {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 180px;
}
.ind-label { font-size: 12px; color: var(--color-text-secondary); }
.ind-value { font-size: 14px; font-weight: 600; }
.ind-hint { font-size: 11px; color: var(--color-text-muted); }

/* ============ 事件区（N5b）============ */

.event-section {
  grid-area: events;
  min-width: 0;
  background: var(--color-bg-surface);
  border-radius: var(--radius-lg);
  padding: 12px 16px;
  box-shadow: var(--shadow-1);
}
.event-head h2 {
  margin: 0;
  font-size: 15px;
  color: var(--color-text-primary);
}
.event-counts {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--color-text-secondary);
}
/* 归因是"对这张图的注解"，给它能被一眼读到的权重（不是 12px 灰字） */
.attribution {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--color-text-primary);
}
.event-empty, .event-notice {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-muted);
}
.event-error {
  margin: 8px 0 0;
  font-size: 13px;
  color: var(--color-danger);
}
.event-notice { font-size: 11px; }

/* 后台补解读的进度。它必须**看起来像进度**而不是错误：
   用的是次级文字色 + 一个呼吸点，不是红色、也不是警示橙。 */
.event-progress {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--color-text-secondary);
  display: flex;
  align-items: center;
  gap: 6px;
}
.event-progress::before {
  content: '';
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-accent);
  animation: news-pulse 1.2s var(--ease-out) infinite;
}
@keyframes news-pulse {
  0%, 100% { opacity: 0.25; }
  50% { opacity: 1; }
}
/* （动效偏好由 style.css 的全局 prefers-reduced-motion 兜底，这里不重复写） */

/* 骨架屏：首屏那几秒原来是整块空白，用户的原话是"点进来过了几秒才有新闻内容"。
   骨架**不冒充内容**（没有假标题、假日期），只说明"这里马上会有东西"。 */
.event-skeleton {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.skeleton-title {
  font-size: 12px;
  color: var(--color-text-muted);
}
.skeleton-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 10px;
  background: var(--color-bg-subtle);
}
.skeleton-line {
  height: 10px;
  border-radius: var(--radius-sm);
  background: var(--color-border);
  animation: news-pulse 1.4s var(--ease-out) infinite;
}
.skeleton-line.short { width: 38%; }

/* 「当前怎么看」：整段连贯判断，是这一块最该被先读到的东西，所以给它最重的视觉权重 */
.stock-read {
  margin-top: 10px;
  padding: 10px 12px;
  background: var(--color-bg-subtle);
  border-left: 3px solid var(--color-accent);
  border-radius: var(--radius-md);
}
.stock-read-label {
  margin: 0 0 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--color-accent);
}
.stock-read-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text-primary);
}
.stock-read-loading {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--color-text-muted);
}

.event-list {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.event-card {
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  padding: 8px 10px;
  cursor: pointer;
  transition: border-color var(--duration-fast) var(--ease-out),
              background var(--duration-fast) var(--ease-out);
}
.event-card:hover { border-color: var(--color-accent); }
/* 图上竖线被指到时高亮对应条目（与 hover 同一视觉，两个方向来的指到都一样） */
.event-card.hovered {
  border-color: var(--color-accent);
  border-left-width: 5px;
  background: var(--color-accent-soft);
}
/* 选中态：左侧色条加粗 + 浅底 + 边框，三个通道一起变 —— 颜色不是唯一提示。
   左侧色条的颜色由内联样式给出（= 该条在事件色板里的颜色），所以这里不覆盖它。 */
.event-card.active {
  border-color: var(--color-accent);
  border-left-width: 5px;
  background: var(--color-accent-soft);
}
/* ============ 图例（图上的竖线怎么读）============ */

.event-legend {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 14px;
  margin-bottom: 8px;
  padding: 6px 10px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  font-size: 11px;
  color: var(--color-text-secondary);
}
.legend-title { font-weight: 600; color: var(--color-text-primary); }
.legend-item { display: inline-flex; align-items: center; gap: 5px; }
/* 竖线样本：与图上 markLine 同一形态（细竖条），颜色用交互蓝只是示意"这里有颜色区分" */
.legend-swatch {
  display: inline-block;
  width: 3px;
  height: 13px;
  border-radius: 1px;
  background: var(--color-event-1);
}
/* 线型样本：用横线展示实线/虚线/点线，与图上竖线的线型一一对应 */
.legend-line {
  display: inline-block;
  width: 18px;
  height: 0;
  border-top-width: 2px;
  border-top-color: var(--color-text-muted);
  margin-left: 10px;
}
.legend-line:first-child { margin-left: 0; }
.legend-line.solid { border-top-style: solid; }
.legend-line.dashed { border-top-style: dashed; }
.legend-line.dotted { border-top-style: dotted; }
.legend-hint { color: var(--color-text-muted); }

/* 悬停/选中那条的说明条：一眼看到"这是哪条新闻"，不用去列表里数编号。
   它取代了原来"在线条上写编号"的做法 —— 那个方案要求用户滚到列表里数到第 5 条，
   等于把问题换成了另一道题。 */
.event-caption {
  margin: 6px 0 0;
  padding: 6px 10px;
  background: var(--color-accent-soft);
  border-left: 3px solid var(--color-accent);
  border-radius: var(--radius-sm);
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-primary);
}

/* ============ 事件卡片：颜色标识 + 悬停/选中联动 ============ */

.event-line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--color-text-muted);
}
.event-source {
  display: inline-flex;
  align-items: center;
  gap: 4px; color: var(--color-text-secondary); }
.event-direction {
  margin-left: auto;
  padding: 1px 6px;
  border: 1px solid currentColor;
  border-radius: var(--radius-pill);
  font-size: 11px;
}
/* 时效与可信度：轻量文本标签，不与"利好/利空"的方向徽章抢视觉权重 */
.event-fresh {
  font-size: 11px;
  color: var(--color-text-muted);
}
.event-credibility {
  padding: 1px 6px;
  border-radius: var(--radius-pill);
  font-size: 11px;
  border: 1px solid currentColor;
  cursor: help;  /* title 里带可信度理由（信源/措辞/印证），hover 可核对"为什么" */
}
.event-credibility.cred-high { color: var(--color-accent); }
.event-credibility.cred-good { color: var(--color-accent); }
.event-credibility.cred-mid  { color: var(--color-text-muted); }
.event-credibility.cred-low  { color: var(--color-warning); }
/* 传闻警示条：整卡最需要被看见的一条，用警示底色而不是小字 */
.event-rumor {
  margin: 6px 0 0;
  padding: 6px 8px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  line-height: 1.5;
  background: var(--color-warning-soft);
  color: var(--color-warning);
}
.event-rumor .rumor-why { display: block; font-size: 11px; opacity: 0.85; }
/* 与全站涨跌同色：红=利好、绿=利空。文字标签同时在场，颜色只是强化。 */
.event-direction.bull { color: var(--color-gain); }
.event-direction.bear { color: var(--color-loss); }
.event-direction.flat { color: var(--color-text-muted); }

.event-title {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--color-text-primary);
}
.event-title a { color: inherit; text-decoration: none; }
.event-title a:hover { text-decoration: underline; }
.event-summary {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--color-text-secondary);
}
.event-summary.muted { color: var(--color-text-muted); }
/* 未解读（后台还在跑）与"模型读过但判不出方向"必须一眼可分：
   后者是结论，前者只是等待。用一点次级文字色 + 一个呼吸点承载"它在动"。 */
.event-summary.pending {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--color-text-secondary);
}
.pending-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--color-text-placeholder);
  animation: news-pulse 1.2s var(--ease-out) infinite;
}


.event-detail { margin-top: 6px; font-size: 12px; }
.event-detail summary { cursor: pointer; color: var(--color-text-secondary); }
.detail-label { margin: 6px 0 0; font-size: 12px; font-weight: 600; }
/* 机会用涨色（与 direction=利好 同义），风险用**警示色**而不是跌色 ——
   跌色（绿）在本应用已经表示"利空方向"，而"风险点"并不等于"看空"。 */
.detail-label.opp { color: var(--color-gain); }
.detail-label.risk { color: var(--color-warning); }
.opp-list, .risk-list {
  margin: 2px 0 0;
  padding-left: 18px;
  line-height: 1.6;
  color: var(--color-text-secondary);
}

.event-toggle {
  margin-top: 10px;
  width: 100%;
  padding: 6px;
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--color-text-secondary);
  cursor: pointer;
}
.event-toggle:hover { border-color: var(--color-accent); color: var(--color-accent); }

/* 全站第二处视口断点（个股页原先一个都没有）。
   桌面端才把事件挪到"旁边"—— 而这个 app 的 manifest 是 orientation: portrait，
   主场景是竖屏手机，所以侧栏是增强而不是主形态。 */
@media (min-width: 1024px) {
  main {
    max-width: 1400px;
    grid-template-columns: minmax(0, 1fr) 360px;
    grid-template-areas:
      "notice notice"
      "chart events"
      "indicators events";
    align-items: start;
  }
  .event-section {
    /* 右侧栏在滚动时保持可见：图很高，事件栏跟着滚走就失去"旁边"的意义 */
    position: sticky;
    top: 12px;
    max-height: calc(100dvh - 24px);
    overflow-y: auto;
  }
}

/* 散户情绪卡：放在事件区顶部。验证结论与数值同屏是硬要求 */
.sentiment-card {
  margin: 10px 0 12px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 8px);
  background: var(--color-bg-subtle);
}
.sentiment-head { display: flex; align-items: center; justify-content: space-between; }
.sentiment-head h3 { margin: 0; font-size: 13px; }
.sentiment-tag {
  font-size: 11px; color: var(--color-text-muted);
  border: 1px solid var(--color-border-strong); border-radius: var(--radius-pill);
  padding: 0 6px;
}
.sentiment-metrics { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 6px; font-size: 12px; color: var(--color-text-secondary); }
.sentiment-verdict { margin: 6px 0 0; font-size: 11px; line-height: 1.5; color: var(--color-text-muted); }
</style>
