import request from './request.js'

/** 组合总览：全部模拟盘叠成一条总曲线（总净值/当日回撤/熔断状态/策略间相关性） */
export const getPortfolio = () => request.get('/paper/portfolio')

/**
 * 决策质量对账：每笔成交 N 个交易日后的方向对错，按决策源（agent/规则）分组对比。
 * 这是"agent 到底比规则好多少"这个问题唯一的答案页。
 */
export const getDecisionQuality = (horizon, lookback) =>
  request.get('/paper/decision-quality', { params: { horizon, lookback } })

/** LLM 用量（整站口径）：Profile 页"成本"卡片用 */
export const getLlmUsage = (days = 30) => request.get('/llm/usage', { params: { days } })

// 每个模拟盘策略的"最近报告 + 哨兵观察"（消息中心收窄后，复盘内容活在模拟盘页）
export const getPaperReport = () => request.get('/paper/report')
