import request from './request.js'

// 记忆管理接口。后端全部走 JWT + 归属校验（不接受前端传 userId），
// 响应由 api/request.js 的拦截器统一剥信封，所以调用方只读 res.data。

/** 概览：多少条事实、多少条经验待确认、多少段摘要 */
export const getMemoryOverview = () => request.get('/memory/overview')

/** 当前有效的事实（系统记下的"关于你"的事） */
export const listMemoryFacts = (params) => request.get('/memory/facts', { params })

/** 长期画像：稳定身份信息，与注入给助手的同一份 */
export const listMemoryPersona = () => request.get('/memory/persona')

/** 某个事实的历史变更链（"这个设置以前是什么"） */
export const getFactHistory = (subject, predicate) =>
  request.get('/memory/facts/history', { params: { subject, predicate } })

/** 撤回一条事实：助手不再使用它，历史记录仍保留 */
export const retractFact = (id) => request.post(`/memory/facts/${id}/retract`)

export const listMemoryLessons = () => request.get('/memory/lessons')
export const activateLesson = (id) => request.post(`/memory/lessons/${id}/activate`)
export const retireLesson = (id) => request.post(`/memory/lessons/${id}/retire`)

/** 会话摘要（前情提要）：可以看到助手是怎么总结对话的 */
export const listMemoryEpisodes = () => request.get('/memory/episodes')
