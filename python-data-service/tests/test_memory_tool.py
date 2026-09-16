# -*- coding: utf-8 -*-
"""memory_search 工具：只读检索用户自己的长期记忆。

两条必须守住的安全边界：
1. **身份来自服务端上下文，不接受模型传入的 user_id** —— 否则模型可以查别人的记忆；
2. **检索结果只是参考**，工具本身不做任何写操作（写入走离线巩固流水线）。
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent.react_agent as ra
import agent.tool_registry as registry
from agent import memory_store, tool_contract as tc, tool_scope


def _call(name, args):
    return registry.execute_tool(name, args)


def test_schema_and_timeout_are_registered():
    names = {t["function"]["name"] for t in registry.TOOL_SCHEMAS}
    assert "memory_search" in names
    assert "memory_search" in registry.TOOL_TIMEOUTS


def test_refuses_to_search_without_a_session_identity(monkeypatch):
    """没有身份就拒绝：绝不能靠模型自己填 user_id。"""
    called = {"n": 0}
    monkeypatch.setattr(memory_store, "search_facts",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or [])

    result = _call("memory_search", {"query": "止损"})

    assert tc.is_envelope(result)
    assert result["ok"] is False
    assert result["error"]["code"] == tc.INVALID_ARGS
    assert called["n"] == 0


def test_searches_only_the_current_user(monkeypatch):
    captured = {}

    def fake_search(user_id, query=None, symbol=None, fact_type=None, limit=8):
        captured.update(user_id=user_id, query=query, symbol=symbol, fact_type=fact_type, limit=limit)
        return [{"id": 57, "subject": "user", "predicate": "stop_loss_pct", "object": "5",
                 "previousValue": "-8", "recordedAt": "2026-09-16T10:00:00"}]

    monkeypatch.setattr(memory_store, "search_facts", fake_search)
    token = tool_scope.set_scope(user_id=7, session_key="7:2026-09-16")
    try:
        result = _call("memory_search", {"query": "止损", "limit": 5})
    finally:
        tool_scope.reset(token)

    assert captured["user_id"] == 7
    assert captured["query"] == "止损"
    assert captured["limit"] == 5
    assert result["ok"] is True
    assert result["data"]["count"] == 1
    assert result["data"]["facts"][0]["previousValue"] == "-8"


def test_empty_result_tells_the_model_not_to_make_things_up(monkeypatch):
    monkeypatch.setattr(memory_store, "search_facts", lambda *a, **k: [])
    token = tool_scope.set_scope(user_id=7, session_key="7:2026-09-16")
    try:
        result = _call("memory_search", {"query": "从没说过的事"})
    finally:
        tool_scope.reset(token)

    assert result["ok"] is True
    assert result["data"]["facts"] == []
    assert "不要编造" in result["data"]["note"]


def test_missing_query_is_a_structured_error():
    token = tool_scope.set_scope(user_id=7, session_key="7:2026-09-16")
    try:
        result = _call("memory_search", {})
    finally:
        tool_scope.reset(token)

    assert result["ok"] is False
    assert result["error"]["code"] == tc.INVALID_ARGS


def test_backend_failure_becomes_an_envelope(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("java down")

    monkeypatch.setattr(memory_store, "search_facts", boom)
    token = tool_scope.set_scope(user_id=7, session_key="7:2026-09-16")
    try:
        result = _call("memory_search", {"query": "止损"})
    finally:
        tool_scope.reset(token)

    assert tc.is_envelope(result)
    assert result["ok"] is False
    assert result["error"]["code"] == tc.TOOL_ERROR


def test_identity_scope_is_isolated_between_concurrent_requests(monkeypatch):
    """并发处理两个用户时，A 的工具绝不能读到 B 的身份。

    contextvars 天生按线程隔离，但工具是在线程池里跑的，
    所以 react_agent 里显式做了 copy_context —— 这条测试就是钉住那一步。
    """
    monkeypatch.setattr(memory_store, "search_facts",
                        lambda user_id, **kwargs: [{"id": 1, "subject": "user", "object": str(user_id)}])

    pool = ra._BoundedToolPool(max_workers=2, capacity=4)
    results = {}

    def worker(user_id):
        token = tool_scope.set_scope(user_id=user_id, session_key=f"{user_id}:2026-09-16")
        try:
            envelope = pool.submit(registry.execute_tool, ("memory_search", {"query": "止损"}), 5)
            results[user_id] = envelope["data"]["facts"][0]["object"]
        finally:
            tool_scope.reset(token)

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(worker, [11, 22]))

    # 每个 worker 看到的都是自己的 user_id —— 说明 copy_context 那一步是有效的
    assert results == {11: "11", 22: "22"}


def test_scope_is_empty_outside_a_request():
    assert tool_scope.current() == {}
    assert tool_scope.user_id() is None
    assert tool_scope.session_key() is None


def test_tool_schemas_description_mentions_when_to_use_it():
    schema = next(t for t in registry.TOOL_SCHEMAS if t["function"]["name"] == "memory_search")
    description = schema["function"]["description"]
    assert "long-term memory" in description
    assert "previous value" in description
