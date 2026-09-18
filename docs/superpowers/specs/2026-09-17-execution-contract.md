# 执行契约（冻结）· 2026-09-17

> **状态**：已实现常量与测试，**尚未被结算路径使用**（第一步是冻结契约，第二步才接实现）。
> **代码位置**：`python-data-service/agent/execution_contract.py`（Python 侧真相源）、
> `src/main/java/com/happyericsix/stocktracker/service/ExecutionContract.java`（Java 侧对称）。
> **测试**：`tests/test_execution_contract.py`、`tests/test_execution_contract_java.py`、`tests/test_money_policy.py`（46 个用例）。

## 0. 它解决什么问题

「模拟盘 vs 回测」的对照是模拟盘存在的**唯一理由**，而它成立的前提是两边口径一致且可查。
现状是隐式且不一致：

| 路径 | 成交价 | 证据 |
|---|---|---|
| 回测 `run_backtest_realistic` | **次日开盘价** + 滑点 | `strategy_engine.fill_open(f = i + 1, …)` |
| 模拟盘（日线与实时共用） | **信号当根收盘价** + 滑点 | `evaluate_bar` 返回 `closes[idx]`；`PaperTradingService.applyBarResult:301,307` 直接拿它当 fill |

而且**没有任何地方记录"这条痕迹属于哪个口径"**。于是两个数字从第一天起就不可比，
而"不可比"会被误读成"策略在实盘变差了"。

## 1. 四个口径的当前决定

| # | 口径 | 决定 | 理由（含证据） |
|---|---|---|---|
① | **成交价** | P0 日线用 `close`；实时用 `realtime_last`；`next_open` 已声明但暂不使用 | 模拟盘在**当日 15:30** 结算，而次日开盘价要到明天 9:30 才知道 → **当日结算结构上给不出次日开盘价**。要用它必须引入"挂单 + 两阶段结算"（P2），届时改 `FILL_BASIS_BY_SETTLEMENT` 一行 |
② | **复权** | 结算用 `none`（不复权）；回测保持 `qfq` 但必须记录 | 除权日价格真的跳空（与用户账户所见一致）。**前复权数据随时间变化**（除权后历史价被重算）→ 回测本身也不可复现，所以 `adjust_mode` 必须随结果记录。除权日 P0 先 `skip + ex_dividend_day` 标注（避免"假暴跌触发止损"） |
③ | **精度** | 价格 4 位 / 金额 2 位 / 净值 2 位 / 收益率 4 位，`ROUND_HALF_UP`，一律 `Decimal`（Java 侧 `BigDecimal`，列改 `DECIMAL`） | 净值恒等式要**零容差**成立；现在 Java 全程 `double`，逐笔对账必然出现"说不清来源的差额" |
④ | **单轨/双轨** | **不做双轨代码**，做"口径指纹 + 对比前校验" | 想换口径＝改一处声明；历史数据仍解释得通（每行带自己的指纹）；用户永远只看到一个口径标签 |

## 2. 常量封闭集

| 组 | 值 |
|---|---|
决策 `DECISIONS` | `buy` / `sell` / `skip` |
结算类型 `SETTLEMENT_KINDS` | `daily` / `realtime` |
成交价 `FILL_BASES` | `close` / `next_open` / `realtime_last` |
复权 `ADJUST_MODES` | `none` / `qfq` |
跳过原因 `SKIP_REASONS` | `market_closed` / `suspended` / `no_bar` / `data_unavailable` / `limit_blocked` / `t1_blocked` / `ex_dividend_day` / `insufficient_cash_for_one_lot` / `insufficient_cash` / `invalid_price` / `rule_not_met` |

**归一规则**：未知值 → `unknown_<原值>`（空值 → `unknown_unspecified`，截断到 32 字符），
**记日志但不抛异常**。理由：结算路径上因为一个枚举值不认识就中断，代价远大于记一条 `unknown_*`；
但也绝不静默丢弃 —— 否则"为什么没成交"永远统计不出来。

`SKIP_REASONS` 的依据是代码里**真实存在**的没成交路径：`PaperTradingService` 的 8 处
静默 `return account`（买不起一手/现金不足/价格非法/T+1…）+ 引擎侧 `bar_date_missing`。

## 3. 执行指纹与"能不能对比"

```
ExecutionFingerprint = { fill_basis, adjust_mode, money_policy_version, engine_version }
compare_allowed(a, b) → (能否, 不能的原因)
```

规则一条：**指纹不同的两份结果不可比**。`compareBlockReason` 会列出不一致的字段
（`fill_basis(close vs next_open)`），前端直接展示"口径不同，不可比"，而不是硬凑一个差额——
差额看起来像信息，实际是错误。

`engine_version` **由代码与常量推导**（`strategy_engine.py` 源码 + 契约常量 + 精度版本的 sha256 前 12 位），
不是人工维护的字符串 —— "记得改版本号"这件事一定会忘。

## 4. 快照：不写死的方式

```
snapshot = { schema_version, bar{...}, indicators{...}, params{...}, fingerprint{...}, extra{...} }
```

- `indicators` 的键由 **`indicator_keys_for(config)`** 推导（策略实际用到什么算什么：
  `ma_cross fast/slow` → `ma_20/ma_60`；`price_cross_ma window` → `ma_120`；`rsi_*` → `rsi`；
  `macd_cross` → `macd_dif/macd_dea`；永远含 `close`）。
  **以后加条件类型只改这一个映射**，快照与痕迹的形状不变；
- 快照自带 `schema_version`：历史行说得出自己属于哪一版，加字段不需要回填历史；
- `extra` 永远存在（默认 `{}`），渲染层不必判 `None`。

## 5. 精度与对账

- `MoneyPolicy`：单处定义 scale 与 `ROUND_HALF_UP`；接受 `int/float/str/Decimal`（浮点经 `str()` 转换，
  吸收 `0.1+0.2` 这类毛刺）；**拒绝 `None` 与 `bool`**（`bool` 是 `int` 子类，放过去会变成 1.00）；
- `shares()` 返回整数（向下取整，整手由结算逻辑保证）；
- `equity_identity_holds(cash, shares, price, equity)`：**零容差**；
- 测试里有一条直接证据：1000 笔逐笔累加（Decimal）等于一次性求和 = `12350670.00`，
  而同一笔账用 `float` 走一遍**对不上** —— 这就是不能用 `double` 的原因。

## 6. 刻意不做

- ❌ 口径**不放进配置文件或数据库**：改动必须走代码评审，并由 `/health` 暴露当前生效值；
  否则它会变成"没人知道当前值"的黑箱。
- ❌ **不做双轨代码**（两套结算路径）：口径是数据属性（指纹），不是分支。
- ❌ 本模块不做计算：指标求值仍在 `strategy_engine`（单一职责）。

## 7. 下一步（按顺序）

1. **接第一个消费者**：`evaluate-bar` 回传快照（同时回当根收盘与下一根开盘，由结算层按声明选），
   并配契约测试 —— 只有这样，"痕迹带口径"才不是空话；
2. Java 侧结算改用 `BigDecimal` + 列改 `DECIMAL`（含迁移脚本与老数据四舍五入）；
3. `paper_trade_trace` 落库（`decision`/`skip_reason`/`fingerprint`/`snapshot`）；
4. 参考数据两项：**交易日历** 与 **ST 5% 涨跌停**；除权除息数据（P0 只做"跳过+标注"）；
5. `PaperTradingService` 8 处静默 `return account` 逐处改为带 `skip_reason` 的痕迹。

> 说明：本步只冻结契约与常量，**尚未改动任何结算行为** —— diff 里没有一行影响现有成交逻辑，
> 所以不需要回归跑批验证；等第 1 步接上消费者时，才开始需要真机对账。
