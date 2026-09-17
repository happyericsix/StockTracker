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


def test_backtest_returns_performance_metrics():
    result = run_backtest(CONFIG, make_bars(n=120))
    for key in (
        "total_return_pct",
        "buy_and_hold_return_pct",
        "excess_return_pct",
        "annualized_return_pct",
        "sharpe_ratio",
        "max_drawdown_pct",
        "win_rate",
        "closed_trades",
        "trade_count",
        "benchmark_equity_curve",
    ):
        assert key in result, key
    assert result["max_drawdown_pct"] <= 0
    assert 0 <= result["win_rate"] <= 100
    assert len(result["benchmark_equity_curve"]) == len(result["equity_curve"])

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


# ==================== 价格与均线的关系（补 DSL 缺口） ====================

def make_step_bars(flat=30, up=20, down=30, base=100.0, high=120.0, low=80.0):
    """先横盘、再跳上一个台阶、最后跌下来 —— 于是"上穿/下穿均线"的时刻是可预知的。

    为什么要专门造这样一段：用随机的斜线序列测"交叉"，测试就得靠"在某个区间里存在"
    这种模糊断言；而这两条规则（上穿买入、跌破卖出）恰恰是**必须精确**的那种。
    """
    prices = [base] * flat + [high] * up + [low] * down
    return [{"date": f"2026-02-{i + 1:02d}", "open": p, "high": p + 1, "low": p - 1,
             "close": p, "volume": 1000} for i, p in enumerate(prices)]


def test_price_cross_ma_above_fires_exactly_on_the_jump():
    bars = make_step_bars()
    ind = compute_indicators(bars)
    rule = RuleGroup.model_validate({
        "logic": "all",
        "conditions": [{"type": "price_cross_ma", "window": 20, "direction": "above"}]})
    fired = [i for i in range(len(bars)) if evaluate_rule(rule, ind, i, None)[0]]
    assert fired == [30], fired   # 台阶那一根上穿，之后是"状态"而不是"事件"


def test_price_cross_ma_below_fires_exactly_on_the_drop():
    bars = make_step_bars()
    ind = compute_indicators(bars)
    rule = RuleGroup.model_validate({
        "logic": "all",
        "conditions": [{"type": "price_cross_ma", "window": 20, "direction": "below"}]})
    fired = [i for i in range(len(bars)) if evaluate_rule(rule, ind, i, None)[0]]
    assert fired == [50], fired


def test_price_above_ma_is_a_state_not_an_event():
    """状态在均线上方的**每一根**都成立 —— 这正是它与"上穿"的区别，也是它不能当入场条件的原因。"""
    bars = make_step_bars()
    ind = compute_indicators(bars)
    rule = RuleGroup.model_validate({
        "logic": "all", "conditions": [{"type": "price_above_ma", "window": 20}]})
    fired = [i for i in range(len(bars)) if evaluate_rule(rule, ind, i, None)[0]]
    assert len(fired) > 1
    assert 30 in fired and 45 in fired


def test_price_below_ma_state_on_the_way_down():
    bars = make_step_bars()
    ind = compute_indicators(bars)
    rule = RuleGroup.model_validate({
        "logic": "all", "conditions": [{"type": "price_below_ma", "window": 20}]})
    assert evaluate_rule(rule, ind, 60, None)[0] is True


def test_price_vs_ma_is_false_while_the_average_is_undefined():
    """均线还没算出来时既不是"之上"也不是"之下" —— 否则会在数据开头误触发。"""
    bars = make_step_bars()
    ind = compute_indicators(bars)
    for condition_type in ("price_above_ma", "price_below_ma"):
        rule = RuleGroup.model_validate({
            "logic": "all", "conditions": [{"type": condition_type, "window": 20}]})
        assert evaluate_rule(rule, ind, 5, None)[0] is False, condition_type
    cross = RuleGroup.model_validate({
        "logic": "all",
        "conditions": [{"type": "price_cross_ma", "window": 20, "direction": "above"}]})
    assert evaluate_rule(cross, ind, 0, None)[0] is False


def test_price_vs_ma_works_for_windows_the_engine_does_not_precompute():
    """引擎只预算 5/10/20/60 日线；用户说"跌破 30 日线"时也要能算（按需计算）。"""
    bars = make_step_bars()
    ind = compute_indicators(bars)
    rule = RuleGroup.model_validate({
        "logic": "all",
        "conditions": [{"type": "price_cross_ma", "window": 30, "direction": "above"}]})
    assert any(evaluate_rule(rule, ind, i, None)[0] for i in range(len(bars)))


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
