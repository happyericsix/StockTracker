# -*- coding: utf-8 -*-
"""角色隔离（W2）：子角色的过程不得污染用户的前情提要。

<h3>这个文件在防什么</h3>
账本、用量事件、工具日志都是**记在用户身上**的（它们由 user_id + session_key 定位）。
在没有 role 这个维度之前，"子 agent 干过什么"和"用户说过什么"在账本里长得一模一样：
都是一条 `kind=tool_result` 的事件。而前情提要被拿去抽事实与经验、**长期保存** ——
于是子角色的中间步骤会被当成用户对话内容二次提炼，写进长期记忆。

这个文件钉住三件事：

1. 角色能从调用方一路传到**工具执行点**（不是只传到一个没人读的字段里）；
2. 入账时角色被记下来（可审计、可按角色算成本），且**主 agent 的条目一字不变**（零行为变化）；
3. 巩固时按角色过滤：子角色的事件不进用户摘要，但**主 agent 的工具失败信息必须保留**
   —— 那是经验抽取（"这类问题上次怎么解决的"）唯一的原料，
   一刀切排除 `tool_result` 会把记忆系统的价值砍掉一半。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent.react_agent as ra
from agent import consolidate
from agent import tool_contract as tc
from agent import tool_scope


# ==================== 1. 身份上下文 ====================


def test_tool_context_carries_role():
    token = tool_scope.begin(user_id="1", session_key="1:2026-09-17", role="strategy_critic")
    try:
        assert tool_scope.role() == "strategy_critic"
        assert tool_scope.current()["role"] == "strategy_critic"
    finally:
        tool_scope.reset(token)


def test_main_agent_has_no_role_at_all():
    """role 缺省时上下文与改造前完全一致：不出现 role 键，而不是出现一个 None 键。

    这很重要：`current()` 会被塞进审计与日志，多余的 None 字段会让"主 agent"
    与"子角色"在输出里看起来只差一个值，而不是差一个能力边界。
    """
    token = tool_scope.begin(user_id="1", session_key="1:2026-09-17")
    try:
        assert tool_scope.role() is None
        assert "role" not in tool_scope.current()
    finally:
        tool_scope.reset(token)


def test_blank_role_is_treated_as_main_agent():
    """空字符串/空格不能变成"一个叫 '' 的角色"——那会把主 agent 误判成子角色。"""
    token = tool_scope.begin(user_id="1", session_key="s", role="   ")
    try:
        assert tool_scope.role() is None
        assert "role" not in tool_scope.current()
    finally:
        tool_scope.reset(token)


def test_reset_restores_the_previous_context():
    outer = tool_scope.begin(user_id="1", session_key="s")
    inner = tool_scope.begin(user_id="1", session_key="s", role="paper_coach")
    try:
        assert tool_scope.role() == "paper_coach"
    finally:
        tool_scope.reset(inner)
    try:
        assert tool_scope.role() is None
    finally:
        tool_scope.reset(outer)


# ==================== 2. 角色必须真的传到工具执行点 ====================


def _tool_call(name, args, call_id="call_1"):
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            }],
        }
    }


def test_role_reaches_the_tool_inside_the_loop(monkeypatch):
    """端到端：`run_agent(role=...)` 之后，工具 handler 里读到的 role 就是它。

    只断言"参数被存进了某个字段"是不够的 —— 这个项目里出过太多次
    "字段传了但没人读"（ToolSpec.redaction 就曾是装饰品）。
    """
    seen = {}

    def fake_execute_tool(name, args):
        seen["role"] = tool_scope.role()
        return tc.ok({"price": 1500.0})

    calls = {"n": 0}

    def fake_completion(messages, tools=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _tool_call("get_quote", {"symbol": "600519"})
        return {"message": {"role": "assistant", "content": "600519 现价 1500 元"}}

    monkeypatch.setattr(ra, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(ra.llm_service, "chat_completion", fake_completion)
    monkeypatch.setattr(ra, "_queue_tool_log", lambda *a, **k: None)
    monkeypatch.setattr(ra, "_queue_usage", lambda *a, **k: None)
    monkeypatch.setattr(ra.recall, "load_context", lambda *a, **k: {})

    out = ra.run_agent("u1", "600519 多少钱", role="strategy_critic")

    assert seen["role"] == "strategy_critic"
    assert out["replies"]


def test_run_agent_without_role_keeps_tools_unbranded(monkeypatch):
    """不带 role 时，工具里读到的仍是 None：守卫上一条测试，防止默认值被写死。"""
    seen = {}

    def fake_execute_tool(name, args):
        seen["role"] = tool_scope.role()
        return tc.ok({"price": 1500.0})

    calls = {"n": 0}

    def fake_completion(messages, tools=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _tool_call("get_quote", {"symbol": "600519"})
        return {"message": {"role": "assistant", "content": "现价 1500 元"}}

    monkeypatch.setattr(ra, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(ra.llm_service, "chat_completion", fake_completion)
    monkeypatch.setattr(ra, "_queue_tool_log", lambda *a, **k: None)
    monkeypatch.setattr(ra, "_queue_usage", lambda *a, **k: None)
    monkeypatch.setattr(ra.recall, "load_context", lambda *a, **k: {})

    ra.run_agent("u1", "600519 多少钱")

    assert seen["role"] is None


# ==================== 3. 入账：按角色可查，且主 agent 零变化 ====================


def test_tool_log_entry_tags_subagent_only():
    tagged = ra._tool_log_entry("get_quote", tc.ok({"price": 1500.0}),
                               {"symbol": "600519"}, role="strategy_critic")
    assert json.loads(tagged["meta"])["role"] == "strategy_critic"
    # 子角色的可信度口径不变：工具输出永远不是"用户说的话"
    assert tagged["trust"] == "low"

    plain = ra._tool_log_entry("get_quote", tc.ok({"price": 1500.0}), {"symbol": "600519"})
    assert "role" not in json.loads(plain["meta"])


def test_usage_event_carries_role(monkeypatch):
    """用量事件带 role：多角色之后的成本必须能按角色归因，否则算不出"谁值这个钱"。"""
    captured = {}

    monkeypatch.setattr(ra._TOOL_LOG_EXECUTOR, "submit", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(ra, "_write_tool_log",
                        lambda uid, sk, entries: captured.update(entries=entries))

    ra._queue_usage(7, "7:2026-09-17", ra.metering.baseline(),
                    ra.metering.turn_started_at(), role="paper_coach")

    meta = json.loads(captured["entries"][0]["meta"])
    assert meta["role"] == "paper_coach"
    assert "delta" in meta and "duration_ms" in meta


def test_usage_event_without_role_keeps_the_old_shape(monkeypatch):
    captured = {}
    monkeypatch.setattr(ra._TOOL_LOG_EXECUTOR, "submit", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(ra, "_write_tool_log",
                        lambda uid, sk, entries: captured.update(entries=entries))

    ra._queue_usage(7, "7:2026-09-17", ra.metering.baseline(), ra.metering.turn_started_at())

    meta = json.loads(captured["entries"][0]["meta"])
    assert set(meta) == {"delta", "duration_ms"}


# ==================== 4. 巩固：子角色的过程不进用户前情提要 ====================


def _event(event_id, kind, content, provenance="tool", meta=None):
    event = {"id": event_id, "kind": kind, "content": content, "provenance": provenance}
    if meta is not None:
        event["meta"] = meta if isinstance(meta, str) else json.dumps(meta)
    return event


def test_subagent_events_are_kept_out_of_the_recap():
    events = [
        _event(1, "chat_user", "帮我把茅台的策略止损改成 5%", provenance="user", meta=None),
        _event(2, "tool_result", "get_quote -> 1 条", meta={"tool": "get_quote", "ok": True}),
        _event(3, "tool_result", "get_history -> 250 条",
               meta={"tool": "get_history", "ok": True, "role": "strategy_critic"}),
        _event(4, "usage", "本轮用量：LLM 2 次", provenance="system",
               meta={"delta": {}, "role": "strategy_critic"}),
    ]

    dialog, used_ids, _ = consolidate._build_dialog(events)

    assert "止损改成 5%" in dialog
    # 主 agent 的工具结果保留：它是经验抽取（symptom → resolution）唯一的原料
    assert "get_quote" in dialog
    # 子角色的过程不进用户前情提要
    assert "get_history" not in dialog
    assert 3 not in used_ids
    assert 4 not in used_ids
    assert 1 in used_ids and 2 in used_ids


def test_main_agent_tool_result_is_still_lesson_material():
    """反向守卫：一刀切排除 tool_result 会让"上次是怎么解决的"失去依据。"""
    events = [_event(1, "tool_result", "backtest_strategy 失败：历史数据不足")]

    dialog, used_ids, _ = consolidate._build_dialog(events)

    assert "历史数据不足" in dialog
    assert used_ids == [1]


def test_unparseable_meta_is_treated_as_main_agent():
    """脏数据不能反过来把摘要整段丢掉：解析不了就按主 agent 处理。"""
    events = [_event(1, "tool_result", "get_quote -> 1 条", meta="{这不是 JSON")]

    dialog, used_ids, _ = consolidate._build_dialog(events)

    assert used_ids == [1]


def test_meta_can_already_be_a_dict():
    """Java 反序列化后 meta 可能是对象；两种形态都要认。"""
    events = [
        _event(1, "tool_result", "get_quote -> 1 条", meta={"role": "paper_coach"}),
        _event(2, "tool_result", "get_quote -> 1 条", meta={"role": ""}),
    ]

    _, used_ids, _ = consolidate._build_dialog(events)

    assert used_ids == [2]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print("PASS", fn.__name__)
