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
