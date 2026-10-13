# -*- coding: utf-8 -*-
"""事件线端点契约：POST /api/v1/news/cluster 与 /api/v1/news/topic-synthesize。

信封形状（{ok,data,error}）、参数校验（400 带中文原因）、内部 token fail-closed
—— 与既有 /news/fetch、/news/analyze 同一套约束，桩掉业务函数只测端点层。
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import news_clustering  # noqa: E402
import news_topics  # noqa: E402
import app as main  # noqa: E402

TOKEN = getattr(main, "INTERNAL_API_TOKEN", "") or ""
HEADERS = {"X-Internal-Token": TOKEN} if TOKEN else {}

ITEM = {"id": 1, "symbol": "", "name": "", "title": "央行开展3000亿元MLF操作",
        "content": "", "url": "", "source_level": 2, "source_name": "新浪财经",
        "event_type_raw": "", "published_at": "2026-10-09 09:00:00"}
ITEM2 = dict(ITEM, id=2, title="央行开展3000亿元MLF操作 利率维持不变",
             published_at="2026-10-09 10:00:00")


# ==================== /api/v1/news/cluster ====================


def test_cluster_endpoint_ok(monkeypatch):
    def fake_cluster(items):
        return {"clusters": [{"title": "t", "members": items, "member_count": 2,
                              "first_at": "", "last_at": "", "sources": ["新浪财经"],
                              "source_count": 1, "symbols": [], "directions": []}],
                "unclustered_count": 0}

    monkeypatch.setattr(news_clustering, "cluster_events", fake_cluster)
    r = TestClient(main.app).post("/api/v1/news/cluster", json={"items": [ITEM, ITEM2]},
                                  headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["clusters"][0]["member_count"] == 2
    # id 原样透传：Java 侧靠它映射回 news_events 主键
    assert [m["id"] for m in body["data"]["clusters"][0]["members"]] == [1, 2]


def test_cluster_endpoint_pure_function_called_with_items(monkeypatch):
    seen = {}

    def fake_cluster(items):
        seen["items"] = items
        return {"clusters": [], "unclustered_count": 0}

    monkeypatch.setattr(news_clustering, "cluster_events", fake_cluster)
    TestClient(main.app).post("/api/v1/news/cluster", json={"items": [ITEM]},
                              headers=HEADERS)
    assert seen["items"] == [ITEM]


@pytest.mark.parametrize("payload", [
    {"items": "not-a-list"},
    {"items": []},
    {"items": [1, 2]},
    {},
])
def test_cluster_endpoint_rejects_bad_payload(monkeypatch, payload):
    monkeypatch.setattr(news_clustering, "cluster_events",
                        lambda items: pytest.fail("参数非法时不该调用聚类"))
    r = TestClient(main.app).post("/api/v1/news/cluster", json=payload, headers=HEADERS)
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_cluster_endpoint_rejects_oversized_input(monkeypatch):
    monkeypatch.setattr(news_clustering, "cluster_events",
                        lambda items: pytest.fail("超上限时不该调用聚类"))
    items = [dict(ITEM, id=i) for i in range(news_clustering.MAX_INPUT_ITEMS + 1)]
    r = TestClient(main.app).post("/api/v1/news/cluster", json={"items": items},
                                  headers=HEADERS)
    assert r.status_code == 400


def test_cluster_endpoint_requires_internal_token(monkeypatch):
    monkeypatch.setattr(news_clustering, "cluster_events",
                        lambda items: pytest.fail("未鉴权的请求不该进入业务逻辑"))
    assert TestClient(main.app).post("/api/v1/news/cluster",
                                     json={"items": [ITEM]}).status_code == 401


# ==================== /api/v1/news/topic-synthesize ====================


EDITOR_REPLY = {"ok": True, "error": "", "topics": [
    {"index": 1, "title": "央行MLF操作", "worth_deep": True, "reason": "多源印证"}]}
WRITER_REPLY = {"ok": True, "error": "", "title": "央行MLF操作", "story": "来龙去脉。",
                "outlook": "看后续量价。", "direction": "中性", "confidence": 0.5,
                "affected_symbols": []}


def test_topic_synthesize_editor_mode(monkeypatch):
    monkeypatch.setattr(news_topics, "editor_pass", lambda clusters: EDITOR_REPLY)
    r = TestClient(main.app).post(
        "/api/v1/news/topic-synthesize",
        json={"mode": "editor", "clusters": [{"title": "t", "members": []}]},
        headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["data"]["topics"][0]["worth_deep"] is True


def test_topic_synthesize_writer_mode(monkeypatch):
    seen = {}

    def fake_write(topic):
        seen["topic"] = topic
        return WRITER_REPLY

    monkeypatch.setattr(news_topics, "write_topic", fake_write)
    r = TestClient(main.app).post(
        "/api/v1/news/topic-synthesize",
        json={"mode": "writer", "topic": {"title": "t", "items": [ITEM]}},
        headers=HEADERS)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["ok"] is True and data["story"] == "来龙去脉。"
    assert seen["topic"]["items"] == [ITEM]


def test_topic_synthesize_inner_failure_is_200_with_ok_false(monkeypatch):
    """LLM 不可用是**业务降级**不是服务错误：外层 200 + data.ok=false，
    Java 侧据此按 editor_llm_unavailable 留痕（不是把它当 5xx 重试）。"""
    monkeypatch.setattr(news_topics, "write_topic",
                        lambda topic: dict(WRITER_REPLY, ok=False, error="AI 服务未配置"))
    r = TestClient(main.app).post(
        "/api/v1/news/topic-synthesize",
        json={"mode": "writer", "topic": {"title": "t", "items": [ITEM]}},
        headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["data"]["ok"] is False


@pytest.mark.parametrize("payload", [
    {"mode": "unknown"},
    {"mode": "editor", "clusters": []},
    {"mode": "editor"},
    {"mode": "writer"},
    {"mode": "writer", "topic": "not-an-object"},
    {},
])
def test_topic_synthesize_rejects_bad_payload(monkeypatch, payload):
    monkeypatch.setattr(news_topics, "editor_pass",
                        lambda c: pytest.fail("参数非法时不该调用 LLM"))
    monkeypatch.setattr(news_topics, "write_topic",
                        lambda t: pytest.fail("参数非法时不该调用 LLM"))
    r = TestClient(main.app).post("/api/v1/news/topic-synthesize", json=payload,
                                  headers=HEADERS)
    assert r.status_code == 400


def test_topic_synthesize_requires_internal_token(monkeypatch):
    monkeypatch.setattr(news_topics, "editor_pass",
                        lambda c: pytest.fail("未鉴权的请求不该进入业务逻辑"))
    assert TestClient(main.app).post(
        "/api/v1/news/topic-synthesize",
        json={"mode": "editor", "clusters": [{"title": "t"}]}).status_code == 401
