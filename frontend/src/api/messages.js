import request from './request.js'

/**
 * 分页获取消息
 * @param {object} options
 * @param {number} options.page        页码（0-based）
 * @param {number} options.size        每页条数
 * @param {string} options.type        可选：单类型或逗号分隔多类型（PAPER_SETTLE,STRATEGY_SENTINEL）
 * @param {string} options.excludeType 可选：type 缺省时排除的类型（"全部"tab 传 ALERT）
 * @param {string} options.since       可选：ISO 时间字符串，2025-08-01T00:00:00
 */
export const getMessages = ({ page = 0, size = 20, type, excludeType, since } = {}) => {
  const params = { page, size }
  if (type) params.type = type
  if (excludeType) params.excludeType = excludeType
  if (since) params.since = since
  return request.get('/messages', { params })
}

/** 获取未读消息数 */
export const getUnreadCount = () => request.get('/messages/unread-count')

/** 标记单条消息已读 */
export const markRead = (id) => request.put(`/messages/${id}/read`)

/** 全部标记已读 */
export const markAllRead = () => request.put('/messages/read-all')

/**
 * 发送聊天消息（后端异步处理，回复通过 SSE 推送）。
 * `focus` 可选（agent 互通 P2）：聚焦对象 `{type:'news_event', eventId:321}` ——
 * 从资讯雷达"问 AI"进入的聚焦对话，后端把这条新闻的事实注入 agent 上下文。
 * 后端无状态：聚焦期间每条消息都带 focus，退出聚焦即不再带。
 */
/**
 * conversationId（2026-11-07 聚焦会话隔离）：聚焦抽屉每次开启生成一个，
 * 抽屉内多轮共用——与主聊天助手双向隔离（后端按它过滤历史）。
 */
export const sendChat = (message, focus = null, conversationId = null) => {
  const body = { message }
  if (focus) body.focus = focus
  if (conversationId) body.conversationId = conversationId
  return request.post('/chat/send', body)
}

/** 获取聊天历史 */
export const getChatHistory = () => request.get('/chat/history')

/** 聚焦会话历史（同一条新闻/教训的问 AI 往来，缓存窗口内；过期返回空） */
export const getFocusHistory = (type, id) =>
  request.get('/chat/focus-history', { params: { type, id } })
