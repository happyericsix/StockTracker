# agent/react_agent.py
from __future__ import annotations
import json
import logging
import queue
import threading

import llm_service
from agent.tool_registry import TOOL_SCHEMAS, execute_tool
from agent.strategy_schema import extract_strategy_json, validate_strategy_config

logger = logging.getLogger(__name__)
MAX_STEPS = 12
TOOL_TIMEOUT = 15

SYSTEM_PROMPT = """你是股票策略助手。用户可能让你生成交易策略。
可用工具分为两类：
1. 事实与验证工具：search_stock、get_quote、get_history、get_indicators、
   get_risk_metrics、validate_strategy、backtest_strategy、finalize_strategy。
2. 模型诊断工具：get_model_status、get_model_consensus。这些只是低权重参考，
   不能作为买卖结论，置信度低或不可用时必须忽略。

核心原则：只基于工具返回的事实、规则和回测结果给建议。不要预测具体价格点位，
禁止出现“明天必涨/必跌”“预测涨到 XX 元”这类说法。模型诊断工具默认只用于
说明“模型是否可参考”，不能替代规则、风控和历史回测。

若用户在描述策略，请先查行情和技术指标，必要时查看风险指标和模型状态，然后生成
完整的策略 JSON，调用一次 validate_strategy 校验，成功后调用 backtest_strategy
做一次历史回测，再调用 finalize_strategy，并在最终回复中用 ```json 代码块输出
同一份 JSON。不要反复猜测字段格式；如果校验失败，根据错误信息一次性修正后重新
校验。回测结果只用于验证规则是否成立，不代表未来收益。

策略 JSON 必须严格符合以下结构：
```json
{
  "schema_version": "1.0",
  "name": "策略名称",
  "symbol": "600519",
  "initial_capital": 100000,
  "data": {"period": "day", "lookback_days": 250},
  "position": {"type": "percent", "size_pct": 100},
  "entry": {
    "logic": "all",
    "conditions": [
      {"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}
    ]
  },
  "exit": {
    "logic": "any",
    "conditions": [
      {"type": "ma_cross", "fast": 20, "slow": 60, "direction": "below"},
      {"type": "stop_loss_pct", "value": -8}
    ]
  },
  "risk": {"commission_pct": 0.1, "slippage_pct": 0.1}
}
```

条件 type 只能是：ma_cross（需 fast/slow/direction）、rsi_above（需 value）、
rsi_below（需 value）、macd_cross（需 direction）、price_above（需 value）、
price_below（需 value）、stop_loss_pct（需 value）、take_profit_pct（需 value）、
trailing_stop_pct（需 value）。direction 只能是 above 或 below。
entry.logic / exit.logic 只能是 all 或 any。position.type 只能是 full 或 percent。
如果用户没有指定股票，请先调用 search_stock 解析，不要擅自假设股票代码。
若用户只是闲聊或查行情，直接回复，不需要生成策略 JSON。"""

CORRECTION_PROMPT = "请直接输出最终策略 JSON，不要输出推理过程。必须用 ```json 代码块给出 schema_version=1.0 的策略 JSON。"
DEGRADE_REPLY = "没理解，请换个说法描述你的策略"


def _describe_conditions(conditions):
    labels = []
    for cond in conditions or []:
        ctype = cond.get("type", "")
        if ctype == "ma_cross":
            labels.append(f"MA{cond.get('fast')}/{cond.get('slow')} {cond.get('direction')}")
        elif ctype == "macd_cross":
            labels.append(f"MACD {cond.get('direction')}")
        elif ctype in {"rsi_above", "rsi_below", "price_above", "price_below",
                       "stop_loss_pct", "take_profit_pct", "trailing_stop_pct"}:
            labels.append(f"{ctype} {cond.get('value')}")
        else:
            labels.append(ctype)
    return "；".join(labels) if labels else "未配置"


def _build_strategy_reply(cfg, backtest):
    data = cfg.model_dump()
    entry = data.get("entry") or {}
    exit_rule = data.get("exit") or {}
    lines = [
        f"✅ 已生成策略「{data.get('name', '策略')}」（{data.get('symbol', '')}）",
        "",
        "**策略配置**",
        f"- 入场条件：{_describe_conditions(entry.get('conditions'))}",
        f"- 出场条件：{_describe_conditions(exit_rule.get('conditions'))}",
    ]

    lines.append("")
    lines.append("**回测验证**")
    if isinstance(backtest, dict) and "total_return_pct" in backtest:
        lines.extend([
            f"- 总收益：{backtest.get('total_return_pct')}%",
            f"- 买入持有：{backtest.get('buy_and_hold_return_pct')}%",
            f"- 超额收益：{backtest.get('excess_return_pct')}%",
            f"- 最大回撤：{backtest.get('max_drawdown_pct')}%",
            f"- 夏普比率：{backtest.get('sharpe_ratio')}",
            f"- 胜率：{backtest.get('win_rate')}%",
            f"- 交易笔数：{backtest.get('trade_count')}",
        ])
    else:
        lines.append("- 当前历史数据不足，回测暂未完成；策略已保存，可稍后在策略详情重试。")

    lines.extend([
        "",
        "策略已保存到策略库，可继续运行回测或启动模拟盘。以上仅作规则验证，不构成收益承诺。",
    ])
    return "\n".join(lines)


class _ToolResult:
    def __init__(self):
        self._event = threading.Event()
        self._value = None
        self._exc = None

    def set_result(self, value):
        self._value = value
        self._event.set()

    def set_exception(self, exc):
        self._exc = exc
        self._event.set()

    def result(self, timeout=None):
        if not self._event.wait(timeout):
            raise TimeoutError("tool execution timed out")
        if self._exc is not None:
            raise self._exc
        return self._value


class _DaemonWorkerPool:
    def __init__(self, max_workers=2, thread_name_prefix="react-agent-tool"):
        self._queue = queue.Queue()
        self._threads = []
        for index in range(max_workers):
            thread = threading.Thread(
                target=self._worker,
                name=f"{thread_name_prefix}-{index}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def _worker(self):
        while True:
            item = self._queue.get()
            if item is None:
                break
            result, fn, name, args = item
            try:
                value = fn(name, args)
            except Exception as exc:
                result.set_exception(exc)
            else:
                result.set_result(value)

    def submit(self, fn, name, args):
        result = _ToolResult()
        self._queue.put((result, fn, name, args))
        return result


_TOOL_EXECUTOR = _DaemonWorkerPool(max_workers=2)


def _looks_like_strategy_attempt(text):
    lowered = (text or "").lower()
    return "```json" in lowered or "schema_version" in lowered


def _looks_like_strategy_request(text):
    lowered = (text or "").lower()
    keywords = ("策略", "买入", "卖出", "止损", "止盈", "均线", "rsi", "macd",
                "仓位", "回测", "模拟盘", "建仓", "平仓")
    return any(keyword in lowered for keyword in keywords)


def _parse_tool_args(raw_args):
    if raw_args is None:
        return {}
    if isinstance(raw_args, dict):
        return raw_args
    if not isinstance(raw_args, str):
        return {}
    try:
        parsed = json.loads(raw_args)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _execute_tool_bounded(name, args, timeout=None):
    if timeout is None:
        timeout = TOOL_TIMEOUT
    result = _TOOL_EXECUTOR.submit(execute_tool, name, args)
    try:
        return result.result(timeout=timeout)
    except TimeoutError:
        return {"error": f"tool {name} timed out after {timeout}s"}
    except Exception as exc:
        return {"error": f"tool {name} failed: {exc}"}


def run_agent(user_id, message, history=None):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": message})
    retry_used = False

    for _ in range(MAX_STEPS):
        try:
            choice = llm_service.chat_completion(messages, tools=TOOL_SCHEMAS)
        except Exception as e:
            logger.error("agent llm error: %s", e)
            return {"replies": ["AI 服务暂不可用，请稍后再试"], "strategy_json": None}

        if not isinstance(choice, dict):
            return {"replies": ["AI 服务暂不可用，请稍后再试"], "strategy_json": None}

        msg = choice.get("message") or {}
        if not isinstance(msg, dict):
            msg = {}
        messages.append(msg)

        tool_calls = msg.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            tool_calls = []

        if tool_calls:
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                if not isinstance(fn, dict):
                    fn = {}
                name = fn.get("name") or ""
                args = _parse_tool_args(fn.get("arguments"))
                result = _execute_tool_bounded(name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": json.dumps(result, ensure_ascii=False),
                })
            continue

        text = msg.get("content") or "我没理解，请换个说法。"
        if not isinstance(text, str):
            text = str(text)

        strategy = extract_strategy_json(text)
        if isinstance(strategy, dict):
            cfg, err = validate_strategy_config(strategy)
            if err is None:
                backtest_result = execute_tool("backtest_strategy", {"strategy_json": cfg.model_dump()})
                if not isinstance(backtest_result, dict) or not backtest_result.get("valid"):
                    backtest_error = (backtest_result or {}).get("error") if isinstance(backtest_result, dict) else "未知错误"
                    if retry_used:
                        return {"replies": [DEGRADE_REPLY], "strategy_json": None}
                    retry_used = True
                    messages.append({
                        "role": "user",
                        "content": f"策略校验通过，但回测失败：{backtest_error}。请检查数据与规则后重新输出策略 JSON。",
                    })
                    continue
                backtest = backtest_result.get("backtest")
                summary = _build_strategy_reply(cfg, backtest)
                return {
                    "replies": llm_service.split_replies(summary),
                    "strategy_json": cfg.model_dump(),
                    "backtest": backtest,
                }

        if retry_used:
            return {"replies": [DEGRADE_REPLY], "strategy_json": None}

        if strategy is not None or _looks_like_strategy_attempt(text) or _looks_like_strategy_request(message):
            retry_used = True
            messages.append({"role": "user", "content": CORRECTION_PROMPT})
            continue

        return {"replies": llm_service.split_replies(text), "strategy_json": None}

    return {"replies": ["这个请求步骤有点多，请简化你的策略描述再试一次。"], "strategy_json": None}
