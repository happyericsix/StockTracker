# -*- coding: utf-8 -*-
"""单笔大亏即时反思（trade-reflection，2026-10-18 二轮）的单测。

与 test_weekly_reflection 同一条纪律钉住：解析保守优先、产物结构化且封顶 1 条、
任何故障退化成 ok=False 而不是抛异常（旁路绝不影响结算主链路）。
"""
from app import trade_reflection_run


class _LLM:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def __call__(self, messages, temperature=0.2, max_tokens=700):
        self.calls.append(messages)
        return {"message": {"content": self.text},
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


PAYLOAD = {
    "strategy": {"name": "趋势跟随", "symbol": "601988", "decisionMode": "agent"},
    "trade": {"date": "2026-10-16", "side": "sell", "price": 6.78, "shares": 14500,
              "roundTripPct": -4.5, "maePct": -6.1, "mfePct": 2.0},
}


def test_missing_payload_fields_fail_closed():
    assert trade_reflection_run({}) ["ok"] is False
    assert trade_reflection_run({"strategy": {"name": "x"}})["ok"] is False   # 缺 trade


def test_produces_at_most_one_lesson():
    text = ('{"lessons": ['
            '{"symptom": "浮盈未锁", "reusable_rule": "设跟踪止盈", "confidence": 0.7},'
            '{"symptom": "第二条", "reusable_rule": "不该出现", "confidence": 0.9}]}')
    out = trade_reflection_run(PAYLOAD, completion=_LLM(text))
    assert out["ok"] is True and len(out["lessons"]) == 1
    assert out["lessons"][0]["reusable_rule"] == "设跟踪止盈"


def test_garbage_output_is_no_lesson_not_crash():
    out = trade_reflection_run(PAYLOAD, completion=_LLM("这笔操作确实有问题，嗯。"))
    assert out["ok"] is True and out["lessons"] == []


def test_injection_discipline_in_prompt():
    llm = _LLM('{"lessons": []}')
    trade_reflection_run(PAYLOAD, completion=llm)
    system = llm.calls[0][0]["content"]
    user = llm.calls[0][1]["content"]
    assert "单笔复盘官" in system and "宁缺毋滥" in system
    assert "601988" in user and "-4.5" in user          # 往返口径必须进材料
