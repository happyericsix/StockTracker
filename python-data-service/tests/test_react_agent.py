# tests/test_react_agent.py
import json
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent.react_agent as ra


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


def fake_execute_tool(name, args):
    if name == "validate_strategy":
        return {"valid": True, "error": None, "normalized": args.get("strategy_json")}
    if name == "backtest_strategy":
        return {
            "valid": True,
            "backtest": {
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
            },
        }
    return {"ok": True}


def _run_with_completion(completion, user_id="u1", message="做一个20日上穿60日买入", history=None):
    original = ra.llm_service.chat_completion
    original_execute_tool = ra.execute_tool
    ra.llm_service.chat_completion = completion
    ra.execute_tool = fake_execute_tool
    try:
        return ra.run_agent(user_id, message, history)
    finally:
        ra.llm_service.chat_completion = original
        ra.execute_tool = original_execute_tool


def fake_completion(messages, tools=None, temperature=0.2, max_tokens=1200):
    return {"message": {"role": "assistant", "content": "最终回复\n```json\n{\"schema_version\":\"1.0\",\"name\":\"ma\",\"symbol\":\"600519\",\"entry\":{\"logic\":\"all\",\"conditions\":[{\"type\":\"ma_cross\",\"fast\":20,\"slow\":60,\"direction\":\"above\"}]},\"exit\":{\"logic\":\"any\",\"conditions\":[{\"type\":\"stop_loss_pct\",\"value\":-8}]}}\n```"}}


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
            return {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "validate_strategy",
                            "arguments": json.dumps({"strategy_json": VALID_STRATEGY}, ensure_ascii=False),
                        },
                    }],
                }
            }
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
        return {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": f"call_{calls['n']}",
                    "type": "function",
                    "function": {"name": "unknown_tool", "arguments": "{}"},
                }],
            }
        }

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
            return {"valid": False, "error": "data insufficient"}
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

    assert isinstance(result, dict)
    assert "timed out" in result["error"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)

