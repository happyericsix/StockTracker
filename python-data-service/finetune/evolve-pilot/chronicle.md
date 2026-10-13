# 601988 自进化回放 2025-01-02 ~ 2025-01-31（冻结线 2025-02-01）

- **2025-01-02** SKIP ｜ 持仓 0 股 ｜ {"signal": "HOLD", "size_fraction": 0.0, "confidence": 0.5, "rationale": "趋势结构未破但1月2日放量长上影跌破MA5、MACD金叉濒临死叉，且无历史战绩与信息面支撑，
- **2025-01-03** SKIP ｜ 持仓 0 股 ｜ {"signal": "HOLD", "size_fraction": 0.0, "confidence": 0.6, "rationale": "趋势结构未破（价4.881>MA20 4.847>MA60 4.604，60日+7.49%）
- **2025-01-06** SKIP ｜ 持仓 0 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.5,
  "rationale": "中期趋势未破（价4.92站上MA20/MA60），但短线动能转弱（MACD
- **2025-01-07** BUY（错，5日 -0.6%） ｜ 持仓 0 股 ｜ {
  "signal": "BUY",
  "size_fraction": 0.25,
  "confidence": 0.6,
  "rationale": "价格站上全部均线、MA60向上、距60日高点仅-1.38%，趋势跟随信号成
- **2025-01-08** BUY（错，5日 -1.4%） ｜ 持仓 5000 股 ｜ {
  "signal": "BUY",
  "size_fraction": 0.25,
  "confidence": 0.6,
  "rationale": "均线全多头且价格站上MA60并逼近60日高点，趋势结构成立；但MACD死叉

## 第 1 周（2025-01-08）反思：连续两日 buy 信号均以负收益收场（-0.6%、-1.39%），且此前 skip 的 hold 信号反而对应正收益（+；本周 judged=2、wins=0，胜率 0%，但仓位仍按 size_pct=50 的 25% 单日加仓，缺乏对连续亏；skip 日（signal=hold）错失 +1.84%、+2.05% 的上涨，说明策略在无明确信号时完全空仓过于保守；

- 提案：{"entry": {"logic": "all", "conditions": [{"type": "price_cross_ma", "window": 60, "direction": "above"}, {"type": "price_above_ma", "window": 20}]}, "exit": {"logic": "any", "conditions": [{"type": "price_cross_ma", "window": 60, "direction": "below"}, {"type": "stop_loss_pct", "value": -8}]}, "pos
- 闸门：回测对比（截至当时）：旧 254.49% vs 新 281.52% → 应用
- **2025-01-09** SKIP ｜ 持仓 5000 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.55,
  "rationale": "趋势结构完整（站上MA20/MA60、距60日高点仅-1.38%），但M
- **2025-01-10** SKIP ｜ 持仓 5000 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.5,
  "rationale": "趋势结构未破但策略无回测、MACD死叉且量能萎缩，证据不足以支撑新增仓位，
- **2025-01-13** SKIP ｜ 持仓 5000 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.6,
  "rationale": "价格4.881仍在MA20/MA60上方、60日+6.55%趋势未破，但已
- **2025-01-14** SKIP ｜ 持仓 5000 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.55,
  "rationale": "趋势结构未破（价格站稳MA20/MA60），但MACD死叉、量能仅0.7
- **2025-01-15** SKIP ｜ 持仓 5000 股 ｜ {"signal": "HOLD", "size_fraction": 0.0, "confidence": 0.55, "rationale": "趋势结构未破但MACD死叉、缩量滞涨，且策略无历史战绩、组合集中度未知，证据不足以支撑新增

## 第 2 周（2025-01-15）反思：本周5个交易日全部skip且signal均为hold，未产生任何交易，stats.judged=0说明策略在震荡/无趋势；1月15日fwd_pct=-2.81%而当日仍为hold/skip，说明当前入场条件（price_cross_ma60 ；本周无交易、无胜负记录，无法验证-8%止损与连续2次亏损熔断的实际触发效果；应固化规则：在连续无交易周后，用历史回测对当

- 提案：{"entry": {"logic": "all", "conditions": [{"type": "price_cross_ma", "window": 60, "direction": "above"}, {"type": "price_above_ma", "window": 20}]}, "exit": {"logic": "any", "conditions": [{"type": "price_cross_ma", "window": 60, "direction": "below"}, {"type": "stop_loss_pct", "value": -8}]}, "pos
- 闸门：回测对比（截至当时）：旧 278.74% vs 新 278.74% → 否决
- **2025-01-16** BUY（错，5日 -0.4%） ｜ 持仓 5000 股 ｜ {"signal": "BUY", "size_fraction": 0.15, "confidence": 0.55, "rationale": "均线多头排列、站上MA60且距60日高点仅-0.99%，趋势结构完整；但MACD死叉、量能
- **2025-01-17** BUY（错，5日 -0.4%） ｜ 持仓 5000 股 ｜ {"signal": "BUY", "size_fraction": 0.25, "confidence": 0.55, "rationale": "价格站上全部均线、MA60远在下方、60日+11.16%，趋势结构成立，缩量回踩属趋势内整
- **2025-01-20** SKIP ｜ 持仓 5000 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.6,
  "rationale": "价格4.941仍高于上行的MA60(4.6835)，中期趋势未破，但已跌破
- **2025-01-21** SKIP ｜ 持仓 5000 股 ｜ {"signal": "HOLD", "size_fraction": 0.0, "confidence": 0.6, "rationale": "价格4.941跌破MA5/10/20且MACD死叉，但仍在MA60上方、60日+12%趋势未
- **2025-01-22** SKIP ｜ 持仓 5000 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.7,
  "rationale": "价格已跌破MA5/10/20且MACD死叉，趋势跟随入场条件（价上MA20

## 第 3 周（2025-01-22）反思：连续两日 buy 信号均以 -0.38% 收负且 aligned=false，说明入场条件（price_cross_ma；skip 的三日（01-20 至 01-22）forward 收益分别为 +3.26%、+0.63%、+2.29%，全部；本周 judged=2、wins=0，且 circuit_breaker 的 consecutive_losses=2 

- 提案：{"entry": {"logic": "all", "conditions": [{"type": "price_cross_ma", "window": 60, "direction": "above"}, {"type": "price_above_ma", "window": 20}, {"type": "ma_slope", "window": 60, "direction": "positive"}]}, "exit": {"logic": "any", "conditions": [{"type": "price_cross_ma", "window": 60, "directi
- 闸门：schema 校验失败：入场条件/条件/第3条/direction：Input should be 'above' or 'below'
- **2025-01-23** SKIP ｜ 持仓 5000 股 ｜ {"signal": "HOLD", "size_fraction": 0.0, "confidence": 0.6, "rationale": "短期均线空头排列(MA5<MA10<MA20)+MACD死叉+放量滞涨，趋势钝化，且无历史战
- **2025-01-24** SKIP ｜ 持仓 5000 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.62,
  "rationale": "价4.962已跌破MA5/10/20，策略入场条件（价上MA20）不成立
- **2025-01-27** BUY（错，5日 -1.4%） ｜ 持仓 5000 股 ｜ {
  "signal": "BUY",
  "size_fraction": 0.15,
  "confidence": 0.55,
  "rationale": "价格创60日新高、站上全部均线且MA60向上，趋势结构完整，但MACD死

# 汇总
轨A（委员会实际决策）0.53%/回撤 1.05% ｜ 买入持有 4.1% ｜ 最终config回测 None% ｜ 提案 {'made': 3, 'applied': 1, 'rejected': 2, 'deferred': 0}
