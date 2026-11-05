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

#: 守门员单次输出 max_tokens（2026-11-05 从 1200 提到 2400）：
#: 多仓位 verdicts（每条 action/size/confidence/理由/thesis_check）+ escalate +
#: next_review + summary，1200 在 3~4 条 verdict 时已到水位线——被截断的后果是
#: 解析失败静默降级成 hold（省 500 token，付"该减仓那次没减"）。
#: 决策类调用的 token 成本比它管的钱低 3~4 个数量级（见花费梯度设计）。
GATE_MAX_TOKENS = 2400

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


# ==================== Tier 2：挑战者（高注时刻的第二双眼睛，2026-11-05） ====================
#
# 触发由 Java 侧按**风险规则**判定（仓位占比 ≥30% / 距硬线 ≤0.5pt / 账户回撤过半），
# 本模块只做两件事：挑战一次 + 保守合并。
#
# 合并语义是本设计的宪法：**挑战者只能让裁决更保守，不能更激进**——
# sustain（维持）或 downgrade（approve→downsize→veto，sell→partial_sell→hold）。
# 理由与方向不对称同源：第二双眼睛的价值是拦错误，不是加仓位的胆气；挑战者想
# 拥护更激进的动作，那不是它的职权（那是委员会开会的事）。

#: 保守序：越靠左越激进。挑战者只能把 verdict 向右移。
_CONSERVATISM_ORDER = {
    ACTION_APPROVE: 0,
    ACTION_DOWNSIZE: 1,
    ACTION_SELL: 0,        # sell/partial_sell 是降风险动作，与 approve 同档起点
    ACTION_PARTIAL_SELL: 1,
    ACTION_HOLD: 2,
    ACTION_VETO: 3,
    ACTION_FLAG: 2,
}
#: 降风险方向的降级链（卖出类：清仓 → 减仓 → 不动）
_RISK_DOWNGRADE = {ACTION_SELL: ACTION_PARTIAL_SELL, ACTION_PARTIAL_SELL: ACTION_HOLD}
#: 入场方向的降级链（买入类：执行 → 缩量 → 否决）
_ENTRY_DOWNGRADE = {ACTION_APPROVE: ACTION_DOWNSIZE, ACTION_DOWNSIZE: ACTION_VETO}

CHALLENGE_MAX_TOKENS = 1600


def challenge_system_prompt() -> str:
    return (
        "你是挑战者——账户守门员的第二次独立审视。第一次裁决已经给出（first_verdicts），"
        "你的任务是**专门反驳它**：找它的证据漏洞、仓位计算错误、被忽略的账户事实、"
        "与守门员自己近期裁决的矛盾、thesis_check 的敷衍。\n"
        "只输出一个 json 对象（不要任何其它文字）：\n"
        '{"overturns": [{"strategy_id": 1, "action": "approve|downsize|veto|hold|'
        'partial_sell", "size_fraction": 0.0-1.0, "reason": "反驳依据(≤60字)"}],\n'
        ' "sustains": [{"strategy_id": 2, "reason": "为什么第一次裁决站得住"}],\n'
        ' "verdict_quality": "sound|flawed|shallow", "summary": "一句话"}\n'
        "硬纪律：你**只能让裁决更保守**——把 approve 降为 downsize/veto、把 sell 降为 "
        "partial_sell/hold、或压低 size_fraction。你无权把 veto 升成 approve、无权调高"
        "仓位（那是委员会的职权，不是刹车片的）。证据不足推翻时选 sustain 并说清"
        "为什么第一次是对的。克制是美德：大部分挑战的结果应该是 sustain。"
    )


def parse_challenge(text: str) -> dict:
    """解析挑战输出（保守优先）：读不懂 = 全部 sustain（挑战失败不改变原裁决）。"""
    body = str(text or "")
    start, end = body.find("{"), body.rfind("}")
    payload = None
    if 0 <= start < end:
        try:
            payload = json.loads(body[start:end + 1])
        except ValueError:
            payload = None
    if not isinstance(payload, dict):
        return {"overturns": [], "sustains": [], "verdict_quality": "unknown",
                "summary": "输出无法解析", "warnings": ["挑战者输出不是 JSON"],
                "degraded": True}
    warnings: list[str] = []
    overturns = []
    for item in payload.get("overturns") or []:
        if not isinstance(item, dict):
            continue
        try:
            strategy_id = int(item.get("strategy_id"))
        except (TypeError, ValueError):
            continue
        action = str(item.get("action") or "").strip().lower()
        if action not in GATE_ACTIONS:
            warnings.append(f"挑战者未知动作 {action!r}，忽略")
            continue
        overturns.append({"strategy_id": strategy_id, "action": action,
                          "size_fraction": _f(item.get("size_fraction")) or 0.0,
                          "reason": str(item.get("reason") or "").strip()[:120]})
    sustains = [str(s.get("reason") or "").strip()[:120]
                for s in payload.get("sustains") or [] if isinstance(s, dict)]
    return {"overturns": overturns, "sustains": sustains,
            "verdict_quality": str(payload.get("verdict_quality") or "unknown")[:16],
            "summary": str(payload.get("summary") or "").strip()[:200],
            "warnings": warnings, "degraded": False}


def merge_conservative(verdicts: list[dict], challenge: dict) -> tuple[list[dict], list[str]]:
    """保守合并：挑战者的 overturn 只能沿降级链向下移或压低仓位。

    返回 (合并后的 verdicts, 合并说明)。原裁决结构不变（新列表），
    被降级的 verdict 带 challenge_note（理由进痕迹）。
    """
    notes: list[str] = []
    by_id = {v["strategy_id"]: dict(v) for v in verdicts}
    for overturn in challenge.get("overturns") or []:
        target = by_id.get(overturn["strategy_id"])
        if target is None:
            notes.append(f"挑战者指向不存在的 strategy {overturn['strategy_id']}，忽略")
            continue
        original = target["action"]
        requested = overturn["action"]
        # ① 只允许同方向或跨到更保守的"降风险"动作（veto/hold 任何方向都合法）
        if requested in (ACTION_VETO, ACTION_HOLD):
            downgrade_chain = (_ENTRY_DOWNGRADE if original in (ACTION_APPROVE, ACTION_DOWNSIZE)
                               else _RISK_DOWNGRADE)
        elif original in (ACTION_APPROVE, ACTION_DOWNSIZE):
            downgrade_chain = _ENTRY_DOWNGRADE
        else:
            downgrade_chain = _RISK_DOWNGRADE
        # ② 合法性：requested 必须在原动作的降级链可达集合里，或与原动作相同
        reachable = set(downgrade_chain.values()) | {original}
        if requested not in reachable:
            notes.append(f"挑战者想把 strategy {overturn['strategy_id']} 从 {original} "
                         f"改成 {requested}（更激进/越权），驳回——挑战者只能更保守")
            continue
        if requested != original:
            target["action"] = requested
            target["challenge_note"] = overturn["reason"]
            target["confidence"] = None   # 被挑战降级后置信度不再可信
            # 动作降级时带上挑战者给的仓位（它是新动作的目标仓位）；
            # 没给或越界则保守取原值的一半——降了档还维持原仓位是自相矛盾
            new_size = overturn.get("size_fraction") or 0.0
            if 0 < new_size <= 1:
                target["size_fraction"] = round(new_size, 4)
            else:
                target["size_fraction"] = round((target.get("size_fraction") or 0.0) / 2, 4)
            notes.append(f"strategy {overturn['strategy_id']}: {original} → {requested}"
                         f"（{overturn['reason']}）")
        else:
            # ③ 动作相同但压低仓位：只取更低值
            new_size = overturn.get("size_fraction") or 0.0
            if 0 < new_size < (target.get("size_fraction") or 0.0):
                target["size_fraction"] = round(new_size, 4)
                target["challenge_note"] = overturn["reason"]
                notes.append(f"strategy {overturn['strategy_id']}: 仓位压低至 {new_size}"
                             f"（{overturn['reason']}）")
    return list(by_id.values()), notes


def challenge_decide(payload: dict, first_verdicts: list[dict],
                     completion: Optional[Callable] = None) -> dict:
    """永不抛异常：挑战失败 = 维持原裁决（degraded=True 只影响度量，不影响决策安全）。"""
    import llm_service

    if not first_verdicts:
        return {**_gate_result(first_verdicts, None, None, "无裁决可挑战",
                               ["挑战者收到空裁决"], degraded=True), "challenge_tokens": 0}
    challenge_payload = dict(payload if isinstance(payload, dict) else {})
    challenge_payload["first_verdicts"] = first_verdicts

    checker = getattr(llm_service, "completion_available", None) \
        or getattr(llm_service, "_is_available", None)
    if callable(checker):
        try:
            if not checker(completion):
                return {"overturns": [], "sustains": [], "merged": first_verdicts,
                        "notes": [], "summary": "模型服务不可用，维持原裁决",
                        "warnings": ["挑战者前探测：LLM 不可用"], "degraded": True,
                        "challenge_tokens": 0}
        except Exception:  # noqa: BLE001
            pass

    call = completion or llm_service.chat_completion
    messages = [{"role": "system", "content": challenge_system_prompt()},
                {"role": "user", "content": json.dumps(challenge_payload,
                                                       ensure_ascii=False, default=str)}]
    try:
        if completion is None:
            choice = call(messages, temperature=0.1, max_tokens=CHALLENGE_MAX_TOKENS,
                          response_format={"type": "json_object"})
        else:
            choice = call(messages, temperature=0.1, max_tokens=CHALLENGE_MAX_TOKENS)
    except Exception as exc:  # noqa: BLE001 —— 挑战失败 = 维持原裁决
        logger.warning("挑战者调用失败：%s", exc)
        return {"overturns": [], "sustains": [], "merged": first_verdicts, "notes": [],
                "summary": "挑战调用失败，维持原裁决",
                "warnings": [f"挑战者调用异常（{type(exc).__name__}）"],
                "degraded": True, "challenge_tokens": 0}

    content = ""
    try:
        content = str((((choice or {}).get("message") or {}).get("content")) or "")
    except Exception:  # noqa: BLE001
        content = ""
    if not content.strip():
        return {"overturns": [], "sustains": [], "merged": first_verdicts, "notes": [],
                "summary": "挑战者返回空，维持原裁决", "warnings": ["挑战者返回空"],
                "degraded": True, "challenge_tokens": 0}

    tokens = 0
    try:
        from agent import metering
        tokens = int(metering.parse_usage(choice if isinstance(choice, dict) else None)
                     .get("total_tokens", 0))
    except Exception:  # noqa: BLE001
        tokens = 0
    parsed = parse_challenge(content)
    merged, notes = merge_conservative(first_verdicts, parsed)
    return {**parsed, "merged": merged, "notes": notes, "degraded": parsed["degraded"],
            "challenge_tokens": tokens}
