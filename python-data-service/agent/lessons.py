"""经验的规范化（与 facts.py 对称）。

经验是"什么情境下用什么做法才有效"：情境 → 有效做法 → 不知道会怎样做差。
它不要求踩坑——平顺完成的任务里发现的非默认做法同样是经验；
判据只有一条：不知道这条，下次同样的情境下助手会做错或做差吗？

<h3>为什么要专门的规范化</h3>
1. **task_type 必须收敛**：中文"生成策略"和 `generate_strategy` 如果是两个键，
   同一个坑的复发次数就统计不出来，而复发次数正是"值不值得升格成 skill"的判据。
2. **情境必须保留原始现象**：注入时靠它做匹配，"回测数据不足"比"出错了"有用得多。
3. **必须有解法**：没有"怎么做成的"就不算经验，只是一句抱怨 —— 直接丢弃。
4. **必须自证价值**：写不出"不知道这条会怎样做差"（why_nontrivial）的经验是
   常识复述，挤占注入窗口还误导以后的对话 —— 直接丢弃。
5. **一律只做整理、不做判断**：去重与复发计数由 Java 侧按行数推导（见 MemoryLessonService）。
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
# why_nontrivial 的最短长度：它要一句话说清"不知道这条，下次会怎么错/差"。
# 撑不到这个长度的（"更稳""少走弯路"）等于没说，按写不出处理。
MIN_WHY_CHARS = 8
# 单条经验最多引用几行来源（防 LLM 把整段对话都标成"依据"）
MAX_EVIDENCE_REFS = 8


def _resolve_refs(refs, line_ids, fallback) -> list:
    """LLM 引用的行号 → 具体事件 id（记忆白盒：来源到句级）。

    行号越界/非整数/对应行无 id 的一律丢弃；解析完为空就退回整段会话的 id
    （fallback）—— 宁可来源粗一点，也不要一条"没有来源"或"来源是错的"的经验。
    """
    if not isinstance(refs, list) or not line_ids:
        return list(fallback or [])
    resolved = []
    for raw in refs:
        try:
            idx = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= idx <= len(line_ids) and line_ids[idx - 1] is not None:
            resolved.append(line_ids[idx - 1])
    # 去重保序，再封顶
    seen = set()
    unique = [i for i in resolved if not (i in seen or seen.add(i))]
    return unique[:MAX_EVIDENCE_REFS] if unique else list(fallback or [])


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


def normalize(raw_lessons, source_event_ids=None, line_ids=None) -> list[dict]:
    """整理模型给出的经验列表。非法项直接丢弃，不做修补。

    line_ids（渲染对话时每行对应的事件 id）在场时，用 LLM 引用的 evidence_refs
    把每条经验的证据细化到句级；不在场或引用无效时退回整段会话 id。
    """
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
            # 没有情境、或者没有解法：不是一条可复用的经验
            continue
        why = str(item.get("why_nontrivial") or item.get("whyNontrivial") or "").strip()
        if len(why) < MIN_WHY_CHARS:
            # 写不出"不知道这条会怎样做差"的是常识复述：一条平庸的记忆比没有更糟，
            # 它会挤占注入窗口、误导以后每一次对话
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
            "evidence_event_ids": _resolve_refs(
                item.get("evidence_refs") or item.get("evidenceRefs"),
                line_ids, source_event_ids),
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
