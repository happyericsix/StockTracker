# -*- coding: utf-8 -*-
"""绩效指标套件测试（测评二：指标计算与解读）。

数值全部手工可验：指标的意义在口径，口径错了数值"看起来也对"——
所以每个用例都把公式写死在断言里，防实现漂移。
"""
import pytest

from agent import perf_metrics as pm


def curve(values, dates=None):
    dates = dates or [f"d{i:03d}" for i in range(len(values))]
    return [{"date": d, "equity": v} for d, v in zip(dates, values)]


# ---------- 风险收益 ----------

def test_sharpe_zero_for_flat():
    assert pm.sharpe_ratio([100, 100, 100, 100]) == 0.0


def test_sortino_rewards_upside_volatility():
    """只涨不跌的曲线：Sortino 走哨兵值（无下行风险），且显著高于 Sharpe。"""
    values = [100, 101, 100.5, 102, 101.5, 103]
    assert pm.sortino_ratio(values) > pm.sharpe_ratio(values)
    assert pm.sortino_ratio([100, 102, 104, 106]) == pytest.approx(99.999)


def test_calmar_none_without_drawdown():
    assert pm.calmar_ratio(0.2, 0.0, 252) is None
    assert pm.calmar_ratio(0.2, -20.0, 252) == pytest.approx(
        pm.annualized_return_pct(0.2, 252) / 20)


# ---------- 回撤与恢复 ----------

def test_max_drawdown_full_recovery():
    stats = pm.max_drawdown_stats([100, 120, 90, 130],
                                  dates=["a", "b", "c", "d"])
    assert stats["max_dd_pct"] == pytest.approx(25.0)
    assert stats["peak_date"] == "b"
    assert stats["trough_date"] == "c"
    assert stats["recovery_date"] == "d"
    assert stats["recovery_bars_from_trough"] == 1
    assert stats["current_dd_pct"] == 0.0


def test_max_drawdown_unrecovered_current_dd():
    stats = pm.max_drawdown_stats([100, 120, 110, 115])
    assert stats["max_dd_pct"] == pytest.approx(8.33, abs=0.01)
    assert stats["recovery_date"] is None
    assert stats["recovery_bars_from_trough"] is None
    assert stats["current_dd_pct"] == pytest.approx(4.17, abs=0.01)
    assert stats["underwater_bars_since_peak"] == 2


def test_longest_underwater_span():
    stats = pm.max_drawdown_stats([100, 110, 105, 108, 104, 106, 120])
    assert stats["longest_underwater_bars"] >= 4   # 110 峰 → 120 新高，中间 4 根水下


# ---------- 交易质量 ----------

def test_round_trips_fifo_with_partial_fills():
    log = [
        {"date": "d1", "side": "buy", "price": 10, "shares": 100, "amount": 1000, "fee": 5},
        {"date": "d2", "side": "buy", "price": 12, "shares": 100, "amount": 1200, "fee": 5},
        {"date": "d3", "side": "sell", "price": 13, "shares": 150, "amount": 1950, "fee": 5},
    ]
    trips = pm.round_trips(log)
    assert len(trips) == 2
    assert trips[0]["entry_date"] == "d1" and trips[0]["exit_date"] == "d3"
    assert trips[0]["shares"] == 100
    # 卖出净得按股数摊分：1945 × 100/150；成本按 FIFO 整笔吃入：1005
    assert trips[0]["pnl"] == pytest.approx((1950 - 5) * 100 / 150 - 1005, abs=0.01)
    assert trips[1]["pnl"] == pytest.approx((1950 - 5) * 50 / 150 - 1205 * 0.5, abs=0.01)
    # 配对盈亏之和 = 现金流守恒：卖出净得 − 被吃掉的成本
    assert sum(t["pnl"] for t in trips) == pytest.approx(1945 - (1005 + 602.5), abs=0.01)


def test_trade_quality_formulas():
    log = [
        {"date": "d1", "side": "buy", "price": 10, "shares": 100, "amount": 1000, "fee": 0},
        {"date": "d2", "side": "sell", "price": 12, "shares": 100, "amount": 1200, "fee": 0},
        {"date": "d3", "side": "buy", "price": 10, "shares": 100, "amount": 1000, "fee": 0},
        {"date": "d4", "side": "sell", "price": 9, "shares": 100, "amount": 900, "fee": 0},
    ]
    trips = pm.round_trips(log)
    q = pm.trade_quality(trips)
    assert q["win_rate_pct"] == 50.0
    assert q["avg_win"] == 200.0 and q["avg_loss"] == -100.0
    assert q["payoff_ratio"] == 2.0
    assert q["profit_factor"] == 2.0
    assert q["expectancy_yuan"] == 50.0
    assert q["note"]


def test_r_expectancy_uses_grid_risk_unit():
    log = [
        {"date": "d1", "side": "buy", "price": 10, "shares": 100, "amount": 1000, "fee": 0},
        {"date": "d2", "side": "sell", "price": 10.5, "shares": 100, "amount": 1050, "fee": 0},
    ]
    trips = pm.round_trips(log)
    q = pm.trade_quality(trips, pm.grid_risk_units(trips, spacing_pct=2.0))
    # R = entry_cost × spacing% = 1000×2% = 20；pnl = 50 → R-Expectancy = 2.5
    assert q["r_expectancy"] == pytest.approx(2.5)
    assert pm.grid_risk_units(trips, None) is None


def test_trade_quality_empty():
    q = pm.trade_quality([])
    assert q["closed_trades"] == 0 and q["profit_factor"] is None


# ---------- 滚动窗口与蒙特卡洛 ----------

def test_rolling_window_shape_and_stability():
    values = [100 * (1 + 0.001 * i) for i in range(60)]
    out = pm.rolling_window_metrics(curve(values), window=20, step=10)
    assert len(out["rows"]) == 5                      # starts 0/10/20/30/40
    for key in ("sharpe_mean", "sharpe_cv", "positive_window_share_pct"):
        assert key in out["stability"]
    assert out["note"]


def test_monte_carlo_deterministic_and_monotone_quantiles():
    rets = [0.01, -0.02, 0.015, 0.005, -0.01, 0.02, 0.008, -0.005] * 5
    a = pm.monte_carlo(rets, n_sims=200, seed=42)
    b = pm.monte_carlo(rets, n_sims=200, seed=42)
    assert a == b                                     # 固定 seed：可重复
    c = pm.monte_carlo(rets, n_sims=200, seed=43)
    assert c["final_return_pct"]["p50"] != a["final_return_pct"]["p50"]
    fr = a["final_return_pct"]
    assert fr["p5"] <= fr["p25"] <= fr["p50"] <= fr["p75"] <= fr["p95"]
    assert 0.0 <= a["prob_loss_pct"] <= 100.0


def test_monte_carlo_insufficient_data():
    assert pm.monte_carlo([0.01, 0.02], n_sims=100)["n_sims"] == 0


# ---------- 汇总入口 ----------

def test_compute_metrics_has_full_scorecard():
    """测评二合格标准的指标清单：一次算全、键齐、带解读。"""
    values = [100, 102, 99, 104, 101, 106, 103, 107]
    log = [
        {"date": "d1", "side": "buy", "price": 100, "shares": 100, "amount": 10000, "fee": 5},
        {"date": "d6", "side": "sell", "price": 106, "shares": 100, "amount": 10600, "fee": 5},
    ]
    m = pm.compute_metrics(curve(values), log, 100000.0, spacing_pct=2.0)
    for key in ("total_return_pct", "annualized_return_pct", "sharpe_ratio",
                "sortino_ratio", "calmar_ratio", "risk_adjusted_note", "drawdown",
                "trade_quality", "closed_trades"):
        assert key in m
    dd = m["drawdown"]
    for key in ("max_dd_pct", "current_dd_pct", "recovery_bars_from_trough",
                "underwater_bars_since_peak", "longest_underwater_bars"):
        assert key in dd
    q = m["trade_quality"]
    for key in ("win_rate_pct", "payoff_ratio", "profit_factor", "r_expectancy",
                "expectancy_yuan", "note"):
        assert key in q
