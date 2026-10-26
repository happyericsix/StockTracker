# TODO

## 高优先级
- [ ] 持仓成本分析：用户绑定买入价，策略建议基于实际盈亏
- [ ] 意图缓存：常见问法跳过LLM，提速
- [ ] 家人版上线：HTTPS + 关闭注册（`AUTH_REGISTRATION_ENABLED=false`，手动开户）+ 备份巡检（`docs/DESKTOP.md` §1）
- [ ] 预警 webhook 外发：POST 到 Server酱/企业微信/飞书 bot，桌面手机双受益（`docs/DESKTOP.md` §7 V1）

## 中优先级
- [ ] 异步回复：先回分析中再追结果
- [ ] 训练日志：记录每次R²，追踪模型退化
- [ ] 下载页预告版 + DeepSeek key 申请教程（`docs/DESKTOP.md` §6）
- [ ] 桌面化地基四件套：Flyway+SQLite 基线 / Redis→Caffeine / 后台任务补偿矩阵（结算类启动补跑全覆盖 + 凌晨任务改启动检查，`docs/DESKTOP.md` §2.4，打包关键路径起点）——llm_service 懒读取+多供应商设置界面已于 2026-10-25 以 BYOK 形式落地（见「已完成」）

## 已完成
- [x] **用户自带 LLM Key（BYOK，2026-10-25）**：个人中心新增「AI 模型 Key」——
      主槽（DeepSeek 预设或任意 OpenAI 兼容端点）+ 单条备用槽，AES-GCM 加密落
      `user_llm_configs`（新 env `LLM_AES_KEY`，fail-closed）。用户触发的 AI（聊天/
      委员会/盯盘/周·单笔·季度反思/修订提案/记忆巩固/fanout 子调研）一律花用户
      自己的 Key：Java 每次调用解密并随请求体下发 `llm_providers`（空数组 = 未配置
      → 降级模板回复，绝不静默烧全局 key），Python 复用既有冷却/切换机制且冷却按
      `u{id}:slot` 命名空间隔离。`DEEPSEEK_API_KEY` 从此只服务新闻解读等系统级任务。
      配套：保存前「测试连通性」（Python `/api/v1/llm/test`）、助手页/聚焦抽屉的
      未配置引导横幅、Key 不回显/留空=不改（与推送 Key 同范式）。
      测试：pytest 1358（含新增 20）/ mvnw 544（含新增 16）全绿。
- [x] **修复委员会决策链路全灭**（2026-10-23）：10-21 部署的 `app.py` 里
      `trading_committee.trading_decision.apply_open_position_guard(...)` 是坏调用
      （`trading_decision` 是独立模块，不在 `trading_committee` 命名空间），每次 agent
      决策必抛 AttributeError，且 except 把它包装成 `skip_reason=data_unavailable`
      伪装成数据问题——连续 3 天（10-21~23）金新农/中国银行全部结算失败，金新农
      从创建起从未开过委员会（无授权→盘中永远 awaiting_authorization），前端
      lastPrice/lastSignal/lastEvalAt 全空。修法：护栏移入 `trading_committee.decide`
      内部（真 TradeDecision 对象所在处，app.py 端点只消费扁平 dict）；except 补
      `error_detail`（type+message）让真因进痕迹快照（Java errorSnapshot 已有消费端），
      skip_reason 保持 data_unavailable 不动（补跑轮只认它）。新增
      `tests/test_agent_decide_endpoint.py` 3 例钉住回归，全量 1338 通过；
      20:17 手动补跑两只策略当日结算成功，金新农拿到首份授权
      （仅当放量收复 MA60 4.03 站稳可开仓上限 20%，跌破 3.52 作废）。
- [x] **移除智谱硬编码**（2026-10-22）：embedding 只认通用 `EMBEDDING_API_KEY/BASE_URL/MODEL` 三变量（配齐才启用，
      缺则安静退化关键词检索），`.env` 已迁移、`_DEFAULT_BASE_URL`/`ZHIPU_*` 已删，1328 测试全绿。
      桌面版最终用户只需注册 DeepSeek 一家；后续本地 ONNX embedding 见 `docs/DESKTOP.md` §3。
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