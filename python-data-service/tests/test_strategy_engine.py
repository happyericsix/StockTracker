import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.strategy_engine import compute_indicators, evaluate_rule, run_backtest, evaluate_bar
from agent.strategy_schema import RuleGroup

CONFIG = {
    "schema_version": "1.0", "name": "ma", "symbol": "600519",
    "initial_capital": 100000,
    "position": {"type": "full"},
    "entry": {"logic": "all", "conditions": [
        {"type": "ma_cross", "fast": 5, "slow": 20, "direction": "above"}
    ]},
    "exit": {"logic": "any", "conditions": [
        {"type": "ma_cross", "fast": 5, "slow": 20, "direction": "below"}
    ]},
    "risk": {"commission_pct": 0.1, "slippage_pct": 0.1},
}

def make_bars(n=80, start=100.0, step=1.0):
    half = n // 2
    bars = []
    for i in range(n):
        if i < half:
            close = start - step * i
        else:
            close = start - step * (half - 1) + step * (i - half)
        bars.append({
            "date": f"2026-01-{i+1:02d}",
            "open": close,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "volume": 1000,
        })
    return bars

def test_ma_cross_above():
    bars = make_bars()
    ind = compute_indicators(bars)
    rule = RuleGroup.model_validate({
        "logic": "all",
        "conditions": [{"type": "ma_cross", "fast": 5, "slow": 20, "direction": "above"}]
    })
    assert any(evaluate_rule(rule, ind, i, None)[0] for i in range(20, len(bars)))

def test_stop_loss_needs_position():
    ind = compute_indicators(make_bars())
    rule = RuleGroup.model_validate({
        "logic": "any",
        "conditions": [{"type": "stop_loss_pct", "value": -5}]
    })
    ok, _ = evaluate_rule(rule, ind, 30, None)
    assert not ok

def test_ma_cross_non_precomputed_window():
    bars = make_bars()
    ind = compute_indicators(bars)
    rule = RuleGroup.model_validate({
        "logic": "all",
        "conditions": [{"type": "ma_cross", "fast": 7, "slow": 21, "direction": "above"}]
    })
    ok, reasons = evaluate_rule(rule, ind, 25, None)
    assert isinstance(ok, bool)
    assert reasons in ([], ["ma_cross"])

def test_backtest_returns_curve_and_trades():
    result = run_backtest(CONFIG, make_bars())
    assert result["data_points"] > 0
    assert "equity_curve" in result and "trade_log" in result

def test_backtest_20_bar_boundary():
    result = run_backtest(CONFIG, make_bars(n=20))
    assert result["error"] == "data insufficient"
    assert result["equity_curve"] == []
    assert result["trade_log"] == []

def test_evaluate_bar_returns_signal():
    bars = make_bars()
    out = evaluate_bar(CONFIG, bars, bars[-1]["date"], None)
    assert out["signal"] in {"buy", "sell", "hold"}
    assert "matched_conditions" in out

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
