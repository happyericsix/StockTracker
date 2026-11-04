# -*- coding: utf-8 -*-
"""账户守门员（ACCOUNT-GATE 阶段1/2，2026-11-04）：agent 模式盘中唯一的裁决者。

<h3>它取代谁</h3>
取代 per-strategy 盯盘交易员（intraday_watch）成为 agent 模式盘中的决策出口。
分工：**一个 agent、多种唤醒源、同一份账户面板**——
- 信号事件：DSL 规则信号已降级为事件（不再是执行器），进来先过守门员；
- 持仓风险阈值：near_stop / drawdown_warning / concentration / auth_expired
  （电平触发，由 Java 侧 5 分钟扫描判定，越界即唤醒）。

<h3>与委员会的分工（三层架构的快层）</h3>
- 硬层（止损）不经它：纯代码每根完结 bar 检查，永远更快；
- 慢层（15:30 委员会）管规划与授权边界；守门员只在边界内裁决，且可 escalate。

<h3>裁决纪律（方向不对称，docs/ACCOUNT-GATE.md §B3.1）</h3>
买入之亏与止损之亏是两回事：买入问"期望值"（举证在信号，证据不足 veto）；
卖出问"持有的理由还在吗"（thesis check，沉没成本不构成理由）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

#: 守门员单次输出 max_tokens：verdicts（每条 action/size/confidence/理由/thesis_check）
#: + escalate + next_review + summary，1200 给推理型模型留了头部余量。
GATE_MAX_TOKENS = 1200

# 动作封闭集（结算侧按它执行；未知动作一律按 hold 处理）。
# 信号裁决类：approve=按信号执行 / veto=否决 / downsize=缩量执行 / flag=只标记给委员会
# 主动风控类：sell=清仓 / partial_sell=按比例减仓（无信号时由风险阈值唤醒产生）
ACTION_HOLD = "hold"
ACTION_APPROVE = "approve"
ACTION_VETO = "veto"
ACTION_DOWNSIZE = "downsize"
ACTION_FLAG = "flag"
ACTION_SELL = "sell"
ACTION_PARTIAL_SELL = "partial_sell"
GATE_ACTIONS = (ACTION_HOLD, ACTION_APPROVE, ACTION_VETO, ACTION_DOWNSIZE,
                ACTION_FLAG, ACTION_SELL, ACTION_PARTIAL_SELL)


def _f(value: Any) -> Optional[float]:
    try:
        out = float(value)
        return out if out == out else None   # NaN → None
    except (TypeError, ValueError):
        return None


# ==================== 系统提示词（裁决纪律的全部成文法） ====================

def gate_system_prompt() -> str:
    return (
        "你是账户守门员，盘中替用户看守**整个账户**的职业风控交易员。策略引擎的信号、"
        "持仓的风险状态都会叫醒你；你的任务是回答：这个动作，放在整个账户的语境里，"
        "该不该做、做多大。\n"
        "只输出一个 json 对象（不要任何其它文字）：\n"
        '{"verdicts": [{"strategy_id": 1, "action": '
        '"hold|approve|veto|downsize|flag|sell|partial_sell", '
        '"size_fraction": 0.0-1.0, "confidence": 0.0-1.0, "reason": "一句话(≤60字)", '
        '"thesis_check": "仅卖出类必填：当初买入的理由现在还成立吗，一句"}],\n'
        ' "escalate": {"signature": "事件签名(英文短语)", "reason": "为什么值得委员会复盘"} 或 null,\n'
        ' "next_review": [{"type": "price_below|price_above|drawdown|time", "value": "6.90"}] 或 null,\n'
        ' "summary": "一句话总评"}\n'
        "判断顺序（职业操盘手三层框架，顺序不可颠倒）：\n"
        "1. **先仓库**：account.positions 是全部持仓——集中度、每仓位浮盈亏与距止损线的"
        "距离、当日已成交、授权边界。任何一个动作都要先回答'账户承受得起吗'；\n"
        "2. **再市面**：market_context 的大盘方向与涨跌家数。逆市异动与顺市异动不是一回事；\n"
        "3. **最后才是信号/事件本身**：wake 与 signals 只是'值得看一眼'的入场券，"
        "不构成动手理由。\n"
        "方向不对称（宪法级纪律）：\n"
        "- **买入类**（approve）：举证责任在信号。证据不足、与委员会最近结论冲突、"
        "仓库状态不支持（满仓/高集中/当日已动过手）→ 一律 veto 或 flag。错过机会的代价是零，"
        "接一个负期望的代价是真金白银；\n"
        "- **卖出类**（sell/partial_sell/对卖出信号的裁决）：先做 thesis_check——"
        "当初买入的理由还成立吗？已破位/失效 → 不讨论盈亏，只做执行优化（现在清 vs 分批）；"
        "仍强劲 + 恐慌误杀 + 无流动性压力 → 才允许保留，且必须给 next_review 硬条件。"
        "**严禁**以'已经亏了 X%、卖了就实亏'作为理由——沉没成本不是决策依据"
        "（这是散户扛单的头号模式）；\n"
        "- 止损硬通道不经你（代码已经比你快），你的 near_stop 裁决是'提前商量'，"
        "最多优化出场节奏，永远不能取消止损。\n"
        "硬约束：T+1 市场，当日买入的份额卖不出（positions 里的 t1_locked_shares）；"
        "authorization 边界之外的动作会被代码拦下；allow_open=false 的策略你的买入类"
        "裁决不会被执行。\n"
        "escalate 的用法：同一模式（signature，如 buy_signal_downtrend_full_position）"
        "反复出现、或一次证据强烈的判断失误 → 提出升级，收盘后委员会会带着历史记录复盘。\n"
        "克制是美德：大部分唤醒的正确答案是 hold 或 veto。证据不足时必须克制，"
        "但该砍仓的时候不要犹豫——你管的是账户的生存。"
    )


# ==================== 解析（确定性、保守优先） ====================

def parse_gate_decision(text: str) -> dict:
    """解析守门员输出：读不懂 → 空 verdicts + 警告，绝不猜。

    与 parse_watch_decision 同一条纪律：解析失败不是'不动'的借口，
    而是返回 degraded 语义（warnings 带原因），由调用方决定留痕方式。
    """
    body = str(text or "")
    start, end = body.find("{"), body.rfind("}")
    payload = None
    if 0 <= start < end:
        try:
            payload = json.loads(body[start:end + 1])
        except ValueError:
            payload = None
    warnings: list[str] = []
    if not isinstance(payload, dict):
        return _gate_result([], None, None, "输出无法解析", ["守门员输出不是 JSON"], degraded=True)

    verdicts = []
    raw_verdicts = payload.get("verdicts")
    if isinstance(raw_verdicts, list):
        for item in raw_verdicts:
            if not isinstance(item, dict):
                continue
            strategy_id = item.get("strategy_id")
            try:
                strategy_id = int(strategy_id)
            except (TypeError, ValueError):
                warnings.append(f"verdict 缺少有效 strategy_id（{item.get('strategy_id')!r}），丢弃")
                continue
            action = str(item.get("action") or "").strip().lower()
            if action not in GATE_ACTIONS:
                warnings.append(f"未知动作 {action!r}，按 hold 处理")
                action = ACTION_HOLD
            size = _f(item.get("size_fraction"))
            if size is None or size < 0 or size > 1:
                if size is not None:
                    warnings.append(f"size_fraction={size!r} 越界，按 0 处理")
                size = 0.0
            confidence = _f(item.get("confidence"))
            if confidence is not None and not (0 <= confidence <= 1):
                confidence = None
            verdicts.append({
                "strategy_id": strategy_id,
                "action": action,
                "size_fraction": round(size, 4),
                "confidence": confidence,
                "reason": str(item.get("reason") or "").strip()[:120],
                "thesis_check": str(item.get("thesis_check") or "").strip()[:160] or None,
            })
    else:
        warnings.append("verdicts 缺失或不是数组，按全部 hold 处理")

    escalate = None
    if isinstance(payload.get("escalate"), dict):
        sig = str(payload["escalate"].get("signature") or "").strip()[:120]
        if sig:
            escalate = {"signature": sig,
                        "reason": str(payload["escalate"].get("reason") or "").strip()[:200]}

    next_review = None
    if isinstance(payload.get("next_review"), list):
        cleaned = []
        for item in payload["next_review"]:
            if isinstance(item, dict) and item.get("type") and item.get("value") is not None:
                cleaned.append({"type": str(item["type"])[:32],
                                "value": str(item["value"])[:32]})
        if cleaned:
            next_review = cleaned[:5]

    degraded = bool(payload.get("degraded"))
    return _gate_result(verdicts, escalate, next_review,
                        str(payload.get("summary") or "").strip()[:200],
                        warnings, degraded=degraded)


def _gate_result(verdicts, escalate, next_review, summary, warnings, *,
                 degraded=False) -> dict:
    return {"verdicts": verdicts, "escalate": escalate, "next_review": next_review,
            "summary": summary, "warnings": warnings, "degraded": degraded}


# ==================== 一次守门员裁决（1 次 LLM 调用） ====================

def gate_decide(payload: dict, completion: Optional[Callable] = None) -> dict:
    """永不抛异常：任何故障都退化成可执行的结论（degraded=True 交结算侧持仓留痕）。"""
    import llm_service

    if not isinstance(payload, dict) or not payload:
        return {**_gate_result([], None, None, "面板为空", ["账户面板缺失"], degraded=True),
                "gate_tokens": 0}

    checker = getattr(llm_service, "completion_available", None) \
        or getattr(llm_service, "_is_available", None)
    if callable(checker):
        try:
            if not checker(completion):
                return {**_gate_result([], None, None, "模型服务不可用",
                                        ["守门员前探测：LLM 不可用"], degraded=True),
                        "gate_tokens": 0}
        except Exception:  # noqa: BLE001 —— 探测失败不应阻止尝试
            pass

    call = completion or llm_service.chat_completion
    messages = [{"role": "system", "content": gate_system_prompt()},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}]
    try:
        if completion is None:
            choice = call(messages, temperature=0.1, max_tokens=GATE_MAX_TOKENS,
                          response_format={"type": "json_object"})
        else:
            choice = call(messages, temperature=0.1, max_tokens=GATE_MAX_TOKENS)
    except Exception as exc:  # noqa: BLE001 —— 调用异常 = 没问成 → 降级
        logger.warning("守门员调用失败： %s", exc)
        return {**_gate_result([], None, None, "调用失败",
                                [f"守门员调用异常（{type(exc).__name__}）"], degraded=True),
                "gate_tokens": 0}

    content = ""
    try:
        content = str((((choice or {}).get("message") or {}).get("content")) or "")
    except Exception:  # noqa: BLE001
        content = ""
    if not content.strip():
        return {**_gate_result([], None, None, "模型返回空内容", ["守门员调用返回空"],
                               degraded=True), "gate_tokens": 0}
    if "AI 服务暂不可用" in content:
        return {**_gate_result([], None, None, "模型服务不可用",
                               ["守门员：模型服务占位响应"], degraded=True),
                "gate_tokens": 0}

    tokens = 0
    try:
        from agent import metering
        tokens = int(metering.parse_usage(choice if isinstance(choice, dict) else None)
                     .get("total_tokens", 0))
    except Exception:  # noqa: BLE001
        tokens = 0
    result = parse_gate_decision(content)
    return {**result, "gate_tokens": tokens}


def describe() -> dict:
    """给 /health 与调试用。"""
    return {"actions": list(GATE_ACTIONS), "max_tokens_per_call": GATE_MAX_TOKENS,
            "llm_calls_per_decision": 1}
