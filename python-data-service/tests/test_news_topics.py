# -*- coding: utf-8 -*-
"""N5 总编/撰稿单测（含契约常量与降级路径）。

桩法与 test_news_understanding 同一套（按序吐 replies 的 chat_completion 桩）。
重点断言：
- 契约字段名与方向枚举（Java 侧逐字比对，见 test_news_topic_contract_java.py）；
- 三级降级都可读：LLM 未配置 / 调用异常 / 输出解析失败 → ok=False + 中文原因，
  事件线本身照常存在（fail-open 是调用方 Java 的职责，Python 只负责明说"没成"）。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm_service  # noqa: E402
import news_topics  # noqa: E402
from news_topics import editor_pass, write_topic  # noqa: E402


def patch_llm(monkeypatch, replies, available=True):
    calls = []

    def fake_completion(messages, tools=None, temperature=0.2, max_tokens=1200):
        calls.append({"messages": messages, "max_tokens": max_tokens})
        reply = replies[min(len(calls) - 1, len(replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return {"message": {"role": "assistant", "content": reply}}

    monkeypatch.setattr(llm_service, "chat_completion", fake_completion)
    monkeypatch.setattr(llm_service, "_is_available", lambda: available)
    return calls


CLUSTER = {
    "title": "贵州茅台关于召开2026年半年度业绩说明会的公告",
    "members": [
        {"title": "贵州茅台将于9月25日举行半年度业绩说明会", "published_at": "2026-10-09 09:00:00",
         "source_name": "证券时报网", "source_level": 2, "direction": "利好",
         "plain_summary": "公司将在业绩说明会回应投资者问题。", "symbol": "SH600519",
         "name": "贵州茅台", "id": 11, "url": "https://e/2"},
        {"title": "贵州茅台关于召开2026年半年度业绩说明会的公告", "published_at": "2026-10-08 18:00:00",
         "source_name": "东方财富·公告", "source_level": 1, "direction": None,
         "plain_summary": "", "symbol": "SH600519", "name": "贵州茅台",
         "id": 10, "url": "https://e/1"},
    ],
    "member_count": 2, "first_at": "2026-10-08 18:00:00", "last_at": "2026-10-09 09:00:00",
    "sources": ["东方财富·公告", "证券时报网"], "source_count": 2,
    "symbols": ["SH600519"], "directions": ["利好"],
}


# ==================== 契约常量 ====================


def test_contract_constants_are_closed_sets():
    """契约是两侧封闭集：字段名与原因枚举必须稳定（Java 侧逐字镜像）。"""
    assert news_topics.EDITOR_OUTPUT_FIELDS == ("index", "title", "worth_deep", "reason")
    assert news_topics.WRITER_OUTPUT_FIELDS == (
        "digest", "title", "story", "outlook", "direction", "confidence", "affected_symbols")
    assert set(news_topics.SKIP_REASONS) == {
        "editor_llm_unavailable", "editor_budget_exceeded",
        "editor_cooldown", "editor_skip_low_priority"}


# ==================== 总编 ====================


def test_editor_pass_normalizes_rows(monkeypatch):
    reply = json.dumps({"topics": [
        {"index": 1, "title": "茅台将开业绩说明会", "worth_deep": True, "reason": "公告+媒体跟进"},
        {"index": 2, "title": "x" * 60, "worth_deep": False, "reason": "孤本报道"},   # 标题超长→截断
        {"index": 99, "title": "越界行"},                                            # index 越界→丢弃
        {"index": 3},                                                                 # 缺标题→丢弃
    ]})
    patch_llm(monkeypatch, [reply])
    result = editor_pass([CLUSTER, dict(CLUSTER), dict(CLUSTER)])
    assert result["ok"] is True
    assert [t["index"] for t in result["topics"]] == [1, 2]
    assert result["topics"][0]["title"] == "茅台将开业绩说明会"
    assert result["topics"][0]["worth_deep"] is True
    assert len(result["topics"][1]["title"]) <= 40


def test_editor_pass_empty_input(monkeypatch):
    patch_llm(monkeypatch, ["不该被调用"])
    assert editor_pass([])["ok"] is False


def test_editor_pass_llm_unavailable(monkeypatch):
    patch_llm(monkeypatch, ["x"], available=False)
    result = editor_pass([CLUSTER])
    assert result["ok"] is False and result["topics"] == []


def test_editor_pass_llm_exception(monkeypatch):
    patch_llm(monkeypatch, [RuntimeError("超时")])
    assert editor_pass([CLUSTER])["ok"] is False


def test_editor_pass_unparseable_output_retries_then_fails(monkeypatch):
    patch_llm(monkeypatch, ["不是 JSON", "还是不是 JSON"])
    result = editor_pass([CLUSTER])
    assert result["ok"] is False


# ==================== 撰稿 ====================


GOOD_WRITER_REPLY = json.dumps({
    "digest": "苹果10月底或发触屏Mac，仍处传闻阶段",
    "title": "苹果将开半年度业绩说明会",
    "story": "公司公告将于9月25日召开2026年半年度业绩说明会，证券时报次日跟进报道。"
             "会上管理层将回应半年度经营问题。",
    "outlook": "关注9月25日说明会上的管理层表态。",
    "direction": "利好",
    "confidence": 0.6,
    "affected_symbols": ["600519", "SH600519", "SH600519"],   # 归一化 + 去重
})


def test_write_topic_happy_path(monkeypatch):
    patch_llm(monkeypatch, [GOOD_WRITER_REPLY])
    result = write_topic({"title": CLUSTER["title"], "items": CLUSTER["members"]})
    assert result["ok"] is True
    assert result["title"] == "苹果将开半年度业绩说明会"
    assert result["digest"] == "苹果10月底或发触屏Mac，仍处传闻阶段"
    assert "业绩说明会" in result["story"]
    # story/outlook **不追加免责短句**：雷达页事件线是多条连排的，逐条追加
    # 会在一屏里刷出十个免责句（用户实测反馈"没人会看"的直接原因之一）。
    # 免责由前端在区块级统一放一行。
    assert "不构成投资建议" not in result["story"]
    assert "不构成投资建议" not in result["outlook"]
    assert result["direction"] == "利好"
    assert result["affected_symbols"] == ["SH600519"]
    # 只喂结论字段：正文 content 不进 prompt（成员 content 即标题，这里断言输入不含正文键）
    # —— 由下面 test_writer_input_shape 单独钉住。


def test_write_topic_digest_falls_back_to_first_sentence(monkeypatch):
    """模型没给 digest：取 story 首句兜底，绝不空着（前端靠它渲染摘要行）。"""
    reply = json.dumps({"title": "t",
                        "story": "公司终止筹划股权收购。终止原因未披露，影响待确认。后续等待说明。",
                        "outlook": "看后续。", "direction": "利空", "confidence": 0.7})
    patch_llm(monkeypatch, [reply])
    result = write_topic({"title": "t", "items": CLUSTER["members"]})
    assert result["ok"] is True
    assert result["digest"].startswith("公司终止筹划股权收购。")


def test_writer_input_only_feeds_conclusion_fields(monkeypatch):
    """撰稿输入只喂结论字段（时间/级别/来源/方向/标题/解读），不重贴正文。"""
    calls = patch_llm(monkeypatch, [GOOD_WRITER_REPLY])
    write_topic({"title": "t", "items": [
        {"title": "标题甲", "content": "SECRET_BODY_甲", "body": "SECRET_BODY_乙",
         "published_at": "2026-10-09 09:00:00", "source_level": 1,
         "source_name": "东方财富·公告", "direction": "利好",
         "plain_summary": "一句解读"}]})
    user_prompt = calls[0]["messages"][1]["content"]
    assert "一句解读" in user_prompt
    assert "SECRET_BODY_甲" not in user_prompt and "SECRET_BODY_乙" not in user_prompt


def test_write_topic_unknown_direction_downgrades(monkeypatch):
    """direction 说了枚举外的词 → None + 置信度压低（与 N2 的防幻觉同一条纪律）。"""
    reply = json.dumps({"title": "t", "story": "一段来龙去脉。", "outlook": "看后续。",
                        "direction": "偏多", "confidence": 0.9})
    patch_llm(monkeypatch, [reply])
    result = write_topic({"title": "t", "items": CLUSTER["members"]})
    assert result["ok"] is True
    assert result["direction"] is None
    assert result["confidence"] < news_topics.news_understanding.MIN_CONFIDENCE


def test_write_topic_no_items(monkeypatch):
    patch_llm(monkeypatch, ["不该被调用"])
    assert write_topic({"title": "t", "items": []})["ok"] is False


def test_write_topic_llm_unavailable_or_broken(monkeypatch):
    patch_llm(monkeypatch, ["x"], available=False)
    assert write_topic({"title": "t", "items": CLUSTER["members"]})["ok"] is False
    patch_llm(monkeypatch, ["空story的输出"])   # 无 story 字段 → 解析失败
    result = write_topic({"title": "t", "items": CLUSTER["members"]})
    assert result["ok"] is False and result["story"] == ""


def test_write_topic_caps_members(monkeypatch):
    """成员上限 20：超出的按输入序保留前 20（Java 侧已按时间倒序传入）。"""
    calls = patch_llm(monkeypatch, [GOOD_WRITER_REPLY])
    members = [dict(m, title=f"报道{i}", id=i) for i, m in enumerate(
        [CLUSTER["members"][0]] * 30)]
    write_topic({"title": "t", "items": members})
    user_prompt = calls[0]["messages"][1]["content"]
    assert "以下是这条线上的 20 条资讯" in user_prompt
