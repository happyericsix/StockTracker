# -*- coding: utf-8 -*-
"""估值分位条件（pe_percentile）的口径用例。

_fetch_valuation_history 被替换成确定性 PE 序列——回测必须是纯函数。
核心三问：分位算得对不对（与 market_context 的估值语境同口径）；
as-of 守卫住不住（晚于 bar 的估值行不许影响历史 bar）；数据缺席时
守不守得住（fail-closed：没有估值信号，绝不是信号永远满足）。
"""

import math
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_bars(n=320):
    bars, price, d = [], 100.0, date(2024, 1, 2)
    for _ in range(n):
        bars.append({"date": d.isoformat(), "open": price, "close": price,
                     "high": price, "low": price, "volume": 1000})
        price += 0.1
        d += timedelta(days=1)
    return bars


def pe_series(bars, start=50.0, end=10.0):
    """线性递减 PE：每个 bar 日期一行（当前值恒为窗口最小 → 分位应≈0）。"""
    n = len(bars)
    return [{"date": b["date"], "pe_ttm": round(start + (end - start) * i / (n - 1), 4)}
            for i, b in enumerate(bars)]


CFG = {
    "schema_version": "1.0",
    "name": "pe-filter",
    "symbol": "600519",
    "initial_capital": 100000,
    "data": {"period": "day", "lookback_days": 250},
    "position": {"type": "percent", "size_pct": 100},
    "entry": {"logic": "all", "conditions": [
        {"type": "pe_percentile", "window": 250, "direction": "below", "value": 30}]},
    "exit": {"logic": "any", "conditions": [
        {"type": "pe_percentile", "window": 250, "direction": "above", "value": 70}]},
    "risk": {"commission_pct": 0.0, "slippage_pct": 0.0},
}


def test_percentile_math_matches_context_convention(monkeypatch):
    """递减序列的当前值是窗口最小（分位≈0），升序序列是窗口最大（≈100）；
    窗口不足 250 行时无分位（NaN）——这是 schema 最小窗口 250 的引擎侧对应。"""
    from agent import strategy_engine as se
    from agent.strategy_schema import validate_strategy_config

    cfg, err = validate_strategy_config(CFG)
    assert err is None
    bars = make_bars()
    monkeypatch.setattr(se, "_fetch_valuation_history", lambda s: pe_series(bars))
    ind = se.compute_indicators(bars)
    se._attach_valuation(ind, cfg)
    arr = ind["pe_pct_250"]
    assert math.isnan(arr[100]) and math.isnan(arr[0])
    assert arr[300] < 1.0

    rising = [{"date": r["date"], "pe_ttm": round(100 - r["pe_ttm"], 4)}
              for r in pe_series(bars)]
    monkeypatch.setattr(se, "_fetch_valuation_history", lambda s: rising)
    ind2 = se.compute_indicators(bars)
    se._attach_valuation(ind2, cfg)
    assert ind2["pe_pct_250"][300] > 99.0


def test_asof_guard_future_pe_rows_cannot_leak(monkeypatch):
    """序列里晚于最后一根 bar 的估值行（回放当时还不存在）不许改变任何分位。"""
    import numpy as np
    from agent import strategy_engine as se
    from agent.strategy_schema import validate_strategy_config

    cfg, _ = validate_strategy_config(CFG)
    bars = make_bars()
    base = pe_series(bars)
    last = date.fromisoformat(bars[-1]["date"])
    extended = base + [{"date": (last + timedelta(days=k)).isoformat(), "pe_ttm": 999.0}
                       for k in range(1, 31)]
    outs = []
    for series in (base, extended):
        monkeypatch.setattr(se, "_fetch_valuation_history",
                            lambda s, _x=series: _x)
        ind = se.compute_indicators(bars)
        se._attach_valuation(ind, cfg)
        outs.append(np.nan_to_num(ind["pe_pct_250"], nan=-1.0))
    assert (outs[0] == outs[1]).all()


def test_backtest_fires_only_in_cheap_zone(monkeypatch):
    """PE 一路走低：买入信号只可能出现在窗口攒够之后的"便宜区"。"""
    from agent import strategy_engine as se

    bars = make_bars()
    monkeypatch.setattr(se, "_fetch_valuation_history", lambda s: pe_series(bars))
    bt = se.run_backtest_realistic(CFG, bars)
    assert not bt.get("error")
    buys = [t for t in bt["trade_log"] if t.get("side") == "buy"]
    assert len(buys) >= 1
    # 首笔成交（次日开盘）必须落在后段：前 249 根窗口不足，分位恒不成立
    assert buys[0]["date"] > bars[240]["date"]


def test_fail_closed_without_valuation_data(monkeypatch):
    """估值数据缺席：不报错、不成交——缺数据的表现是"没有信号"。"""
    from agent import strategy_engine as se

    bars = make_bars()
    monkeypatch.setattr(se, "_fetch_valuation_history", lambda s: [])
    bt = se.run_backtest_realistic(CFG, bars)
    assert not bt.get("error")
    assert bt["trade_log"] == []


def test_no_valuation_fetch_when_unused(monkeypatch):
    """不含估值条件的策略一次估值都不取（默认路径零开销、零网络）。"""
    from agent import strategy_engine as se

    calls = []

    def spy(symbol):
        calls.append(symbol)
        return pe_series(make_bars())

    monkeypatch.setattr(se, "_fetch_valuation_history", spy)
    cfg = dict(CFG)
    cfg["entry"] = {"logic": "all", "conditions": [
        {"type": "price_cross_ma", "window": 60, "direction": "above"}]}
    cfg["exit"] = {"logic": "any", "conditions": [
        {"type": "stop_loss_pct", "value": -8}]}
    se.run_backtest_realistic(cfg, make_bars())
    assert calls == []


def test_valuation_percentile_series_asof_and_floor(monkeypatch):
    """估值分位序列：窗口不足 250 根为 None；分位口径与 DSL/语境一致（<=当前值占比）。"""
    from datetime import date, timedelta
    from agent import strategy_engine  # noqa: F401  （确保模块路径可用）
    import akshare_client

    start = date(2024, 1, 1)
    rows = [{"date": (start + timedelta(days=i)).isoformat(), "pe_ttm": 50 - 40 * i / 299}
            for i in range(300)]  # 线性递减 300 根
    monkeypatch.setattr(akshare_client, "get_valuation_history", lambda s: rows)
    series = akshare_client.get_valuation_percentile_series("600519")
    assert len(series) == 300
    assert series[100]["pct"] is None            # 不足 250 根：无分位
    assert series[-1]["pct"] is not None and series[-1]["pct"] < 1.0  # 递减尾部≈最便宜
    assert series[-1]["pe"] == rows[-1]["pe_ttm"]

    monkeypatch.setattr(akshare_client, "get_valuation_history", lambda s: [])
    assert akshare_client.get_valuation_percentile_series("600519") == []
