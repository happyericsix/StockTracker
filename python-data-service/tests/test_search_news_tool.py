# -*- coding: utf-8 -*-
"""search_news 工具（资讯雷达链路进 agent）：多源搜索 + 可信度标注。

与 test_external_tools.py 同一套替身思路：news_client.search_news 换成假的，
只测我们自己写的那部分 —— 参数校验、可信度注入、条目精简、信封、
以及"搜索源与快讯源健康互不牵连"（独立数据集的全部意义）。
真实的取数路径与资讯雷达页共用（app.py /api/v1/news/fetch），另有覆盖。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import news_client
from agent import external_source, tool_contract as tc, tool_scope, tool_sets
from agent.tool_registry import DEFAULT_USER_SCOPES, execute_tool, get_spec
from trading_calendar import CN_TZ


@pytest.fixture
def ctx():
    token = tool_scope.begin(user_id="u1", session_key="1:2026-09-16",
                             scopes=DEFAULT_USER_SCOPES)
    yield tool_scope.context()
    tool_scope.reset(token)


@pytest.fixture
def fresh_registry(monkeypatch):
    """每个用例一份干净的健康状态：否则"某个用例把源打挂"会污染其它用例。"""
    reg = external_source.ExternalSourceRegistry()
    monkeypatch.setattr(external_source, "REGISTRY", reg)
    return reg


def _event(level=2, title="标题", source="测试媒体", published=None,
           symbol="SH600519", content="正文内容"):
    return {
        "symbol": symbol, "name": "贵州茅台", "title": title, "content": content,
        "url": "https://example.com/1", "source_level": level, "source_name": source,
        "published_at": published or datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }


def _fake_search(items, errors=None, calls=None):
    def fake(symbol="", keyword="", types=None, days=7, with_body=False,
             only_relevant=False):
        if calls is not None:
            calls.append({"symbol": symbol, "keyword": keyword,
                          "types": types, "days": days,
                          "with_body": with_body, "only_relevant": only_relevant})
        counts = {"notice": 0, "news": 0, "report": 0, "market": 0}
        return {"items": list(items), "counts": counts,
                "errors": list(errors or []), "bodies_fetched": 0, "truncated": False}
    return fake


# ==================== 取数与信封 ====================

def test_keyword_search_returns_credibility_annotated_envelope(ctx, fresh_registry, monkeypatch):
    monkeypatch.setattr(news_client, "search_news",
                        _fake_search([_event(title="茅台公告", level=1, source="上交所")]))

    out = execute_tool("search_news", {"keyword": "茅台"})

    assert tc.is_ok(out)
    assert out["meta"]["source"] == "news.search"
    assert out["meta"]["trust"] == "low", "第三方内容永远按 low 信服"
    assert out["meta"]["fetched_at"], "外部数据必须带取数时间"
    item = out["data"]["items"][0]
    # 可信度三件套是这条链路存在的理由：分数 + 分档 + 理由必须一起到模型手里
    assert isinstance(item["credibility"], int) and 0 <= item["credibility"] <= 100
    assert item["credibility_grade"] in ("高", "较高", "中", "较低", "低")
    assert item["reasons"], "没有理由的分数只会被盲信"
    assert item["source"] == "上交所"
    assert item["source_level"] == 1
    assert item["freshness"], "时效标注必须给（模型不该心算日期差）"


def test_freshness_label_is_human_readable(ctx, fresh_registry, monkeypatch):
    """时效是用代码算的"3小时前"，不是把原始时间戳扔给模型。"""
    recent = (datetime.now(CN_TZ) - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    old = (datetime.now(CN_TZ) - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setattr(news_client, "search_news",
                        _fake_search([_event(title="新", published=recent),
                                      _event(title="旧", published=old)]))

    out = execute_tool("search_news", {"keyword": "x"})

    labels = [item["freshness"] for item in out["data"]["items"]]
    assert labels == ["30分钟前", "3天前"]


def test_symbol_is_resolved_and_passed_through(ctx, fresh_registry, monkeypatch):
    calls = []
    monkeypatch.setattr(news_client, "search_news", _fake_search([], calls=calls))

    execute_tool("search_news", {"symbol": "600519", "types": [1, 3], "days": 30})

    assert "600519" in calls[0]["symbol"], "名字必须先解析成代码再进搜索"
    assert calls[0]["types"] == [1, 3]
    assert calls[0]["days"] == 30
    # 走的是搜索链路，不该替用户付"补公告正文"的请求成本
    assert calls[0]["with_body"] is False


def test_credibility_engine_actually_discriminates(ctx, fresh_registry, monkeypatch):
    """真引擎接入：公告 + 具体数字 要压过 满篇传闻措辞的媒体条目。"""
    items = [
        _event(level=1, source="上交所", title="关于回购股份的公告",
               content="公司拟以不超过 5.8 亿元回购股份"),
        _event(level=2, source="某自媒体号", title="据传茅台或将暴涨",
               content="据传知情人士透露重大利好"),
    ]
    monkeypatch.setattr(news_client, "search_news", _fake_search(items))

    out = execute_tool("search_news", {"keyword": "茅台"})

    announcement, rumor = out["data"]["items"]
    assert announcement["credibility"] > rumor["credibility"]
    assert rumor["rumor_flag"] is True
    assert announcement["rumor_flag"] is False


def test_limit_is_applied_to_deduped_items(ctx, fresh_registry, monkeypatch):
    monkeypatch.setattr(news_client, "search_news",
                        _fake_search([_event(title=f"T{i}") for i in range(5)]))

    out = execute_tool("search_news", {"keyword": "x", "limit": 3})

    assert out["data"]["count"] == 3


def test_empty_result_is_a_valid_answer(ctx, fresh_registry, monkeypatch):
    monkeypatch.setattr(news_client, "search_news", _fake_search([]))

    out = execute_tool("search_news", {"keyword": "量子计算"})

    assert tc.is_ok(out)
    assert out["data"]["count"] == 0
    assert "不要编造" in out["data"]["note"]


# ==================== 参数校验 ====================

def test_requires_keyword_or_symbol_and_points_to_get_news(ctx, fresh_registry):
    """两个参数都没有时不能静默全市场搜索：那是 get_news 的职责，边界要说清。"""
    out = execute_tool("search_news", {})

    assert not tc.is_ok(out)
    assert out["error"]["code"] == tc.INVALID_ARGS
    assert "get_news" in out["error"]["message"]


def test_unresolvable_symbol_fails_loudly(ctx, fresh_registry):
    out = execute_tool("search_news", {"symbol": "不存在的公司名XYZ"})

    assert not tc.is_ok(out)
    assert out["error"]["code"] == tc.INVALID_ARGS
    assert "search_stock" in out["error"]["message"]


def test_unknown_source_level_is_rejected_not_ignored(ctx, fresh_registry):
    """静默忽略未知类型 = 模型以为"公告一条都没有"。必须报出来。"""
    out = execute_tool("search_news", {"keyword": "x", "types": [1, 9]})

    assert not tc.is_ok(out)
    assert out["error"]["code"] == tc.INVALID_ARGS
    assert "9" in out["error"]["message"]


def test_non_list_types_is_invalid_args(ctx, fresh_registry):
    out = execute_tool("search_news", {"keyword": "x", "types": "公告"})

    assert not tc.is_ok(out)
    assert out["error"]["code"] == tc.INVALID_ARGS


# ==================== 独立健康（独立数据集的意义） ====================

def test_dead_search_source_does_not_take_down_get_news(ctx, fresh_registry, monkeypatch):
    """搜索源挂三次 → search_news 被摘除，但快讯（get_news）必须还活着。

    这条断言钉住"为什么 search_news 要声明自己的数据集"：
    若与快讯共用 eastmoney 上游，一次搜索故障会把整类新闻能力一起摘掉。
    """
    for _ in range(external_source.FAILURE_THRESHOLD):
        fresh_registry.record_failure("news.search", "ConnectionError: boom")

    out = execute_tool("search_news", {"keyword": "x"})

    assert not tc.is_ok(out)
    assert out["error"]["code"] == tc.SOURCE_UNAVAILABLE
    assert out["error"]["retryable"] is False

    enrolled = tool_sets.enroll(ctx)
    assert "search_news" not in enrolled
    assert "get_news" in enrolled, "快讯源与搜索源互不牵连"


def test_search_news_is_enrolled_by_default(ctx, fresh_registry):
    assert "search_news" in tool_sets.enroll(ctx)
    assert "search_news" in tool_sets.sets_for(*tool_sets.MODES["market"])
