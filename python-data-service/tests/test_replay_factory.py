# -*- coding: utf-8 -*-
"""回放工厂的口径用例：样本结构、对齐数学、断点续跑、无 bar 跳过。

FakeLLM 模拟委员会 8 个角色（每个都回同一段裁决文本）——不花钱验证管线。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finetune import replay_factory as rf  # noqa: E402


class FakeJudge:
    """run_committee 会串行调 8 次；每次都给一段带规范行的裁决文本。"""

    VERDICT = ('FINAL TRANSACTION PROPOSAL: **BUY**\n'
               '```json\n{"signal":"buy","size_fraction":0.5,"confidence":0.6,'
               '"rationale":"测试裁决"}\n```')

    def __init__(self):
        self.calls = 0

    def __call__(self, messages, temperature=0.2, max_tokens=800):
        self.calls += 1
        return {"message": {"role": "assistant", "content": self.VERDICT},
                "usage": {"prompt_tokens": 500, "completion_tokens": 0,
                          "total_tokens": 500}}


def make_records():
    """2025-01-02 起的 10 根日线：100 → 逐步涨到 110（买对齐应为正）。"""
    records = []
    price = 100.0
    for i in range(10):
        day = f"2025-01-{2 + i:02d}"
        records.append({"date": day, "open": price, "close": price,
                        "high": price, "low": price, "volume": 1000})
        price += 1.0
    return records


def test_replay_produces_aligned_samples(tmp_path):
    out = str(tmp_path / "replay.jsonl")
    records = make_records()
    rf.get_history = lambda *a, **k: records
    judge = FakeJudge()
    stats = rf.replay_symbol("601988", "2025-01-02", "2025-01-06",
                             horizon=3, max_samples=5, out_path=out,
                             completion=judge)

    lines = [json.loads(l) for l in open(out, encoding="utf-8")]
    assert stats["samples"] >= 3 and len(lines) == stats["written"]
    sample = lines[0]
    assert sample["task"] == "trade_decision"
    assert sample["conversations"][0]["from"] == "system"
    assert "FINAL TRANSACTION PROPOSAL" in sample["conversations"][2]["value"]
    assert "evidence" in sample["conversations"][1]["value"]
    # 一路上涨的行情里，BUY 决策的 3 日对齐必须为正（对账数学和线上同一套方向语义）
    assert sample["meta"]["outcome"]["aligned"] is True
    assert sample["meta"]["outcome"]["aligned_pct"] > 0


def test_hold_sample_has_no_aligned_flag(tmp_path):
    """HOLD 不判对错：只有 forward（错过多少），没有 aligned。"""
    out = str(tmp_path / "hold.jsonl")

    class HoldJudge(FakeJudge):
        VERDICT = ('FINAL TRANSACTION PROPOSAL: **HOLD**\n'
                   '```json\n{"signal":"hold","size_fraction":0.0,"confidence":0.5}\n```')

    rf.get_history = lambda *a, **k: make_records()
    rf.replay_symbol("601988", "2025-01-02", "2025-01-03", horizon=3,
                     max_samples=2, out_path=out, completion=HoldJudge())
    line = json.loads(open(out, encoding="utf-8").readline())
    assert "aligned" not in line["meta"]["outcome"]
    assert line["meta"]["outcome"]["forward_return_pct"] > 0


def test_resume_skips_written_days(tmp_path):
    out = str(tmp_path / "resume.jsonl")
    records = make_records()
    rf.get_history = lambda *a, **k: records
    judge1 = FakeJudge()
    rf.replay_symbol("601988", "2025-01-02", "2025-01-03", horizon=3,
                     max_samples=2, out_path=out, completion=judge1)
    first_calls = judge1.calls

    judge2 = FakeJudge()
    rf.replay_symbol("601988", "2025-01-02", "2025-01-03", horizon=3,
                     max_samples=2, out_path=out, completion=judge2)
    assert judge2.calls == 0, "已产出的决策日不该再花一次委员会的钱"
    assert judge1.calls == first_calls
    # 文件里没有重复 sample_id
    ids = [json.loads(l)["meta"]["sample_id"] for l in open(out, encoding="utf-8")]
    assert len(ids) == len(set(ids))


def test_forward_return_window_math():
    records = make_records()
    # 01-02 收 100 → 第 3 个交易日（01-05）收 103 → +3%
    fwd = rf._forward_return(records, "2025-01-02", 3)
    assert abs(fwd - 0.03) < 1e-9
    # 尾部 bar 不够：None（不许用"更远的一天"凑数）
    assert rf._forward_return(records, "2025-01-09", 3) is None


def test_position_ledger_respects_lots():
    pos = {"cash": 100_000.0, "shares": 0, "avg_cost": None}
    rf._apply_decision(pos, {"signal": "buy", "size_fraction": 0.5}, 7.0)
    # 预算 5 万 ÷ (7.007×100) = 71.3 手 → 71 手 = 7100 股
    assert pos["shares"] == 7100 and pos["shares"] % rf.LOT == 0
    assert pos["cash"] < 100_000
    rf._apply_decision(pos, {"signal": "sell", "size_fraction": 1.0}, 8.0)
    assert pos["shares"] == 0 and pos["cash"] > 100_000


def test_fetch_end_has_forward_buffer(tmp_path):
    """取数终点必须比回放结束日晚（对账要回放段之外的 bar）——
    冒烟实测踩过的坑：不带缓冲，回放尾部的 forward 全是 None。"""
    captured = {}

    def fake_get_history(symbol, start, end, period):
        captured["fetch_end"] = end
        return make_records()

    rf.get_history = fake_get_history
    rf.replay_symbol("601988", "2025-01-02", "2025-01-06", horizon=3,
                     max_samples=2, out_path=str(tmp_path / "x.jsonl"),
                     completion=FakeJudge())
    assert captured["fetch_end"] > "2025-01-06", "取数终点应带缓冲（> 回放结束日）"


def test_ledger_v2_supports_adding_with_weighted_cost():
    pos = {"cash": 100_000.0, "shares": 0, "avg_cost": None}
    assert rf._apply_decision(pos, {"signal": "buy", "size_fraction": 0.5}, 10.0) == "open"
    first_shares = pos["shares"]
    # 加仓：0.5 × 剩余现金，成本加权（旧版这里静默丢弃——23 次加仓意图的教训）
    action = rf._apply_decision(pos, {"signal": "buy", "size_fraction": 0.5}, 11.0)
    assert action == "add" and pos["shares"] > first_shares
    assert pos["shares"] % rf.LOT == 0
    assert 10.0 < pos["avg_cost"] < 11.0, "加权成本应介于两次成交价之间"


def test_ledger_v2_partial_sell_floors_to_lot():
    pos = {"cash": 0.0, "shares": 1000, "avg_cost": 10.0}
    assert rf._apply_decision(pos, {"signal": "sell", "size_fraction": 0.3}, 12.0) == "partial_sell"
    assert pos["shares"] == 700 and 0 < pos["cash"]
    # 减 30% 只卖 75 股 → 不足一手 → 按全仓（A股卖不了零股，与生产同语义）
    pos2 = {"cash": 0.0, "shares": 250, "avg_cost": 10.0}
    assert rf._apply_decision(pos2, {"signal": "sell", "size_fraction": 0.3}, 12.0) == "sell"
    assert pos2["shares"] == 0


def test_ledger_v2_sell_while_flat_is_traced_mismatch():
    pos = {"cash": 100_000.0, "shares": 0, "avg_cost": None}
    assert rf._apply_decision(pos, {"signal": "sell", "size_fraction": 1.0}, 10.0) == "skip_mismatch"


def test_hard_stop_triggers_on_daily_low_touch():
    pos = {"cash": 0.0, "shares": 1000, "avg_cost": 10.0}
    # 止损线 10×0.92=9.2；当日 low 9.0 击穿 → 全卖
    assert rf._apply_hard_stop(pos, day_low=9.0, stop_pct=-8) == "hard_stop"
    assert pos["shares"] == 0 and pos["cash"] > 0
    # low 高于止损线 → 不动
    pos2 = {"cash": 0.0, "shares": 1000, "avg_cost": 10.0}
    assert rf._apply_hard_stop(pos2, day_low=9.5, stop_pct=-8) == "none"
    assert pos2["shares"] == 1000


def test_stop_pct_reads_from_config():
    cfg = {"exit": {"conditions": [{"type": "stop_loss_pct", "value": -5}]}}
    assert rf._stop_pct_of(cfg) == -5.0
    assert rf._stop_pct_of({}) == -8.0


def test_ledger_v3_decision_skip_overrides_signal():
    """口径铁律：裁决 SKIP 时，模型原话 signal 说什么都不下单。
    第一轮 A/B 实测踩过：规范行 SELL + 裁决 SKIP，旧账本照 signal 清仓，卖在低点。"""
    pos = {"cash": 0.0, "shares": 1000, "avg_cost": 10.0}
    action = rf._apply_decision(pos, {"decision": "skip", "signal": "sell",
                                      "size_fraction": 1.0}, 12.0)
    assert action == "none" and pos["shares"] == 1000, "SKIP 裁决必须压过 SELL 原话"
    action2 = rf._apply_decision(pos, {"decision": "skip", "signal": "buy",
                                       "size_fraction": 0.5}, 12.0)
    assert action2 == "none" and pos["shares"] == 1000


def test_ledger_v3_add_filter_blocks_when_not_allowed():
    pos = {"cash": 50_000.0, "shares": 1000, "avg_cost": 10.0}
    action = rf._apply_decision(pos, {"decision": "buy", "signal": "buy",
                                      "size_fraction": 0.25}, 11.0, allow_add=False)
    assert action == "add_blocked" and pos["shares"] == 1000 and pos["cash"] == 50_000.0
    # 开仓不受过滤限制（空仓进场是策略本职）
    flat = {"cash": 50_000.0, "shares": 0, "avg_cost": None}
    assert rf._apply_decision(flat, {"decision": "buy", "signal": "buy",
                                     "size_fraction": 0.25}, 11.0,
                              allow_add=False) == "open"
