# -*- coding: utf-8 -*-
"""多角色委员会：**as-of 守卫、硬预算、决策由代码收口、全过程留痕**。

<h3>这个文件在防什么</h3>
把决策交给一组 LLM 角色之后，能毁掉整套模拟盘记录的方式全都不会报错：

- **偷看未来**：证据里混进一根决策日之后的 bar，结论漂亮且完全虚假 ——
  而"看起来很有道理"正是它最危险的地方；
- **没有上限**：8 个角色 × 若干轮的调用量失控制，成本变成不可预测；
- **把散文当决策**：模型写"倾向于逐步建仓"，代码却从中读出一个满仓买入；
- **过程不留痕**：事后没人能回答"这条决策当时看到的是什么、哪几个角色反对过"。

所以四件事逐条钉住。LLM 调用全部走注入的 `completion` 替身 —— 这一层不需要真模型。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import execution_contract as ec  # noqa: E402
from agent import trading_committee as tc  # noqa: E402
from agent.trading_decision import prompt_version  # noqa: E402


def make_bars(n=120, start=100.0, step=0.5):
    bars = []
    for i in range(n):
        close = start + step * i
        bars.append({"date": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                     "open": close, "high": close + 1, "low": close - 1,
                     "close": close, "volume": 1000 + i})
    return bars


class FakeLLM:
    """假模型：按角色返回可辨认的文本，并记录每次调用的输入。"""

    def __init__(self, judge_output='FINAL TRANSACTION PROPOSAL: **HOLD**',
                 per_call_tokens=100, fail_on=None, model="fake-model"):
        self.answer = judge_output
        self.per_call_tokens = per_call_tokens
        self.fail_on = fail_on
        self.model = model
        self.calls = []

    def __call__(self, messages, temperature=0.2, max_tokens=800):
        role_label = messages[0]["content"][:12]
        self.calls.append({"system": messages[0]["content"], "user": messages[1]["content"]})
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise RuntimeError("boom")
        text = self.answer if "风控裁决" in messages[0]["content"] else f"（{role_label}…）我的看法"
        return {"message": {"role": "assistant", "content": text},
                "usage": {"prompt_tokens": self.per_call_tokens,
                          "completion_tokens": 0,
                          "total_tokens": self.per_call_tokens}}


AS_OF = "2026-03-10"


# ==================== 1. as-of 守卫：证据只到决策日 ====================

def test_evidence_never_contains_a_bar_after_the_decision_day():
    bars = make_bars(60)
    as_of = bars[40]["date"]
    evidence, warnings = tc.build_evidence("600519", as_of, bars[:41])

    assert evidence["recent_bars"][-1]["date"] == as_of
    assert all(bar["date"] <= as_of for bar in evidence["recent_bars"])
    assert warnings == [], "调用方给的本来就是截止日的数据，不该有警告"


def test_future_bars_are_dropped_and_the_fact_is_reported():
    """剔除**必须**发生（否则指标里含未来信息）；报出**也必须**发生
    （否则"调用方喂了未来数据"这件事永远没人知道）。"""
    bars = make_bars(60)
    as_of = bars[40]["date"]
    expected = min(tc.EVIDENCE_BARS, 41)     # 不写死根数：它是可调的成本参数

    evidence, warnings = tc.build_evidence("600519", as_of, bars)   # 后面还有 19 根

    assert len(evidence["recent_bars"]) == expected
    assert all(bar["date"] <= as_of for bar in evidence["recent_bars"])
    assert any("as-of 守卫" in warning for warning in warnings)
    assert "19" in warnings[0], warnings

    # 反过来：只给到决策日就不该有任何剔除警告
    _, clean = tc.build_evidence("600519", as_of, bars[:41])
    assert clean == []


def test_the_evidence_pack_carries_indicators_and_derived_facts():
    bars = make_bars(90)
    evidence, _ = tc.build_evidence("600519", bars[-1]["date"], bars)

    assert evidence["symbol"] == "600519"
    assert evidence["as_of"] == bars[-1]["date"]
    assert evidence["last_close"] == round(bars[-1]["close"], 4)
    assert evidence["indicators"]["ma_20"] is not None
    assert evidence["indicators"]["ma_60"] is not None
    assert evidence["derived"]["return_20d_pct"] is not None
    assert evidence["derived"]["distance_to_60d_high_pct"] is not None
    assert "不得假设或引用" in evidence["note"], "必须明确告诉模型不许引用未来的事"


def test_the_evidence_pack_is_deterministic():
    bars = make_bars(80)
    first, _ = tc.build_evidence("600519", bars[-1]["date"], bars)
    second, _ = tc.build_evidence("600519", bars[-1]["date"], bars)
    assert first == second, "同样的输入必须给出同样的证据包，否则决策不可归因"


def test_the_position_is_part_of_the_evidence():
    """同一个行情，空仓与满仓该给不同的答案 —— 所以持仓必须在证据里。"""
    empty, _ = tc.build_evidence("600519", AS_OF, make_bars(30))
    holding, _ = tc.build_evidence("600519", AS_OF, make_bars(30),
                                   {"shares": 100, "entry_price": 10.0, "high_watermark": 12.0})

    assert empty["position"]["shares"] == 0.0
    assert holding["position"]["shares"] == 100.0
    assert holding["position"]["avg_cost"] == 10.0


def test_no_bars_before_the_decision_day_is_a_skip_not_a_crash():
    result = tc.decide("600519", "2020-01-01", make_bars(30), completion=FakeLLM())

    assert result["decision"] == ec.DECISION_SKIP
    assert result["skip_reason"] == ec.SKIP_NO_BAR
    assert result["committee"]["llm_calls"] == 0, "没有证据就不该花钱"


def test_a_decision_day_without_its_own_bar_does_not_borrow_yesterdays_price():
    """**真跑发现的坑**：2026-08-21 无 bar，当时静默用了 08-20 的收盘价。

    决策日当天必须有 bar —— 与规则路径的 `bar_date_missing` 同一条纪律
    （休市/数据未出时宁可不结算，也不要用旧 bar 冒充当日）。
    而且要在跑委员会**之前**就拦住：花 30k tokens 去为一个无 bar 的日子辩论没有意义。
    """
    bars = make_bars(30)
    missing_day = "2026-01-31"          # 序列里没有这一天
    llm = FakeLLM()

    result = tc.decide("600519", missing_day, bars, completion=llm)

    assert result["decision"] == ec.DECISION_SKIP
    assert result["skip_reason"] == ec.SKIP_NO_BAR
    assert result["bar_date_missing"] is True
    assert llm.calls == [], "无 bar 的日子不该发起任何 LLM 调用"
    assert any("没有 K 线" in warning for warning in result["committee"]["warnings"])
    assert result["fingerprint"]["decision_mode"] == ec.DECISION_MODE_AGENT, "口径仍要写清楚"


def test_a_decision_day_with_its_own_bar_proceeds():
    bars = make_bars(30)
    llm = FakeLLM()
    result = tc.decide("600519", bars[-1]["date"], bars, completion=llm)

    assert result.get("bar_date_missing") is not True
    assert len(llm.calls) == len(tc.ROLES)
    assert result["price"] == round(bars[-1]["close"], 4)


# ==================== 2. 角色顺序与结构 ====================

def test_all_roles_run_in_a_fixed_order():
    llm = FakeLLM()
    result = tc.decide("600519", AS_OF, make_bars(90), completion=llm)

    assert [entry["role"] for entry in result["committee"]["roles"]] == list(tc.ROLES)
    assert result["committee"]["llm_calls"] == len(tc.ROLES)
    assert result["committee"]["prompt_version"] == prompt_version()


def test_the_bear_role_sees_the_bull_argument():
    """反对意见必须是**流程产物**：Bear 的输入里必须有多头刚说过的话。"""
    llm = FakeLLM()
    tc.decide("600519", AS_OF, make_bars(90), completion=llm)

    bear_prompt = llm.calls[1]["user"]
    assert "bull" in bear_prompt and "我的看法" in bear_prompt


def test_the_judge_sees_every_earlier_role():
    llm = FakeLLM()
    tc.decide("600519", AS_OF, make_bars(90), completion=llm)

    judge_prompt = llm.calls[-1]["user"]
    for role in ("bull", "bear", "research_manager", "trader",
                 "risk_aggressive", "risk_conservative", "risk_neutral"):
        assert role in judge_prompt, role


def test_the_trader_and_judge_are_given_the_output_contract():
    llm = FakeLLM()
    tc.decide("600519", AS_OF, make_bars(90), completion=llm)

    assert "FINAL TRANSACTION PROPOSAL" in llm.calls[3]["system"], "交易员要按契约输出"
    assert "FINAL TRANSACTION PROPOSAL" in llm.calls[-1]["system"], "裁决者要按契约输出"


def test_every_role_is_recorded_with_a_hash_and_an_excerpt():
    result = tc.decide("600519", AS_OF, make_bars(90), completion=FakeLLM())
    for entry in result["committee"]["roles"]:
        assert entry["label"]
        assert len(entry["sha256"]) == 12
        assert entry["chars"] > 0
        assert entry["excerpt"]


# ==================== 3. 硬预算 ====================

def test_the_call_budget_stops_the_run_and_says_where(monkeypatch):
    monkeypatch.setattr(tc, "MAX_LLM_CALLS", 3)
    llm = FakeLLM()

    result = tc.decide("600519", AS_OF, make_bars(90), completion=llm)

    assert len(llm.calls) == 3, "上限到了就必须停，不能把 8 个角色跑完"
    assert result["decision"] == ec.DECISION_SKIP
    assert result["skip_reason"] == ec.SKIP_AGENT_BUDGET_EXCEEDED
    assert any("停在角色" in warning for warning in result["committee"]["warnings"])


def test_the_token_budget_stops_the_run(monkeypatch):
    monkeypatch.setattr(tc, "MAX_TOTAL_TOKENS", 250)
    llm = FakeLLM(per_call_tokens=100)

    result = tc.decide("600519", AS_OF, make_bars(90), completion=llm)

    assert result["skip_reason"] == ec.SKIP_AGENT_BUDGET_EXCEEDED
    assert result["committee"]["llm_calls"] < len(tc.ROLES)


def test_the_budget_is_a_skip_reason_not_a_silent_hold():
    """"花光预算"与"模型说 HOLD"在结果上都不动，但原因必须分开 ——
    否则预算设小了这件事永远发现不了。"""
    assert ec.SKIP_AGENT_BUDGET_EXCEEDED != ec.SKIP_RULE_NOT_MET
    assert ec.SKIP_AGENT_BUDGET_EXCEEDED in ec.SKIP_REASONS
    assert ec.SKIP_AGENT_LLM_UNAVAILABLE in ec.SKIP_REASONS


# ==================== 4. 决策由代码收口 ====================

def test_a_hold_verdict_becomes_a_skip():
    result = tc.decide("600519", AS_OF, make_bars(90),
                       completion=FakeLLM('FINAL TRANSACTION PROPOSAL: **HOLD**\n'
                                          '```json\n{"signal":"HOLD","size_fraction":0}\n```'))
    assert result["decision"] == ec.DECISION_SKIP
    assert result["skip_reason"] == ec.SKIP_RULE_NOT_MET


def test_a_buy_verdict_with_size_becomes_a_buy():
    bars = make_bars(90)
    result = tc.decide("600519", AS_OF, bars,
                       completion=FakeLLM('FINAL TRANSACTION PROPOSAL: **BUY**\n'
                                          '```json\n{"signal":"BUY","size_fraction":0.4,'
                                          '"confidence":0.6,"rationale":"趋势向上"}\n```'))
    assert result["decision"] == ec.DECISION_BUY
    assert result["size_fraction"] == 0.4
    assert result["rationale"] == "趋势向上"
    # 价格取**决策日**那根的收盘价，不是序列最后一根 —— as-of 守卫在这里体现得非常具体
    close_at_as_of = [bar["close"] for bar in bars if bar["date"] == AS_OF][-1]
    assert result["price"] == round(close_at_as_of, 4)
    assert result["price"] != round(bars[-1]["close"], 4), "序列里还有更晚的 bar，但决策看不见它们"


def test_only_the_judge_decides_not_the_earlier_roles():
    """中途角色说得再激进都不算决策 —— 决策只认最后一棒。"""
    llm = FakeLLM('FINAL TRANSACTION PROPOSAL: **HOLD**')
    result = tc.decide("600519", AS_OF, make_bars(90), completion=llm)
    assert result["decision"] == ec.DECISION_SKIP
    # 前面的角色照常发言（含"我的看法"），但决策来自裁决者
    assert result["committee"]["roles"][3]["role"] == "trader"


def test_an_unparsable_verdict_skips_with_a_real_reason():
    result = tc.decide("600519", AS_OF, make_bars(90),
                       completion=FakeLLM("我觉得可以再等等，量能还没配合。"))

    assert result["decision"] == ec.DECISION_SKIP
    assert result["skip_reason"] == ec.SKIP_AGENT_UNPARSABLE


def test_an_empty_verdict_skips_too():
    result = tc.decide("600519", AS_OF, make_bars(90), completion=FakeLLM(""))
    assert result["skip_reason"] == ec.SKIP_AGENT_UNPARSABLE


# ==================== 5. 故障与留痕 ====================

def test_a_role_failure_degrades_to_a_skip_not_an_exception():
    result = tc.decide("600519", AS_OF, make_bars(90), completion=FakeLLM(fail_on=2))

    assert result["decision"] == ec.DECISION_SKIP
    assert result["skip_reason"] == ec.SKIP_AGENT_LLM_UNAVAILABLE
    assert any("调用失败" in warning for warning in result["committee"]["warnings"])


def test_an_unavailable_model_service_is_its_own_reason(monkeypatch):
    """模型不可用是**我们这边的故障**，不是市场的结论 —— 必须与 HOLD 分开。"""
    import llm_service

    monkeypatch.setattr(llm_service, "_is_available", lambda: False)
    llm = FakeLLM()
    result = tc.decide("600519", AS_OF, make_bars(90), completion=llm)

    assert result["skip_reason"] == ec.SKIP_AGENT_LLM_UNAVAILABLE
    assert llm.calls == [], "不可用就不该发起调用"


def test_the_snapshot_carries_the_whole_committee_for_the_trace():
    result = tc.decide("600519", AS_OF, make_bars(90), completion=FakeLLM())
    snapshot = result["snapshot"]
    committee = snapshot["extra"]["committee"]

    assert snapshot["params"]["decision_mode"] == ec.DECISION_MODE_AGENT
    assert committee["as_of"] == AS_OF
    assert committee["llm_calls"] == len(tc.ROLES)
    assert committee["total_tokens"] == len(tc.ROLES) * 100
    assert committee["budget"]["max_calls"] == tc.MAX_LLM_CALLS
    assert len(committee["roles"]) == len(tc.ROLES)
    # 证据包也在快照里（指标 + 证据来源）
    assert snapshot["indicators"]["ma_20"] is not None


def test_the_fingerprint_says_agent_so_the_curves_stay_incomparable():
    result = tc.decide("600519", AS_OF, make_bars(90), completion=FakeLLM())

    assert result["fingerprint"]["decision_mode"] == ec.DECISION_MODE_AGENT
    rule = ec.fingerprint_for(ec.SETTLEMENT_DAILY)
    allowed, reason = ec.compare_allowed(
        ec.fingerprint_for(ec.SETTLEMENT_DAILY, decision_mode=ec.DECISION_MODE_AGENT), rule)
    assert not allowed and "decision_mode" in reason


def test_every_branch_returns_the_closed_enum_and_a_reason():
    """所有分支都必须落在封闭集里 —— 结算侧遇到未知值只会归一成 unknown_*。"""
    cases = [
        FakeLLM('FINAL TRANSACTION PROPOSAL: **BUY**\n```json\n{"size_fraction":0.3}\n```'),
        FakeLLM('FINAL TRANSACTION PROPOSAL: **SELL**\n```json\n{"size_fraction":0.3}\n```'),
        FakeLLM('FINAL TRANSACTION PROPOSAL: **HOLD**'),
        FakeLLM("读不懂"),
        FakeLLM(fail_on=1),
    ]
    for llm in cases:
        result = tc.decide("600519", AS_OF, make_bars(90), completion=llm)
        assert result["decision"] in ec.DECISIONS
        assert 0.0 <= result["size_fraction"] <= 1.0
        if result["decision"] == ec.DECISION_SKIP:
            assert result["skip_reason"] in ec.SKIP_REASONS, result["skip_reason"]


def test_describe_exposes_roles_and_budgets_for_health():
    described = tc.describe()
    assert len(described["roles"]) == len(tc.ROLES)
    assert described["max_llm_calls"] == tc.MAX_LLM_CALLS
    assert described["decision_mode"] == ec.DECISION_MODE_AGENT
    assert "决策日" in described["as_of_guard"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print("PASS", fn.__name__)
