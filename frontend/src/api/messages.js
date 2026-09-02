import request from './request.js'

/**
 * 分页获取消息
 * @param {object} options
 * @param {number} options.page   页码（0-based）
 * @param {number} options.size   每页条数
 * @param {string} options.type   可选：ALERT / CHAT_USER / CHAT_BOT / SYSTEM
 * @param {string} options.since  可选：ISO 时间字符串，2025-08-01T00:00:00
 */
export const getMessages = ({ page = 0, size = 20, type, since } = {}) => {
  const params = { page, size }
  if (type) params.type = type
  if (since) params.since = since
  return request.get('/messages', { params })
}

/** 获取未读消息数 */
export const getUnreadCount = () => request.get('/messages/unread-count')

/** 标记单条消息已读 */
export const markRead = (id) => request.put(`/messages/${id}/read`)

/** 全部标记已读 */
export const markAllRead = () => request.put('/messages/read-all')

/** 发送聊天消息（后端异步处理，回复通过 SSE 推送） */
export const sendChat = (message) => request.post('/chat/send', { message })

/** 获取聊天历史 */
export const getChatHistory = () => request.get('/chat/history')
