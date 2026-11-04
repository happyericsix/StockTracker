# ACCOUNT-GATE.md — 账户守门员与决策架构升级（设计稿）

> 一句话：**agent 模式的盘中决策从"单策略信号驱动"改为"账户守门员驱动"——一个轻量 agent，
> 被信号事件与持仓风险阈值两种来源唤醒，看着全账户仓库做裁决；反复出现的同类情况自动升级
> 委员会复盘并产出策略修改提案。** 附带三个数据护栏与策略名自动生成。

- 状态：**设计稿，未实施**（2026-11-04）
- 前置阅读：`PaperTradingService.java` 的盘中授权注释（:1553-1566）、`intraday_watch.py` 模块头
- 关联决策：见 §2 决策记录（均已拍板）

---

## 0. 背景与问题

### 0.1 案例：601988 两轮亏损还原（2026-10/11 实盘痕迹）

策略名"中国银行-价格上穿**60日线**趋势跟随"，实际配置（traces 导出）：

```json
entry: price_cross_ma above window=2     ← 2，不是 60
exit:  price_cross_ma below window=10 或 stop_loss_pct -5%
仓位: 100%，盘中以 1 分钟线评估
```

| 时间 | 动作 | 触发 | 还原 |
|---|---|---|---|
| 10/14 10:12:12 | 买 14500@7.1271 | price_cross_ma | 分钟 bar close 7.12 vs MA2 7.115，**高出 0.005 元即"上穿"**；fill=7.12×1.001 滑点 |
| 10/15 09:32:40 | 卖 14500@6.96 | price_cross_ma | 当日 14:56 已触发下穿 MA10 卖出，**T+1 锁死被迫隔夜**；次日低开成交。代码注释记录：此笔发生在 LLM 降级、规则无人过目时（`PaperTradingService.java:1645`） |
| 10/29 15:30:32 | 买 2800@7.17 | price_cross_ma | 15:30 日线结算，收盘上穿 **2日均线**；仓位被授权压至约两成 |
| 11/3 09:47:22 | 卖 2800@6.79 | stop_loss_pct | 6.79/7.17−1=−5.30% ≤ −5%，止损硬通道不等模型直接执行 |

### 0.2 三个根因

1. **名实不符**：策略名与配置零关联。四个写入口（LLM 起名无约束 / 手动编辑 / revision 审批只换 config 保留旧名 / 库层无校验）都能造成漂移。
2. **边缘触发的单策略 DSL**：每根分钟 bar 的规则信号叫起只看单策略切片的盯盘 agent（`intraday_watch.build_panel` 只有本策略持仓），看不到账户全貌；DSL 本身不看仓库、不看大盘、不看胜率。
3. **数据新鲜度无防护**：
   - 缓存现状（`akshare_client.py`）：实时快照 3s、分钟K 30s（`MINUTE_CACHE_TTL=30`）、日线 60s~1h。缓存本身合理，**错在消费端不验数据年龄**。
   - 疑似 bug：`_minute_bar_start_label`（`akshare_client.py:677`）把新浪"结束时刻标签"转成"起点标签"（为前端图表），而 Java 已完结 bar 门控（`PaperTradingService.java:1610`）按**结束时刻**判定——口径改了门控没跟，**bar 开始几秒即可放行，而新浪最后一根是正在走的柱（close=最新价）→ 盘中可能拿半根柱算信号**。10/14 10:12:12 成交价与此吻合。
   - 完全缺失：只有"未来"门控（bar 未完结→等），没有"过去"门控（**数据太旧→拒**）。数据源卡住/停牌/缓存穿透失败时，旧行情照常结算与刷市值。

### 0.3 调研结论 → 三层架构

跨领域调研（K8s level-triggered 调和循环、事件触发 MPC、AIOps 分层唤醒、买方风控 pre-trade limits 工作流、Letta 快慢双系统、ROS/Prometheus/FIX 的陈旧数据处理、OpenAI Realtime 的"代码吃流模型吃摘要"）收敛出同一形状：

| 层 | 是谁 | 节奏 | 看什么 |
|---|---|---|---|
| 硬层 | 止损/移动止损 | 每根完结分钟 bar | 价格 vs 止损线。**纯代码，永不等待模型**（现状保留） |
| 慢层 | 15:30 全委会 | 每日一次 | 已有组合上下文（`portfolioContextQuietly`）。负责规划、授权、复盘 |
| 快层 | **账户守门员（本设计）** | 事件唤醒 | **全账户仓库 + 市面信息**，边界内裁决 |

核心原则：**电平触发**（对"实际持仓状态 vs 风险政策"的差值行动，而非对瞬时事件反应）；
**代码吃流、模型吃摘要**（秒级数据只做估值与烟雾报警，不做决策）。

参考资料：
- Level-triggered reconciliation: https://hackernoon.com/level-triggering-and-reconciliation-in-kubernetes-1f17fe30333d 、https://planetscale.com/blog/the-feedback-loops-behind-kubernetes
- 事件触发 MPC: https://arxiv.org/html/2610.09302v1 、https://people.kth.se/~dimos/pdfs/Sun_TAC19.pdf
- AIOps 分层唤醒: https://activewizards.com/blog/real-time-anomaly-detection-with-kafka-and-ai-agents
- 行情陈旧数据: https://www.dxrg.ai/field-notes/stale-quote-age-and-connection-health 、https://www.onixs.biz/insights/inside-the-fix-session-layer
- Prometheus staleness: https://www.robustperception.io/staleness-and-promql
- Flink 事件时间/水位线: https://nightlies.apache.org/flink/flink-docs-stable/docs/concepts/time
- ROS 时间戳纪律: https://www.openkinematics.com/blog/sensor-fusion-robot-perception-roles
- 快慢双系统: https://www.letta.com/blog/sleep-time-compute
- TradingAgents: https://arxiv.org/abs/2412.20138 、FinMem: https://arxiv.org/abs/2311.13743

---

## 1. 总体设计

```
                 ┌─────────────────────────────────────────────────┐
                 │              唤醒源（纯代码，零 LLM）             │
                 │  A. 信号事件：DSL 规则信号（降级为事件，非执行器）  │
                 │  B. 持仓风险阈值：near_stop / drawdown_warning /  │
                 │     concentration / auth_expired / gap_sharp /   │
                 │     plan_review_due（电平触发，5 分钟扫描）        │
                 └───────────────────────┬─────────────────────────┘
                                         ↓ 越界（含冷却/预算门）
                 ┌───────────────────────┴─────────────────────────┐
                 │        账户守门员（每用户每次唤醒 1 次 LLM）        │
                 │  输入：账户面板（仓库+市面+信号+历史战绩+教训）      │
                 │  输出：verdicts（approve/veto/downsize/flag）      │
                 │        + escalate（升级建议，带事件签名）           │
                 └───────┬───────────────────────────────┬─────────┘
                 裁决执行 ↓（复用现有守卫栈）              记忆回路 ↓
     授权边界/组合熔断/往返守卫/T+1           paper_gate_events 落库
     /整手/痕迹（trigger=account_gate）      同签名窗口内 ≥N 次
                                         ↓
                          pattern_escalation 注入 15:30 委员会（必须开会）
                                         ↓
                          委员会带 N 次证据复盘 → 产出 revision 提案
                                         ↓
                          用户一键审批 → 策略进化（名字自动同步）
```

**一个 agent、多种唤醒源、同一份账户面板。** 原盯盘交易员（`intraday_watch`）与原"组合雷达"
设计合并为本守门员——不新增第二个盘中 LLM 角色。

### 1.1 决策记录（已拍板，2026-11-04）

| 决策点 | 结论 |
|---|---|
| 策略命名 | 服务端按配置强制生成，四个写入口全覆盖；存量手动回填（一次性接口） |
| 守门员形态 | 单 agent 多唤醒源（信号事件 + 持仓风险阈值），每用户每轮至多 1 次 LLM |
| DSL 地位 | agent 模式下降级为证据/绊线；止损硬通道一字不动；rule 模式策略不受影响 |
| 开仓权限 | `paper.account-gate.allow-open` 默认 **false（盘中只减不加）**；打开后仍受委员会授权 allow_open 双重锁 |
| 上线节奏 | **影子期 3-5 交易日**（只记裁决不下单）→ 只减不加期 →（可选）完全体 |
| 数据护栏 | 阶段 0 先行，是守门员的前提 |

---

## 2. Part 0：数据护栏（阶段 0，半天）

三件防御性修复，不改变任何交易逻辑：

1. **已完结口径修复**：完结判定改为 `墙钟 ≥ bar标签 + period`；或最干净——Python 评估前
   **丢弃最后一根未走完的柱**（`evaluate_bar` 及守门员面板只消费完整 bar）。
   现况：起点标签 + 按结束时刻写的门控 = 半根柱可能被结算（见 §0.2-3）。
2. **数据年龄护栏**：结算与面板组装前校验 `墙钟 − 最后一根bar时间 ≤ 2×period + 缓冲`；
   超龄 → skip + `stale_data` 痕迹（fail-visible）。对停牌/数据源故障/缓存穿透失败免疫。
   对齐 Prometheus staleness / ROS header.stamp 纪律。
3. **序列连续性校验**：bar 日期严格递增 + 当日根数符合交易时段预期；断档返回
   `data_gap` 而不是被当成"没有信号"（对齐 FIX 序列号/跳号检测语义）。

落点：`python-data-service/agent/strategy_engine.py`（evaluate_bar 前置过滤）、
`akshare_client.py`（get_minute_kline 附带 last_bar_finished / data_age 字段）、
`PaperTradingService.java`（门控口径 + skip 原因）。

## 3. Part A：策略名自动生成（独立，约 1 天）

- 新增 `agent/strategy_naming.py`：`canonical_name(config)` 确定性生成，
  形如 `601988·上穿MA2→下穿MA10或止损-5%·全仓`（≤100 字符）。
  `_describe_conditions`（`react_agent.py:194`）迁为共享实现并补齐
  bias_pct / volume_ratio / pe_percentile 三种缺失描述。
- `app.py /api/v1/strategies/validate-config` valid 响应增加 `canonical_name`。
- Python 生成路径覆写：`tool_registry._tool_finalize_strategy`、`react_agent` 直出 JSON 路径。
- Java 四写入口覆写 name：`StrategyService.createStrategy/updateStrategy/createFromAgent`、
  `StrategyRevisionService.approve`（提交名一律忽略；revision 不再保留旧名）。
- 一次性回填：`POST /api/v1/strategies/rename-to-canonical`（手动触发，幂等）。
- 前端 `StrategyEditSheet.vue`：name 只读 + 提示"名称由策略参数自动生成"。

## 4. Part B：账户守门员（主体）

### B1 唤醒源（纯代码）

调度改为两段式（`evaluateRealtime()` 重构）：

```
第一段（每轮，纯代码）：扫全部 paper_enabled 策略
  - 规则引擎评估完结 bar（agent 模式：信号仅入待决清单，不执行）
  - 止损硬通道照旧立即执行（不走守门员）
  - markToMarket 照旧
第二段（每用户聚合，至多 1 次 LLM）：
  待决信号清单 + 持仓风险阈值扫描结果 → 有内容才唤醒守门员
```

持仓风险阈值（复用/移植 `intraday_watch` 既有口径，账户级）：

| 唤醒源 | 条件 | 参考实现 |
|---|---|---|
| near_stop | 任一持仓距 stop_loss/trailing 线 ≤1pt | `_stop_lines` 口径 |
| drawdown_warning | 组合日回撤 ≥ 熔断阈值一半（默认 2.5%） | `PortfolioService.dayDrawdownPct` |
| concentration | 同标的跨策略合计敞口 ≥30% | 新增（快照组装时算） |
| auth_expired | 持仓策略授权 TTL 到期 | `authorizationStale` |
| gap_sharp | 持仓标的跳空 ≥2% / 急涨急跌 | `detect_events` 阈值 |
| plan_review_due | 上次守门员输出的回看条件命中 | §B5 next_review（self-triggered MPC 模式） |

冷却：`userId:eventSignature` 15 分钟；预算：`paper.account-gate.daily-budget`（默认 10 次/用户/日）。

### B2 账户面板（Java 组装，fail-open）

提取 `portfolioContextQuietly`（`PaperTradingService.java:2166`）为独立构建器并扩展：

- **仓库**：每持仓——标的/股数/成本/浮盈亏%/持仓天数/**距各风控线距离**/T+1 锁定批次；
  现金、总敞口、同标的叠加度、各策略授权边界（allow_open/allow_close/max_position_pct/剩余 TTL）
- **市面**：market_context（指数/涨跌家数，已有 TTL 缓存）、该标的近 24h 高影响新闻
- **信号**：待决信号明细（策略、命中条件、当时指标值）或风险阈值命中详情
- **历史**：该策略战绩（`strategyExperienceQuietly`）、最近复盘教训、
  **同类事件近期计数**（§B5，让守门员知道"这是本月第 3 次了"）
- **今日**：today_context（各策略当日成交 + 委员会最近结论）

### B3 裁决契约（Python `agent/account_gate.py`，新端点 `/api/v1/agent/account-gate`）

- 1 次 LLM、JSON mode、15s 超时，对齐 `intraday_watch.intraday_decide` 的工程纪律
  （degraded 处理 / 保守解析：读不懂一律 veto-hold 并留痕）。
- 输出：

```json
{"verdicts": [{"strategy_id": 1, "decision": "approve|veto|downsize|flag",
               "size_fraction": 0.3, "confidence": 0.55, "reason": "一句话"}],
 "escalate": {"signature": "buy_signal|downtrend|full_position", "reason": "为什么值得委员会复盘"},
 "next_review": [{"type": "price_below|time|drawdown", "value": "..."}]}
```

- 系统提示词纪律（在盯盘纪律基础上加强）：
  1. **先仓库 → 再市面 → 最后信号**；逆市面且与委员会最近结论冲突的动作几乎总是错的（选 flag）
  2. 证据不足必须 veto（宁可错过）；size_fraction ≤ confidence
  3. T+1 硬约束；授权边界硬约束（allow_open=false 不能 approve 买入）
  4. 每个裁决一句话理由——落痕迹，用户可见"为什么"
  5. next_review 是给纯代码扫描的回看条件（下次什么情况再看一眼）

#### B3.1 方向不对称：买入之亏与止损之亏（2026-11-04 补）

**"亏钱的交易"≠"错误的交易"。** 两种亏损的裁决逻辑完全不同：

| | 买入类（approve 买入） | 卖出类（sell / partial_sell） |
|---|---|---|
| 本质 | 开始一个新风险 | 结束一个已有风险 |
| 正确的问题 | "这笔交易的期望值是正的吗？" | "**继续持有的理由（thesis）还成立吗？**" |
| 举证责任 | 在"买"——证据不足默认 veto | 在"持有"——理由失效默认卖 |
| veto 偏见 | 低门槛（宁可错过） | 高门槛（仅限特定情形，见下） |

这是既有授权哲学（allow_open 默认 false / allow_close 默认 true / 熔断不挡平仓 /
止损硬通道越过一切）向裁决层的贯穿。

落地规则：

1. **卖出裁决强制先做 thesis check**：输出契约中 sell/partial_sell 类 verdict 附带
   `thesis_check` 字段（"持有原始理由是否仍成立"及依据）。thesis 已破 → 不讨论盈亏，
   只做执行优化（现在清 vs 分批/等反弹，且必须给 next_review 硬条件，防扛单）；
   thesis 仍强 + 恐慌误杀 + 无流动性压力 → 才允许 veto，且必须写明"什么情况下承认判断错误"。
2. **沉没成本禁令**：提示词明令禁止以"已亏 X%、卖了就实亏"作为 veto 理由
   （处置效应——散户扛单的头号模式，点名封死）。
3. **硬底线：裁决不能取消止损**。硬层在每根完结 bar 独立检查止损线，守门员最多
   "优化"一个亏损出场（提前减/分批），永远不能"阻止"它。价格穿越止损线即代码执行。

#### B3.2 升级回路的归因防护（2026-11-04 补）

反复亏损出场 ≠ 卖出规则有错（通常是入场质量问题）。防升级回路把系统"优化"成
不止损的扛单机器：

- **event_signature 带方向维度**：`buy_signal|downtrend|full` 与 `stop_hit|downtrend|full`
  是两个签名，分别计数、分别升级；
- **委员会复盘第一议程 = 归因**："亏损发生在入场决策还是持有决策？"（601988 两轮的
  答案均为入场：window=2 追噪音；出场端跳空止损/-5% 止损本身无错）；
- **revision 提案护栏**：以"放宽止损"作为亏损解药的提案**必须附带回测对比**且需用户
  明确批准——全系统唯一不允许自动优化的方向。

### B4 执行与留痕

- `ExecutionContract` 新增 `TRIGGER_ACCOUNT_GATE = "account_gate"` 并入允许集。
- verdict 执行复用现有守卫栈（从 `runIntradayWatch` 执行块 `PaperTradingService.java:1842-1868`
  提取为公共方法）：授权方向边界 / 组合熔断 `haltsNewOpens` / 当日往返守卫 / T+1 批次 / 整手。
- allow-open=false 时：approve 买入类 verdict 降级为 flag（留痕说明被全局开关拦下）。
- 全部 verdict（含 veto/flag）落痕迹；影子模式下 buy/sell 一律跳过并标注 `shadow`。

### B5 记忆回路（升级机制）

- 新表 `paper_gate_events`：
  `(id, user_id, strategy_id, event_signature, decision, reason_class, snapshot_json, created_at)`
- **event_signature** = `信号类型 | 市场状态桶(涨/跌/震荡) | 仓库状态桶(空/半/满仓)` 的组合。
  例：`buy_signal|downtrend|full_position` = "满仓+下跌趋势中又触发买入信号"。
- 计数：同签名在窗口（默认 14 天）内 ≥N 次（默认 3）→ 写升级请求。
- 升级动作：Python 委员会召集门控（`trading_committee.convene_reasons` 的
  `INJECTABLE_TRIGGERS`）新增 `pattern_escalation`——下一次 15:30 **必须开会**，
  证据包含这 N 次事件的完整记录（当时的信号、仓库快照、裁决理由、后续实际走势）。

### B6 委员会 → 策略优化闭环

- 委员会结论含"建议修改参数"时 → **自动创建 revision 提案**（`agent/revision.py` 生成、
  状态=待用户审批，绝不自动改配置）——打通 `StrategyRevisionService.approve` 既有链路。
- 配合 Part A：审批通过后策略名自动按新配置重生成。整条闭环：

```
信号被守门员反复否决（同签名 ≥3 次）
→ 委员会强制开会，拿 N 次证据复盘
→ 产出参数修改提案（待审批）
→ 用户一键批准 → 策略进化 + 名实同步
```

### B7 配置项

| 键 | 默认 | 说明 |
|---|---|---|
| `paper.account-gate.enabled` | true | 总开关（false = 回到现行为） |
| `paper.account-gate.shadow` | **true** | 影子模式：只记裁决不下单 |
| `paper.account-gate.allow-open` | **false** | 盘中只减不加 |
| `paper.account-gate.daily-budget` | 10 | 每用户每日 LLM 次数 |
| `paper.account-gate.cooldown-ms` | 900000 | 同签名冷却 |
| `paper.account-gate.scan-interval-ms` | 300000 | 持仓风险扫描周期 |
| `paper.account-gate.near-stop-pct` | 1.0 | 近止损唤醒阈值 |
| `paper.account-gate.concentration-pct` | 30 | 叠加敞口唤醒阈值 |
| `paper.account-gate.escalate-window-days` | 14 | 升级计数窗口 |
| `paper.account-gate.escalate-threshold` | 3 | 升级阈值 |

---

## 5. 实施阶段与检查点

| 阶段 | 内容 | 检查点 | 回滚 |
|---|---|---|---|
| **0** | 数据护栏三修复 | 用 10/14-11/3 真实行情回放：半根柱不再被结算；构造旧序列→skip+stale_data | revert（行为可对比） |
| **1** | 守门员核心（面板组装 + account_gate 端点 + 两段式接线），**影子模式** | 影子痕迹流出现 trigger=account_gate 的裁决与一句话理由；影子期 3-5 交易日收盘对账 | `shadow=true` 本身即回滚 |
| **2** | 唤醒源合并 + 正式启用（只减不加） | 构造 near_stop 场景→守门员被唤醒并留痕；rule 模式策略行为 diff 为零 | `enabled=false` 一键回现状 |
| **3** | 记忆回路 + 委员会注入 + revision 自动提案 | 同签名第 3 次出现→次日委员会必开且证据包含三次记录→产出待审批提案 | 关闭 escalate 配置 |
| 独立 | Part A 命名 | 新建策略名=参数口径；回填接口改掉"60日线"名实不符 | revert |

关键风险点：阶段 1 的两段式调度重构与阶段 2 的守卫块提取——均设计为
**先零行为重构（现有测试守护）再接线**，是全计划仅有的两处触碰旧核心路径的改动。

## 6. 验收标准（端到端）

1. 回放 10/14 10:12 场景：MA2 微观交叉信号被守门员以账户级理由 veto（或 downsize），痕迹可读；
2. 回放 11/3 09:47 场景：near_stop 在 6.9x 提前唤醒守门员；止损硬通道行为不变；
3. 同类 veto 出现 3 次→委员会自动开会→产生 revision 提案待审批→批准后策略名同步更新；
4. rule 模式策略、15:30 委员会、止损硬通道行为与现状零差异（回归测试全绿）；
5. 断数据源/停牌场景：skip+stale_data 痕迹，无误结算；
6. **方向不对称反例**（§B3.1）：①构造"thesis 已破的亏损卖出"——守门员不得以
   "会亏"为由 veto，最多优化执行节奏；②构造"守门员试图阻止止损"场景——
   硬层照常执行，裁决留痕；
7. **归因反例**（§B3.2）：构造"止损反复触发"的签名计数——升级后委员会议题必须是
   入场质量归因，产出的提案不得为无回测对比的"放宽止损"。

## 7. 附录：601988 案例在新架构下的重演推演

- **10/14 10:12**：DSL 上穿 MA2 → 降级为事件唤醒守门员。面板显示：空仓、601988 贴着均线抖动、
  无量能无新闻 → 大概率 veto（"噪音级信号，证据不足"）。即使 approve，两成仓上限。
- **10/15 09:32**：跳空 ≥2% 触发 gap 唤醒（不再依赖被 T+1 锁死的旧持仓信号链）→ 守门员看着
  "隔夜跳空+仓位"全貌给出账户级决定；同时止损硬通道独立兜底。
- **11/3 09:47 前**：near_stop 在 6.9x（距 −5% 线 1pt 内）提前唤醒 → 守门员决定"等止损线"或
  "先减"，-5% 不再是唯一出场方式。
- **升级闭环**：若"下跌趋势中买入信号"同签名两周内出现 ≥3 次 → 委员会自动复盘 →
  window=2/10 与策略意图（60 日线趋势跟随）的错配在证据面前显形 → 参数提案 → 审批 → 名实同步。
