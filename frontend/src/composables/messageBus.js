import { reactive } from 'vue'
import { getUnreadCount } from '../api/messages.js'

/**
 * 消息中心口径：只有这些类型的未读才进红点。
 * 必须与后端 MessageService.CENTER_TYPES 保持一致（列表、未读数接口同源）——
 * 这条 SSE 流上还有 CHAT_BOT/CHAT_USER 聊天消息（read=false 也照推，
 * 聊天页实时回复靠它），无差别累加会造出"消息中心里看不见、也点不掉"的幽灵红点。
 */
export const CENTER_TYPES = ['ALERT', 'PAPER_SETTLE', 'STRATEGY_SENTINEL', 'STRATEGY_REVIEW', 'NEWS_ALERT', 'SYSTEM']

/** 模拟盘 tab 的类型集（与"模拟盘"tab 的服务端过滤同一口径） */
export const PAPER_TYPES = ['PAPER_SETTLE', 'STRATEGY_SENTINEL', 'STRATEGY_REVIEW']

/**
 * 站内消息总线（全局单例）
 * - 维护一条 SSE 长连接，接收服务端推送的新消息
 * - 维护未读数状态，供 Dashboard 角标与消息中心共用
 */
export const messageBus = reactive({
  unread: 0,
  latest: null,
  es: null,
  handlers: [],
  /**
   * 重连订阅者：SSE **只推新消息、不补发断线期间的**，所以恢复连接后必须让各页面
   * 自己重新拉一次。少了这一步，断线窗口里产生的那条回复要等到用户手动刷新页面才出现
   * （聊天气泡上表现为"永远在输入中"）。
   */
  reconnectHandlers: [],
  /** 上一次连接是否断开过：EventSource 每次连上都会触发 onopen，首次不算重连 */
  _errored: false,

  connect() {
    const token = localStorage.getItem('token')
    if (!token) return
    if (this.es) {
      // 同一 token 且连接**确实活着**才复用；换账号/换 token 时先关旧流再重建。
      // 2026-11-07 修复：此前只看 token 就复用——服务器重启/断线后浏览器放弃
      // 重连的 EventSource（readyState=CLOSED，比如遭遇过 401）会一直"假活"，
      // 之后每次 connect 都复用这条死连接，推送全部丢失（实测：问 AI 永远在转，
      // 后端其实早已回复）。CLOSED 的连接浏览器不会再自愈，必须重建。
      if (this.es._token === token && this.es.readyState === 1 /* OPEN */) return
      this.es.close()
      this.es = null
    }
    this.es = new EventSource(`/api/v1/messages/stream?token=${encodeURIComponent(token)}`)
    this.es._token = token
    // 未读数初值不能依赖"用户恰好先落在 Dashboard"（那只是目前碰巧调 refreshUnread
    // 的页面之一）——连接真正建立时统一对一次账。
    this.refreshUnread()
    this.es.onopen = () => {
      // 只有"断开过又恢复"才通知：首次连接时页面本来就刚拉过数据
      if (this._errored) {
        this._errored = false
        this.reconnectHandlers.forEach((h) => h())
      }
    }
    this.es.addEventListener('message', (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg && msg.read === false && CENTER_TYPES.includes(msg.type)) this.unread += 1
        this.latest = msg
        this.handlers.forEach((h) => h(msg))
      } catch (err) {
        // 忽略解析失败
      }
    })
    this.es.onerror = () => {
      // 已登出：主动断开，避免 EventSource 拿过期 token 无限重连打 401
      if (!localStorage.getItem('token')) {
        this.disconnect()
        return
      }
      // 仍在线：EventSource 自带重连，这里负责重新同步未读数，并记下"断开过"
      this._errored = true
      this.refreshUnread()
    }
  },

  disconnect() {
    if (this.es) {
      this.es.close()
      this.es = null
    }
    this.handlers = []
    this.reconnectHandlers = []
    this._errored = false
    this.unread = 0
    this.latest = null
  },

  /** 订阅新消息，返回取消订阅函数 */
  subscribe(fn) {
    this.handlers.push(fn)
    return () => {
      const idx = this.handlers.indexOf(fn)
      if (idx >= 0) this.handlers.splice(idx, 1)
    }
  },

  /** 订阅"断线后重连成功"，返回取消订阅函数。用于补拉断线期间错过的数据。 */
  subscribeReconnect(fn) {
    this.reconnectHandlers.push(fn)
    return () => {
      const idx = this.reconnectHandlers.indexOf(fn)
      if (idx >= 0) this.reconnectHandlers.splice(idx, 1)
    }
  },

  /** 从服务端拉取真实未读数 */
  async refreshUnread() {
    try {
      const res = await getUnreadCount()
      this.unread = res.data?.count ?? this.unread
    } catch (err) {
      // 后端接口暂未就绪时静默
    }
  }
})
