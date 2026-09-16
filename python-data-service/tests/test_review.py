# -*- coding: utf-8 -*-
"""裁决者（critic）的离线测试：**只测它的边界，不测模型聪不聪明**。

<h3>这个文件在防什么</h3>
审查者最危险的失效方式不是"判错"，而是**顺从**：顺着说"这是单位换算"就把编造洗白了。
防它的唯一办法是确定性的 —— 所以这里钉死三件事：

1. **判"合法"必须引用真实存在的证据编号**，引用不存在的编号整条作废；
2. **它结构上什么都做不了**：没有工具、不写任何东西；
3. **它永远不阻断**：模型不可用 / 输出不是 JSON / 抛异常 —— 一律退化成 unreviewed。

至于"判得准不准"，那必须真调模型，见 `tests/test_review_eval.py`（默认 skip）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import review


QUOTE_EVIDENCE = {
    "tool": "get_quote",
    "content": ('{"ok": true, "data": {"quote": {"代码": "300750", "最新价": "305.48", '
                '"成交额": "2398035", "涨跌幅": "-3.44", "涨跌额": "-10.88", '
                '"市盈率-动态": "16.63"}}}'),
}


def _choice(payload: dict) -> dict:
    return {"message": {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)}}


def _completion(payload):
    calls = []

    def call(messages, **kwargs):
        calls.append({"messages": messages, "kwargs": kwargs})
        return _choice(payload)

    call.calls = calls
    return call


# ==================== 1. 证据编号 ====================


def test_evidence_blocks_are_numbered_and_gaps_are_skipped():
    blocks, refs = review._evidence_blocks([
        QUOTE_EVIDENCE,
        "纯字符串证据",
        {"tool": "get_news", "content": "   "},   # 空的不占编号
        None,
    ])
    assert list(refs) == ["E1", "E2"]
    assert refs["E1"]["tool"] == "get_quote"
    assert refs["E2"]["tool"] == ""
    assert blocks[0]["text"].startswith('{"ok": true')


def test_evidence_text_is_capped():
    """证据太大时截断：裁决只需要"能被推出那个数的字段"，不是整篇新闻正文。"""
    blocks, _ = review._evidence_blocks([{"tool": "get_news", "content": "x" * 99999}])
    assert len(blocks[0]["text"]) == review.MAX_EVIDENCE_CHARS


# ==================== 2. JSON 容错 ====================


def test_extract_json_variants():
    assert review._extract_json('{"verdicts": []}') == {"verdicts": []}
    assert review._extract_json('```json\n{"verdicts": []}\n```') == {"verdicts": []}
    assert review._extract_json('好的，结果是：{"verdicts": []} 以上。') == {"verdicts": []}
    assert review._extract_json("完全不是 JSON") is None
    assert review._extract_json(None) is None
    assert review._extract_json("[1, 2]") is None  # 只接受对象


# ==================== 3. 确定性过滤：这套设计的核心 ====================


def _candidates(*numbers):
    return [{"number": number, "value": 1.0, "context": f"…{number}…"} for number in numbers]


def test_legitimate_verdict_with_valid_ref_is_kept():
    kept, dropped = review._filter_verdicts(
        [{"number": "239.8", "label": "legitimate_derivation",
          "derivation": "2398035 万元 = 239.8035 亿元", "evidence_refs": ["E1"]}],
        _candidates("239.8"), {"E1"})
    assert len(kept) == 1 and not dropped
    assert kept[0]["evidence_refs"] == ["E1"]


def test_legitimate_verdict_with_hallucinated_ref_is_dropped():
    """**关键约束**：判"合法"却引用不存在的证据 = 洗白，整条作废。"""
    kept, dropped = review._filter_verdicts(
        [{"number": "999", "label": "legitimate_derivation",
          "derivation": "证据里就是这么写的", "evidence_refs": ["E99"]}],
        _candidates("999"), {"E1", "E2"})
    assert kept == []
    assert dropped[0]["reason"] == "unknown_evidence_ref"


def test_legitimate_verdict_without_any_ref_is_dropped():
    kept, dropped = review._filter_verdicts(
        [{"number": "239.8", "label": "legitimate_derivation"}],
        _candidates("239.8"), {"E1"})
    assert kept == []
    assert dropped[0]["reason"] == "legitimate_without_evidence"


def test_unsupported_verdict_needs_no_ref():
    """不对称是刻意的：判"编造"的定义就是"证据里推不出来"，它没有可引用的证据。"""
    kept, dropped = review._filter_verdicts(
        [{"number": "18.3", "label": "unsupported", "derivation": "证据里没有回测数据"}],
        _candidates("18.3"), {"E1"})
    assert len(kept) == 1 and not dropped


def test_verdict_for_non_candidate_number_is_dropped():
    """只裁决被标记的数字：不能借机对别的东西发表意见。"""
    kept, dropped = review._filter_verdicts(
        [{"number": "42", "label": "unsupported"}], _candidates("18.3"), {"E1"})
    assert kept == []
    assert dropped[0]["reason"] == "number_not_a_candidate"


def test_unknown_label_is_dropped():
    kept, dropped = review._filter_verdicts(
        [{"number": "18.3", "label": "大概是吧"}], _candidates("18.3"), {"E1"})
    assert kept == []
    assert dropped[0]["reason"] == "unknown_label"


def test_garbage_verdict_entries_are_dropped_not_crashed():
    kept, dropped = review._filter_verdicts(
        [None, "字符串", 42], _candidates("18.3"), {"E1"})
    assert kept == []
    assert len(dropped) == 3


# ==================== 4. 入口：成本、分组、失败退化 ====================


def test_no_candidates_means_no_llm_call():
    """没有候选就零成本返回 —— 大多数轮次都不该花这笔钱。"""
    call = _completion({"verdicts": []})
    result = review.review_turn({"unsupported_claims": []}, [QUOTE_EVIDENCE], completion=call)
    assert result["status"] == "ok"
    assert result["cost"]["llm_calls"] == 0
    assert call.calls == []


def test_happy_path_groups_by_label():
    call = _completion({
        "verdicts": [
            {"number": "10.88", "label": "legitimate_derivation",
             "derivation": "涨跌额 -10.88，符号由「跌」这个字承担", "evidence_refs": ["E1"]},
            {"number": "18.3", "label": "unsupported", "derivation": "证据里没有回测"},
            {"number": "7", "label": "unclear", "derivation": "证据被裁剪"},
        ],
        "summary": "一条推导、一条编造、一条无法判断",
    })
    audit = {"unsupported_claims": _candidates("10.88", "18.3", "7")}
    result = review.review_turn(audit, [QUOTE_EVIDENCE], user_message="宁德时代为什么跌",
                                completion=call)
    assert result["status"] == "ok"
    assert result["legitimate"] == ["10.88"]
    assert result["unsupported"] == ["18.3"]
    assert result["unclear"] == ["7"]
    assert result["evidence_refs"] == {"E1": "get_quote"}
    assert result["cost"]["llm_calls"] == 1


def test_payload_shape_is_cold_and_references_evidence():
    """冷输入：只有候选 + 证据 + 用户原话 —— 没有作者的推理过程。"""
    call = _completion({"verdicts": []})
    review.adjudicate_claims(_candidates("239.8"), [QUOTE_EVIDENCE],
                             user_message="宁德时代为什么跌", completion=call)

    messages = call.calls[0]["messages"]
    assert messages[0]["role"] == "system"
    assert "不要使用你自己的市场知识" in messages[0]["content"]
    body = json.loads(messages[1]["content"].split("\n\n只输出 JSON")[0])
    assert set(body) == {"user_request", "candidates", "evidence"}
    assert body["candidates"][0]["number"] == "239.8"
    assert body["evidence"][0]["ref"] == "E1"
    assert "2398035" in body["evidence"][0]["text"]
    # 没有工具：它结构上只能裁决
    assert "tools" not in call.calls[0]["kwargs"]


def test_invalid_json_degrades_to_unreviewed():
    def bad_call(messages, **kwargs):
        return {"message": {"role": "assistant", "content": "我觉得没问题啊"}}

    result = review.adjudicate_claims(_candidates("18.3"), [QUOTE_EVIDENCE], completion=bad_call)
    assert result["status"] == "unreviewed"
    assert result["reason"] == "invalid_json"
    assert result["verdicts"] == []


def test_llm_failure_degrades_to_unreviewed():
    def boom(messages, **kwargs):
        raise RuntimeError("模型挂了")

    result = review.adjudicate_claims(_candidates("18.3"), [QUOTE_EVIDENCE], completion=boom)
    assert result["status"] == "unreviewed"
    assert "模型挂了" in result["reason"]


def test_unavailable_llm_canned_message_is_unreviewed():
    """没配 key 时 chat_completion 会返回一句固定中文 —— 那不是判决，必须识别成未审查。"""
    def canned(messages, **kwargs):
        return {"message": {"role": "assistant", "content": "AI 服务暂不可用"}}

    result = review.adjudicate_claims(_candidates("18.3"), [QUOTE_EVIDENCE], completion=canned)
    assert result["status"] == "unreviewed"


def test_garbage_inputs_never_raise():
    assert review.adjudicate_claims(None, None)["status"] == "ok"
    assert review.adjudicate_claims([None, 42, "x"], None,
                                    completion=_completion({"verdicts": []}))["status"] == "ok"
    assert review.review_turn(None, None)["status"] == "ok"
    assert review.review_turn("不是字典", None)["status"] == "ok"


def test_candidates_are_capped():
    """候选太多时只裁决前若干个：无上限的裁决等于把成本交给模型。"""
    call = _completion({"verdicts": []})
    many = _candidates(*[str(index) for index in range(20)])
    review.adjudicate_claims(many, [QUOTE_EVIDENCE], completion=call)
    body = json.loads(call.calls[0]["messages"][1]["content"].split("\n\n只输出 JSON")[0])
    assert len(body["candidates"]) == review.MAX_CANDIDATES


def test_describe_states_it_cannot_act():
    described = review.describe()
    assert described["tools"] == []
    assert described["blocking"] is False
    assert described["requires_evidence_for"] == "legitimate_derivation"


# ==================== 5. 接进一轮对话：只在有候选时才花钱 ====================


def test_review_is_queued_only_when_there_are_candidates(monkeypatch):
    """大多数轮次审计不会报候选 —— 那时裁决者必须连一次调用都不发生。"""
    import agent.react_agent as ra

    submitted = []
    monkeypatch.setattr(ra._REVIEW_EXECUTOR, "submit",
                        lambda fn, *a, **k: submitted.append((fn, a)))
    monkeypatch.setattr(ra, "_run_review", lambda *a: None)

    ra._queue_review({"unsupported_claims": []}, [], "问一句")
    assert submitted == []

    ra._queue_review({"unsupported_claims": [{"number": "18.3", "context": "…"}]}, [], "问一句")
    assert len(submitted) == 1


def test_queued_review_never_raises_when_the_critic_fails(monkeypatch):
    import agent.react_agent as ra

    def boom(*args, **kwargs):
        raise RuntimeError("裁决炸了")

    monkeypatch.setattr(review, "review_turn", boom)

    # 直接跑（而不是入队），确认异常被吞掉 —— 线程池里抛出去只会变成无人看的堆栈
    assert ra._run_review([{"number": "18.3"}], [], "问一句") is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print("PASS", fn.__name__)
