# -*- coding: utf-8 -*-
"""会话巩固：把一段对话总结成前情提要。

最重要的一条：**宁可没有摘要，也不要一条坏摘要**。
摘要会被注入到以后每一次对话里，一条把推测写成事实的摘要会持续污染后续所有回答，
而且很难被发现。所以事件太少、模型不可用、输出不是合法 JSON —— 一律不落库。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import consolidate, memory_store


EVENTS = [
    {"id": 11, "kind": "chat_user", "role": "user", "content": "帮我做一个20日上穿60日买入的策略", "occurredAt": "2026-09-14T10:00:00"},
    {"id": 12, "kind": "chat_bot", "role": "assistant", "content": "已生成策略，回测显示最大回撤偏大", "occurredAt": "2026-09-14T10:01:00"},
    {"id": 13, "kind": "chat_user", "role": "user", "content": "那如果把止损改成5%呢", "occurredAt": "2026-09-14T10:05:00"},
]

VALID_SUMMARY = {
    "summary": "用户在调试均线策略，发现回撤偏大，尝试收紧止损。",
    "key_points": ["20日上穿60日", "关注回撤"],
    "open_questions": ["是否改用周线"],
    "entities": {"symbols": ["600519"], "strategies": ["20日上穿60日"]},
    "facts": [
        {"subject": "user", "predicate": "止损", "object": "5", "fact_type": "constraint",
         "confidence": 0.9, "time_hint": ""},
        {"subject": "600519", "predicate": "关注理由", "object": "低估值", "fact_type": "preference",
         "confidence": 0.8, "time_hint": "去年"},
    ],
}


def _patch(monkeypatch, events, llm_content, saved=None):
    monkeypatch.setattr(memory_store, "list_events", lambda user_id, session_key: events)
    calls = {"episode": None, "facts": None, "lessons": None}

    def fake_save(**kwargs):
        calls["episode"] = kwargs
        return saved if saved is not None else {"version": 1}

    def fake_save_facts(user_id, session_key, facts, source_event_ids=None):
        calls["facts"] = {"user_id": user_id, "session_key": session_key,
                          "facts": facts, "source_event_ids": source_event_ids}
        return {"results": [{"id": 57, "action": "superseded", "supersededId": 41,
                             "subject": "user", "predicate": "stop_loss_pct", "object": "5"}]}

    def fake_save_lessons(user_id, session_key, lessons, source_event_ids=None):
        calls["lessons"] = {"user_id": user_id, "session_key": session_key,
                            "lessons": lessons, "source_event_ids": source_event_ids}
        return {"results": [{"id": 31, "action": "created", "occurrences": 1,
                             "taskType": "backtest", "symptom": "回测提示历史数据不足"}]}

    monkeypatch.setattr(memory_store, "save_episode", fake_save)
    monkeypatch.setattr(memory_store, "save_facts", fake_save_facts)
    monkeypatch.setattr(memory_store, "save_lessons", fake_save_lessons)

    def fake_completion(messages, tools=None, temperature=0.2, max_tokens=1200):
        calls["messages"] = messages
        return {"message": {"role": "assistant", "content": llm_content}}

    import llm_service
    monkeypatch.setattr(llm_service, "chat_completion", fake_completion)
    return calls


def test_too_few_events_is_skipped(monkeypatch):
    calls = _patch(monkeypatch, EVENTS[:1], "should not be called")

    result = consolidate.consolidate_session(1, "1:2026-09-14")

    assert "skipped" in result
    assert calls["episode"] is None


def test_valid_summary_is_persisted_with_provenance(monkeypatch):
    calls = _patch(monkeypatch, EVENTS, json.dumps(VALID_SUMMARY, ensure_ascii=False))

    result = consolidate.consolidate_session(1, "1:2026-09-14")

    assert result["version"] == 1
    episode = calls["episode"]
    assert episode["summary"].startswith("用户在调试均线策略")
    assert episode["key_points"] == ["20日上穿60日", "关注回撤"]
    assert episode["open_questions"] == ["是否改用周线"]
    # 摘要必须可追溯到账本原始事件，否则没法回查"这句话是谁什么时候说的"
    assert episode["source_event_ids"] == [11, 12, 13]
    assert episode["started_at"] == "2026-09-14T10:00:00"
    assert episode["ended_at"] == "2026-09-14T10:05:00"


def test_summary_inside_code_fence_is_accepted(monkeypatch):
    content = "```json\n" + json.dumps(VALID_SUMMARY, ensure_ascii=False) + "\n```"
    calls = _patch(monkeypatch, EVENTS, content)

    result = consolidate.consolidate_session(1, "1:2026-09-14")

    assert result["version"] == 1
    assert calls["episode"] is not None


def test_invalid_json_is_not_persisted(monkeypatch):
    calls = _patch(monkeypatch, EVENTS, "好的，我总结如下：{这不是 JSON}")

    result = consolidate.consolidate_session(1, "1:2026-09-14")

    assert "error" in result
    assert calls["episode"] is None


def test_empty_summary_is_not_persisted(monkeypatch):
    calls = _patch(monkeypatch, EVENTS, json.dumps({"summary": "  ", "key_points": []}))

    result = consolidate.consolidate_session(1, "1:2026-09-14")

    assert "error" in result
    assert calls["episode"] is None


def test_llm_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(memory_store, "list_events", lambda user_id, session_key: EVENTS)
    monkeypatch.setattr(memory_store, "save_episode", lambda **kwargs: {"version": 1})

    import llm_service

    def boom(messages, tools=None, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(llm_service, "chat_completion", boom)

    result = consolidate.consolidate_session(1, "1:2026-09-14")

    assert result == {"error": "llm unavailable"}


def test_persist_failure_is_reported(monkeypatch):
    _patch(monkeypatch, EVENTS, json.dumps(VALID_SUMMARY, ensure_ascii=False), saved={})

    result = consolidate.consolidate_session(1, "1:2026-09-14")

    assert result == {"error": "failed to persist episode"}


def test_missing_identity_is_skipped(monkeypatch):
    calls = _patch(monkeypatch, EVENTS, json.dumps(VALID_SUMMARY, ensure_ascii=False))

    assert "skipped" in consolidate.consolidate_session(None, "1:2026-09-14")
    assert "skipped" in consolidate.consolidate_session(1, "")
    assert calls["episode"] is None


def test_prompt_forbids_guessing_and_requires_absolute_dates():
    prompt = consolidate.SUMMARY_SYSTEM_PROMPT

    assert "确实出现过" in prompt
    assert "open_questions" in prompt
    assert "绝对日期" in prompt
    # 行情数据不该进长期记忆：会变、且随时可重查
    assert "不要" in prompt and "行情数据" in prompt


def test_facts_are_extracted_in_the_same_llm_call(monkeypatch):
    """事实和摘要在同一次 LLM 调用里产出，不额外花钱。"""
    calls = _patch(monkeypatch, EVENTS, json.dumps(VALID_SUMMARY, ensure_ascii=False))

    result = consolidate.consolidate_session(1, "1:2026-09-14")

    assert result["version"] == 1
    saved = calls["facts"]
    assert saved["user_id"] == 1
    assert saved["session_key"] == "1:2026-09-14"
    assert saved["source_event_ids"] == [11, 12, 13]
    # "止损" 必须被收敛成规范键，否则取代链会断
    assert saved["facts"][0]["predicate"] == "stop_loss_pct"
    # "去年" 必须被解析成绝对时间
    assert saved["facts"][1]["predicate"] == "watch_reason"
    assert saved["facts"][1]["event_time"].startswith("2025-01-01")
    assert saved["facts"][1]["raw_time_phrase"] == "去年"


def test_supersede_action_is_reported_back(monkeypatch):
    _patch(monkeypatch, EVENTS, json.dumps(VALID_SUMMARY, ensure_ascii=False))

    result = consolidate.consolidate_session(1, "1:2026-09-14")

    assert result["facts"][0]["action"] == "superseded"
    assert result["facts"][0]["supersededId"] == 41


def test_no_facts_means_no_fact_write(monkeypatch):
    payload = dict(VALID_SUMMARY)
    payload["facts"] = []
    calls = _patch(monkeypatch, EVENTS, json.dumps(payload, ensure_ascii=False))

    consolidate.consolidate_session(1, "1:2026-09-14")

    assert calls["facts"] is None


def test_fact_write_failure_does_not_lose_the_summary(monkeypatch):
    """摘要已经落库了，事实写失败不能把它回滚 —— 记忆是增强功能，要尽力而为。"""
    calls = _patch(monkeypatch, EVENTS, json.dumps(VALID_SUMMARY, ensure_ascii=False))

    def boom(*args, **kwargs):
        raise RuntimeError("java down")

    monkeypatch.setattr(memory_store, "save_facts", boom)

    result = consolidate.consolidate_session(1, "1:2026-09-14")

    assert result["version"] == 1
    assert result["facts"] == []
    assert calls["episode"] is not None


def test_low_confidence_facts_are_dropped_before_write(monkeypatch):
    payload = dict(VALID_SUMMARY)
    payload["facts"] = [
        {"subject": "user", "predicate": "止损", "object": "5", "confidence": 0.05},
        {"subject": "user", "predicate": "风险偏好", "object": "稳健", "confidence": 0.9},
    ]
    calls = _patch(monkeypatch, EVENTS, json.dumps(payload, ensure_ascii=False))

    consolidate.consolidate_session(1, "1:2026-09-14")

    assert len(calls["facts"]["facts"]) == 1
    assert calls["facts"]["facts"][0]["predicate"] == "risk_preference"


# ==================== 经验（M2） ====================

LESSON_PAYLOAD = {
    "task_type": "回测",
    "symptom": "回测提示历史数据不足",
    "attempts": ["原样重跑"],
    "resolution": "先取更长周期的数据",
    "reusable_rule": "回测前确认历史条数≥20",
    "confidence": 0.7,
}


def test_lessons_are_extracted_in_the_same_llm_call(monkeypatch):
    payload = dict(VALID_SUMMARY)
    payload["lessons"] = [LESSON_PAYLOAD]
    calls = _patch(monkeypatch, EVENTS, json.dumps(payload, ensure_ascii=False))

    result = consolidate.consolidate_session(1, "1:2026-09-14")

    saved = calls["lessons"]
    assert saved["user_id"] == 1
    assert saved["source_event_ids"] == [11, 12, 13]
    # 中文 task_type 必须被收敛，"回测" 与 "backtest" 不能算两个键
    assert saved["lessons"][0]["task_type"] == "backtest"
    assert result["lessons"][0]["id"] == 31


def test_lesson_without_resolution_is_not_written(monkeypatch):
    payload = dict(VALID_SUMMARY)
    payload["lessons"] = [{"task_type": "回测", "symptom": "很慢"}]   # 只说问题、没说怎么解决
    calls = _patch(monkeypatch, EVENTS, json.dumps(payload, ensure_ascii=False))

    consolidate.consolidate_session(1, "1:2026-09-14")

    assert calls["lessons"] is None


def test_no_lessons_means_no_lesson_write(monkeypatch):
    payload = dict(VALID_SUMMARY)
    payload["lessons"] = []
    calls = _patch(monkeypatch, EVENTS, json.dumps(payload, ensure_ascii=False))

    consolidate.consolidate_session(1, "1:2026-09-14")

    assert calls["lessons"] is None


def test_lesson_write_failure_does_not_lose_the_summary(monkeypatch):
    payload = dict(VALID_SUMMARY)
    payload["lessons"] = [LESSON_PAYLOAD]
    calls = _patch(monkeypatch, EVENTS, json.dumps(payload, ensure_ascii=False))

    def boom(*args, **kwargs):
        raise RuntimeError("java down")

    monkeypatch.setattr(memory_store, "save_lessons", boom)

    result = consolidate.consolidate_session(1, "1:2026-09-14")

    assert result["version"] == 1
    assert result["lessons"] == []
    assert calls["episode"] is not None


def test_consolidation_invalidates_the_semantic_index(monkeypatch):
    """语料变了，语义索引必须失效，否则用户刚改的口令下一轮还用旧向量召回。"""
    invalidated = {"user": None}
    payload = dict(VALID_SUMMARY)
    payload["lessons"] = []
    _patch(monkeypatch, EVENTS, json.dumps(payload, ensure_ascii=False))

    from agent import vector_index
    monkeypatch.setattr(vector_index, "invalidate", lambda user_id=None: invalidated.update(user=user_id))

    consolidate.consolidate_session(7, "7:2026-09-14")

    assert invalidated["user"] == 7


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
