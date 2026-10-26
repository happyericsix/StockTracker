# -*- coding: utf-8 -*-
"""修订门禁的复杂度罚金：加条件要付账、删条件奖励封顶、纯调参不收费。

回测引擎被替换成"可编程净值曲线"——门禁数学需要精确到 pt 的收益控制，
真引擎在合成行情上的数字不可复现（那是 test_evolve_replay 的活）。
净值曲线口径：决策段（截至留出边界）恒定日收益复利、留出段走平——
恒定日收益下近期加权年化就等于设定的年化（权重归一后均值不变）。
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import revision  # noqa: E402

BASE = {
    "schema_version": "1.0",
    "name": "gate-base",
    "symbol": "600519",
    "initial_capital": 100000,
    "data": {"period": "day", "lookback_days": 250},
    "position": {"type": "percent", "size_pct": 50},
    "entry": {"logic": "all", "conditions": [
        {"type": "price_cross_ma", "window": 60, "direction": "above"}]},
    "exit": {"logic": "any", "conditions": [
        {"type": "price_cross_ma", "window": 60, "direction": "below"},
        {"type": "stop_loss_pct", "value": -8}]},
    "risk": {"commission_pct": 0.1, "slippage_pct": 0.1},
}


def clone(cfg, name):
    out = json.loads(json.dumps(cfg))
    out["name"] = name
    return out


def with_extra_condition(cfg):
    out = json.loads(json.dumps(cfg))
    out["exit"]["conditions"].append({"type": "rsi_above", "value": 80})
    return out


def with_rich_exit(cfg):
    """exit 三条条件：复杂度 4，给"删条件拿奖励"的用例当旧版。"""
    out = json.loads(json.dumps(cfg))
    out["exit"]["conditions"].append({"type": "take_profit_pct", "value": 15})
    return out


def make_records(n=160):
    records = []
    d = date(2025, 1, 2)
    for _ in range(n):
        records.append({"date": d.isoformat(), "open": 100.0, "close": 100.0,
                        "high": 100.0, "low": 100.0, "volume": 1000})
        d += timedelta(days=1)
    return records


def canned_backtest(records, decision_annual_pct):
    """可编程回测：决策段恒定日收益复利（年化 = decision_annual_pct%），留出段走平。"""
    boundary_idx = len(records) - revision.HOLDOUT_BARS
    dr = decision_annual_pct / 100.0 / 252.0
    curve, eq = [], 100000.0
    for i, r in enumerate(records):
        if i <= boundary_idx:
            eq *= 1.0 + dr
        curve.append({"date": r["date"], "equity": eq})
    total = (curve[-1]["equity"] / curve[0]["equity"] - 1) * 100
    return {"total_return_pct": round(total, 2), "excess_return_pct": round(total, 2),
            "max_drawdown_pct": 0.0, "closed_trades": 10, "exposure_pct": 60.0,
            "equity_curve": curve, "trade_log": []}


def patch_engine(monkeypatch, perf_by_name):
    """按 config.name 路由到设定年化的假回测（revision_gate 在函数内 import，
    所以补丁打在 agent.strategy_engine 模块属性上）。"""
    def fake(cfg, records):
        return canned_backtest(records, perf_by_name[cfg["name"]])
    monkeypatch.setattr("agent.strategy_engine.run_backtest_realistic", fake)


# ==================== 罚金数学（不碰回测引擎） ====================


def test_penalty_math():
    base, add1 = BASE, with_extra_condition(BASE)
    cx = revision.complexity_penalty_pt(base, add1)
    assert cx["delta"] == 1 and cx["adjustment_pt"] == 5.0 and "罚金" in cx["note"]

    # 纯调参（窗口改值）：条件条数不变，不收费
    tuned = clone(BASE, "gate-tuned")
    tuned["entry"]["conditions"][0]["window"] = 30
    assert revision.complexity_penalty_pt(BASE, tuned)["adjustment_pt"] == 0.0

    # 删条件：奖励按条数累计但封顶（delta=-2 → 原始 -10pt → 封顶 -5pt）
    lean = clone(BASE, "gate-lean")
    lean["exit"]["conditions"] = lean["exit"]["conditions"][:1]
    cx2 = revision.complexity_penalty_pt(with_rich_exit(BASE), lean)
    assert cx2["delta"] == -2 and cx2["adjustment_pt"] == -5.0


# ==================== 门禁行为（假引擎 + 可编程净值曲线） ====================


def test_added_condition_pays_penalty(monkeypatch):
    """领先 2pt 但净增 1 条条件（罚金 5pt）→ 否决；没有罚金这个提案本来会过。"""
    add1 = with_extra_condition(clone(BASE, "gate-add1"))
    patch_engine(monkeypatch, {"gate-base": 0.0, "gate-add1": 2.0})
    gate = revision.revision_gate(make_records(), BASE, add1)
    assert not gate["passed"]
    assert "复杂度" in gate["reason"] and "罚金" in gate["reason"]
    assert gate["complexity"]["delta"] == 1
    # 通俗版结论（给前端主视图）：每个分支都要有，且不带技术口径词
    assert gate["summary"] and "不建议采用" in gate["summary"]
    assert "决策段" not in gate["summary"] and "半衰期" not in gate["summary"]


def test_added_condition_passes_when_beating_penalty(monkeypatch):
    """领先 8pt > 罚金 5pt → 放行（罚金拦的是小改善，不拦真改善）。"""
    add1 = with_extra_condition(clone(BASE, "gate-add1"))
    patch_engine(monkeypatch, {"gate-base": 0.0, "gate-add1": 8.0})
    gate = revision.revision_gate(make_records(), BASE, add1)
    assert gate["passed"] and "门禁通过" in gate["reason"] and "罚金" in gate["reason"]
    assert gate["summary"] and "可以批准" in gate["summary"]


def test_pure_reparam_not_taxed(monkeypatch):
    """条件条数不变的调参提案：门槛不含罚金，2pt 领先正常放行。"""
    tuned = clone(BASE, "gate-tuned")
    tuned["entry"]["conditions"][0]["window"] = 30
    patch_engine(monkeypatch, {"gate-base": 0.0, "gate-tuned": 2.0})
    gate = revision.revision_gate(make_records(), BASE, tuned)
    assert gate["passed"] and gate["complexity"]["delta"] == 0


def test_simpler_config_gets_capped_bonus(monkeypatch):
    """删条件换 3pt 劣化：极简奖励 5pt 内 → 放行（1-SE 规则：更简允许稍差）。"""
    rich = with_rich_exit(clone(BASE, "gate-rich"))
    patch_engine(monkeypatch, {"gate-rich": 0.0, "gate-base": -3.0})
    gate = revision.revision_gate(make_records(), rich, clone(BASE, "gate-base"))
    assert gate["passed"] and "极简奖励" in gate["reason"]


def test_simplicity_bonus_is_capped(monkeypatch):
    """劣化 8pt 超过奖励上限 5pt → 否决：不许拿删条件换大幅劣化。"""
    rich = with_rich_exit(clone(BASE, "gate-rich"))
    patch_engine(monkeypatch, {"gate-rich": 0.0, "gate-base": -8.0})
    gate = revision.revision_gate(make_records(), rich, clone(BASE, "gate-base"))
    assert not gate["passed"]


def test_early_returns_carry_complexity(monkeypatch):
    """提前返回（换标的/schema 坏）也带 complexity 字段：消费方不用判空。"""
    cheat = clone(BASE, "gate-cheat")
    cheat["symbol"] = "601988"
    gate = revision.revision_gate(make_records(), BASE, cheat)
    assert not gate["passed"] and gate["complexity"]["delta"] == 0


# ==================== 开仓护栏（2026-10-16，PROPOSAL 第一步） ====================


def test_open_position_guard_caps_undeclared_buy():
    """策略没声明仓位：agent 开仓 >50% 被压到 50%，护栏写进 rationale（留痕不静默）。"""
    from agent.trading_decision import TradeDecision, apply_open_position_guard
    d = TradeDecision(decision="buy", signal="buy", size_fraction=1.0,
                      confidence=0.8, rationale="看多")
    guarded, note = apply_open_position_guard(d, {"position": {}})
    assert guarded.size_fraction == 0.5 and "护栏" in guarded.rationale
    assert d.size_fraction == 1.0          # 原决策不可变（frozen）


def test_open_position_guard_respects_declared_and_sells():
    from agent.trading_decision import TradeDecision, apply_open_position_guard
    full = TradeDecision(decision="buy", signal="buy", size_fraction=1.0,
                         confidence=0.8, rationale="x")
    # 用户显式写了 size_pct=100：表过态，尊重
    assert apply_open_position_guard(full, {"position": {"size_pct": 100}}) is None
    # 卖出不受开仓护栏约束（清仓永远允许）
    sell = TradeDecision(decision="sell", signal="sell", size_fraction=1.0,
                         confidence=0.8, rationale="x")
    assert apply_open_position_guard(sell, {"position": {}}) is None


def test_finalize_gap_questions_before_confirm():
    """② 参数缺口问答：仓位/止损缺失时 finalize 拒绝并产出问题清单；确认后放行。"""
    from agent.tool_registry import REGISTRY
    base = {"schema_version": "1.0", "name": "x", "symbol": "600519",
            "initial_capital": 100000,
            "entry": {"logic": "all", "conditions": [{"type": "price_cross_ma", "window": 60, "direction": "above"}]},
            "exit": {"logic": "any", "conditions": [{"type": "price_cross_ma", "window": 60, "direction": "below"}]},
            "risk": {"commission_pct": 0.1, "slippage_pct": 0.1}}
    out = REGISTRY.get("finalize_strategy").handler({"strategy_json": base})
    assert out["ok"] and not out["data"]["valid"]
    assert len(out["data"]["needs_clarification"]) == 2   # 仓位 + 止损双缺口
    confirmed = REGISTRY.get("finalize_strategy").handler({"strategy_json": base, "gaps_confirmed": True})
    assert confirmed["data"]["valid"] is not False or "needs_clarification" not in confirmed["data"]
