# -*- coding: utf-8 -*-
"""钉住 /api/v1/agent/chat 的请求契约。

为什么需要这个测试：
    这个端点被 Java 的 ChatService.callLlm 调用。它一度发的是 {"user_name": ...}，
    而本端读的是 data["user_id"] → 取到空串 → 直接返回 {"replies": []}，
    **LLM 完全没被调用**；Java 收到空数组后用"没理解你的问题"兜底，
    前端看起来就是"机器人坏了"，但两侧日志都没有报错。

    之所以一直没被发现：Python 侧的 test_react_agent.py 直接调 run_agent()
    绕过了 HTTP 层，Java 侧的 ChatServiceTest mock 掉了 callLlm，
    于是**没有任何测试覆盖这个 HTTP 契约**。

本测试直接打这个 HTTP 端点，因此任何一侧改了字段名都会在这里红。

同样的道理适用于 history：它是多轮对话的唯一来源（Java 侧从 message 表带过来），
一旦这个字段在链路上被吞掉，表现就是"agent 每轮都失忆"，而且不会报任何错。
"""
import io
import sys
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402
import agent.react_agent as react_agent_module  # noqa: E402

client = TestClient(app_module.app)

# 服务间鉴权：与服务自身读到的令牌保持一致（.env 里配了就必须带）
TOKEN = getattr(app_module, "INTERNAL_API_TOKEN", "") or ""
HEADERS = {"X-Internal-Token": TOKEN} if TOKEN else {}

FAKE_RESULT = {"replies": ["这是被 mock 的模型回复"], "strategy_json": None}


def _post(payload):
    return client.post("/api/v1/agent/chat", json=payload, headers=HEADERS)


def test_user_id_field_reaches_the_agent():
    """正式契约：user_id 必须能让请求走到 agent（而不是被空值守卫拦掉）。"""
    with patch("agent.react_agent.run_agent", return_value=FAKE_RESULT) as mocked:
        r = _post({"user_id": "u1", "message": "贵州茅台现价多少？"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["replies"] == FAKE_RESULT["replies"], (
        "replies 为空说明请求被空值守卫拦掉了 —— 字段名契约被改坏了"
    )
    # 关键：agent 真的被调用了（这正是历史故障里没发生的事）
    mocked.assert_called_once()
    assert mocked.call_args[0][0] == "u1"


def test_user_name_alias_still_works():
    """历史兼容：Java 曾经发 user_name，保留它以免旧客户端静默失效。"""
    with patch("agent.react_agent.run_agent", return_value=FAKE_RESULT) as mocked:
        r = _post({"user_name": "u1", "message": "贵州茅台现价多少？"})

    assert r.status_code == 200, r.text
    assert r.json()["replies"] == FAKE_RESULT["replies"]
    mocked.assert_called_once()


@pytest.mark.parametrize("payload", [
    {"message": "只有 message，没有任何用户标识"},
    {"user_id": "u1", "message": ""},
    {"user_id": "   ", "message": "空白用户标识"},
])
def test_missing_fields_short_circuit_without_calling_agent(payload):
    """缺字段时必须**快速返回空 replies 且不调用 agent**（这是设计行为）。

    这条测试同时是"故障特征"的说明：一旦字段名再次对不上，
    表现就是这里的行为 —— 空 replies、agent 未被调用。
    """
    with patch("agent.react_agent.run_agent", return_value=FAKE_RESULT) as mocked:
        r = _post(payload)

    assert r.status_code == 200, r.text
    assert r.json() == {"replies": [], "strategy_json": None}
    mocked.assert_not_called()


def test_history_and_session_id_are_forwarded_to_the_agent():
    """多轮上下文的契约：history / session_id 必须原样到达 agent。"""
    history = [
        {"role": "user", "content": "贵州茅台现在多少钱"},
        {"role": "assistant", "content": "约 1500 元，仅供参考"},
    ]
    with patch("agent.react_agent.run_agent", return_value=FAKE_RESULT) as mocked:
        r = _post({"user_id": "u1", "message": "那它呢", "session_id": "42", "history": history})

    assert r.status_code == 200, r.text
    assert r.json()["replies"] == FAKE_RESULT["replies"]
    mocked.assert_called_once()
    assert mocked.call_args.kwargs["history"] == history
    assert mocked.call_args.kwargs["session_id"] == "42"


@pytest.mark.parametrize("bad_history", ["not-a-list", {"role": "user"}, 42, None])
def test_malformed_history_is_coerced_to_empty_list(bad_history):
    """history 结构不合法时不能把请求打挂，退化成"无历史"即可。"""
    with patch("agent.react_agent.run_agent", return_value=FAKE_RESULT) as mocked:
        r = _post({"user_id": "u1", "message": "hi", "history": bad_history})

    assert r.status_code == 200, r.text
    mocked.assert_called_once()
    assert mocked.call_args.kwargs["history"] == []


def test_memory_user_id_and_session_key_are_forwarded():
    """记忆账本用数字 userId 定位；session_id 是会话键（userId:yyyy-MM-dd）。

    两者语义不同（user_id 是用户名），一旦 Java 侧少传 memory_user_id，
    表现是"agent 每轮都没有前情提要"——不报错、只是失忆，属于最难查的那类故障。
    """
    with patch("agent.react_agent.run_agent", return_value=FAKE_RESULT) as mocked:
        r = _post({"user_id": "alice", "message": "它呢",
                   "session_id": "9:2026-09-16", "memory_user_id": 9})

    assert r.status_code == 200, r.text
    mocked.assert_called_once()
    assert mocked.call_args.kwargs["session_id"] == "9:2026-09-16"
    assert mocked.call_args.kwargs["memory_user_id"] == 9


def test_memory_user_id_absent_degrades_to_none():
    """没有记忆标识时不能报错，只是没有前情提要。"""
    with patch("agent.react_agent.run_agent", return_value=FAKE_RESULT) as mocked:
        r = _post({"user_id": "alice", "message": "你好"})

    assert r.status_code == 200, r.text
    assert mocked.call_args.kwargs["memory_user_id"] is None


def test_role_is_forwarded_to_the_agent():
    """角色（W2）必须原样到达 agent。

    为什么值一条契约测试：role 决定"这段过程算不算用户的对话"（账本 → 前情提要）。
    一旦这个字段在链路上被吞掉，子角色（critic/coach）的工具日志就会被当成
    用户说过的话写进长期记忆 —— 不报错、只是记忆慢慢变脏，属于最难查的那类故障。
    """
    with patch("agent.react_agent.run_agent", return_value=FAKE_RESULT) as mocked:
        r = _post({"user_id": "u1", "message": "复审这个策略", "role": "strategy_critic"})

    assert r.status_code == 200, r.text
    assert mocked.call_args.kwargs["role"] == "strategy_critic"


@pytest.mark.parametrize("payload_role", [None, "", "   "])
def test_absent_or_blank_role_means_main_agent(payload_role):
    """缺省/空白角色 = 主 agent：不能变成一个叫 "" 的角色（那会把主 agent 误判成子角色）。"""
    payload = {"user_id": "u1", "message": "你好"}
    if payload_role is not None:
        payload["role"] = payload_role

    with patch("agent.react_agent.run_agent", return_value=FAKE_RESULT) as mocked:
        r = _post(payload)

    assert r.status_code == 200, r.text
    assert mocked.call_args.kwargs["role"] is None


def test_memory_lesson_focus_is_forwarded_to_the_agent():
    """反问功能（2026-10-16）：聚焦对象必须原样到达 agent。

    focus 在链路上被吞掉的表现是"反问变成普通聊天"——不报错，只是 agent
    根本不知道用户在质疑哪条经验，答非所问，属于最难查的那类静默失效。
    """
    focus = {"type": "memory_lesson", "lesson_id": 41}
    with patch("agent.react_agent.run_agent", return_value=FAKE_RESULT) as mocked:
        r = _post({"user_id": "u1", "message": "这条经验的依据是什么？", "focus": focus})

    assert r.status_code == 200, r.text
    mocked.assert_called_once()
    assert mocked.call_args.kwargs["focus"] == focus


@pytest.mark.parametrize("bad_focus", ["not-a-dict", 42, None, ["memory_lesson"]])
def test_malformed_focus_degrades_to_none(bad_focus):
    """focus 结构不合法时不能把请求打挂，退化成无聚焦的普通聊天。"""
    payload = {"user_id": "u1", "message": "hi"}
    if bad_focus is not None:
        payload["focus"] = bad_focus

    with patch("agent.react_agent.run_agent", return_value=FAKE_RESULT) as mocked:
        r = _post(payload)

    assert r.status_code == 200, r.text
    mocked.assert_called_once()
    assert mocked.call_args.kwargs["focus"] is None


def test_epoch_is_forwarded_and_recorded():
    """连发中断契约（2026-10-20）：epoch 必须原样到达 agent，
    并登记为该用户最新已受理的轮次。

    字段被吞掉的表现是"旧轮次永远跑完"——不报错、只是白烧模型调用，
    和 history 失忆同属最难查的静默失效。
    """
    with patch("agent.react_agent.run_agent", return_value=FAKE_RESULT) as mocked:
        r = _post({"user_id": "u1", "message": "hi", "epoch": 7})

    assert r.status_code == 200, r.text
    mocked.assert_called_once()
    assert mocked.call_args.kwargs["epoch"] == 7
    assert react_agent_module._EPOCHS.get("u1") == 7, "端点受理时必须登记轮次"


@pytest.mark.parametrize("bad_epoch", ["7", 1.5, True, [], {}])
def test_malformed_epoch_degrades_to_none(bad_epoch):
    """epoch 形状不合法（字符串/浮点/布尔/容器）不能打挂请求，退化为不启用中断。"""
    with patch("agent.react_agent.run_agent", return_value=FAKE_RESULT) as mocked:
        r = _post({"user_id": "u1", "message": "hi", "epoch": bad_epoch})

    assert r.status_code == 200, r.text
    mocked.assert_called_once()
    assert mocked.call_args.kwargs["epoch"] is None


# ===== 流式端点（2026-10-20 前端流式输出）=====

def _post_stream(payload):
    return client.post("/api/v1/agent/chat/stream", json=payload, headers=HEADERS)


def _parse_sse(text):
    import json as _json
    events = []
    for line in text.splitlines():
        if line.startswith("data:"):
            events.append(_json.loads(line[len("data:"):].strip()))
    return events


def test_stream_endpoint_emits_deltas_then_result():
    """流式契约：delta 事件逐段在前，result 终态在最后（Java 侧以 result 落库）。"""
    def fake_run(user_id, message, **kwargs):
        cb = kwargs.get("on_delta")
        cb("你")
        cb("好")
        return FAKE_RESULT

    with patch("agent.react_agent.run_agent", side_effect=fake_run):
        r = _post_stream({"user_id": "u1", "message": "hi", "epoch": 5})

    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    assert {"type": "delta", "text": "你"} in events
    assert {"type": "delta", "text": "好"} in events
    assert events[-1] == {"type": "result", "result": FAKE_RESULT}, "终态事件必须是最后一个"
    # epoch 登记在流式端点同样生效（连发中断契约不分流式/非流式）
    assert react_agent_module._EPOCHS.get("u1") == 5


def test_stream_endpoint_missing_fields_emit_empty_result():
    """缺字段时不能打挂流，也不能调 agent：一个空 result 事件收尾。"""
    with patch("agent.react_agent.run_agent", return_value=FAKE_RESULT) as mocked:
        r = _post_stream({"message": "没有用户标识"})

    assert r.status_code == 200, r.text
    assert _parse_sse(r.text) == [
        {"type": "result", "result": {"replies": [], "strategy_json": None}}]
    mocked.assert_not_called()
