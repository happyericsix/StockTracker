import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
import akshare_client
import agent.tool_registry
import app as main

VALID_STRATEGY = {
    "schema_version": "1.0",
    "name": "ma",
    "symbol": "600519",
    "entry": {"logic": "all", "conditions": [
        {"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}
    ]},
    "exit": {"logic": "any", "conditions": [
        {"type": "stop_loss_pct", "value": -8}
    ]},
}

def make_bars(n=30, start=100.0, step=1.0):
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

def test_validate_endpoint():
    c = TestClient(main.app)
    payload = {"strategy_json": VALID_STRATEGY}
    r = c.post("/api/v1/strategies/validate", json=payload)
    assert r.status_code == 200
    assert r.json()["valid"] is True

def test_validate_rejects_non_object_strategy_json():
    c = TestClient(main.app)
    r = c.post("/api/v1/strategies/validate", json={"strategy_json": []})
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert body["error"] == "strategy_json must be an object"
    assert body["normalized"] is None

def test_backtest_with_fixed_history():
    c = TestClient(main.app)
    original = akshare_client.get_history
    akshare_client.get_history = lambda symbol, *args, **kwargs: make_bars()
    try:
        r = c.post("/api/v1/strategies/backtest", json={"strategy_json": VALID_STRATEGY})
    finally:
        akshare_client.get_history = original
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True
    assert "backtest" in body

def test_evaluate_bar_empty_history():
    c = TestClient(main.app)
    original = akshare_client.get_history
    akshare_client.get_history = lambda symbol, *args, **kwargs: None
    try:
        r = c.post("/api/v1/strategies/evaluate-bar", json={"strategy_json": VALID_STRATEGY})
    finally:
        akshare_client.get_history = original
    assert r.status_code == 200
    assert "error" in r.json()


def test_agent_diagnostic_endpoint():
    c = TestClient(main.app)
    original = agent.tool_registry.execute_tool

    def fake_execute(name, args):
        if name == "get_risk_metrics":
            return {"risk_level": "low", "decision_note": "test"}
        if name == "get_model_status":
            return {"available": True, "decision_use": False}
        if name == "get_model_consensus":
            return {"consensus": "neutral", "confidence": "low", "decision_use": False}
        return {"error": "unexpected"}

    agent.tool_registry.execute_tool = fake_execute
    try:
        r = c.get("/api/v1/agent/diagnostic/600519")
    finally:
        agent.tool_registry.execute_tool = original

    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "600519"
    assert body["risk"]["risk_level"] == "low"
    assert body["model_status"]["decision_use"] is False
    assert body["model_consensus"]["decision_use"] is False
    assert "disclaimer" in body


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
