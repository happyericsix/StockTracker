# -*- coding: utf-8 -*-
"""账户守门员（ACCOUNT-GATE 阶段1）的单元测试。

断言对齐设计文档（docs/ACCOUNT-GATE.md §B3/§B3.1）：
- 解析保守：读不懂 → 空 verdicts + degraded，绝不猜
- 方向不对称的成文纪律必须在提示词里（买入举证/thesis check/沉没成本禁令/止损不可取消）
"""
import json

from agent import account_gate as ag


class TestParseGateDecision:
    def test_valid_multi_verdict(self):
        text = json.dumps({
            "verdicts": [
                {"strategy_id": 1, "action": "veto", "size_fraction": 0.0,
                 "confidence": 0.8, "reason": "满仓下跌中噪音信号", "thesis_check": None},
                {"strategy_id": 2, "action": "partial_sell", "size_fraction": 0.5,
                 "confidence": 0.6, "reason": "near_stop 提前减",
                 "thesis_check": "趋势理由已破位"},
            ],
            "escalate": {"signature": "buy_signal_downtrend_full_position",
                         "reason": "两周内第三次"},
            "next_review": [{"type": "price_below", "value": "6.90"}],
            "summary": "先仓库后市面再信号",
        })
        out = ag.parse_gate_decision(text)
        assert out["degraded"] is False
        assert [v["action"] for v in out["verdicts"]] == ["veto", "partial_sell"]
        assert out["verdicts"][1]["thesis_check"] == "趋势理由已破位"
        assert out["escalate"]["signature"] == "buy_signal_downtrend_full_position"
        assert out["next_review"][0]["type"] == "price_below"

    def test_not_json_is_degraded_not_guesswork(self):
        out = ag.parse_gate_decision("我觉得应该再看看")
        assert out["degraded"] is True and out["verdicts"] == []
        assert out["warnings"]

    def test_unknown_action_becomes_hold(self):
        text = json.dumps({"verdicts": [{"strategy_id": 1, "action": "yolo",
                                         "size_fraction": 1.5}]})
        out = ag.parse_gate_decision(text)
        assert out["verdicts"][0]["action"] == "hold"
        assert out["verdicts"][0]["size_fraction"] == 0.0   # 越界夹回 0
        assert any("yolo" in w for w in out["warnings"])

    def test_verdict_without_strategy_id_dropped(self):
        text = json.dumps({"verdicts": [{"action": "veto"}, {"strategy_id": 3, "action": "hold"}]})
        out = ag.parse_gate_decision(text)
        assert len(out["verdicts"]) == 1 and out["verdicts"][0]["strategy_id"] == 3

    def test_missing_verdicts_treated_as_hold_all(self):
        out = ag.parse_gate_decision(json.dumps({"summary": "没说话"}))
        assert out["verdicts"] == [] and any("verdicts" in w for w in out["warnings"])

    def test_escalate_without_signature_ignored(self):
        text = json.dumps({"verdicts": [], "escalate": {"reason": "没签名"}})
        out = ag.parse_gate_decision(text)
        assert out["escalate"] is None

    def test_next_review_cleaned_and_capped(self):
        text = json.dumps({"verdicts": [], "next_review":
                           [{"type": "price_below", "value": "6.9"}] * 7
                           + [{"no": "type"}]})
        out = ag.parse_gate_decision(text)
        assert out["next_review"] is not None and len(out["next_review"]) <= 5


class TestGateDecideDegraded:
    def test_empty_payload_degrades(self):
        out = ag.gate_decide(None)
        assert out["degraded"] is True and out["gate_tokens"] == 0

    def test_completion_exception_degrades(self):
        def boom(*a, **kw):
            raise RuntimeError("llm down")
        out = ag.gate_decide({"account": {"positions": []}}, completion=boom)
        assert out["degraded"] is True and "调用失败" in out["summary"]


class TestPromptDiscipline:
    """方向不对称（§B3.1）的成文纪律必须在系统提示词里——提示词就是守门员的宪法。"""

    def test_prompt_contains_core_disciplines(self):
        prompt = ag.gate_system_prompt()
        # 买入举证责任在信号
        assert "举证责任在信号" in prompt
        # 卖出先做 thesis check
        assert "thesis_check" in prompt or "当初买入的理由" in prompt
        # 沉没成本禁令（防处置效应扛单）
        assert "沉没成本" in prompt
        # 止损硬通道不经守门员（最多优化不能取消）
        assert "不能取消止损" in prompt or "比你快" in prompt
        # 判断顺序：先仓库后市面再信号
        assert "先仓库" in prompt and "再市面" in prompt
