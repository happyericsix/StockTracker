# 601988 自进化回放 2025-01-02 ~ 2025-01-31（冻结线 2025-02-01）

- **2025-01-02** HOLD（SKIP x0.0） ｜ 持仓 0 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.6,
  "rationale": "趋势结构未破（均线多头、价在MA60上方、MACD仍金叉），但1月2日放量
- **2025-01-03** HOLD（SKIP x0.0） ｜ 持仓 0 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.62,
  "rationale": "价格已破MA5/MA10且MACD死叉，短线动能先破，趋势跟随逻辑进入警
- **2025-01-06** HOLD（SKIP x0.0） ｜ 持仓 0 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.6,
  "rationale": "价格4.921仍稳居MA60（4.610）上方、20/60日+6.26%/
- **2025-01-07** HOLD（SKIP x0.0） ｜ 持仓 0 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.55,
  "rationale": "趋势结构完整但MACD死叉、缩量且策略零验证，首仓不追高，等回踩MA20
- **2025-01-08** 开仓（BUY x0.3）（错，5日 -1.4%） ｜ 持仓 5900 股 ｜ {
  "signal": "BUY",
  "size_fraction": 0.3,
  "confidence": 0.6,
  "rationale": "价格5.051站上全部均线、距60日高点仅-0.2%，趋势延续证据硬于MAC

## 第 1 周（2025-01-08）反思：本周唯一一次买入(01-08)在信号触发后即遭遇-1.39%回撤，且此前连续4日skip期间标的累计上涨约2.5%，说明；连续4日signal=hold时全部skip，但其中01-02、01-03的fwd_pct为正（+1.84%、+2.05；本周judged=1、wins=0，样本量过小无法验证策略有效性；应固化规则：单周有效决策少于3笔时，不据此调整conf

- 提案：{"entry": {"logic": "all", "conditions": [{"type": "price_cross_ma", "window": 60, "direction": "above"}, {"type": "bias_pct", "window": 60, "max": 5.0}]}, "exit": {"logic": "any", "conditions": [{"type": "price_cross_ma", "window": 60, "direction": "below"}, {"type": "stop_loss_pct", "value": -8}]}
- 闸门：schema 校验失败：入场条件/条件/第2条：不支持的条件类型：bias_pct
- **2025-01-09** HOLD（SKIP x0.0） ｜ 持仓 5900 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.6,
  "rationale": "趋势结构未破（站上MA20/MA60、MA60上行、距60日高点仅-1.3
- **2025-01-10** HOLD（SKIP x0.0） ｜ 持仓 5900 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.6,
  "rationale": "价格结构仍多头（收盘4.981>MA20 4.9215>MA60 4.63
- **2025-01-13** HOLD（SKIP x0.0） ｜ 持仓 5900 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.55,
  "rationale": "中期趋势未破（收盘4.881高于MA60约5.2%）但短线动能转弱（MA
- **2025-01-14** HOLD（SKIP x0.0） ｜ 持仓 5900 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.6,
  "rationale": "价格站稳MA20/60、60日+10.71%、距高点仅-1.98%，趋势结
- **2025-01-15** HOLD（SKIP x0.0） ｜ 持仓 5900 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.6,
  "rationale": "趋势结构未破（价4.981在MA20上方、距60日高点仅-1.58%），但

## 第 2 周（2025-01-15）反思：本周5个交易日全部skip且signal均为hold，未产生任何可评估交易（judged=0），说明入场条件（价格上穿6；在无交易时无法验证策略有效性，但fwd_pct显示本周存在+1.23%和-2.81%的波动，说明空仓期间错失了潜在机会也；当前config仅有单一入场条件（price_cross_ma 60），缺乏对趋势强度或成交量的确认；建议固化为规则：入

- 提案：{"entry": {"logic": "all", "conditions": [{"type": "price_cross_ma", "window": 60, "direction": "above"}, {"type": "volume_ratio", "window": 20, "threshold": 1.2, "direction": "above"}]}, "exit": {"logic": "any", "conditions": [{"type": "price_cross_ma", "window": 60, "direction": "below"}, {"type":
- 闸门：schema 校验失败：入场条件/条件/第2条：不支持的条件类型：volume_ratio
- **2025-01-16** HOLD（SKIP x0.0） ｜ 持仓 5900 股 ｜ {"signal": "HOLD", "size_fraction": 0.0, "confidence": 0.6, "rationale": "趋势结构完整（站上全部均线、MA60向上、60日+11.58%），但缩量逼近前高且MACD柱
- **2025-01-17** HOLD（SKIP x0.0） ｜ 持仓 5900 股 ｜ {"signal": "HOLD", "size_fraction": 0.0, "confidence": 0.6, "rationale": "中期趋势成立（收盘4.981站上MA60约6.5%，60日+11.16%），但量能仅20日均
- **2025-01-20** HOLD（SKIP x0.0） ｜ 持仓 5900 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.6,
  "rationale": "价格4.941仍在MA60（4.6835）上方约5.5%，趋势跟随信号未破
- **2025-01-21** HOLD（SKIP x0.0） ｜ 持仓 5900 股 ｜ {"signal": "HOLD", "size_fraction": 0.0, "confidence": 0.6, "rationale": "价格4.941仍在MA60(4.6923)上方、60日+12.02%中期趋势未破，但已跌破M
- **2025-01-22** HOLD（SKIP x0.0） ｜ 持仓 5900 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.62,
  "rationale": "价格4.841已失守MA5/10/20且MACD死叉、20日动能-2.4

## 第 3 周（2025-01-22）反思：连续5个交易日信号均为 hold 且全部 skip，但期间出现 +3.26%、+2.29% 等显著正向 fwd_pct，；本周 judged=0，没有任何交易被记录或评估，策略处于空转状态；需固化规则：当连续 N 日（如3日）无任何入场信号时；当前 config 仅有单一入场条件且无仓位分批机制，一旦错过上穿点便无补救；应固化为规则：入场逻辑改为 any 或加入

- 提案：{"entry": {"logic": "any", "conditions": [{"type": "price_cross_ma", "window": 60, "direction": "above"}, {"type": "price_cross_ma", "window": 20, "direction": "above"}]}, "exit": {"logic": "any", "conditions": [{"type": "price_cross_ma", "window": 60, "direction": "below"}, {"type": "stop_loss_pct"
- 闸门：回测对比（截至当时）：旧 1.38% vs 新 2.13% → 应用
- **2025-01-23** HOLD（SKIP x0.0） ｜ 持仓 5900 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.6,
  "rationale": "价格站上全部均线、60日+14.47%、距60日高点仅-1.36%，趋势结
- **2025-01-24** HOLD（SKIP x0.0） ｜ 持仓 5900 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.6,
  "rationale": "中期趋势（MA60上方约5%、60日+14%）未破，但短期均线全破、MAC
- **2025-01-27** HOLD（SKIP x0.0） ｜ 持仓 5900 股 ｜ {
  "signal": "HOLD",
  "size_fraction": 0.0,
  "confidence": 0.55,
  "rationale": "价格创60日新高、站上全部均线且量能放大，趋势跟随入场条件成立，但MAC

# 汇总
轨A（委员会实际决策）0.27%/回撤 1.27% ｜ 买入持有 4.1% ｜ 最终config回测 None% ｜ 提案 {'made': 3, 'applied': 1, 'rejected': 2, 'deferred': 0}
