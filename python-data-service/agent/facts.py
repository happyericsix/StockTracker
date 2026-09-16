"""事实（L2）的规范化：把模型抽出来的东西整理成可入库、可取代、可检索的结构。

<h3>为什么需要一个专门的规范化步骤</h3>
模型的抽取结果不能直接入库，三个原因：
1. **键必须稳定**——"止损"和"stop_loss_pct"如果算两个键，取代链就断了：
   用户第二次改止损会新增一条事实，而旧的那条仍然"有效"，之后每次注入都会看到两个互相矛盾的止损值。
   所以这里用别名表把常见中文谓词映射到规范键。
2. **时间必须解析**——"去年"要在写入时变成绝对区间（见 timeutil），解析不出来就不带时间，不猜。
3. **置信度与来源必须显式**——低于阈值的直接丢弃；模型推断出来的一律
   {@code confirmed=false}（不能被当成用户说过的话）。

这个模块<b>只做整理，不做判断</b>：谁取代谁由 Java 侧在事务里决定（见 MemoryFactService），
因为那是一致性问题，不是文本问题。
"""
from __future__ import annotations

from agent import timeutil

FACT_TYPES = ("preference", "constraint", "goal", "holding", "decision", "observation")

# 低于该置信度的事实直接丢弃：模型瞎猜的东西不该变成"记忆"
MIN_CONFIDENCE = 0.3
MAX_FACTS_PER_SESSION = 20
MAX_VALUE_CHARS = 200
MAX_PREDICATE_CHARS = 64

# 谓词别名 → 规范键。收敛键名是取代链能工作的前提（见模块 docstring）
PREDICATE_ALIASES = {
    "止损": "stop_loss_pct",
    "止损比例": "stop_loss_pct",
    "止损线": "stop_loss_pct",
    "止盈": "take_profit_pct",
    "止盈比例": "take_profit_pct",
    "跟踪止盈": "trailing_stop_pct",
    "风险偏好": "risk_preference",
    "风险承受": "risk_preference",
    "仓位": "position_size",
    "仓位比例": "position_size",
    "持仓成本": "holding_cost",
    "成本价": "holding_cost",
    "买入价": "holding_cost",
    "持有": "holding",
    "关注理由": "watch_reason",
    "关注原因": "watch_reason",
    "投资周期": "investment_horizon",
    "持有周期": "investment_horizon",
    "资金量": "capital",
    "本金": "capital",
    "交易频率": "trading_frequency",
    "行业偏好": "sector_preference",
    "板块偏好": "sector_preference",
    "不碰": "excluded_subject",
    "禁用": "excluded_subject",
    "禁买": "excluded_subject",
    "投资风格": "investing_style",
    "风格": "investing_style",
    "目标收益": "target_return",
    "最大回撤容忍": "max_drawdown_tolerance",
    "学习目标": "learning_goal",
    "用途": "purpose",
}


def canonical_predicate(raw) -> str:
    """把谓词收敛成规范键：中文别名 → 英文键；英文 → 小写下划线形式。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    if text in PREDICATE_ALIASES:
        return PREDICATE_ALIASES[text]
    for alias, canonical in PREDICATE_ALIASES.items():
        if len(alias) >= 2 and alias in text:
            return canonical
    # 英文/混合：转成小写下划线，保证 "Stop Loss" 与 "stop_loss" 是同一个键
    normalized = text.lower().replace(" ", "_").replace("-", "_")
    return "".join(ch for ch in normalized if ch.isalnum() or ch == "_")[:MAX_PREDICATE_CHARS]


def normalize(raw_facts, now=None, source_event_ids=None) -> list[dict]:
    """把模型给出的事实列表整理成可入库结构。非法项直接丢弃，不做修补。"""
    if not isinstance(raw_facts, list):
        return []

    normalized: list[dict] = []
    seen_keys: dict[tuple[str, str], int] = {}

    for item in raw_facts:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject") or "").strip()
        predicate = canonical_predicate(item.get("predicate"))
        value = str(item.get("object") or item.get("value") or "").strip()
        if not subject or not predicate or not value:
            continue
        if len(value) > MAX_VALUE_CHARS:
            value = value[:MAX_VALUE_CHARS]

        confidence = _confidence(item.get("confidence"))
        if confidence < MIN_CONFIDENCE:
            continue

        fact_type = str(item.get("fact_type") or item.get("factType") or "observation").strip().lower()
        if fact_type not in FACT_TYPES:
            fact_type = "observation"

        raw_phrase = str(item.get("time_hint") or item.get("raw_time_phrase") or "").strip()
        span = timeutil.resolve(raw_phrase, now) if raw_phrase else None

        fact = {
            "subject": subject[:128],
            "predicate": predicate,
            "object": value,
            "fact_type": fact_type,
            "confidence": confidence,
            "event_time": span.point if span else None,
            "raw_time_phrase": raw_phrase or None,
            "provenance": "model",
            # 用户亲口说的内容才可能是 high；模型抽取的一律 medium，注入时会标注
            "trust": "medium",
            "confirmed": bool(item.get("confirmed", False)),
            "source_event_ids": list(source_event_ids or []),
        }

        key = (fact["subject"].lower(), fact["predicate"])
        if key in seen_keys:
            # 同一段会话里对同一个键说了多次：后面的说法覆盖前面的（保留最新），
            # 但保留先前那条的置信度上限，避免"越说越确定"的自我强化
            previous = normalized[seen_keys[key]]
            fact["confidence"] = max(previous["confidence"], fact["confidence"])
            normalized[seen_keys[key]] = fact
        else:
            seen_keys[key] = len(normalized)
            normalized.append(fact)

        if len(normalized) >= MAX_FACTS_PER_SESSION:
            break

    return normalized


def _confidence(raw) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.6  # 模型没给置信度时用中等默认值，而不是直接丢弃
    return max(0.0, min(1.0, value))


def describe(fact: dict) -> str:
    """给日志/调试用的一行描述。"""
    return f"{fact.get('subject')}.{fact.get('predicate')}={fact.get('object')}"
