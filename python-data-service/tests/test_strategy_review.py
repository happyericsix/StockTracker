# -*- coding: utf-8 -*-
"""策略审查的离线测试：**确定性那一半必须一条不漏**。

这一半不用模型，所以没有"偶尔判错"的借口。它要抓的是这样一类策略：
**能通过 schema 校验、回测却一笔都不成交（或刚买就卖）** —— 用户看到的只是
"这策略没反应"，而原因藏在引擎的语义里（止损符号、指标窗口、互斥条件）。

至于"用户说止损 8%，JSON 里却是 -5%"那一半，必须真调模型，见 tests/test_review_eval.py。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import strategy_review as sr


def _base(**overrides):
    config = {
        "schema_version": "1.0",
        "name": "MA cross",
        "symbol": "600519",
        "initial_capital": 100000,
        "data": {"period": "day", "lookback_days": 250},
        "position": {"type": "percent", "size_pct": 100},
        "entry": {"logic": "all",
                  "conditions": [{"type": "ma_cross", "fast": 20, "slow": 60,
                                  "direction": "above"}]},
        "exit": {"logic": "any",
                 "conditions": [{"type": "ma_cross", "fast": 20, "slow": 60,
                                 "direction": "below"},
                                {"type": "stop_loss_pct", "value": -8}]},
        "risk": {"commission_pct": 0.1, "slippage_pct": 0.1},
    }
    config.update(overrides)
    return config


def _kinds(findings):
    return {item["kind"] for item in findings}


def _one(findings, kind):
    return [item for item in findings if item["kind"] == kind]


# ==================== 1. 干净策略不该被报 ====================


def test_valid_strategy_has_no_structural_findings():
    assert sr.structural_findings(_base()) == []


def test_garbage_config_does_not_raise():
    assert sr.structural_findings(None) == []
    assert sr.structural_findings("不是字典") == []
    assert sr.structural_findings({}) == []
    assert sr.structural_findings({"entry": {"conditions": [None, 42]}}) == []


# ==================== 2. 符号写反：恒为真 → 刚买就卖 ====================


def test_positive_stop_loss_is_flagged():
    config = _base(exit={"logic": "any", "conditions": [{"type": "stop_loss_pct", "value": 8}]})
    finding = _one(sr.structural_findings(config), "sign_flipped_condition")[0]
    assert finding["severity"] == sr.SEVERITY_HIGH
    assert finding["path"] == "exit.conditions[0].value"
    assert "恒为真" in finding["detail"]


def test_non_positive_take_profit_is_flagged():
    config = _base(exit={"logic": "any", "conditions": [{"type": "take_profit_pct", "value": -5}]})
    assert _one(sr.structural_findings(config), "sign_flipped_condition")


def test_non_positive_trailing_stop_is_flagged():
    config = _base(exit={"logic": "any", "conditions": [{"type": "trailing_stop_pct", "value": 0}]})
    assert _one(sr.structural_findings(config), "sign_flipped_condition")


# ==================== 3. 窗口不足：恒为假 → 永不成交 ====================


def test_lookback_equal_to_slow_window_can_never_fire():
    """60 根 K 线算不出 MA60，更算不出"上穿"（需要 61 根）。这是最阴的一类。"""
    config = _base(data={"period": "day", "lookback_days": 60})
    finding = _one(sr.structural_findings(config), "insufficient_lookback")[0]
    assert finding["severity"] == sr.SEVERITY_HIGH
    assert finding["path"] == "data.lookback_days"
    assert "一笔都不会成交" in finding["detail"]


def test_lookback_below_engine_floor_is_flagged():
    config = _base(data={"period": "day", "lookback_days": 20})
    finding = _one(sr.structural_findings(config), "insufficient_lookback")[0]
    assert "数据不足" in finding["detail"]


def test_tight_but_sufficient_lookback_is_only_a_note():
    config = _base(data={"period": "day", "lookback_days": 80})
    finding = _one(sr.structural_findings(config), "tight_lookback")[0]
    assert finding["severity"] == sr.SEVERITY_LOW


def test_required_bars_follows_the_indicators_used():
    # 注意每次都要换掉 exit：基准配置里的 ma_cross 60 会把下限顶到 61
    plain_exit = {"logic": "any", "conditions": [{"type": "stop_loss_pct", "value": -8}]}
    assert sr.required_bars(_base()) == 61                      # ma_cross slow=60 → 61
    assert sr.required_bars(_base(entry={"logic": "all", "conditions": [
        {"type": "macd_cross", "direction": "above"}]}, exit=plain_exit)) == 35
    assert sr.required_bars(_base(entry={"logic": "all", "conditions": [
        {"type": "rsi_below", "value": 30}]}, exit=plain_exit)) == 21


def test_required_bars_counts_price_versus_ma_windows():
    """价格与均线的条件同样吃窗口：回看不够照样"永不成交"。"""
    entry = {"logic": "all",
             "conditions": [{"type": "price_cross_ma", "window": 120, "direction": "above"}]}
    plain_exit = {"logic": "any", "conditions": [{"type": "stop_loss_pct", "value": -8}]}

    assert sr.required_bars(_base(entry=entry, exit=plain_exit)) == 121

    tight = _base(entry=entry, exit=plain_exit, data={"period": "day", "lookback_days": 100})
    finding = _one(sr.structural_findings(tight), "insufficient_lookback")[0]
    assert finding["severity"] == sr.SEVERITY_HIGH


def test_price_ma_state_conditions_are_recognised_by_the_structural_checks():
    """结构检查不认识新类型就会把它当噪声漏过去 —— 这里确认它认得。"""
    config = _base(entry={"logic": "all", "conditions": [
        {"type": "price_above_ma", "window": 20}]},
        exit={"logic": "any", "conditions": [{"type": "price_below_ma", "window": 20}]})
    assert sr.structural_findings(config) == []


# ==================== 4. 不可能成立的条件与组合 ====================


def test_rsi_out_of_range_is_flagged():
    config = _base(entry={"logic": "any", "conditions": [{"type": "rsi_above", "value": 120}]})
    assert _one(sr.structural_findings(config), "impossible_condition")

    config = _base(entry={"logic": "any", "conditions": [{"type": "rsi_below", "value": -5}]})
    assert _one(sr.structural_findings(config), "impossible_condition")


def test_impossible_all_combination_is_flagged():
    """logic=all 同时要求"高于 100"与"低于 90" —— 逻辑上永不成立。"""
    config = _base(entry={"logic": "all", "conditions": [
        {"type": "price_above", "value": 100},
        {"type": "price_below", "value": 90}]})
    finding = _one(sr.structural_findings(config), "impossible_combination")[0]
    assert finding["severity"] == sr.SEVERITY_HIGH
    assert finding["path"] == "entry.logic"


def test_satisfiable_all_combination_is_not_flagged():
    """高于 90 与低于 100 是可以同时成立的 —— 不能误报。"""
    config = _base(entry={"logic": "all", "conditions": [
        {"type": "price_above", "value": 90},
        {"type": "price_below", "value": 100}]})
    assert _one(sr.structural_findings(config), "impossible_combination") == []


def test_inverted_ma_cross_is_flagged():
    config = _base(entry={"logic": "all", "conditions": [
        {"type": "ma_cross", "fast": 60, "slow": 20, "direction": "above"}]})
    assert _one(sr.structural_findings(config), "inverted_ma_cross")


# ==================== 5. 复制粘贴与仓位 ====================


def test_identical_condition_in_both_groups_is_flagged():
    same = {"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}
    config = _base(entry={"logic": "all", "conditions": [same]},
                   exit={"logic": "any", "conditions": [dict(same)]})
    finding = _one(sr.structural_findings(config), "duplicate_condition")[0]
    assert finding["path"] == "exit.conditions[0]"


def test_different_direction_is_not_a_duplicate():
    assert _one(sr.structural_findings(_base()), "duplicate_condition") == []


def test_zero_and_oversized_position_are_flagged():
    for size in (0, -10, 120):
        config = _base(position={"type": "percent", "size_pct": size})
        finding = _one(sr.structural_findings(config), "invalid_position_size")
        assert finding and finding[0]["severity"] == sr.SEVERITY_HIGH, size


def test_full_position_ignores_size_pct():
    config = _base(position={"type": "full", "size_pct": 0})
    assert _one(sr.structural_findings(config), "invalid_position_size") == []


def test_non_positive_capital_is_flagged():
    assert _one(sr.structural_findings(_base(initial_capital=0)), "invalid_capital")


# ==================== 6. 证据是 JSON 路径，且必须真实存在 ====================


def test_path_validation():
    config = _base()
    assert sr._path_exists(config, "entry.conditions[0].value") is False  # ma_cross 没有 value
    assert sr._path_exists(config, "entry.conditions[0].slow") is True
    assert sr._path_exists(config, "exit.conditions[1].value") is True
    assert sr._path_exists(config, "entry.conditions[9]") is False
    assert sr._path_exists(config, "entry.conditions") is True           # 列表本身也算存在
    assert sr._path_exists(config, "positions.size_pct") is False
    assert sr._path_exists(config, "entry.conditions[0].nope") is False
    assert sr._path_exists(config, "") is False
    assert sr._path_exists(config, None) is False


def test_findings_with_unknown_path_are_dropped():
    kept, dropped = sr._filter_findings(
        [{"kind": "requirement_mismatch", "severity": "high", "detail": "x",
          "path": "entry.conditions[9].value"}], _base())
    assert kept == []
    assert dropped[0]["reason"] == "unknown_path"


def test_requirements_accusing_without_a_valid_path_are_dropped():
    """说"用户要的东西没实现"却指不出位置 —— 这种指控不能采信。"""
    kept = sr._filter_requirements(
        [{"text": "止损 8%", "status": "violated", "path": "nope.value"},
         {"text": "止损 8%", "status": "satisfied", "path": ""},
         {"text": "止损 8%", "status": "violated", "path": "exit.conditions[1].value"}],
        _base())
    assert [item["status"] for item in kept] == ["satisfied", "violated"]


# ==================== 7. 判决一致性 ====================


def test_accept_with_a_high_finding_is_downgraded():
    findings = [{"kind": "x", "severity": sr.SEVERITY_HIGH}]
    assert sr._resolve_verdict("accept", findings) == sr.VERDICT_REVISE
    assert sr._resolve_verdict("accept", [{"severity": sr.SEVERITY_LOW}]) == sr.VERDICT_ACCEPT
    assert sr._resolve_verdict("胡说", []) == sr.VERDICT_REVISE


# ==================== 8. 入口：成本、冷输入、失败退化 ====================


def _completion(payload):
    calls = []

    def call(messages, **kwargs):
        calls.append({"messages": messages, "kwargs": kwargs})
        return {"message": {"role": "assistant",
                            "content": json.dumps(payload, ensure_ascii=False)}}

    call.calls = calls
    return call


def test_structural_only_when_there_is_no_request():
    """没有用户原话就没有"对不对得上"可言 —— 不许花这次钱。"""
    call = _completion({"verdict": "accept"})
    result = sr.review_strategy("", _base(), completion=call)
    assert result["status"] == sr.STATUS_OK
    assert result["cost"]["llm_calls"] == 0
    assert call.calls == []


def test_structural_findings_force_revise_even_without_a_request():
    result = sr.review_strategy("", _base(data={"period": "day", "lookback_days": 20}))
    assert result["verdict"] == sr.VERDICT_REVISE
    assert _one(result["findings"], "insufficient_lookback")


def test_semantic_findings_are_merged_with_structural_ones():
    call = _completion({
        "requirements": [{"text": "止损 8%", "status": "violated",
                          "path": "exit.conditions[1].value", "note": "JSON 是 -5"}],
        "findings": [{"kind": "requirement_mismatch", "severity": "high",
                      "detail": "用户要 8%，JSON 是 5%", "path": "exit.conditions[1].value"}],
        "verdict": "accept",   # 故意给错的判决，必须被改成 revise
        "summary": "止损不一致",
    })
    config = _base(data={"period": "day", "lookback_days": 60})   # 顺带带一条结构问题
    result = sr.review_strategy("做个20日均线上穿60日买入、止损8%的策略", config, completion=call)

    assert result["status"] == sr.STATUS_OK
    assert result["cost"]["llm_calls"] == 1
    assert _kinds(result["findings"]) == {"insufficient_lookback", "requirement_mismatch"}
    assert result["requirements"][0]["status"] == sr.VIOLATED
    assert result["verdict"] == sr.VERDICT_REVISE


def test_payload_is_cold_and_has_no_tools():
    call = _completion({"verdict": "accept", "requirements": [], "findings": []})
    sr.review_strategy("止损 8%", _base(), completion=call)

    messages = call.calls[0]["messages"]
    assert "不要使用你自己的市场知识" in messages[0]["content"]
    body = json.loads(messages[1]["content"].split("\n\n只输出 JSON")[0])
    assert set(body) == {"user_request", "strategy_json", "structural_findings"}
    assert body["user_request"] == "止损 8%"
    assert "tools" not in call.calls[0]["kwargs"]


def test_invalid_json_and_failures_degrade_to_unreviewed():
    def bad(messages, **kwargs):
        return {"message": {"role": "assistant", "content": "我觉得挺好的"}}

    result = sr.review_strategy("止损 8%", _base(), completion=bad)
    assert result["status"] == sr.STATUS_UNREVIEWED
    assert result["reason"] == "invalid_json"

    def boom(messages, **kwargs):
        raise RuntimeError("模型挂了")

    result = sr.review_strategy("止损 8%", _base(), completion=boom)
    assert result["status"] == sr.STATUS_UNREVIEWED
    assert "模型挂了" in result["reason"]


def test_garbage_inputs_never_raise():
    assert sr.review_strategy(None, None)["status"] == sr.STATUS_OK
    assert sr.review_strategy("x", "不是字典")["status"] == sr.STATUS_OK
    assert sr.review_strategy("x", _base(),
                              completion=_completion({"findings": "不是列表"}))["status"] == sr.STATUS_OK


def test_describe_states_the_split_and_that_it_cannot_act():
    described = sr.describe()
    assert "sign_flipped_condition" in described["deterministic_checks"]
    assert described["evidence"] == "json_path"
    assert described["tools"] == []
    assert described["blocking"] is False


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print("PASS", fn.__name__)
