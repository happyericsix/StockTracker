# -*- coding: utf-8 -*-
"""自适应网格引擎测试（测评一：策略生成 → 结构化参数与可执行语义）。

全部离线确定性：合成路径来自 stress_lab.synthetic_records（固定 seed），
不碰网络、不碰模型。每个用例对应一条"合格标准"：
- 结构化参数（基准价/格数/ATR 间距公式/每格资金/破网策略）→ describe_grid
- 高抛低吸在震荡市赚钱、单边市显形（卖飞/破网）→ 情景行为
- 硬风控由确定性代码执行（回撤止损终态、仓位上限、T+1）→ 对抗性路径
- 同输入同输出 → determinism
"""
import pytest

from agent import grid_strategy as gs
from agent import perf_metrics as pm
from agent import stress_lab as sl


def base_config(**over):
    cfg = {
        "schema_version": "1.0", "name": "测试网格", "symbol": "600000",
        "initial_capital": 100000.0,
        "base_price_mode": "anchor", "grid_count": 4,
        "atr_window": 5, "atr_multiplier": 1.0,
        "per_grid_capital_pct": 20.0,
        "breakout_policy": "halt", "recenter_cooldown_bars": 2,
        "atr_review_bars": 5, "spacing_rebuild_change_pct": 30.0,
        "max_drawdown_stop_pct": 15.0, "max_position_pct": 80.0,
        "commission_pct": 0.0, "slippage_pct": 0.0,
    }
    cfg.update(over)
    return cfg


def osc_records(days=80, seed=7):
    return sl.synthetic_records("oscillate_high", days=days, start_price=100.0, seed=seed)


# ---------- 参数校验与结构化输出（合格标准：不是模糊描述） ----------

def test_config_rejects_bad_params_with_chinese_errors():
    _, err = gs.validate_grid_config(base_config(base_price_mode="manual"))
    assert err and "base_price" in err
    _, err = gs.validate_grid_config(base_config(grid_count=0))
    assert err and "grid_count" in err
    _, err = gs.validate_grid_config(base_config(max_drawdown_stop_pct=3))
    assert err and "max_drawdown_stop_pct" in err
    _, err = gs.validate_grid_config(base_config(atr_multiplier=-1))
    assert err and "atr_multiplier" in err


def test_signature_stable_and_sensitive():
    cfg = base_config()
    assert gs.grid_signature(cfg) == gs.grid_signature(base_config())
    assert gs.grid_signature(cfg) != gs.grid_signature(base_config(grid_count=5))


def test_atr_is_asof_no_lookahead():
    records = osc_records(days=60)
    full = records[:60]
    prefix = records[:30]
    def atr(recs):
        import numpy as np
        highs = [r["high"] for r in recs]; lows = [r["low"] for r in recs]; closes = [r["close"] for r in recs]
        return gs.compute_atr_pct(highs, lows, closes, 5)
    a_full, a_prefix = atr(full), atr(prefix)
    for i in range(len(a_prefix)):
        assert (a_full[i] == a_prefix[i]) or (a_full[i] != a_full[i] and a_prefix[i] != a_prefix[i])


def test_grid_levels_geometry():
    levels = gs.grid_levels(100.0, 2.0, 4)
    assert len(levels) == 9                     # 上下各 4 格 + 基准
    assert levels[4] == 100.0
    assert levels[5] == pytest.approx(102.0)    # 间距公式：base × spacing%
    assert levels[3] == pytest.approx(98.0)
    assert levels == sorted(levels)


def test_describe_grid_outputs_structured_params():
    """测评一合格标准逐项：基准价/格数/动态间距公式/每格资金/破网策略/硬风控。"""
    cfg = gs.GridConfig.model_validate(base_config())
    snap = gs.describe_grid(cfg, base=100.0, atr_pct=3.0)
    for key in ("base_price", "spacing_pct", "spacing_abs", "grid_count_per_side",
                "upper_boundary", "lower_boundary", "per_grid_capital", "per_grid_shares",
                "levels", "breakout_policy", "max_drawdown_stop_pct", "max_position_pct"):
        assert key in snap, f"缺结构化参数：{key}"
    assert snap["spacing_pct"] == pytest.approx(3.0)        # = atr_multiplier × ATR%
    assert snap["per_grid_shares"] == 200                   # 20000 / (100×100) = 2 手
    assert snap["upper_boundary"] == pytest.approx(100.0 + 4 * 3.0)


# ---------- 行为：震荡赚钱、单边显形（测评三的情景结论在引擎层先成立） ----------

def test_grid_harvests_oscillation():
    r = gs.run_grid_backtest(base_config(), osc_records())
    assert not r.get("error")
    assert r["total_return_pct"] > 0
    assert r["excess_return_pct"] > 0                          # 跑赢买入持有
    buys = [t for t in r["trade_log"] if t["side"] == "buy"]
    sells = [t for t in r["trade_log"] if t["side"] == "sell"]
    assert buys and sells                                      # 高抛低吸都在动
    avg_buy = sum(t["price"] * t["shares"] for t in buys) / sum(t["shares"] for t in buys)
    avg_sell = sum(t["price"] * t["shares"] for t in sells) / sum(t["shares"] for t in sells)
    assert avg_sell > avg_buy                                  # 卖均价高于买均价


def test_grid_lags_in_monotonic_uptrend():
    """单边上涨：网格无事可做、大幅跑输基准（'卖飞/踏空'死穴的显形）。"""
    records = sl.synthetic_records("trend_up", days=60, start_price=100.0, seed=7)
    r = gs.run_grid_backtest(base_config(), records)
    assert r["excess_return_pct"] < 0
    events = [e["event"] for e in r["grid_events"]]
    assert r["total_return_pct"] == pytest.approx(0.0) or events.count("breakout_up") > 0


def test_grid_beats_hold_in_crash_with_bounded_loss():
    records = sl.synthetic_records("crash", days=60, start_price=100.0, seed=7)
    r = gs.run_grid_backtest(base_config(), records)
    assert r["excess_return_pct"] > 0                          # 暴跌里比持有抗揍
    assert r["total_return_pct"] > -25                         # 且损失有界（网格资本有限）


def test_vol_regime_rebuild_widens_spacing():
    """波动率切换时按 ATR 重建网格（动态间距公式的第二条生效链路）。

    重建只改间距、锚点不动（keep_base）——否则网格追着价格跑，防追跌语义失效。
    """
    records = sl.synthetic_records("regime_shift", days=90, start_price=100.0, seed=3)
    cfg = base_config(atr_review_bars=3, spacing_rebuild_change_pct=40.0)
    r = gs.run_grid_backtest(cfg, records)
    causes = [b["cause"] for b in r["grid_builds"]]
    assert "vol_regime" in causes
    spacings = [b["spacing_pct"] for b in r["grid_builds"]]
    assert max(spacings) / min(spacings) > 1.15                # 高波动段间距更宽
    # 锚点漂移有限：regime_shift 围绕 100 锚定震荡，重建不应把锚搬走
    bases = [b["base_price"] for b in r["grid_builds"] if b["cause"] == "vol_regime"]
    assert all(abs(b - 100.0) / 100.0 < 0.10 for b in bases)


def test_flatten_policy_liquidates_on_breakout():
    records = sl.synthetic_records("crash", days=60, start_price=100.0, seed=7)
    r = gs.run_grid_backtest(base_config(breakout_policy="flatten"), records)
    sells = [t for t in r["trade_log"] if t["side"] == "sell"]
    assert any(t["reason"] == gs.REASON_FLATTEN_BREAKOUT for t in sells)
    assert r["open_inventory"] == []                           # 破网清仓后不再持仓


def test_halt_suspends_buys_until_reentry_confirmed():
    """V 型路径：破网 → 回区间 → 确认期内下穿格子必须被挡（留痕 SKIP_HALTED）。

    止损线放到 50%：这里要观察的是 halt 链路本身，不想让回撤止损先把戏台拆了
    （止损终态另有专门用例）。
    """
    # 先平静（网格按低 ATR 收紧）→ 暴跌破网 → 反弹回区间 → 确认期内回落被拦。
    # 没有平静段的话，建网发生在暴跌中段、ATR 已高企，网格太宽根本不会出界。
    scenario = {"stages": [
        {"days_share": 0.20, "drift_pct": 0.0, "amplitude_pct": 1.0},
        {"days_share": 0.16, "drift_pct": -6.0},
        {"days_share": 0.16, "drift_pct": 7.0},
        {"days_share": 0.12, "drift_pct": -2.0},
        {"days_share": 0.36, "drift_pct": 0.0},
    ]}
    records = sl.synthetic_records(scenario, days=50, start_price=100.0, seed=11)
    r = gs.run_grid_backtest(base_config(max_drawdown_stop_pct=50.0), records)
    events = [e["event"] for e in r["grid_events"]]
    assert "breakout_down" in events
    assert "re_enter_range" in events
    assert r["skip_counter"].get(gs.SKIP_HALTED, 0) > 0


# ---------- 硬风控与账目纪律（测评四） ----------

def aggressive_config(**over):
    """风控探针配置：部署重到暴跌必穿止损线，验证机制而非运气。"""
    return base_config(per_grid_capital_pct=25.0, grid_count=6,
                       max_drawdown_stop_pct=12.0, max_position_pct=90.0, **over)


def test_risk_stop_is_terminal_and_leaves_no_buy_after():
    records = sl.synthetic_records("crash", days=50, start_price=100.0, seed=11)
    r = gs.run_grid_backtest(aggressive_config(), records)
    assert r["risk_stopped"] is True
    stop_trades = [t for t in r["trade_log"] if t["reason"] == gs.REASON_RISK_STOP]
    assert stop_trades, "回撤止损必须由确定性代码触发并留痕"
    stop_date = min(t["date"] for t in stop_trades)
    buys_after = [t for t in r["trade_log"]
                  if t["side"] == "buy" and t["date"] > stop_date]
    assert buys_after == [], "止损后不允许再有任何买入（不给侥幸留路径）"
    assert any(e["event"] == "risk_stop" for e in r["grid_events"])


def test_position_cap_holds_every_bar():
    """仓位上限约束的是**买入行为**（信号层与成交层都夹紧），不是市值漂移：
    买入后价格上涨导致的 pct 上升是利润，不是违规。所以断言两件事：
    ① 上限确实拦截/削减了买单（skip 留痕）；② pct 的超额只来自涨（幅度有限）。
    """
    records = osc_records(days=90)
    r = gs.run_grid_backtest(base_config(max_position_pct=40.0), records)
    for row in r["equity_curve"]:
        assert row["position_pct"] <= 40.0 * 1.05, row   # 涨幅余量 5%：±4% 震荡内的漂移
    assert r["skip_counter"].get(gs.SKIP_POSITION_CAP, 0) > 0 or any(
        t["side"] == "buy" and t["shares"] < 200 for t in r["trade_log"])


def test_equity_identity_holds_every_bar():
    """净值恒等式 cash + shares×close == equity（分位舍入下容差 1 分钱）。"""
    r = gs.run_grid_backtest(base_config(commission_pct=0.03, slippage_pct=0.05),
                             osc_records(days=90, seed=5))
    for row in r["equity_curve"]:
        assert abs(row["equity"] - (row["cash"] + row["shares"] * row["close"])) <= 0.02


def test_t1_every_round_trip_holds_at_least_one_bar():
    r = gs.run_grid_backtest(base_config(), osc_records(days=90, seed=5))
    trips = pm.round_trips(r["trade_log"])
    assert trips
    assert all(t["holding_bars"] >= 1 for t in trips)


def test_fees_use_official_rate_table_not_percent_as_ratio():
    """费率口径：印花税 0.05% 是百分数——按比率直接乘会放大 100 倍（曾实测每笔多扣千元）。"""
    r = gs.run_grid_backtest(base_config(commission_pct=0.0, slippage_pct=0.0),
                             osc_records(days=60))
    sells = [t for t in r["trade_log"] if t["side"] == "sell"]
    assert sells
    for t in sells:
        rate = t["fee"] / t["amount"]
        assert rate < 0.002, f"卖出费率 {rate:.4f} 异常（印花税+过户费应约 0.06%）"


def test_determinism_same_input_same_output():
    records = osc_records(days=70, seed=9)
    check = sl.determinism_check(base_config(), records, runs=3)
    assert check["identical"]


def test_start_date_limits_trading_window():
    """start_date 之前的 bar 只作 ATR 预热：不在窗口内建网交易。"""
    records = osc_records(days=60, seed=7)
    r = gs.run_grid_backtest(base_config(), records, start_date=records[30]["date"])
    assert r["start_date"] == records[30 + 0]["date"] or r["start_date"] >= records[30]["date"]
    assert all(t["date"] >= records[30]["date"] for t in r["trade_log"])
    assert all(e["date"] >= records[30]["date"] for e in r["grid_events"])
