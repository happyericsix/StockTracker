# -*- coding: utf-8 -*-
"""压力实验室测试（测评三：情景分析 / Walk-Forward / PSI；测评四：确定性与硬风控）。

合成路径有界且固定 seed —— 压力测试本身必须可重复，否则"过不过"取决于跑的那次运气。
"""
import numpy as np
import pytest

from agent import grid_strategy as gs
from agent import stress_lab as sl


def base_config(**over):
    cfg = {
        "schema_version": "1.0", "name": "压测网格", "symbol": "600000",
        "initial_capital": 100000.0,
        "grid_count": 4, "atr_window": 5, "atr_multiplier": 1.0,
        "per_grid_capital_pct": 20.0,
        "breakout_policy": "halt", "recenter_cooldown_bars": 2,
        "atr_review_bars": 5, "spacing_rebuild_change_pct": 30.0,
        "max_drawdown_stop_pct": 15.0, "max_position_pct": 80.0,
        "commission_pct": 0.0, "slippage_pct": 0.0,
    }
    cfg.update(over)
    return cfg


# ---------- 合成路径 ----------

def test_synthetic_deterministic_by_seed():
    a = sl.synthetic_records("oscillate_high", days=40, seed=7)
    b = sl.synthetic_records("oscillate_high", days=40, seed=7)
    c = sl.synthetic_records("oscillate_high", days=40, seed=8)
    assert a == b
    assert a != c


def test_oscillation_is_bounded():
    records = sl.synthetic_records("oscillate_high", days=120, start_price=100.0, seed=7)
    closes = [r["close"] for r in records]
    # ±4% 锚定 + 0.3% 新鲜噪声：界外余量给到 1.5%
    assert max(closes) <= 100 * 1.055
    assert min(closes) >= 100 * 0.945


def test_trend_scenarios_reach_target_magnitude():
    up = sl.synthetic_records("trend_up", days=40, start_price=100.0, seed=7)
    assert up[-1]["close"] > 130
    down = sl.synthetic_records("trend_down", days=40, start_price=100.0, seed=7)
    assert down[-1]["close"] < 75


# ---------- 情景矩阵（测评三：单边暴涨卖飞 / 单边暴跌破网） ----------

def test_scenario_matrix_flags_the_two_death_zones():
    out = sl.scenario_matrix(base_config(), days=80, seed=7,
                             scenarios=["trend_up", "oscillate_high", "crash", "trend_down"])
    rows = {r["scenario"]: r for r in out["rows"]}
    up = rows["trend_up"]
    assert up["excess_return_pct"] < 0                 # 单边暴涨：网格跑输基准（卖飞/踏空）
    assert up["sold_out_in_uptrend"] or up["breakout_up_count"] > 0
    osc = rows["oscillate_high"]
    assert osc["excess_return_pct"] > 0                # 主场情景：跑赢
    crash = rows["crash"]
    assert crash["halted_buying_in_downtrend"] or crash["risk_stop_fired"]  # 破网被硬规则拦住
    for row in out["rows"]:
        assert row.get("diagnosis")


# ---------- Walk-Forward（测评三：过拟合诊断） ----------

def test_walk_forward_structure_and_ranges():
    records = sl.synthetic_records("regime_shift", days=160, seed=3)
    out = sl.walk_forward(base_config(), records,
                          param_grid={"atr_multiplier": [0.6, 0.8, 1.0, 1.2, 1.5]},
                          segments=3)
    assert not out.get("error")
    assert out["folds"] >= 1
    for row in out["rows"]:
        assert 0.0 <= row["oos_percentile_of_pick"] <= 100.0
        assert "picked" in row and "is_range" in row and "oos_range" in row
    assert 0.0 <= out["oos_percentile_of_picks_mean"] <= 100.0
    assert -1.0 <= out["is_oos_rank_corr_mean"] <= 1.0
    assert out["note"]


def test_walk_forward_insufficient_data_reports_error():
    out = sl.walk_forward(base_config(), sl.synthetic_records("oscillate_high", days=40))
    assert out.get("error")


# ---------- PSI（测评三：环境偏移预警） ----------

def test_psi_identical_distributions_stable():
    rng = np.random.default_rng(1)
    sample = list(rng.normal(0, 1, 500))
    out = sl.psi(sample, sample)
    assert out["psi"] < 0.01
    assert out["verdict"] == "stable"


def test_psi_shifted_distributions_flagged():
    rng = np.random.default_rng(2)
    ref = list(rng.normal(0, 1, 500))
    shifted = list(rng.normal(1.5, 1, 500))
    out = sl.psi(ref, shifted)
    assert out["psi"] > 0.25
    assert out["verdict"] == "shifted"
    assert "失效" in out["verdict_zh"] or "偏移" in out["verdict_zh"]


def test_psi_records_shapes_and_features():
    ref = sl.synthetic_records("oscillate_high", days=80, seed=5)
    recent = sl.synthetic_records("trend_down", days=60, seed=5)
    out = sl.psi_records(ref, recent, feature="returns")
    assert out["feature"] == "returns"
    assert out["reference_bars"] == 80 and out["recent_bars"] == 60
    assert out["psi"] is None or out["psi"] > 0.25      # 震荡 → 单边下跌：必须报警


def test_psi_insufficient_data():
    out = sl.psi([0.1] * 3, [0.2] * 3)
    assert out["verdict"] == "insufficient_data"


# ---------- 测评四：行为一致性 + 风控有效性 ----------

def test_determinism_check_identical_hashes():
    records = sl.synthetic_records("regime_shift", days=60, seed=7)
    out = sl.determinism_check(base_config(), records, runs=4)
    assert out["identical"]
    assert len(set(out["hashes"])) == 1


def test_risk_control_probe_passes_on_adversarial_crash():
    """对抗性暴跌：止损必须触发、触发后零买入、仓位上限全程不破。"""
    out = sl.risk_control_probe(
        base_config(per_grid_capital_pct=25.0, grid_count=6,
                    max_drawdown_stop_pct=12.0, max_position_pct=90.0),
        anchor_price=100.0)
    assert out["passed"] is True
    assert out["risk_stop_fired"] is True
    assert out["buys_after_stop"] == 0
    assert out["position_cap_held"] is True
    assert out["liquidated_shares"] > 0


def test_risk_control_probe_reports_failure_fields():
    """止损线设得离谱地高（50%）：探针应如实报告未触发，而不是硬造通过。"""
    out = sl.risk_control_probe(
        base_config(per_grid_capital_pct=10.0, grid_count=3,
                    max_drawdown_stop_pct=50.0, max_position_pct=30.0),
        anchor_price=100.0)
    assert out["passed"] is False
    assert out["risk_stop_fired"] is False
    assert "未通过" in out["note"]
