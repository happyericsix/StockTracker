# -*- coding: utf-8 -*-
"""把模型的多角色辩论结果**确定性地**落成一个可审计的决策。

<h3>为什么解析层必须由代码做，而且是"保守优先"</h3>
TradingAgents 的做法值得学：辩论结束后由 `SignalProcessor` 产出 typed record，
规范行优先、HOLD 仓位归零、解析失败退默认值、归一化时带 `warning_message`。
我们照这个形状做，**但把解析失败的默认动作改成 SKIP**：

它那边解析失败默认是"BUY/SELL，仓位 0.25"—— 意思是**模型输出坏了就替它下一个单**。
我们反过来：**读不懂就不动**，并留下 `unknown_*` 的原因。理由很简单 ——
"模型没说清楚"绝不能变成一个真实的仓位；这也是我们"宁可跳过，也不写一个错的净值"的同一纪律。

<h3>与既有契约的关系</h3>
- 决策落进 `execution_contract` 的封闭集（`buy` / `sell` / `skip`），未知一律 `unknown_*` 并告警；
- 仓位是 `size_fraction ∈ [0,1]`，**上限由调用方（结算侧）裁决**，不在这里放大；
- 解析结果自带 `warnings`（哪一步被归一化了），随痕迹一起落库 —— 归一化必须留痕。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from agent import execution_contract as ec

logger = logging.getLogger(__name__)

# 规范行：与 TradingAgents 的 `FINAL TRANSACTION PROPOSAL: **BUY**` 同形状。
# 为什么认这行：模型在长辩论之后，最后一句才是它的结论；
# 让它把结论写成一个**固定格式的行**，比去猜哪段散文是结论可靠得多。
# 取**最后一个**匹配：辩论记录里 judge 可能先复述别人的规范行再给自己的结论，
# 取第一个会把别人的结论当成它的（解析器只该认最后一句话）。
_CANONICAL_RE = re.compile(
    r"FINAL\s+TRANSACTION\s+PROPOSAL\s*[:：]\s*\**\s*(BUY|SELL|HOLD)\b",
    re.IGNORECASE,
)


def _last_canonical(text: str) -> Optional[re.Match]:
    """全文里**最后一条**规范行（没有则 None）。"""
    last = None
    for match in _CANONICAL_RE.finditer(text or ""):
        last = match
    return last

# JSON 块（```json ... ``` 或裸 {...}）：结构化字段（仓位/置信度/止损）走它
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

MAX_RATIONALE_CHARS = 2000
MAX_WARNING_CHARS = 500


@dataclass(frozen=True)
class TradeDecision:
    """一条可落库的 agent 决策。

    `decision` 是**结算侧唯一认的字段**（buy/sell/skip）；`signal` 是模型的原话方向
    （buy/sell/hold）—— 两者分开，是因为"模型说要买"与"最终决定买"不是一回事：
    仓位为 0、或解析失败、或风控否决，都会让 signal=buy 而 decision=skip。

    `authorization` 是**给下一交易日的盘中授权**（authorization 模式）：
    委员会划定的边界，规则引擎（DSL）在边界内自动执行。它与会话级决策独立 ——
    今天 HOLD 不代表明天盘中不许按规则动。None = 模型没给，调用方按"未授权"处理（保守）。
    """

    decision: str                      # buy | sell | skip（封闭集）
    signal: Optional[str]              # buy | sell | hold（模型给出的方向，可为空）
    size_fraction: float               # 0.0 ~ 1.0
    confidence: Optional[float]
    rationale: str
    warnings: tuple[str, ...] = ()
    skip_reason: Optional[str] = None  # 仅 decision=skip 时有值（封闭集）
    raw_text: str = ""                 # 原始输出（调用方通常只存哈希/长度）
    authorization: Optional[dict] = None

    def as_dict(self) -> dict:
        return {
            "decision": self.decision,
            "signal": self.signal,
            "size_fraction": self.size_fraction,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "warnings": list(self.warnings),
            "skip_reason": self.skip_reason,
            "authorization": self.authorization,
        }


def _skip(reason: str, warnings: list[str], *, raw_text: str = "",
          rationale: str = "", signal: Optional[str] = None,
          authorization: Optional[dict] = None) -> TradeDecision:
    return TradeDecision(
        decision=ec.DECISION_SKIP,
        signal=signal,
        size_fraction=0.0,
        confidence=None,
        rationale=rationale[:MAX_RATIONALE_CHARS],
        warnings=tuple(warnings),
        skip_reason=ec.normalize_skip_reason(reason),
        raw_text=raw_text,
        authorization=authorization,
    )


def _extract_json(text: str) -> tuple[Optional[dict], list[str]]:
    warnings: list[str] = []
    candidate = None
    match = _JSON_BLOCK_RE.search(text or "")
    if match:
        candidate = match.group(1)
    else:
        # 没有围栏时退而求其次：取最外层的一对花括号。
        # 刻意**不**做"猜哪个括号配对"的复杂解析：猜错的风险高于收益。
        start, end = (text or "").find("{"), (text or "").rfind("}")
        if 0 <= start < end:
            candidate = text[start:end + 1]
    if not candidate:
        if "{" in (text or ""):
            # 有左括号没有配对的右括号：**最常见的原因是输出被截断**（撞上 max_tokens）。
            # 静默当成"没有结构化字段"会把一个真实的故障藏起来 —— 而它恰恰需要被看见。
            warnings.append("输出里有 { 但没有配对的 }（疑似被截断，可能撞到 max_tokens 上限）")
        return None, warnings
    try:
        parsed = json.loads(candidate)
    except (ValueError, TypeError) as exc:
        warnings.append(f"JSON 块无法解析（{type(exc).__name__}），结构化字段全部按缺失处理")
        return None, warnings
    if not isinstance(parsed, dict):
        warnings.append("JSON 块不是对象，结构化字段全部按缺失处理")
        return None, warnings
    return parsed, warnings


def _clean_size(raw: Any, warnings: list[str]) -> float:
    """仓位：只接受数字；非数字或越界一律**夹到 [0,1]** 并留警告。"""
    if raw is None:
        return 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        warnings.append(f"size_fraction 不是数字（{raw!r}），按 0 处理")
        return 0.0
    if value != value:  # NaN
        warnings.append("size_fraction 是 NaN，按 0 处理")
        return 0.0
    if value < 0.0 or value > 1.0:
        clamped = min(1.0, max(0.0, value))
        warnings.append(f"size_fraction {value} 越界，已夹到 {clamped}")
        return clamped
    return round(value, 4)


def _clean_confidence(raw: Any, warnings: list[str]) -> Optional[float]:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        warnings.append(f"confidence 不是数字（{raw!r}），按缺失处理")
        return None
    if value != value or value < 0.0 or value > 1.0:
        warnings.append(f"confidence {value} 不在 [0,1]，按缺失处理")
        return None
    return round(value, 4)


def _clean_text(raw: Any, limit: int) -> str:
    if raw is None:
        return ""
    return str(raw).strip()[:limit]


MAX_AUTH_NOTES_CHARS = 500


def _clean_authorization(raw: Any, warnings: list[str]) -> Optional[dict]:
    """把 JSON 块里的 authorization 归一成可落库的授权。

    <h3>保守优先的语义</h3>
    - 整块缺失 / 不是对象 → None（调用方按"未授权"处理，盘中不执行）；
    - `intraday_execution` 缺省 false：授权必须**显式给出**，绝不默认打开；
    - `allow_open` 缺省跟随 intray_execution；`allow_close` 缺省 true——
      风控语义上"允许退出"应比"允许进场"宽松（不许出场的授权等于把人锁在车里）；
    - `max_position_pct` ∈ [0,1]，缺省 1.0（不额外限制；结算侧还有策略 size_pct
      与 agent size_fraction 两层夹取，这里只是委员会的显式边界）。
    """
    if not isinstance(raw, dict):
        if raw is not None:
            warnings.append("authorization 不是对象，按未授权处理")
        return None

    def flag(key: str, default: bool) -> bool:
        value = raw.get(key, default)
        return bool(value) if isinstance(value, bool) else default

    intraday = flag("intraday_execution", False)
    allow_open = flag("allow_open", intraday)
    allow_close = flag("allow_close", True)

    max_pct = 1.0
    raw_pct = raw.get("max_position_pct")
    if raw_pct is not None:
        try:
            value = float(raw_pct)
        except (TypeError, ValueError):
            warnings.append(f"max_position_pct 不是数字（{raw_pct!r}），按 1.0 处理")
        else:
            if value != value or value < 0.0 or value > 1.0:
                clamped = min(1.0, max(0.0, value))
                warnings.append(f"max_position_pct {value} 越界，已夹到 {clamped}")
                max_pct = clamped
            else:
                max_pct = round(value, 4)

    return {
        "intraday_execution": intraday,
        "allow_open": allow_open,
        "allow_close": allow_close,
        "max_position_pct": max_pct,
        "notes": _clean_text(raw.get("notes"), MAX_AUTH_NOTES_CHARS),
    }


def parse_trade_decision(text: str) -> TradeDecision:
    """把模型的输出解析成决策（**确定性、可测、保守优先**）。

    优先级（与 TradingAgents 一致）：**规范行 > JSON 块的方向**。
    规范行是模型的最终结论；JSON 只补结构化字段（仓位/置信度）。
    两者冲突时以规范行为准，并留警告 —— 冲突这件事本身要被看见。
    """
    body = str(text or "")
    canonical = _last_canonical(body)
    payload, warnings = _extract_json(body)

    json_signal = None
    if payload is not None:
        raw_signal = str(payload.get("signal") or "").strip().lower()
        if raw_signal in ("buy", "sell", "hold"):
            json_signal = raw_signal
        elif raw_signal:
            warnings.append(f"JSON 里的 signal 不认识（{raw_signal!r}），已忽略")

    signal = canonical.group(1).lower() if canonical else json_signal
    if canonical and json_signal and canonical.group(1).lower() != json_signal:
        # 冲突必须留痕：它意味着模型自己前后不一致（辩论被最后一次发言带跑了）
        warnings.append(
            f"规范行（{canonical.group(1).upper()}）与 JSON 的 signal（{json_signal.upper()}）不一致，"
            "以规范行为准")
    # 盘中授权与会话级决策独立提取：解析不出方向时授权也不该被采纳
    # （一个连结论都写不清楚的输出，不该拥有写授权的权力）。
    authorization = _clean_authorization((payload or {}).get("authorization"), warnings)
    if signal is None:
        return _skip(ec.SKIP_AGENT_UNPARSABLE,
                     warnings + ["输出里既没有规范行也没有可用的 signal"],
                     raw_text=body)

    size = _clean_size((payload or {}).get("size_fraction"), warnings)
    confidence = _clean_confidence((payload or {}).get("confidence"), warnings)
    rationale = _clean_text((payload or {}).get("rationale"), MAX_RATIONALE_CHARS)

    if signal == "hold":
        # HOLD 的仓位强制定成 0：模型偶尔会写 "HOLD, size=0.3"，那是有歧义的表达，
        # 而"持有"在结算侧只可能是"不加仓"。
        if size > 0:
            warnings.append(f"HOLD 但给了 size_fraction={size}，已强制为 0（持有＝不加仓）")
        return TradeDecision(
            decision=ec.DECISION_SKIP,
            signal="hold",
            size_fraction=0.0,
            confidence=confidence,
            rationale=rationale,
            warnings=tuple(warnings),
            skip_reason=ec.SKIP_RULE_NOT_MET,
            raw_text=body,
            authorization=authorization,
        )

    if size <= 0.0:
        # 方向有了、仓位是 0：等价于"看多但不下注"。记成 skip 而不是 0 股成交 ——
        # 前者在痕迹里能被统计，后者会让"成交 0 股"这种东西混进成交表。
        warnings.append("方向为 %s 但 size_fraction=0，按 skip 处理" % signal.upper())
        return TradeDecision(
            decision=ec.DECISION_SKIP,
            signal=signal,
            size_fraction=0.0,
            confidence=confidence,
            rationale=rationale,
            warnings=tuple(warnings),
            skip_reason=ec.SKIP_RULE_NOT_MET,
            raw_text=body,
            authorization=authorization,
        )

    return TradeDecision(
        decision=ec.DECISION_BUY if signal == "buy" else ec.DECISION_SELL,
        signal=signal,
        size_fraction=size,
        confidence=confidence,
        rationale=rationale,
        warnings=tuple(warnings),
        skip_reason=None,
        raw_text=body,
        authorization=authorization,
    )


def decision_prompt_contract() -> str:
    """给模型看的输出契约（**与解析器同源**：改解析器就得改这里，所以放在一起）。

    `prompt_versions` 会把这个契约的哈希写进痕迹：报告要能回答
    "这条决策是在哪一版输出契约下产生的"。

    authorization（盘中授权）：委员会的产出不只是"今天动不动"，还有
    "下一次开会之前，规则引擎可以在什么边界内自动执行"。这是 agent 模式
    盘中执行的唯一来源——没有它，盘中就回到"只在日线结算时决策"。
    """
    return (
        "输出必须以一行规范结论结束，格式**严格**如下：\n"
        "FINAL TRANSACTION PROPOSAL: **BUY**  （或 **SELL** / **HOLD**）\n"
        "并在之前给出一个 JSON 块，字段：\n"
        '  {"signal": "BUY|SELL|HOLD", "size_fraction": 0.0-1.0, '
        '"confidence": 0.0-1.0, "rationale": "一句话理由", "warnings": ["..."],\n'
        '   "authorization": {"intraday_execution": true|false, '
        '"allow_open": true|false, "allow_close": true|false, '
        '"max_position_pct": 0.0-1.0, "notes": "一句话理由"}}\n'
        "约束：HOLD 的 size_fraction 必须为 0；读不懂或不一致时**宁可 HOLD**。\n"
        "authorization 是**下一次开会之前**的盘中授权：规则引擎（策略 DSL）将在边界内"
        "自动执行买卖信号；intraday_execution=false 表示盘中不自动执行（只等日线开会）。"
        "给边界时依据今天的证据：趋势与波动支持规则执行就放行并给出仓位上限，"
        "证据含糊或风险高就收紧或关闭；allow_close 通常应保持 true（允许退出风控）。"
    )


def prompt_version() -> str:
    """输出契约的版本（哈希），随痕迹落库。"""
    import hashlib

    payload = decision_prompt_contract() + "|" + "|".join(ec.DECISIONS)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
