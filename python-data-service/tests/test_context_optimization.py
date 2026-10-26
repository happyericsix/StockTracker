# -*- coding: utf-8 -*-
"""上下文工程与路由优化（2026-10-18）的单测。

钉住四件事：
1. **渲染瘦身不动结构**：_compact_for_prompt 只改 prompt 里看到的样子，
   内部证据结构（dissent/_bar_of/测试消费的形状）一个字段不变；
2. **版本化真的敏感**：角色卡或 few-shot 一改，role_system_version 必须变；
3. **校准提示是确定性翻译**：三档口径与样本门槛，不下任何判断；
4. **教训块与路由**：lessons_block 的信任语义、终审角色路由只在生产默认路径生效。
"""
import agent.trading_committee as tc
from agent.trading_committee import (
    _calibration_hint,
    _compact_for_prompt,
    _FEW_SHOTS,
    _role_messages,
    lessons_block,
    REASONER_ROLES,
    role_system_version,
)


def _bars(n=3):
    return [{"date": f"2026-03-0{i + 1}", "open": 10.123, "high": 10.5, "low": 10.0,
             "close": 10.3, "volume": 1234567.0} for i in range(n)]


def _evidence():
    return {"symbol": "600519", "recent_bars": _bars(), "last_close": 10.3,
            "indicators": {"ma_5": 10.2}}


# ==================== 渲染瘦身 ====================

def test_compact_prompt_compresses_bars_with_legend():
    out = _compact_for_prompt(_evidence())
    bar = out["recent_bars"][0]
    assert isinstance(bar, list) and len(bar) == 6
    assert bar[0] == "2026-03-01" and bar[1] == 10.12 and bar[5] == 1234567
    assert out["bars_format"] == "[date, open, high, low, close, volume]"


def test_compact_prompt_leaves_internal_structure_untouched():
    ev = _evidence()
    _compact_for_prompt(ev)
    assert isinstance(ev["recent_bars"][0], dict)          # 原结构未动
    assert ev["recent_bars"][0]["open"] == 10.123


def test_compact_prompt_survives_garbage():
    assert _compact_for_prompt({"recent_bars": "不是列表"})["recent_bars"] == "不是列表"


def test_role_messages_use_compact_evidence():
    msgs = _role_messages("bull", _evidence(), [])
    user = msgs[1]["content"]
    # 逐 bar 的 dict 键（"open": 等）不再出现；图例是纯文本，不是 JSON 键
    assert '"open":' not in user and '"high":' not in user
    assert "[date, open, high, low, close, volume]" in user


# ==================== few-shot 与版本化 ====================

def test_few_shot_attached_to_target_roles_only():
    for role in ("bear", "trader", "risk_judge"):
        system = _role_messages(role, _evidence(), [])[0]["content"]
        assert _FEW_SHOTS[role] in system
    assert _FEW_SHOTS["bear"] not in _role_messages("bull", _evidence(), [])[0]["content"]


def test_role_system_version_changes_when_cards_or_shots_change(monkeypatch):
    base = role_system_version()
    monkeypatch.setitem(tc._ROLE_SYSTEM, "bull", "改过的卡片")
    assert role_system_version() != base
    monkeypatch.setitem(tc._FEW_SHOTS, "bear", "改过的范例")
    assert role_system_version() != base


def test_audit_carries_role_system_version():
    result = tc.run_committee("600519", "2026-03-03", _evidence(), [],
                              completion=lambda *a, **k: {
                                  "message": {"content": "FINAL TRANSACTION PROPOSAL: **HOLD**"},
                                  "usage": {}})
    audit = result.as_audit()
    assert audit["role_system_version"] == role_system_version()
    assert len(audit["roles"]) == 8


# ==================== 校准提示 ====================

def test_calibration_hint_three_tiers_and_sample_floor():
    assert _calibration_hint({"evaluatedTrades": 2, "winRatePct": 70}) is None   # 样本不足
    hot = _calibration_hint({"evaluatedTrades": 6, "winRatePct": 60})
    mid = _calibration_hint({"evaluatedTrades": 6, "winRatePct": 50})
    cold = _calibration_hint({"evaluatedTrades": 6, "winRatePct": 30})
    assert hot and mid and cold
    assert "打折" in mid["rule"] and "同步下调" in cold["rule"] and "满格" in hot["rule"]
    assert cold["win_rate_pct"] == 30.0


def test_calibration_hint_attached_to_evidence():
    # decide 全链路太重（要过门控），这里钉"experience 样本足够时证据包里一定有 hint"
    ev = {}
    hint = _calibration_hint({"evaluatedTrades": 5, "winRatePct": 40})
    assert hint and hint["sample_trades"] == 5


# ==================== 教训块 ====================

def test_lessons_block_filters_and_caps():
    items = [{"symptom": f"症状{i}", "reusableRule": f"规则{i}"} for i in range(5)]
    block = lessons_block(items)
    assert len(block["items"]) == 3                       # 上限 3 条
    assert "未经用户逐条确认" in block["note"]            # 信任级别必须写明
    assert lessons_block([]) is None
    assert lessons_block([{"foo": 1}]) is None            # 没有可注入内容的行不算教训


def test_lessons_block_accepts_java_shapes():
    block = lessons_block([{"symptom": "追高", "action": "首仓减半", "occurrences": 2,
                            "origin": "中国银行"}])
    assert block["items"][0]["rule"] == "首仓减半" and block["items"][0]["origin"] == "中国银行"


# ==================== 终审角色路由 ====================

def test_reasoner_routing_only_on_default_path(monkeypatch):
    """completion 替身（沙盘/测试通道）不该收到路由参数；只有 trader/risk_judge 被路由。"""
    seen = []

    def stub(messages, temperature=0.2, max_tokens=800, **kwargs):
        seen.append((messages[0]["content"][:12], max_tokens, kwargs.get("model")))
        return {"message": {"content": "FINAL TRANSACTION PROPOSAL: **HOLD**"}, "usage": {}}

    tc.run_committee("600519", "2026-03-03", _evidence(), [], completion=stub)
    assert all(model is None for _, _, model in seen)      # 替身路径不路由
    assert len(seen) == 8

    calls = []

    def fake_chat_completion(messages, tools=None, temperature=0.2, max_tokens=1200,
                             model=None, thinking=None, response_format=None, timeout_s=None):
        calls.append({"model": model, "thinking": thinking, "max_tokens": max_tokens,
                     "timeout": timeout_s})
        return {"message": {"content": "FINAL TRANSACTION PROPOSAL: **HOLD**"}, "usage": {}}

    import llm_service
    monkeypatch.setattr(llm_service, "chat_completion", fake_chat_completion)
    tc.run_committee("600519", "2026-03-03", _evidence(), [])
    routed = [c for c in calls if c["model"]]
    assert {c["model"] for c in routed} == {tc.REASONER_MODEL}
    assert len(routed) == len(REASONER_ROLES)
    assert all(c["thinking"] is True and c["timeout"] for c in routed)
    # 辩论角色保持老口径
    unrouted = [c for c in calls if not c["model"]]
    assert len(unrouted) == 8 - len(REASONER_ROLES)
    assert all(c["timeout"] is None for c in unrouted)


# ==================== 二轮：条件注入 / 裁剪 / 留痕 / 去重 ====================

from agent.trading_committee import (  # noqa: E402
    _annotate_briefing,
    _evidence_blocks,
    _prior_for,
    _lesson_symptoms,
)


def test_briefing_annotated_only_when_no_symbol_events():
    """注记而非裁剪：topics 是该标的的专题 digest（既有测试钉住的语义），内容一字不丢。"""
    relevant = {"events": [{"title": "立案调查", "symbol": "600519"}],
                "topics": [{"title": "市场专题"}], "market_read": "偏弱"}
    irrelevant = {"events": [], "topics": [{"title": "重组草案出炉", "digest": "x" * 500}],
                  "market_read": "偏弱"}
    out = _annotate_briefing(relevant)
    assert "strength_note" not in out                       # 有事件级信息：原样
    out = _annotate_briefing(irrelevant)
    assert "重组草案出炉" in str(out["topics"])             # 内容保留
    assert "专题级" in out["strength_note"]                  # 只补强度注记


def test_prior_statements_capped_for_risk_roles_only():
    long_research = "多" * 400
    transcript = [{"role": "bull", "excerpt": long_research},
                  {"role": "trader", "excerpt": "提" * 400}]
    judge_view = _prior_for("risk_judge", transcript)
    assert judge_view[0]["said"] == "多" * tc._PRIOR_RESEARCH_CAP_CHARS   # 研究链被裁
    assert len(judge_view[1]["said"]) == 400                             # trader 全文
    trader_view = _prior_for("trader", transcript)
    assert len(trader_view[0]["said"]) == 400                            # trader 看全文


def test_lessons_block_dedupes_against_experience():
    lessons = [{"symptom": "追高被套", "reusableRule": "首仓减半"},
               {"symptom": "不止盈", "reusableRule": "设跟踪止盈"}]
    exclude = _lesson_symptoms({"lessons": [{"symptom": "追高被套 ", "reusableRule": "x"}]})
    block = lessons_block(lessons, exclude=exclude)
    assert [i["symptom"] for i in block["items"]] == ["不止盈"]


def _decide_bars():
    bars = []
    for i in range(40):
        bars.append({"date": f"2026-03-{i + 1:02d}", "open": 10 + i * 0.1,
                     "high": 10.3 + i * 0.1, "low": 9.8 + i * 0.1,
                     "close": 10 + i * 0.1, "volume": 1000 + i})
    return bars


class _HoldLLM:
    def __call__(self, messages, temperature=0.2, max_tokens=800):
        text = ("FINAL TRANSACTION PROPOSAL: **HOLD**"
                if "风控裁决" in messages[0]["content"] else "（发言）")
        return {"message": {"content": text},
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def test_decide_records_evidence_blocks_and_injected_lessons():
    bars = _decide_bars()
    result = tc.decide("600519", bars[-1]["date"], bars,
                       experience={"evaluatedTrades": 5, "winRatePct": 40,
                                   "lessons": [{"symptom": "旧教训", "reusableRule": "r"}]},
                       news_briefing={"events": [], "topics": [{"digest": "x" * 300}]},
                       lessons=[{"symptom": "旧教训", "reusableRule": "r"},
                                {"symptom": "新教训", "reusableRule": "r2"}],
                       completion=_HoldLLM())
    audit = result["committee"]
    names = [b["block"] for b in audit["evidence_blocks"]]
    # 实际携带清单在场且含新增块（A/B 归因的锚点）
    assert "calibration_hint" in names and "trading_lessons" in names
    assert all(b["chars"] > 0 for b in audit["evidence_blocks"])
    # 注入的教训留了痕，且与 experience 内嵌教训去重后只剩新教训
    assert [i["symptom"] for i in audit["injected_lessons"]] == ["新教训"]
    # 简报在场（注记语义：内容保留，只补 strength_note）
    assert "news_briefing" in [b["block"] for b in audit["evidence_blocks"]]


def test_gate_snapshot_records_evidence_blocks_too():
    bars = _decide_bars()
    result = tc.decide("600519", bars[-1]["date"], bars,
                       last_decision_at=bars[-1]["date"],     # 当天刚开会 → 不召集
                       news_briefing={"events": [], "topics": []},
                       completion=_HoldLLM())
    assert result["skip_reason"] == "agent_no_new_information"
    assert result["snapshot"]["extra"]["evidence_blocks"]


# ==================== 裁决者合规重试（2026-10-18 测试轮新增） ====================

class _FlakyJudgeLLM:
    """judge 第一次输出垃圾（不可解析），重试后给规范结论；其余角色正常发言。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, messages, temperature=0.2, max_tokens=800):
        self.calls += 1
        if "风控裁决" in messages[0]["content"] and self.calls == 8:
            text = "综合各方意见，我认为当前证据不足，不宜进场，再观察为宜。"
        elif "风控裁决" in messages[0]["content"]:
            text = ('{"signal": "HOLD", "size_fraction": 0.0, "confidence": 0.55,'
                    ' "rationale": "证据不足"}\nFINAL TRANSACTION PROPOSAL: **HOLD**')
        else:
            text = "（发言）"
        return {"message": {"content": text},
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def test_judge_retry_rescues_unparsable_output():
    llm = _FlakyJudgeLLM()
    result = tc.run_committee("600519", "2026-03-03", _evidence(), [], completion=llm)
    assert llm.calls == 9                                    # 8 角色 + 1 次合规重试
    assert result.llm_calls == 9
    assert result.decision.signal == "hold"                  # 重试后被救回
    assert result.decision.skip_reason != "agent_unparsable"
    assert any("合规重试后解析成功" in w for w in result.warnings)
    labels = [r["label"] for r in result.transcript]
    assert labels.count("风控裁决（合规重试）") == 1         # 重试在痕迹里可见


def test_judge_retry_not_triggered_when_parsable():
    result = tc.run_committee("600519", "2026-03-03", _evidence(), [], completion=_HoldLLM())
    assert result.llm_calls == 8                             # 正常路径不加调用
    assert not any("合规重试" in w for w in result.warnings)
