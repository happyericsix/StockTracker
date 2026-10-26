# -*- coding: utf-8 -*-
"""自进化回放的口径用例：闸门三拦（换标的/回测更差/schema 坏）、冻结段、编年史。

FakeLLM 同时扮演委员会 8 角色与反思/提案调用（按 system 内容区分）。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finetune import evolve_replay as er  # noqa: E402


class RouterLLM:
    """按 system 提示词路由：委员会给裁决，反思给教训，提案给新 config。"""

    def __init__(self, proposal: dict | None = None):
        self.proposal = proposal
        self.proposal_calls = 0
        self.committee_calls = 0

    def __call__(self, messages, temperature=0.2, max_tokens=800):
        system = messages[0]["content"]
        if "策略进化师" in system:
            self.proposal_calls += 1
            content = json.dumps(self.proposal, ensure_ascii=False)
        elif "周复盘官" in system:
            content = '{"lessons": ["追高买入连续错，等回踩确认"]}'
        else:
            self.committee_calls += 1
            content = ('FINAL TRANSACTION PROPOSAL: **HOLD**\n'
                       '```json\n{"signal":"hold","size_fraction":0.0,'
                       '"confidence":0.6,"rationale":"观望"}\n```')
        return {"message": {"role": "assistant", "content": content},
                "usage": {"prompt_tokens": 300, "completion_tokens": 0,
                          "total_tokens": 300}}


def make_records(n=140):
    """140 根缓慢上行的日线（足够闸门的 60 根门槛 + 回测需求）。"""
    records = []
    price = 100.0
    from datetime import date, timedelta
    d = date(2025, 1, 2)
    for _ in range(n):
        records.append({"date": d.isoformat(), "open": price, "close": price,
                        "high": price, "low": price, "volume": 1000})
        price += 0.2
        d += timedelta(days=1)
    return records


def run(tmp_path, proposal=None, freeze="2025-04-01"):
    # 回放窗口放在 3 月：1-2 月的数据已攒够闸门的 60 根门槛，
    # 否则前几周全部"提案暂缓"，测不到提案路径
    er.get_history = lambda *a, **k: make_records()
    llm = RouterLLM(proposal=proposal)
    report = er.evolve_replay(
        "601988", "2025-03-03", "2025-03-31", freeze,
        json.loads(json.dumps(er.DEFAULT_CONFIG)),
        str(tmp_path), completion=llm, budget_tokens=0)
    return report, llm, tmp_path


def test_baseline_runs_and_writes_chronicle(tmp_path):
    report, llm, out = run(tmp_path)
    assert report["days"] >= 20 and report["error"] is None
    assert (out / "chronicle.md").exists() and (out / "report.json").exists()
    assert (out / "config_history.json").exists()
    # 全 HOLD：轨 A 收益应为 0（不动的账本不许漂移）
    assert report["track_a_return_pct"] == 0.0
    assert llm.proposal_calls > 0


def test_proposal_with_worse_backtest_is_rejected(tmp_path):
    # 提案把止损改成 -1（频繁止损，缓慢上行行情里回测必更差）→ 闸门应否决
    bad = json.loads(json.dumps(er.DEFAULT_CONFIG))
    bad["exit"]["conditions"][1]["value"] = -1
    report, llm, _ = run(tmp_path, proposal=bad)
    assert report["proposals"]["rejected"] >= 1
    assert report["proposals"]["applied"] == 0
    assert report["config_versions"] == 1   # 还是初始版


def test_proposal_trying_to_change_symbol_is_rejected(tmp_path):
    cheat = json.loads(json.dumps(er.DEFAULT_CONFIG))
    cheat["symbol"] = "600519"
    report, _, _ = run(tmp_path, proposal=cheat)
    assert report["proposals"]["rejected"] >= 1
    assert report["proposals"]["applied"] == 0


def test_frozen_phase_makes_no_proposals(tmp_path):
    # 冻结线设为第一天：全程不许提案
    report, llm, _ = run(tmp_path, freeze="2025-03-03")
    assert llm.proposal_calls == 0
    assert report["proposals"]["made"] == 0


def test_gate_symbol_and_schema():
    ok, reason, _, _ = er._gate(make_records(80), er.DEFAULT_CONFIG,
                                {**er.DEFAULT_CONFIG, "symbol": "600519"}, "601988")
    assert not ok and "换标的" in reason
    # 标的对但结构坏：应落在 schema 拦截（带正确 symbol 才能走到 schema 那一步）
    ok2, reason2, _, _ = er._gate(make_records(80), er.DEFAULT_CONFIG,
                                  {**er.DEFAULT_CONFIG, "entry": "不是规则组"}, "601988")
    assert not ok2 and "schema" in reason2


def test_gate_complexity_penalty(monkeypatch):
    """闸门与生产门禁共用复杂度罚金（agent.revision 同一把尺）：
    加条件 2pt 领先抵不过 5pt 罚金；删条件换 3pt 劣化在奖励范围内放行。"""
    old = json.loads(json.dumps(er.DEFAULT_CONFIG))
    add1 = json.loads(json.dumps(er.DEFAULT_CONFIG))
    add1["exit"]["conditions"].append({"type": "rsi_above", "value": 80})

    def fake(cfg, records):
        # DEFAULT_CONFIG 复杂度 3，加一条变 4：旧 10% vs 新 12%，罚金 5pt 后 7% < 10%
        return {"total_return_pct": 12.0 if len(cfg["exit"]["conditions"]) == 3 else 10.0}
    monkeypatch.setattr(er, "run_backtest_realistic", fake)
    ok, reason, _, _ = er._gate(make_records(80), old, add1, "601988")
    assert not ok and "复杂度" in reason and "否决" in reason

    lean = json.loads(json.dumps(er.DEFAULT_CONFIG))
    lean["exit"]["conditions"] = lean["exit"]["conditions"][:1]

    def fake2(cfg, records):
        # 删一条（复杂度 4→2 的等价场景用单删一验证）：旧 10% vs 新 7%，奖励 5pt 后 12% > 10%
        return {"total_return_pct": 7.0 if len(cfg["exit"]["conditions"]) == 1 else 10.0}
    monkeypatch.setattr(er, "run_backtest_realistic", fake2)
    ok2, reason2, _, _ = er._gate(make_records(80), old, lean, "601988")
    assert ok2 and "极简奖励" in reason2


def test_valuation_block_asof_and_floor():
    """估值块纯函数：as-of 切片、分位口径、250 行下限。"""
    from datetime import date as _d, timedelta as _td
    start = _d(2024, 3, 1)
    series = [{"date": (start + _td(days=i)).isoformat(),
               "pe_ttm": round(50 - 40 * i / 299, 4)} for i in range(300)]
    block = er._valuation_block(series, series[-1]["date"])
    assert block and block["pe_percentile_5y"] < 1.0 and block["date"] == series[-1]["date"]
    # 早于全部估值的 as_of：没有语境（不拿缺席当便宜）
    assert er._valuation_block(series, "2020-01-01") is None
    # 不足 250 行：分位无意义
    assert er._valuation_block(series[:100], series[99]["date"]) is None


def test_evidence_extras_injectable_and_fail_open(monkeypatch):
    """沙盘证据对齐块：注入桩可驱动；取数失败时两块都缺席不挡回放。"""
    from datetime import date as _d, timedelta as _td

    start = _d(2024, 1, 1)
    series = [{"date": (start + _td(days=i)).isoformat(),
               "pe_ttm": round(50 - 40 * i / 259, 4)} for i in range(260)]
    er._SANDBOX_VALUATION_CACHE.clear()
    er._SANDBOX_FUNDAMENTALS_CACHE.clear()
    monkeypatch.setattr(er, "_fetch_valuation_quietly", lambda s: series)
    monkeypatch.setattr(er, "_fetch_fundamentals_quietly",
                        lambda s, a: {"periods": [{"period": "20250331"}]})
    extras = er._evidence_extras("600519", "2025-06-15")
    assert "valuation_context" in extras and "fundamentals" in extras

    er._SANDBOX_VALUATION_CACHE.clear()
    er._SANDBOX_FUNDAMENTALS_CACHE.clear()
    monkeypatch.setattr(er, "_fetch_valuation_quietly", lambda s: [])
    monkeypatch.setattr(er, "_fetch_fundamentals_quietly", lambda s, a: None)
    assert er._evidence_extras("600519", "2025-06-15") == {}
