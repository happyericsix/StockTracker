# -*- coding: utf-8 -*-
"""一轮对话的确定性体检（W3 第一步）。

<h3>这个文件在防什么</h3>
最危险的一句话不是"我不知道"，而是**看起来最专业、但数字是编的那句**：
"最大回撤 18.3%、胜率 42%" —— 说得越具体越像真的，而它可能从未被任何一次工具调用
支持过。这类错无法靠"再让模型自查一遍"发现（它就是编的那个部件），
但可以**机械地查出来**：回答里的每个数字，去这一轮模型看到过的文本里找依据。

所以这里钉的不是"审计写得好不好"，而是**审计的判定边界**：

- 该报的必须报：无据数字、违规表述、对着停用数据源重试；
- 不该报的绝不能报：用户自己说过的数字、记忆里注入的数字、策略 JSON 里的参数、
  "约 1500 元"这类合法近似、日期与股票代码。
  误报会迅速摧毁审计的可信度，而可信度是它唯一的价值。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent.react_agent as ra
from agent import tool_audit
from agent import tool_contract as tc


def _entry(tool, envelope, args=None):
    """用真实的账本条目构造器（而不是手写 dict）：这样 meta 里的 code / args 一定对得上。"""
    return ra._tool_log_entry(tool, envelope, args or {})


def _findings(audit, kind):
    return [f for f in audit["findings"] if f["kind"] == kind]


# ==================== 1. 数字溯源 ====================


def test_number_supported_by_tool_result_is_clean():
    audit = tool_audit.audit_turn(
        replies=["600519 现价 1500.0 元。"],
        tool_contents=[{"tool": "get_quote", "content": '{"price": 1500.0}'}],
    )
    assert audit["unsupported_numbers"] == []
    assert _findings(audit, tool_audit.KIND_UNSUPPORTED_CLAIM) == []


def test_number_without_evidence_is_flagged():
    audit = tool_audit.audit_turn(
        replies=["该策略最大回撤 18.3%，胜率 42%。"],
        tool_contents=[{"tool": "get_quote", "content": '{"price": 1500.0}'}],
    )
    assert set(audit["unsupported_numbers"]) == {"18.3", "42"}
    assert audit["verdict"] == "review"
    finding = _findings(audit, tool_audit.KIND_UNSUPPORTED_CLAIM)[0]
    assert finding["severity"] == tool_audit.SEVERITY_MEDIUM


def test_numbers_the_user_said_are_not_claims():
    """用户自己说的数字，模型复述一遍不是编造。"""
    audit = tool_audit.audit_turn(
        replies=["按你说的，止损设在 5%。"],
        user_message="把止损改成 5% 吧",
    )
    assert audit["unsupported_numbers"] == []


def test_numbers_from_injected_memory_are_supported():
    """记忆块里注入的数字（用户之前说过的）同样算依据。"""
    audit = tool_audit.audit_turn(
        replies=["你之前定的止损是 8%。"],
        context_block="<memory>止损: 8</memory>",
    )
    assert audit["unsupported_numbers"] == []


def test_strategy_json_parameters_are_supported():
    """策略 JSON 的参数是校验过的产物，不算"编出来的数字"。"""
    audit = tool_audit.audit_turn(
        replies=["入场条件是 MA20 上穿 MA60，仓位 100%。"],
        strategy_json={"entry": {"conditions": [{"type": "ma_cross", "fast": 20, "slow": 60}]},
                       "position": {"type": "percent", "size_pct": 100}},
    )
    assert audit["unsupported_numbers"] == []


def test_approximate_restatement_is_allowed():
    """模型说"约 1500 元"而工具给的是 1498.5 —— 这是表达，不是编造。"""
    audit = tool_audit.audit_turn(
        replies=["现在大约 1500 元。"],
        tool_contents=[{"tool": "get_quote", "content": '{"price": 1498.5}'}],
    )
    assert audit["unsupported_numbers"] == []


def test_rounding_to_displayed_precision_is_allowed():
    """工具给 18.34、模型写 "18.3%" 属于合法呈现。"""
    audit = tool_audit.audit_turn(
        replies=["最大回撤 18.3%。"],
        tool_contents=[{"tool": "backtest_strategy", "content": '{"max_drawdown_pct": 18.34}'}],
    )
    assert audit["unsupported_numbers"] == []


def test_approximation_does_not_apply_to_small_numbers():
    """近似容差只对量级 ≥100 生效：把 5 写成 6 不是"约等于"，那是错的。"""
    audit = tool_audit.audit_turn(
        replies=["止损是 6%。"],
        tool_contents=[{"tool": "backtest_strategy", "content": '{"stop_loss_pct": 5}'}],
    )
    assert audit["unsupported_numbers"] == ["6"]


def test_dates_and_stock_codes_are_not_claims():
    audit = tool_audit.audit_turn(
        replies=["600519 在 2026-09-17 15:00 的行情如下。"],
        tool_contents=[],
    )
    assert audit["unsupported_numbers"] == []


def test_small_plain_integers_are_not_claims():
    """序号、条数、周期参数不该被审 —— 低召回是刻意的。"""
    audit = tool_audit.audit_turn(
        replies=["有 3 个条件，分别是第 1 条和第 2 条。",],
        tool_contents=[],
    )
    assert audit["unsupported_numbers"] == []


def test_strict_mode_reports_everything():
    audit = tool_audit.audit_turn(
        replies=["第 3 条规则。"],
        tool_contents=[],
        strict=True,
    )
    assert audit["unsupported_numbers"] == ["3"]


def test_system_prompt_is_deliberately_not_support():
    """提示词里的 schema 示例不算依据。

    否则模型抄一个示例数字（100000 / 250 / -8）就永远"有据"，这条检查直接作废。
    所以这里断言：工具什么都没给时，抄示例数字必须被报出来。
    """
    audit = tool_audit.audit_turn(
        replies=["初始资金 100000 元，回看 250 天，止损 -8%。"],
        tool_contents=[],
    )
    assert set(audit["unsupported_numbers"]) == {"100000", "250", "-8"}


# ==================== 2. 合规（确定性黑名单） ====================


def test_forbidden_promise_is_flagged():
    audit = tool_audit.audit_turn(replies=["这只明天必涨，可以满仓。"], tool_contents=[])
    finding = _findings(audit, tool_audit.KIND_COMPLIANCE)
    assert finding and finding[0]["severity"] == tool_audit.SEVERITY_HIGH
    assert audit["verdict"] == "review"


def test_normal_risk_wording_is_not_flagged():
    audit = tool_audit.audit_turn(
        replies=["建议控制仓位，止损 -8%，以上不构成投资建议。"], tool_contents=[])
    assert _findings(audit, tool_audit.KIND_COMPLIANCE) == []


# ==================== 3. 序列异常 ====================


def test_retry_after_source_unavailable_is_high_severity():
    """系统提示明令"不要重试已停用的源" —— 这里检查它到底有没有遵守。"""
    log = [
        _entry("get_news", tc.fail(tc.SOURCE_UNAVAILABLE, "上游不可用"), {"symbol": "600519"}),
        _entry("get_news", tc.fail(tc.SOURCE_UNAVAILABLE, "上游不可用"), {"symbol": "600519"}),
    ]
    audit = tool_audit.audit_turn(replies=["查不到消息。"], tool_log=log)
    finding = _findings(audit, tool_audit.KIND_RETRY_AFTER_UNAVAILABLE)
    assert finding and finding[0]["severity"] == tool_audit.SEVERITY_HIGH


def test_single_failure_without_retry_is_not_flagged():
    log = [_entry("get_news", tc.fail(tc.SOURCE_UNAVAILABLE, "上游不可用"))]
    audit = tool_audit.audit_turn(replies=["这个数据源暂时取不到。"], tool_log=log)
    assert _findings(audit, tool_audit.KIND_RETRY_AFTER_UNAVAILABLE) == []
    assert _findings(audit, tool_audit.KIND_TOOL_FAILURES) == []


def test_repeated_identical_call_is_flagged():
    log = [
        _entry("get_quote", tc.ok({"price": 1.0}), {"symbol": "600519"}),
        _entry("get_quote", tc.ok({"price": 1.0}), {"symbol": "600519"}),
    ]
    audit = tool_audit.audit_turn(replies=["现价 1 元。"], tool_log=log)
    assert _findings(audit, tool_audit.KIND_REPEATED_CALL)


def test_repeated_call_with_different_args_is_fine():
    log = [
        _entry("get_quote", tc.ok({"price": 1.0}), {"symbol": "600519"}),
        _entry("get_quote", tc.ok({"price": 2.0}), {"symbol": "000001"}),
    ]
    audit = tool_audit.audit_turn(replies=["现价 1 元与 2 元。"], tool_log=log)
    assert _findings(audit, tool_audit.KIND_REPEATED_CALL) == []


def test_two_or_more_tool_failures_are_flagged():
    log = [
        _entry("get_history", tc.fail(tc.INSUFFICIENT_DATA, "数据不足")),
        _entry("get_indicators", tc.fail(tc.TOOL_ERROR, "算不出来")),
    ]
    audit = tool_audit.audit_turn(replies=["数据不足。"], tool_log=log)
    finding = _findings(audit, tool_audit.KIND_TOOL_FAILURES)
    assert finding and finding[0]["severity"] == tool_audit.SEVERITY_MEDIUM


def test_truncated_result_is_noted():
    log = [_entry("get_history", tc.ok({"records": []}, truncated=True))]
    seen = [{"tool": "get_history", "content": '{"records": [], "meta": {"truncated": true}}'}]
    audit = tool_audit.audit_turn(replies=["历史如下。"], tool_log=log, tool_contents=seen)
    assert _findings(audit, tool_audit.KIND_TRUNCATED_RESULT)


# ==================== 4. 策略层拒绝与预算 ====================


def test_refusals_are_reported_by_code():
    log = [
        _entry("validate_strategy", tc.fail(tc.POLICY_DENIED, "没有 strategy:compute")),
        _entry("backtest_strategy", tc.fail(tc.POLICY_DENIED, "没有 strategy:compute")),
    ]
    audit = tool_audit.audit_turn(replies=["无法校验。"], tool_log=log)
    finding = _findings(audit, tool_audit.KIND_REFUSALS)[0]
    assert finding["severity"] == tool_audit.SEVERITY_MEDIUM
    assert audit["refused"] == 2
    # 被拒绝不算"工具坏了"
    assert _findings(audit, tool_audit.KIND_TOOL_FAILURES) == []


def test_budget_pressure_at_three_quarters_of_the_cap():
    log = [_entry("get_quote", tc.ok({"price": 1.0}), {"symbol": f"00000{i}"}) for i in range(9)]
    audit = tool_audit.audit_turn(replies=["查完了。"], tool_log=log)
    finding = _findings(audit, tool_audit.KIND_BUDGET_PRESSURE)
    assert finding and finding[0]["severity"] == tool_audit.SEVERITY_MEDIUM


def test_no_budget_pressure_below_the_threshold():
    log = [_entry("get_quote", tc.ok({"price": 1.0}), {"symbol": f"00000{i}"}) for i in range(8)]
    audit = tool_audit.audit_turn(replies=["查完了。"], tool_log=log)
    assert _findings(audit, tool_audit.KIND_BUDGET_PRESSURE) == []


# ==================== 5. 结论与健壮性 ====================


def test_low_severity_alone_stays_clean():
    """只有 low 级问题不算"要人看"：verdict 只有 clean / review 两档。"""
    log = [
        _entry("get_quote", tc.ok({"price": 1.0}), {"symbol": "600519"}),
        _entry("get_quote", tc.ok({"price": 1.0}), {"symbol": "600519"}),
    ]
    seen = [{"tool": "get_quote", "content": '{"price": 1.0}'}]
    audit = tool_audit.audit_turn(replies=["现价 1 元。"], tool_log=log, tool_contents=seen)
    assert _findings(audit, tool_audit.KIND_REPEATED_CALL), "重复调用应当被记下来"
    assert audit["verdict"] == "clean"


def test_audit_never_raises_on_garbage():
    broken = [
        None,
        "not-a-dict",
        {"meta": "{不是 JSON"},
        {"meta": json.dumps({"tool": "get_quote"})},  # 没有 ok 键
        {"content": None},
    ]
    audit = tool_audit.audit_turn(replies=[None, 123, "正常文本"], tool_log=broken,
                                  tool_contents=[None, 42])
    assert audit["verdict"] in ("clean", "review")
    assert isinstance(audit["findings"], list)


def test_counts_are_reported():
    log = [
        _entry("get_quote", tc.ok({"price": 1.0}), {"symbol": "600519"}),
        _entry("get_news", tc.fail(tc.SOURCE_UNAVAILABLE, "上游不可用")),
        _entry("backtest_strategy", tc.fail(tc.BUDGET_EXCEEDED, "重操作超限")),
    ]
    audit = tool_audit.audit_turn(replies=["好的。"], tool_log=log)
    assert (audit["calls"], audit["ok"], audit["failed"], audit["refused"]) == (3, 1, 2, 1)


def test_describe_documents_the_contract():
    described = tool_audit.describe()
    assert described["blocking"] is False
    assert "unsupported_claim" in described["checks"]
    assert described["approx_tolerance"] > 0


# ==================== 6. 端到端：真跑一轮，无据数字必须被抓到 ====================


def _run_agent_with(tool_payload, reply_text, monkeypatch):
    def fake_execute_tool(name, args):
        return tc.ok(tool_payload)

    calls = {"n": 0}

    def fake_completion(messages, tools=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "get_quote", "arguments": '{"symbol": "600519"}'}}]}}
        return {"message": {"role": "assistant", "content": reply_text}}

    monkeypatch.setattr(ra, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(ra.llm_service, "chat_completion", fake_completion)
    monkeypatch.setattr(ra, "_queue_tool_log", lambda *a, **k: None)
    monkeypatch.setattr(ra, "_queue_usage", lambda *a, **k: None)
    monkeypatch.setattr(ra.recall, "load_context", lambda *a, **k: {})
    return ra.run_agent("u1", "600519 现在多少钱")


def test_end_to_end_supported_reply_is_clean(monkeypatch):
    result = _run_agent_with({"price": 1500.0}, "600519 现价 1500 元。", monkeypatch)

    assert result["audit"]["verdict"] == "clean"
    assert result["audit"]["calls"] == 1
    assert result["audit"]["unsupported_numbers"] == []
    assert result["replies"]


def test_end_to_end_fabricated_number_is_caught(monkeypatch):
    """端到端：模型在行情回答里塞了一个工具从未给过的数字 → 必须被记下来。"""
    result = _run_agent_with(
        {"price": 1500.0},
        "600519 现价 1500 元，最大回撤 18.3%，胜率 42%。",
        monkeypatch,
    )

    assert result["audit"]["verdict"] == "review"
    assert set(result["audit"]["unsupported_numbers"]) == {"18.3", "42"}
    # 有据的那个数字不能被误报
    assert "1500" not in result["audit"]["unsupported_numbers"]
    # 而且审计不改变给用户的回复
    assert "最大回撤 18.3%" in "".join(result["replies"])


def test_end_to_end_audit_failure_does_not_break_the_reply(monkeypatch):
    """审计是旁路：它自己炸了也不能影响回答。"""
    def boom(*args, **kwargs):
        raise RuntimeError("audit exploded")

    monkeypatch.setattr(tool_audit, "audit_turn", boom)
    result = _run_agent_with({"price": 1500.0}, "600519 现价 1500 元。", monkeypatch)

    assert "audit" not in result
    assert result["replies"]


# ==================== 7. 契约取法 ====================


def test_error_code_accessor():
    assert tc.error_code(tc.fail(tc.SOURCE_UNAVAILABLE, "x")) == tc.SOURCE_UNAVAILABLE
    assert tc.error_code(tc.ok({"a": 1})) == ""
    assert tc.error_code(None) == ""
    assert tc.error_code({"error": "老式字符串"}) == ""


def test_ledger_meta_carries_the_error_code():
    """账本条目必须带上错误码：审计靠它区分"上游挂了"与"工具坏了"。"""
    failed = json.loads(_entry("get_news", tc.fail(tc.SOURCE_UNAVAILABLE, "上游不可用"))["meta"])
    assert failed["code"] == tc.SOURCE_UNAVAILABLE

    succeeded = json.loads(_entry("get_quote", tc.ok({"price": 1.0}))["meta"])
    assert "code" not in succeeded


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print("PASS", fn.__name__)
