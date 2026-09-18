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
// 决策来源：rule（策略 DSL）/ agent（多角色委员会）。会改变历史解释依据，所以是显式动作，
// 生效日由后端记录 —— 界面只负责发起，不自己算"从哪天开始"。
export const switchDecisionMode = (id, mode) =>
  request.post(`/strategies/${id}/decision-mode`, null, { params: { mode } })
// 预期：到 deadline 为止，某个账户层面的度量是否 ≥ 门槛（到期由每日结算自动回填）
export const getExpectation = (id) => request.get(`/strategies/${id}/expectation`)
export const registerExpectation = (id, { metric, threshold, horizonDays }) =>
  request.post(`/strategies/${id}/expectation`, null, {
    params: { metric, threshold, horizonDays }
  })
