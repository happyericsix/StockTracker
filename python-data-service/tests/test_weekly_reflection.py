# -*- coding: utf-8 -*-
"""周反思（P2 经验回流闭环）的口径用例。

钉住三件事：产物是**结构化教训**（symptom + 可执行规则，缺一不可）；
解析读不懂就当没有教训（保守优先，与 trading_decision 同一条纪律）；
模型服务不可用 / 调用失败 fail-open 成"没有教训"，绝不抛异常。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import _parse_weekly_lessons, weekly_reflection_run  # noqa: E402


class FakeLLM:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def __call__(self, messages, temperature=0.2, max_tokens=800):
        self.calls.append(messages)
        return {"message": {"role": "assistant", "content": self.content},
                "usage": {"prompt_tokens": 100, "completion_tokens": 0, "total_tokens": 100}}


PAYLOAD = {"strategy": {"name": "中行趋势跟随", "symbol": "601988", "decisionMode": "agent"},
           "experience": {"evaluatedTrades": 3, "winRatePct": 33.3},
           "trades": [], "committee": []}


def test_lessons_require_symptom_and_reusable_rule():
    """缺 symptom 或 reusable_rule 的条目是散文不是教训，必须被丢弃。"""
    text = ('```json\n{"lessons": ['
            '{"symptom": "追高买入连续方向错", "reusable_rule": "首仓不超过 50%，等回踩确认", "confidence": 0.7},'
            '{"symptom": "只有症状没有规则", "confidence": 0.5},'
            '{"reusable_rule": "只有规则没有症状", "confidence": 0.5}'
            ']}\n```')
    lessons = _parse_weekly_lessons(text)
    assert len(lessons) == 1
    assert lessons[0]["reusable_rule"].startswith("首仓不超过")
    assert lessons[0]["confidence"] == 0.7


def test_unparseable_text_yields_no_lessons():
    assert _parse_weekly_lessons("我觉得这周做得还行。") == []
    assert _parse_weekly_lessons("") == []


def test_run_returns_structured_lessons_and_passes_payload():
    llm = FakeLLM('{"lessons": [{"symptom": "止损后立刻反手买回", '
                  '"context": "本周 2 笔，对齐 -3.1%", '
                  '"reusable_rule": "止损当日不再开新仓", "confidence": 0.8}]}')
    result = weekly_reflection_run(PAYLOAD, completion=llm)
    assert result["ok"] is True and len(result["lessons"]) == 1
    # 材料必须完整进提示词：反思要看的是战绩与过程，不是只有名字
    user_prompt = llm.calls[0][1]["content"]
    assert "601988" in user_prompt and "winRatePct" in user_prompt
    assert "中行趋势跟随" in llm.calls[0][0]["content"] or "周复盘官" in llm.calls[0][0]["content"]


def test_run_fails_open_on_placeholder_and_exception():
    result = weekly_reflection_run(PAYLOAD, completion=FakeLLM("AI 服务暂不可用，请稍后再试"))
    assert result["ok"] is False and result["lessons"] == []

    def boom(messages, temperature=0.2, max_tokens=800):
        raise RuntimeError("provider down")

    result2 = weekly_reflection_run(PAYLOAD, completion=boom)
    assert result2["ok"] is False and result2["lessons"] == []


def test_run_rejects_payload_without_strategy():
    result = weekly_reflection_run({"experience": {}})
    assert result["ok"] is False and "strategy" in result["warnings"][0]


if __name__ == "__main__":
    for fn in [test_lessons_require_symptom_and_reusable_rule,
               test_unparseable_text_yields_no_lessons,
               test_run_returns_structured_lessons_and_passes_payload,
               test_run_fails_open_on_placeholder_and_exception,
               test_run_rejects_payload_without_strategy]:
        fn()
        print(f"passed: {fn.__name__}")
