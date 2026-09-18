# 模拟盘 × Agent 结合方案（设计草案 v2 · 待评审）

> **文档状态**：草案 v2，**未实现**。本文只描述设计。
> **v2 说明**：v1 经过一轮独立评审，评审意见见 §11；本文已按评审修订（删除 §4.F 惩罚/配额/分账、
> 删除原 P4 自主交易、外部源从 9 项砍到 2 项、补前置改造与迁移章节、修正全部引用）。**未采纳之处已在 §11.3 标明并给出理由。**
> **日期**：2026-09-17 ｜ **相关 spec**：`2026-09-02-strategy-agent-design.md`、`2026-09-16-boundary-control-design.md`、
> `2026-09-16-memory-system-design.md`、`2026-09-16-tool-layer-design.md`、`2026-09-05-news-module-design.md`（零代码）

---

## 0. 给评审者的提示

用户诉求一句话：**"我有一个交易策略，想用模拟盘在真实行情下验证它，并且希望有一个 AI 长期帮我盯着、帮我改进。"**

请优先回答：

1. **只保留 3 个部件，该留哪 3 个？删掉其余的会损失什么？**
2. **哪些判据不可判定（不可测）？具体到字段名。**（§4.D3 已给出两个最小样本门槛，请检查是否够）
3. **哪些地方让模型获得了超出其应有的影响力？**（§4.D1、§4.D2 已加三处约束，请检查是否有漏）
4. **§5 的外部源清单只剩 2 项，是否仍有该砍的、或漏了该加的？**
5. **逐条对照 §3 的六条原则**，指出冲突处。
6. **§7 的前置改造是否完整？**（少了任何一项，P0 都上不了线）
7. **§10 的 D1–D4（4 个待定决策）你会怎么定？为什么？**
8. **核心用户价值假设**："用户会看盘后报告与交易解释"。若不成立，哪些部分应砍掉？（§6 已给出可测指标）

---

## 1. 用户诉求与期望形态

模拟盘 = 真实行情、真实规则、只差真钱；AI 一开始完全按策略交易；随讨论逐步完善策略；
agent 可自动化交易但**需要人工审批**；**每次交易都有惩罚**；每笔**留痕**并可点时间戳知道"为什么这时候这样交易"；
单笔可与 agent 单独交流以优化后续决策；希望**双 agent**（主 agent 聊天 + 模拟盘 agent 长期交易）；
两者**记忆分开**、需要时合并；**不造新模块、能复用就复用**；希望接入更多外部信息以获得"更高智能"。

---

## 2. 现状（事实，`文件:行号` 可核查）

### 2.1 模拟盘当前与 agent 完全无关

```
起点  POST /strategies/{id}/paper/start → initializeAccount + 立即一次实时结算（PaperTradingService.java:81,93）
每日  PaperTradingJob.java:18-25  cron = "0 30 15 * * MON-FRI", Asia/Shanghai
       → evaluateDaily:111（transactionTemplate.execute:117，每策略 REQUIRES_NEW:73-74）
实时  StockPriceRefreshJob.java:39,44（fixedRate=300_000, initialDelay=30_000）
       → PricesRefreshedEvent → PaperTradingListener.java:21-28 → evaluateRealtime:127
       （transactionTemplate.execute:132 —— 见 §7 前置修复 P-4）
判定  StrategyClient.evaluateBar / evaluateBarRealtime:45-60 → POST /api/v1/strategies/evaluate-bar（app.py:728）
       → strategy_engine._cond_met / evaluate_rule（纯规则求值）
成交  次日开盘 + 滑点；整手（688/689 起 200 股）；佣金 max(费率,5 元)；卖出印花税 0.05%；
       涨跌停挡单；T+1（lastBuyBar）
幂等  每日 existsByStrategyIdAndTradeDate；实时 lastBarTime；休市/无 bar → bar_date_missing 跳过
```

### 2.2 已存在的地基（要复用的）

角色与记忆隔离（`tool_scope.ToolContext.role`、`run_agent(role=...)`、`app.py` 接受 `role`、账本 `meta.role`）；
工具集与模式（`tool_sets.py`：14 工具 / 7 域 / 4 模式）；工具声明与**唯一执行管线**
（`tool_spec.py` / `tool_pipeline.py`）；外部源门禁（`external_source.py`，`DATASETS` 在 `:96`，
含高/中/低三级 trust `:54-56`）；记忆（账本 `memory_events`、事实取代链 `MemoryFactService.saveFacts:96-137`、
撤回 `:320-345`、经验 `memory_lessons`、摘要 `memory_episodes` 版本递增 `MemoryService.saveEpisode:451`）；
**客观事实通道**（`agent/objective.py` 13 个键：`backtest_*` 8 + `paper_*` 5，`provenance=system/trust=high/confirmed=true`；
写入 `MemoryFactService.recordObjective:159`，写入点＝回测后 + 每日结算后 `PaperTradingService.java:190,220`）；
审计与裁决（`tool_audit.py` / `review.py` / `strategy_review.py`）；消息与 SSE（含断线重拉
`messageBus.subscribeReconnect`）；安全边界（双向 `X-Internal-Token`，Python 侧 fail-closed `app.py:95-121`）；
评测设施（`tool_eval` 16 用例、`memory_eval` 12 用例 + 门禁、`review_eval` **9 个用例**
（`cases.yaml` 4 + `strategy_cases.yaml` 5）＋离线诚实性断言）。

### 2.3 缺口（已按评审修订）

| # | 缺口 | 修正后的准确表述 |
|---|---|---|
**G1** | 成交原因不可解释 | `paper_trades.reason` 只是条件类型名的逗号串（`evaluate_rule` 返回 `reasons = [n for n, f in zip(names, flags) if f]`，`strategy_engine.py:147`），**不含任何指标值**；`evaluate-bar` 也只回 `signal / matched_conditions / bar_time`（`app.py:746-759`）。**要落痕必须先改 Python 契约**（见 §7）|
**G2** | 净值**已存在**，但形态不对 | 每日结算已把 `paper_equity / paper_cash / paper_shares / paper_return_pct / paper_last_eval_at` 写进**客观事实取代链**（`PaperTradingService.java:190,220`，取代链追加式非物理删）。缺的是①**时序查询形状**（事实链以 `(subject,predicate)` 为键，不是时序表，取"最近 30 天净值"要绕）②**主从关系**（事实链同时承担"记忆语义"，会被召回与注入）。新增快照表**必须指定谁是准的** |
**G3** | 策略无版本 | `updateStrategy` 就地覆盖；`createFromAgent` 每次新建一行 |
**G4** | 跳过与挡单只写日志 | "今天为什么没动"无处可答 |
**G5** | 审批只有一半 | Python 侧只有门禁（`tool_pipeline.check_approval:175`，注释写明"真实交互在 T3"），`run_agent` 从未传 `approvals`；**而动作是 Java 域操作**，Java 侧没有 `pending_action` 队列与执行令牌（§4.E）|

---

## 3. 约束与不可协商的原则

1. **执行层确定性**：同一输入重跑得到同一结果（可复现、可与回测对照、可幂等重放）；
2. **不预测股价**（三处硬约束 `react_agent.py:44-45`、`tool_registry.py:686`、`app.py:679` + 合规黑名单）；
3. **单一执行管线**：所有工具调用过 `tool_pipeline`；
4. **单一真相源**：数据只在 Java（MySQL）；Python 无凭据；
5. **证据制**：模型结论必须能引用机器可校验的证据；
6. **fail-open**：旁路失败不影响主流程，且 `/health` 可见（**审批是唯一 fail-closed 的例外**）。

### 3.1 由原则 1 直接导出的显式决策（**v2 新增，v1 缺失**）

> **本方案不实现"agent 自主下单"。** agent 与"钱怎么走"之间只有两条合法通路：
> **(a) 提议修改策略**（→ §4.D 的 revision 流程，用户采纳后由确定性引擎执行）；
> **(b) 提议低频 Java 域动作**（暂停模拟盘、调整仓位上限，→ §4.E 审批后由 Java 执行）。
>
> 理由：原则 1 要求执行可复现、可对照回测、可幂等重放，而"模型临场 discretionary 下单"三条都不满足。
> v1 曾把这件事排在 P4 并为其设计了惩罚/配额/分账（§4.F），**v2 已删除**（§11.1）。

---

## 4. 目标设计

### A. 双角色，同一运行时（不新建第二个运行时）

| | 主 agent | 模拟盘 agent |
|---|---|---|
`role` | `chat` / `strategy` | `paper_trader` |
工具集 | `core|market|strategy|model|fundamental` | 新增 `paper` 模式（现有只读工具 + 账户读取）；**不含写工具** |
记忆切片 | `user/*`（主观偏好、约束、目标） | `strategy:<id>/*`（客观表现 + 交易经验） |
触发 | 用户消息 | 15:30 定时 / 5 分钟价格事件（**常驻的是任务，不是进程**） |
职责 | 答疑 / 生成与修改策略 / 解释某笔交易 / 汇总建议 | 守规则结算 / 复盘 / 发现异常 / **提议**（绝不自己放宽规则，也**不下单**） |

### B. 记忆作用域分离 + 读取时合并

- **不新建存储**：靠现有 `subject` + 账本 `meta.role` 区分。**命名统一用冒号**：
  `user`、`strategy:<数字 id>`（与 `ObjectiveFactKeys.java:30-31,82` 一致）、`strategy:名称`（模型抽取侧，
  `consolidate.py:99`）。**v1 写的 `strategy/<id>/*` 是第三种写法，已废弃**——取代链以
  `(subject,predicate)` 为键，多一种写法就是"每条都对、合起来自相矛盾"。
- **为什么必须分**：用户亲口说的 `user.stop_loss_pct=8` 与经验 `strategy:42.observed_stop_loss_too_tight`
  必须是不同 subject，否则后者会取代前者。
- **合并规则**：**只在渲染层合并，永不写回**；注入时分区块（"关于你" / "关于这个策略"）。
- **交易经验来源是痕迹不是对话**：复用 `consolidate` 的抽取管线写入 `lessons`（`task_type=paper_trading`），
  且**引用痕迹字段必须机器可校验**（复用 `strategy_review` 的 `evidence=json_path` 口径，见 §4.D2）。

### C. 交易痕迹（trace）——**快照与可审计，不承诺"可复算"**

`paper_trade_trace`（一行 = 一次**值得留痕**的结算决策；粒度见 §4.C1）：

```
identity   strategy_id / strategy_revision_id / settlement_kind(daily|realtime) / trigger(cron|event|manual)
input      bar_date, bar_time, bar_ohlc(open/high/low/close/volume)
           indicators(当时算出的指标值快照), params(策略参数快照)
account    结算前/后：cash / shares / avg_cost / high_watermark / equity
decision   buy|sell|skip + matched_conditions(类型+参数+命中) + skip_reason
           skip_reason ∈ 由 `execution_contract.SKIP_REASONS` 定义（**封闭集只在那里**，
           本文件不抄一份：抄一份的下场是两边慢慢不一致，而两份都"看起来对"）
provenance rule_version + engine_version + adjust_mode + money_policy_version
integrity  trace_hash（用途＝**防篡改/可对账**，不是"可复算"）
dedupe     dedupe_key（唯一）——"同一件事"的定义只在一处，见 §7.3.1
```

**为什么不能承诺"可复算"（v2 修正 v1 的高估）**：行情按**前复权**取（`akshare_client.py:313`
的 `f"{code},{period},,,500,qfq"`），除权后历史 bar 会变；且 `get_history` 恒取最近 500 根、
`start_date/end_date` 参数事实上未被使用（`:284,297`），缓存键也不含日期。所以**只有触发那一根的
OHLC 无法重算 MA60**（需要整段 lookback，而那段数据可能已因除权而不同）。
→ 痕迹的定位是：**"当时那根 bar、当时那些指标值就是证据"**，支持审计与对账，不支持"用同样输入复算"。

#### C1. 痕迹体量（降噪规则，v2 新增）

实时路径每 5 分钟跑一次（`PaperTradingService.java:127`）。若把每次 `rule_not_met` 都留痕，就是
**约 48 行/日/策略 ≈ 1.2 万行/年/策略**，"点时间戳看为什么"会被噪声淹没。规则：

- **留痕**：每日结算一行 + 所有**成交**行 + **阻塞型 skip**（`limit_blocked` / `t1_blocked` /
  `insufficient_cash_for_one_lot` / `data_unavailable` / `market_closed`）；
- **不留痕**：实时路径的 `rule_not_met`（聚合进当日那一行：当日检查次数、最近一次与阈值的距离）。

### D. 观察 → 报告 → 讨论 → 引擎裁决 → 版本

```
观察（确定性，五类判据）→ 报告（分级）→ 讨论（命题化）→ 引擎裁决 → 预期登记 → 采纳/拒绝
   → 新版本（strategy_revision）→ N 周后回填
```

#### D1. 建议来源白名单（五类）

| 类别 | 判据 | 需要模型做什么 |
|---|---|---|
① 沉默期异常 | 连续未触发天数 vs 历史沉默期分位（**含最小样本门槛，见 D3**） | 讲人话 |
② 越界 | 回撤/波动超过用户**亲口说过**的容忍度（`max_drawdown_tolerance`）。**缺失时的行为必须写明**：v2 定为"缺失即不报"，绝不取默认值假装越界 | 讲人话 |
③ 规则互斥/退化 | `strategy_review` 的确定性 9 项 | 不需要 |
④ 参数系统性问题 | 同参数在**多时段 × 多标的**上系统性更差（**前置能力缺失，见 §7 P-2**） | 不需要 |
⑤ 执行异常 | 涨停挡单 / 无 bar / 资金不足一手 / 审批延迟成本 | 讲人话 |

白名单的**唯一目的**：模型的"想法"来自它刚看到的那段行情，而策略改动必须样本外验证才有意义。
否则会形成自我强化循环：看刚发生的行情提建议 → 用户被说服 → 改策略 → 同段历史回测"更好看" → 未来更差。

**v2 新增约束（评审第二处越权）**：白名单只约束"类别"，不约束"提哪个命题、在哪些标的/时段上验证"。
**标的池与时段必须由代码或用户固定，模型只能填空**——挑选样本＝挑选证据。

#### D2. 讨论协议

1. **命题化**：把主张变成可回测断言（"slow 60→55，回撤 ≤ -14%"）；
2. **引擎裁决**：多时段 × 多标的（前置能力见 §7 P-2）；
3. **预期登记**：`expectation`（可验证，非股价预测）；
4. **采纳/拒绝**：模型输出 `stance ∈ {agree, disagree, insufficient_evidence}` +
   `counter_evidence` + `what_would_change_my_mind`。
   **`counter_evidence` 必须由机器校验**：复用 `review.py` 的"引用不存在即作废"机制
   （策略类证据用 `strategy_review` 的 `json_path` 口径），**"无证据的同意"直接丢弃**。

#### D3. 空转期报告（三层）与**最小样本门槛**

| 层 | 内容 | 门槛 |
|---|---|---|
事实层 | 当前价与阈值距离、连续未触发天数、净值、最近结算结果（含 skip 原因） | 无（永远可报） |
统计层 | "历史平均每 N 天触发一次；当前沉默 k 天" | **已完成的沉默区间 < N_min（建议 ≥ 8）时，只报事实层，不报分位**（n≈6 的分位是噪声） |
解释层 | 一句话人话 + 是否需要行动 + 可讨论选项（**选项集合同样白名单化**，v2 新增） | 引用上两层 |

#### D4. 打扰门禁（必须阈值触发，不能由模型判断）

每日（静默入消息中心，有结算才写）／每周（固定节奏）／立即推送（确定性阈值：越界、执行异常、连续 N 天沉默）。

### E. 审批（**重建：审批与执行都在 Java**）

v1 把审批画在 `tool_pipeline` 上、执行放在 Java，等于动作在管线之外落地（评审指出为最明显的架构空洞）。v2 修正：

```
模拟盘 agent（Python）产出**提议 JSON** → POST 内部端点 → Java 落 pending_action
   → 推送到消息中心（复用）→ 用户批准/拒绝（可附理由，进账本）
   → Java 生成一次性令牌并**由 Java 执行**（动作是 Java 域操作）
   → 写痕迹 → 通知结果
```

- agent **不调任何写工具**；Python 侧 `approvals` 门禁在 v2 架构下**保持未使用**（因为没有 Python 写工具），
  这是刻意的简化，不再作为"待打通"的缺口。
- 可审批动作（低频、非时效）：**暂停模拟盘**、**调整仓位上限**、**参数变更（= revision）**。
  真实下单**不在列**（§3.1）。
- **影子模式**：批准前展示"如果批准，它会做什么"（不执行）。
- **延迟成本如实记录**：因审批延迟造成的成交价差写进痕迹并在收益里单列（对应 §6 R2）。
- 动作分级：免批（只读/报告/建议）｜需批（上述三项）｜禁止（放宽止损、与用户原话相反的规则、真实下单）。

### F.（v1 的惩罚/配额/分账已删除）

删除理由：① 与原则 1 冲突（"非规则触发的自主交易"在确定性执行下无法定义）；
② 不进 P&L 的固定基点在经济上等于零，只是界面装饰；进 P&L 则污染"模拟盘≈实盘"的口径；
③ 它服务的是一个已被 §3.1 否掉的能力。**仅在 §3.1 被修订时才重新评估**。
摩擦成本（佣金/印花税/滑点）保持现状，属"真实口径"，不需要新机制。

---

## 5. 外部信息接入（按 §5.1 判据筛过）

### 5.1 唯一判据

**它是否让 §4.D1 的某一类观察变得"可判定"或"可解释"。** 不改变任何判据的源＝纯成本（上下文预算 + 注入面 + 维护）。

### 5.2 只保留 2 项（v2 从 9 项砍到 2 项）

| 项 | 服务的观察 | 为什么现在就要 | 归类 |
|---|---|---|---|
**交易日历**（休市/半日市/临时休市） | ⑤ 执行异常 | 否则 `skip_reason` 里 `market_closed` / `no_bar` / `data_unavailable` **无法区分**，⑤ 的判据不可判定 | **参考数据**（静态表 + 版本化） |
**ST 涨跌停幅度（5%）** | ⑤ 执行异常 | 现有 `_default_limit_pct`（`strategy_engine.py:263`）已覆盖 10% / 20%（688/689/300/301/302）/ 30%（北交所 43/83/87/88/92），**但缺 ST 的 5%** → ⑤ 的判据对 ST 股是错的 | **参考数据** |

> **v2 修正（评审的类别错误批评）**：这两项**不应进 `external_source.DATASETS`**。该注册表是为
> "内容型外部源"设计的（TTL、健康门禁、`<external_data>` 包裹、注入防护），虽然它支持多级 trust
> （`external_source.py:54-56`），但把**高可信参考数据**塞进"内容净化"流程是范畴错误。
> 正确做法：**静态表 + 版本号**（日历与幅度规则按年/按交易所更新）。
>
> **v2 修正（评审"砍 70%"的批评）**：指数/板块、新闻公告、资金流、同业对照、财报事件化、宏观日历
> **全部从本方案移除**——它们是"解释层"素材（`trust=low` 只能当线索），不影响任何判据。
> 其中新闻/公告已有独立设计（`2026-09-05-news-module-design.md`），应由那个模块决定，不并入本方案。

### 5.3 三条反例（保留）

① 不改变判据的源是纯成本（新闻类工具上下文预算 3600 字符，指标类很小，塞满会挤掉指标与对话历史）；
② 外部正文只能当线索（账本层已禁止存外部正文，只存"取到几条"+落盘编号）；
③ **接入 ≠ 会用**：每加一个源必须同时加一条 golden set 用例（`tests/tool_eval` 形状），否则无法知道它是否被正确使用。

---

## 6. 已知风险（v2 修订）

| # | 风险 | v2 的处理 |
|---|---|---|
R1 | 惩罚污染收益口径 | **已删除相关设计（§4.F）**，风险随之消失 |
R2 | **日线策略 + 人工审批 = 永远慢半拍**（根本矛盾） | 审批只保留低频、非时效动作（§4.E）；延迟成本如实记录；**不假装审批能及时保护用户** |
R3 | 过拟合循环 | 观察白名单（§4.D1）+ 标的/时段由代码固定（§4.D1 末）+ 多时段多标的裁决（§7 P-2） |
R4 | 模型顺从用户 | 命题化 + 引擎裁决 + 反证据机器校验（§4.D2） |
R5 | 空转期零信息量 | §4.D3 三层报告 + 最小样本门槛 |
R6 | 过度设计 | **已按 §11.1 删掉一整节 + 一个阶段**；若只需 3 个部件，保留"痕迹 + 日度净值 + 一句人话" |
R7 | 审批疲劳 | 影子模式 + 额度内免批 + 只批重要动作（§4.E） |
R8 | **"越交易越完善"可能永远不成立** | 日线策略 1–2 次/月，20 次样本＝10–20 个月。→ **报告必须给统计显著性反馈**；`strategy_revision.outcome` 的 N 周窗口在可预见生命周期内**填不出可信值**，故 v2 把 outcome 定位为"有则记录，不足样本时留空并标注" |
R9 | 改动缺少纪律 | 改动冷却期 + 每月上限（沿用 §4.D 末端）——**前提是 D 流程先跑起来** |
R10 | 与正式盘差距未声明 | 必须在界面写明：对得到"日线级 + 次日开盘 + 涨跌停 + T+1 + 费用"，**对不到**盘口/排队/部分成交/撮合 |
R11 | 成本累积 | 见 §7.2 成本上限（v2 新增） |

---

## 7. 前置改造与迁移（v2 新增，评审指出 v1 完全缺失）

### 7.1 前置改造（缺任何一项，P0 上不了线）

| # | 改造 | 为什么必须 |
|---|---|---|
**P-1** | **`evaluate-bar` 契约扩展**：回传 `bar_ohlc` + 指标快照 + 参数快照 + `engine_version` | 现在只回 `signal / matched_conditions / bar_time`（`app.py:746-759`），`matched_conditions` 只是类型名（`strategy_engine.py:147`）。要么改 Python 契约（推荐，配 `test_agent_chat_contract.py` 那样的契约测试），要么 Java 重算（违反原则 4） |
**P-2** | **日期区间回测 + 多标的×多时段裁决 + 成本归因**（✅ 已完成，见 §7.1.1） | 现在 `get_history` 恒取最近 500 根、区间参数未被使用（`akshare_client.py:284,297`）；§4.D2 的"多时段裁决"与 §4.D1 ④ 都依赖它 |
**P-3** | **复权口径**：明确痕迹与回测使用同一复权方式，并记录 `adjust_mode` | 前复权数据随除权变化（`:313`），不记录口径则历史无法对账 |
**P-4** | **修复既有事务包裹**：`evaluateRealtime` 把最长 60s 的 Python 调用包在 `transactionTemplate.execute` 里（`:132`），与该文件注释声明的意图相反 | 往这条路径再加痕迹写入（与未来 agent 调用）会放大连接占用 |
**P-5** | **ST 涨跌停 5%** + **交易日历**（§5.2） | 否则 ⑤ 的判据对 ST 股是错的，休市与"数据未出"分不开 |

### 7.1.1 P-2 的落地与三个实测坑（2026-09-17 补）

裁决能力落在 `evaluate_strategy_matrix` + `POST /api/v1/strategies/backtest-matrix`
+ 工具 `backtest_matrix`（`strategy` 工具集，昂贵、带缓存键）。**确定性、不调模型**：
模型只在"解释这张表"时上场。

实测（8 只票 × 3 段 × 2023-01~2026-09，¥10 万本金，MA60 上下穿）暴露了三件事，
每一件都曾经让这张表**看起来很合理、其实是错的**：

1. **区间被静默砍掉**：腾讯 `fqkline` 的 `limit` 是"区间内最近 N 根"，且有硬上限 640
   （超过 640 直接返回空）。所以"从 2023 年起验证"必须**分页**取，不能靠调大 `limit`；
   取回后再本地裁到区间内。→ `akshare_client._windows_for` / `SOURCE_MAX_BARS`
2. **0.00% 有三种意思**：整段在指标预热期内（没验证）、该段确实没信号（验证过、没动）、
   **信号触发了但一手就超过本金**（本金做不了这个标的）。茅台的三个格子是第三种 ——
   上穿 60 日线触发了 6 次，一手 ~14 万 > 10 万本金，一笔都建不了仓。
   表格必须把三者分开写，且**不参与统计**，否则"没样本"会被读成"表现平平"。
   → `_segment_note` / `summarize_segments.segments_warmup_only|segments_unaffordable`
3. **段长下限必须跟着策略的指标窗口走**：MA60 的策略被切成 40~166 根一段时，
   整段都在预热里。→ `required_warmup_bars` + `min_bars_per_segment = 预热 + 30`

结论示例（同一份结果，两种视角）：21 个有效格子里 **10 格跑赢买入持有**（≈掷硬币），
平均超额 **-1.35%**，摩擦合计 **¥39,535**（平均吃掉本金 1.883%）；
只取 6 只票时平均超额变成 +1.08% —— **换一批标的结论就翻号**，这本身就是"没有边际"的证据。

### 7.2 成本上限（v2 新增）

- 成本 = **用户数 × 策略数 × 触发频次**（v1 只提了策略数）；实时路径每 5 分钟一次，须明确
  "每次触发是否调模型"（本方案：**不调**，只做确定性检查；模型只在每日复盘与用户讨论时调用）。
- 需要：月度 token 预算、超限行为（降级为模板报告）、**按角色/用途的账单**（现有 `metering` 有计量，
  缺按角色聚合）。

### 7.3 迁移与回填

- `strategy_revision` 的 v1 从哪来：**上线时为每个存量策略补一行 v1**（`source=user`，`reason='迁移'`）；
- `paper_equity_snapshot` 是否补历史：**不补**（无法重算受复权影响的历史净值），从上线日起记录，
  界面明确"曲线自 YYYY-MM-DD 起"；
- 存量 `paper_trades`：`origin` 回填为 `rule`，`trace_id` 留空（**历史痕迹不可能回填**——G1 的实质，
  必须在界面注明"该笔交易发生在痕迹功能上线前"）；✅ **已实现**：界面上一笔无 `traceId` 的成交会写明
  "痕迹功能上线前的成交，或痕迹写入失败" —— 留白会被读成"一切正常"。
- 事实链与快照表的主从：**快照表为准**（时序查询用），事实链保留"记忆语义"用途，
  两者在同一事务里写入以避免不一致。

### 7.3.1 痕迹（P0）的落地与实测修正（2026-09-17 补）

**已实现**：`paper_trade_traces`（`entity/PaperTradeTrace`）+ `PaperTraceService`（写入策略）
+ `PaperTradingService` 全路径接线 + `GET /strategies/{id}/paper/traces[/{date}]`
+ 前端"结算痕迹"卡片（含证据快照折叠）+ 成交行反向链接 `paper_trades.trace_id`。

落地时对设计做了四处**收紧**，每处都有真实理由：

1. **去重键从"唯一索引三列"改成单列 `dedupe_key`**：MySQL 唯一索引里 NULL 互不相等，
   而实时成交的 `bar_time` 在日线行里是 NULL —— 用三列做键等于"日线行可以无限重复"。
   单列键的形状（`daily:<sid>:<date>` / `realtime:<sid>:<barTime>:<decision>` /
   `realtime:<sid>:<date>:<reason>`）把"同一件事"的定义收在一处。
   重复出现时**更新那一行**并累加 `repeatCount`：读者要的是"这个键上的结论"，
   而不是"它被算过几次"。首次可能是取数失败、当天稍后成功，所以让最后一次结论生效。
2. **`0.00%` 的第三种意思**：茅台上穿 60 日线在回测里触发 6 次、成交 0 笔 ——
   因为一手（100 × ~1400 元）超过 10 万本金。所以新增 `insufficient_cash_for_one_lot`
   与 `state_mismatch`（信号与账户状态对不上，结构上不该发生，一旦出现多半是并发或契约变更），
   并且**不给它们编"规则没成立"这种看起来合理的原因**。
3. **`warmup` 进了封闭集**：`rule_not_met`（条件算出来了、不成立 = **有样本**）与
   `warmup`（指标还没算出来 = **没有样本**）必须分开，后者由**引擎**判定
   （`strategy_engine._warmup_pending`）并随响应回传 `skip_reason` —— Java 手里没有指标值，
   让它猜必然写出一个看起来很确定的错原因。
4. **钱的精度先落到 Java**：`service/Money.java` 是 Python `MoneyPolicy` 的镜像
   （price 4 / amount 2 / equity 2 / return 4，HALF_UP，且**先转字符串**再 `BigDecimal`）。
   痕迹的金额列一律 `DECIMAL`：证据本身带二进制误差时，"对账"这件事就无从谈起。
   ✅ **账户与成交也已迁到 DECIMAL（2026-09-18）**：`paper_accounts` / `paper_trades`
   的字段全部改成 `BigDecimal`，结算路径（整手取整、佣金下限、印花税、净值与最高水位）
   重写为定点运算，`PaperAccountResponse` / `PaperTradeResponse` 同步。
   理由用一条断言写死：100 股 × 10.1234 元 + 现金 8999.87 应当**恰好**等于 10012.21，
   而 double 算出来是 10012.210000000001（见 `MoneyTest`）。
   ⚠️ **已有库仍需跑一次 DDL**：`ddl-auto=update` 只加列、**不改列类型**，
   所以老库里的列还是 `DOUBLE`。脚本在
   `docs/superpowers/specs/2026-09-18-paper-money-decimal.sql`；
   启动时 `MoneySchemaCheck` 会用 JDBC 元数据查一遍，不一致就 WARN 并把 ALTER 打出来
   （只告警不中断 —— 但绝不静默："以为迁了、其实没迁"的钱包比没迁更危险）。

```sql
-- 账户与成交的钱迁到 DECIMAL（与 Money.java / MoneyPolicy 同一口径）。
-- 为什么要跑：double 让"净值 = 现金 + 股数 × 价格"结构上不可能零容差成立。
ALTER TABLE paper_accounts
  MODIFY initial_capital DECIMAL(18,2), MODIFY cash DECIMAL(18,2),
  MODIFY shares DECIMAL(18,4),        MODIFY avg_cost DECIMAL(18,4),
  MODIFY equity DECIMAL(18,2),        MODIFY high_watermark DECIMAL(18,4),
  MODIFY last_price DECIMAL(18,4);
ALTER TABLE paper_trades
  MODIFY price DECIMAL(18,4), MODIFY shares DECIMAL(18,4), MODIFY amount DECIMAL(18,2);
```

### 7.3.2 盘后复盘报告（P1）的落地（2026-09-18 补）

**已实现**：`PaperReviewReportService`（确定性拼装）+ `POST /strategies/{id}/verify`
+ `Message` 表新增 `dedupe_key`（唯一）+ 前端消息中心"复盘"tab。

一条真实生成的报告（端到端跑出来的，行情为 8 只票 × 3 段）：

```
【模拟盘复盘】上穿60日线 · 600519 · 2026-09-18
· 2026-09-18（600519） 未成交：现金不足一手，买不进

账户：净值 100000.00（+0.00%），现金 100000.00，持仓 0.0000 股
今天的结算：日线 1 次，盘中评估 0 次；跳过原因：现金不足一手，买不进 ×1

规则证据（样本外验证，2026-09-18 跑）：
  · 21 个有效格子里 8 格跑赢买入持有；平均超额 -0.10%
  · 摩擦平均吃掉本金 1.477%（换手越勤，这部分越大）
  · 口径：引擎 16bd4e8f293c（引擎版本不同则数字不可比）
```

落地时的关键决定（每条都对应一个具体的失败方式）：

1. **报告不调模型**：它是判断依据，不能每次生成都不一样，也不能因模型不可用而缺席。
   "一句人话"由 `skip_reason → 人话` 的确定性映射给出（每个封闭值都有一句，有测试盯着）；
   模型将来可以在这之上再解释，但**不能替代**它。
2. **验证结论进客观事实通道**（新增 6 个谓词 `verify_*`）：盘后复盘要*引用*它、
   P2 的讨论协议要*裁决*它 —— "引用"的前提是有一份带时间戳、可被取代、可查历史的记录。
   写在对话里的话下一轮就找不到了。为此矩阵响应新增 `engine_version`（口径随结论走）。
3. **"没验证成"不许顶替"上次的结论"**：没有可用样本时只写 `verify_at` 与
   `verify_valid_cells_count=0`，其余键不写。写了就会取代掉真实数字，
   于是报告把"这次没验证成"讲成上次的结果 —— 比不记录危险得多。
4. **标的池由代码固定**（`StrategyService.VERIFICATION_PEERS`，策略自身标的 + 8 只流动性好的主板大票）：
   挑选样本＝挑选证据。实测同一策略换一批标的，平均超额能从 -1.35% 翻成 +1.08%。
5. **不打扰**：日线每天产出**一行**痕迹，但只有"成交或阻塞型跳过"才推日报；
   否则一周一条空转期摘要（含最小样本门槛：不到 20 个交易日就写"还不能判断"，
   而不是暗示"它坏了"）。实时路径的 `rule_not_met` 不推 —— 那是噪声。
6. **幂等由数据库保证**：`messages.dedupe_key` 唯一索引 + `MessageService.saveReport`，
   且它跑在**独立事务**（REQUIRES_NEW）。原因很具体：加入调用方事务的话，
   一次报告写入失败会把结算事务标记成 rollback-only —— 为了发一条消息而丢掉当天的真实成交。
   报告生成本身也放在结算事务**提交之后**。


一次性令牌防重放（非一次性拒绝）、动作过期即失效（到期未批＝自动拒绝并通知）、
每日结算的重跑不得重复写痕迹（以 `(strategy_id, trade_date, settlement_kind)` 为唯一键）、
报告重跑不得重复推送。

### 7.4 真库上才暴露的三件事（2026-09-18 实测）

这一版代码第一次真正跑在**真 MySQL + 真 app**上时（此前所有 Java 侧验证都跑在 H2 上），
暴露了三件测试库给不了的事。三条都不是"实现细节"，而是"只有真库会告诉你"的类别。

1. **实体列名撞上 MySQL 保留字 → 建表直接语法报错，而 H2 不报**。
   `create table paper_trade_traces` 里有列叫 `signal`，MySQL 8 的 `SIGNAL`（存储程序用）
   是保留字，于是整条建表语句失败；`trigger` 同理。测试库 H2 接受这两个名字，
   所以 200+ 条用例全绿、DDL 看起来正常。
   后果不是"报个错"而是**痕迹永远写不进去**：结算照常成交、账户照常更新，
   `PaperTraceService.record` 按设计吞掉异常（fail-open 是对的，记忆类旁路不该影响结算），
   于是"为什么今天没成交"从此无人能答，界面痕迹页永远空白。
   落点：列名改为 `signal_value` / `trigger_kind`；新增 `MySqlReservedColumnTest`，
   保留字清单**从真库导出**（`SELECT WORD FROM INFORMATION_SCHEMA.KEYWORDS WHERE RESERVED=1`，
   MySQL 8.0 共 262 个，存 `src/test/resources/mysql8-reserved-words.txt`），
   并用一个故意违法的探针类证明"检测本身有效"——检测不出来时，全表扫描的绿灯毫无意义。
   **规矩**：新增实体列一律先过这条用例；H2 绿不代表 DDL 能在真库跑。
2. **`ddl-auto=update` 会改列类型（Hibernate 7.2），原来的说法是错的**。
   启动日志里出现 `alter table paper_trades modify column amount decimal(18,2) not null`，
   之后 `MoneySchemaCheck` 报"金额字段都是定点数"，`INFORMATION_SCHEMA` 确认全部 decimal。
   也就是说 `2026-09-18-paper-money-decimal.sql` 正常情况下**不需要手工执行**
   （该文件已按实测更正，降级为兜底与自检）。注意它会顺带加 `NOT NULL`，
   历史数据里有 NULL 时仍需手工处理。
3. **这套功能此前从未在真库上跑过**：真 `stockdb` 里根本没有 `paper_trade_traces` /
   `paper_equity_snapshots` 两张表，`strategies` 也没有 `decision_mode` 列 ——
   它们只存在于 H2 的测试会话里。追加式 DDL 在启动时补上了；
   但这件事本身说明：**"集成测试通过"与"生产 schema 正确"是两件事**，
   涉及新表/新列的改动必须至少启动一次真库。

### 7.5 可观测性（v2 新增）

`/health` 需新增：`pending_action_queue_length`、`approval_overdue_count`、
`trace_write_failures`、`report_failures`、`calendar_version`、`engine_version`。

---

## 8. 分阶段路线（v2 重排）

| 阶段 | 内容 | 验收标准 |
|---|---|---|
**P0** | §7.1 前置改造（P-1…P-5）+ `paper_trade_trace`（含阻塞型 skip）✅ + `paper_equity_snapshot` + **一句人话解释** ✅（确定性拼装，见 §7.3.1） | ① 能画净值曲线（并注明起点）② 随机抽 5 笔/5 次阻塞跳过，不看代码能明白"为什么这一刻"③ 所有 skip 的 `skip_reason` 都能区分 |
**P1** | 盘后复盘 + 空转期周报（含最小样本门槛）写入消息中心 ✅（见 §7.3.2） | **用 `Message.read` 做验收**：报告已读率 ≥ 目标值（例：P1 上线 4 周内周报已读率 ≥ 60%）——这是"用户会不会看"的**可测**判据（`entity/Message.java:51-53`） |
**P2** | `strategy_revision` + 讨论协议（命题化 + 引擎裁决 + 反证据校验）+ 观察白名单 ①③⑤ | 建议采纳率、expectation 命中率可统计；无一条建议未经多时段多标的验证 |
**P3** | 审批通道（Java 侧 `pending_action` + 一次性令牌 + 审批卡 + 影子模式）+ 三个低频动作 | 每个动作有审批记录与幂等键；重放安全（§7.4）；延迟成本被如实记录 |

> **已删除**：v1 的 P4（自主交易 + 配额/惩罚/分账）——见 §3.1 与 §4.F。

---

## 9. 明确不做

❌ 不把逐笔买卖决策交给模型；❌ **不实现 agent 自主下单**（§3.1）；❌ 不预测股价；
❌ 不做总调度 agent 调子 agent；❌ 不新建第二个 agent 运行时 / 第二个数据源 / 文件式记忆；
❌ 不做分钟级撮合、盘口与排队模拟；❌ 不允许 agent 放宽自身约束；
❌ 不把外部正文写进长期记忆；❌ 不把内容型外部源并入本方案（交由其独立 spec）。

---

## 10. 待定决策（v2：4 个）

**D1 审批粒度**：先只做"参数变更 / 暂停模拟盘"这类低频非时效动作（本文倾向），延迟成本如实记录。
**D2 解释文本是否落库**：痕迹为唯一真相源；解释文本按需生成，落库时标注"由某模型在某时生成"，
且**不得作为事实写回长期记忆**（本文倾向）。
**D3 痕迹粒度**：按 §4.C1 的降噪规则（本文倾向），不采用"每次跳过都留痕"。
**D4 空转报告节奏**：固定周报（不由模型判断值不值得说）+ 最小样本门槛（本文倾向）。

---

## 11. 评审记录（v1 → v2）

### 11.1 已采纳

1. **删除 §4.F（惩罚/配额/分账）与 P4（自主交易）**，并把"agent 不交易"写成 §3.1 的**显式决策**；
2. **承认前置改造**：trace 需要 `evaluate-bar` 契约扩展（P-1）；引擎裁决需要日期区间回测（P-2）；
   "只加两张表其余全复用"的说法不成立；
3. **trace 降级为"快照/可审计"**，不再承诺"可复算"，`trace_hash` 改为防篡改/对账用途（前复权 + 区间参数未生效）；
4. **外部源从 9 项砍到 2 项**，并把它们**移出 `external_source.DATASETS`**（参考数据走静态表）；
5. 命名统一为 `strategy:<id>`；修正 `review_eval` 用例数（**9** 而非 16）、§0 Q7 的错误指向
   （待定决策是 §10 的 D1–D4）；
6. 补 **§7**（前置改造、成本上限、迁移回填、重放幂等、可观测性）；
7. 补**最小样本门槛**（沉默期分位、`outcome` 回填）与**统计显著性反馈**（R8）；
8. 补**模型越权的两处约束**：标的/时段由代码固定（§4.D1）、可讨论选项白名单化（§4.D3）；
9. **验收改用 `Message.read` 已读率**（P1），把"用户会不会看"变成可测判据；
10. **审批重建为 Java 侧**（§4.E），承认 v1 的审批与执行分属两条管线；
11. 补**痕迹体量降噪规则**（§4.C1）与 **P-4 既有事务包裹修复**（`:132`）。

### 11.2 修正的引用

`PaperTradingService` 事实写入点 `:190,220`（v1 写 214/220）；`external_source.DATASETS` 在 `:96` ✓；
`_default_limit_pct` 在 `strategy_engine.py:263`。

### 11.3 未采纳（附理由）

**评审称"涨跌停规则未覆盖北交所 30%（现有只覆盖 20%/10%）"——不准确。**
实际 `_default_limit_pct`（`strategy_engine.py:263-270`）覆盖：20%（688/689/300/301/302）、
**30%（北交所 43/83/87/88/92）**、10%（主板）。**真正缺的是 ST 的 5%**（ST 是常见标的，且现有实现对
ST 股会用 10% 计算挡单 → ⑤ 判据对 ST 是错的）。故 §5.2 保留"ST 涨跌停幅度"一项，但不保留"北交所"的表述。

**另：评审称把日历/幅度放进 `DATASETS` 会"把高可信数据污染成低可信"——结论同意（应走静态表），
但理由不准确**：`external_source.py:54-56` 定义了 high/medium/low 三级 trust，`:94` 的注释也说明
结构化数据可用 medium。准确的反对理由是**范畴**：该注册表是"内容型外部源"的净化与门禁流程
（TTL、`<external_data>` 包裹、注入防护），参考数据不该走这条路。

---

## 附：所依据的现状事实索引

- 结算：`PaperTradingJob.java:18-25`、`StockPriceRefreshJob.java:39,44`、`PaperTradingListener.java:21-28`、
  `PaperTradingService.java:81,93,111,117,127,132,190,208,220,281`
- 幂等与守卫：`PaperTradingService.java:162,183-187,276-280,342,474-479`
- 规则引擎：`strategy_engine.py:147,263-270,289,391,425`
- Python 端点与契约：`app.py:728,746-759`
- 行情取数：`akshare_client.py:284,297,313`
- 记忆：`MemoryFactService.java:84,96-137,159,320-345,359`、`MemoryService.java:111,357,422,451`、
  `consolidate.py:99`、`ObjectiveFactKeys.java:30-31,82`、`external_source.py:54-56,94,96`
- 审批门禁：`tool_pipeline.py:175-183`
- 消息：`entity/Message.java:51-53`、`messageBus.js`（断线重拉）
- 前端：`Assistant.vue`、`StrategyDetail.vue`
