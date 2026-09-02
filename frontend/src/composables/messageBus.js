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
    if (this.es) return
    const token = localStorage.getItem('token')
    if (!token) return
    this.es = new EventSource(`/api/v1/messages/stream?token=${encodeURIComponent(token)}`)
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
      // EventSource 自带重连；这里只负责重新同步未读数
      this.refreshUnread()
    }
  },

  disconnect() {
    if (this.es) {
      this.es.close()
      this.es = null
    }
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
