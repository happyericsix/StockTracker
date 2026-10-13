# -*- coding: utf-8 -*-
"""聚焦注入（agent 互通 P2）：render_focus + /agent/chat 的 focus 透传。

钉住三件事：
- 有效 focus 渲染成带新闻事实与工具指引的聚焦块，且拼在 system 最前；
- fail-open：未知 type / 事件取不到 / focus 非法形状，都退化为普通聊天（不炸、不注入）；
- 契约：focus 只认 {"type":"news_event","event_id":n}（Java ChatService 同判）。
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import news_knowledge
from agent import react_agent
import app as main  # noqa: E402

# conftest 把 INTERNAL_API_TOKEN 设成固定值（app.py 的内部端点是 fail-closed 的）
TOKEN = getattr(main, "INTERNAL_API_TOKEN", "") or ""
HEADERS = {"X-Internal-Token": TOKEN} if TOKEN else {}

FACTS = {
    "id": 321, "symbol": "SH600519", "name": "贵州茅台",
    "title": "贵州茅台关于回购公司股份的公告",
    "source_level": 1, "source_name": "东方财富·公告",
    "published_at": "2026-10-09 00:00:00", "date_only": True,
    "direction": "利好", "impact_level": "high",
    "credibility": 95, "credibility_grade": "高",
    "analyzed": True, "plain_summary": "公司拟回购股份。",
}


def test_render_focus_news_event_builds_block(monkeypatch):
    monkeypatch.setattr(news_knowledge, "event_facts", lambda event_id: dict(FACTS))
    block = react_agent.render_focus({"type": "news_event", "event_id": 321})
    assert "本轮聚焦" in block
    assert FACTS["title"] in block
    assert "利好" in block and "95" in block
    assert "read_news_detail" in block, "要指引模型用深挖工具，而不是只靠这一段背景"
    assert "仅日期" in block, "date_only 条目要说明时刻是占位"


@pytest.mark.parametrize("focus", [
    None,
    {},
    {"type": "unknown_type", "event_id": 1},
    {"type": "news_event"},                     # 缺 event_id
    "not-a-dict",
])
def test_render_focus_invalid_shapes_are_empty(monkeypatch, focus):
    def boom(event_id):
        raise AssertionError("非法形状不该触发取数")

    monkeypatch.setattr(news_knowledge, "event_facts", boom)
    assert react_agent.render_focus(focus) == ""


def test_render_focus_unreachable_facts_fail_open(monkeypatch):
    monkeypatch.setattr(news_knowledge, "event_facts", lambda event_id: None)
    assert react_agent.render_focus({"type": "news_event", "event_id": 999}) == ""


def test_run_agent_prepends_focus_block(monkeypatch):
    """聚焦块必须拼在 system（含记忆块）最前 —— 它定义这一轮对话围绕什么。"""
    captured = {}

    def fake_build_messages(system_prompt, history, message, context_block=None):
        captured["context_block"] = context_block
        return [{"role": "system", "content": system_prompt},
                {"role": "user", "content": message}]

    def fake_run_loop(*args, **kwargs):
        return {"replies": ["ok"], "strategy_json": None}

    monkeypatch.setattr(react_agent.memory, "build_messages", fake_build_messages)
    monkeypatch.setattr(react_agent.recall, "load_context", lambda *a, **k: {})
    monkeypatch.setattr(react_agent.recall, "render", lambda ctx: "记忆块：今天几号…")
    monkeypatch.setattr(react_agent, "_run_loop", fake_run_loop)
    monkeypatch.setattr(news_knowledge, "event_facts", lambda event_id: dict(FACTS))
    monkeypatch.setattr(react_agent, "_queue_tool_log", lambda *a, **k: None)
    monkeypatch.setattr(react_agent, "_queue_usage", lambda *a, **k: None)

    react_agent.run_agent("u1", "这条新闻什么意思？", memory_user_id=1,
                          focus={"type": "news_event", "event_id": 321})
    block = captured["context_block"]
    assert block.startswith("【本轮聚焦"), "聚焦块应位于 context 最前"
    assert "记忆块" in block, "记忆块仍在（聚焦只是前置，不是替换）"


def test_chat_endpoint_passes_focus_through(monkeypatch):
    seen = {}

    def fake_run(user_id, message, history=None, session_id=None, memory_user_id=None,
                 tool_mode=None, scopes=None, role=None, focus=None):
        seen["focus"] = focus
        return {"replies": ["ok"], "strategy_json": None}

    monkeypatch.setattr("agent.react_agent.run_agent", fake_run)
    r = TestClient(main.app).post("/api/v1/agent/chat", json={
        "user_id": "u1", "message": "聊聊这条",
        "focus": {"type": "news_event", "event_id": 321}}, headers=HEADERS)
    assert r.status_code == 200
    assert seen["focus"] == {"type": "news_event", "event_id": 321}


def test_chat_endpoint_ignores_non_dict_focus(monkeypatch):
    seen = {}

    def fake_run(user_id, message, history=None, session_id=None, memory_user_id=None,
                 tool_mode=None, scopes=None, role=None, focus=None):
        seen["focus"] = focus
        return {"replies": ["ok"], "strategy_json": None}

    monkeypatch.setattr("agent.react_agent.run_agent", fake_run)
    TestClient(main.app).post("/api/v1/agent/chat", json={
        "user_id": "u1", "message": "普通聊天", "focus": "junk"}, headers=HEADERS)
    assert seen["focus"] is None
