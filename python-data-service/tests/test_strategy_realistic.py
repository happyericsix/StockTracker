"""真实化执行引擎（run_backtest_realistic）回归测试。

覆盖：次日开盘成交、整手取整、卖出印花税/佣金、涨跌停挡单、
缺失 bar 守卫（bar_date_missing）、理想 vs 真实 A/B 对照。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.strategy_engine import (  # noqa: E402
    evaluate_bar,
    run_backtest_realistic,
    compare_executions,
)

CONFIG = {
    "schema_version": "1.0", "name": "pb", "symbol": "600519",
    "initial_capital": 100000.0,
    "position": {"type": "full"},
    "entry": {"logic": "all", "conditions": [{"type": "price_above", "value": 100.0}]},
    "exit": {"logic": "any", "conditions": [{"type": "price_below", "value": 90.0}]},
    "risk": {"commission_pct": 0.0, "slippage_pct": 0.0},
}


def make_bars(open_overrides=None):
    """构造：前 25 根收盘 90（无入场信号）→ 25..39 收盘 110（触发买入后持有）
    → 40 起收盘 80（触发卖出）。open = close+3（滑点置 0 时便于断言次日开盘价）。"""
    closes = []
    for i in range(60):
        if i < 25:
            closes.append(90.0)
        elif i < 40:
            closes.append(110.0)
        else:
            closes.append(80.0)
    opens = [c + 3.0 for c in closes]
    overrides = open_overrides or {}
    for idx, val in overrides.items():
        opens[idx] = val
    bars = []
    for i in range(60):
        c = closes[i]
        bars.append({
            "date": f"d{i:03d}",
            "open": opens[i],
            "high": c + 5.0,
            "low": c - 5.0,
            "close": c,
            "volume": 1000,
        })
    return bars


def test_fill_on_next_open_and_whole_lots():
    bars = make_bars()
    result = run_backtest_realistic(CONFIG, bars)
    assert result.get("execution") == "realistic"
    assert result["trade_log"], "应发生交易"
    buy = result["trade_log"][0]
    assert buy["side"] == "buy"
    # 信号在第 25 根收盘(110) → 成交在第 26 根【开盘】，不是信号收盘价
    assert buy["date"] == bars[26]["date"]
    assert abs(buy["price"] - bars[26]["open"]) < 1e-9
    assert buy["fill_mode"] == "next_open"
    assert buy["shares"] % 100 == 0, "A 股应整手(100股)成交"
    sell = result["trade_log"][-1]
    assert sell["side"] == "sell"
    assert sell["date"] == bars[41]["date"]


def test_sell_pays_stamp_tax_and_commission():
    cfg = dict(CONFIG)
    cfg["risk"] = {"commission_pct": 0.1, "slippage_pct": 0.0}  # 0.1%
    result = run_backtest_realistic(cfg, make_bars())
    sells = [t for t in result["trade_log"] if t["side"] == "sell"]
    assert sells, "应发生卖出"
    for s in sells:
        # fee 已含佣金+印花税(0.05%)，必然 > 0
        assert s["fee"] > 0


def test_limit_up_blocks_buying():
    bars = make_bars()
    # 从信号次日(26)起连续一字/高开涨停：open >= 昨收*1.1 → 一律买不进
    overrides = {i: bars[i - 1]["close"] * 1.15 for i in range(26, 42)}
    bars = make_bars(overrides)
    result = run_backtest_realistic(CONFIG, bars)
    assert result["trade_log"] == [], "涨停开盘应买不进"


def test_evaluate_bar_marks_missing_date():
    out = evaluate_bar(CONFIG, make_bars(), "2099-01-01", None)
    assert out["bar_date_missing"] is True
    assert out["signal"] == "hold"


def test_compare_executions_returns_both_models():
    bars = make_bars()
    cmp = compare_executions(CONFIG, bars)
    assert "ideal" in cmp and "realistic" in cmp
    assert "gap_total_return_pct" in cmp
    for name in ("ideal", "realistic"):
        for key in ("total_return_pct", "max_drawdown_pct", "trade_count"):
            assert key in cmp[name], (name, key)


# ==================== 买不起一手：必须说明原因，不能只是"0 笔交易" ====================
# 真实场景：agent 给茅台（约 1455 元/股）生成了 initial_capital=100000 的策略，
# 而一手 100 股 ≈ 14.5 万 > 10 万 —— 回测里信号触发了 22 次、一笔都没成交，
# 而回复只写"交易笔数：0"。用户根本不知道是没钱还是没信号。

def test_insufficient_capital_for_one_lot_is_explained():
    cfg = dict(CONFIG)
    cfg["initial_capital"] = 1000.0     # 一手（100 股 × 约 113 元）都买不起
    result = run_backtest_realistic(cfg, make_bars())

    assert result["trade_count"] == 0
    note = result.get("funding_note")
    assert note, "买不起一手时必须给出说明，而不是静默 0 笔"
    assert "一手" in note and "1000" in note


def test_no_funding_note_when_capital_is_enough():
    result = run_backtest_realistic(CONFIG, make_bars())
    assert result["trade_count"] > 0
    assert result.get("funding_note") is None


def test_no_funding_note_when_no_signal_fired_at_all():
    """一次信号都没触发时不该误报"买不起" —— 那两件事必须分得清。"""
    cfg = dict(CONFIG)
    cfg["entry"] = {"logic": "all", "conditions": [{"type": "price_above", "value": 99999.0}]}
    result = run_backtest_realistic(cfg, make_bars())

    assert result["trade_count"] == 0
    assert result.get("funding_note") is None


def test_reply_explains_zero_trades():
    """回复里必须带上原因：用户看到的是文案，不是 trade_count。"""
    from agent.react_agent import _build_strategy_reply
    from agent.strategy_schema import validate_strategy_config

    cfg = dict(CONFIG)
    cfg["name"] = "pb"
    cfg["initial_capital"] = 1000.0
    parsed, err = validate_strategy_config(cfg)
    assert err is None, err

    backtest = run_backtest_realistic(cfg, make_bars())
    reply = _build_strategy_reply(parsed, backtest)

    assert "买不起一手" in reply
    assert "把初始资金调高" in reply


def test_reply_explains_a_strategy_that_never_fired():
    from agent.react_agent import _build_strategy_reply
    from agent.strategy_schema import validate_strategy_config

    cfg = dict(CONFIG)
    cfg["name"] = "pb"
    cfg["entry"] = {"logic": "all", "conditions": [{"type": "price_above", "value": 99999.0}]}
    parsed, err = validate_strategy_config(cfg)
    assert err is None, err

    backtest = run_backtest_realistic(cfg, make_bars())
    reply = _build_strategy_reply(parsed, backtest)

    assert "一次都没触发" in reply
