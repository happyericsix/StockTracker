# -*- coding: utf-8 -*-
"""每日观察员（daily observer）：门控说"今天没必要开会"之后，让 agent 亲眼看一眼盘面。

<h3>三层看盘结构（本模块补的是第三层）</h3>
- 全委会（trading_committee）：8 角色辩论，~21k tokens —— 只在门控触发时开；
- 盯盘交易员（intraday_watch）：盘中分钟级事件，1 次调用秒级；
- 观察员（本模块）：**每日收盘结算各跑一次**的轻量扫视，1 次调用 ~1-2k tokens。

修的是确定性门控的结构性盲区：门控的四条触发全是价量阈值，看不见
"缓慢形成的趋势 / 板块与个股的背离 / 新闻面的累积发酵 / 论点前提已经变化"。
观察员不决策、不下单，只回答一个问题：**今天值得把全委会叫起来吗？**

<h3>纪律（与 intraday_watch 同源）</h3>
- 输出契约封闭：{attention, propose_convene, thesis_status, note}。
  读不懂 → attention=low / propose_convene=False（fail-open：回落门控的结论，
  门控说不开就不开 —— 观察员只有"叫开会"的提案权，没有"维持开会"的否决权）；
- 模型不可用 / 调用异常 / 预算用完 → degraded=True，等同不召集；
- 每天的结论（含"安静"）都进 gate detail 落痕迹：安静的日子也要能回答
  "agent 今天看过了、说了什么"（2026-10-28 生产事故的教训：沉默必须可见）。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

#: 观察员单次输出上限：一个 JSON 对象四五个字段，500 足够（散文是解析失败的来源）。
OBSERVER_MAX_TOKENS = 500

#: 注意力档位（封闭集）。high 只是"更该开会"的信号强化，不构成任何执行语义。
ATTENTION_LEVELS = ("high", "med", "low")


def _f(value: Any) -> Optional[float]:
    try:
        out = float(value)
        return out if out == out else None   # NaN → None
    except (TypeError, ValueError):
        return None


def build_panel(evidence: dict, records: list[dict], position: dict | None) -> dict:
    """观察员看的盘面：刻意比全委会的证据包小一个量级（它只判断"要不要开会"）。

    - 近 10 根日线（日期/收盘/涨跌幅/量比）——趋势与节奏；
    - 指标快照（MA/RSI 等，build_evidence 已算好）——结构位置；
    - 持仓摘要 —— 有仓与空仓的"该不该多看一眼"标准不同。
    """
    bars = list(evidence.get("recent_bars") or [])
    closes = [_f(b.get("close")) for b in bars]
    volumes = [_f(b.get("volume")) for b in bars]
    recent = []
    for i, bar in enumerate(bars[-10:]):
        pct = None
        if i > 0 and closes[-10:][i - 1]:
            cur, prev = closes[-10:][i], closes[-10:][i - 1]
            if cur is not None and prev:
                pct = round((cur / prev - 1.0) * 100.0, 2)
        recent.append({"date": str(bar.get("date", ""))[:10],
                       "close": _f(bar.get("close")),
                       "pct_chg": pct})
    avg_vol = (sum(volumes[-20:-1]) / len(volumes[-20:-1])) if len(volumes) >= 21 else None
    if bars and avg_vol:
        recent[-1]["volume_ratio"] = round((volumes[-1] or 0.0) / avg_vol, 2)

    panel = {
        "recent_daily_bars": recent,
        "indicators": {k: v for k, v in dict(evidence.get("indicators") or {}).items()
                       if not isinstance(v, (list, dict))},
        "position": {"shares": _f((position or {}).get("shares")) or 0.0,
                     "avg_cost": _f((position or {}).get("entry_price"))},
    }
    return panel


def _observer_system_prompt() -> str:
    return (
        "你是策略观察员，正在替一条既定策略做每日收盘后的例行扫视。"
        "全委会（8 角色辩论）很贵，确定性门控（价量阈值）决定要不要开——"
        "你的职责是补上门控看不见的东西：缓慢形成的趋势、板块与个股的背离、"
        "新闻面的累积发酵、策略论点前提的变化。\n"
        "只输出一个 json 对象（不要任何其它文字）：\n"
        '{"attention": "low|med|high", "propose_convene": true|false, '
        '"thesis_status": "一句话（≤40字）：策略论点当前是否仍成立", '
        '"note": "一句话（≤60字）：今天盘面最值得注意的一件事"}\n'
        "propose_convene=true 的标准：出现了值得 8 个角色正式辩论的迹象——"
        "规则引擎没触发、但论点相关的证据在积累。只为这条策略负责，"
        "不要把大盘波动当成本策略的开会理由。\n"
        "克制是美德：绝大多数日子 propose_convene=false、attention=low。"
        "propose_convene 只是提案，开会与否最终由门控记录与全委会决定。"
    )


def _observer_user_prompt(symbol: str, as_of: str, panel: dict, config: dict | None,
                          news_briefing: dict | None, days_since_last: Any) -> str:
    payload = {
        "symbol": symbol,
        "as_of": str(as_of),
        "days_since_committee_met": days_since_last,
        "strategy_intent": {
            "name": (config or {}).get("name", ""),
            "entry": (config or {}).get("entry"),
            "exit": (config or {}).get("exit"),
        },
        "panel": panel,
    }
    # 信息面只给条数与最近标题（观察员判断"要不要开会"，不开会不需要全文证据）
    if isinstance(news_briefing, dict) and news_briefing:
        events = news_briefing.get("events")
        if isinstance(events, list) and events:
            payload["news_pulse"] = {
                "event_count": len(events),
                "recent_titles": [str(e.get("title") or "")[:40] for e in events[:5]],
            }
    return json.dumps(payload, ensure_ascii=False, default=str)


def parse_observer_verdict(text: str) -> dict:
    """解析观察员输出（确定性、保守优先）：读不懂 → low / 不召集，绝不猜。"""
    body = str(text or "")
    candidate = None
    start, end = body.find("{"), body.rfind("}")
    if 0 <= start < end:
        candidate = body[start:end + 1]
    payload = None
    if candidate:
        try:
            payload = json.loads(candidate)
        except ValueError:
            payload = None

    warnings: list[str] = []
    if not isinstance(payload, dict):
        warnings.append("观察员输出不是 JSON，按不召集处理")
        return {"attention": "low", "propose_convene": False,
                "thesis_status": "", "note": "输出无法解析", "warnings": warnings}

    attention = str(payload.get("attention") or "").strip().lower()
    if attention not in ATTENTION_LEVELS:
        warnings.append(f"未知注意力档位 {attention!r}，按 low 处理")
        attention = "low"
    # propose_convene 只有显式 true 才算提案；attention=high 但没提案也尊重模型
    propose = payload.get("propose_convene") is True
    if propose and attention == "low":
        warnings.append("propose_convene=true 但 attention=low，按模型显式提案保留")
    return {"attention": attention, "propose_convene": propose,
            "thesis_status": str(payload.get("thesis_status") or "")[:80],
            "note": str(payload.get("note") or "")[:120], "warnings": warnings}


def observe(symbol: str, as_of: str, records: list[dict], position: dict | None,
            evidence: dict, config: dict | None = None,
            completion: Optional[Callable] = None,
            news_briefing: dict | None = None,
            days_since_last: Any = None) -> dict:
    """观察员的一次例行扫视。**永不抛异常**：任何故障都退化成"不召集"。

    返回 {attention, propose_convene, thesis_status, note, warnings, degraded,
    observer_tokens}。degraded=True 表示根本没问成模型（不可用/异常/预算）——
    调用方应把这种日子记成"观察员缺席"而不是"观察员看过说安静"。
    """
    import llm_service

    def _quiet(note: str, warning: str) -> dict:
        return {"attention": "low", "propose_convene": False, "thesis_status": "",
                "note": note, "warnings": [warning], "degraded": True,
                "observer_tokens": 0}

    if os.getenv("AGENT_OBSERVER", "1").strip().lower() in ("0", "off", "false"):
        return _quiet("观察员未启用", "AGENT_OBSERVER=0")

    # 每日 token 预算硬顶与全委会同一条纪律（预算在门控前查过一次，
    # 这里补一次：观察员自己也不该越过成本止损线）
    budget = int(os.getenv("LLM_DAILY_TOKEN_BUDGET", "0") or "0")
    if budget > 0:
        try:
            from agent import metering
            if int(metering.daily_llm_tokens()) >= budget:
                return _quiet("今日 LLM 预算已用完", "observer_budget_exceeded")
        except Exception:  # noqa: BLE001 —— 计量挂了不挡观察
            pass

    checker = getattr(llm_service, "completion_available", None) \
        or getattr(llm_service, "_is_available", None)
    if callable(checker):
        try:
            if not checker(completion):
                return _quiet("模型服务不可用", "观察员前探测：LLM 不可用")
        except Exception:  # noqa: BLE001 —— 探测失败不阻止尝试
            pass

    call = completion or llm_service.chat_completion
    panel = build_panel(evidence, records, position)
    try:
        messages = [{"role": "system", "content": _observer_system_prompt()},
                    {"role": "user", "content": _observer_user_prompt(
                        symbol, as_of, panel, config, news_briefing, days_since_last)}]
        if completion is None:
            choice = call(messages, temperature=0.1, max_tokens=OBSERVER_MAX_TOKENS,
                          response_format={"type": "json_object"})
        else:
            choice = call(messages, temperature=0.1, max_tokens=OBSERVER_MAX_TOKENS)
    except Exception as exc:  # noqa: BLE001 —— 调用异常 = 没问成 → 退化
        logger.warning("观察员调用失败 symbol=%s: %s", symbol, exc)
        return _quiet("调用失败", f"观察员调用异常（{type(exc).__name__}）")

    content = ""
    try:
        content = str((((choice or {}).get("message") or {}).get("content")) or "")
    except Exception:  # noqa: BLE001
        content = ""
    if not content.strip():
        return _quiet("模型返回空内容", "观察员调用返回空")
    if "AI 服务暂不可用" in content:
        return _quiet("模型服务不可用", "观察员：模型服务占位响应")

    tokens = 0
    try:
        from agent import metering
        tokens = int(metering.parse_usage(choice if isinstance(choice, dict) else None)
                     .get("total_tokens", 0))
    except Exception:  # noqa: BLE001
        tokens = 0
    result = parse_observer_verdict(content)
    return {**result, "degraded": False, "observer_tokens": tokens}


def describe() -> dict:
    """给 /health 与调试用。"""
    return {"attention_levels": list(ATTENTION_LEVELS),
            "max_tokens": OBSERVER_MAX_TOKENS,
            "switch": "AGENT_OBSERVER"}
