<script setup>
import { ref, onMounted, onUnmounted, nextTick, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getHistory, getMinuteKline, getStock } from '../api/stock.js'
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

// ============ 数据加载 ============
const loadData = async (sym) => {
  error.value = ''
  loading.value = true
  stockInfo.value = null
  indicators.value = null
  lastUpdate.value = ''
  disposeChart()

  try {
    // 行情：分钟模式下用最近一个数据点的 close
    const stockRes = await getStock(sym).catch(() => null)
    stockInfo.value = stockRes?.data ?? null

    let list = []
    if (period.value === 'minute') {
      const res = await getMinuteKline(sym, minutePeriod.value).catch(() => null)
      // 后端返回的是 List<DailyStockResponse>（裸 DTO），拦截器不会动它，res.data 直接是 list
      const data = Array.isArray(res?.data) ? res.data : []
      list = data.map(r => ({
        date: r.date, open: r.open, close: r.close, high: r.high, low: r.low, volume: r.volume
      }))
    } else {
      // 拉 size=1000 让前端按 range 切；分页 size 给 1000 一次性拉够
      const historyRes = await getHistory(sym, 0, 1000, period.value).catch(() => null)
      list = historyRes?.data?.content || []
    }

    if (list.length === 0) {
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
    if (!chartRef.value) return

    chart = echarts.init(chartRef.value)
    renderChart(sym, data)
    window.addEventListener('resize', handleResize)
  } catch (e) {
    // 只采用后端返回的消息；去掉 axios 的 e.message —— 那是英文
    // ("Request failed with status code 500")，会原样显示给用户。
    // 兜底文案必须说清怎么恢复，不能只有"失败了"。
    error.value = e?.response?.data?.message
      || 'K 线数据加载失败，请检查股票代码是否正确，或确认网络后重试'
  } finally {
    loading.value = false
  }
}

// ============ 图表渲染 ============
const renderChart = (sym, data) => {
  const dates = data.map(d => d.date)
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
    series.push({
      name: lineName, type: 'line', data: closes,
      tooltip: { valueFormatter: (v) => (+v).toFixed(3) },
      smooth: true, showSymbol: false,
      lineStyle: { width: 2, color: token('--color-accent', '#0958d9') },
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
      lineStyle: { width: 1, color }
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
    series.push({ name: 'BOLL上', type: 'line', data: upper, showSymbol: false, tooltip: bollFmt, lineStyle: { width: 1, color: bollBand, opacity: 0.6 } })
    series.push({ name: 'BOLL中', type: 'line', data: middle, showSymbol: false, tooltip: bollFmt, lineStyle: { width: 1, color: bollMid, type: 'dashed' } })
    series.push({ name: 'BOLL下', type: 'line', data: lower, showSymbol: false, tooltip: bollFmt, lineStyle: { width: 1, color: bollBand, opacity: 0.6 } })
    legend.push('BOLL上', 'BOLL中', 'BOLL下')
  }

  // 成交量
  const volumes = data.map((d, i) => [
    i, +d.volume || 0,
    +d.close >= +d.open ? 1 : -1
  ])

  const subPeriodLabel = isMinute ? `${minutePeriod.value}分K` : { day: '日K', week: '周K', month: '月K' }[period.value]
  const stockName = stockInfo.value?.name || sym
  // 本应用是 A 股行情（贵州茅台 600519 等），价格单位是人民币，用 ¥ 而不是 $
  const priceStr = stockInfo.value?.price ? `¥${stockInfo.value.price}` : (data.length ? `¥${data[data.length-1].close}` : 'N/A')

  chart.setOption({
    title: {
      text: `${stockName} ${subPeriodLabel}`,
      subtext: `当前价: ${priceStr}  |  更新: ${lastUpdate.value}`,
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
        axisLabel: { formatter: (v) => isMinute ? v.substring(5) : v.substring(5), fontSize: 11 },
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

onMounted(() => loadData(route.params.symbol))
onUnmounted(disposeChart)

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
    <header>
      <button class="back" @click="router.back()">← 返回</button>
      <h1>K 线图</h1>
    </header>

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
      <p v-if="error" class="error-msg" role="alert">{{ error }}</p>
      <div v-if="loading && !chart" class="empty-state">加载中...</div>
      <div ref="chartRef" class="chart-container"></div>

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

      <p v-if="stockInfo" class="price-info">
        {{ stockInfo.name || symbol }} 当前价: <b class="num">¥{{ stockInfo.price || 'N/A' }}</b>
        <span class="update num"> | {{ stockInfo.lastUpdated || 'N/A' }}</span>
      </p>
    </main>
  </div>
</template>

<style scoped>
.kline-page {
  min-height: 100vh;
  /* 移动端地址栏高度算进 100vh，会顶出底部，补 dvh 兜底 */
  min-height: 100dvh;
  background: var(--color-bg-page);
  display: flex;
  flex-direction: column;
}
header {
  background: var(--color-bg-inverse);
  color: var(--color-text-inverse);
  /* iOS 独立模式（black-translucent）内容会顶到状态栏下，让出顶部安全区 */
  padding: calc(14px + env(safe-area-inset-top, 0px)) 16px 14px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}
header h1 { margin: 0; font-size: 18px; flex: 1; }
.back {
  background: transparent;
  border: 1px solid var(--color-border-control);
  color: var(--color-text-inverse);
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
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
  display: flex;
  flex-direction: column;
  gap: 8px;
  box-sizing: border-box;
}
.chart-container {
  background: var(--color-bg-surface);
  border-radius: var(--radius-lg);
  padding: 8px;
  height: 560px;
  box-shadow: var(--shadow-1);
}
/* 原 #ff4d4f 对浅灰底仅 2.91:1 */
.error-msg { color: var(--color-danger); text-align: center; padding: 24px; font-size: 14px; }
/* 原 #999 对灰底 2.54:1 */
.empty-state { color: var(--color-text-muted); text-align: center; padding: 48px 24px; font-size: 14px; }

.indicator-panel {
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

.price-info {
  background: var(--color-bg-surface);
  border-radius: var(--radius-lg);
  padding: 12px 16px;
  text-align: center;
  font-size: 14px;
  color: var(--color-text-secondary);
  box-shadow: var(--shadow-1);
}
/* 原 #fa8c16 作文字对白仅 2.38:1 */
.price-info b { color: var(--color-warning); font-size: 18px; margin: 0 4px; }
.update { color: var(--color-text-muted); font-size: 12px; }
</style>
