import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent.tool_registry as registry
from agent.tool_registry import TOOL_SCHEMAS, execute_tool


def test_schemas_have_names():
    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert {
        "search_stock", "get_quote", "get_history", "get_indicators",
        "get_risk_metrics", "validate_strategy", "backtest_strategy",
        "finalize_strategy", "get_model_status", "get_model_consensus",
    } <= names


def test_validate_tool():
    out = execute_tool("validate_strategy", {"strategy_json": {
        "schema_version": "1.0", "name": "x", "symbol": "600519",
        "entry": {"logic": "all", "conditions": [{"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}]},
        "exit": {"logic": "any", "conditions": [{"type": "stop_loss_pct", "value": -8}]}}})
    assert out["valid"] is True


def _fake_bars(n=80, start=100.0, step=1.0):
    bars = []
    for i in range(n):
        close = start + step * i
        bars.append({
            "date": f"2026-01-{i+1:02d}",
            "open": close,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "volume": 1000,
        })
    return bars


def test_indicators_and_risk_tools():
    original = registry.get_history
    registry.get_history = lambda symbol: _fake_bars()
    try:
        indicators = execute_tool("get_indicators", {"symbol": "600519"})
        risk = execute_tool("get_risk_metrics", {"symbol": "600519"})
    finally:
        registry.get_history = original

    assert indicators["rsi14"] is not None
    assert "risk_level" in risk
    assert "decision_note" in risk


def test_model_tools_are_diagnostic_only():
    original = registry.get_history
    registry.get_history = lambda symbol: []
    try:
        status = execute_tool("get_model_status", {"symbol": "600519"})
        consensus = execute_tool("get_model_consensus", {"symbol": "600519"})
    finally:
        registry.get_history = original

    for out in (status, consensus):
        assert out["decision_use"] is False
        assert out["confidence"] == "low"
        assert out["consensus"] == "neutral"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
