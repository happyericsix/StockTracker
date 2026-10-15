# -*- coding: utf-8 -*-
"""季度策略信（评语半环）的口径用例。

钉住三件事：产物是**写给用户的评语**（verdict 必须有，suggestions 可空）；
解析读不懂就当没有评语（保守优先）；模型不可用 / 调用失败 fail-open，
绝不抛异常 —— Java 侧的数字底座不因评语缺席而少发。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import quarterly_review_run  # noqa: E402


class FakeLLM:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def __call__(self, messages, temperature=0.3, max_tokens=600):
        self.calls.append(messages)
        return {"message": {"role": "assistant", "content": self.content},
                "usage": {"prompt_tokens": 100, "completion_tokens": 0, "total_tokens": 100}}


PAYLOAD = {
    "strategy": {"name": "中行趋势跟随", "symbol": "601988", "decisionMode": "agent"},
    "period": {"label": "2026-Q3", "start": "2026-07-01", "end": "2026-09-30"},
    "stats": {"return_pct": 5.0, "win_rate_pct": 50, "excess_vs_buy_and_hold_pct": -1.2},
    "trades": [],
}


def test_run_returns_letter_and_passes_material():
    llm = FakeLLM('{"verdict": "本季逻辑成立，超额为负主要来自空仓期。", '
                  '"suggestions": "提议：把加仓门槛的回踩幅度从 2% 放宽到 3%（需你确认）。"}')
    result = quarterly_review_run(PAYLOAD, completion=llm)
    assert result["ok"] is True
    assert result["letter"]["verdict"].startswith("本季逻辑成立")
    assert "需你确认" in result["letter"]["suggestions"]
    # 季度材料必须完整进提示词：评语要引用的是事实，不是只有策略名
    user_prompt = llm.calls[0][1]["content"]
    assert "601988" in user_prompt and "win_rate_pct" in user_prompt
    assert "季度复盘官" in llm.calls[0][0]["content"]


def test_suggestions_optional():
    llm = FakeLLM('{"verdict": "样本不足，本季不构成结论。", "suggestions": ""}')
    result = quarterly_review_run(PAYLOAD, completion=llm)
    assert result["ok"] is True
    assert result["letter"]["suggestions"] is None


def test_run_fails_open_on_placeholder_exception_and_garbage():
    r1 = quarterly_review_run(PAYLOAD, completion=FakeLLM("AI 服务暂不可用，请稍后再试"))
    assert r1["ok"] is False and r1["letter"] is None

    def boom(messages, temperature=0.3, max_tokens=600):
        raise RuntimeError("provider down")

    r2 = quarterly_review_run(PAYLOAD, completion=boom)
    assert r2["ok"] is False and "调用失败" in r2["warnings"][0]

    # 散文不是 JSON：读不懂就当没有评语，绝不把散文当 verdict
    r3 = quarterly_review_run(PAYLOAD, completion=FakeLLM("这季度还行吧。"))
    assert r3["ok"] is False and r3["letter"] is None


def test_run_rejects_payload_without_strategy():
    result = quarterly_review_run({"period": {}})
    assert result["ok"] is False and "strategy" in result["warnings"][0]


if __name__ == "__main__":
    for fn in [test_run_returns_letter_and_passes_material,
               test_suggestions_optional,
               test_run_fails_open_on_placeholder_exception_and_garbage,
               test_run_rejects_payload_without_strategy]:
        fn()
        print(f"passed: {fn.__name__}")
