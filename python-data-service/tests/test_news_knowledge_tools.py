# -*- coding: utf-8 -*-
"""新闻知识工具（agent 互通 P1）：read_news_detail / get_topic_story / get_stock_briefing。

替身思路与 test_search_news_tool 同一套：把 agent.news_knowledge 的取数换成假的，
只测我们自己写的部分 —— 参数校验、信封、未分析条目的现场深读、
以及"端点不可用 = SOURCE_UNAVAILABLE（不重试，换路）"的错误分类。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import news_knowledge, tool_contract as tc, tool_scope, tool_sets
from agent.tool_registry import DEFAULT_USER_SCOPES, execute_tool


@pytest.fixture
def ctx():
    token = tool_scope.begin(user_id="u1", session_key="1:2026-10-09",
                             scopes=DEFAULT_USER_SCOPES)
    yield tool_scope.context()
    tool_scope.reset(token)


ANALYZED_EVENT = {
    "id": 321, "symbol": "SH600519", "name": "贵州茅台",
    "title": "贵州茅台关于回购公司股份的公告",
    "content": "公告内容", "body": "公告全文",
    "source_level": 1, "source_name": "东方财富·公告",
    "published_at": "2026-10-09 00:00:00", "date_only": True,
    "direction": "利好", "impact_level": "high", "event_type": "回购",
    "credibility": 95, "credibility_grade": "高",
    "analyzed": True, "plain_summary": "公司拟回购股份。",
}

UNANALYZED_EVENT = dict(ANALYZED_EVENT, id=322, analyzed=False,
                        plain_summary="", direction=None, impact_level=None)


def run_tool(name, **args):
    return execute_tool(name, args)


# ==================== 注册与装填 ====================


def test_news_tools_registered_and_enrolled():
    names = set(tool_sets.sets_for("news"))
    assert {"read_news_detail", "get_topic_story", "get_stock_briefing"} <= names


# ==================== read_news_detail ====================


def test_read_news_detail_analyzed_event_returns_facts_as_is(monkeypatch):
    monkeypatch.setattr(news_knowledge, "event_facts",
                        lambda event_id: dict(ANALYZED_EVENT) if event_id == 321 else None)
    result = run_tool("read_news_detail", event_id=321)
    assert result["ok"] is True
    event = result["data"]["event"]
    assert event["plain_summary"] == "公司拟回购股份。"
    assert "ad_hoc_analysis" not in result["data"], "已分析条目不该再花一次深读"


def test_read_news_detail_unanalyzed_event_gets_ad_hoc_deep_read(monkeypatch):
    monkeypatch.setattr(news_knowledge, "event_facts",
                        lambda event_id: dict(UNANALYZED_EVENT) if event_id == 322 else None)
    called = {}

    def fake_analyze_one(item, mode="deep"):
        called["mode"] = mode
        called["title"] = item["title"]
        return {"direction": "利好", "confidence": 0.8, "impact_level": "high",
                "plain_summary": "现场解读结论。", "risks": ["r"], "opportunities": ["o"],
                "analyzed": True}

    import news_understanding
    monkeypatch.setattr(news_understanding, "analyze_one", fake_analyze_one)
    result = run_tool("read_news_detail", event_id=322)
    assert result["ok"] is True
    assert result["data"]["ad_hoc_analysis"]["plain_summary"] == "现场解读结论。"
    assert "未落库" in result["data"]["note"]
    assert called["mode"] == "deep" and called["title"] == UNANALYZED_EVENT["title"]


def test_read_news_detail_unavailable_is_source_unavailable(monkeypatch):
    monkeypatch.setattr(news_knowledge, "event_facts", lambda event_id: None)
    result = run_tool("read_news_detail", event_id=999)
    assert result["ok"] is False
    assert result["error"]["code"] == tc.SOURCE_UNAVAILABLE


def test_read_news_detail_requires_event_id():
    result = run_tool("read_news_detail")
    assert result["ok"] is False
    assert result["error"]["code"] == tc.INVALID_ARGS


# ==================== get_topic_story ====================


def test_get_topic_story_slims_topics(monkeypatch):
    monkeypatch.setattr(news_knowledge, "topics", lambda symbol, days=7: [{
        "title": "茅台终止收购", "digest": "终止且未披露原因", "summary": "来龙去脉。" * 100,
        "outlook": "看后续。", "direction": "利空", "event_count": 8, "source_count": 2,
        "firstEventAt": "2026-10-08T00:00:00", "lastEventAt": "2026-10-09T00:00:00"}])
    result = run_tool("get_topic_story", symbol="600519", days=7)
    assert result["ok"] is True
    topic = result["data"]["topics"][0]
    assert topic["digest"] == "终止且未披露原因"
    assert len(topic["story"]) <= 400, "story 进上下文要截断（全文在雷达页）"
    assert topic["span"].startswith("2026-10-08")


def test_get_topic_story_empty_vs_unavailable_are_different(monkeypatch):
    monkeypatch.setattr(news_knowledge, "topics", lambda symbol, days=7: [])
    empty = run_tool("get_topic_story", symbol="600519")
    assert empty["ok"] is True and empty["data"]["topics"] == []
    assert "没有已撰稿专题" in empty["data"]["note"]

    monkeypatch.setattr(news_knowledge, "topics", lambda symbol, days=7: None)
    down = run_tool("get_topic_story", symbol="600519")
    assert down["ok"] is False and down["error"]["code"] == tc.SOURCE_UNAVAILABLE


def test_get_topic_story_requires_known_symbol():
    result = run_tool("get_topic_story", symbol="不存在的公司名XYZ")
    assert result["ok"] is False
    assert result["error"]["code"] == tc.INVALID_ARGS


# ==================== get_stock_briefing ====================


def test_get_stock_briefing_passes_facts_with_note(monkeypatch):
    seen = {}

    def fake_briefing(symbol, days=7, max_events=12):
        seen.update(symbol=symbol, days=days)
        return {"symbol": symbol, "events": [dict(ANALYZED_EVENT)], "topics": [],
                "event_count": 1}

    monkeypatch.setattr(news_knowledge, "briefing", fake_briefing)
    result = run_tool("get_stock_briefing", symbol="600519", days=3)
    assert result["ok"] is True
    assert seen == {"symbol": "600519", "days": 3}
    assert "可信度" in result["data"]["note"]


def test_get_stock_briefing_empty_gets_do_not_fabricate_note(monkeypatch):
    monkeypatch.setattr(news_knowledge, "briefing",
                        lambda symbol, days=7, max_events=12:
                        {"symbol": symbol, "events": [], "topics": [], "event_count": 0})
    result = run_tool("get_stock_briefing", symbol="600519")
    assert result["ok"] is True
    assert "不要编造" in result["data"]["note"]


def test_get_stock_briefing_unavailable_is_source_unavailable(monkeypatch):
    monkeypatch.setattr(news_knowledge, "briefing",
                        lambda symbol, days=7, max_events=12: None)
    result = run_tool("get_stock_briefing", symbol="600519")
    assert result["ok"] is False and result["error"]["code"] == tc.SOURCE_UNAVAILABLE
