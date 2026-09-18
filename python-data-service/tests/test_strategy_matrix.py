# -*- coding: utf-8 -*-
"""样本外验证：多时段分段 + 成本归因（把"这条策略行不行"从观点变成一张表）。

<h3>这个文件在防什么</h3>
单个标的、单段历史的回测数字，既可能是运气，也可能是换手磨出来的。而在我们能回答
"这条规则到底行不行"之前，讨论"要给它加什么数据、让 AI 看什么"都是空的。

所以这里钉三件事：

1. **分段不丢数据**：切 N 段必须覆盖全部 K 线（最后一段吃掉余数），
   数据不够时**减少段数并说明**，而不是硬切出几段残缺样本 —— 后者给人"已经验证过"的错觉；
2. **归因能分开方向与摩擦**：同样的信号，22 个来回与 5 个来回的差别常常比"看对方向"更大，
   所以费用必须能单独拿出来看（含"零佣金世界会怎样"）；
3. **逐段结论可汇总**：跑赢买入持有的段数、平均超额、最差/最好、摩擦总额。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.strategy_engine import (  # noqa: E402
    MIN_TRADABLE_BARS,
    attribute_costs,
    evaluate_strategy_matrix,
    evaluate_strategy_segments,
    required_warmup_bars,
    slice_records,
    split_segments,
    summarize_segments,
)

CONFIG = {
    "schema_version": "1.0", "name": "均线上穿", "symbol": "600519",
    "initial_capital": 100000,
    "data": {"period": "day", "lookback_days": 250},
    "position": {"type": "percent", "size_pct": 100},
    "entry": {"logic": "all",
              "conditions": [{"type": "price_cross_ma", "window": 20, "direction": "above"}]},
    "exit": {"logic": "any", "conditions": [
        {"type": "price_cross_ma", "window": 20, "direction": "below"},
        {"type": "stop_loss_pct", "value": -8}]},
    "risk": {"commission_pct": 0.1, "slippage_pct": 0.1},
}


def make_bars(n=180, wobble=6.0):
    """一段有涨有跌的合成行情：不是为了让策略赚钱，而是为了**能算出数字**。"""
    import math

    bars = []
    for i in range(n):
        close = 100.0 + wobble * math.sin(i / 9.0) + i * 0.05
        bars.append({"date": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                     "open": close + 0.2, "high": close + 1.0,
                     "low": close - 1.0, "close": close, "volume": 1000})
    return bars


# ==================== 1. 区间裁剪 ====================


def test_slice_records_is_inclusive_on_both_ends():
    bars = [{"date": f"2026-01-{day:02d}"} for day in range(1, 11)]
    window = slice_records(bars, "2026-01-03", "2026-01-05")
    assert [item["date"] for item in window] == ["2026-01-03", "2026-01-04", "2026-01-05"]


def test_slice_records_without_bounds_returns_everything():
    bars = [{"date": f"2026-01-{day:02d}"} for day in range(1, 6)]
    assert slice_records(bars) == bars
    assert slice_records(None) == []


# ==================== 2. 分段：覆盖全部、不硬切残缺样本 ====================


def test_segments_cover_every_bar_exactly_once():
    bars = make_bars(180)
    split = split_segments(bars, segments=3)

    assert split["segments_used"] == 3
    covered = [item for segment in split["segments"] for item in segment["records"]]
    assert covered == bars, "分段必须无重无漏、且保持原顺序"
    assert split["segments"][0]["start"] == bars[0]["date"]
    assert split["segments"][-1]["end"] == bars[-1]["date"]


def test_segments_fall_back_when_history_is_short():
    """数据不够就**少切**并说明原因 —— 硬切出几段残缺样本比不验证更糟。"""
    bars = make_bars(70)
    split = split_segments(bars, segments=5)

    assert split["segments_used"] == 1
    assert split["segments_requested"] == 5
    assert split["note"] and "只能切 1 段" in split["note"]


def test_segments_note_is_none_when_the_request_is_satisfied():
    split = split_segments(make_bars(180), segments=3)
    assert split["note"] is None


def test_single_segment_is_labelled_全部():
    split = split_segments(make_bars(60), segments=1)
    assert split["segments_used"] == 1
    assert split["segments"][0]["label"] == "全部"


# ==================== 3. 成本归因：方向 vs 摩擦 ====================


def _fake_result(total_return_pct=-30.0, capital=100000.0):
    return {
        "initial_capital": capital,
        "total_return_pct": total_return_pct,
        "excess_return_pct": -12.0,
        "exposure_pct": 40.0,
        "closed_trades": 22,
        "trade_log": [{"side": "buy", "fee": 5.0}, {"side": "sell", "fee": 6.0}] * 11,
    }


def test_attribution_separates_fees_from_direction():
    attribution = attribute_costs(_fake_result())

    assert attribution["trade_count"] == 22
    assert attribution["round_trips"] == 22
    assert attribution["fees_total"] == 121.0          # (5+6) × 11
    assert attribution["cost_pct_of_capital"] == 0.121
    # 零佣金世界的收益 = 实际收益 + 费用占比
    assert attribution["return_excluding_fees_pct"] == -29.88
    assert attribution["excess_vs_buy_and_hold_pct"] == -12.0


def test_attribution_reports_cost_share_only_when_losing():
    losing = attribute_costs(_fake_result(total_return_pct=-30.0))
    assert losing["cost_share_of_loss_pct"] == 0.4     # 0.121 / 30 ≈ 0.4%

    winning = attribute_costs(_fake_result(total_return_pct=30.0))
    assert winning["cost_share_of_loss_pct"] is None, "赚钱时谈'亏损占比'没有意义"


def test_attribution_tolerates_missing_pieces():
    attribution = attribute_costs({})
    assert attribution["trade_count"] == 0
    assert attribution["cost_pct_of_capital"] is None
    assert attribution["return_excluding_fees_pct"] is None


# ==================== 4. 汇总：能直接判断的几个数 ====================


def test_summary_counts_the_segments_that_beat_buy_and_hold():
    rows = [
        {"total_return_pct": 5.0, "excess_return_pct": 2.0, "attribution": {"cost_pct_of_capital": 0.1, "fees_total": 10.0}},
        {"total_return_pct": -8.0, "excess_return_pct": -3.0, "attribution": {"cost_pct_of_capital": 0.3, "fees_total": 30.0}},
        {"total_return_pct": 1.0, "excess_return_pct": 0.5, "attribution": {"cost_pct_of_capital": 0.2, "fees_total": 20.0}},
    ]
    summary = summarize_segments(rows)

    assert summary["segments_valid"] == 3
    assert summary["beat_buy_and_hold"] == 2
    assert summary["avg_excess_pct"] == -0.17
    assert summary["worst_return_pct"] == -8.0
    assert summary["total_fees"] == 60.0


def test_summary_of_nothing_is_empty_not_a_crash():
    summary = summarize_segments([])
    assert summary["segments_valid"] == 0
    assert summary["avg_excess_pct"] is None
    assert summary["total_fees"] == 0.0


# ==================== 5. 逐段回测与矩阵 ====================


def test_segments_report_per_segment_returns_and_attribution():
    result = evaluate_strategy_segments(CONFIG, make_bars(180), segments=3, symbol="TEST")

    assert result["segments_used"] == 3
    assert len(result["rows"]) == 3
    for row in result["rows"]:
        assert row["bars"] > 0
        assert row["total_return_pct"] is not None
        assert row["attribution"]["trade_count"] >= 0
    assert result["summary"]["segments_valid"] == 3


def test_segments_carry_the_date_range_filter():
    bars = make_bars(180)
    result = evaluate_strategy_segments(CONFIG, bars, segments=2,
                                        start_date=bars[60]["date"],
                                        end_date=bars[150]["date"])
    assert result["bars"] == 91
    assert result["rows"][0]["start"] == bars[60]["date"]
    assert result["rows"][-1]["end"] == bars[150]["date"]


def test_matrix_covers_every_symbol_and_survives_missing_data():
    records = {"AAA": make_bars(180), "BBB": make_bars(150), "CCC": None}
    matrix = evaluate_strategy_matrix(CONFIG, records, segments=3, adjust_mode="qfq")

    assert matrix["symbols"] == ["AAA", "BBB", "CCC"]
    assert matrix["cells_total"] == 6, "取不到数据的标的不参与统计"
    assert matrix["cells_valid"] == 6
    missing = [item for item in matrix["per_symbol"] if item["symbol"] == "CCC"][0]
    assert missing["note"] == "取不到行情数据"
    assert matrix["adjust_mode"] == "qfq"


def test_matrix_exposes_the_contract_basis_for_the_report():
    """报告必须能说明"这些数字是哪个复权口径、哪个引擎、切了几段" —— 否则表本身不可解释。

    这一条是有来历的：验证结论会写进客观事实、被盘后复盘引用，
    而一份不说口径的结论没法跟后来的数字对比（"是变好了还是换了个算法"）。
    """
    matrix = evaluate_strategy_matrix(CONFIG, {"AAA": make_bars(180)}, segments=3,
                                      adjust_mode="qfq", start_date="", end_date="")
    assert matrix["adjust_mode"] == "qfq"
    assert matrix["segments_requested"] == 3
    assert matrix["summary"]["segments_valid"] == 3
    assert len(matrix["engine_version"]) == 12, matrix["engine_version"]


# ==================== 6. 预热期：0 笔成交的两种不同意思 ====================


def test_warmup_comes_from_the_strategy_not_from_a_constant():
    """预热根数是**策略里的数据**（窗口），不是代码里的分支。"""
    assert required_warmup_bars(CONFIG) == 20                     # price_cross_ma window 20
    assert required_warmup_bars({"entry": {"conditions": [
        {"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}]}}) == 60
    assert required_warmup_bars({"exit": {"conditions": [{"type": "macd_cross", "direction": "below"}]}}) == 35
    assert required_warmup_bars({"entry": {"conditions": [{"type": "stop_loss_pct", "value": -8}]}}) == 0
    assert required_warmup_bars({}) == 0
    assert required_warmup_bars(None) == 0


def test_segment_length_floor_follows_the_warmup():
    """这段是实测逼出来的：MA60 的策略被切成 166 根一段时，前 60 根不可能出信号。

    如果段长下限是个与策略无关的常数（40），就会出现"整段都在预热"的格子 ——
    它在表上写着 0.00%，看起来像"验证过、很稳"，其实一格样本都没有。
    """
    config = {**CONFIG, "entry": {"logic": "all", "conditions": [
        {"type": "price_cross_ma", "window": 60, "direction": "above"}]},
        "exit": {"logic": "any", "conditions": [
            {"type": "price_cross_ma", "window": 60, "direction": "below"}]}}

    result = evaluate_strategy_segments(config, make_bars(400), segments=3)

    assert result["warmup_bars"] == 60
    assert result["min_bars_per_segment"] == 60 + MIN_TRADABLE_BARS
    for row in result["rows"]:
        assert row["tradable_bars"] == row["bars"] - 60


def test_a_segment_that_is_all_warmup_says_so_and_is_not_counted():
    """只有预热、没有可交易 bar 的一段，必须写明"等于没验证"，并且不参与统计。"""
    config = {**CONFIG, "entry": {"logic": "all", "conditions": [
        {"type": "price_cross_ma", "window": 60, "direction": "above"}]},
        "exit": {"logic": "any", "conditions": [
            {"type": "price_cross_ma", "window": 60, "direction": "below"}]}}

    # 段长下限是 90，所以 100 根只能切 1 段；直接绕过 split 传一段短数据来触发这个分支
    result = evaluate_strategy_segments(config, make_bars(45), segments=1)

    row = result["rows"][0]
    assert row["tradable_bars"] == 0
    assert "等于没验证" in row["note"]
    assert result["summary"]["segments_valid"] == 0
    assert result["summary"]["segments_warmup_only"] == 1


def test_summary_ignores_warmup_only_cells_but_still_reports_them():
    rows = [
        {"total_return_pct": 0.0, "excess_return_pct": -3.0, "tradable_bars": 0,
         "attribution": {"cost_pct_of_capital": 0.0, "fees_total": 0.0}},
        {"total_return_pct": 5.0, "excess_return_pct": 2.0, "tradable_bars": 100,
         "attribution": {"cost_pct_of_capital": 0.1, "fees_total": 10.0}},
    ]
    summary = summarize_segments(rows)

    assert summary["segments_valid"] == 1, "没有样本的格子不能充数"
    assert summary["segments_warmup_only"] == 1
    assert summary["avg_total_return_pct"] == 5.0, "0.00% 不该把结论往'不赚不亏'拉"


# ==================== 7. 买不起一手：0.00% 的第三种意思 ====================


def _high_priced_bars(unit=1500.0, n_down=60, n_up=60):
    """先跌后涨：MA20 必然被上穿一次 —— 用来验证"有信号但买不起"这条路径。"""
    bars = []
    price = unit
    for i in range(n_down + n_up):
        price = unit - i * 6.0 if i < n_down else unit - n_down * 6.0 + (i - n_down) * 9.0
        bars.append({"date": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                     "open": price, "high": price + 2, "low": price - 2,
                     "close": price, "volume": 1000})
    return bars


def test_a_signal_that_cannot_be_afforded_is_not_reported_as_no_signal():
    """茅台实测：上穿 60 日线在第三段触发了 6 次，成交仍是 0 笔。

    因为一手（100 股 × ~1400 元）就超过 10 万本金。表上写 0.00%，
    读起来像"这段没机会"，实际是"有 6 次机会，只是这个本金做不了" ——
    这两件事对"要不要用这条策略"的意义完全相反，不能在表里长成一个样子。
    """
    result = evaluate_strategy_segments(CONFIG, _high_priced_bars(), segments=1)

    row = result["rows"][0]
    assert row["trade_count"] == 0
    assert row["funding_note"], "引擎早就报出了原因，表格不该装作看不见"
    assert "一手就超过本金" in row["note"]
    assert result["summary"]["segments_valid"] == 0
    assert result["summary"]["segments_unaffordable"] == 1


def test_the_same_bars_do_trade_once_the_capital_is_enough():
    """同样的行情、同样的规则，只是本金够了 —— 成交就出现。

    这条是对着上一条写的：如果只测"0 笔 + 有 note"，一个"信号其实根本没触发"的实现
    也能蒙过去。两行对着看，才能确认 note 说的是真原因。
    """
    rich = {**CONFIG, "initial_capital": 1_000_000}
    result = evaluate_strategy_segments(rich, _high_priced_bars(), segments=1)

    row = result["rows"][0]
    assert row["trade_count"] > 0
    assert not row["funding_note"]
    assert "note" not in row or row["note"] is None
    assert result["summary"]["segments_valid"] == 1
    assert result["summary"]["segments_unaffordable"] == 0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print("PASS", fn.__name__)
