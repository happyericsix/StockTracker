# -*- coding: utf-8 -*-
"""N5 事件线综合层：总编（一次全局 LLM）+ 撰稿（每条事件线一次 LLM）。

与既有两层的分工：
- `news_understanding.synthesize_market`：**瞬时**综合（缓存 10 分钟，不落库），
  回答"这屏快讯现在合起来在说什么"；
- 本模块：**积累**综合（Java 侧落库 news_topics），回答"这件事从发生到现在，
  来龙去脉是什么"—— 单条新闻看不出东西，事件线 + 后续报道串起来才有信息量。
  这正是用户对新闻雷达升级的核心诉求。

<h3>两段式的成本结构</h3>
总编每天 ~8 次定时运行 × 1 次调用（输入是每条线的紧凑摘要），
撰稿只给总编圈出的线（worth_deep）且受 Java 侧预算/冷却约束 ——
见 application.properties 的 news.editor.* 配置。

<h3>契约（Java↔Python 两侧封闭集，改一侧必须改另一侧）</h3>
EDITOR_OUTPUT_FIELDS / WRITER_OUTPUT_FIELDS 是两侧共用的字段名集合；
SKIP_REASONS 是 Java 侧落库的"没撰稿原因"枚举（Python 侧经
`tests/test_news_topic_contract_java.py` 与 Java 的 NewsTopicContract 逐字比对）。
"""

import logging

import llm_service
import news_understanding
from news_understanding import DIRECTIONS, extract_json, _rewrite

logger = logging.getLogger(__name__)

# ===== 契约常量（与 Java NewsTopicContract.java 逐字一致，测试钉住）=====
# 总编输出的字段名
EDITOR_OUTPUT_FIELDS = ("index", "title", "worth_deep", "reason")
# 撰稿输出的字段名（digest 是 30 字内一句话结论：雷达页不展开卡片只扫这一行）
WRITER_OUTPUT_FIELDS = ("digest", "title", "story", "outlook", "direction",
                        "confidence", "affected_symbols")
# Java 侧落库的"事件线未生成综合"原因（两侧封闭集）
SKIP_REASONS = (
    "editor_llm_unavailable",     # LLM 不可用/超时/输出解析失败
    "editor_budget_exceeded",     # 当日撰稿预算已用尽
    "editor_cooldown",            # 同一事件线两次撰稿之间的冷却期
    "editor_skip_low_priority",   # 总编判定不值得撰稿（信息量不足）
)

EDITOR_MAX_TOKENS = 1200
WRITER_MAX_TOKENS = 1500
WRITER_MAX_ITEMS = 20          # 事件线成员上限（超出的按时间保留最新）
WRITER_SAMPLE_CHARS = 120      # 成员摘要进入 prompt 的截断长度
TEMPERATURE = 0.2

AI_NOTICE = news_understanding.AI_NOTICE


# ==================== 总编：全局总览，一次调用 ====================


def _editor_input(clusters: list[dict]) -> str:
    """把事件线编成紧凑输入：每条线一行元信息 + 最多 3 条成员样例。"""
    lines = []
    for index, cluster in enumerate(clusters, 1):
        span = f"{str(cluster.get('first_at') or '')[:10]}~{str(cluster.get('last_at') or '')[:10]}"
        symbols = "、".join((cluster.get("symbols") or [])[:3]) or "无标的"
        lines.append(
            f"[{index}] {span}｜{cluster.get('member_count', 0)} 条报道｜"
            f"{cluster.get('source_count', 0)} 源｜标的：{symbols}\n"
            f"    代码标题：{str(cluster.get('title') or '')[:60]}"
        )
        for member in (cluster.get("members") or [])[:3]:
            direction = member.get("direction") or "未判方向"
            summary = str(member.get("plain_summary") or member.get("content") or "").strip()
            if summary:
                summary = summary[:WRITER_SAMPLE_CHARS]
            lines.append(
                f"    · [{str(member.get('published_at') or '')[:16]} "
                f"{member.get('source_name') or ''} {direction}] "
                f"{str(member.get('title') or '')[:50]}" + (f"——{summary}" if summary else "")
            )
    return "\n".join(lines)


def _normalize_editor_row(row, index_count: int) -> dict | None:
    """把总编输出的一行规整成契约形状；index 越界/缺 title 的行丢弃。"""
    if not isinstance(row, dict):
        return None
    try:
        index = int(row.get("index", 0))
    except (TypeError, ValueError):
        return None
    if not 1 <= index <= index_count:
        return None
    title = _rewrite(str(row.get("title") or "").strip())[:40]
    if not title:
        return None
    return {
        "index": index,
        "title": title,
        "worth_deep": bool(row.get("worth_deep", False)),
        "reason": _rewrite(str(row.get("reason") or "").strip())[:80],
    }


def editor_pass(clusters: list[dict]) -> dict:
    """总编：给每条事件线起名 + 圈出值得撰稿的（一次 LLM 调用）。

    Returns:
        `{"ok": bool, "error": "", "topics": [{index, title, worth_deep, reason}]}`
        LLM 不可用/解析失败 → ok=False（调用方按 editor_llm_unavailable 降级，
        事件线照常展示代码标题 —— fail-open）。
    """
    result = {"ok": False, "error": "", "topics": []}
    selected = [c for c in (clusters or []) if str(c.get("title") or "").strip()]
    if not selected:
        result["error"] = "没有可编的事件线"
        return result
    if not llm_service._is_available():
        result["error"] = "AI 服务未配置"
        return result

    system_prompt = llm_service._load_prompt("news_editor")
    user_prompt = (f"以下是今日的 {len(selected)} 条事件线（按最新活动倒序）：\n\n"
                   + _editor_input(selected) + "\n\n请按系统提示输出 JSON。")
    try:
        parsed, text = _chat_json(system_prompt, user_prompt, EDITOR_MAX_TOKENS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("总编调用失败: %s", exc)
        result["error"] = "AI 服务暂时不可用"
        return result
    rows = parsed.get("topics") if isinstance(parsed, dict) else parsed
    if not isinstance(rows, list):
        logger.warning("总编输出无法解析: %.120s", text)
        result["error"] = "输出无法解析"
        return result
    topics = [row for row in (_normalize_editor_row(r, len(selected)) for r in rows)
              if row is not None]
    if not topics:
        result["error"] = "输出没有可用行"
        return result
    result["topics"] = topics
    result["ok"] = True
    return result


# ==================== 撰稿：每条事件线一次调用 ====================


def _writer_input(topic: dict) -> str:
    """事件线成员 → 紧凑输入。只喂结论字段（方向/强度/一句话转述），不重贴正文 ——
    成员已逐条读过一次（N2），把原文塞回去只会诱导模型退化成逐条复述。"""
    title = str(topic.get("title") or "")
    lines = [f"事件线：{title}", f"以下是这条线上的 {len(topic.get('items') or [])} 条资讯（按时间倒序）：\n"]
    for index, item in enumerate(topic.get("items") or [], 1):
        level = {1: "公告", 2: "媒体", 3: "研报", 4: "舆情"}.get(item.get("source_level"), "资讯")
        direction = item.get("direction") or "未判方向"
        summary = str(item.get("plain_summary") or "").strip() or "（无解读）"
        lines.append(
            f"[{index}] {str(item.get('published_at') or '')[:16]}　{level}　"
            f"{item.get('source_name') or '未知'}　方向={direction}\n"
            f"    标题：{str(item.get('title') or '')}\n"
            f"    解读：{summary[:WRITER_SAMPLE_CHARS]}"
        )
    lines.append("\n请按系统提示输出 JSON。")
    return "\n".join(lines)


def _first_sentence(text: str, limit: int = 36) -> str:
    """story 的首个完整句（到句号/问号/叹号），超长按字数截断 —— digest 的兜底来源。"""
    cleaned = str(text or "").strip()
    for index, char in enumerate(cleaned):
        if char in "。！？；" and index + 1 <= limit * 2:
            head = cleaned[:index + 1]
            return head if len(head) <= limit * 2 else head[:limit] + "…"
    return cleaned[:limit] + ("…" if len(cleaned) > limit else "")


def _normalize_writer_output(parsed: dict, fallback_title: str) -> dict | None:
    """撰稿输出 → 契约形状；direction 不在枚举 → None（宁缺毋错，同 N2 纪律）。

    story/outlook 只做投顾句式改写（_rewrite），**不追加免责短句**：
    雷达页事件线区块是多条连排的，逐条追加会在一屏里刷出十个
    "（仅供参考，不构成投资建议）"——免责移到前端区块级统一一行
    （与 news_understanding 里"免责句是整条解读级别"的既有取舍同源）。
    """
    if not isinstance(parsed, dict):
        return None
    story = str(parsed.get("story") or "").strip()
    if not story:
        return None
    direction = parsed.get("direction")
    direction = direction if direction in DIRECTIONS else None
    confidence = parsed.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence))) if confidence is not None else 0.0
    except (TypeError, ValueError):
        confidence = 0.0
    if direction is None:
        confidence = min(confidence, news_understanding.MIN_CONFIDENCE - 0.01)
    affected = []
    for symbol in parsed.get("affected_symbols") or []:
        text = news_understanding._normalize_related(symbol)
        if text and text not in affected:
            affected.append(text)
    digest = _rewrite(str(parsed.get("digest") or "").strip())[:40]
    if not digest:
        digest = _first_sentence(story)
    return {
        "digest": digest,
        "title": _rewrite(str(parsed.get("title") or "").strip())[:40] or fallback_title[:40],
        "story": _rewrite(story),
        "outlook": _rewrite(str(parsed.get("outlook") or "").strip()),
        "direction": direction,
        "confidence": confidence,
        "affected_symbols": affected[:5],
    }


def write_topic(topic: dict) -> dict:
    """撰稿：把一条事件线写成连贯的"来龙去脉"。

    Returns:
        `{"ok": bool, "error": "", **WRITER_OUTPUT_FIELDS}`
        失败 → ok=False（调用方按 editor_llm_unavailable 留痕，下次运行重试）。
    """
    result = {"ok": False, "error": "",
              "digest": "", "title": "", "story": "", "outlook": "",
              "direction": None, "confidence": 0.0, "affected_symbols": []}
    items = [item for item in (topic.get("items") or [])
             if str(item.get("title") or "").strip()][:WRITER_MAX_ITEMS]
    if not items:
        result["error"] = "事件线没有成员"
        return result
    if not llm_service._is_available():
        result["error"] = "AI 服务未配置"
        return result

    fallback_title = str(topic.get("title") or "")
    try:
        parsed, text = _chat_json(
            llm_service._load_prompt("news_topic_writer"),
            _writer_input(dict(topic, items=items)), WRITER_MAX_TOKENS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("事件线撰稿失败（%s）: %s", fallback_title[:20], exc)
        result["error"] = "AI 服务暂时不可用"
        return result
    normalized = _normalize_writer_output(parsed, fallback_title) \
        if isinstance(parsed, dict) else None
    if normalized is None:
        logger.warning("事件线撰稿输出无法解析: %.120s", text)
        result["error"] = "输出无法解析"
        return result
    result.update(normalized)
    result["ok"] = True
    return result


# 复用 news_understanding 的容错 JSON 解析风格（围栏→裸 JSON→带纠错的重试）
def _chat_json(system_prompt: str, user_prompt: str, max_tokens: int, attempts: int = 2):
    last_text = ""
    for attempt in range(max(1, attempts)):
        messages = [{"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}]
        if attempt:
            messages.append({"role": "assistant", "content": last_text[:500]})
            messages.append({"role": "user",
                             "content": "上面的输出不是合法 JSON。只输出 JSON，不要任何其他文字。"})
        choice = llm_service.chat_completion(messages, temperature=TEMPERATURE,
                                             max_tokens=max_tokens)
        last_text = str((choice or {}).get("message", {}).get("content") or "")
        parsed = extract_json(last_text)
        if parsed is not None:
            return parsed, last_text
    return None, last_text
