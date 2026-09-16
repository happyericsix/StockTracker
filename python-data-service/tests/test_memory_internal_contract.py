# -*- coding: utf-8 -*-
"""钉住 Python → Java 记忆内部接口的契约。

为什么要有这个文件：这个项目里最贵的一类故障就是"两边字段名对不上 → 静默失效"——
agent 照常回复、日志没有报错，只是记忆/上下文悄悄没了（user_id vs user_name 那次）。
方向反过来也一样：Python 把 userId 写成 user_id、把 sessionKey 写成 session_key，
Java 侧收不到参数就当成"没有身份"，表现同样是"每轮都失忆"。

所以这里断言的是**请求的形状**：路径、查询参数名、请求头、请求体的键名与格式。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import memory_store


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _capture(monkeypatch, payload):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured.update(method="GET", url=url, params=params, headers=headers, timeout=timeout)
        return _FakeResponse(payload)

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(method="POST", url=url, body=json, headers=headers, timeout=timeout)
        return _FakeResponse(payload)

    monkeypatch.setattr(memory_store.requests, "get", fake_get)
    monkeypatch.setattr(memory_store.requests, "post", fake_post)
    return captured


def test_context_request_shape(monkeypatch):
    captured = _capture(monkeypatch, {"today": "2026-09-16", "recaps": []})
    monkeypatch.setenv("INTERNAL_API_TOKEN", "tok")
    monkeypatch.setenv("JAVA_BASE_URL", "http://java:8080")

    memory_store.load_context(7, "7:2026-09-16")

    assert captured["url"] == "http://java:8080/api/v1/internal/memory/context"
    # Java 侧读的是 @RequestParam Long userId / String sessionKey，参数名错一个就等于没传
    assert captured["params"] == {"userId": 7, "sessionKey": "7:2026-09-16"}
    assert captured["headers"]["X-Internal-Token"] == "tok"


def test_events_request_shape(monkeypatch):
    captured = _capture(monkeypatch, {"events": [{"id": 1, "kind": "chat_user"}]})

    events = memory_store.list_events(7, "7:2026-09-16")

    assert captured["url"].endswith("/api/v1/internal/memory/events")
    assert captured["params"] == {"userId": 7, "sessionKey": "7:2026-09-16"}
    assert events == [{"id": 1, "kind": "chat_user"}]


def test_events_response_tolerates_unexpected_shape(monkeypatch):
    """Java 返回了意料之外的结构时，调用方拿到的必须是空列表而不是崩溃。"""
    _capture(monkeypatch, {"events": "oops"})

    assert memory_store.list_events(7, "7:2026-09-16") == []


def test_episode_body_shape_and_iso_timestamps(monkeypatch):
    captured = _capture(monkeypatch, {"sessionKey": "7:2026-09-15", "version": 2})

    result = memory_store.save_episode(
        user_id=7,
        session_key="7:2026-09-15",
        summary="用户在调试均线策略",
        key_points=["关注回撤"],
        open_questions=["是否改用周线"],
        entities={"symbols": ["600519"]},
        source_event_ids=[11, 12],
        model="deepseek-chat",
        started_at="2026-09-15T10:00:00",
        ended_at="2026-09-15T10:05:00",
    )

    assert captured["url"].endswith("/api/v1/internal/memory/episodes")
    body = captured["body"]
    # 这些键名对应 MemoryEpisodeRequest 的字段：写错任何一个，摘要就静默丢失
    assert body["userId"] == 7
    assert body["sessionKey"] == "7:2026-09-15"
    assert body["summary"].startswith("用户在调试")
    assert body["keyPoints"] == ["关注回撤"]
    assert body["openQuestions"] == ["是否改用周线"]
    assert body["entities"] == {"symbols": ["600519"]}
    assert body["sourceEventIds"] == [11, 12]
    assert body["model"] == "deepseek-chat"
    # 必须是 ISO 格式：Java 侧是 LocalDateTime，给 "2026-09-15 10:00" 会反序列化失败
    assert body["startedAt"] == "2026-09-15T10:00:00"
    assert body["endedAt"] == "2026-09-15T10:05:00"
    assert result == {"sessionKey": "7:2026-09-15", "version": 2}


def test_append_event_body_shape(monkeypatch):
    captured = _capture(monkeypatch, {"id": 99})

    memory_store.append_event(7, "7:2026-09-16", "chat_user", "user", "你好",
                              symbol="600519", provenance="user", trust="high")

    assert captured["url"].endswith("/api/v1/internal/memory/events")
    body = captured["body"]
    assert body == {
        "userId": 7,
        "sessionKey": "7:2026-09-16",
        "kind": "chat_user",
        "role": "user",
        "content": "你好",
        "symbol": "600519",
        "provenance": "user",
        "trust": "high",
    }


def test_facts_search_request_shape(monkeypatch):
    captured = _capture(monkeypatch, {"facts": [{"predicate": "stop_loss_pct", "object": "5"}]})

    found = memory_store.search_facts(7, query="止损", symbol="600519", fact_type="constraint", limit=5)

    assert captured["url"].endswith("/api/v1/internal/memory/facts")
    assert captured["params"] == {"userId": 7, "limit": 5, "query": "止损",
                                  "symbol": "600519", "factType": "constraint"}
    assert found[0]["predicate"] == "stop_loss_pct"


def test_save_facts_body_uses_camel_case_keys(monkeypatch):
    """事实键名必须与 Java 的 MemoryFactRequest 对齐。

    这里是真实踩过的坑：Python 内部事实是 snake_case（fact_type / event_time），
    直接发出去 Jackson 映射不到，于是"类型"静默降级成 observation、
    "时间"静默丢掉 —— 而时间是这套系统的核心。所以映射层 + 契约测试缺一不可。
    """
    captured = _capture(monkeypatch, {"results": [{"id": 57, "action": "superseded"}]})

    memory_store.save_facts(7, "7:2026-09-16", [{
        "subject": "user",
        "predicate": "stop_loss_pct",
        "object": "5",
        "fact_type": "constraint",
        "confidence": 0.9,
        "event_time": "2025-01-01T00:00:00",
        "raw_time_phrase": "去年",
        "data_as_of": "2025-03-11T00:00:00",
        "provenance": "model",
        "trust": "medium",
        "confirmed": False,
    }], source_event_ids=[11, 12])

    assert captured["url"].endswith("/api/v1/internal/memory/facts")
    body = captured["body"]
    assert body["userId"] == 7
    assert body["sessionKey"] == "7:2026-09-16"
    assert body["sourceEventIds"] == [11, 12]

    fact = body["facts"][0]
    # 必须是 camelCase，且一个都不能少
    assert fact["factType"] == "constraint"
    assert fact["eventTime"] == "2025-01-01T00:00:00"
    assert fact["rawTimePhrase"] == "去年"
    assert fact["dataAsOf"] == "2025-03-11T00:00:00"
    assert fact["object"] == "5"
    assert fact["confirmed"] is False
    assert set(fact) == {"subject", "predicate", "object", "factType", "confidence",
                         "eventTime", "rawTimePhrase", "dataAsOf", "provenance",
                         "trust", "confirmed"}


def test_save_facts_without_identity_makes_no_request(monkeypatch):
    captured = _capture(monkeypatch, {})

    assert memory_store.save_facts(None, "7:2026-09-16", [{"subject": "user"}]) == {}
    assert memory_store.save_facts(7, "7:2026-09-16", []) == {}
    assert "url" not in captured


def test_context_request_carries_the_current_topic(monkeypatch):
    """带上 query/symbol，Java 才能挑出"相关"事实而不是"最近"事实。"""
    captured = _capture(monkeypatch, {"today": "2026-09-16", "facts": []})

    memory_store.load_context(7, "7:2026-09-16", query="把止损改成5%", symbol="600519")

    assert captured["params"] == {"userId": 7, "sessionKey": "7:2026-09-16",
                                 "query": "把止损改成5%", "symbol": "600519"}


def test_no_identity_means_no_request(monkeypatch):
    captured = _capture(monkeypatch, {})

    assert memory_store.load_context(None, "7:2026-09-16") == {}
    assert memory_store.load_context(7, "") == {}
    assert "url" not in captured


# ==================== M2 新增端点 ====================

def test_index_corpus_request_shape(monkeypatch):
    captured = _capture(monkeypatch, {"facts": [], "episodes": [], "lessons": []})

    memory_store.index_corpus(7)

    assert captured["url"].endswith("/api/v1/internal/memory/index")
    assert captured["params"] == {"userId": 7}


def test_lessons_request_shape(monkeypatch):
    captured = _capture(monkeypatch, {"lessons": [{"id": 31, "symptom": "数据不足"}]})

    found = memory_store.search_lessons(7, query="回测失败", task_type="backtest", limit=3)

    assert captured["url"].endswith("/api/v1/internal/memory/lessons")
    assert captured["method"] == "GET"
    assert captured["params"] == {"userId": 7, "limit": 3, "query": "回测失败", "taskType": "backtest"}
    assert found[0]["id"] == 31


def test_save_lessons_body_uses_camel_case_keys(monkeypatch):
    """经验字段同样踩过 snake_case / camelCase 的坑：task_type 丢了就归到错误的键上。"""
    captured = _capture(monkeypatch, {"results": [{"id": 31, "action": "created"}]})

    memory_store.save_lessons(7, "7:2026-09-16", [{
        "task_type": "backtest",
        "symptom": "回测提示历史数据不足",
        "attempts": ["原样重跑"],
        "resolution": "先取更长周期的数据",
        "reusable_rule": "回测前确认条数≥20",
        "confidence": 0.7,
        "provenance": "model",
        "trust": "medium",
    }], evidence_event_ids=[11, 12])

    assert captured["url"].endswith("/api/v1/internal/memory/lessons")
    body = captured["body"]
    assert body["userId"] == 7
    assert body["evidenceEventIds"] == [11, 12]
    lesson = body["lessons"][0]
    assert set(lesson) == {"taskType", "symptom", "context", "attempts", "resolution",
                           "reusableRule", "confidence", "provenance", "trust"}
    assert lesson["taskType"] == "backtest"
    assert lesson["reusableRule"] == "回测前确认条数≥20"


def test_batch_event_append_shape(monkeypatch):
    captured = _capture(monkeypatch, {"saved": 2})

    memory_store.append_events(7, "7:2026-09-16", [
        {"kind": "tool_result", "role": "tool", "content": "backtest_strategy 失败：数据不足",
         "provenance": "tool", "trust": "low"},
    ])

    assert captured["url"].endswith("/api/v1/internal/memory/events/batch")
    body = captured["body"]
    assert body["userId"] == 7
    assert body["sessionKey"] == "7:2026-09-16"
    assert body["events"][0]["kind"] == "tool_result"
    assert body["events"][0]["trust"] == "low"


def test_retract_fact_shape(monkeypatch):
    captured = _capture(monkeypatch, {"retracted": 41})

    memory_store.retract_fact(7, 41)

    assert "/api/v1/internal/memory/facts/41/retract" in captured["url"]
    assert "userId=7" in captured["url"]


def test_m2_calls_are_skipped_without_identity(monkeypatch):
    captured = _capture(monkeypatch, {})

    assert memory_store.index_corpus(None) == {}
    assert memory_store.search_lessons(None) == []
    assert memory_store.save_lessons(7, "s", []) == {}
    assert memory_store.append_events(7, "s", []) == {}
    assert memory_store.retract_fact(7, None) == {}
    assert "url" not in captured


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
