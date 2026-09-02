import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.strategy_schema import validate_strategy_config, extract_strategy_json

VALID = {
    "schema_version": "1.0",
    "name": "MA cross",
    "symbol": "600519",
    "initial_capital": 100000,
    "data": {"period": "day", "lookback_days": 250},
    "position": {"type": "percent", "size_pct": 100},
    "entry": {"logic": "all", "conditions": [
        {"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}
    ]},
    "exit": {"logic": "any", "conditions": [
        {"type": "ma_cross", "fast": 20, "slow": 60, "direction": "below"},
        {"type": "stop_loss_pct", "value": -8},
        {"type": "take_profit_pct", "value": 15}
    ]},
    "risk": {"commission_pct": 0.1, "slippage_pct": 0.1},
}

def test_valid_strategy():
    cfg, err = validate_strategy_config(VALID)
    assert err is None and cfg.name == "MA cross"

def test_unknown_condition_rejected():
    bad = {**VALID, "entry": {"logic": "all", "conditions": [{"type": "vibe"}]}}
    cfg, err = validate_strategy_config(bad)
    assert cfg is None and err

def test_ma_cross_requires_fast_slow_direction():
    bad = {**VALID, "entry": {"logic": "all", "conditions": [{"type": "ma_cross", "fast": 20}]}}
    cfg, err = validate_strategy_config(bad)
    assert cfg is None and "ma_cross" in err

def test_extract_json_from_fence():
    text = 'Here:\n```json\n{"schema_version":"1.0"}\n```'
    assert extract_strategy_json(text) == {"schema_version": "1.0"}

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
