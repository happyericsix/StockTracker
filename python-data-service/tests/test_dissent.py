# -*- coding: utf-8 -*-
"""确定性空方简报（dissent）：阈值、纯函数性、"只给事实不给指令"。

这份简报的价值恰恰在于**它不是模型**：同样的证据包必须永远得到同样的清单，
每条事实必须带数字、可核对，且永不出现"卖出"这类结论性指令。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import dissent  # noqa: E402


def evidence_of(closes, volumes=None, *, ma20=None, ma60=None, rsi=None,
                ret60=None, drawdown=None, position=None):
    """拼一个最小证据包：bear_case_facts 只读 indicators/derived/recent_bars/position。"""
    volumes = volumes if volumes is not None else [1000.0] * len(closes)
    return {
        "last_close": closes[-1],
        "indicators": {"ma_20": ma20, "ma_60": ma60, "rsi": rsi},
        "derived": {"return_60d_pct": ret60, "distance_to_60d_high_pct": drawdown,
                    "volume_vs_20d_avg": None},
        "recent_bars": [{"date": f"2026-03-{i + 1:02d}", "open": c, "high": c,
                         "low": c, "close": c, "volume": v}
                        for i, (c, v) in enumerate(zip(closes, volumes))],
        "position": position or {"shares": 0.0, "avg_cost": None, "high_watermark": None},
    }


def facts_of(ev):
    return {item["fact"] for item in dissent.bear_case_facts(ev)}


# ==================== ① 阈值命中 ====================

def test_death_alignment_is_a_fact():
    ev = evidence_of([10.0] * 25, ma20=9.8, ma60=10.2)
    assert "death_alignment" in facts_of(ev)
    detail = next(f["detail"] for f in dissent.bear_case_facts(ev)
                  if f["fact"] == "death_alignment")
    assert "9.8" in detail and "10.2" in detail, "事实必须带可核对的数字"


def test_bullish_alignment_is_not_a_fact():
    ev = evidence_of([10.0] * 25, ma20=10.2, ma60=9.8)
    assert "death_alignment" not in facts_of(ev)


def test_deep_drawdown_and_negative_return_are_facts():
    ev = evidence_of([10.0] * 25, drawdown=-12.5, ret60=-15.0)
    facts = facts_of(ev)
    assert "drawdown_from_high" in facts
    assert "negative_60d_return" in facts


def test_shallow_drawdown_is_just_breathing_room():
    """浅回撤是趋势里的正常呼吸，不该进简报（阈值 -8%）。"""
    ev = evidence_of([10.0] * 25, drawdown=-5.0, ret60=3.0)
    assert "drawdown_from_high" not in facts_of(ev)
    assert "negative_60d_return" not in facts_of(ev)


def test_overbought_rsi_flags_against_fresh_longs():
    ev = evidence_of([10.0] * 25, rsi=78.0)
    assert "overbought" in facts_of(ev)
    ev2 = evidence_of([10.0] * 25, rsi=55.0)
    assert "overbought" not in facts_of(ev2)


def test_price_volume_divergence_needs_shrinking_volume():
    """价升量缩：近 5 日价格上行、均量缩到之前 15 日的 70% 以下才算。"""
    rises = [10.0 + 0.2 * i for i in range(20)]
    thin = [1000.0] * 15 + [500.0] * 5          # 最近 5 日量减半（老在前、新在后）
    ev = evidence_of(rises, volumes=thin)
    assert "price_volume_divergence" in facts_of(ev)

    steady = [1000.0] * 20                                  # 量没缩 → 不算
    ev2 = evidence_of(rises, volumes=steady)
    assert "price_volume_divergence" not in facts_of(ev2)


def test_distribution_day_needs_down_close_and_volume():
    ev = evidence_of([10.0] * 25)
    ev["recent_bars"][-1] = {"date": "2026-03-25", "open": 10.5, "high": 10.6,
                             "low": 9.9, "close": 10.0, "volume": 1000}
    ev["derived"]["volume_vs_20d_avg"] = 2.0
    assert "distribution_day" in facts_of(ev)

    ev["recent_bars"][-1]["close"] = 10.8                   # 收阳了 → 不算
    assert "distribution_day" not in facts_of(ev)


def test_underwater_position_is_a_fact_against_holding():
    ev = evidence_of([10.0] * 25, position={"shares": 1000, "avg_cost": 11.0,
                                            "high_watermark": 12.0})
    assert "underwater_position" in facts_of(ev)
    ev2 = evidence_of([10.0] * 25, position={"shares": 1000, "avg_cost": 9.0,
                                             "high_watermark": 12.0})
    assert "underwater_position" not in facts_of(ev2)


# ==================== ② 纪律 ====================

def test_a_healthy_uptrend_yields_no_facts():
    ev = evidence_of([10.0 + 0.3 * i for i in range(25)],
                     ma20=16.0, ma60=15.0, rsi=62.0, ret60=8.0, drawdown=-1.0)
    assert dissent.bear_case_facts(ev) == []
    assert dissent.brief_for(ev) is None, "没有事实就不注入空块"


def test_facts_never_contain_an_instruction():
    """简报是论据不是结论：任何事实的措辞里都不该出现买卖指令。"""
    ev = evidence_of([10.0] * 25, ma20=9.8, ma60=10.2, rsi=75.0, drawdown=-10.0)
    for item in dissent.bear_case_facts(ev):
        assert "卖出" not in item["detail"]
        assert "清仓" not in item["detail"]
        assert "建议" not in item["detail"]


def test_the_brief_is_a_pure_function():
    ev = evidence_of([10.0 + 0.1 * i for i in range(25)], ma20=10.5, ma60=10.8)
    assert dissent.bear_case_facts(ev) == dissent.bear_case_facts(ev)


def test_fact_count_is_capped():
    """它是下限保障不是清单大全：超过上限的条数会被截掉（防模型略读）。"""
    ev = evidence_of([10.0] * 25, ma20=9.8, ma60=10.2, rsi=80.0, drawdown=-20.0,
                     ret60=-30.0,
                     position={"shares": 1000, "avg_cost": 15.0, "high_watermark": 16.0})
    ev["recent_bars"][-1] = {"date": "2026-03-25", "open": 10.5, "high": 10.6,
                             "low": 9.9, "close": 10.0, "volume": 1000}
    ev["derived"]["volume_vs_20d_avg"] = 2.5
    facts = dissent.bear_case_facts(ev)
    assert len(facts) <= dissent.MAX_FACTS
    assert len(facts) >= 6, "极端利空证据下六类事实都该命中（再按上限截断）"


def test_bad_values_are_skipped_not_raised():
    """证据包里有 None/字符串等坏值时跳过该条事实，不许炸。"""
    ev = evidence_of([10.0] * 25, ma20="bad", ma60=None, rsi=None,
                     ret60=None, drawdown=None)
    assert dissent.bear_case_facts(ev) == []


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print("PASS", fn.__name__)
