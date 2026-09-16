"""记忆系统与 Java 内部接口之间的客户端（M0）。

<h3>方向与边界</h3>
数据在 Java（MySQL），Python 侧<b>没有任何数据库凭据</b>，只能通过
{@code /api/v1/internal/memory/*} 读写，且接口形状本身就限制了能力：
读上下文、读事件、追加事件、写摘要 —— 没有 update/delete 可以调用。
这是"agent 不能修改数据层"这条约束在代码层的落地。

<h3>失败一律 fail-open</h3>
记忆是增强功能，不是关键路径。Java 没起、网络抖动、令牌不对……任何一种情况都只能是
"这次没有记忆"，绝不能变成"用户发不出消息"。因此这里所有函数都不抛异常，
失败时返回空值并打日志（用 debug 级别，避免正常降级时刷屏）。

<h3>时区</h3>
固定用 UTC+8，不用 {@code zoneinfo}：Windows 上没装 tzdata 时
{@code ZoneInfo("Asia/Shanghai")} 会直接抛异常，而中国没有夏令时，
固定偏移与真实时区等价、且零依赖。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

CN_TZ = timezone(timedelta(hours=8))
TZ_LABEL = "Asia/Shanghai (UTC+8)"


def _base_url() -> str:
    # 优先读 JAVA_BASE_URL，其次跟随 .env.example 里既有的 SPRING_BASE_URL 命名。
    # 两套名字都认，是因为"两边配置名对不上导致静默失效"在这个项目里已经发生过
    # （user_id / user_name 那次）。
    return (os.getenv("JAVA_BASE_URL") or os.getenv("SPRING_BASE_URL")
            or "http://localhost:8080").rstrip("/")


def _headers() -> dict:
    # .env 由 llm_service 在导入时加载，所以这里必须**延迟读取**：
    # 模块导入顺序不受我们控制，早读会拿到空令牌。
    token = os.getenv("INTERNAL_API_TOKEN", "").strip()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Internal-Token"] = token
    return headers


def _timeout() -> float:
    try:
        return float(os.getenv("MEMORY_HTTP_TIMEOUT", "5"))
    except ValueError:
        return 5.0


def today_str() -> str:
    """用户所在时区的"今天"。agent 必须知道今天是几号，否则"去年""上周"无从换算。"""
    return datetime.now(CN_TZ).strftime("%Y-%m-%d")


def _get(path: str, params: dict) -> dict:
    try:
        resp = requests.get(f"{_base_url()}{path}", params=params,
                            headers=_headers(), timeout=_timeout())
        resp.raise_for_status()
        payload = resp.json()
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:  # noqa: BLE001 —— 记忆层不允许把异常抛给聊天链路
        logger.debug("记忆接口读取失败 %s: %s", path, exc)
        return {}


def _post(path: str, body: dict) -> dict:
    try:
        resp = requests.post(f"{_base_url()}{path}", json=body,
                             headers=_headers(), timeout=_timeout())
        resp.raise_for_status()
        payload = resp.json()
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("记忆接口写入失败 %s: %s", path, exc)
        return {}


def load_context(user_id, session_key, query=None, symbol=None, fact_limit=None) -> dict:
    """取"今天几号 + 前情提要 + 画像 + 相关事实与经验"。

    query/symbol 用来挑<b>相关</b>的条目；fact_limit 给大一些是为了让 Python 侧的
    语义融合有足够候选（Java 的关键词排序只是其中一路名次，见 vector_index）。
    失败返回 {}（调用方会退化成只有时间锚点）。
    """
    if not user_id or not session_key:
        return {}
    params = {"userId": user_id, "sessionKey": session_key}
    if query:
        params["query"] = query
    if symbol:
        params["symbol"] = symbol
    if fact_limit:
        params["factLimit"] = fact_limit
    return _get("/api/v1/internal/memory/context", params)


def index_corpus(user_id) -> dict:
    """向量索引语料（事实 + 摘要 + 经验）。Python 侧带 TTL 缓存，几分钟拉一次。"""
    if not user_id:
        return {}
    return _get("/api/v1/internal/memory/index", {"userId": user_id})


def search_lessons(user_id, query=None, task_type=None, limit=3) -> list:
    """检索经验（"这类问题上次是怎么解决的"）。"""
    if not user_id:
        return []
    params = {"userId": user_id, "limit": limit}
    if query:
        params["query"] = query
    if task_type:
        params["taskType"] = task_type
    payload = _get("/api/v1/internal/memory/lessons", params)
    lessons = payload.get("lessons")
    return lessons if isinstance(lessons, list) else []


def save_lessons(user_id, session_key, lessons, evidence_event_ids=None) -> dict:
    """写入经验。一律以 pending 落库，人工确认（或回归通过）才会变 active。"""
    if not user_id or not lessons:
        return {}
    return _post("/api/v1/internal/memory/lessons", {
        "userId": user_id,
        "sessionKey": session_key,
        "evidenceEventIds": list(evidence_event_ids or []),
        "lessons": [_lesson_payload(lesson) for lesson in lessons],
    })


def append_events(user_id, session_key, events) -> dict:
    """批量追加账本事件（工具调用结果入账走这里，避免每个工具一次 HTTP）。"""
    if not user_id or not session_key or not events:
        return {}
    return _post("/api/v1/internal/memory/events/batch", {
        "userId": user_id,
        "sessionKey": session_key,
        "events": list(events),
    })


def retract_fact(user_id, fact_id) -> dict:
    """撤回一条事实（逻辑撤回，历史仍可回溯）。"""
    if not user_id or not fact_id:
        return {}
    return _post(f"/api/v1/internal/memory/facts/{fact_id}/retract?userId={user_id}", {})


def _lesson_payload(lesson: dict) -> dict:
    """Python 内部（snake_case）→ Java 内部 API（camelCase）。

    与事实层同样的坑：字段名对不上就静默丢内容
    （task_type 丢了 → 经验归到错误的键上，复发次数统计不出来）。
    """
    return {
        "taskType": lesson.get("task_type") or lesson.get("taskType"),
        "symptom": lesson.get("symptom"),
        "context": lesson.get("context"),
        "attempts": lesson.get("attempts"),
        "resolution": lesson.get("resolution"),
        "reusableRule": lesson.get("reusable_rule") or lesson.get("reusableRule"),
        "confidence": lesson.get("confidence"),
        "provenance": lesson.get("provenance"),
        "trust": lesson.get("trust"),
    }


def search_facts(user_id, query=None, symbol=None, fact_type=None, limit=8) -> list:
    """检索当前有效的事实（agent 的 memory_search 工具走这里，只读）。"""
    if not user_id:
        return []
    params = {"userId": user_id, "limit": limit}
    if query:
        params["query"] = query
    if symbol:
        params["symbol"] = symbol
    if fact_type:
        params["factType"] = fact_type
    payload = _get("/api/v1/internal/memory/facts", params)
    facts = payload.get("facts")
    return facts if isinstance(facts, list) else []


def save_facts(user_id, session_key, facts, source_event_ids=None) -> dict:
    """批量写入事实。谁取代谁由 Java 侧判定（同键不同值 = 取代）。"""
    if not user_id or not facts:
        return {}
    return _post("/api/v1/internal/memory/facts", {
        "userId": user_id,
        "sessionKey": session_key,
        "sourceEventIds": list(source_event_ids or []),
        "facts": [_fact_payload(fact) for fact in facts],
    })


def _fact_payload(fact: dict) -> dict:
    """Python 内部（snake_case）→ Java 内部 API（camelCase）。

    这层显式映射不是多余的：字段名两边对不上是这个项目里最贵的一类故障
    （user_id / user_name 那次是"整条链路看起来坏了却没有任何报错"）。
    事实里的 fact_type / event_time 一旦漏掉，语义会静默降级成
    "类型=observation、时间=空"，而时间正是这个系统的核心。
    契约由 tests/test_memory_internal_contract.py 钉住。
    """
    return {
        "subject": fact.get("subject"),
        "predicate": fact.get("predicate"),
        "object": fact.get("object"),
        "factType": fact.get("fact_type") or fact.get("factType"),
        "confidence": fact.get("confidence"),
        "eventTime": fact.get("event_time") or fact.get("eventTime"),
        "rawTimePhrase": fact.get("raw_time_phrase") or fact.get("rawTimePhrase"),
        "dataAsOf": fact.get("data_as_of") or fact.get("dataAsOf"),
        "provenance": fact.get("provenance"),
        "trust": fact.get("trust"),
        "confirmed": fact.get("confirmed"),
    }


def list_events(user_id, session_key) -> list:
    """某段会话的全部账本事件（生成摘要用）。"""
    payload = _get("/api/v1/internal/memory/events",
                   {"userId": user_id, "sessionKey": session_key})
    events = payload.get("events")
    return events if isinstance(events, list) else []


def save_episode(user_id, session_key, summary, key_points, open_questions,
                 entities, source_event_ids, model, started_at=None, ended_at=None) -> dict:
    """把生成的摘要写回 Java（版本号由 Java 分配）。"""
    body = {
        "userId": user_id,
        "sessionKey": session_key,
        "summary": summary,
        "keyPoints": key_points,
        "openQuestions": open_questions,
        "entities": entities,
        "sourceEventIds": source_event_ids,
        "model": model,
        "startedAt": started_at,
        "endedAt": ended_at,
    }
    return _post("/api/v1/internal/memory/episodes", body)


def append_event(user_id, session_key, kind, role, content,
                 symbol=None, provenance="model", trust="medium") -> dict:
    """追加一条账本事件（M0 里 Java 侧自己在写，这里留给后续 Python 侧主动记账用）。"""
    return _post("/api/v1/internal/memory/events", {
        "userId": user_id,
        "sessionKey": session_key,
        "kind": kind,
        "role": role,
        "content": content,
        "symbol": symbol,
        "provenance": provenance,
        "trust": trust,
    })
