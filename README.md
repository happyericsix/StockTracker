# StaringAgent

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

**StaringAgent 反过来：它是一个常驻的交易系统。** 策略 7×24 挂在系统里，确定性代码决定"什么时候值得叫醒 AI"，AI 只在被叫醒时做判断，执行与记账全部回到确定性代码。

| | 研究工具型 agent | **StaringAgent** |
|---|---|---|
| 触发 | 用户手动点 | **事件驱动门控**（规则信号/波动异常/持仓过期，零 LLM 成本判断"值不值得开会"） |
| 生命周期 | 一次会话 | 常驻：日线结算 + 盘中盯盘 + 周反思闭环 |
| 执行 | 停在研究结论 | **A 股真实化撮合**：整手/佣金下限/印花税/T+1/涨跌停挡单 |
| 留痕 | 一次报告 | 每条决策带证据快照、角色发言哈希、token 用量、skip 原因（封闭枚举） |
| 学习 | 无 | 周反思提炼教训回流 → 被后续战绩证伪则**自动退役** |

另有完整的个人股票助手功能：实时行情（SSE 快车道）、价格预警、新闻事件雷达与专题聚类、AI 助手（工具调用 + 记忆）、PWA。

## 核心特性

### 🏛 多角色决策委员会（agent 决策模式）

```
证据包（as-of 截止）→ 多头 ⚔ 空头 → 研究主管 → 交易员
                   → 风控三方（激进/保守/中性）→ 风控裁决（唯一决策出口）
```

- **模型决定要不要动，代码决定怎么动**：委员会输出经确定性解析落成封闭枚举，读不懂就 SKIP，绝不猜
- **确定性空方简报**（`agent/dissent.py`）：均线空头排列/回撤/RSI 过热/价升量缩等反方事实由**纯代码**算出进证据包——反对意见的下限不依赖模型的自觉
- **会议上下文**：被召集者知道"谁喊的开会"（含规则信号）、"为哪条策略开会"（策略意图）
- **硬预算**：调用次数/token 双上限 + 每日成本止损线，超限记 `agent_budget_exceeded`（与 HOLD 分开统计）
- **盘中授权**：委员会划定 `allow_open/allow_close/max_position_pct` 边界，盯盘 agent 在边界内秒级反应；止损硬通道不等模型

### ⏰ 事件驱动门控（成本结构的关键）

绝大多数日子结论都是 HOLD——日频召集等于花钱买重复劳动。四条确定性触发（封闭集）才开会：规则信号 / 波动异常（相对 2× 或绝对 3%）/ 持仓过期（持仓 3 天、空仓 10 天）/ 首次会议。没触发就记 `agent_no_new_information`，**"没看"与"看过决定不动"在痕迹里是两件事**。

### 🧾 真实化执行与全链路留痕

- 回测与结算共用同一撮合语义：信号收盘出、次日开盘成交，涨跌停挡单，佣金单笔最低 5 元
- 每条决策落库：证据快照、8 角色发言摘要 + SHA-256、prompt 版本哈希、gate 判定细节
- **决策质量对账**：每笔成交 N 日后回看方向对错，agent vs 规则分开统计——模型好不好由记录说话

### 🔄 经验回流与自进化

- 周反思（1 次 LLM 调用）提炼 ≤3 条可执行教训 → 随经验包回流下一次会议
- **教训退出机制**：教训出生后的 agent 成交已判读 ≥4 笔且胜率 ≤30% → 自动退役，坏教训不会永远污染提示词
- **自进化沙盘**（`finetune/evolve_replay.py`）：as-of 逐日回放一年，委员会交易 + 每周提案改策略，回测闸门把关、冻结段验证
- **委员会消融 harness**（`finetune/committee_ablation.py`）：judge/duo/core4/full8 四变体同行情对比——回答"前七棒到底值不值那个 token"

### 🧠 AI 助手与记忆

工具调用 agent（行情/指标/回测/新闻/记忆工具），默认值披露铁律，批评者角色核查数字出处；客观事实由代码写入，模型不能改写自己的历史；关键词 + 向量（RRF）混合召回。

## 系统架构

```
Vue 3 前端（PWA）
      │ REST / SSE
Java 21 · Spring Boot ── MySQL（唯一数据库，JPA）
      │ WebClient（X-Internal-Token，fail-closed）
Python 3.11 · FastAPI
      ├── agent/：委员会 · 盯盘 · 聊天 ReAct · 周反思 · 记忆 · dissent
      ├── strategy_engine：规则 DSL + 真实化回测（与结算同一引擎）
      └── 数据源：akshare / 东财 / 腾讯行情（自选股同步可选）
```

## 快速开始

依赖：Docker（含 compose 插件）。数据源无需注册任何海外 key；LLM 用 [DeepSeek](https://platform.deepseek.com)（OpenAI 兼容协议，可替换为任何兼容端点，包括本地 Ollama）。

```bash
git clone https://github.com/happyericsix/StaringAgent.git
cd StaringAgent

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

每条策略二选一：**规则**（DSL：均线交叉/RSI/MACD/止损止盈等，引擎自动执行）或 **智囊**（8 角色委员会裁量决策，事件驱动开会）。两种模式走**同一条执行路径**，`fingerprint.decision_mode` 写进每条痕迹，两段净值永远不被悄悄混排。

## 可选功能：同花顺自选股同步

出于合规考虑，本仓库**不含**同花顺协议的第三方实现代码（`vendor/thspypc_src`）。需要扫码绑定/自选同步时，自行拉取后即可启用：

```bash
python python-data-service/_fetch_ths.py   # 拉取上游源码到 vendor/
```

不拉取不影响其他所有功能（`ths_client` 会给出清晰提示）。

## 测试

```bash
# Python（委员会/盯盘/决策解析/消融/记忆 等约 1078 项）
cd python-data-service && python -m pytest tests

# Java（结算/授权/组合/教训退役 等约 430 项）
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
