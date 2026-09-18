# TradingAgents 评估记录（2026-09-18）

> 本文是**决策记录**，不是设计文档。目的：把"要不要用 TradingAgents"这件事一次性查清并留证，
> 免得三个月后再讨论一遍。结论是**不采用**；但其中四件事值得吸收，两件是我们目前缺的。
> 全部原始产物在项目外的沙箱里（`E:\GitHubjob\ta-sandbox`），可整目录删除。

## 0. 结论

| 问题 | 答案 |
|---|---|
| 把它接进来当引擎？ | **不**。它的核心＝LLM 直接产出买卖方向与仓位，与本文档库里的六条"明确不做"直接冲突（见 §4） |
| 值得试吗？ | 值得，而且试出了东西（§3 第 1 条是这次最有价值的观察） |
| 要吸收什么？ | 四件，其中 **always-HOLD / random 基准**与 **look-ahead 守卫**是我们**已经该有却还没有**的（§5） |

## 1. 它是什么（事实，非印象）

PyPI `tradingagents 0.7.0`（Mai0313 维护分支；原始版 TauricResearch/TradingAgents，[arXiv 2412.20138](https://arxiv.org/abs/2412.20138)），基于 LangGraph：

```
Market / News-Sentiment / News / Fundamentals 分析师（各自绑 yfinance 工具）
  → Situation Summariser（压成 ≤400 token 的 BM25 检索 query）
  → Bull ⚔ Bear 辩论 → Research Manager → investment_plan
  → Trader → "FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**"
  → Aggressive → Conservative → Neutral 风控辩论 → Risk Judge
  → SignalProcessor（确定性解析 → TradeRecommendation）
```

- **12 个 LLM agent** + 3 个支撑件；记忆是 5 份 BM25 词法记忆（JSONL 文件）+ 事后 reflector
- 回测台：日期网格、按**次日收盘** mark-to-market、`transaction_cost_bps`（默认 10）+ `slippage_bps`、
  `--max_position_fraction`（默认 0.2，不采信 LLM 报的 1.0）、walk-forward、train/val/test 切分、
  `signal_distribution`、`warning_rate`、`--budget_cap_usd`、`--dry_run`（桩 LLM，$0）
- **四个基准**：`buy_and_hold` / `always_hold` / `sma_crossover` / `random_baseline`

## 2. 它在这台机器上怎么跑起来的（可复现）

数据层是它最大的现实障碍，也是这次唯一需要"绕"的地方：

1. **Yahoo 直接被限流**：`yf.download` 对 `600519.SS`、`000001.SZ`、连 `AAPL` 都返回
   `YFRateLimitError: Too Many Requests`（裸 HTTP 请求甚至 403）。
2. **但它的历史回测永远复用磁盘缓存**：`dataflows/yfinance.py::_is_cache_fresh` 对回测日期直接
   返回 True，缓存文件是 `<results_dir>/data_cache/<TICKER>-YFin-data.csv`。
3. 所以用**我们自己的行情源**（腾讯 ifzq，与产品同一个源）把真实日线写进那个 CSV：
   `600519.SS` 3716 根、`000001.SZ` 3704 根（2011-06-01 ~ 2026-09-17，覆盖它要求的
   `[curr_date-15y, curr_date]`）。脚本：沙箱里的 `seed_cache.py`（复用产品的分页取数，
   只把 `MAX_PAGES` 成本上限临时抬高）。
   **刻意用真实数据**，没有伪造一根 15 年前的 bar 去骗过覆盖检查。

环境：Python 3.12.9（项目是 3.10，所以装在项目外 `E:\GitHubjob\ta-sandbox\py312`）+ 独立 venv +
PyPI 走清华镜像（`pypi.org` 本机超时）。

跑法：

```powershell
# 工具链验证（桩 LLM，$0）
python -m tradingagents backtest --tickers 600519.SS --start 2026-08-14 --end 2026-08-21 `
  --frequency weekly --dry_run
# 真实决策（DeepSeek）
python -m tradingagents backtest --tickers 600519.SS --start 2026-08-14 --end 2026-08-21 `
  --frequency weekly --budget_cap_usd 2 --llm_provider litellm `
  --deep_think_llm deepseek/deepseek-chat --quick_think_llm deepseek/deepseek-chat `
  --response_language zh-CN
```

## 3. 实测结果

**规模**：每个决策 13–14 次 LLM 调用、31–40 次工具调用；两天各一份 65–71KB 的完整对话日志。

| | 2026-08-14 | 2026-08-21 |
|---|---|---|
| 最终信号 | **HOLD** size=0.00 | **HOLD** size=0.00 |
| LLM 调用 | 14 | 13 |
| TOOL_ERROR 出现次数 | 59 | 46 |
| NO_DATA | 40 | — |
| confidence | 0.55 | 0.62 |

回测汇总（2 次决策）：`Buy/Sell/Hold = 0/0/2`、命中率 0%（无交易）、
**`warning_rate = 100%`**、四个基准 `always_hold +0.0000 / buy_and_hold -0.0073 /
random_baseline +0.0056 / sma_crossover +0.0056`。

### 3.1 最有价值的一条观察：**数据缺失时，它默认 HOLD**

它自己的 rationale 写得很清楚（原文摘录）：

> "News sentiment report / News report / Fundamentals report **三份源报告均为管道故障导致的空集报告，
> 明确声明不可读作中性或方向信号，故缺失三维赋零权重**。… 现价 1341.99 距短期支撑 1335 仅 0.5%，
> 任何方向的主动建仓在此位置都缺乏赔率支持。"
> `warning_message`: "本决策仅基于技术面单维证据，置信度低于多维交叉验证情形，**不因三维缺失而倒向任一方向**。"

这正是"**不动不需要付代价**"的现场实证，而且机制比"想不到未来"更具体：

- 3/4 的分析输入取数失败 → 它**拒绝**用残缺证据下注 → 选择与 `always_hold` 完全等价的动作；
- 它把"我不该被当成中性信号"写进了 warning，而不是假装分析完整；
- 结果上它**打平了"什么都不做"**（0.0000），输给一条最朴素的 SMA 规则（+0.0056），
  但赢了 buy-and-hold（-0.0073，因为这段行情在跌）。

换句话说：**在数据受损 + 结果无法归因的结构下，HOLD 是理性选择** —— 这既说明了它的诚实，
也说明了"让不动有代价"必须靠**外部记账**（基准 + 预期登记），而不能指望模型自己变得激进。

### 3.2 它的三个坑（实测）

1. **预算上限对非价目表内模型无效**：`estimated_cost_usd = 0.0000`，因为 LiteLLM 的远程价目表
   取不到（`raw.githubusercontent.com` 超时）、本地备份里没有 DeepSeek 的价格 →
   `--budget_cap_usd 2` **实际从未生效**。它的日志不记 token，所以真实花费拿不到。
   想按预算跑，得先自己补价目表。
2. **数据源全押在 Yahoo / Google News**：A 股的基本面、内部人、分析师预期、新闻全部拿不到
   （59 次 TOOL_ERROR）。它的降级行为是好的（返回 `[TOOL_ERROR]`/`[NO_DATA]` 哨兵而不是编造），
   但结果就是"四个分析师里三个是空的"。
3. **`random_baseline` 与 `sma_crossover` 数值完全相同**（两次窗口都一样）—— 可疑，至少在这次运行里
   随机基准没有体现出随机性。（仅记录观察，未深查其实现。）

## 4. 为什么不能接进我们的产品

| 它的做法 | 我们已定下的原则（本设计文档 §9） |
|---|---|
| Trader / Risk Judge 直接产出买卖方向与仓位 | ❌ 不把逐笔买卖决策交给模型 |
| target_price / time_horizon / 看未来 | ❌ 不预测股价（用户亲口强调过） |
| LangGraph 编排 12 个 agent | ❌ 不做总调度 agent 调子 agent |
| 第二个 agent 运行时（langchain 全家桶） | ❌ 不新建第二个运行时 |
| yfinance + Google News | ❌ 不新建第二个数据源 |
| JSONL 记忆 + `results/*.json` | ❌ 不做文件式记忆 |

六条里它一次踩满；**接进来不是加功能，是替换掉我们这几轮做硬的东西**。

## 5. 值得吸收的四件

| # | 它的做法 | 我们的现状 | 该做什么 |
|---|---|---|---|
| 1 | **`always_hold` 与 `random_baseline` 基准** | 我们只有 buy-and-hold | 加两个基准：这条规则比"什么都不做"、比随机强吗？（几行代码，把"超额"从一个参照系变成三个） |
| 2 | **look-ahead 守卫**：未来日期一律 `[TOOL_ERROR]`；历史数据按 `as_of`（含披露滞后）过滤；无历史存档的数据返回 `[NO_DATA]` 哨兵 | **我们缺这个** | 给取数工具加"禁止未来日期"，并在验证结果里带上 as-of 口径 |
| 3 | **证据门禁**：分析师在拿到任何工具结果之前就产出报告 → 写 `[TOOL_ERROR]` 警告报告，而不是把无根据的文字当证据 | 我们有 audit（`evidence=json_path`） | 对照补齐：报告型输出必须绑定工具证据 |
| 4 | **末尾确定性解析 + `warning_message`**：规范行优先、HOLD 仓位归零、解析失败退保守默认、归一化时带警告 | 这正是我们 P2"LLM 辩论 + 引擎裁决"的设计 | 拿它当对照实现：**归一化必须留痕**（我们已有 `skip_reason`/`unknown_*` 的同款思路） |

## 6. 复现与清理

- 沙箱：`E:\GitHubjob\ta-sandbox`（`py312\` Python 3.12.9、`venv\` 依赖、`ta\` 工作目录与结果、
  `seed_cache.py`）。**与项目完全隔离**，删除该目录即彻底还原。
- 项目内改动：只有本文档。（产品代码、依赖、数据源一处未动。）
- 唯一需要人工留意的：验证/报告里目前**没有** look-ahead 守卫（§5 第 2 条），
  这是本次评估暴露出的我们自身的缺口。
