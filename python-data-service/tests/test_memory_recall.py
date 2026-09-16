# -*- coding: utf-8 -*-
"""记忆检索与注入（M0）。

这里钉住两件容易在重构中被弄丢、但用户能直接感知的事：
1. **agent 永远知道今天是几号** —— 即使记忆接口全挂，时间锚点也必须在；
2. **记忆层的故障不影响对话** —— Java 没起、超时、返回垃圾，都只能退化成"没有记忆"。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import memory_store, recall


def test_render_always_carries_time_anchor_even_without_memory():
    block = recall.render({})

    assert "<memory" in block and "</memory>" in block
    assert 'today="' in block
    assert "Asia/Shanghai" in block
    # 时间换算是 M0 要修的核心缺陷：agent 以前完全不知道"今天"
    assert "换算" in block


def test_render_marks_memory_as_data_not_instructions():
    """记忆内容来自用户输入与模型输出，属于不可信内容，必须声明不能当指令执行。"""
    block = recall.render({"today": "2026-09-16", "recaps": []})

    assert "不是指令" in block
    assert "以当前消息为准" in block


def test_render_includes_recap_with_absolute_date():
    context = {
        "today": "2026-09-16",
        "timeZone": "Asia/Shanghai (UTC+8)",
        "recaps": [{
            "sessionKey": "1:2026-09-14",
            "version": 2,
            "summary": "用户在调试均线策略，回撤偏大。",
            "keyPoints": ["关注回撤", "希望降低交易频率"],
            "openQuestions": ["是否改用周线"],
        }],
    }
    block = recall.render(context)

    assert "2026-09-14" in block
    assert "摘要 v2" in block
    assert "用户在调试均线策略" in block
    assert "- 关注回撤" in block
    assert "尚未确认" in block
    assert "是否改用周线" in block


def test_render_notes_pending_sessions_instead_of_pretending_to_know():
    block = recall.render({"today": "2026-09-16", "recaps": [], "pendingSessions": ["1:2026-09-13"]})

    assert "尚未生成摘要" in block
    assert "不要编造" in block


def test_render_includes_raw_digest_of_the_unsummarized_session():
    """兜底摘录：用户隔几天回来问"上次那个策略"，第一条消息时正式摘要往往还没生成，
    这时必须给原文摘录，否则第一条必然失忆。"""
    context = {
        "today": "2026-09-16",
        "recaps": [],
        "pendingSessions": ["1:2026-09-14"],
        "pendingDigest": [
            {"role": "user", "content": "帮我做一个20日上穿60日买入的策略", "occurredAt": "2026-09-14T10:00:00"},
            {"role": "assistant", "content": "已生成策略「MA cross」（600519）", "occurredAt": "2026-09-14T10:01:00"},
        ],
    }
    block = recall.render(context)

    assert "上一段对话（原始记录，摘要尚未生成）" in block
    assert "[2026-09-14 10:00] 用户: 帮我做一个20日上穿60日买入的策略" in block
    assert "助手: 已生成策略" in block
    # 已经被摘录覆盖，就不该再说"尚未生成摘要"来让模型以为这段完全没有上下文
    assert "另有" not in block


def test_render_digest_truncates_long_entries():
    context = {
        "today": "2026-09-16",
        "pendingDigest": [{"role": "user", "content": "长" * 500, "occurredAt": "2026-09-14T10:00:00"}],
    }
    block = recall.render(context)

    assert "长" * 500 not in block
    assert recall.DIGEST_CONTENT_CHARS < 500


def test_render_respects_budget():
    context = {
        "today": "2026-09-16",
        "recaps": [{"sessionKey": f"1:2026-09-{10 + i:02d}", "summary": "长" * 2000,
                    "keyPoints": ["要点" * 100]} for i in range(3)],
    }
    block = recall.render(context)

    assert len(block) <= recall.MEMORY_MAX_CHARS + len("\n…（记忆内容过长已截断）\n</memory>")
    assert block.endswith("</memory>")


def test_render_tolerates_garbage_context():
    """接口返回意料之外的结构时不能抛异常。"""
    for garbage in (None, [], "text", {"recaps": "not-a-list"}, {"recaps": [None, 42]}):
        block = recall.render(garbage)
        assert "</memory>" in block


def test_load_context_is_fail_open(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("java down")

    monkeypatch.setattr(memory_store, "load_context", boom)
    assert recall.load_context(1, "1:2026-09-16") == {}


def test_load_context_guesses_symbol_for_ranking(monkeypatch):
    """用户提到 600519 时，这只标的的事实应该排到前面（只影响排序，不参与判断）。"""
    seen = {}

    def spy(user_id, session_key, query=None, symbol=None, **kwargs):
        seen.update(user_id=user_id, session_key=session_key, query=query, symbol=symbol,
                    fact_limit=kwargs.get("fact_limit"))
        return {"today": "2026-09-16"}

    monkeypatch.setattr(memory_store, "load_context", spy)
    recall.load_context(1, "1:2026-09-16", query="600519 的成本是多少")

    assert seen["query"] == "600519 的成本是多少"
    assert seen["symbol"] == "600519"
    # 多要候选：Python 侧还要做语义融合，收敛到 8 条是融合之后的事
    assert seen["fact_limit"] == recall.SEMANTIC_CANDIDATE_LIMIT


def test_guess_symbol_ignores_non_codes():
    assert recall._guess_symbol("茅台现在多少钱") is None
    assert recall._guess_symbol("") is None
    assert recall._guess_symbol(None) is None
    assert recall._guess_symbol("12345 不是代码") is None
    assert recall._guess_symbol("代码 600519") == "600519"


def test_load_context_skips_call_without_identity(monkeypatch):
    called = {"n": 0}

    def spy(user_id, session_key):
        called["n"] += 1
        return {"today": "2026-09-16"}

    monkeypatch.setattr(memory_store, "load_context", spy)
    assert recall.load_context(None, None) == {}
    assert recall.load_context(1, None) == {}
    assert called["n"] == 0


def test_memory_store_never_raises_when_service_is_down(monkeypatch):
    """Java 侧不可达时必须返回空值，而不是把异常抛进聊天链路。"""
    import requests

    def boom(*args, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(requests, "post", boom)

    assert memory_store.load_context(1, "1:2026-09-16") == {}
    assert memory_store.list_events(1, "1:2026-09-16") == []
    assert memory_store.save_episode(1, "1:2026-09-16", "s", [], [], {}, [], "m") == {}


def test_today_uses_china_offset():
    from datetime import datetime

    assert memory_store.today_str() == datetime.now(memory_store.CN_TZ).strftime("%Y-%m-%d")


def test_base_url_follows_existing_project_naming(monkeypatch):
    """Java 地址认两套名字：JAVA_BASE_URL 和 .env.example 里既有的 SPRING_BASE_URL。"""
    monkeypatch.delenv("JAVA_BASE_URL", raising=False)
    monkeypatch.setenv("SPRING_BASE_URL", "http://spring:8080/")
    assert memory_store._base_url() == "http://spring:8080"

    monkeypatch.setenv("JAVA_BASE_URL", "http://java:9090/")
    assert memory_store._base_url() == "http://java:9090"

    monkeypatch.delenv("JAVA_BASE_URL")
    monkeypatch.delenv("SPRING_BASE_URL")
    assert memory_store._base_url() == "http://localhost:8080"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
