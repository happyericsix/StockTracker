import request from './request.js'

export const listStrategies = () => request.get('/strategies')
export const createStrategy = (payload) => request.post('/strategies', payload)
export const updateStrategy = (id, payload) => request.put(`/strategies/${id}`, payload)
export const deleteStrategy = (id) => request.delete(`/strategies/${id}`)
export const runBacktest = (id) => request.post(`/strategies/${id}/backtest`)
export const getStrategyDiagnostic = (id) => request.get(`/strategies/${id}/diagnostic`)
export const startPaper = (id) => request.post(`/strategies/${id}/paper/start`)
export const stopPaper = (id) => request.post(`/strategies/${id}/paper/stop`)
export const getPaperAccount = (id) => request.get(`/strategies/${id}/paper/account`)
export const getPaperTrades = (id) => request.get(`/strategies/${id}/paper/trades`)
// 结算痕迹：每一行 = 一根 bar 上的一个结论，含"为什么没成交"（skip_reason → 一句人话）
export const getPaperTraces = (id, limit = 50) =>
  request.get(`/strategies/${id}/paper/traces`, { params: { limit } })
// 净值曲线：点 + 由同一份点算出的汇总（回撤、空仓比例、相对买入持有的超额）
export const getPaperEquity = (id, days = 120) =>
  request.get(`/strategies/${id}/paper/equity`, { params: { days } })
