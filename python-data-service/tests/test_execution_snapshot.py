# -*- coding: utf-8 -*-
"""执行契约（消费者）：`evaluate-bar` 必须回传"口径 + 证据快照"。

<h3>这个文件在防什么</h3>
契约冻结了常量与指纹（见 `test_execution_contract.py`），但**契约只有在被真正回传时才有意义**。
在此之前，调用方拿到的是一个裸的 `{"signal", "price"}`：它不知道这个价是收盘价还是开盘价、
用的是哪根 bar、当时指标是多少 —— 于是"这条痕迹为什么这样成交"永远答不出来。

所以这里钉四件事：
1. **口径随结果走**：`fill_basis` / `fingerprint` 出现在**每一条返回分支**上（含无数据与数据不足）；
2. **成交价候选都给**：`fills.close` 与 `fills.next_open`（末根没有下一根时显式为 `None`）——
   将来把日线切到"次日开盘"只需改映射与取价处，不必再改契约；
3. **快照是证据**：当根 bar 的 OHLC、当时算出的指标值（**恰好是策略用到的那几个**）、
   逐条件命中明细；缺数据时指标是 `None` 而不是 0；
4. **口径不明就显形**：调用方没声明结算类型时归一成 `unknown_*`，**不许猜**。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import execution_contract as ec  # noqa: E402
from agent.strategy_engine import compute_indicators, evaluate_bar  # noqa: E402

CONFIG = {
    "schema_version": "1.0", "name": "均线上穿", "symbol": "600519",
    "initial_capital": 100000,
    "data": {"period": "day", "lookback_days": 250},
    "position": {"type": "percent", "size_pct": 100},
    "entry": {"logic": "all",
              "conditions": [{"type": "price_cross_ma", "window": 20, "direction": "above"}]},
    "exit": {"logic": "any", "conditions": [{"type": "stop_loss_pct", "value": -8}]},
    "risk": {"commission_pct": 0.1, "slippage_pct": 0.1},
}

# 平盘 30 根 → 跳上 120 → 跌到 80：上穿第 20 日均线恰好发生在第 30 根。
FLAT, UP, DOWN = 30, 20, 30


def make_bars():
    prices = [100.0] * FLAT + [120.0] * UP + [80.0] * DOWN
    return [
        {"date": f"2026-02-{i + 1:02d}", "open": price + 0.5, "high": price + 1,
         "low": price - 1, "close": price, "volume": 1000}
        for i, price in enumerate(prices)
    ]


def _crossing_date(records):
    return records[FLAT]["date"]


# ==================== 1. 正常分支：口径 + 候选成交价 + 快照 ====================


def test_response_carries_the_basis_and_both_fill_candidates():
    records = make_bars()
    result = evaluate_bar(CONFIG, records, _crossing_date(records), None, "daily", "qfq")

    assert result["signal"] == "buy"
    assert result["fill_basis"] == ec.FILL_CLOSE
    assert isinstance(result["fills"]["close"], float)
    assert result["fills"]["close"] == records[FLAT]["close"]
    # 下一根开盘价也在（将来切到 next_open 时不必再改契约）
    assert result["fills"]["next_open"] == records[FLAT + 1]["open"]
    assert result["fingerprint"]["fill_basis"] == ec.FILL_CLOSE
    assert result["fingerprint"]["adjust_mode"] == "qfq"
    assert result["fingerprint"]["money_policy_version"] == ec.MONEY.version
    assert len(result["fingerprint"]["engine_version"]) == 12


def test_snapshot_is_the_evidence_of_that_bar():
    records = make_bars()
    result = evaluate_bar(CONFIG, records, _crossing_date(records), None, "daily", "qfq")
    snapshot = result["snapshot"]

    assert tuple(snapshot) == ec.SNAPSHOT_KEYS
    assert snapshot["schema_version"] == ec.SNAPSHOT_SCHEMA_VERSION
    bar = snapshot["bar"]
    assert bar["date"] == records[FLAT]["date"]
    for key in ("open", "high", "low", "close", "volume"):
        assert bar[key] == records[FLAT][key], key


def test_snapshot_indicators_are_exactly_what_the_strategy_uses():
    records = make_bars()
    result = evaluate_bar(CONFIG, records, _crossing_date(records), None, "daily", "qfq")
    indicators = result["snapshot"]["indicators"]

    assert sorted(indicators) == ec.indicator_keys_for(CONFIG)
    computed = compute_indicators(records)
    assert indicators["close"] == records[FLAT]["close"]
    # 指标必须与引擎算出来的一致（快照是证据，不是重新编的一份）
    assert indicators["ma_20"] == round(float(computed["ma_20"][FLAT]), 4)


def test_snapshot_records_which_condition_fired():
    """`matched_conditions` 只有类型名，看不出"当时那条到底成不成立" —— 明细补上这一层。"""
    records = make_bars()
    result = evaluate_bar(CONFIG, records, _crossing_date(records), None, "daily", "qfq")
    detail = result["snapshot"]["extra"]["matched_detail"]

    assert len(detail) == len(CONFIG["entry"]["conditions"])
    entry = detail[0]
    assert entry["type"] == "price_cross_ma"
    assert entry["params"] == {"window": 20, "direction": "above"}
    assert entry["hit"] is True


def test_snapshot_is_json_serializable():
    """快照要落库、要过 HTTP：里面不能有 Decimal 之类的不可序列化对象。"""
    records = make_bars()
    result = evaluate_bar(CONFIG, records, _crossing_date(records), None, "daily", "qfq")
    text = json.dumps(result["snapshot"], ensure_ascii=False)
    assert "price_cross_ma" in text
    assert isinstance(result["fills"]["close"], float)


# ==================== 2. 边界：末根 / 数据不足 / 无 bar ====================


def test_last_bar_has_no_next_open():
    records = make_bars()
    result = evaluate_bar(CONFIG, records, records[-1]["date"], None, "daily", "qfq")
    assert result["fills"]["close"] == records[-1]["close"]
    assert result["fills"]["next_open"] is None


def test_short_history_still_carries_the_basis_and_a_snapshot():
    """数据不足（idx < 20）也要带口径与快照：否则这一天为什么没动就无从解释。"""
    records = make_bars()[:10]
    result = evaluate_bar(CONFIG, records, records[-1]["date"], None, "daily", "qfq")
    assert result["signal"] == "hold"
    assert result["fill_basis"] == ec.FILL_CLOSE
    assert result["snapshot"] is not None


def test_missing_bar_still_carries_the_basis_but_no_snapshot():
    """请求的 bar 不存在（非交易日/数据未出）：**没有证据，但口径仍要写清楚**。"""
    records = make_bars()
    result = evaluate_bar(CONFIG, records, "2099-01-01", None, "daily", "qfq")
    assert result["bar_date_missing"] is True
    assert result["snapshot"] is None
    assert result["fills"] == {"close": None, "next_open": None}
    assert result["fill_basis"] == ec.FILL_CLOSE
    assert result["fingerprint"]["adjust_mode"] == "qfq"


def test_snapshot_bar_normalises_string_numbers():
    """真实数据源把 OHLC 给成**字符串**（`"1277.270"`）：快照里必须变成数字。

    否则读痕迹的人和模型看到的是一串带引号的数字 —— 不能直接比大小，
    而且"7.2 与 7.20 是不是同一个"这种问题会在渲染层重新出现。
    """
    records = make_bars()
    for record in records:
        for key in ("open", "high", "low", "close", "volume"):
            record[key] = f"{record[key]:.3f}"
    result = evaluate_bar(CONFIG, records, _crossing_date(records), None, "daily", "qfq")
    bar = result["snapshot"]["bar"]

    for key in ("open", "high", "low", "close", "volume"):
        assert isinstance(bar[key], float), (key, bar[key])
    assert bar["close"] == float(records[FLAT]["close"])
    assert isinstance(bar["date"], str)


def test_snapshot_bar_tolerates_missing_and_non_numeric_values():
    """直接测这一层：**引擎本身需要 OHLC 才能算指标**，所以"字段残缺"这件事
    只能在快照构造函数上验证。转不了的值保持原样、缺字段就是缺 —— 都不补 0。"""
    from agent.strategy_engine import _snapshot_bar

    bar = _snapshot_bar({"date": "2026-09-14", "open": "1277.270", "close": None,
                         "volume": "数据缺失"})
    assert bar["open"] == 1277.27
    assert bar["close"] is None
    assert bar["volume"] == "数据缺失"
    assert bar["date"] == "2026-09-14"


# ==================== 3. 口径由调用方声明，不许猜 ====================


def test_realtime_settlement_declares_its_own_basis():
    records = make_bars()
    result = evaluate_bar(CONFIG, records, _crossing_date(records), None, "realtime", "qfq")
    assert result["fill_basis"] == ec.FILL_REALTIME_LAST
    assert result["fingerprint"]["fill_basis"] == ec.FILL_REALTIME_LAST


def test_undeclared_settlement_kind_becomes_unknown_not_close():
    """没声明就**不许按 close 猜**：口径猜错会让这条记录永远说不清是哪个价。"""
    records = make_bars()
    result = evaluate_bar(CONFIG, records, _crossing_date(records), None, None, None)
    assert result["fill_basis"] == "unknown_unspecified"
    assert result["fingerprint"]["adjust_mode"] == "unknown_unspecified"


def test_daily_and_realtime_results_are_not_comparable():
    """两种结算口径的指纹不同 → 契约判定"不可比"，而不是硬凑差额。"""
    records = make_bars()
    daily = evaluate_bar(CONFIG, records, _crossing_date(records), None, "daily", "qfq")
    realtime = evaluate_bar(CONFIG, records, _crossing_date(records), None, "realtime", "qfq")

    def fp(payload):
        return ec.ExecutionFingerprint(**payload["fingerprint"])

    allowed, reason = ec.compare_allowed(fp(daily), fp(realtime))
    assert not allowed
    assert "fill_basis" in reason


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print("PASS", fn.__name__)
