# -*- coding: utf-8 -*-
"""多角色决策委员会：把"这条标的现在该不该动"交给一组角色辩论，再由确定性代码收口。

<h3>为什么是这几个角色（照 TradingAgents 的形状）</h3>
```
证据包（as-of 截止）→ Bull ⚔ Bear → Research Manager → Trader
                     → 风控三方（激进 → 保守 → 中性）→ Risk Judge
```
它的价值不在"三个臭皮匠"，而在**结构上强制出现反对意见**：单轮问答里模型会顺着提问者的
语气走（"帮我看看能不能买" → 大概率给你一个能买的理由）；而 Bear 这一棒必须找出反证，
风控三方必须分别从激进/保守/中性立场挑毛病。**反对意见是流程产物，不是模型的心情。**

<h3>四条纪律（缺一条这套东西就不能用在真钱旁边）</h3>
1. **as-of 守卫**：证据只到决策日为止。晚于 as-of 的 bar **一律剔除并留警告** ——
   偷看未来的 agent 会给出漂亮且完全虚假的结论，而且没有任何现有测试能发现。
2. **硬预算**：调用次数与 token 都有上限，超了就停，并记成 `agent_budget_exceeded`。
   这与"模型说 HOLD"是两件事，必须能分开统计（否则预算设小了永远发现不了）。
3. **决策由代码收口**：模型的散文**不是**决策。Risk Judge 的输出经
   `trading_decision.parse_trade_decision` 落成封闭枚举；读不懂就不动。
4. **全部留痕**：每个角色的输出摘要 + 哈希 + token 用量，随快照一起落库。
   摘要给人读，哈希证明记录没被改过。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from agent import execution_contract as ec
from agent import strategy_engine
from agent.trading_decision import (
    TradeDecision,
    decision_prompt_contract,
    parse_trade_decision,
    prompt_version,
)

logger = logging.getLogger(__name__)

# ==================== 硬预算（声明式常量，不做运行时可变的配置） ====================

#: 一轮委员会最多几次 LLM 调用。恰好等于角色数：**不重试**。
#: 重试会让"上限"变成软约束，也会把一次决策的成本悄悄翻倍。
MAX_LLM_CALLS = 8
#: 一轮委员会最多多少 token（prompt + completion 合计）。
#: <h3>这个数是**实测出来的**，不是拍的</h3>
#: 第一次真跑（8 个角色、600519、真 DeepSeek）实测 **33.4k** tokens ——
#: 而当时的上限写的是 30k，于是委员会在**最后一个角色之前**停住，
#: 每一条决策都以 `agent_budget_exceeded` 告终（"永远不动"）。
#: 这正是把上限设得太小时的典型症状：它不会报错，只会让 agent 永不决策。
#: 现在按实测值的约 1.8 倍留余量；上限的作用是拦住**失控**（重试、超长输出），不是日常限流。
#: <h3>上限的语义</h3>
#: 检查发生在**每次调用之前**，所以实际总量可能超出上限、超出量不超过一次调用。
#: 这是刻意的：token 数在调用返回之前不可知，事后才发现超限就只能回滚 ——
#: 而"回滚一次已经发生的调用"没有任何意义。痕迹里同时记**上限与实测总量**。
MAX_TOTAL_TOKENS = 60_000
#: 证据包放进 prompt 的最近 bar 数。
#: <h3>为什么是 30 而不是 60</h3>
#: 指标（含 MA60）是用**截至决策日的全部历史**算的，和这里给模型看多少根无关。
#: 而每个角色都要重发一遍证据包：60 根 bar ≈ 2.5k tokens，8 个角色就是 ~20k ——
#: 实测一次决策 33.4k tokens 里有大半是这几行数字重复了八遍。
#: 给模型看最近 30 根足够它读形态；成本省一半，信息没少。
EVIDENCE_BARS = 30

# ==================== 召集门控（事件驱动） ====================
# <h3>为什么要有门控</h3>
# 日频召集 = 每天花 ~21k tokens 让 8 个角色重新看一遍**几乎相同**的证据（只差一根 bar），
# 而绝大多数日子结论都是 HOLD。这买的不是信息，是重复劳动。
# 信息是**事件驱动**的：真正值得开会的时刻少而明确。
#
# <h3>四条触发理由（封闭集，全部确定性、不看模型）</h3>
# - `rule_signal`：策略 DSL 在当天给出买/卖（**复用规则引擎本身**，不另写判断）
# - `volatility_spike`：当日波动 ≥ 2×近 20 日平均波动，或量能 ≥ 2×20 日均量
# - `stale`：太久没开会（有仓位时更短 —— 持仓时更需要盯着）
# - `first_decision`：从来没有开过会（不能因为"没触发"就一直不开）
#
# <h3>两个刻意的取舍</h3>
# ① 没有触发时**不猜**：返回 `skip` 且 `skip_reason=agent_no_new_information`，
#    并记 0 次 LLM 调用 —— "没看"与"看过之后决定不动"必须分得开；
# ② 门控是**多给**而不是少给：任何一条触发就开会；`last_decision_at` 缺失时也开会
#    （fail-open：宁可多花钱，也不要因为传参缺失而永远沉默）。
TRIGGER_RULE_SIGNAL = "rule_signal"
TRIGGER_VOLATILITY_SPIKE = "volatility_spike"
TRIGGER_STALE = "stale"
TRIGGER_FIRST_DECISION = "first_decision"
CONVENE_TRIGGERS = (TRIGGER_RULE_SIGNAL, TRIGGER_VOLATILITY_SPIKE, TRIGGER_STALE,
                    TRIGGER_FIRST_DECISION)

#: 空仓时最多几天不开会（自然日）。空仓时"错过机会"的代价与"没盯着"的代价都更小。
MAX_DAYS_FLAT = 10
#: 有仓位时最多几天不开会 —— 持仓时风险敞口是真实的，盯得紧一点。
MAX_DAYS_HOLDING = 3
#: 波动/量能异常的倍数门槛。
SPIKE_MULTIPLE = 2.0
#: 波动的**绝对**下限（%）。
#: 为什么必须有它：极静市场里近 20 日平均波动可能是 0，"2×0 仍然是 0" ——
#: 于是"一片平静之后突然跳一下"这种**最该开会**的日子反而不开会。
#: 这是写测试时发现的：第一版的倍数判断在平盘序列上永远不触发。
MIN_MOVE_PCT = 3.0
#: 每个角色的输出只留这么长的摘要进痕迹（全文太大，哈希负责防篡改）。
EXCERPT_CHARS = 400

#: 角色顺序是**固定**的（不是"让模型决定先问谁"）：顺序一变，辩论的可比性就没了。
ROLES = ("bull", "bear", "research_manager", "trader",
         "risk_aggressive", "risk_conservative", "risk_neutral", "risk_judge")

ROLE_LABELS = {
    "bull": "多头研究员",
    "bear": "空头研究员",
    "research_manager": "研究主管",
    "trader": "交易员",
    "risk_aggressive": "风控·激进",
    "risk_conservative": "风控·保守",
    "risk_neutral": "风控·中性",
    "risk_judge": "风控裁决",
}

_ROLE_SYSTEM = {
    "bull": ("你是多头研究员。基于给定证据，只找**支持做多**的理由，并指出最强的反证是什么。"
             "不超过 200 字。"),
    "bear": ("你是空头研究员。基于给定证据，只找**反对做多**的理由（做空/观望的依据），"
             "并指出多头论证里最站不住的一点。不超过 200 字。"),
    "research_manager": ("你是研究主管。听完多空双方，给出**你自己的裁决**：哪一方的证据更硬，"
                         "以及这条标的当前处于什么状态（趋势/震荡/转折）。不超过 200 字。"),
    "trader": ("你是交易员。给出你的交易提案（方向与仓位比例），必须说明**触发条件**与"
               "**在什么情况下这个提案就作废**。不超过 200 字。"),
    "risk_aggressive": "你是激进风控。从「错过机会的成本」角度挑交易员提案的毛病。不超过 120 字。",
    "risk_conservative": "你是保守风控。从「最大回撤与流动性」角度挑交易员提案的毛病。不超过 120 字。",
    "risk_neutral": "你是中性风控。指出双方各自的过度之处，给出你认可的折中。不超过 120 字。",
    "risk_judge": ("你是风控裁决。综合全部材料给出**最终决策**。"
                   "证据不足时必须选择 HOLD，并在 warnings 里说明缺什么。"),
}


@dataclass
class CommitteeResult:
    """一轮委员会的产出：决策 + 可审计的过程记录。"""

    decision: TradeDecision
    evidence: dict
    transcript: list[dict] = field(default_factory=list)
    llm_calls: int = 0
    total_tokens: int = 0
    warnings: tuple[str, ...] = ()
    as_of: str = ""
    model: str = ""

    def as_audit(self) -> dict:
        """进痕迹的那份（角色摘要 + 成本 + 口径）。"""
        return {
            "as_of": self.as_of,
            "model": self.model,
            "prompt_version": prompt_version(),
            "llm_calls": self.llm_calls,
            "total_tokens": self.total_tokens,
            "budget": {"max_calls": MAX_LLM_CALLS, "max_tokens": MAX_TOTAL_TOKENS},
            "roles": self.transcript,
            "warnings": list(self.warnings),
            "decision": self.decision.as_dict(),
        }


# ==================== 1. 证据包（确定性 + as-of 守卫） ====================

def build_evidence(symbol: str, as_of: str, records: list[dict],
                   position: dict | None = None) -> tuple[dict, list[str]]:
    """把决策日**当时能看到**的证据打包。返回 (evidence, warnings)。

    as-of 守卫就在这里：晚于 `as_of` 的 bar 会被剔除，并且**留下警告**。
    剔除是必须的（否则指标里就含了未来信息），留警告也是必须的
    （否则"调用方给了未来数据"这件事永远没人知道）。
    """
    warnings: list[str] = []
    all_records = list(records or [])
    window = [record for record in all_records
              if str(record.get("date", "")) <= str(as_of)]
    dropped = len(all_records) - len(window)
    if dropped > 0:
        warnings.append(f"证据包剔除了 {dropped} 根晚于 {as_of} 的 bar（as-of 守卫）")
    if not window:
        return {}, warnings + [f"{as_of} 之前没有任何 bar"]

    ind = strategy_engine.compute_indicators(window)
    idx = len(window) - 1
    closes = [float(record.get("close") or 0.0) for record in window]

    def ma(window_size: int) -> Optional[float]:
        values = ind.get(f"ma_{window_size}")
        if values is None or idx >= len(values):
            return None
        value = float(values[idx])
        return None if value != value else round(value, 4)  # NaN → None

    def indicator(key: str) -> Optional[float]:
        values = ind.get(key)
        if values is None or idx >= len(values):
            return None
        value = float(values[idx])
        return None if value != value else round(value, 4)

    tail = window[-min(EVIDENCE_BARS, len(window)):]
    volumes = [float(record.get("volume") or 0.0) for record in window[-20:]]
    avg_volume = sum(volumes) / len(volumes) if volumes else 0.0

    evidence = {
        "symbol": symbol,
        "as_of": str(as_of),
        # 明确告诉模型"这是当时能看到的一切"，避免它去引用记忆里的后来行情
        "note": (f"以下是在 {as_of} 收盘时**可得**的证据。任何晚于该日的信息都不存在，"
                 "不得假设或引用。"),
        "last_close": round(closes[idx], 4),
        "indicators": {
            "ma_5": ma(5), "ma_10": ma(10), "ma_20": ma(20), "ma_60": ma(60),
            "rsi": indicator("rsi"),
            "macd_dif": indicator("macd_dif"), "macd_dea": indicator("macd_dea"),
        },
        "derived": {
            "return_20d_pct": _pct(closes, idx, 20),
            "return_60d_pct": _pct(closes, idx, 60),
            "distance_to_60d_high_pct": _distance(closes, idx, 60, high=True),
            "distance_to_60d_low_pct": _distance(closes, idx, 60, high=False),
            "volume_vs_20d_avg": round(closes and (float(window[-1].get("volume") or 0.0)
                                                   / avg_volume), 3) if avg_volume else None,
        },
        "recent_bars": [
            {"date": str(record.get("date")), "open": _num(record.get("open")),
             "high": _num(record.get("high")), "low": _num(record.get("low")),
             "close": _num(record.get("close")), "volume": _num(record.get("volume"))}
            for record in tail
        ],
        "position": _position(position),
    }
    return evidence, warnings


def _num(value: Any) -> Optional[float]:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _pct(closes: list[float], idx: int, lookback: int) -> Optional[float]:
    start = idx - lookback
    if start < 0 or closes[start] == 0:
        return None
    return round((closes[idx] - closes[start]) / closes[start] * 100.0, 2)


def _distance(closes: list[float], idx: int, lookback: int, *, high: bool) -> Optional[float]:
    start = max(0, idx - lookback + 1)
    window = closes[start:idx + 1]
    if not window:
        return None
    reference = max(window) if high else min(window)
    if reference == 0:
        return None
    return round((closes[idx] - reference) / reference * 100.0, 2)


def _position(position: dict | None) -> dict:
    """持仓状态也进证据包：**同一个行情，空仓与满仓该给不同的答案。**"""
    if not isinstance(position, dict):
        return {"shares": 0.0, "avg_cost": None, "high_watermark": None}

    def value(key):
        try:
            return round(float(position.get(key) or 0.0), 4)
        except (TypeError, ValueError):
            return None

    return {"shares": value("shares") or 0.0, "avg_cost": value("entry_price"),
            "high_watermark": value("high_watermark")}


# ==================== 1b. 召集门控（事件驱动） ====================

def _days_since(last_decision_at: Any, as_of: str) -> Optional[int]:
    """距上次**真的开会**过了多少自然日。解析不出来返回 None（调用方按"该开会"处理）。"""
    if not last_decision_at:
        return None
    text = str(last_decision_at).strip()[:10]
    try:
        from datetime import date as _date
        return (_date.fromisoformat(str(as_of)[:10]) - _date.fromisoformat(text)).days
    except (TypeError, ValueError):
        return None


def convene_reasons(records: list[dict], as_of: str, *, config: dict | None = None,
                    position: dict | None = None,
                    last_decision_at: Any = None) -> tuple[list[str], dict]:
    """该不该召集委员会？返回 (触发的理由, 供痕迹记录的判定细节)。

    **确定性、零 LLM 成本**。任何一条触发就开会；一条都没有就"今天没必要看"。

    注意这里不是"省钱的开关"，而是**把信息密度提上来**：
    日频召集时 8 个角色每天重新读一遍只差一根 bar 的证据，绝大多数结论是 HOLD ——
    那买到的不是信息，是重复劳动。真正值得开会的时刻少而明确。
    """
    reasons: list[str] = []
    detail: dict = {"as_of": str(as_of), "last_decision_at": str(last_decision_at or "")}

    # ① 从没开过会：不能因为"没触发"就一直不开
    if not last_decision_at:
        reasons.append(TRIGGER_FIRST_DECISION)

    # ② 规则信号：**复用规则引擎本身**（不另写一套判断）
    signal = None
    if config and records:
        try:
            rule = strategy_engine.evaluate_bar(config, records, str(as_of), position)
            signal = rule.get("signal")
            detail["rule_signal"] = signal
        except Exception as exc:  # noqa: BLE001 —— 门控失败按"该开会"处理（fail-open）
            detail["rule_signal_error"] = type(exc).__name__
    if signal in ("buy", "sell"):
        reasons.append(TRIGGER_RULE_SIGNAL)

    # ③ 波动/量能异常（只用手边已有的 bar，不引入新指标）
    closes = [float(record.get("close") or 0.0) for record in records or []]
    volumes = [float(record.get("volume") or 0.0) for record in records or []]
    if len(closes) >= 21:
        moves = [abs(closes[i] / closes[i - 1] - 1.0) for i in range(len(closes) - 20, len(closes))
                 if closes[i - 1]]
        average_move = sum(moves[:-1]) / len(moves[:-1]) if len(moves) > 1 else 0.0
        today_move = moves[-1] if moves else 0.0
        volume_ratio = (volumes[-1] / (sum(volumes[-20:]) / 20.0)) if sum(volumes[-20:]) else 0.0
        detail["today_move_pct"] = round(today_move * 100, 2)
        detail["average_move_pct"] = round(average_move * 100, 2)
        detail["volume_ratio"] = round(volume_ratio, 2)
        # 两条判据取或：① 明显大于近期平均；② 绝对幅度过了下限（覆盖"极静之后突然一跳"）
        relative = average_move > 0 and today_move >= SPIKE_MULTIPLE * average_move
        absolute = today_move * 100.0 >= MIN_MOVE_PCT
        detail["spike_basis"] = ("relative" if relative and not absolute
                                 else "absolute" if absolute and not relative
                                 else "both" if relative else "none")
        if relative or absolute or volume_ratio >= SPIKE_MULTIPLE:
            reasons.append(TRIGGER_VOLATILITY_SPIKE)

    # ④ 太久没看（有仓位时更短：持仓的风险敞口是真实的）
    holding = bool(isinstance(position, dict) and float(position.get("shares") or 0.0) > 0)
    limit = MAX_DAYS_HOLDING if holding else MAX_DAYS_FLAT
    age = _days_since(last_decision_at, as_of)
    detail["days_since_last_decision"] = age
    detail["stale_limit_days"] = limit
    detail["holding"] = holding
    if age is not None and age >= limit:
        reasons.append(TRIGGER_STALE)

    return reasons, detail


# ==================== 2. 委员会 ====================

def _role_messages(role: str, evidence: dict, transcript: list[dict]) -> list[dict]:
    """一个角色的输入：证据 + 之前所有角色的发言（摘要）。"""
    system = _ROLE_SYSTEM[role]
    if role in ("trader", "risk_judge"):
        system = f"{system}\n\n{decision_prompt_contract()}"
    prior = [{"role": entry["role"], "label": ROLE_LABELS.get(entry["role"], entry["role"]),
              "said": entry["excerpt"]} for entry in transcript]
    user = json.dumps({"evidence": evidence, "prior_statements": prior},
                      ensure_ascii=False, default=str)
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def _summarize(text: str) -> dict:
    body = str(text or "")
    return {
        "chars": len(body),
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()[:12],
        "excerpt": body[:EXCERPT_CHARS],
    }


def decide(symbol: str, as_of: str, records: list[dict],
           position: dict | None = None, *, settlement_kind: str = ec.SETTLEMENT_DAILY,
           adjust_mode: str | None = None, config: dict | None = None,
           last_decision_at: Any = None,
           completion: Optional[Callable] = None) -> dict:
    """跑一轮委员会并返回**与 `evaluate-bar` 同形状**的决策。

    形状相同是刻意的：结算侧只需要换一个调用地址，执行路径一行不改
    （整手/佣金下限/T+1/涨跌停/DECIMAL 全部照旧）。**模型决定要不要动，代码决定怎么动。**

    `config`（策略 JSON）只用于门控里的"规则信号"判断 —— 委员会本身不看 entry/exit 条件。
    """
    evidence, warnings = build_evidence(symbol, as_of, records, position)
    fingerprint = ec.fingerprint_for(settlement_kind, adjust_mode=adjust_mode or "",
                                     decision_mode=ec.DECISION_MODE_AGENT)
    base = {
        "decision": ec.DECISION_SKIP,
        "signal": None,
        "size_fraction": 0.0,
        "skip_reason": None,
        "matched_conditions": [],
        "date": str(as_of),
        "fill_basis": fingerprint.fill_basis,
        "fingerprint": fingerprint.as_dict(),
        "snapshot": None,
        "bar_date_missing": not evidence,
    }
    if not evidence:
        return {**base, "skip_reason": ec.SKIP_NO_BAR,
                "committee": _empty_audit(as_of, warnings)}

    # <h3>决策日**必须**有一根真实的 bar</h3>
    # 证据包取的是"截至决策日的最后一根"，那是为了算指标；但**决策日当天必须有 bar**，
    # 否则这条决策就是拿前一天的收盘价当今天用 —— 与规则路径的 `bar_date_missing` 守卫同一条纪律
    # （休市/数据未出时宁可不结算，也不要用旧 bar 冒充当日）。
    # 这一条是**真跑发现的**：2026-08-21 无 bar，而当时的实现静默用了 2026-08-20 的收盘价，
    # 报告上完全看不出来。现在：不跑委员会（省掉一次 ~30k tokens），直接按 no_bar 跳过。
    exact = any(str(record.get("date")) == str(as_of) for record in (records or []))
    if not exact:
        warnings.append(f"{as_of} 没有 K 线（休市或数据未出），未运行委员会")
        return {**base, "bar_date_missing": True, "skip_reason": ec.SKIP_NO_BAR,
                "committee": _empty_audit(as_of, warnings)}

    # ==================== 召集门控（事件驱动） ====================
    # 没有触发理由就不开会：**不花一次调用**，并且把"没看"记成一个真实原因。
    reasons, gate = convene_reasons(records, str(as_of), config=config, position=position,
                                    last_decision_at=last_decision_at)
    gate["convened"] = bool(reasons)
    gate["reasons"] = list(reasons)
    if not reasons:
        warnings.append("没有值得开会的触发理由（事件驱动门控），未运行委员会")
        return {**base,
                "price": evidence["last_close"],
                "snapshot": _gate_snapshot(evidence, as_of, fingerprint, gate),
                "skip_reason": ec.SKIP_AGENT_NO_NEW_INFORMATION,
                "committee": {**_empty_audit(as_of, warnings), "gate": gate}}

    price = evidence["last_close"]
    snapshot = ec.build_snapshot(
        bar=_bar_of(evidence, str(as_of)),
        indicators=dict(evidence.get("indicators") or {}),
        # 参数位放的是**决策口径**（而不是策略参数）：agent 这一路没有 DSL 参数，
        # 但痕迹必须能回答"这条决策按什么规则产生的"。
        params={"decision_mode": ec.DECISION_MODE_AGENT,
                "roles": list(ROLES), "prompt_version": prompt_version(),
                "budget": {"max_calls": MAX_LLM_CALLS, "max_tokens": MAX_TOTAL_TOKENS}},
        fingerprint=fingerprint,
    )
    base["snapshot"] = snapshot
    base["price"] = price

    result = run_committee(symbol, as_of, evidence, warnings, completion=completion)
    decision = result.decision
    audit = {**result.as_audit(), "gate": gate}
    snapshot["extra"]["committee"] = audit

    return {
        **base,
        "decision": decision.decision,
        "signal": decision.signal,
        "size_fraction": decision.size_fraction,
        "skip_reason": decision.skip_reason,
        "confidence": decision.confidence,
        "rationale": decision.rationale,
        "warnings": list(decision.warnings),
        "committee": audit,
    }


def _gate_snapshot(evidence: dict, as_of: str, fingerprint, gate: dict) -> dict:
    """"没开会"也要留证据：那天的 bar、指标、以及**为什么判定不必开会**。

    为什么不能省：报告要回答"agent 多久没真正看过行情了"，
    而这个答案只能从这些行里读出来 —— 什么都不记的话，"没开会"与"没跑"长得一样。
    """
    snapshot = ec.build_snapshot(
        bar=_bar_of(evidence, str(as_of)),
        indicators=dict(evidence.get("indicators") or {}),
        params={"decision_mode": ec.DECISION_MODE_AGENT, "roles": list(ROLES),
                "prompt_version": prompt_version(), "convened": False},
        fingerprint=fingerprint,
    )
    snapshot["extra"]["gate"] = gate
    return snapshot


def _bar_of(evidence: dict, as_of: str) -> dict:
    for bar in reversed(evidence.get("recent_bars") or []):
        if str(bar.get("date")) == as_of:
            return bar
    bars = evidence.get("recent_bars") or []
    return bars[-1] if bars else {"date": as_of}


def _empty_audit(as_of: str, warnings: list[str]) -> dict:
    return {"as_of": as_of, "roles": [], "llm_calls": 0, "total_tokens": 0,
            "warnings": list(warnings), "prompt_version": prompt_version()}


def run_committee(symbol: str, as_of: str, evidence: dict, initial_warnings: list[str],
                  *, completion: Optional[Callable] = None) -> CommitteeResult:
    """按固定顺序跑完全部角色。**永不抛异常**：任何故障都退化成"不动 + 原因"。"""
    import llm_service

    call = completion or llm_service.chat_completion
    model = str(getattr(llm_service, "MODEL", "") or "")
    warnings = list(initial_warnings)
    transcript: list[dict] = []
    calls = 0
    tokens = 0

    def bail(reason: str, decision: TradeDecision) -> CommitteeResult:
        return CommitteeResult(decision=decision, evidence=evidence, transcript=transcript,
                               llm_calls=calls, total_tokens=tokens,
                               warnings=tuple(warnings), as_of=as_of, model=model)

    def skip(reason: str, signal: Optional[str] = None, rationale: str = "") -> TradeDecision:
        return TradeDecision(decision=ec.DECISION_SKIP, signal=signal, size_fraction=0.0,
                             confidence=None, rationale=rationale, warnings=tuple(warnings),
                             skip_reason=ec.normalize_skip_reason(reason))

    # 模型服务不可用：先问清楚，别让它的"暂不可用"占位文本被当成角色的发言
    checker = getattr(llm_service, "_is_available", None)
    if callable(checker):
        try:
            if not checker():
                warnings.append("模型服务不可用，委员会未运行")
                return bail(ec.SKIP_AGENT_LLM_UNAVAILABLE, skip(ec.SKIP_AGENT_LLM_UNAVAILABLE))
        except Exception:  # noqa: BLE001 —— 探测本身失败不应阻止尝试
            pass

    judge_text = ""
    for role in ROLES:
        # 硬预算：**在调用之前**检查，超了就停在这里并说明停在哪一步
        if calls >= MAX_LLM_CALLS:
            warnings.append(f"调用次数上限 {MAX_LLM_CALLS} 已用尽，停在角色 {role}")
            return bail(ec.SKIP_AGENT_BUDGET_EXCEEDED, skip(ec.SKIP_AGENT_BUDGET_EXCEEDED))
        if tokens >= MAX_TOTAL_TOKENS:
            warnings.append(f"token 上限 {MAX_TOTAL_TOKENS} 已用尽，停在角色 {role}")
            return bail(ec.SKIP_AGENT_BUDGET_EXCEEDED, skip(ec.SKIP_AGENT_BUDGET_EXCEEDED))

        try:
            choice = call(_role_messages(role, evidence, transcript),
                          temperature=0.2, max_tokens=800)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"角色 {role} 调用失败（{type(exc).__name__}）")
            return bail(ec.SKIP_AGENT_LLM_UNAVAILABLE, skip(ec.SKIP_AGENT_LLM_UNAVAILABLE))

        calls += 1
        usage = _usage_of(choice)
        tokens += usage["total_tokens"]
        text = _content_of(choice)
        transcript.append({"role": role, "label": ROLE_LABELS.get(role, role),
                           "tokens": usage, **_summarize(text)})
        if not text.strip():
            warnings.append(f"角色 {role} 返回空内容")
        if role == "risk_judge":
            judge_text = text

    # 决策**只**认 Risk Judge 的输出：中途任何角色的倾向都不构成决策
    decision = parse_trade_decision(judge_text)
    if not judge_text.strip():
        decision = TradeDecision(decision=ec.DECISION_SKIP, signal=None, size_fraction=0.0,
                                 confidence=None, rationale="",
                                 warnings=tuple(warnings + ["风控裁决没有给出任何内容"]),
                                 skip_reason=ec.SKIP_AGENT_UNPARSABLE)
    return CommitteeResult(decision=decision, evidence=evidence, transcript=transcript,
                           llm_calls=calls, total_tokens=tokens,
                           warnings=tuple(warnings), as_of=as_of, model=model)


def _content_of(choice: Any) -> str:
    message = (choice or {}).get("message") if isinstance(choice, dict) else None
    content = (message or {}).get("content") if isinstance(message, dict) else None
    return str(content or "")


def _usage_of(choice: Any) -> dict:
    from agent import metering

    return metering.parse_usage(choice if isinstance(choice, dict) else None)


def describe() -> dict:
    """给 /health 与调试用：角色、预算、门控、口径。"""
    return {
        "roles": [{"key": role, "label": ROLE_LABELS.get(role, role)} for role in ROLES],
        "max_llm_calls": MAX_LLM_CALLS,
        "max_total_tokens": MAX_TOTAL_TOKENS,
        "budget_semantics": ("每次调用**前**检查：实际总量可能超出上限、超出不超过一次调用；"
                             "痕迹里同时记上限与实测总量"),
        "evidence_bars": EVIDENCE_BARS,
        "prompt_version": prompt_version(),
        "decision_mode": ec.DECISION_MODE_AGENT,
        "as_of_guard": "证据只到决策日为止，晚于该日的 bar 一律剔除并留警告",
        # 门控必须可见：它直接决定"多久才真的看一次行情"
        "convene_triggers": list(CONVENE_TRIGGERS),
        "stale_limits": {"flat_days": MAX_DAYS_FLAT, "holding_days": MAX_DAYS_HOLDING},
        "spike_multiple": SPIKE_MULTIPLE,
        "skip_when_not_convened": ec.SKIP_AGENT_NO_NEW_INFORMATION,
        # 实测值（供成本规划用；刻意不换成金额 —— 金额需要价目表，猜出来的美元数是假信息）
        "measured_tokens_per_decision": 21_000,
        "cost_note": "token 由痕迹记录；金额取决于所用模型的价目表，系统不猜",
    }
