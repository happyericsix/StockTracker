# -*- coding: utf-8 -*-
"""工具执行管线（T0）：策略阶段、拒绝语义、配额。

这一层是"谁能调、什么时候能调"的唯一落点。设计上有两条必须钉住的性质：

1. **拒绝 ≠ 故障**：`policy_denied` / `budget_exceeded` / `needs_approval` 是"这次不该做"，
   `tool_error` / `tool_timeout` 是"做不成"。混在一起，模型会对权限问题反复重试。
2. **被拒绝的调用不消耗配额**：否则一次越权尝试会把后续正常调用挤掉。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import tool_contract as tc, tool_pipeline, tool_scope
from agent.tool_spec import (
    APPROVAL_CONFIRM,
    COST_EXPENSIVE,
    LAYER_ACTION,
    LAYER_INTEGRATION,
    LAYER_PURE,
    SIDE_EFFECT_WRITE,
    ToolRegistry,
    ToolSpec,
)
from agent.tool_registry import DEFAULT_USER_SCOPES, execute_tool, get_spec


def _call(name, args, scopes=None, handler=None, **context_kwargs):
    """模拟生产调用：建立请求上下文 → 走管线。"""
    token = tool_scope.begin(user_id="u1", session_key="1:2026-09-16",
                             scopes=DEFAULT_USER_SCOPES if scopes is None else scopes,
                             **context_kwargs)
    try:
        return tool_pipeline.call_tool_bounded(
            name, args, spec=get_spec(name), handler=handler or execute_tool)
    finally:
        tool_scope.reset(token)


def _echo(name, args):
    return {"echo": args}


# ==================== 参数校验 ====================

def test_missing_required_argument_is_rejected_before_the_handler_runs():
    called = {"n": 0}

    def handler(name, args):
        called["n"] += 1
        return {"ok": True}

    result = _call("get_quote", {}, handler=handler)

    assert result["ok"] is False
    assert result["error"]["code"] == tc.INVALID_ARGS
    assert "symbol" in result["error"]["message"]
    # 关键：参数不合格时根本不该执行工具（避免"带着错参数打真实上游"）
    assert called["n"] == 0


def test_numeric_strings_are_coerced_instead_of_rejected():
    """模型常把数字写成字符串（"days": "60"）。能安全转换就转 —— 拒绝本来能跑的调用是纯损失。"""
    seen = {}

    def handler(name, args):
        seen.update(args)
        return {"records": []}

    result = _call("get_history", {"symbol": "600519", "days": "30"}, handler=handler)

    assert result["ok"] is True
    assert seen["days"] == 30


def test_defaults_are_injected_from_the_schema():
    seen = {}

    def handler(name, args):
        seen.update(args)
        return {"records": []}

    _call("get_history", {"symbol": "600519"}, handler=handler)

    assert seen["days"] == 60   # schema 里的 default，handler 不必再兜


def test_values_are_clamped_to_the_declared_range():
    seen = {}

    def handler(name, args):
        seen.update(args)
        return {"records": []}

    _call("get_history", {"symbol": "600519", "days": 9999}, handler=handler)

    assert seen["days"] == 120   # schema 的 maximum，防止一次拉爆上下文


def test_wrong_type_is_rejected_with_a_readable_message():
    result = _call("get_history", {"symbol": "600519", "days": {"bad": "type"}})

    assert result["ok"] is False
    assert result["error"]["code"] == tc.INVALID_ARGS
    assert "days" in result["error"]["message"]


def test_enum_violation_is_rejected():
    token = tool_scope.begin(user_id="u1", session_key="s", scopes=DEFAULT_USER_SCOPES)
    try:
        result = tool_pipeline.call_tool_bounded(
            "memory_search", {"query": "止损", "fact_type": "不存在的类型"},
            spec=get_spec("memory_search"), handler=_echo)
    finally:
        tool_scope.reset(token)

    assert result["ok"] is False
    assert result["error"]["code"] == tc.INVALID_ARGS
    assert "fact_type" in result["error"]["message"]


# ==================== 授权 ====================

def test_missing_scope_is_policy_denied_not_an_error():
    result = _call("get_quote", {"symbol": "600519"}, scopes=())   # 没有 market:read

    assert result["ok"] is False
    assert result["error"]["code"] == tc.POLICY_DENIED
    assert "market:read" in result["error"]["message"]
    # 策略拒绝不该被当成可重试故障
    assert result["error"]["retryable"] is False


def test_reduced_scope_set_blocks_only_that_capability():
    """按套餐下发权限的雏形：只有 market:read 的用户能用行情工具，用不了记忆工具。"""
    limited = frozenset({"market:read"})

    assert _call("get_quote", {"symbol": "600519"}, scopes=limited, handler=_echo)["ok"] is True
    denied = _call("memory_search", {"query": "止损"}, scopes=limited, handler=_echo)
    assert denied["error"]["code"] == tc.POLICY_DENIED


def test_pure_tools_need_no_special_scope_but_still_declare_one():
    # 当前实现里连 L1 工具也声明了 scope（策略更严）；这里钉住"纯计算层也走授权"
    spec = get_spec("validate_strategy")
    assert spec.layer == LAYER_PURE
    assert spec.scopes
    denied = _call("validate_strategy", {"strategy_json": {"name": "x"}}, scopes=())
    assert denied["error"]["code"] == tc.POLICY_DENIED


# ==================== 预算 ====================

def test_budget_exhaustion_is_reported_with_state():
    token = tool_scope.begin(user_id="u1", session_key="s", scopes=DEFAULT_USER_SCOPES,
                             budget=tool_scope.ToolBudget(max_calls_per_turn=2))
    try:
        first = tool_pipeline.call_tool_bounded("get_quote", {"symbol": "600519"},
                                                spec=get_spec("get_quote"), handler=_echo)
        second = tool_pipeline.call_tool_bounded("get_quote", {"symbol": "600519"},
                                                 spec=get_spec("get_quote"), handler=_echo)
        third = tool_pipeline.call_tool_bounded("get_quote", {"symbol": "600519"},
                                                spec=get_spec("get_quote"), handler=_echo)
    finally:
        tool_scope.reset(token)

    assert first["ok"] and second["ok"]
    assert third["ok"] is False
    assert third["error"]["code"] == tc.BUDGET_EXCEEDED
    assert third["meta"]["budget"]["calls"] == 2


def test_expensive_tools_have_their_own_quota():
    """回测这类重操作单独限次：查行情和跑回测的成本差几个数量级，不该共用一个额度。"""
    token = tool_scope.begin(user_id="u1", session_key="s", scopes=DEFAULT_USER_SCOPES,
                             budget=tool_scope.ToolBudget(max_calls_per_turn=20,
                                                          max_expensive_per_turn=2))
    try:
        results = [
            tool_pipeline.call_tool_bounded("backtest_strategy", {"strategy_json": {"name": "x"}},
                                            spec=get_spec("backtest_strategy"), handler=_echo)
            for _ in range(3)
        ]
    finally:
        tool_scope.reset(token)

    assert results[0]["ok"] and results[1]["ok"]
    assert results[2]["error"]["code"] == tc.BUDGET_EXCEEDED


def test_rejected_calls_do_not_consume_budget():
    """越权尝试不该把正常调用的额度吃掉。"""
    token = tool_scope.begin(user_id="u1", session_key="s", scopes=frozenset({"market:read"}),
                             budget=tool_scope.ToolBudget(max_calls_per_turn=1))
    try:
        denied = tool_pipeline.call_tool_bounded("memory_search", {"query": "止损"},
                                                 spec=get_spec("memory_search"), handler=_echo)
        allowed = tool_pipeline.call_tool_bounded("get_quote", {"symbol": "600519"},
                                                  spec=get_spec("get_quote"), handler=_echo)
    finally:
        tool_scope.reset(token)

    assert denied["error"]["code"] == tc.POLICY_DENIED
    assert allowed["ok"] is True


def test_budget_is_shared_across_tool_calls_in_one_turn():
    calls = []

    def handler(name, args):
        calls.append(name)
        return {"ok": True}

    _call("get_quote", {"symbol": "600519"}, handler=handler)
    _call("get_quote", {"symbol": "600519"}, handler=handler)

    # 两次调用各自建了上下文 → 各自一份配额（说明配额是"每轮"而不是全局）
    assert len(calls) == 2


# ==================== 审批（L3） ====================

ACTION_SPEC = ToolSpec(
    name="start_paper_trading", namespace="strategy",
    description="启动模拟盘（示例：第一个有副作用的动作）",
    parameters={"type": "object", "properties": {"strategy_id": {"type": "integer"}},
                "required": ["strategy_id"]},
    handler=lambda name, args: {"started": args["strategy_id"]},
    layer=LAYER_ACTION, side_effect=SIDE_EFFECT_WRITE, approval=APPROVAL_CONFIRM,
    idempotency="key", scopes=("strategy:write",), retryable=False,
)


def test_action_requires_approval_before_it_executes():
    """有副作用的动作必须先拿到确认 —— 而且这时候**一个字节都不能改**。"""
    executed = {"n": 0}

    def handler(name, args):
        executed["n"] += 1
        return {"started": True}

    token = tool_scope.begin(user_id="u1", session_key="s", scopes=("strategy:write",))
    try:
        result = tool_pipeline.call_tool_bounded("start_paper_trading", {"strategy_id": 7},
                                                 spec=ACTION_SPEC, handler=handler)
    finally:
        tool_scope.reset(token)

    assert result["ok"] is False
    assert result["error"]["code"] == tc.NEEDS_APPROVAL
    assert executed["n"] == 0   # 关键：未确认时零副作用


def test_approved_action_executes():
    token = tool_scope.begin(user_id="u1", session_key="s", scopes=("strategy:write",),
                             approvals=("start_paper_trading",))
    try:
        result = tool_pipeline.call_tool_bounded("start_paper_trading", {"strategy_id": 7},
                                                 spec=ACTION_SPEC, handler=ACTION_SPEC.handler)
    finally:
        tool_scope.reset(token)

    assert result["ok"] is True
    assert result["data"] == {"started": 7}


# ==================== 声明自检 ====================

def test_action_declaration_without_approval_is_caught_by_the_self_check():
    """自检要能把"有副作用却没声明审批"挑出来 —— 否则规范只是一纸空文。"""
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="delete_everything", namespace="danger",
        description="有副作用但没声明审批/幂等/权限",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda name, args: {},
        layer=LAYER_ACTION, side_effect=SIDE_EFFECT_WRITE,
    ))

    problems = registry.validate()

    assert any("approval" in problem for problem in problems)
    assert any("idempotency" in problem or "幂等" in problem for problem in problems)
    assert any("scopes" in problem for problem in problems)


def test_irreversible_action_may_not_be_retryable():
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="place_order", namespace="trade",
        description="不可逆动作 + 声明可重试 = 可能重复下单",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda name, args: {},
        layer=LAYER_ACTION, side_effect="irreversible", approval=APPROVAL_CONFIRM,
        idempotency="key", scopes=("trade:write",), retryable=True,
    ))

    assert any("重试" in problem for problem in registry.validate())


def test_cache_key_must_reference_real_input_fields():
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="cached_thing", namespace="x", description="cache 键写错字段名",
        parameters={"type": "object", "properties": {"symbol": {"type": "string"}},
                    "required": ["symbol"]},
        handler=lambda name, args: {},
        layer=LAYER_INTEGRATION, scopes=("market:read",),
        cache={"ttl_s": 60, "key_fields": ("symbol", "不存在的字段")},
    ))

    assert any("key_fields" in problem for problem in registry.validate())


def test_pure_layer_with_a_network_sized_timeout_is_a_smell():
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="looks_pure_but_waits", namespace="x", description="层选错的信号",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda name, args: {},
        layer=LAYER_PURE, scopes=("market:read",), timeout_s=30.0,
    ))

    assert any("L1" in problem for problem in registry.validate())


def test_duplicate_tool_name_is_refused():
    registry = ToolRegistry()
    spec = ToolSpec(name="dup", namespace="x", description="d",
                    parameters={"type": "object", "properties": {}, "required": []},
                    handler=lambda name, args: {})
    registry.register(spec)

    with pytest.raises(ValueError):
        registry.register(spec)


# ==================== 未登记工具与兼容性 ====================

def test_unregistered_tool_still_executes_through_the_pipeline():
    """边界说明：管线对**未登记**的名字不套策略，只管执行。

    生产里到不了这一步（注册表先返回 unknown_tool）；测试替身需要它，
    而且"策略只作用于已声明的能力"这条边界值得写下来。
    """
    result = tool_pipeline.call_tool_bounded("totally_made_up", {"a": 1}, handler=_echo)

    assert result["ok"] is True
    assert result["data"] == {"echo": {"a": 1}}


def test_unknown_tool_is_rejected_by_the_registry():
    result = _call("nope", {})

    assert result["ok"] is False
    assert result["error"]["code"] == tc.UNKNOWN_TOOL


def test_non_dict_args_are_rejected():
    result = tool_pipeline.call_tool_bounded("x", "not-a-dict", handler=_echo)

    assert result["ok"] is False
    assert result["error"]["code"] == tc.INVALID_ARGS


def test_spec_timeout_is_used_when_none_is_passed():
    def slow(name, args):
        import time
        time.sleep(0.5)
        return {"ok": True}

    token = tool_scope.begin(user_id="u1", session_key="s", scopes=DEFAULT_USER_SCOPES)
    try:
        result = tool_pipeline.call_tool_bounded(
            "get_quote", {"symbol": "600519"}, spec=get_spec("get_quote"), handler=slow,
            pool=tool_pipeline.BoundedToolPool(max_workers=1, capacity=2))
    finally:
        tool_scope.reset(token)

    # get_quote 的声明是 12s，0.5s 的活不该超时（说明超时取自声明而不是某个全局默认值）
    assert result["ok"] is True


def test_explicit_timeout_beats_the_spec():
    def slow(name, args):
        import time
        time.sleep(0.6)
        return {"ok": True}

    token = tool_scope.begin(user_id="u1", session_key="s", scopes=DEFAULT_USER_SCOPES)
    try:
        result = tool_pipeline.call_tool_bounded(
            "get_quote", {"symbol": "600519"}, spec=get_spec("get_quote"), handler=slow,
            timeout=0.05)
    finally:
        tool_scope.reset(token)

    assert result["ok"] is False
    assert result["error"]["code"] == tc.TOOL_TIMEOUT


def test_budget_snapshot_is_available_for_observability():
    token = tool_scope.begin(user_id="u1", session_key="s", scopes=DEFAULT_USER_SCOPES)
    try:
        _call("get_quote", {"symbol": "600519"}, handler=_echo)
        snapshot = tool_pipeline.budget_snapshot()
    finally:
        tool_scope.reset(token)

    assert set(snapshot) == {"calls", "expensive", "max_calls_per_turn", "max_expensive_per_turn"}


# ==================== 同轮结果复用（T1，由 live 评测触发） ====================

def test_identical_read_call_in_the_same_turn_is_reused():
    """真调模型的评测里看到模型同一轮用同样的参数调了两次 memory_search。

    两次调用 = 两倍延迟与配额，而答案完全一样，所以同轮同参只算一次。
    """
    calls = {"n": 0}

    def handler(name, args):
        calls["n"] += 1
        return {"quote": {"price": 1500}}

    token = tool_scope.begin(user_id="u1", session_key="s", scopes=DEFAULT_USER_SCOPES)
    try:
        first = tool_pipeline.call_tool_bounded("get_quote", {"symbol": "600519"},
                                                spec=get_spec("get_quote"), handler=handler)
        second = tool_pipeline.call_tool_bounded("get_quote", {"symbol": "600519"},
                                                 spec=get_spec("get_quote"), handler=handler)
    finally:
        tool_scope.reset(token)

    assert calls["n"] == 1                      # 真正执行只发生一次
    assert first["data"] == second["data"]
    assert second["meta"]["reused_in_turn"] is True
    assert "reused_in_turn" not in (first["meta"] or {})


def test_different_args_are_not_reused():
    calls = {"n": 0}

    def handler(name, args):
        calls["n"] += 1
        return {"quote": {"symbol": args.get("symbol")}}

    token = tool_scope.begin(user_id="u1", session_key="s", scopes=DEFAULT_USER_SCOPES)
    try:
        tool_pipeline.call_tool_bounded("get_quote", {"symbol": "600519"},
                                        spec=get_spec("get_quote"), handler=handler)
        tool_pipeline.call_tool_bounded("get_quote", {"symbol": "000001"},
                                        spec=get_spec("get_quote"), handler=handler)
    finally:
        tool_scope.reset(token)

    assert calls["n"] == 2


def test_failures_are_not_reused():
    """失败可能是瞬时的（超时、限流），不该在整轮里被固化成"这个工具坏了"。"""
    calls = {"n": 0}

    def flaky(name, args):
        calls["n"] += 1
        return tc.fail(tc.TOOL_TIMEOUT, "upstream slow", retryable=True)

    token = tool_scope.begin(user_id="u1", session_key="s", scopes=DEFAULT_USER_SCOPES)
    try:
        tool_pipeline.call_tool_bounded("get_quote", {"symbol": "600519"},
                                        spec=get_spec("get_quote"), handler=flaky)
        tool_pipeline.call_tool_bounded("get_quote", {"symbol": "600519"},
                                        spec=get_spec("get_quote"), handler=flaky)
    finally:
        tool_scope.reset(token)

    assert calls["n"] == 2


def test_side_effect_tools_are_never_reused():
    """有副作用的动作被"复用"就是漏执行 —— 这是同轮复用最危险的误用方式。"""
    executed = {"n": 0}

    def handler(name, args):
        executed["n"] += 1
        return {"started": True}

    token = tool_scope.begin(user_id="u1", session_key="s", scopes=("strategy:write",),
                             approvals=("start_paper_trading",))
    try:
        for _ in range(2):
            tool_pipeline.call_tool_bounded("start_paper_trading", {"strategy_id": 7},
                                            spec=ACTION_SPEC, handler=handler)
    finally:
        tool_scope.reset(token)

    assert executed["n"] == 2


def test_semantically_equal_args_hit_the_same_cache_key():
    """键要按**参数语义**算，不是按原文算。

    这是真跑一轮 agent 时抓到的低效：模型给的策略 JSON 与内部归一化后的 model_dump()
    差一批默认值，按原文做键 → 同一次回测被执行两次（模型自己一次、我们的 _run_backtest 一次）。
    `cache_key` 让"补齐了默认值的同一份配置"命中同一个键。
    """
    import hashlib
    import json

    normalized = {"name": "ma", "symbol": "600519", "initial_capital": 100000}
    spec = ToolSpec(
        name="heavy_compute", namespace="x", description="重操作",
        parameters={"type": "object", "properties": {"config": {"type": "object"}},
                    "required": ["config"]},
        handler=lambda name, args: {"done": True},
        layer=LAYER_INTEGRATION, scopes=("strategy:compute",), cost_class=COST_EXPENSIVE,
        # 归一化：忽略调用方有没有补齐默认值，只看配置内容
        cache_key=lambda args: hashlib.sha256(
            json.dumps({**normalized, **(args.get("config") or {})}, sort_keys=True).encode()
        ).hexdigest()[:16],
    )
    executed = {"n": 0}

    def handler(name, args):
        executed["n"] += 1
        return {"done": True}

    token = tool_scope.begin(user_id="u1", session_key="s", scopes=("strategy:compute",))
    try:
        # 第一次：模型给的"精简"参数；第二次：内部补齐默认值后的等价参数
        first = tool_pipeline.call_tool_bounded("heavy_compute", {"config": {"name": "ma"}},
                                                spec=spec, handler=handler)
        second = tool_pipeline.call_tool_bounded(
            "heavy_compute", {"config": {"name": "ma", "symbol": "600519",
                                         "initial_capital": 100000}},
            spec=spec, handler=handler)
    finally:
        tool_scope.reset(token)

    assert executed["n"] == 1
    assert second["meta"]["reused_in_turn"] is True
    assert "reused_in_turn" not in (first["meta"] or {})


def test_broken_cache_key_normalizer_falls_back_instead_of_failing():
    """归一化函数抛异常时，退回原文比较 —— 绝不能因为缓存优化而不执行工具。"""
    def broken(args):
        raise RuntimeError("normalizer is broken")

    spec = ToolSpec(
        name="flaky_key", namespace="x", description="归一化会炸",
        parameters={"type": "object", "properties": {"a": {"type": "integer"}},
                    "required": ["a"]},
        handler=lambda name, args: {"ok": True},
        layer=LAYER_INTEGRATION, scopes=("market:read",), cache_key=broken,
    )
    executed = {"n": 0}

    def handler(name, args):
        executed["n"] += 1
        return {"ok": True}

    token = tool_scope.begin(user_id="u1", session_key="s", scopes=("market:read",))
    try:
        first = tool_pipeline.call_tool_bounded("flaky_key", {"a": 1}, spec=spec, handler=handler)
        second = tool_pipeline.call_tool_bounded("flaky_key", {"a": 1}, spec=spec, handler=handler)
    finally:
        tool_scope.reset(token)

    assert first["ok"] and second["ok"]
    assert executed["n"] == 1   # 退回原文比较后仍能正确复用
