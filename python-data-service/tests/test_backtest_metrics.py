"""回测指标统计回归测试：胜率/盈亏比必须按 (BUY, SELL) 配对计算。

背景：旧实现把所有 SELL 都算"盈利"（单轮往返胜率恒 100%），
且用相邻两笔 trade 的 value 相减（(SELL,BUY) 也被当作一轮亏损）。
"""

from backtest import BacktestEngine


def _build(trades, equity):
    engine = BacktestEngine()
    return engine._build_result(
        "TEST", "signal", equity, trades, [], 0.0, [10.0, 11.0]
    )


def test_win_rate_counts_round_trips_not_sells():
    trades = [
        {"action": "BUY", "value": 100_000.0},
        {"action": "SELL", "value": 110_000.0},   # 盈利回合
        {"action": "BUY", "value": 110_000.0},
        {"action": "SELL", "value": 99_000.0},    # 亏损回合
    ]
    result = _build(trades, [100_000.0, 110_000.0])
    assert result.win_rate == 0.5
    assert result.profit_trades == 1
    assert result.total_trades == 4
    # BacktestResult 字段经 round(…, 4)，故用 1e-4 容差
    assert abs(result.profit_factor - 10_000.0 / 11_000.0) < 1e-4
    assert result.avg_loss_per_trade == 11_000.0


def test_single_winning_round_trip_is_100pct_win_with_no_profit_factor():
    trades = [
        {"action": "BUY", "value": 100_000.0},
        {"action": "SELL", "value": 120_000.0},
    ]
    result = _build(trades, [100_000.0, 120_000.0])
    assert result.win_rate == 1.0
    assert result.profit_trades == 1
    assert result.profit_factor == 0.0  # 没有亏损回合 → 无盈亏比


def test_losing_round_trip_is_zero_win():
    trades = [
        {"action": "BUY", "value": 100_000.0},
        {"action": "SELL", "value": 90_000.0},
    ]
    result = _build(trades, [100_000.0, 90_000.0])
    assert result.win_rate == 0.0
    assert result.profit_trades == 0
    assert abs(result.avg_loss_per_trade - 10_000.0) < 1e-9
