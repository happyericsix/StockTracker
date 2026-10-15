import { reactive } from 'vue'

/**
 * 行情总线（全局单例，快车道 2026-10-08）
 * - 维护一条行情 SSE 长连接（/api/v1/stocks/stream），自选股约 15 秒一批最新价；
 *   带 focus 参数时（图表页），该票额外走 3 秒快车道（FocusQuoteStreamJob）
 * - quotes: { [symbol]: { price, at } } —— 页面直接读这个 map 给自己的行做实时补丁
 *
 * <h3>与 messageBus 的分工</h3>
 * 同一套 EventSource 模式（token 走查询参数、登出即断），但行情是"可丢的快照流"：
 * 断线期间错过的批次不需要补 —— 下一批几秒后就来。所以不需要 reconnectHandlers，
 * 各页面 onMounted 时的 HTTP 初载 + 之后的 SSE 补丁已经覆盖所有场景。
 */
export const quoteBus = reactive({
  quotes: {},
  es: null,
  _focus: '',

  /** 图表页正在看的标的（2026-10-15）：带上 focus 参数重连，后端把它并入 15 秒刷新批，
   *  任何票（不必在自选）都能收到实时价推送。切票/离开图表页时重设即可。 */
  setFocus(symbol) {
    const next = symbol ? String(symbol).toUpperCase() : ''
    if (this._focus === next) return
    this._focus = next
    if (this.es) this.connect(true)   // 强制重连以更新 focus 参数
  },

  connect(force = false) {
    const token = localStorage.getItem('token')
    if (!token) return
    if (this.es) {
      if (!force && this.es._token === token) return
      this.es.close()
      this.es = null
    }
    const focus = this._focus ? `&focus=${encodeURIComponent(this._focus)}` : ''
    this.es = new EventSource(`/api/v1/stocks/stream?token=${encodeURIComponent(token)}${focus}`)
    this.es._token = token
    this.es.addEventListener('quotes', (e) => {
      try {
        const batch = JSON.parse(e.data)
        if (!Array.isArray(batch)) return
        const at = Date.now()
        for (const item of batch) {
          if (item && item.symbol && Number.isFinite(item.price)) {
            this.quotes[item.symbol] = { price: item.price, at }
          }
        }
      } catch (err) {
        // 忽略解析失败：行情是快照流，这一批丢了等下一批
      }
    })
    this.es.onerror = () => {
      // 已登出：主动断开，避免 EventSource 拿过期 token 无限重连打 401
      if (!localStorage.getItem('token')) {
        this.disconnect()
      }
      // 仍在线：EventSource 自带重连，不需要补拉（快照流没有"错过"一说）
    }
  },

  disconnect() {
    if (this.es) {
      this.es.close()
      this.es = null
    }
    this.quotes = {}
  },

  /** 某只股票的最新推送价；没收到过推送就是 undefined（调用方保留 HTTP 初载的值） */
  priceOf(symbol) {
    return this.quotes[symbol]?.price
  }
})
