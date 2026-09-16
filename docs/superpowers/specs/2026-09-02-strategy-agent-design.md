# Strategy Agent 设计（自然语言 → 策略 JSON → 回测/模拟盘）

日期：2026-09-02
状态：已与用户逐节对齐，待最终审阅

## 1. 背景与目标

把现有 Stock Tracker 的量化能力封装成一个 **ReAct Agent**：用户在聊天框用自然语言描述策略，Agent 调用工具理解、校验并生成一份**结构化策略配置 JSON**；后端引擎读取同一份 JSON，执行**回测**和**前向模拟盘**。策略 JSON 是唯一契约，将来实盘引擎复用同一格式。

目标用户仍是普通散户，输出必须用大白话解释，并明确“模拟盘是虚拟账户，不构成投资建议”。

## 2. v1 范围与非目标

**范围内**
- 全部聊天消息统一进入 Agent（替代现有 `parse_intent` 固定分发路径）
- 自然语言 → ReAct Agent → 策略 JSON
- 策略 JSON 持久化 + 策略库（列表/详情/编辑/运行/暂停）
- 基于策略 JSON 的历史回测（信号清单、交易明细、权益曲线、指标）
- 日频前向模拟盘（虚拟账户、虚拟成交、权益曲线）
- 规则类策略 DSL：MA 金叉/死叉、RSI 超买/超卖、MACD 金叉/死叉、价格上穿/下穿、仓位比例、止盈/止损/跟踪止盈

**非目标（明确延后）**
- 预测类模型（LGB / Transformer / DQN）作为主链路核心；降级为可选辅助信号，不进入 v1
- 因子模型 / 多股选股组合
- MCP 接入外部数据源
- 实盘交易 / 券商网关 / 下单
- 盘中高频/实时撮合模拟盘

## 3. 核心契约：策略 JSON

`strategy_schema.py`（pydantic）负责校验。v1 schema 示例：

```json
{
  "schema_version": "1.0",
  "name": "20日均线上穿60日买入",
  "symbol": "600519",
  "initial_capital": 100000,
  "data": { "period": "day", "lookback_days": 250 },
  "position": { "type": "percent", "size_pct": 100 },
  "entry": {
    "logic": "all",
    "conditions": [
      { "type": "ma_cross", "fast": 20, "slow": 60, "direction": "above" }
    ]
  },
  "exit": {
    "logic": "any",
    "conditions": [
      { "type": "ma_cross", "fast": 20, "slow": 60, "direction": "below" },
      { "type": "stop_loss_pct", "value": -8 },
      { "type": "take_profit_pct", "value": 15 }
    ]
  },
  "risk": { "commission_pct": 0.1, "slippage_pct": 0.1 }
}
```

v1 条件类型：

- `ma_cross`：`fast`、`slow`、`direction`（`above` / `below`）
- `rsi_above` / `rsi_below`：`value`
- `macd_cross`：`direction`（`above` / `below`）
- `price_above` / `price_below`：`value`
- `stop_loss_pct`、`take_profit_pct`、`trailing_stop_pct`：`value`

`entry.logic` / `exit.logic` 支持 `all`（所有条件同时满足）或 `any`（任一满足）。
`position.type` 支持 `full`（全仓）或 `percent`（`size_pct` 百分比）。

## 4. 架构与组件

```
前端 (Vue)
  ├─ 聊天输入栏（自然语言描述策略）
  └─ 策略库页面（列表/详情/回测/模拟盘）
        │  JWT
        ▼
Spring Boot (:8080)
  ├─ ChatService        → 异步调 Python Agent，SSE 回显
  ├─ StrategyController  → 策略 CRUD / 回测触发 / 模拟盘启停
  ├─ StrategyService     → 策略 JSON 持久化、校验、调用 Python
  ├─ PaperTradingService → 虚拟账户/持仓/成交单/权益结算
  └─ PaperTradingJob     → 日频模拟盘调度
        │  HTTP(WebClient)
        ▼
Python FastAPI (:8000)
  ├─ agent/react_agent.py    → ReAct 循环
  ├─ agent/tool_registry.py  → 工具注册表
  ├─ agent/strategy_schema.py→ JSON 校验
  ├─ agent/strategy_engine.py→ 规则求值 + 回测执行
  └─ 复用 quant_model / backtest / akshare
```

### Python 新增

- `agent/tool_registry.py`：工具注册表，工具含 `name`、`description`、参数 schema、执行函数。
- `agent/react_agent.py`：ReAct 循环，最多 12 步（`MAX_STEPS = 12`，本行原先写 6，已按代码修正）；LLM 输出"思考 + 工具调用"或"最终答案"。
- `agent/memory.py` / `agent/recall.py` / `agent/consolidate.py`：记忆系统（账本、前情提要、
  事实与经验），设计见 `2026-09-16-memory-system-design.md`。
- `agent/strategy_schema.py`：pydantic 校验策略 JSON。
- `agent/strategy_engine.py`：指标计算、规则求值、回测执行、单日求值。
- 新端点：
  - `POST /api/v1/agent/chat`：聊天统一入口
  - `POST /api/v1/strategies/validate`
  - `POST /api/v1/strategies/backtest`
  - `POST /api/v1/strategies/evaluate-bar`

工具集合（v1）：

- `search_stock`：股票名 → 代码
- `get_quote`：实时行情
- `get_history`：历史 K 线
- `get_indicators`：技术指标
- `validate_strategy`：校验策略 JSON
- `backtest_strategy`：按 JSON 回测
- `finalize_strategy`：输出最终 JSON + 人话总结

### Java 新增

- 实体与 Repository：`Strategy`、`PaperAccount`、`PaperTrade`
- `StrategyClient`：WebClient 调 Python 策略端点（或扩展 `AkshareStockClient`）
- `StrategyService`：CRUD、归属校验、回测触发、校验
- `PaperTradingService`：虚拟账户结算、成交、权益曲线、幂等去重
- `PaperTradingJob`：日频调度启用中的模拟盘
- `StrategyController` / `PaperTradingController`
- 前端策略库页面

## 5. 数据流

**生成策略（聊天）**
前端 → Java `/api/v1/chat/send` → `ChatService` 统一异步调 Python `/api/v1/agent/chat` → ReAct 循环调用工具 → `finalize_strategy` 产出 JSON → 返回 `{replies, strategy_json}` → Java 保存 `Strategy` + 落消息库 + SSE 推送。

**历史回测**
前端策略库点“回测” → Java `StrategyController` → Python `/api/v1/strategies/backtest` → Python 引擎按日遍历历史、跟踪持仓和止盈止损 → 返回信号、交易、权益曲线和指标 → Java 回显。

**前向模拟盘**
`PaperTradingJob` 每个交易日收盘后对每个启用策略调 Python `/api/v1/strategies/evaluate-bar`，请求体带 Java 端的 `{entry_price, quantity}` 持仓上下文；Python 返回 `{signal, matched_conditions, price, date}`；Java `PaperTradingService` 结算虚拟成交与权益。缺持仓上下文时，Python 只评估技术入场/出场，不评估止损止盈，避免误成交。

## 6. 错误处理与边界

- ReAct：最多 6 步、单工具超时；LLM 返回非法 JSON 自动重试一次，仍失败降级为“没理解/请换个说法”。
- JSON 校验：Agent 自检 `validate_strategy`；最终仍不合法则不落库，只回人话错误。
- 回测：历史不足 20 条、代码找不到、条件类型不支持 → 返回结构化 `{error}`，不抛 500。
- 模拟盘：逐策略隔离，单条失败只 `log` + 跳过；用 `strategyId + tradeDate` 去重保证结算幂等。
- 服务间：Java 调 Python 单次 60s 超时，失败回退固定文案；Python 新端点 `try/except` 返回结构化错误。
- 权限：策略/模拟盘 API 全走 JWT，并校验资源归属。
- 安全：模拟盘明确为虚拟账户，绝不接真实下单。

## 7. 测试策略

- Python 单测（pytest）：`strategy_schema` 合法/非法；`strategy_engine` 各条件与 `all/any`、全仓/百分比；`react_agent` mock LLM 测工具选择、max_steps、非法 JSON 重试与降级。
- Python API 冒烟（TestClient）：`validate` / `backtest` / `evaluate-bar` 用固定历史夹具。
- Java 单测（JUnit）：`PaperTradingService` 买入/卖出/权益/幂等；`StrategyService` 归属校验与 CRUD。
- 契约测试：Java 与 Python 共享策略 JSON 夹具；Java 用 WireMock 桩 Python 响应。
- 验收：手动跑通“20 日均线上穿 60 日买入” → JSON → 回测 → 模拟盘 → 次日结算。

## 8. 后续路线图

- 因子模型 / 多股选股组合（`strategy_type` 扩展位预留）
- MCP 接入研报、财报、新闻等外部数据源
- 实盘券商网关（复用同一策略 JSON）
- 预测模型降级为可选辅助信号，按需回填
