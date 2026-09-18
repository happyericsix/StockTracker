# -*- coding: utf-8 -*-
"""`chat_completion` 的返回形状：**usage 必须跟着 choice 一起回来**。

<h3>这条为什么要单独钉</h3>
它是"跨模块的缝"：`chat_completion` 返回 `body["choices"][0]`，而 token 用量在**响应体顶层**。
于是"按返回值的 usage 统计成本"的调用方永远记到 0 —— 而 0 看起来像"很便宜"，
比没有数字更糟。实测踩到过：多角色委员会的 token 预算因此**从未生效**，
真跑一轮 8 个角色后 trace 里写着 `total_tokens=0`。

同一类错误在 TradingAgents 上也出现过（它拿不到 DeepSeek 的价目表 → `cost=0` → 预算上限失效）。
所以这里把"usage 必须可达"钉成契约。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm_service  # noqa: E402


class _Response:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def _fake_post(monkeypatch, body):
    def post(url, json=None, headers=None, timeout=None):
        return _Response(body)

    monkeypatch.setattr(llm_service.requests, "post", post)


def _body_with_usage():
    return {
        "choices": [{"message": {"role": "assistant", "content": "好的"}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
    }


def test_the_returned_choice_carries_the_usage(monkeypatch):
    monkeypatch.setattr(llm_service, "_is_available", lambda: True)
    _fake_post(monkeypatch, _body_with_usage())

    choice = llm_service.chat_completion([{"role": "user", "content": "在吗"}])

    assert choice["message"]["content"] == "好的"
    assert choice["usage"]["total_tokens"] == 150, "少了它，调用方统计出来的成本恒为 0"


def test_an_existing_usage_on_the_choice_is_not_overwritten(monkeypatch):
    """有些兼容实现会把 usage 直接放在 choice 里；那就以它为准（不覆盖调用方看得见的东西）。"""
    monkeypatch.setattr(llm_service, "_is_available", lambda: True)
    body = _body_with_usage()
    body["choices"][0]["usage"] = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    _fake_post(monkeypatch, body)

    choice = llm_service.chat_completion([{"role": "user", "content": "在吗"}])

    assert choice["usage"]["total_tokens"] == 2


def test_a_response_without_usage_still_works(monkeypatch):
    """没有 usage 也不能炸：返回形状是契约，缺字段只是缺字段。"""
    monkeypatch.setattr(llm_service, "_is_available", lambda: True)
    _fake_post(monkeypatch, {"choices": [{"message": {"role": "assistant", "content": "x"}}]})

    choice = llm_service.chat_completion([{"role": "user", "content": "在吗"}])

    assert choice["message"]["content"] == "x"
    assert "usage" not in choice


def test_the_committee_reads_that_usage_back(monkeypatch):
    """端到端那一小步：委员会从 choice 里读到的用量要真的进痕迹。"""
    from agent import trading_committee as tc

    def completion(messages, temperature=0.2, max_tokens=800):
        text = ("FINAL TRANSACTION PROPOSAL: **HOLD**"
                if "风控裁决" in messages[0]["content"] else "我的看法")
        return {"message": {"role": "assistant", "content": text},
                "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}}

    bars = [{"date": f"2026-01-{i + 1:02d}", "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0 + i, "volume": 1000} for i in range(30)]
    result = tc.decide("600519", bars[-1]["date"], bars, completion=completion)

    assert result["committee"]["llm_calls"] == len(tc.ROLES)
    assert result["committee"]["total_tokens"] == 150 * len(tc.ROLES)
    assert all(entry["tokens"]["total_tokens"] == 150 for entry in result["committee"]["roles"])


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print("PASS", fn.__name__)
