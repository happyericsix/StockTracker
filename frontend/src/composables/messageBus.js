import { reactive } from 'vue'
import { getUnreadCount } from '../api/messages.js'

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

  connect() {
    const token = localStorage.getItem('token')
    if (!token) return
    if (this.es) {
      // 同一 token 的已有连接直接复用；换账号/换 token 时先关旧流再重建
      if (this.es._token === token) return
      this.es.close()
      this.es = null
    }
    this.es = new EventSource(`/api/v1/messages/stream?token=${encodeURIComponent(token)}`)
    this.es._token = token
    this.es.addEventListener('message', (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg && msg.read === false) this.unread += 1
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
      // 仍在线：EventSource 自带重连，这里只负责重新同步未读数
      this.refreshUnread()
    }
  },

  disconnect() {
    if (this.es) {
      this.es.close()
      this.es = null
    }
    this.handlers = []
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
