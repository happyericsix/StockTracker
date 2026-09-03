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
