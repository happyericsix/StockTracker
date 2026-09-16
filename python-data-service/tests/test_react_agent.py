# tests/test_react_agent.py
import json
import threading
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent.react_agent as ra
from agent import tool_contract as tc


VALID_STRATEGY = {
    "schema_version": "1.0",
    "name": "MA cross",
    "symbol": "600519",
    "entry": {
        "logic": "all",
        "conditions": [{"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}],
    },
    "exit": {
        "logic": "any",
        "conditions": [{"type": "stop_loss_pct", "value": -8}],
    },
}

INVALID_STRATEGY = {"schema_version": "1.0", "name": "bad"}

BACKTEST = {
    "symbol": "600519",
    "total_return_pct": 1.23,
    "buy_and_hold_return_pct": 0.55,
    "excess_return_pct": 0.68,
    "max_drawdown_pct": -12.34,
    "sharpe_ratio": 0.81,
    "win_rate": 42.5,
    "trade_count": 8,
    "trade_log": [],
    "equity_curve": [],
    "benchmark_equity_curve": [],
}

# 默认替身：所有工具按新契约（信封）返回
BIG_HISTORY = {"records": [{"date": f"2026-01-{i % 28 + 1:02d}", "close": 100 + i} for i in range(400)]}


def fake_execute_tool(name, args):
    if name == "validate_strategy":
        return tc.ok({"valid": True, "error": None, "normalized": args.get("strategy_json")})
    if name == "backtest_strategy":
        return tc.ok({"valid": True, "backtest": BACKTEST})
    if name == "finalize_strategy":
        return tc.ok({"valid": True, "error": None, "strategy_json": args.get("strategy_json")})
    if name == "get_history":
        return tc.ok(BIG_HISTORY)
    return tc.ok({"ok": True})


def _run_with_completion(completion, user_id="u1", message="做一个20日上穿60日买入", history=None):
    original = ra.llm_service.chat_completion
    original_execute_tool = ra.execute_tool
    original_queue = ra._queue_tool_log
    ra.llm_service.chat_completion = completion
    ra.execute_tool = fake_execute_tool
    # 工具结果入账会发 HTTP：单测里不联网，也不让它异步写日志
    ra._queue_tool_log = lambda *args, **kwargs: None
    try:
        return ra.run_agent(user_id, message, history)
    finally:
        ra.llm_service.chat_completion = original
        ra.execute_tool = original_execute_tool
        ra._queue_tool_log = original_queue


def fake_completion(messages, tools=None, temperature=0.2, max_tokens=1200):
    return {"message": {"role": "assistant", "content": "最终回复\n```json\n{\"schema_version\":\"1.0\",\"name\":\"ma\",\"symbol\":\"600519\",\"entry\":{\"logic\":\"all\",\"conditions\":[{\"type\":\"ma_cross\",\"fast\":20,\"slow\":60,\"direction\":\"above\"}]},\"exit\":{\"logic\":\"any\",\"conditions\":[{\"type\":\"stop_loss_pct\",\"value\":-8}]}}\n```"}}


def _tool_call(name, args, call_id="call_1"):
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            }],
        }
    }


def test_run_agent_returns_strategy():
    out = _run_with_completion(fake_completion)
    assert out["strategy_json"] is not None
    assert isinstance(out["backtest"], dict)
    assert len(out["replies"]) >= 1


def test_tool_call_then_final_strategy():
    calls = {"n": 0}

    def fake(messages, tools=None, temperature=0.2, max_tokens=1200):
        calls["n"] += 1
        if calls["n"] == 1:
            return _tool_call("validate_strategy", {"strategy_json": VALID_STRATEGY})
        text = "已生成策略\n```json\n" + json.dumps(VALID_STRATEGY, ensure_ascii=False) + "\n```"
        return {"message": {"role": "assistant", "content": text}}

    out = _run_with_completion(fake)
    assert calls["n"] == 2
    assert isinstance(out["strategy_json"], dict)
    assert out["strategy_json"]["name"] == "MA cross"
    assert len(out["replies"]) >= 1


def test_max_steps_exhaustion():
    calls = {"n": 0}

    def fake(messages, tools=None, temperature=0.2, max_tokens=1200):
        calls["n"] += 1
        return _tool_call("unknown_tool", {}, f"call_{calls['n']}")

    out = _run_with_completion(fake, "u1", "test", [])
    assert calls["n"] == ra.MAX_STEPS
    assert out["strategy_json"] is None
    assert any("步骤" in reply for reply in out["replies"])


def test_llm_exception_fallback():
    def fake(messages, tools=None, temperature=0.2, max_tokens=1200):
        raise RuntimeError("boom")

    out = _run_with_completion(fake, "u1", "test", [])
    assert out["strategy_json"] is None
    assert out["replies"] == ["AI 服务暂不可用，请稍后再试"]


def test_invalid_final_json_retries_then_degrades():
    calls = {"n": 0}

    def fake(messages, tools=None, temperature=0.2, max_tokens=1200):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"message": {"role": "assistant", "content": "策略如下：\n```json\n{invalid json}\n```"}}
        return {"message": {"role": "assistant", "content": "还是不行"}}

    out = _run_with_completion(fake, "u1", "做一个20日上穿60日买入", [])
    assert calls["n"] == 2
    assert out["strategy_json"] is None
    assert any("没理解" in reply for reply in out["replies"])


def test_malformed_tool_arguments_do_not_raise():
    calls = {"n": 0}

    def fake(messages, tools=None, temperature=0.2, max_tokens=1200):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "bad_call",
                        "type": "function",
                        "function": {"name": "unknown_tool", "arguments": None},
                    }],
                }
            }
        text = "已生成策略\n```json\n" + json.dumps(VALID_STRATEGY, ensure_ascii=False) + "\n```"
        return {"message": {"role": "assistant", "content": text}}

    out = _run_with_completion(fake, "u1", "做一个20日上穿60日买入", [])
    assert isinstance(out["strategy_json"], dict)
    assert out["strategy_json"]["name"] == "MA cross"


def test_schema_invalid_final_json_is_not_returned():
    calls = {"n": 0}

    def fake(messages, tools=None, temperature=0.2, max_tokens=1200):
        calls["n"] += 1
        text = "策略如下：\n```json\n" + json.dumps(INVALID_STRATEGY, ensure_ascii=False) + "\n```"
        return {"message": {"role": "assistant", "content": text}}

    out = _run_with_completion(fake, "u1", "做一个20日上穿60日买入", [])
    assert calls["n"] == 2
    assert out["strategy_json"] is None
    assert any("没理解" in reply for reply in out["replies"])


def test_valid_strategy_is_backtested_and_reply_is_clean():
    out = _run_with_completion(fake_completion)
    assert out["strategy_json"] is not None
    assert out["backtest"]["total_return_pct"] == 1.23
    joined_replies = "\n".join(out["replies"])
    assert "```json" not in joined_replies
    assert "回测验证" in joined_replies


def test_backtest_failure_retries_then_degrades():
    calls = {"n": 0}

    def fake(messages, tools=None, temperature=0.2, max_tokens=1200):
        calls["n"] += 1
        text = "策略如下：\n```json\n" + json.dumps(VALID_STRATEGY, ensure_ascii=False) + "\n```"
        return {"message": {"role": "assistant", "content": text}}

    original = ra.llm_service.chat_completion
    original_execute_tool = ra.execute_tool

    def failing_backtest(name, args):
        if name == "backtest_strategy":
            return tc.ok({"valid": False, "error": "data insufficient"})
        return fake_execute_tool(name, args)

    ra.llm_service.chat_completion = fake
    ra.execute_tool = failing_backtest
    try:
        out = ra.run_agent("u1", "做一个20日上穿60日买入", [])
    finally:
        ra.llm_service.chat_completion = original
        ra.execute_tool = original_execute_tool

    assert calls["n"] == 2
    assert out["strategy_json"] is None
    assert any("没理解" in reply for reply in out["replies"])


def test_tool_timeout_returns_structured_error():
    original_timeout = ra.TOOL_TIMEOUT
    original_execute_tool = ra.execute_tool
    ra.TOOL_TIMEOUT = 0.05

    def slow_tool(name, args):
        time.sleep(1)
        return {"ok": True}

    ra.execute_tool = slow_tool
    try:
        result = ra._execute_tool_bounded("slow_tool", {})
    finally:
        ra.TOOL_TIMEOUT = original_timeout
        ra.execute_tool = original_execute_tool

    # 超时是"工具没能给出答案"，必须是结构化信封而不是裸字符串
    assert tc.is_envelope(result)
    assert result["ok"] is False
    assert result["error"]["code"] == tc.TOOL_TIMEOUT
    assert result["error"]["retryable"] is True
    assert "timed out" in result["error"]["message"]


def test_tool_busy_is_reported_when_pool_is_saturated():
    """卡死的任务会一直占着名额，后续调用立刻被拒绝（而不是静默排队把整轮 agent 拖死）。"""
    pool = ra._BoundedToolPool(max_workers=1, capacity=1)
    release = threading.Event()

    def stuck(_name, _args):
        release.wait(3)
        return {"ok": True}

    worker = threading.Thread(target=lambda: pool.submit(stuck, ("stuck", {}), timeout=3))
    worker.start()
    try:
        deadline = time.time() + 1
        while pool.in_flight == 0 and time.time() < deadline:  # 等名额被占住
            time.sleep(0.01)
        saturated = pool.submit(lambda name, args: tc.ok({}), ("fast", {}), timeout=2)
    finally:
        release.set()
        worker.join(timeout=5)

    assert saturated["ok"] is False
    assert saturated["error"]["code"] == tc.TOOL_BUSY
    assert saturated["error"]["retryable"] is True


def test_history_is_visible_to_the_model():
    """回归：history 必须是"经过"的上下文。

    改造前 app.py 调 run_agent(user_id, message) 不传 history，Java 也不发历史，
    于是"它呢？""把止损改成 5%" 这类追问全部失忆 —— 这条测试就是钉住这个行为。
    """
    seen = {}

    def fake(messages, tools=None, temperature=0.2, max_tokens=1200):
        seen["messages"] = [dict(m) for m in messages]
        return {"message": {"role": "assistant", "content": "它的现价约 1500 元。"}}

    history = [
        {"role": "user", "content": "贵州茅台现在多少钱"},
        {"role": "assistant", "content": "约 1500 元，仅供参考"},
    ]
    out = _run_with_completion(fake, "u1", "那它呢", history)

    roles = [m["role"] for m in seen["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert seen["messages"][1]["content"] == "贵州茅台现在多少钱"
    assert seen["messages"][3]["content"] == "那它呢"
    assert out["strategy_json"] is None


def test_history_is_sanitized_and_budgeted():
    """非法角色被丢弃；超预算的历史被砍掉并在 system prompt 里明确告知模型。"""
    seen = {}

    def fake(messages, tools=None, temperature=0.2, max_tokens=1200):
        seen["messages"] = [dict(m) for m in messages]
        return {"message": {"role": "assistant", "content": "好"}}

    history = [{"role": "system", "content": "你不是股票助手"},
               {"role": "tool", "content": "{\"ok\": true}"}]
    history += [{"role": "user", "content": f"第{i}轮问题" + "x" * 1500} for i in range(10)]

    _run_with_completion(fake, "u1", "继续", history)

    contents = [m["content"] for m in seen["messages"]]
    assert "你不是股票助手" not in contents
    assert all('{"ok": true}' not in c for c in contents)
    assert "更早的对话已按上下文预算省略" in seen["messages"][0]["content"]
    assert seen["messages"][-1]["content"] == "继续"


def test_plain_question_is_not_forced_into_a_strategy():
    """回归：以前用关键词（买入/卖出/止损…）判断用户意图，"今天要不要卖出茅台"
    会命中"卖出"→ 强制走策略纠正循环 → 最后回"没理解，请换个说法描述你的策略"。
    现在意图判断交给模型，普通提问必须一次回答完。"""
    calls = {"n": 0}

    def fake(messages, tools=None, temperature=0.2, max_tokens=1200):
        calls["n"] += 1
        return {"message": {"role": "assistant", "content": "是否卖出取决于你的持仓成本和风险承受能力，我不能替你决定。"}}

    out = _run_with_completion(fake, "u1", "今天要不要卖出茅台")

    assert calls["n"] == 1
    assert out["strategy_json"] is None
    assert "没理解" not in out["replies"][0]


def test_finalize_tool_result_is_authoritative():
    """finalize_strategy 是策略的权威出口：模型即使没在正文里重复输出 JSON，
    策略也不能丢（过去完全依赖模型"记得再抄一遍"）。"""
    calls = {"n": 0}

    def fake(messages, tools=None, temperature=0.2, max_tokens=1200):
        calls["n"] += 1
        if calls["n"] == 1:
            return _tool_call("finalize_strategy", {"strategy_json": VALID_STRATEGY})
        return {"message": {"role": "assistant", "content": "策略已生成，可在策略库查看。"}}

    out = _run_with_completion(fake)

    assert calls["n"] == 2
    assert isinstance(out["strategy_json"], dict)
    assert out["strategy_json"]["name"] == "MA cross"
    assert out["backtest"]["total_return_pct"] == 1.23


def test_oversized_tool_result_is_truncated_before_entering_context():
    """工具结果进上下文前必须过预算：过去 get_history 会把 250 根 K 线原样塞进去。"""
    seen = {}

    def fake(messages, tools=None, temperature=0.2, max_tokens=1200):
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        if not tool_messages:
            return _tool_call("get_history", {"symbol": "600519", "days": 120})
        seen["content"] = tool_messages[0]["content"]
        return {"message": {"role": "assistant", "content": "看完了"}}

    _run_with_completion(fake, "u1", "600519 最近走势怎么样", [])

    assert len(seen["content"]) <= tc.DEFAULT_MAX_CHARS
    payload = json.loads(seen["content"])
    assert payload["ok"] is True
    assert payload["meta"]["truncated"] is True
    assert payload["data"]["omitted"]["records"] > 0


def test_tool_message_content_is_always_an_envelope():
    seen = {}

    def fake(messages, tools=None, temperature=0.2, max_tokens=1200):
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        if not tool_messages:
            return _tool_call("validate_strategy", {"strategy_json": VALID_STRATEGY})
        seen["content"] = tool_messages[0]["content"]
        return {"message": {"role": "assistant", "content": "好的"}}

    _run_with_completion(fake, "u1", "看看这个策略", [])

    payload = json.loads(seen["content"])
    assert set(payload) == {"ok", "data", "error", "meta"}
    assert payload["data"]["valid"] is True


def test_model_backtest_is_not_repeated_by_the_loop():
    """整轮里同一个策略只回测一次。

    回归自真跑一轮 agent 的评测记录：模型自己调了一次 backtest_strategy，
    之后我们的 `_run_backtest`（在拿到 finalize/正文策略时兜底回测）又调了一次 ——
    两次参数语义相同，只是模型给的是原始 JSON、内部传的是补齐默认值后的 model_dump()。
    `cache_key` 归一化之后命中同一个键，第二次只复用结果、不再执行。
    """
    from agent import tool_scope
    from agent.tool_registry import DEFAULT_USER_SCOPES

    executions = {"backtest": 0}
    original_execute = ra.execute_tool

    def counting_backtest(name, args):
        if name == "backtest_strategy":
            executions["backtest"] += 1
            return tc.ok({"valid": True, "backtest": dict(BACKTEST, trade_count=7)})
        return fake_execute_tool(name, args)

    calls = {"n": 0}

    def fake(messages, tools=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # 第一步：模型自己发起回测（参数是"精简"的，没有 initial_capital 等默认值）
            return _tool_call("backtest_strategy", {"strategy_json": {
                "schema_version": "1.0", "name": "MA cross", "symbol": "600519",
                "entry": {"logic": "all", "conditions": [
                    {"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}]},
                "exit": {"logic": "any", "conditions": [{"type": "stop_loss_pct", "value": -8}]},
            }})
        # 第二步：给出策略 JSON，循环会再走一次回测
        return {"message": {"role": "assistant", "content":
                            "好了\n```json\n" + json.dumps(VALID_STRATEGY, ensure_ascii=False) + "\n```"}}

    original_completion = ra.llm_service.chat_completion
    original_queue = ra._queue_tool_log
    ra.llm_service.chat_completion = fake
    ra.execute_tool = counting_backtest
    ra._queue_tool_log = lambda *args, **kwargs: None
    token = tool_scope.begin(user_id="u1", session_key="1:2026-09-16", scopes=DEFAULT_USER_SCOPES)
    try:
        out = ra.run_agent("u1", "做个均线策略", [], session_id="1:2026-09-16", memory_user_id=1)
    finally:
        tool_scope.reset(token)
        ra.llm_service.chat_completion = original_completion
        ra.execute_tool = original_execute
        ra._queue_tool_log = original_queue

    assert isinstance(out["strategy_json"], dict)
    assert executions["backtest"] == 1, "同一个策略被回测了两次（缓存键没有归一化）"
    assert out["backtest"]["trade_count"] == 7


def test_legacy_bare_tool_payload_is_normalized():
    """测试替身/未改造的工具返回裸 dict 时，循环里也必须只看到信封。

    T0 起工具调用要先过策略层（授权/预算/审批），所以这里必须像生产一样先建立请求上下文
    —— 没有上下文 = 没有任何权限，`get_quote` 会被 `policy_denied` 拦下（这是有意行为）。
    """
    from agent import tool_scope
    from agent.tool_registry import DEFAULT_USER_SCOPES

    original_execute_tool = ra.execute_tool

    def legacy_tool(name, args):
        return {"quote": {"price": 1500}}

    ra.execute_tool = legacy_tool
    token = tool_scope.begin(user_id="u1", session_key="1:2026-09-16", scopes=DEFAULT_USER_SCOPES)
    try:
        envelope = ra._execute_tool_bounded("get_quote", {"symbol": "600519"})
    finally:
        tool_scope.reset(token)
        ra.execute_tool = original_execute_tool

    assert tc.is_envelope(envelope)
    assert envelope["ok"] is True
    assert envelope["data"] == {"quote": {"price": 1500}}


def test_time_anchor_is_injected_even_without_memory():
    """回归：agent 以前完全不知道"今天"是几号，"去年/上周"只能瞎猜。"""
    seen = {}

    def fake(messages, tools=None, **kwargs):
        seen["system"] = messages[0]["content"]
        return {"message": {"role": "assistant", "content": "好"}}

    _run_with_completion(fake, "u1", "今天几号", [])

    assert "<memory" in seen["system"]
    assert "today=" in seen["system"]


def test_recap_block_is_injected_into_the_system_prompt():
    seen = {}

    def fake(messages, tools=None, **kwargs):
        seen["system"] = messages[0]["content"]
        return {"message": {"role": "assistant", "content": "好"}}

    original = ra.recall.load_context
    ra.recall.load_context = lambda memory_user_id, session_id, **kwargs: {
        "today": "2026-09-16",
        "recaps": [{"sessionKey": "1:2026-09-14", "version": 1, "summary": "用户在调试均线策略"}],
    }
    try:
        _run_with_completion(fake, "u1", "接着说", [])
    finally:
        ra.recall.load_context = original

    assert "更早的对话摘要" in seen["system"]
    assert "用户在调试均线策略" in seen["system"]


def test_memory_failure_degrades_to_time_anchor_only():
    """记忆层炸了也必须能正常回答：退化成"没有记忆"，而不是让用户收不到回复。"""

    def boom(memory_user_id, session_id):
        raise RuntimeError("memory backend down")

    seen = {}

    def fake(messages, tools=None, **kwargs):
        seen["system"] = messages[0]["content"]
        return {"message": {"role": "assistant", "content": "好"}}

    original = ra.recall.load_context
    ra.recall.load_context = boom
    try:
        out = _run_with_completion(fake, "u1", "你好", [])
    finally:
        ra.recall.load_context = original

    assert out["replies"] == ["好"]
    assert "<memory" in seen["system"]  # 时间锚点仍然在


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
