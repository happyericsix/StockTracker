import request from './request.js'

export const listStrategies = () => request.get('/strategies')
// 单条策略：编辑面板打开时取最新 config（不依赖列表页恰好带着它）
export const getStrategy = (id) => request.get(`/strategies/${id}`)
// 更新（编辑面板）：name + configJson；后端保存前会做 DSL 校验，改坏的规则进不了库
export const updateStrategy = (id, payload) => request.put(`/strategies/${id}`, payload)
export const deleteStrategy = (id) => request.delete(`/strategies/${id}`)
// 回测要在 Python 侧跑完整历史撮合（含逐 bar 评估），冷缓存时可能远超全局 30s，单独放宽
export const runBacktest = (id) => request.post(`/strategies/${id}/backtest`, null, { timeout: 180000 })
export const getStrategyDiagnostic = (id) => request.get(`/strategies/${id}/diagnostic`)
// 启动模拟盘：agent 模式启动即首会（委员会 8 次串行 LLM 调用，后端已放宽到 240s），
// 前端必须比后端更晚超时，否则用户看到失败而后台实际成功落库 —— 300s 留足余量
export const startPaper = (id) =>
  request.post(`/strategies/${id}/paper/start`, null, { timeout: 300000 })
export const stopPaper = (id) => request.post(`/strategies/${id}/paper/stop`)
export const getPaperAccount = (id) => request.get(`/strategies/${id}/paper/account`)
export const getPaperTrades = (id) => request.get(`/strategies/${id}/paper/trades`)

/**
 * 决策日志（trade journal）：每天"说了什么"×次日收盘"实际怎样"→ 对答案。
 * 口径是方向命中（≠盈亏），详见后端 TradeJournalResponse 的诚实边界注释。
 */
export const getStrategyJournal = (id, days = 90) =>
  request.get(`/strategies/${id}/journal`, { params: { days } })
// 结算痕迹：每一行 = 一根 bar 上的一个结论，含"为什么没成交"（skip_reason → 一句人话）
export const getPaperTraces = (id, limit = 50) =>
  request.get(`/strategies/${id}/paper/traces`, { params: { limit } })
// 净值曲线：点 + 由同一份点算出的汇总（回撤、空仓比例、相对买入持有的超额）
export const getPaperEquity = (id, days = 120) =>
  request.get(`/strategies/${id}/paper/equity`, { params: { days } })
// 决策来源已统一为 agent（2026-10-15）：切换接口下线，规则引擎保留为策略定义与降级底座
// 预期：到 deadline 为止，某个账户层面的度量是否 ≥ 门槛（到期由每日结算自动回填）
export const getExpectation = (id) => request.get(`/strategies/${id}/expectation`)
export const registerExpectation = (id, { metric, threshold, horizonDays }) =>
  request.post(`/strategies/${id}/expectation`, null, {
    params: { metric, threshold, horizonDays }
  })
// 模拟盘总览：**一次请求回答四个问题** —— 在跑什么 / 赚没赚 / 今天动没动 / 下次什么时候。
// 挂在 /paper 下（不是 /strategies/{id}/...）：它是跨策略的读视图，
// 也是"这个功能在哪"的顶级入口 —— 原来必须先点进某条策略才看得到任何东西。
export const getPaperOverview = () => request.get('/paper/overview')

// ---- 策略修订闭环（P2，2026-10-15）----
// 健康信号（确定性，无 LLM）；生成 = 1 次提案 LLM + 截至当天的时间衰减加权回测门禁。
// 生成较慢（模型 + 两次回测），超时放宽到 150s（后端 120s + 余量）。
export const getRevisionHealth = (id) => request.get(`/strategies/${id}/revision/health`)
export const generateRevision = (id) =>
  request.post(`/strategies/${id}/revisions/generate`, null, { timeout: 150000 })
export const getRevisions = (id) => request.get(`/strategies/${id}/revisions`)
export const approveRevision = (id, revisionId, note) =>
  request.post(`/strategies/${id}/revisions/${revisionId}/approve`, { note })
export const rejectRevision = (id, revisionId, note) =>
  request.post(`/strategies/${id}/revisions/${revisionId}/reject`, { note })

// ---- 只读分享（token 即凭证；重新生成=旧链接失效，删除=下线） ----
export const enableShare = (id) => request.post(`/strategies/${id}/share`)
export const disableShare = (id) => request.delete(`/strategies/${id}/share`)
// 公开视图：分享页常在未登录/带着过期 token 的浏览器里打开，
// 与扫码登录同一条纪律（skipAuth）——本地存的旧 token 对这条请求没有意义。
export const getSharedStrategy = (token) =>
  request.get(`/public/strategies/${token}`, { skipAuth: true })
