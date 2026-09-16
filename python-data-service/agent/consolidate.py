"""会话巩固：把一段对话总结成"前情提要"（M0）。

<h3>为什么异步、为什么用便宜模型</h3>
巩固要调一次 LLM，绝不能放在用户消息的关键路径上（那会把首字延迟直接翻倍）。
触发方是 Java：它在用户跨天进入新会话时异步调用本模块。
模型用默认的 deepseek-chat 即可 —— 这是"整理"，不是"推理"。

<h3>为什么摘要必须可追溯</h3>
摘要一定会有损、会有误差。所以：
- 每个摘要都带 {@code sourceEventIds}，指向账本里的原始事件，随时能回查；
- 摘要<b>只写确实出现过的内容</b>，不确定的一律进 {@code open_questions}（疑问句），
  不允许写进 summary 当事实 —— 一次把"用户可能想换策略"写成"用户要换策略"，
  之后每段对话都会被这个错误污染，而且没人能发现。
- 生成失败/不是合法 JSON 时<b>不落库</b>，下次再试。宁可没有摘要，也不要一条坏摘要。

<h3>它是派生数据</h3>
摘要完全由账本推导，可以随时重放重建、可以重生成新版本（Java 侧版本号 +1，不覆盖旧版）。
所以这里的质量不是关键路径风险。
"""
from __future__ import annotations

import json
import logging
import re

import llm_service
from agent import facts as facts_mod
from agent import lessons as lessons_mod
from agent import memory_store

logger = logging.getLogger(__name__)

# 少于这么多条事件不值得总结（例如用户只发了一句就再没回来）
MIN_EVENTS_FOR_RECAP = 2
# 单条事件进提示词时的字符上限
MAX_EVENT_CHARS = 600
# 一次最多处理的对话事件条数（超长的会话只取最近的）
MAX_EVENTS = 60
# 摘要正文的字符上限
MAX_SUMMARY_CHARS = 1200
# 不进摘要的事件类型：usage 是我们自己的计量，不是对话内容
NON_DIALOG_KINDS = frozenset({"usage"})
# 不进摘要的来源：第三方内容属于"数据"，不是任何人的主张，不能变成长期记忆
NON_DIALOG_PROVENANCE = frozenset({"external"})


def _is_subagent_event(event) -> bool:
    """这条事件是不是**子角色**（`meta.role` 非空）产生的？

    <h3>为什么要按"谁产生的"过滤，而不是按"什么类型"过滤</h3>
    账本事件都记在用户身上，但子角色（critic / coach / 未来的并行角色）的工具日志
    是**系统的中间过程**，不是用户或助手说过的话。而前情提要被拿去抽事实与经验、
    长期保存 —— 一旦混进去，用户下次的"前情提要"里就会出现
    "critic 查了 250 天历史"这种句子，并被当成对话内容二次提炼。
    这与 NON_DIALOG_PROVENANCE 是同一条原则：**只有"人说的话"能进摘要**。

    所以这里刻意**不**把 `kind=tool_result` 一刀切排除：主 agent 的工具失败信息
    （"backtest_strategy 失败：数据不足"）正是经验抽取的原料
    （见 react_agent._tool_log_entry 的说明）。该过滤的是角色，不是类型。

    meta 是 TEXT 列，历史行可能不是合法 JSON —— 解析不了就按"主 agent"处理，
    宁可多进一条，也不要因为一条脏数据把整段摘要丢掉。
    """
    meta = event.get("meta")
    if not meta:
        return False
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError, ValueError):
            return False
    if not isinstance(meta, dict):
        return False
    return bool(str(meta.get("role") or "").strip())

SUMMARY_SYSTEM_PROMPT = """你是对话记忆整理器。把一段"用户 × 股票助手"的对话压缩成前情提要，\
并抽出值得长期记住的事实，供下次对话开始时参考。

严格输出下面这个 JSON（不要解释、不要代码块以外的任何文字）：
{
  "summary": "3~5 句话。讲清楚用户关心什么、做过什么决定、得到什么结论。用'用户'作主语。",
  "key_points": ["最多 6 条，每条一句话"],
  "open_questions": ["最多 3 条：还没确定、下次可能需要确认的问题；没有就给空数组"],
  "entities": {"symbols": ["600519"], "strategies": ["20日均线上穿60日"]},
  "facts": [
    {"subject": "user", "predicate": "stop_loss_pct", "object": "5",
     "fact_type": "constraint", "confidence": 0.9, "time_hint": ""}
  ],
  "lessons": [
    {"task_type": "backtest", "symptom": "回测提示历史数据不足",
     "attempts": ["直接用默认参数重跑", "仍然失败"],
     "resolution": "先取更长周期标的的历史再回测",
     "reusable_rule": "回测前先确认历史条数≥20，不足就先换标的或周期",
     "confidence": 0.7}
  ]
}

facts 是"值得跨会话记住的原子事实"，只在用户**明确说过**时才写：
- subject：关于用户自己写 user；关于某只股票写它的代码（如 600519）；关于某策略写 strategy:名称
- **风险设置类事实的 subject 默认写 user**：stop_loss_pct / take_profit_pct /
  trailing_stop_pct / position_size / risk_preference / max_drawdown_tolerance /
  investing_style / trading_frequency —— 这些是用户自己的设定，**即使他是在讨论某个策略时说的，
  也一样写 user**。原因：用户下次很可能只说"止损改成 3%"而不再提策略名，
  如果这次记在 strategy:名称 下、下次记在 user 下，两条事实就是两个不同的键，
  取代关系会断掉，系统里会同时存在两个互相矛盾的止损值。
  只有当用户**明确说**"这个策略专用"时才写 strategy:名称。
- predicate：只能用这些键——risk_preference（风险偏好）、investing_style（风格）、
  investment_horizon（投资周期）、capital（资金量）、position_size（仓位）、
  stop_loss_pct（止损）、take_profit_pct（止盈）、trailing_stop_pct（跟踪止盈）、
  holding_cost（持仓成本）、holding（持有）、watch_reason（关注理由）、
  sector_preference（板块偏好）、excluded_subject（明确不碰的）、
  target_return（目标收益）、max_drawdown_tolerance（最大回撤容忍）、purpose（用途）
- object：值本身（"稳健"、"5"、"1500"、"不碰ST股"）
- fact_type：preference 偏好 / constraint 约束 / goal 目标 / holding 持仓 / decision 决定
- confidence：0~1。用户原话直接说的给 0.8~0.95；需要一点推断才得出的给 0.6 以下
- time_hint：**只在用户原话里出现时间说法时**填那个说法本身（如"去年""上个月"），否则留空字符串

lessons 是"这类问题这次是怎么解决的"，用来避免以后重复踩坑：
- task_type：generate_strategy（生成策略） / backtest（回测） / quote_lookup（查行情） /
  analysis（技术分析） / screening（选股） / news_lookup（新闻公告） / general
- symptom：**可复现的现象**（"回测提示历史数据不足""取不到行情"），不是情绪描述
- attempts：试过哪些做法、为什么不行
- resolution：最后是怎么做成的
- reusable_rule：一句可执行、可验证的做法（比如"回测前先确认历史条数≥20"）
- confidence：0~1。只有对话里确实出现过"遇到问题 → 解决掉"的过程才写；平顺完成的任务不写。

规则（必须遵守）：
- 只写对话里**确实出现过**的内容。不要推测，不要补充常识，不要写投资建议，不要预测价格。
- 任何不确定的信息放进 open_questions，**不要**写进 summary 当成事实。
- facts 里**不要**写行情数据（价格、涨跌幅）——那些会变，属于可随时重查的信息，不是长期记忆。
- lessons 的 reusable_rule **不要**写成投资结论（如"跌破均线就卖"），只能写成操作方法。
- time_hint 必须原样照抄用户的说法，不要自己换算日期。
- 时间一律写绝对日期（用下面给出的对话日期），不要写"昨天""前几天"。
- entities 里只放对话中明确出现的股票代码/名称与策略名，没有就给空数组。"""


def _extract_json(text: str) -> dict | None:
    """从模型输出里取出 JSON 对象。

    与 strategy_schema.extract_strategy_json 是同一类解析（M1 可以合并成一个公共工具），
    这里单独写一份是为了不把"策略校验"和"记忆摘要"两个模块耦合在一起。
    """
    if not isinstance(text, str):
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _clip(text, limit):
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "…"


def _build_dialog(events: list) -> tuple[str, list, list]:
    """把账本事件渲染成提示词里的对话文本，返回 (文本, 参与的事件 id, 时间戳列表)。

    <h3>哪些事件**不进**摘要（P4d）</h3>
    摘要会被拿去抽"事实"与"经验"，长期保存。所以进摘要的内容必须满足
    "这句话是人说的"：

    - `kind=usage`（P1 的用量事件）：是我们自己的计量数字，不是对话内容。
      放进去只会让摘要里出现"本轮用量：LLM 3 次/8000 tokens"这种噪音。
    - `provenance=external`（第三方正文/数据）：它们**是**数据，不是任何人的主张。
      账本里本来就不存外部正文（只存"取到几条"），但这条规则要**显式**写出来 ——
      靠"上游碰巧没写进去"维持的安全边界，会被下一个往账本里塞正文的人一脚踹开。
    """
    lines: list[str] = []
    used_ids: list = []
    timestamps: list[str] = []

    for event in events[-MAX_EVENTS:]:
        if not isinstance(event, dict):
            continue
        content = (event.get("content") or "").strip()
        if not content:
            continue
        kind = event.get("kind") or ""
        if kind in NON_DIALOG_KINDS:
            continue
        if str(event.get("provenance") or "").lower() in NON_DIALOG_PROVENANCE:
            continue
        if _is_subagent_event(event):
            continue
        speaker = {"chat_user": "用户", "chat_bot": "助手", "strategy": "系统"}.get(kind, "系统")
        symbol = event.get("symbol")
        suffix = f"（{symbol}）" if symbol else ""

        occurred = str(event.get("occurredAt") or "")
        # 提示词里给人看的时间用"2026-09-14 10:00"；回传 Java 的 startedAt/endedAt
        # 必须保持 ISO 格式，否则 Jackson 反序列化 LocalDateTime 会失败。
        display = occurred[:16].replace("T", " ") if occurred else ""
        if occurred:
            timestamps.append(occurred[:19])

        lines.append(f"[{display}] {speaker}{suffix}: {_clip(content, MAX_EVENT_CHARS)}")
        if event.get("id") is not None:
            used_ids.append(event.get("id"))

    return "\n".join(lines), used_ids, timestamps


def _as_str_list(value, limit):
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:limit]


def consolidate_session(user_id, session_key) -> dict:
    """把一个会话总结成前情提要并写回 Java。返回结构化结果，不抛异常。"""
    if not user_id or not session_key:
        return {"skipped": "user_id/session_key required"}

    events = memory_store.list_events(user_id, session_key)
    if len(events) < MIN_EVENTS_FOR_RECAP:
        return {"skipped": f"only {len(events)} events"}

    dialog, source_event_ids, timestamps = _build_dialog(events)
    if not dialog.strip():
        return {"skipped": "empty dialog"}

    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": f"会话日期：{session_key}\n\n对话记录：\n{dialog}\n\n只输出 JSON。"},
    ]

    try:
        choice = llm_service.chat_completion(messages, temperature=0.1, max_tokens=800)
    except Exception as exc:  # noqa: BLE001
        logger.warning("会话摘要 LLM 调用失败 session=%s: %s", session_key, exc)
        return {"error": "llm unavailable"}

    message = (choice or {}).get("message") if isinstance(choice, dict) else None
    content = (message or {}).get("content") if isinstance(message, dict) else None
    parsed = _extract_json(content)
    if parsed is None:
        # 不落库：宁可没有摘要，也不要一条没法追溯的坏摘要
        logger.warning("会话摘要不是合法 JSON，已跳过 session=%s content=%s",
                       session_key, _clip(content, 200))
        return {"error": "invalid summary json"}

    summary = _clip(parsed.get("summary"), MAX_SUMMARY_CHARS).strip()
    if not summary:
        return {"error": "empty summary"}

    entities = parsed.get("entities") if isinstance(parsed.get("entities"), dict) else {}
    saved = memory_store.save_episode(
        user_id=user_id,
        session_key=session_key,
        summary=summary,
        key_points=_as_str_list(parsed.get("key_points"), 6),
        open_questions=_as_str_list(parsed.get("open_questions"), 3),
        entities=entities,
        source_event_ids=source_event_ids,
        model=llm_service.MODEL,
        started_at=timestamps[0] if timestamps else None,
        ended_at=timestamps[-1] if timestamps else None,
    )
    if not saved:
        return {"error": "failed to persist episode"}

    # 事实层：同一次 LLM 调用里顺带抽出来（不额外花钱），规范化后交给 Java 判定取代关系。
    # 这一步失败不影响摘要已落库 —— 记忆是增强功能，不能因为事实写失败就把摘要也回滚了。
    fact_results = _save_facts(user_id, session_key, parsed.get("facts"), source_event_ids)
    lesson_results = _save_lessons(user_id, session_key, parsed.get("lessons"), source_event_ids)

    # 语料变了，语义索引要失效：否则用户这一轮刚改的口令，下一轮语义召回还用旧向量
    try:
        from agent import vector_index

        vector_index.invalidate(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("语义索引失效失败: %s", exc)

    logger.info("会话摘要已生成 session=%s v=%s events=%d facts=%d lessons=%d",
                session_key, saved.get("version"), len(source_event_ids),
                len(fact_results), len(lesson_results))
    return {
        "sessionKey": session_key,
        "version": saved.get("version"),
        "events": len(source_event_ids),
        "summary": summary,
        "facts": fact_results,
        "lessons": lesson_results,
    }


def _save_lessons(user_id, session_key, raw_lessons, source_event_ids) -> list:
    """规范化并写入经验（一律 pending 落库）。失败只记日志。"""
    try:
        normalized = lessons_mod.normalize(raw_lessons, source_event_ids=source_event_ids)
    except Exception as exc:  # noqa: BLE001
        logger.warning("经验规范化失败 session=%s: %s", session_key, exc)
        return []
    if not normalized:
        return []
    try:
        result = memory_store.save_lessons(user_id, session_key, normalized, source_event_ids)
    except Exception as exc:  # noqa: BLE001
        logger.warning("经验写入失败 session=%s: %s", session_key, exc)
        return []
    items = result.get("results") if isinstance(result, dict) else None
    if not isinstance(items, list):
        return []
    for item in items:
        if isinstance(item, dict) and item.get("occurrences", 0) >= 3:
            # 复发到一定次数就值得人来判断该不该变成 skill —— 只提示，不自动升格
            logger.info("经验复发 %s 次，建议人工审核升格: %s | %s",
                        item.get("occurrences"), item.get("taskType"), item.get("symptom"))
    return items


def _save_facts(user_id, session_key, raw_facts, source_event_ids) -> list:
    """规范化并写入事实，返回 Java 侧的处置结果（created / superseded / unchanged）。"""
    try:
        normalized = facts_mod.normalize(raw_facts, now=_now(), source_event_ids=source_event_ids)
    except Exception as exc:  # noqa: BLE001
        logger.warning("事实规范化失败 session=%s: %s", session_key, exc)
        return []
    if not normalized:
        return []
    try:
        result = memory_store.save_facts(user_id, session_key, normalized, source_event_ids)
    except Exception as exc:  # noqa: BLE001
        logger.warning("事实写入失败 session=%s: %s", session_key, exc)
        return []
    items = result.get("results") if isinstance(result, dict) else None
    if not isinstance(items, list):
        return []
    for item in items:
        # 取代动作要留痕：这是排查"为什么某个设置变了"的唯一线索
        if isinstance(item, dict) and item.get("action") == "superseded":
            logger.info("事实被取代 user=%s %s.%s 旧值 id=%s",
                        user_id, item.get("subject"), item.get("predicate"), item.get("supersededId"))
    return items


def _now():
    from datetime import datetime

    from agent.memory_store import CN_TZ

    return datetime.now(CN_TZ)
