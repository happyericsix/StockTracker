import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.tool_registry import TOOL_SCHEMAS, execute_tool


def test_schemas_have_names():
    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert {"search_stock", "validate_strategy", "backtest_strategy", "finalize_strategy"} <= names


def test_validate_tool():
    out = execute_tool("validate_strategy", {"strategy_json": {
        "schema_version": "1.0", "name": "x", "symbol": "600519",
        "entry": {"logic": "all", "conditions": [{"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}]},
        "exit": {"logic": "any", "conditions": [{"type": "stop_loss_pct", "value": -8}]}}})
    assert out["valid"] is True


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
