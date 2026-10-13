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


# ==================== 6. 召集门控（事件驱动） ====================

def crossing_config():
    """一个会在最后一根上"上穿 20 日均线"的规则 —— 用来测 rule_signal 触发。"""
    return {"schema_version": "1.0", "name": "上穿20日线", "symbol": "600519",
            "entry": {"logic": "all", "conditions": [
                {"type": "price_cross_ma", "window": 20, "direction": "above"}]},
            "exit": {"logic": "any", "conditions": [
                {"type": "price_cross_ma", "window": 20, "direction": "below"}]}}


def quiet_bars(n=60, price=100.0):
    """一根几乎不动的序列：没有规则信号、没有波动异常。"""
    return [{"date": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
             "open": price, "high": price + 0.05, "low": price - 0.05,
             "close": price, "volume": 1000} for i in range(n)]


def test_nothing_happening_means_no_briefing_at_all():
    """没有触发理由 → **一次调用都不花**，并留下一个真实原因。

    "没看"与"看过之后决定不动"必须分得开：前者说明今天没有值得开会的理由，
    后者说明委员们看了。混在一起，"agent 多久没真正看过行情"就永远查不出来。
    """
    bars = quiet_bars(60)
    llm = FakeLLM()

    result = tc.decide("600519", bars[-1]["date"], bars, completion=llm,
                       last_decision_at=bars[-2]["date"])   # 昨天刚开过会

    assert llm.calls == [], "没有触发理由就不该发起任何调用"
    assert result["decision"] == ec.DECISION_SKIP
    assert result["skip_reason"] == ec.SKIP_AGENT_NO_NEW_INFORMATION
    assert result["committee"]["llm_calls"] == 0
    assert result["committee"]["gate"]["convened"] is False
    assert result["committee"]["gate"]["reasons"] == []


def test_the_skip_for_not_convening_still_leaves_evidence():
    """没开会也要留那天的 bar 与指标：否则"没开会"与"没跑"长得一样。"""
    bars = quiet_bars(60)
    result = tc.decide("600519", bars[-1]["date"], bars, completion=FakeLLM(),
                       last_decision_at=bars[-2]["date"])

    snapshot = result["snapshot"]
    assert snapshot is not None
    assert snapshot["bar"]["date"] == bars[-1]["date"]
    assert snapshot["indicators"]["ma_20"] is not None
    assert snapshot["extra"]["gate"]["convened"] is False
    assert result["price"] == round(bars[-1]["close"], 4), "价格照旧回传（结算要用）"


def test_a_rule_signal_convenes_the_committee():
    """规则给出买/卖 = 值得开会 —— 这一条**复用规则引擎本身**，不另写判断。

    涨幅刻意压到 1%（低于 3% 的绝对波动下限）：这样触发理由**只有** rule_signal，
    测的才是"规则信号"这一条，而不是顺手把波动异常也测了。
    """
    records = quiet_bars(40) + [{"date": "2026-02-14", "open": 100.5, "high": 101.2,
                                 "low": 100.4, "close": 101.0, "volume": 1000}]
    llm = FakeLLM()

    result = tc.decide("600519", records[-1]["date"], records, completion=llm,
                       config=crossing_config(), last_decision_at=records[-2]["date"])

    assert result["committee"]["gate"]["reasons"] == [tc.TRIGGER_RULE_SIGNAL]
    assert result["committee"]["gate"]["rule_signal"] == "buy"
    assert len(llm.calls) == len(tc.ROLES)


def test_a_volatility_spike_convenes_the_committee():
    """异常波动/量能也要开会：那正是"行情变了"的信号。"""
    bars = quiet_bars(40)
    bars.append({"date": "2026-02-14", "open": 130.0, "high": 131.0, "low": 129.0,
                 "close": 130.0, "volume": 1000})     # 平盘之后一天涨 30%
    llm = FakeLLM()

    result = tc.decide("600519", bars[-1]["date"], bars, completion=llm,
                       last_decision_at=bars[-2]["date"])

    assert tc.TRIGGER_VOLATILITY_SPIKE in result["committee"]["gate"]["reasons"]
    assert len(llm.calls) == len(tc.ROLES)


def test_a_spike_on_a_perfectly_flat_base_still_counts():
    """**写测试时发现的真问题**：极静市场里近 20 日平均波动可能是 0，
    而"2×0 仍然是 0" —— 于是"一片平静之后突然一跳"这种最该开会的日子反而不开会。
    所以除了相对倍数，还要有一条**绝对**下限。"""
    bars = quiet_bars(40)                       # 完全不动 → 平均波动 = 0
    bars.append({"date": "2026-02-14", "open": 105.0, "high": 105.5, "low": 104.5,
                 "close": 105.0, "volume": 1000})   # 涨 5%，平均波动仍是 0

    reasons, gate = tc.convene_reasons(bars, bars[-1]["date"],
                                       last_decision_at=bars[-2]["date"])

    assert tc.TRIGGER_VOLATILITY_SPIKE in reasons
    assert gate["average_move_pct"] == 0.0
    assert gate["spike_basis"] in ("absolute", "both")


def test_a_volume_spike_alone_is_enough():
    bars = quiet_bars(40)
    bars.append({"date": "2026-02-14", "open": 100.0, "high": 100.1, "low": 99.9,
                 "close": 100.0, "volume": 9000})     # 量能 9 倍，价格没动
    result = tc.decide("600519", bars[-1]["date"], bars, completion=FakeLLM(),
                       last_decision_at=bars[-2]["date"])
    assert tc.TRIGGER_VOLATILITY_SPIKE in result["committee"]["gate"]["reasons"]


def test_holding_gets_a_tighter_clock_than_being_flat():
    """持仓时风险敞口是真实的，所以盯得更紧。"""
    bars = quiet_bars(60)
    as_of = bars[-1]["date"]
    four_days_ago = "2026-02-24"      # 与 as_of 相差 4 天

    flat = tc.decide("600519", as_of, bars, completion=FakeLLM(), last_decision_at=four_days_ago)
    holding = tc.decide("600519", as_of, bars, {"shares": 100, "entry_price": 99.0},
                        completion=FakeLLM(), last_decision_at=four_days_ago)

    assert flat["committee"]["gate"]["reasons"] == [], "空仓 4 天还不算久（上限 10 天）"
    assert holding["committee"]["gate"]["reasons"] == [tc.TRIGGER_STALE], \
        "持仓 4 天就该看了（上限 3 天）"
    assert holding["committee"]["gate"]["stale_limit_days"] == tc.MAX_DAYS_HOLDING


def test_never_briefed_before_convenes_instead_of_staying_silent():
    """**fail-open**：传参缺失时宁可多花钱，也不要因为一个字段没传就永远沉默。"""
    bars = quiet_bars(30)
    result = tc.decide("600519", bars[-1]["date"], bars, completion=FakeLLM())

    assert result["committee"]["gate"]["reasons"] == [tc.TRIGGER_FIRST_DECISION]
    assert result["committee"]["llm_calls"] == len(tc.ROLES)


def test_the_gate_detail_is_recorded_for_traceability():
    """门控的判定细节要能被事后复查（为什么开/为什么不开）。"""
    bars = quiet_bars(60)
    result = tc.decide("600519", bars[-1]["date"], bars, completion=FakeLLM(),
                       last_decision_at=bars[-2]["date"])
    gate = result["committee"]["gate"]

    assert gate["as_of"] == bars[-1]["date"]
    assert gate["last_decision_at"] == bars[-2]["date"]
    assert gate["days_since_last_decision"] == 1
    assert gate["volume_ratio"] == 1.0
    assert "today_move_pct" in gate


def test_every_trigger_reason_is_in_the_closed_set():
    for reason in tc.CONVENE_TRIGGERS:
        assert isinstance(reason, str) and reason and reason.islower()
    assert ec.SKIP_AGENT_NO_NEW_INFORMATION in ec.SKIP_REASONS
    assert ec.SKIP_AGENT_NO_NEW_INFORMATION not in tc.CONVENE_TRIGGERS


def test_describe_exposes_the_gate_so_it_can_be_seen():
    described = tc.describe()
    assert described["convene_triggers"] == list(tc.CONVENE_TRIGGERS)
    assert described["stale_limits"]["holding_days"] < described["stale_limits"]["flat_days"]
    assert described["skip_when_not_convened"] == ec.SKIP_AGENT_NO_NEW_INFORMATION


def test_describe_exposes_roles_and_budgets_for_health():
    described = tc.describe()
    assert len(described["roles"]) == len(tc.ROLES)
    assert described["max_llm_calls"] == tc.MAX_LLM_CALLS
    assert described["decision_mode"] == ec.DECISION_MODE_AGENT
    assert "决策日" in described["as_of_guard"]


# ==================== 每日 token 预算硬顶（成本止损线） ====================

def test_daily_token_budget_blocks_the_committee_before_any_llm_call(monkeypatch):
    """预算用完 → 不开会、零调用、skip=agent_budget_exceeded（不是静默 HOLD）。

    预算口径是**当日**用量（daily_llm_tokens）：拿进程终身累计比较，
    超限一天之后委员会就从此不再开会直到重启，"每日预算"名不副实。
    """
    monkeypatch.setenv("LLM_DAILY_TOKEN_BUDGET", "1000")

    import agent.metering as metering_mod
    # 预算判定读 metering.daily_llm_tokens()（模块级便捷入口），直接换掉它
    monkeypatch.setattr(metering_mod, "daily_llm_tokens", lambda: 1500)

    llm = FakeLLM()
    result = tc.decide("600519", AS_OF, make_bars(90), completion=llm)

    assert llm.calls == [], "预算耗尽后一次调用都不该发生"
    assert result["skip_reason"] == ec.SKIP_AGENT_BUDGET_EXCEEDED
    # 预算分支也必须带价格与证据快照：结算侧拿不到 price 会把它记成 invalid_price，
    # "预算设小了"这件事就永远看不见了。
    assert result["price"], "预算分支必须带当日收盘价"
    assert result["snapshot"], "预算分支必须带证据快照"


def test_daily_token_budget_zero_means_disabled(monkeypatch):
    """默认（0/未配置）= 不设限：老行为分毫不变。"""
    monkeypatch.setenv("LLM_DAILY_TOKEN_BUDGET", "0")
    result = tc.decide("600519", AS_OF, make_bars(90), completion=FakeLLM())
    assert result["skip_reason"] != ec.SKIP_AGENT_BUDGET_EXCEEDED


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print("PASS", fn.__name__)


# ==================== news_briefing（agent 互通 P3，2026-10-09） ====================


def test_news_briefing_enters_the_evidence_seen_by_every_role():
    """信息面证据随 evidence 进 8 个角色；空包/缺省不注入（fail-open 不是带空块）。"""
    llm = FakeLLM()
    briefing = {"topics": [{"title": "重组草案出炉", "digest": "内控否定意见影响消除",
                            "direction": "利好", "event_count": 39}],
                "events": [], "note": "信息面证据：可信度分级见各条 credibility"}
    tc.decide("600519", AS_OF, make_bars(90), completion=llm, news_briefing=briefing)

    assert llm.calls, "first_decision 门控下应当真的开会"
    for call in llm.calls:
        assert '"news_briefing"' in call["user"], "每个角色都该看到同一份信息面证据"
        assert "重组草案出炉" in call["user"]

    # 空包不注入：缺席时委员会照旧只看行情（fail-open，不是带空块浪费上下文）
    llm2 = FakeLLM()
    tc.decide("600519", AS_OF, make_bars(90), completion=llm2, news_briefing={})
    for call in llm2.calls:
        assert '"news_briefing"' not in call["user"]


# ==================== experience（P4 经验回流，2026-10-12） ====================


def test_experience_enters_the_evidence_seen_by_every_role():
    """策略自己的成绩单随 evidence 进 8 个角色；空包/缺省不注入（fail-open 不是带空块）。"""
    llm = FakeLLM()
    experience = {"note": "以下是这条策略自己过去的成交成绩单（开会前先读自己的病历）。",
                  "evaluatedTrades": 4, "winRatePct": 25.0, "avgAlignedPct": -0.8,
                  "recent": [{"date": "2026-09-28", "side": "buy", "mode": "agent",
                              "alignedPct": -2.1, "win": False}]}
    tc.decide("600519", AS_OF, make_bars(90), completion=llm, experience=experience)

    assert llm.calls, "first_decision 门控下应当真的开会"
    for call in llm.calls:
        assert '"experience"' in call["user"], "每个角色都该看到同一份成绩单"
        assert "winRatePct" in call["user"]

    llm2 = FakeLLM()
    tc.decide("600519", AS_OF, make_bars(90), completion=llm2, experience={})
    for call in llm2.calls:
        assert '"experience"' not in call["user"]


def test_portfolio_context_enters_the_evidence_seen_by_every_role():
    """组合上下文（P5）随 evidence 进 8 个角色；空包/缺省不注入（fail-open 不是带空块）。"""
    llm = FakeLLM()
    portfolio = {"note": "账户与组合上下文：你管的不只这一条策略。",
                 "thisStrategy": {"cash": 667.49, "shares": 14600, "positionSharePct": 99.3},
                 "userPortfolio": {"strategiesRunning": 1, "openPositions": [], "totalPositionValue": 100448.0}}
    tc.decide("600519", AS_OF, make_bars(90), completion=llm, portfolio=portfolio)

    assert llm.calls, "first_decision 门控下应当真的开会"
    for call in llm.calls:
        assert '"portfolio"' in call["user"], "每个角色都该看到同一份组合上下文"
        assert "positionSharePct" in call["user"]

    llm2 = FakeLLM()
    tc.decide("600519", AS_OF, make_bars(90), completion=llm2, portfolio={})
    for call in llm2.calls:
        assert '"portfolio"' not in call["user"]


# ==================== 会议上下文（why_convened / strategy_intent / dissent_brief，2026-10-13） ====================


def test_the_committee_is_told_why_it_was_convened():
    """**被召集者必须知道谁喊的开会**：门控因为 rule_signal 触发时，
    证据包里必须带上这个信号——否则规则引擎喊大家来开会，与会者却不知道
    是谁喊的、喊的是什么，信息在半路丢了。"""
    records = quiet_bars(40) + [{"date": "2026-02-14", "open": 100.5, "high": 101.2,
                                 "low": 100.4, "close": 101.0, "volume": 1000}]
    llm = FakeLLM()

    result = tc.decide("600519", records[-1]["date"], records, completion=llm,
                       config=crossing_config(), last_decision_at=records[-2]["date"])

    assert result["committee"]["gate"]["reasons"] == [tc.TRIGGER_RULE_SIGNAL]
    for call in llm.calls:
        assert '"why_convened"' in call["user"], "每个角色都该知道召集原因"
        assert '"rule_signal": "buy"' in call["user"], "规则信号必须随召集原因进场"


def test_stale_convening_also_carries_its_reason():
    """非 rule_signal 的召集（如 stale）也要说明原因——只是 rule_signal 为 null。"""
    bars = quiet_bars(60)
    llm = FakeLLM()
    tc.decide("600519", bars[-1]["date"], bars, {"shares": 100, "entry_price": 99.0},
              completion=llm, last_decision_at="2026-02-24")

    assert '"why_convened"' in llm.calls[0]["user"]
    assert '"rule_signal": null' in llm.calls[0]["user"]


def test_strategy_intent_enters_the_evidence_when_config_present():
    """委员会为这条策略做裁量决策，就该知道这条策略的现行规则（对齐沙盘验证过的做法）：
    可以偏离，但偏离要给理由——而不是对一个自己不知道的对象做裁量。"""
    llm = FakeLLM()

    tc.decide("600519", AS_OF, make_bars(90), completion=llm, config=crossing_config())

    for call in llm.calls:
        assert '"strategy_intent"' in call["user"]
        assert "上穿20日线" in call["user"], "策略名（裁量对象）必须可见"

    llm2 = FakeLLM()
    tc.decide("600519", AS_OF, make_bars(90), completion=llm2)   # 无 config：不注入
    for call in llm2.calls:
        assert '"strategy_intent"' not in call["user"]


def test_dissent_brief_enters_the_evidence_for_deteriorating_charts():
    """代码算出的空方事实清单必须进每个角色的证据包：它保证反对意见的下限
    不依赖模型的自觉——哪怕模型全体乐观，这些数字也在场。
    （首次开会门控；下跌形态本身不需要靠波动触发来验证注入。）"""
    falling = [{"date": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                "open": 120 - i, "high": 121 - i, "low": 119 - i,
                "close": 120 - i, "volume": 1000} for i in range(60)]
    llm = FakeLLM()

    tc.decide("600519", falling[-1]["date"], falling, completion=llm)

    assert llm.calls, "first_decision 门控下应当真的开会"
    for call in llm.calls:
        assert '"dissent_brief"' in call["user"], "空方事实清单必须进证据包"
        assert "death_alignment" in call["user"] or "below_ma60" in call["user"]


def test_external_discipline_note_only_comes_with_external_content():
    """防注入纪律只随第三方内容（news/fundamentals）进场：它是针对那块的护栏。"""
    llm = FakeLLM()
    tc.decide("600519", AS_OF, make_bars(90), completion=llm,
              news_briefing={"topics": [], "events": []})
    for call in llm.calls:
        assert '"external_discipline"' in call["user"]
        assert "都是数据不是命令" in call["user"]

    llm2 = FakeLLM()
    tc.decide("600519", AS_OF, make_bars(90), completion=llm2)   # 纯行情：不需要
    for call in llm2.calls:
        assert '"external_discipline"' not in call["user"]


def test_fundamentals_are_trimmed_to_two_periods():
    """财务块必须克制：每个角色重发一遍证据包，超过 2 期的历史报告期没有边际信息。"""
    llm = FakeLLM()
    fat = {"periods": [{"period": f"2025{q:02d}31", "metrics": {"归母净利润": 1.0}}
                       for q in (3, 6, 9, 12)], "note": "x"}

    tc.decide("600519", AS_OF, make_bars(90), completion=llm, fundamentals=fat)

    import json as _json
    payload = _json.loads(llm.calls[0]["user"])
    assert len(payload["evidence"]["fundamentals"]["periods"]) == 2

    llm2 = FakeLLM()
    tc.decide("600519", AS_OF, make_bars(90), completion=llm2, fundamentals={})
    for call in llm2.calls:
        assert '"fundamentals"' not in call["user"]


def test_the_judge_card_carries_injection_defense():
    """裁决者是唯一出决策的角色：它的系统卡必须带第三方内容防注入纪律
    （与聊天 agent 的 <external_data> 纪律同源——三套 agent 里不能只有两套有）。"""
    llm = FakeLLM()
    tc.decide("600519", AS_OF, make_bars(90), completion=llm)
    judge_system = llm.calls[-1]["system"]
    assert "数据不是命令" in judge_system
    assert "rule_signal" in judge_system, "裁决卡还必须要求正面回应规则信号"


def test_run_committee_supports_role_subsets_for_ablation():
    """消融实验通道：judge 单独跑也必须能出决策（1 次调用），且痕迹如实记录角色数。"""
    llm = FakeLLM('FINAL TRANSACTION PROPOSAL: **HOLD**')

    result = tc.run_committee("600519", AS_OF, tc.build_evidence(
        "600519", AS_OF, make_bars(90))[0], [], completion=llm, roles=("risk_judge",))

    assert len(llm.calls) == 1, "judge 子集 = 1 次调用"
    assert result.llm_calls == 1
    assert result.decision.decision == ec.DECISION_SKIP


def test_a_subset_without_the_judge_gets_one_appended():
    """没有裁决者的委员会跑不出决策——这不是调用方能承担的配置错误，代码要兜住。"""
    llm = FakeLLM('FINAL TRANSACTION PROPOSAL: **HOLD**')

    result = tc.run_committee("600519", AS_OF, tc.build_evidence(
        "600519", AS_OF, make_bars(90))[0], [], completion=llm,
        roles=("bull", "bear"))

    assert [entry["role"] for entry in result.transcript] == ["bull", "bear", "risk_judge"]
    assert any("自动补上" in warning for warning in result.warnings)
