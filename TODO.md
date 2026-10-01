# TODO

## 高优先级
- [ ] 持仓成本分析：用户绑定买入价，策略建议基于实际盈亏
- [ ] 意图缓存：常见问法跳过LLM，提速

## 中优先级
- [ ] 异步回复：先回分析中再追结果
- [ ] 训练日志：记录每次R²，追踪模型退化

## 已完成
- [x] **休市提示 + 资讯首屏提速**（2026-09-20）
      用户反馈两条：①"明明是在休息日，却显示昨收，这里难道不应该提醒用户几天休息吗"；
      ②"新闻获取的就慢，点进来过了几秒才有内容，出来以后还要再等一会才有 ai 分析"。
      根因是同一种毛病的两种形态 —— **页面在替用户猜**：
      ① `quote` 的 `lastTradingDay` 硬编码 `date.today()`，于是休市日把上一交易日的
      收盘价标成了"今天的数据"；② `/news/events` 在返回前同步做完抓取 + 最多 6 次
      LLM 解读，首屏因此整块空白，而且那 6 次串行调用还会撞上 `NewsClient` 的 30 秒
      读超时，超时后**整批解读一起丢**。
      修法：新增交易日历（akshare `tool_trade_date_hist_sina`，含调休与长假）与
      `/api/v1/market/status`，`quote` 改报 `quoteDate`；`/news/events` 改成
      "秒回库内内容 + 后台单飞分批解读 + 报告进度"，前端骨架屏 + 2 秒轮询补全 +
      并行取「当前怎么看」（半批数据的结论不缓存）。
      设计文档：`docs/superpowers/specs/2026-09-20-market-status-and-news-latency-design.md`。
      已知缺口（写在文档 §4）：日历只覆盖到当年年末；后台补解读靠轮询而非推送；
      `ANALYZE_BATCH_SIZE` 与 Python 的 `BATCH_SIZE` 是两处常量（没有跨语言用例钉住）；
      前端没有自动化测试（`verifyMarketStatusWording()` 只能手动跑）。
- [x] **盘中成交当天的净值缺口**（2026-09-18 真库实测发现，同日修复，commit `4a42be9`）
      原实现里 `PaperTradingService.evaluateStrategy` 开头有一条
      `existsByStrategyIdAndTradeDate(...)` 守卫：当天已有成交就整段 `return null`。
      它防的是重复下单（对的），但连带把当天该做的四件事也跳过了 ——
      **净值快照、客观事实、到期预期回填、当天报告**；而"当天有成交"恰好是最值得看的那天。
      修法：把"同一天不能下两次单"从**整段跳过**收窄成**禁止下单** ——
      `evaluateStrategy` 不再 return null，只记住 `alreadyTradedToday`；
      `applyBarResult` 为真时仍按收盘重估账户、写净值点，并记
      `decision=skip / skip_reason=already_traded_today` 的痕迹（skip 原因两侧镜像测试已同步）。
- [x] 模型持久化（磁盘pkl）
- [x] 按需/脚本重训（无后台定时调度，与 README 声明保持一致）
- [x] 时序切分（防数据泄漏）
- [x] 三窗口差异化调参
- [x] Prompt审慎化
- [x] 股票名→代码自动转换
- [x] 回复截断