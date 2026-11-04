# -*- coding: utf-8 -*-
"""市场综合流式版（2026-10-28 资讯雷达体验重订）的测试。

两层：
- _ReadStreamExtractor：从流式到达的 JSON 文本里增量提取 read 的"安全前缀"
  （转义凑齐才解码、闭引号即停、边界任意切分不炸）；
- synthesize_market_stream：与 synthesize_market 同形返回、流式全文解析失败
  退回非流式重试一次、已吐增量后断流不重试。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import news_understanding as nu  # noqa: E402


ITEMS = [{"title": "央行开展逆回购", "summary": "..."}] * 3


# ==================== 增量提取器 ====================

def feed_all(extractor, text, emit, chunk=7):
    """按 chunk 字符切着喂，模拟流式到达的任意边界。"""
    for i in range(0, len(text), chunk):
        extractor.feed(text[i:i + chunk], emit)


def test_extractor_emits_read_progressively_and_stops_at_close():
    ex = nu._ReadStreamExtractor()
    got = []
    feed_all(ex, '{"read": "市场信息面偏暖，主线集中", "themes": [{"topic": "流动性"}]}',
             got.append, chunk=5)
    assert "".join(got) == "市场信息面偏暖，主线集中"
    assert ex.closed, "闭引号之后应停止"


def test_extractor_handles_escapes_and_unicode():
    ex = nu._ReadStreamExtractor()
    got = []
    # \n 换行、\" 引号、\\u4e2d（"中"）—— 全部要在凑齐后才解码
    feed_all(ex, r'{"read": "第一行\n第二行\"引号\"中的字", "themes": []}', got.append, chunk=3)
    assert "".join(got) == '第一行\n第二行"引号"中的字'

    got2 = []
    ex2 = nu._ReadStreamExtractor()
    feed_all(ex2, r'{"read": "a 中 b", "themes": []}'.replace("中", r"\u4e2d"), got2.append, chunk=2)
    assert "".join(got2) == "a 中 b"


def test_extractor_silent_before_key_and_without_key():
    ex = nu._ReadStreamExtractor()
    got = []
    feed_all(ex, '抱歉，我换个格式：```json\n{"read": "正文"', got.append, chunk=4)
    assert "".join(got) == "正文", "模型在 JSON 前的客套话不应被当成 read"

    ex2 = nu._ReadStreamExtractor()
    got2 = []
    feed_all(ex2, '{"themes": []} 没有read字段', got2.append, chunk=4)
    assert got2 == []


def test_extractor_never_raises_on_garbage():
    ex = nu._ReadStreamExtractor()
    got = []
    for piece in ["{", None, '"read"', '": "', "文", "\\", 123, '"']:
        ex.feed(piece, got.append)   # 任何类型都只被 str() 化，绝不抛
    assert "".join(got).startswith("文") or not got


# ==================== 流式综合（monkeypatch llm_service） ====================

def _patch_llm(monkeypatch, stream_content, stream_raises=None, retry_content=None):
    """替换 llm_service 的三个入口。stream_content 按 5 字符一段喂 on_delta。"""
    events = {"deltas": [], "stream_called": False, "retry_called": False}

    def fake_stream(messages, tools=None, temperature=0.2, max_tokens=1200,
                    model=None, thinking=None, on_delta=None, providers=None):
        events["stream_called"] = True
        if stream_raises is not None:
            for i in range(0, min(len(stream_content or ""), 20), 5):
                on_delta(stream_content[i:i + 5])
                events["deltas"].append(stream_content[i:i + 5])
            raise stream_raises
        text = stream_content or ""
        for i in range(0, len(text), 5):
            if on_delta:
                on_delta(text[i:i + 5])
                events["deltas"].append(text[i:i + 5])
        return {"message": {"role": "assistant", "content": text}}

    def fake_chat(messages, temperature=0.2, max_tokens=1200):
        events["retry_called"] = True
        return {"message": {"role": "assistant", "content": retry_content or ""}}

    monkeypatch.setattr(nu.llm_service, "_is_available", lambda providers=None: True,
                        raising=False)
    monkeypatch.setattr(nu.llm_service, "chat_completion_stream", fake_stream)
    monkeypatch.setattr(nu.llm_service, "chat_completion", fake_chat)
    return events


def test_stream_happy_path_shapes_like_nonstream(monkeypatch):
    body = ('{"read": "信息面中性偏暖，关注科技与消费两条线", "themes": '
            '[{"topic": "科技", "count": 5, "line": "半导体获政策加码"}], "conflicts": ""}')
    events = _patch_llm(monkeypatch, stream_content=body)

    deltas = []
    result = nu.synthesize_market_stream(ITEMS, on_delta=deltas.append)

    assert result["ok"] is True
    assert result["read"].startswith("信息面中性偏暖")
    assert result["themes"] and result["themes"][0]["topic"] == "科技"
    assert "".join(deltas) == "信息面中性偏暖，关注科技与消费两条线"
    assert events["stream_called"] and not events["retry_called"]


def test_stream_unparsable_falls_back_to_nonstream_retry(monkeypatch):
    events = _patch_llm(
        monkeypatch,
        stream_content="这不是 JSON，只是散文",
        retry_content='{"read": "重试后的结论", "themes": [], "conflicts": ""}')

    deltas = []
    result = nu.synthesize_market_stream(ITEMS, on_delta=deltas.append)

    assert events["retry_called"] is True
    assert result["ok"] is True
    # sanitize 会给 read 补免责短句（幂等）：断言前缀而不是全等
    assert result["read"].startswith("重试后的结论")


def test_stream_broken_after_deltas_does_not_retry(monkeypatch):
    """已吐过增量后断流：不重试（重试会把同一段话拼两遍），ok=false 让前端收块。"""
    events = _patch_llm(monkeypatch, stream_content='{"read": "半截',
                        stream_raises=RuntimeError("连接断了"))

    deltas = []
    result = nu.synthesize_market_stream(ITEMS, on_delta=deltas.append)

    assert events["retry_called"] is False
    assert deltas, "断流前确实吐过增量"
    assert result["ok"] is False


def test_stream_skips_llm_when_no_items_or_unavailable(monkeypatch):
    assert nu.synthesize_market_stream([], on_delta=lambda p: None)["ok"] is False

    monkeypatch.setattr(nu.llm_service, "_is_available", lambda providers=None: False,
                        raising=False)
    assert nu.synthesize_market_stream(ITEMS, on_delta=lambda p: None)["ok"] is False


def test_shape_market_result_shared_by_both_paths():
    """非流式与流式最终都走 _shape_market_result：themes 上限 4、坏 theme 被丢。"""
    parsed = {
        "read": "结论",
        "themes": [
            {"topic": f"主题{i}", "count": 1, "line": f"内容{i}"} for i in range(6)
        ] + [{"topic": "", "line": "没名字的丢掉"}, {"topic": "没内容的", "line": ""}],
        "conflicts": "  ",
    }
    shaped = nu._shape_market_result(parsed)
    assert shaped["ok"] is True
    assert len(shaped["themes"]) == 4
    assert shaped["conflicts"] == ""
    assert nu._shape_market_result({"themes": []})["ok"] is False   # 没 read 就不 ok
