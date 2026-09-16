"""经验的规范化（与 facts.py 对称）。

经验是"这类问题上次是怎么解决的"：症状 → 试过什么 → 最后怎么做成的 → 可复用做法。

<h3>为什么要专门的规范化</h3>
1. **task_type 必须收敛**：中文"生成策略"和 `generate_strategy` 如果是两个键，
   同一个坑的复发次数就统计不出来，而复发次数正是"值不值得升格成 skill"的判据。
2. **症状必须保留原始现象**：注入时靠它做匹配，"回测数据不足"比"出错了"有用得多。
3. **必须有解法**：没有"怎么解决的"就不算经验，只是一句抱怨 —— 直接丢弃。
4. **一律只做整理、不做判断**：去重与复发计数由 Java 侧按行数推导（见 MemoryLessonService）。
"""
from __future__ import annotations

TASK_TYPE_ALIASES = {
    "生成策略": "generate_strategy",
    "写策略": "generate_strategy",
    "策略生成": "generate_strategy",
    "改策略": "generate_strategy",
    "回测": "backtest",
    "策略回测": "backtest",
    "查行情": "quote_lookup",
    "行情": "quote_lookup",
    "查价": "quote_lookup",
    "技术分析": "analysis",
    "分析": "analysis",
    "看盘": "analysis",
    "选股": "screening",
    "新闻": "news_lookup",
    "公告": "news_lookup",
    "研报": "news_lookup",
    "闲聊": "general",
}

CANONICAL_TASK_TYPES = (
    "generate_strategy", "backtest", "quote_lookup", "analysis",
    "screening", "news_lookup", "general",
)

MIN_CONFIDENCE = 0.3
MAX_LESSONS_PER_SESSION = 5
MAX_SYMPTOM_CHARS = 200
MAX_FIELD_CHARS = 500


def canonical_task_type(raw) -> str:
    text = str(raw or "").strip()
    if not text:
        return "general"
    if text in TASK_TYPE_ALIASES:
        return TASK_TYPE_ALIASES[text]
    lowered = text.lower().replace(" ", "_").replace("-", "_")
    if lowered in CANONICAL_TASK_TYPES:
        return lowered
    for alias, canonical in TASK_TYPE_ALIASES.items():
        if alias in text:
            return canonical
    return lowered[:64] or "general"


def normalize(raw_lessons, source_event_ids=None) -> list[dict]:
    """整理模型给出的经验列表。非法项直接丢弃，不做修补。"""
    if not isinstance(raw_lessons, list):
        return []

    normalized: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for item in raw_lessons:
        if not isinstance(item, dict):
            continue
        symptom = str(item.get("symptom") or "").strip()
        resolution = str(item.get("resolution") or "").strip()
        rule = str(item.get("reusable_rule") or item.get("reusableRule") or "").strip()
        if not symptom or (not resolution and not rule):
            # 没有症状、或者没有解法：不是一条可复用的经验
            continue
        if _confidence(item.get("confidence")) < MIN_CONFIDENCE:
            continue

        task_type = canonical_task_type(item.get("task_type") or item.get("taskType"))
        key = (task_type, symptom.lower())
        if key in seen:
            continue
        seen.add(key)

        normalized.append({
            "task_type": task_type,
            "symptom": symptom[:MAX_SYMPTOM_CHARS],
            "context": item.get("context") if isinstance(item.get("context"), dict) else {},
            "attempts": _as_str_list(item.get("attempts")),
            "resolution": resolution[:MAX_FIELD_CHARS],
            "reusable_rule": rule[:MAX_FIELD_CHARS],
            "confidence": _confidence(item.get("confidence")),
            "provenance": "model",
            # 经验一律 medium：它可能被污染，注入时标注"未经确认"
            "trust": "medium",
            "evidence_event_ids": list(source_event_ids or []),
        })
        if len(normalized) >= MAX_LESSONS_PER_SESSION:
            break

    return normalized


def _confidence(raw) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.6
    return max(0.0, min(1.0, value))


def _as_str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:MAX_FIELD_CHARS] for item in value if str(item).strip()][:6]
