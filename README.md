# Stock Tracker

> **量化平台把"如何发现统计优势"做到极致，StockTracker 把"如何让一个决策可解释、可验证、可纪律化执行"做到极致。**

> **A 股多智能体模拟交易系统** —— 8 角色 AI 委员会值守你的纸面账户：事件驱动开会、盘中盯盘、真实化撮合、全链路留痕。

[![Java](https://img.shields.io/badge/Java-21-orange)]()
[![Spring Boot](https://img.shields.io/badge/Spring%20Boot-4-green)]()
[![Vue](https://img.shields.io/badge/Vue-3-brightgreen)]()
[![Python](https://img.shields.io/badge/Python-3.11-blue)]()
[![License](https://img.shields.io/badge/License-MIT-purple)]()

> ⚠️ **免责声明**：本项目仅供学习与技术研究，是一个**模拟盘（纸面交易）系统，不接入任何真实交易通道**。项目所有输出均不构成投资建议；市场有风险，决策需谨慎。

---

## 这是什么 / 不是什么

市面上的 LLM 炒股 agent（[TradingAgents](https://github.com/TauricResearch/TradingAgents)、ai-hedge-fund 等）解决的是"**你想问的时候，它给你一份研究报告**"——用户手动触发，跑完即散。

**Stock Tracker 反过来：它是一个常驻的交易系统。** 策略 7×24 挂在系统里，确定性代码决定"什么时候值得叫醒 AI"，AI 只在被叫醒时做判断，执行与记账全部回到确定性代码。

| | 研究工具型 agent | **Stock Tracker** |
|---|---|---|
| 触发 | 用户手动点 | **事件驱动门控**（规则信号/波动异常/持仓过期，零 LLM 成本判断"值不值得开会"） |
| 生命周期 | 一次会话 | 常驻：日线结算 + 盘中盯盘 + 周反思闭环 |
| 执行 | 停在研究结论 | **A 股真实化撮合**：整手/佣金下限/印花税/T+1/涨跌停挡单 |
| 留痕 | 一次报告 | 每条决策带证据快照、角色发言哈希、token 用量、skip 原因（封闭枚举） |
| 学习 | 无 | 周反思提炼教训回流 → 被战绩或新证据证伪则**修订/退役**（版本链保留全部历史） |

另有完整的个人股票助手功能：实时行情（SSE 快车道）、价格预警、新闻事件雷达与专题聚类（多源快讯含财联社电报、个股人气榜热榜、**自选股高影响事件主动推送**）、AI 助手（工具调用 + 记忆）、PWA。

## 核心特性

### 🏛 多角色决策委员会（agent 决策模式）

```
证据包（as-of 截止）→ 多头 ⚔ 空头 → 研究主管 → 交易员
                   → 风控三方（激进/保守/中性）→ 风控裁决（唯一决策出口）
```

- **模型决定要不要动，代码决定怎么动**：委员会输出经确定性解析落成封闭枚举，读不懂就 SKIP，绝不猜
- **确定性空方简报**（`agent/dissent.py`）：均线空头排列/回撤/RSI 过热/价升量缩/估值历史高位等反方事实由**纯代码**算出进证据包——反对意见的下限不依赖模型的自觉
- **会议上下文**：被召集者知道"谁喊的开会"（含规则信号）、"为哪条策略开会"（策略意图）
- **硬预算**：调用次数/token 双上限 + 每日成本止损线，超限记 `agent_budget_exceeded`（与 HOLD 分开统计）
- **盘中授权**：委员会划定 `allow_open/allow_close/max_position_pct` 边界，盯盘 agent 在边界内秒级反应；止损硬通道不等模型

### ⏰ 事件驱动门控（成本结构的关键）

绝大多数日子结论都是 HOLD——日频召集等于花钱买重复劳动。四条确定性触发（封闭集）才开会：规则信号 / 波动异常（相对 2× 或绝对 3%）/ 持仓过期（持仓 3 天、空仓 10 天）/ 首次会议。没触发就记 `agent_no_new_information`，**"没看"与"看过决定不动"在痕迹里是两件事**。

### 🧾 真实化执行与全链路留痕

- **回测与模拟盘共用同一份执行契约**（`agent/execution_contract.py`）：回测按"次日开盘 + 滑点"成交，模拟盘日线按"信号当根收盘价 + 滑点"成交（15:30 结算结构性给不出次日开盘价）；每条痕迹带**执行指纹**（成交价口径/复权模式/费率版本/引擎版本/决策来源），**指纹不同不许对比**——口径是数据的属性，不做双轨代码。涨跌停挡单、T+1、整手、印花税、过户费、佣金单笔最低 5 元
- 每条决策落库：证据快照、8 角色发言摘要 + SHA-256、prompt 版本哈希、gate 判定细节
- **决策质量对账**：每笔成交 5 个交易日后回看方向对错（买入看涨对、卖出看跌对），还没走满窗口的一律 pending 不判读——模型好不好由记录说话

### 🔄 经验回流与自进化

- 周反思（1 次 LLM 调用）提炼 ≤3 条可执行教训 → 随经验包回流下一次会议
- **教训退出机制**：教训出生后的 agent 成交已判读 ≥4 笔且胜率 ≤30% → 自动退役，坏教训不会永远污染提示词
- **质疑回路**：周反思带着本策略现存旧教训开会（`prior_lessons`），本周材料与某条旧教训明确矛盾时可输出**修订**或**停用**指令；指令过 id 白名单双重闸门（解析器丢弃非法 id + Java 只认喂入集合），修订落 pending 等人工确认、停用直接生效——教训可以被系统自己质疑，但 agent 的改写永远过人这一关
- **教训修订走取代链**（与事实层同构）：修订 = 追加新版本行指向旧行，内容永不 UPDATE、历史永远可回看；复发计数只数"独立出生"的行，修订不灌水升格信号
- **自进化沙盘**（`finetune/evolve_replay.py`）：as-of 逐日回放一年，委员会交易 + 每周提案改策略，回测闸门把关、冻结段验证；估值/财务证据块按 as-of 切片与生产对齐（回放里委员会看到的和生产同一套证据）
- **策略修订闭环**（`agent/revision.py`，生产版）：健康信号触发 → 1 次 LLM 参数级提案 → 两段式门禁（近期加权主判 + 留出段反证 + 弱基准加码 + **复杂度罚金**：BIC 式，每净增一条条件收 5pt 加权年化罚金，删条件奖励封顶）→ **带证据交人审批，门禁过了也不自动应用**
- **委员会消融 harness**（`finetune/committee_ablation.py`）：judge/duo/core4/full8 四变体同行情对比——回答"前七棒到底值不值那个 token"

### 🧠 AI 助手与记忆

工具调用 agent（行情/指标/回测/新闻/记忆工具），默认值披露铁律，批评者角色核查数字出处；客观事实由代码写入，模型不能改写自己的历史；关键词 + 向量（RRF）混合召回。

四层记忆（事件账本 / 会话摘要 / 语义事实 / 经验教训）内容只追加：改口与撤回走取代链与逻辑撤回，历史永远可回溯。记忆页全白盒：每条记忆可查**原始来源对话**、可人工**编辑**（落新版本，旧版保留）、可一键停用，还能对任意一条经验**反问 agent**——它被要求摆出这条经验的来源与后续战绩自证，站不住就认错并建议停用或修订（建议不自动生效，决定权始终在人）。经验按策略分区（`strategy:N`），A 策略的教训绝不会流进 B 策略的委员会提示词。

## 模拟盘表现示例（生产实例，数据截至 2026-10-16）

以下数字全部来自一台真实部署的生产实例（2 核 4G 云服务器），未做挑选。**样本极小——1 条策略、9 个交易日、6 笔成交——不构成任何业绩暗示**；放这一节的目的是展示"留痕与对账长什么样"，不是展示收益。

实盘策略：中国银行（601988）· 价格上穿 60 日线趋势跟随 · **智囊（agent）模式**，初始资金 100,000 元。

**净值快照**（含全部费用；净值恒等式 `equity == cash + shares × price` 零容差成立）：

| 日期 | 净值（元） | 累计 |
|---|---|---|
| 10-08 | 100,000.00 | — |
| 10-09 | 100,000.00 | — |
| 10-12 | 102,429.49 | +2.43% |
| 10-13 | 102,989.06 | +2.99% |
| 10-14 | 101,945.11 | +1.95% |
| 10-15 | 100,853.41 | +0.85% |
| 10-16 | 100,853.41 | +0.85%（空仓） |

**三段已了结往返**（毛收益 = 卖出价 / FIFO 入场成本 − 1，与 realizedPnl 同口径、不含费）：

| # | 买入 | 卖出 | 毛收益 |
|---|---|---|---|
| 1 | 10-12 @6.7968 | 10-13 @7.0230 | **+3.33%** |
| 2 | 10-13 @7.0671 | 10-14 @7.1129 | **+0.65%** |
| 3 | 10-14 @7.1271 | 10-15 @6.9600 | **−2.34%** |

**29 条留痕 = 6 笔成交 + 23 次"不动"，每一次不动都有名有姓**（封闭枚举 skip 原因）：

| 痕迹 | 笔数 | 含义 |
|---|---|---|
| 成交（无 skip） | 6 | — |
| `watch_hold` | 5 | 盯盘 agent 被问过、决定不动（含否决规则信号） |
| `already_traded_today` | 4 | 盘中已动过，收盘只重估不下单 |
| `agent_intraday_disabled` | 3 | 委员会开过会并主动关闭盘中执行 |
| `awaiting_authorization` | 3 | 委员会尚未给过盘中授权 |
| `t1_blocked` | 3 | T+1：当日买入不可卖 |
| `no_bar` / `rule_not_met` / `agent_no_new_information` | 2 / 2 / 1 | 无 K 线 / 规则未命中 / 无新信息没开会 |

触发结构：event 15 / cron 9 / watch 5——**事件驱动是常态，cron 只是兜底**。结算无人值守：`paper-daily` 连续 5 个交易日全部 ok，`paper-weekly` 周反思、`paper-periodic` 定期复盘按期触发。

至于 5 交易日方向对账：这 6 笔成交**全部还没走满窗口，`evaluated=0`**。宁缺毋滥——窗口没走满就不判读，这条纪律正是这一节最想展示的东西。

## 系统架构

```
Vue 3 前端（PWA）
      │ REST / SSE
Java 21 · Spring Boot ── MySQL（唯一数据库，JPA）
      │ WebClient（X-Internal-Token，fail-closed）
Python 3.11 · FastAPI
      ├── agent/：委员会 · 盯盘 · 聊天 ReAct · 周反思 · 记忆 · dissent
      ├── strategy_engine：规则 DSL + 真实化回测（与结算同一引擎）
      └── 数据源：akshare / 东财 / 腾讯行情 / 财联社电报（自选股同步可选）
```

## 快速开始

依赖：Docker（含 compose 插件）。数据源无需注册任何海外 key；LLM 用 [DeepSeek](https://platform.deepseek.com)（OpenAI 兼容协议，可替换为任何兼容端点，包括本地 Ollama）。

```bash
git clone https://github.com/happyericsix/stock-tracker.git
cd stock-tracker

cp .env.example .env
# 编辑 .env，三个必填值：
#   MYSQL_ROOT_PASSWORD / INTERNAL_API_TOKEN（强随机）/ DEEPSEEK_API_KEY

docker compose build          # 首次约 10-20 分钟
docker compose up -d
docker compose ps             # 五个容器应全部 Up（mysql/redis healthy）

# 验收
curl -I http://localhost/                  # 200：前端页
curl http://localhost/api/v1/market/status # 401：API 通（未登录）
```

浏览器打开 `http://localhost/` 注册账号 → 加自选 → 让 AI 助手建一条策略 → 开模拟盘。服务器部署与 HTTPS 上线路线见 [DEPLOY.md](DEPLOY.md)。

内存小于 4G 的机器用覆盖文件：`docker compose -f docker-compose.yml -f docker-compose.small.yml up -d`。

### 决策模式

每条策略二选一：**规则**（DSL：均线交叉/RSI/MACD/止损止盈/估值分位等，引擎自动执行）或 **智囊**（8 角色委员会裁量决策，事件驱动开会）。两种模式走**同一条执行路径**，`fingerprint.decision_mode` 写进每条痕迹，两段净值永远不被悄悄混排。

## 可选功能：同花顺自选股同步

出于合规考虑，本仓库**不含**同花顺协议的第三方实现代码（`vendor/thspypc_src`）。需要扫码绑定/自选同步时，自行拉取后即可启用：

```bash
python python-data-service/_fetch_ths.py   # 拉取上游源码到 vendor/
```

不拉取不影响其他所有功能（`ths_client` 会给出清晰提示）。

## 测试

```bash
# Python（委员会/盯盘/决策解析/消融/记忆/周反思质疑回路 等约 1130 项）
cd python-data-service && python -m pytest tests

# Java（结算/授权/组合/教训退役与修订 等约 490 项）
./mvnw -s maven-settings.xml test
```

## 目录导览

| 路径 | 内容 |
|---|---|
| `python-data-service/agent/trading_committee.py` | 8 角色委员会 + 事件驱动门控 |
| `python-data-service/agent/dissent.py` | 确定性空方简报 |
| `python-data-service/agent/intraday_watch.py` | 盘中盯盘交易员 |
| `python-data-service/agent/strategy_engine.py` | 规则 DSL + 真实化回测引擎 |
| `python-data-service/finetune/` | 自进化沙盘 + 委员会消融 harness |
| `src/main/java/.../PaperTradingService.java` | 结算/执行/留痕/教训退役（Java 侧心脏） |
| `docs/ROADMAP.md` | 工程决策与事故复盘记录 |

## 借鉴致谢

- [TradingAgents](https://github.com/TauricResearch/TradingAgents)（Apache-2.0）：多空辩论 + 风控审批的委员会形状、规范行输出契约
- [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund)（MIT）：把风格化纪律写进角色卡的做法

本项目借鉴其**形状**，内容与实现按自身口径重写；执行、留痕、门控、经验回流为本项目独立设计。

## License

[MIT](LICENSE) © 2026 happyericsix
