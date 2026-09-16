# -*- coding: utf-8 -*-
"""计量（P1）：先能看见，再能限制。

这一层的价值是"把已经发生的事记下来"，所以测试的重点不是"数字好看"，
而是**数字不能自欺**：被拒绝的调用不能混进调用数、同轮复用要单独记、
LLM 用量缺失时不能猜。跨请求配额的阈值将建立在这些数字上 —— 数字错了，
限流就会限错人。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import metering, tool_contract as tc, tool_pipeline, tool_scope
from agent.tool_pipeline import BoundedToolPool
from agent.tool_registry import DEFAULT_USER_SCOPES, get_spec


def fresh_meter(monkeypatch):
    """每个用例一份干净的计量器：计数是全局状态，用例之间必须隔离。"""
    meter = metering.Meter()
    monkeypatch.setattr(metering, "METER", meter)
    monkeypatch.setattr(metering, "METER", meter)
    return meter


def make_ctx():
    return tool_scope.ToolContext(user_id="u1", session_key="1:2026-09-16",
                                  scopes=frozenset(DEFAULT_USER_SCOPES))


def call(name, args, handler, ctx=None, spec=None, **kwargs):
    """走真实管线调一次（计量挂在管线上，必须从这里进才测得到）。"""
    return tool_pipeline.call_tool_bounded(
        name, args, handler=handler, ctx=ctx or make_ctx(),
        spec=spec if spec is not None else get_spec(name),
        pool=BoundedToolPool(), **kwargs)


# ==================== 计数器本身 ====================

def test_tool_call_is_counted_with_latency_and_size(monkeypatch):
    meter = fresh_meter(monkeypatch)

    meter.record_tool_call("get_quote", ok=True, latency_ms=40, result_chars=300)
    meter.record_tool_call("get_quote", ok=False, code=tc.TOOL_TIMEOUT,
                           latency_ms=200, result_chars=0, cost_class="expensive")

    snapshot = meter.snapshot()
    detail = snapshot["tools"]["get_quote"]
    assert detail["calls"] == 2
    assert detail["ok"] == 1 and detail["failed"] == 1
    assert detail["latency_ms_avg"] == 120
    assert detail["latency_ms_max"] == 200
    assert snapshot["totals"]["tool_calls"] == 2
    assert snapshot["totals"]["tool_failures"] == 1
    assert snapshot["totals"]["expensive_calls"] == 1
    assert snapshot["totals"]["llm_calls"] == 0


def test_refusals_are_counted_separately_from_calls(monkeypatch):
    """被拒绝的调用**不能**进 tool_calls。

    否则"这个工具被用了多少次"就失去意义：模型试了 10 次被拒 10 次，
    在指标上看起来像高频使用 —— 而它一次都没真的执行。
    """
    meter = fresh_meter(monkeypatch)

    meter.record_refusal("get_news", tc.SOURCE_UNAVAILABLE)
    meter.record_refusal("get_news", tc.SOURCE_UNAVAILABLE)
    meter.record_refusal("memory_search", tc.POLICY_DENIED)
    meter.record_tool_call("get_quote", ok=True, latency_ms=10, result_chars=100)

    snapshot = meter.snapshot()
    assert snapshot["totals"]["tool_calls"] == 1, "只有真的执行过的那一次才算调用"
    assert snapshot["totals"]["tool_refused"] == 3
    assert snapshot["totals"]["refusals"] == {tc.POLICY_DENIED: 1, tc.SOURCE_UNAVAILABLE: 2}
    assert snapshot["tools"]["get_news"]["refused"] == 2
    assert snapshot["tools"]["get_news"]["calls"] == 0


def test_turn_delta_subtracts_a_baseline(monkeypatch):
    meter = fresh_meter(monkeypatch)
    meter.record_llm(total_tokens=1000, latency_ms=500)
    baseline = meter.baseline()

    meter.record_llm(total_tokens=500, latency_ms=300)
    meter.record_tool_call("get_quote", ok=True, latency_ms=20, result_chars=100)
    delta = meter.since(baseline)

    assert delta["llm.calls"] == 1
    assert delta["llm.total_tokens"] == 500, "增量必须是差，不是累计"
    assert delta["tools.calls"] == 1


def test_describe_turn_is_readable_and_names_the_refusal_reasons(monkeypatch):
    meter = fresh_meter(monkeypatch)
    meter.record_llm(total_tokens=2400, latency_ms=900)
    meter.record_tool_call("get_news", ok=True, latency_ms=60, result_chars=1500)
    meter.record_tool_reuse("get_quote")
    meter.record_tool_call("backtest_strategy", ok=False, code=tc.TOOL_TIMEOUT, latency_ms=30000)
    meter.record_refusal("get_financial_abstract", tc.BUDGET_EXCEEDED)

    text = metering.describe_turn(meter.snapshot()["totals"])

    assert "LLM 1 次/2400 tokens" in text
    assert "工具 2 次" in text
    assert "同轮复用 1 次" in text
    assert "失败 1 次" in text
    assert "budget_exceeded" in text


def test_parse_usage_never_guesses(monkeypatch):
    """用量缺失就是 0：宁可少算，也不要凭空补一个数。"""
    assert metering.parse_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                           "total_tokens": 15}}) == {
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert metering.parse_usage({"usage": None})["total_tokens"] == 0
    assert metering.parse_usage({"usage": {"total_tokens": "abc"}})["total_tokens"] == 0
    assert metering.parse_usage(None)["total_tokens"] == 0


def test_external_usage_is_derived_not_duplicated(monkeypatch):
    """外部源的用量只有一个来源（从健康状态派生），否则两个数迟早对不上。"""
    meter = fresh_meter(monkeypatch)

    rollup = meter.snapshot()["external"]

    assert set(rollup) >= {"calls", "cache_hits", "cache_entries", "offloads"}


def test_metering_failure_never_breaks_the_caller(monkeypatch):
    """计量是旁路：它自己坏了不能把工具调用带崩。"""
    meter = fresh_meter(monkeypatch)

    def boom(*_args, **_kwargs):
        raise RuntimeError("meter is broken")

    monkeypatch.setattr(meter, "_tool", boom)
    meter.record_tool_call("get_quote", ok=True)  # 不抛异常即为通过


# ==================== 接进管线之后 ====================

def test_pipeline_records_real_calls(monkeypatch):
    meter = fresh_meter(monkeypatch)

    envelope = call("get_quote", {"symbol": "600519"},
                    handler=lambda name, args: tc.ok({"quote": {"最新价": "1"}}))

    assert tc.is_ok(envelope)
    snapshot = meter.snapshot()
    assert snapshot["tools"]["get_quote"]["calls"] == 1
    assert snapshot["tools"]["get_quote"]["result_chars_total"] > 0


def test_pipeline_records_refusal_not_a_call(monkeypatch):
    meter = fresh_meter(monkeypatch)
    ctx = tool_scope.ToolContext(user_id="u1", scopes=frozenset({"market:read"}))
    handler_calls = []

    envelope = call("memory_search", {"query": "止损"},
                    handler=lambda name, args: handler_calls.append(1) or tc.ok({}),
                    ctx=ctx)

    assert envelope["error"]["code"] == tc.POLICY_DENIED
    assert not handler_calls, "被拒绝的调用不该执行 handler"
    snapshot = meter.snapshot()
    assert snapshot["totals"]["tool_refused"] == 1
    assert snapshot["totals"]["tool_calls"] == 0


def test_pipeline_records_turn_cache_reuse_separately(monkeypatch):
    meter = fresh_meter(monkeypatch)
    ctx = make_ctx()
    handler = lambda name, args: tc.ok({"quote": {"最新价": "1"}})

    call("get_quote", {"symbol": "600519"}, handler=handler, ctx=ctx)
    call("get_quote", {"symbol": "600519"}, handler=handler, ctx=ctx)

    snapshot = meter.snapshot()
    assert snapshot["tools"]["get_quote"]["calls"] == 1
    assert snapshot["tools"]["get_quote"]["reused"] == 1


def test_external_source_unavailable_is_a_refusal(monkeypatch):
    """外部源门禁的拒绝要能被数出来（"今天新闻查了几次没查到"是个运维问题）。"""
    meter = fresh_meter(monkeypatch)
    from agent import external_source

    registry = external_source.ExternalSourceRegistry(failure_threshold=1)
    monkeypatch.setattr(external_source, "REGISTRY", registry)
    registry.record_failure("eastmoney.news", "boom")

    envelope = call("get_news", {"symbol": "600519"}, handler=lambda name, args: tc.ok({}))

    assert envelope["error"]["code"] == tc.SOURCE_UNAVAILABLE
    assert meter.snapshot()["totals"]["refusals"] == {tc.SOURCE_UNAVAILABLE: 1}


# ==================== LLM 用量 ====================

def test_llm_calls_are_metered_with_tokens(monkeypatch):
    """LLM 用量必须记在**底层调用**里：记在调用方就意味着以后新增的调用点会漏记。"""
    import llm_service

    meter = fresh_meter(monkeypatch)

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 900, "completion_tokens": 100,
                              "total_tokens": 1000}}

    monkeypatch.setattr(llm_service, "_is_available", lambda: True)
    monkeypatch.setattr(llm_service.requests, "post", lambda *a, **k: _Resp())

    choice = llm_service.chat_completion([{"role": "user", "content": "你好"}])

    assert choice["message"]["content"] == "ok"
    snapshot = meter.snapshot()
    assert snapshot["llm"]["calls"] == 1
    assert snapshot["llm"]["total_tokens"] == 1000
    assert snapshot["totals"]["llm_tokens"] == 1000


def test_llm_failure_is_metered_and_still_raises(monkeypatch):
    """失败也要计数（否则失败率高的时候指标看起来一切正常），但异常必须继续往上抛。"""
    import llm_service

    meter = fresh_meter(monkeypatch)

    def boom(*_args, **_kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr(llm_service, "_is_available", lambda: True)
    monkeypatch.setattr(llm_service.requests, "post", boom)

    try:
        llm_service.chat_completion([{"role": "user", "content": "你好"}])
        raise AssertionError("异常必须继续往上抛，agent 循环靠它走降级路径")
    except OSError:
        pass

    snapshot = meter.snapshot()
    assert snapshot["llm"]["calls"] == 1
    assert snapshot["llm"]["failures"] == 1


# ==================== 一轮的用量入账 ====================

def test_turn_usage_is_written_to_the_ledger_as_a_usage_event(monkeypatch):
    import agent.react_agent as ra

    meter = fresh_meter(monkeypatch)
    captured = {}
    baseline = meter.baseline()
    meter.record_llm(total_tokens=3200, latency_ms=2000)
    meter.record_tool_call("get_news", ok=True, latency_ms=60, result_chars=1500)

    monkeypatch.setattr(ra._TOOL_LOG_EXECUTOR, "submit",
                        lambda fn, *args: captured.update(args=args))

    ra._queue_usage("1", "1:2026-09-16", baseline, metering.turn_started_at())

    _user, _session, entries = captured["args"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["kind"] == "usage"
    assert entry["provenance"] == "system"
    assert "3200 tokens" in entry["content"]
    import json

    meta = json.loads(entry["meta"])
    assert meta["delta"]["llm.total_tokens"] == 3200
    assert "duration_ms" in meta


def test_usage_event_is_skipped_without_identity(monkeypatch):
    """没有身份（例如被直接调用）时不该往账本里写无主事件。"""
    import agent.react_agent as ra

    fresh_meter(monkeypatch)
    submitted = []
    monkeypatch.setattr(ra._TOOL_LOG_EXECUTOR, "submit", lambda *a, **k: submitted.append(a))

    ra._queue_usage(None, None, {}, metering.turn_started_at())

    assert submitted == []


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
