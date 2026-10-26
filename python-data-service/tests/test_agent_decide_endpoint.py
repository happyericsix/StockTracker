# -*- coding: utf-8 -*-
"""`/api/v1/agent/decide` 端点的回归用例。

钉住 2026-10-21 部署事故（连续 3 天、每轮委员会决策必崩）的两层病灶：
① 端点对 `decide()` 返回值的错误假设——它是扁平 dict，不是 CommitteeResult，
   任何属性访问（`trading_committee.trading_decision` / `result.decision`）都 AttributeError；
② except 把一切异常包装成 skip_reason=data_unavailable，代码错误伪装成数据问题，
   数据库里看不到真因（真因只能翻容器日志）。

护栏（apply_open_position_guard）的端到端生效也在这一层钉住：它必须发生在
`trading_committee.decide` 内部（那里才有真 TradeDecision 对象），端点只消费 dict。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import akshare_client  # noqa: E402
import app as main  # noqa: E402
from agent import trading_committee  # noqa: E402
from agent.trading_decision import TradeDecision  # noqa: E402

TOKEN = getattr(main, "INTERNAL_API_TOKEN", "") or ""
HEADERS = {"X-Internal-Token": TOKEN} if TOKEN else {}

STRATEGY_JSON = {
    "schema_version": "1.0",
    "name": "ma",
    "symbol": "600519",
    "entry": {"logic": "all", "conditions": [
        {"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}
    ]},
    "exit": {"logic": "any", "conditions": [
        {"type": "stop_loss_pct", "value": -8}
    ]},
}


def make_bars(n=30, start=100.0, step=1.0):
    bars = []
    for i in range(n):
        close = start + step * i
        bars.append({
            "date": f"2026-01-{i+1:02d}",
            "open": close, "high": close + 2, "low": close - 2,
            "close": close, "volume": 1000,
        })
    return bars


LAST_DATE = "2026-01-30"


def _post_decide(client):
    return client.post("/api/v1/agent/decide", headers=HEADERS, json={
        "strategy_json": STRATEGY_JSON,
        "symbol": "600519",
        "date": LAST_DATE,
        "settlement_kind": "daily",
    })


def test_decide_endpoint_returns_flat_dict_shape(monkeypatch):
    """端点必须返回 200 + valid=True 的扁平决策 dict（与 evaluate-bar 同形状）。

    事故前症状：端点内部 AttributeError → except 兜底 → valid=False +
    skip_reason=data_unavailable，委员会连续 3 天"看起来像数据源坏了"。
    """
    monkeypatch.setenv("AGENT_FUNDAMENTALS", "0")
    monkeypatch.setenv("AGENT_MARKET_CONTEXT", "0")
    monkeypatch.setattr(akshare_client, "get_history",
                        lambda symbol, *a, **k: make_bars())
    monkeypatch.setattr(
        trading_committee, "decide",
        lambda *a, **k: {
            "decision": "skip", "signal": "hold",
            "skip_reason": "agent_no_new_information",
            "size_fraction": 0.0, "matched_conditions": [],
            "date": LAST_DATE, "fill_basis": "close",
            "fingerprint": {}, "snapshot": None, "bar_date_missing": False,
            "committee": {"as_of": LAST_DATE},
        })
    r = _post_decide(TestClient(main.app))
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True
    assert body["decision"] == "skip"
    assert "error" not in body and "error_detail" not in body


def test_decide_endpoint_error_envelope_carries_detail(monkeypatch):
    """决策内部抛异常：skip_reason 保持 data_unavailable（补跑轮只认它），
    但真因必须随 error_detail 出端点——代码错误不再伪装成纯数据问题。"""
    monkeypatch.setenv("AGENT_FUNDAMENTALS", "0")
    monkeypatch.setenv("AGENT_MARKET_CONTEXT", "0")
    monkeypatch.setattr(akshare_client, "get_history",
                        lambda symbol, *a, **k: make_bars())

    def boom(*a, **k):
        raise ValueError("委员会内部错误")

    monkeypatch.setattr(trading_committee, "decide", boom)
    r = _post_decide(TestClient(main.app))
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert body["skip_reason"] == "data_unavailable"
    assert body["error_detail"]["type"] == "ValueError"
    assert "委员会内部错误" in body["error_detail"]["message"]


def test_decide_applies_open_position_guard_on_real_decision(monkeypatch):
    """护栏必须真生效在 decide 的产出上：策略未声明仓位 + 委员会 full buy
    → 响应 size_fraction 被压到 50%，且 rationale 留痕（不静默改）。

    只测 decide 这一层就够：端点对 dict 透传，端点层的形状由上面两个用例钉住。
    """
    import agent.trading_committee as tc

    bars = make_bars()
    monkeypatch.setattr(
        tc, "build_evidence",
        lambda symbol, as_of, records, position=None: (
            {"last_close": bars[-1]["close"], "indicators": {},
             "recent_bars": bars[-3:]}, []))
    monkeypatch.setattr(
        tc, "convene_reasons",
        lambda *a, **k: (["rule_signal"], {"rule_signal": "buy"}))
    monkeypatch.setattr(
        tc, "run_committee",
        lambda *a, **k: tc.CommitteeResult(
            decision=TradeDecision(decision="buy", signal="buy",
                                   size_fraction=1.0, confidence=0.8,
                                   rationale="看多"),
            evidence={}, transcript=[], llm_calls=1, total_tokens=100,
            as_of=LAST_DATE, model="test"))

    cfg_without_position = {
        "schema_version": "1.0", "name": "t", "symbol": "600519",
        "entry": {"logic": "all", "conditions": []},
        "exit": {"logic": "any", "conditions": []},
    }
    out = tc.decide("600519", LAST_DATE, bars, None, config=cfg_without_position)
    assert out["decision"] == "buy"
    assert out["size_fraction"] == 0.5
    assert "仓位护栏" in out["rationale"]

    # 对照：策略显式声明了仓位（size_pct=100），委员会 full buy 被尊重
    cfg_declared = dict(cfg_without_position,
                        position={"type": "percent", "size_pct": 100})
    out2 = tc.decide("600519", LAST_DATE, bars, None, config=cfg_declared)
    assert out2["decision"] == "buy"
    assert out2["size_fraction"] == 1.0
