import request from './request.js'

/** 组合总览：全部模拟盘叠成一条总曲线（总净值/当日回撤/熔断状态/策略间相关性） */
export const getPortfolio = () => request.get('/paper/portfolio')

// 「决策对账：智囊 vs 规则」接口已于 2026-10-14 移除：规则与 agent 是同一流水线的
// 层（规则出信号、委员会划边界、边界内执行），不是可对比的两个决策者；单笔方向
// 对账保留在策略详情页的「决策日志」（/strategies/{id}/journal）。

/** LLM 用量（整站口径）：Profile 页"成本"卡片用 */
export const getLlmUsage = (days = 30) => request.get('/llm/usage', { params: { days } })

// 每个模拟盘策略的"最近报告 + 哨兵观察"（消息中心收窄后，复盘内容活在模拟盘页）
export const getPaperReport = () => request.get('/paper/report')
