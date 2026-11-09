# -*- coding: utf-8 -*-
"""前日预案制：clean_plan 归一 + evaluate_plan 判定矩阵的单元测试。

判定矩阵必须覆盖：失效优先于触发、量比叠加"未满足≠失效"、跳空出区间按失效、
空仓只看 entries / 持仓只看 exits、均线穿越的"昨收在另一侧"前置条件。
"""
import pytest

from agent import trade_plan as tp


# ==================== clean_plan ====================

class TestCleanPlan:
    def test_missing_returns_none(self):
        w = []
        assert tp.clean_plan(None, w) is None
        assert tp.clean_plan("buy if you dare", w) is None
        assert any("不是对象" in x for x in w)

    def test_minimal_empty_plan_is_kept(self):
        """空预案（纯观望）也是一份明确的预案——保留而不是丢弃。"""
        w = []
        plan = tp.clean_plan({"valid_days": 1, "summary": "明天观望"}, w)
        assert plan == {"valid_days": 1, "summary": "明天观望",
                        "entries": [], "exits": [], "invalidation": []}
        assert w == []

    def test_full_plan_roundtrip(self):
        raw = {
            "valid_days": 2, "summary": "上穿MA60且量比1.5买入",
            "entries": [{"side": "buy",
                         "trigger": {"type": "ma_cross_above", "window": 60,
                                     "require_volume_ratio": 1.5},
                         "size_fraction": 0.3,
                         "valid_price_range": [3.30, 4.50]}],
            "exits": [{"side": "sell",
                       "trigger": {"type": "price_below", "value": 3.30},
                       "size_fraction": 1.0}],
            "invalidation": [{"type": "price_below", "value": 3.30},
                             {"type": "news_high_impact"}],
        }
        w = []
        plan = tp.clean_plan(raw, w)
        assert w == []
        assert plan["valid_days"] == 2
        assert plan["entries"][0]["trigger"]["window"] == 60
        assert plan["invalidation"][1] == {"type": "news_high_impact"}

    def test_unknown_trigger_type_dropped(self):
        w = []
        plan = tp.clean_plan({"entries": [
            {"trigger": {"type": "rsi_above", "value": 70}, "size_fraction": 0.5}]}, w)
        assert plan["entries"] == []
        assert any("不在词表内" in x for x in w)

    def test_price_trigger_requires_positive_value(self):
        w = []
        plan = tp.clean_plan({"entries": [
            {"trigger": {"type": "price_above", "value": -1}, "size_fraction": 0.5}]}, w)
        assert plan["entries"] == []
        assert any("非正数" in x for x in w)

    def test_size_fraction_clamped_and_zero_dropped(self):
        w = []
        plan = tp.clean_plan({
            "entries": [
                {"trigger": {"type": "price_above", "value": 4.0}, "size_fraction": 2.5},
                {"trigger": {"type": "price_above", "value": 4.0}, "size_fraction": 0},
            ]}, w)
        assert len(plan["entries"]) == 1
        assert plan["entries"][0]["size_fraction"] == 1.0
        assert any("夹到 1.0" in x for x in w)
        assert any("≤0" in x for x in w)

    def test_valid_days_clamped(self):
        w = []
        plan = tp.clean_plan({"valid_days": 99}, w)
        assert plan["valid_days"] == tp.MAX_VALID_DAYS
        assert any("越界" in x for x in w)

    def test_ma_cross_window_default(self):
        w = []
        plan = tp.clean_plan({"entries": [
            {"trigger": {"type": "ma_cross_above"}, "size_fraction": 0.3}]}, w)
        assert plan["entries"][0]["trigger"]["window"] == tp.DEFAULT_MA_WINDOW
        assert any("默认" in x for x in w)

    def test_wrong_side_dropped(self):
        w = []
        plan = tp.clean_plan({"entries": [
            {"side": "sell", "trigger": {"type": "price_above", "value": 4.0},
             "size_fraction": 1.0}]}, w)
        assert plan["entries"] == []
        assert any("side" in x for x in w)

    def test_limits_truncated(self):
        raw = {"entries": [{"trigger": {"type": "price_above", "value": 1},
                            "size_fraction": 0.1}] * 5,
               "invalidation": [{"type": "price_below", "value": 1}] * 5}
        w = []
        plan = tp.clean_plan(raw, w)
        assert len(plan["entries"]) == tp.MAX_ENTRIES
        assert len(plan["invalidation"]) == tp.MAX_INVALIDATIONS
        assert any("超过" in x for x in w)


# ==================== evaluate_plan ====================

def _days(closes, today="2026-11-10", today_volume=None):
    """构造 day_records：完结日线 + 可选的今日运行 bar。"""
    from datetime import date as _d, timedelta
    start = _d(2026, 1, 1)
    records = []
    for i, close in enumerate(closes):
        # 跳过周末，凑出连续交易日
        d = start
        for _ in range(i):
            d += timedelta(days=1 if d.weekday() != 4 else 3)
        records.append({"date": d.isoformat(), "close": close, "volume": 1_000_000})
    if today_volume is not None:
        records.append({"date": today, "close": 4.0, "volume": today_volume})
    return records


class TestEvaluatePlan:
    def test_no_plan_holds(self):
        out = tp.evaluate_plan(None, price=4.0, day_records=[], position=None,
                               today="2026-11-10")
        assert out["action"] == "hold" and out["invalidated"] is None

    def test_no_price_holds_without_judgement(self):
        plan = tp.clean_plan({"entries": [{"trigger": {"type": "price_above",
                                                       "value": 3.9},
                                           "size_fraction": 0.3}]}, [])
        out = tp.evaluate_plan(plan, price=None, day_records=[], position=None,
                               today="2026-11-10")
        assert out["action"] == "hold"
        assert "现价" in out["detail"]

    def test_price_above_triggers_when_flat(self):
        plan = tp.clean_plan({"entries": [{"trigger": {"type": "price_above",
                                                       "value": 3.9},
                                           "size_fraction": 0.3}]}, [])
        out = tp.evaluate_plan(plan, price=3.95, day_records=_days([3.5] * 70),
                               position=None, today="2026-11-10")
        assert out["action"] == "buy"
        assert out["matched"]["size_fraction"] == 0.3

    def test_price_above_not_triggered(self):
        plan = tp.clean_plan({"entries": [{"trigger": {"type": "price_above",
                                                       "value": 3.9},
                                           "size_fraction": 0.3}]}, [])
        out = tp.evaluate_plan(plan, price=3.85, day_records=_days([3.5] * 70),
                               position=None, today="2026-11-10")
        assert out["action"] == "hold" and out["matched"] is None

    def test_invalidation_beats_trigger(self):
        """失效优先于触发：同一价格既触发又失效时，预案死。"""
        plan = tp.clean_plan({
            "entries": [{"trigger": {"type": "price_above", "value": 3.9},
                         "size_fraction": 0.3}],
            "invalidation": [{"type": "price_below", "value": 3.9}]}, [])
        out = tp.evaluate_plan(plan, price=3.85, day_records=_days([3.5] * 70),
                               position=None, today="2026-11-10")
        assert out["invalidated"] is not None
        assert out["action"] == "hold"

    def test_news_invalidation(self):
        plan = tp.clean_plan({
            "entries": [{"trigger": {"type": "price_above", "value": 3.9},
                         "size_fraction": 0.3}],
            "invalidation": [{"type": "news_high_impact"}]}, [])
        out = tp.evaluate_plan(plan, price=4.0, day_records=_days([3.5] * 70),
                               position=None, today="2026-11-10",
                               news_high_impact=True)
        assert out["invalidated"] is not None and "新闻" in out["invalidated"]
        # 没有新闻标记时同一价格照常触发
        out2 = tp.evaluate_plan(plan, price=4.0, day_records=_days([3.5] * 70),
                                position=None, today="2026-11-10",
                                news_high_impact=False)
        assert out2["action"] == "buy"

    def test_gap_out_of_range_invalidates(self):
        """触发价到了但跳空出 valid_price_range → 按失效处理（异常跳空不是机会）。"""
        plan = tp.clean_plan({
            "entries": [{"trigger": {"type": "price_above", "value": 3.9},
                         "size_fraction": 0.3,
                         "valid_price_range": [3.8, 4.1]}]}, [])
        out = tp.evaluate_plan(plan, price=4.5, day_records=_days([3.5] * 70),
                               position=None, today="2026-11-10")
        assert out["invalidated"] is not None and "区间" in out["invalidated"]

    def test_volume_ratio_gate_defers_not_invalidates(self):
        """量比叠加未满足是"还没触发"不是"失效"——量能盘中累计上升，晚些可满足。"""
        records = _days([3.5] * 70, today_volume=800_000)   # 量比 0.8
        plan = tp.clean_plan({
            "entries": [{"trigger": {"type": "price_above", "value": 3.9,
                                     "require_volume_ratio": 1.5},
                         "size_fraction": 0.3}]}, [])
        out = tp.evaluate_plan(plan, price=3.95, day_records=records,
                               position=None, today="2026-11-10")
        assert out["action"] == "hold" and out["invalidated"] is None
        assert "量比" in out["detail"]
        # 放量后同一价格触发
        records2 = _days([3.5] * 70, today_volume=2_000_000)  # 量比 2.0
        out2 = tp.evaluate_plan(plan, price=3.95, day_records=records2,
                                position=None, today="2026-11-10")
        assert out2["action"] == "buy"

    def test_ma_cross_above_requires_prev_close_below(self):
        """昨收必须在均线下方才算"上穿"——已站上均线的票不构成穿越事件。"""
        # 昨收 3.5、MA60≈3.6：在下方；现价 3.7 站上盘中均线 → 触发
        closes = [3.7] * 59 + [3.5]
        plan = tp.clean_plan({
            "entries": [{"trigger": {"type": "ma_cross_above", "window": 60},
                         "size_fraction": 0.3}]}, [])
        out = tp.evaluate_plan(plan, price=3.72, day_records=_days(closes),
                               position=None, today="2026-11-10")
        assert out["action"] == "buy", out["detail"]

        # 昨收 3.8 已在 MA60（≈3.75）上方：不算上穿（本来就站着）
        closes2 = [3.7] * 59 + [3.8]
        out2 = tp.evaluate_plan(plan, price=3.82, day_records=_days(closes2),
                                position=None, today="2026-11-10")
        assert out2["action"] == "hold"

    def test_ma_cross_below_for_holding(self):
        closes = [3.7] * 59 + [3.9]
        plan = tp.clean_plan({
            "exits": [{"trigger": {"type": "ma_cross_below", "window": 60},
                       "size_fraction": 1.0}]}, [])
        out = tp.evaluate_plan(plan, price=3.55, day_records=_days(closes),
                               position={"shares": 1000, "entry_price": 3.8},
                               today="2026-11-10")
        assert out["action"] == "sell"

    def test_flat_evaluates_entries_holding_evaluates_exits(self):
        """空仓只看 entries（exits 的条件满足也不卖空）；持仓只看 exits。"""
        plan = tp.clean_plan({
            "entries": [{"trigger": {"type": "price_above", "value": 3.9},
                         "size_fraction": 0.3}],
            "exits": [{"trigger": {"type": "price_below", "value": 4.1},
                       "size_fraction": 1.0}]}, [])
        # 空仓 + exit 条件满足（3.5 ≤ 4.1）但 entry 未满足（3.5 < 3.9）——不该卖空
        out = tp.evaluate_plan(plan, price=3.5, day_records=_days([3.5] * 70),
                               position=None, today="2026-11-10")
        assert out["action"] == "hold"
        # 持仓 + entry 条件满足（3.95 ≥ 3.9）但按持仓纪律走 exits：
        # price_below 4.1 → 3.95 ≤ 4.1 命中 exit → sell（不是按入场加仓）
        out2 = tp.evaluate_plan(plan, price=3.95, day_records=_days([3.5] * 70),
                                position={"shares": 100, "entry_price": 3.6},
                                today="2026-11-10")
        assert out2["action"] == "sell"

    def test_insufficient_history_no_trigger(self):
        closes = [3.5] * 30   # 不足 60 根
        plan = tp.clean_plan({
            "entries": [{"trigger": {"type": "ma_cross_above", "window": 60},
                         "size_fraction": 0.3}]}, [])
        out = tp.evaluate_plan(plan, price=4.5, day_records=_days(closes),
                               position=None, today="2026-11-10")
        assert out["action"] == "hold" and "不足" in out["detail"]
