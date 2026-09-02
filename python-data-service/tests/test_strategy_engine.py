import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.strategy_engine import compute_indicators, evaluate_rule
from agent.strategy_schema import RuleGroup

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

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
