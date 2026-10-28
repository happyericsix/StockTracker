# -*- coding: utf-8 -*-
"""每日观察员 + 门控扩充（P0/P1/P3/P4/P5，2026-10-28）的测试。

覆盖四件事：
- observer 的解析纪律（读不懂 → 不召集，绝不猜）；
- observer 接入 decide 后的三种日型：安静（1 次调用+skip）、提议开会（observer_flag）、
  注入触发（auth_review，观察员不跑——已经有理由开会，省一次扫视）；
- convene_reasons 的信息面触发（news_signal / sector_move）与按策略阈值（gate 段）；
- shadow 模式：跳过门控天天开会、响应带标记、真实触发状态另存。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import execution_contract as ec                     # noqa: E402
from agent import trading_committee as tc                      # noqa: E402
from agent.daily_observer import parse_observer_verdict        # noqa: E402


# ==================== 测试替身（与 test_trading_committee 的 FakeLLM 同形） ====================

class FakeLLM:
    """假模型：按角色返回可辨认的文本，并记录每次调用的输入。"""

    def __init__(self, judge_output='FINAL TRANSACTION PROPOSAL: **HOLD**',
                 per_call_tokens=100):
        self.answer = judge_output
        self.per_call_tokens = per_call_tokens
        self.calls = []

    def __call__(self, messages, temperature=0.2, max_tokens=800):
        role_label = messages[0]["content"][:12]
        self.calls.append({"system": messages[0]["content"], "user": messages[1]["content"]})
        text = self.answer if "风控裁决" in messages[0]["content"] else f"（{role_label}…）我的看法"
        return {"message": {"role": "assistant", "content": text},
                "usage": {"prompt_tokens": self.per_call_tokens,
                          "completion_tokens": 0,
                          "total_tokens": self.per_call_tokens}}


class ObserverSaysConvene(FakeLLM):
    """观察员那一次调用返回"提议开会"；委员会角色走 FakeLLM 默认。"""

    def __call__(self, messages, temperature=0.2, max_tokens=800):
        if "策略观察员" in messages[0]["content"]:
            self.calls.append({"system": messages[0]["content"], "user": messages[1]["content"]})
            return {"message": {"role": "assistant", "content":
                    '{"attention": "high", "propose_convene": true, '
                    '"thesis_status": "论点仍成立", "note": "板块与个股背离"}'},
                    "usage": {"prompt_tokens": 60, "completion_tokens": 20,
                              "total_tokens": 80}}
        return super().__call__(messages, temperature=temperature, max_tokens=max_tokens)


def quiet_bars(n=60, price=100.0):
    """一路横盘的 bar：保证价量门控全不触发。"""
    return [{"date": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
             "open": price, "high": price + 0.05, "low": price - 0.05,
             "close": price, "volume": 1000} for i in range(n)]


# ==================== 观察员解析纪律 ====================

def test_parse_verdict_valid_json():
    verdict = parse_observer_verdict(
        '{"attention": "med", "propose_convene": true, "thesis_status": "成立", "note": "量能持续放大"}')
    assert verdict["attention"] == "med"
    assert verdict["propose_convene"] is True
    assert verdict["warnings"] == []


def test_parse_verdict_garbage_is_low_and_quiet():
    """读不懂 → low / 不召集，绝不猜（FakeLLM 的散文输出就落在这条分支）。"""
    verdict = parse_observer_verdict("（多头研究员…）我的看法")
    assert verdict["attention"] == "low"
    assert verdict["propose_convene"] is False
    assert verdict["warnings"]


def test_parse_verdict_unknown_attention_downgrades():
    verdict = parse_observer_verdict('{"attention": "urgent", "propose_convene": false}')
    assert verdict["attention"] == "low"
    assert verdict["propose_convene"] is False


def test_parse_verdict_propose_requires_literal_true():
    verdict = parse_observer_verdict('{"attention": "high", "propose_convene": "yes"}')
    assert verdict["propose_convene"] is False


# ==================== observer 接入 decide 的三种日型 ====================

def test_quiet_day_observer_looks_and_skips(monkeypatch):
    """安静日：观察员跑一次（解析失败→不召集），委员会零调用，结论进 gate。"""
    monkeypatch.delenv("AGENT_OBSERVER", raising=False)
    llm = FakeLLM()
    bars = quiet_bars()
    result = tc.decide("600519", bars[-1]["date"], bars, completion=llm,
                       last_decision_at=bars[-2]["date"])
    assert len(llm.calls) == 1
    assert result["skip_reason"] == ec.SKIP_AGENT_NO_NEW_INFORMATION
    observer = result["committee"]["gate"]["observer"]
    assert observer["attention"] == "low"
    assert observer["propose_convene"] is False
    assert observer["degraded"] is False


def test_observer_disabled_is_true_zero_calls(monkeypatch):
    """AGENT_OBSERVER=0 → 回到 P0 之前的"零调用"行为（开关可回退）。"""
    monkeypatch.setenv("AGENT_OBSERVER", "0")
    llm = FakeLLM()
    bars = quiet_bars()
    result = tc.decide("600519", bars[-1]["date"], bars, completion=llm,
                       last_decision_at=bars[-2]["date"])
    assert llm.calls == []
    assert result["skip_reason"] == ec.SKIP_AGENT_NO_NEW_INFORMATION
    # 缺席不是错误：gate 里不出现 observer 键（区别于"看过说安静"）


def test_observer_proposal_convenes_committee(monkeypatch):
    """观察员提议开会 → observer_flag 进触发理由，全委会真开（角色调用发生）。"""
    monkeypatch.delenv("AGENT_OBSERVER", raising=False)
    llm = ObserverSaysConvene()
    bars = quiet_bars()
    result = tc.decide("600519", bars[-1]["date"], bars, completion=llm,
                       last_decision_at=bars[-2]["date"])
    gate = result["committee"]["gate"]
    assert gate["reasons"] == [tc.TRIGGER_OBSERVER]
    assert gate["convened"] is True
    assert gate["observer"]["propose_convene"] is True
    # 1 次观察员 + 8 个角色
    assert len(llm.calls) == 1 + 8


def test_injected_auth_review_skips_observer(monkeypatch):
    """调用方注入 auth_review：已有开会理由，观察员不必再扫视（省一次调用）。"""
    monkeypatch.delenv("AGENT_OBSERVER", raising=False)
    llm = FakeLLM()
    bars = quiet_bars()
    result = tc.decide("600519", bars[-1]["date"], bars, completion=llm,
                       last_decision_at=bars[-2]["date"],
                       convene_flags=[tc.TRIGGER_AUTH_REVIEW])
    gate = result["committee"]["gate"]
    assert tc.TRIGGER_AUTH_REVIEW in gate["reasons"]
    assert gate["injected"] == [tc.TRIGGER_AUTH_REVIEW]
    assert "observer" not in gate
    assert len(llm.calls) == 8   # 只有委员会角色


def test_unknown_convene_flag_is_ignored():
    """注入的触发必须在封闭集内 —— 任意字符串不许混进 reasons。"""
    bars = quiet_bars()
    result = tc.decide("600519", bars[-1]["date"], bars, completion=FakeLLM(),
                       last_decision_at=bars[-2]["date"],
                       convene_flags=["because_i_say_so"])
    gate = result["committee"]["gate"]
    assert "because_i_say_so" not in gate["reasons"]
    assert gate["injected"] == []


# ==================== 信息面触发（P1） ====================

def test_news_pulse_high_impact_triggers():
    reasons, detail = tc.convene_reasons([], "2026-10-28",
                                         last_decision_at="2026-10-27",
                                         news_pulse={"window_days": 1,
                                                     "total_events": 1,
                                                     "high_impact_events": 1})
    assert tc.TRIGGER_NEWS_SIGNAL in reasons
    assert detail["news_pulse"]["high_impact_events"] == 1


def test_news_pulse_needs_three_when_low_impact():
    """低影响单条不开会，攒够三条才算"发酵"。"""
    _, detail = tc.convene_reasons([], "2026-10-28", last_decision_at="2026-10-27",
                                   news_pulse={"total_events": 2,
                                               "high_impact_events": 0})
    assert detail["news_pulse"]["total_events"] == 2

    reasons, _ = tc.convene_reasons([], "2026-10-28", last_decision_at="2026-10-27",
                                    news_pulse={"total_events": 3,
                                                "high_impact_events": 0})
    assert tc.TRIGGER_NEWS_SIGNAL in reasons


def test_sector_move_triggers_on_threshold():
    reasons, detail = tc.convene_reasons([], "2026-10-28", last_decision_at="2026-10-27",
                                         sector_pct=-2.5)
    assert tc.TRIGGER_SECTOR_MOVE in reasons
    assert detail["sector_pct"] == -2.5


def test_sector_move_quiet_below_threshold():
    reasons, _ = tc.convene_reasons([], "2026-10-28", last_decision_at="2026-10-27",
                                    sector_pct=1.9)
    assert tc.TRIGGER_SECTOR_MOVE not in reasons


def test_bad_news_pulse_and_sector_values_fail_open_quietly():
    """坏数值不算触发也不炸：门控是风控入口，不许被脏数据打断。"""
    reasons, detail = tc.convene_reasons([], "2026-10-28", last_decision_at="2026-10-27",
                                         news_pulse={"total_events": "many",
                                                     "high_impact_events": None},
                                         sector_pct="not-a-number")
    assert tc.TRIGGER_NEWS_SIGNAL not in reasons
    assert tc.TRIGGER_SECTOR_MOVE not in reasons
    assert "sector_pct" not in detail


# ==================== 按策略阈值（P4） ====================

def test_gate_tuning_overrides_and_defends():
    tuning = tc._gate_tuning({"gate": {"spike_multiple": 1.5, "max_days_flat": 5}})
    assert tuning["spike_multiple"] == 1.5
    assert tuning["max_days_flat"] == 5
    assert tuning["min_move_pct"] == tc.MIN_MOVE_PCT   # 未覆盖的键走默认

    # 风控参数不许被坏配置置零：非正数回全局默认
    bad = tc._gate_tuning({"gate": {"min_move_pct": 0, "max_days_holding": -3}})
    assert bad["min_move_pct"] == tc.MIN_MOVE_PCT
    assert bad["max_days_holding"] == tc.MAX_DAYS_HOLDING

    # 没写 gate 段就是全局默认
    assert tc._gate_tuning({}) == tc._gate_tuning(None)


def test_lowered_min_move_pct_convenes_on_ordinary_move():
    """同一根 2% 的 bar：默认阈值(3%)不开会，策略自降阈值(1.5%)就开会。"""
    bars = quiet_bars(40, price=100.0)
    bars[-1] = {**bars[-1], "open": 100.0, "close": 102.0,
                "high": 102.1, "low": 99.9}

    default_reasons, _ = tc.convene_reasons(bars, bars[-1]["date"],
                                            last_decision_at=bars[-2]["date"])
    assert tc.TRIGGER_VOLATILITY_SPIKE not in default_reasons

    tuned_reasons, _ = tc.convene_reasons(bars, bars[-1]["date"],
                                          config={"gate": {"min_move_pct": 1.5}},
                                          last_decision_at=bars[-2]["date"])
    assert tc.TRIGGER_VOLATILITY_SPIKE in tuned_reasons


# ==================== shadow 模式（P5） ====================

def test_shadow_bypasses_gate_and_marks_response(monkeypatch):
    """shadow：门控零触发也开会；响应带 shadow 标记；真实触发状态另存。"""
    monkeypatch.delenv("AGENT_OBSERVER", raising=False)
    llm = FakeLLM()
    bars = quiet_bars()
    result = tc.decide("600519", bars[-1]["date"], bars, completion=llm,
                       last_decision_at=bars[-2]["date"], shadow=True)
    assert result.get("shadow") is True
    gate = result["committee"]["gate"]
    assert gate["reasons"] == [tc.TRIGGER_SHADOW]
    assert gate["shadow_bypass"] is True
    assert gate["real_reasons"] == []
    assert len(llm.calls) == 8          # 委员会真开了（观察员被跳过：shadow 不需要提案）
    assert result["committee"]["llm_calls"] == 8


def test_shadow_preserves_real_reasons_when_market_did_trigger():
    """shadow 日如果市场本来就有触发，真实触发状态存 real_reasons 不丢。"""
    bars = quiet_bars(40, price=100.0)
    bars[-1] = {**bars[-1], "open": 100.0, "close": 105.0,
                "high": 105.1, "low": 99.9}   # 5% ≥ 绝对下限 → volatility_spike
    result = tc.decide("600519", bars[-1]["date"], bars, completion=FakeLLM(),
                       last_decision_at=bars[-2]["date"], shadow=True)
    gate = result["committee"]["gate"]
    assert gate["reasons"] == [tc.TRIGGER_SHADOW]
    assert tc.TRIGGER_VOLATILITY_SPIKE in gate["real_reasons"]


def test_trigger_constants_closed_sets():
    """触发常量的封闭集完整性：shadow 不进 CONVENE_TRIGGERS（统计真实频率时排除）。"""
    assert tc.TRIGGER_SHADOW not in tc.CONVENE_TRIGGERS
    for flag in tc.INJECTABLE_TRIGGERS:
        assert flag in tc.CONVENE_TRIGGERS
