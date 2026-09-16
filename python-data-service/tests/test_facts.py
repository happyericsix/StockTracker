# -*- coding: utf-8 -*-
"""事实（L2）的规范化与注入渲染。

两条最关键的断言：
1. **键必须稳定**——"止损"与"stop_loss_pct"是同一个键，否则取代链断掉，
   用户改了口之后系统里会同时存在两个互相矛盾的止损值；
2. **事实必须带时间**——"去年买的"要落成绝对日期，解析不出来就不带时间（不猜）。
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import facts, recall, timeutil

NOW = datetime(2026, 9, 16, 10, 30, tzinfo=timeutil.CN_TZ)


def test_predicate_alias_collapses_to_a_stable_key():
    """中文说法与英文键必须收敛成同一个键，这是取代链能工作的前提。"""
    assert facts.canonical_predicate("止损") == "stop_loss_pct"
    assert facts.canonical_predicate("止损比例") == "stop_loss_pct"
    assert facts.canonical_predicate("Stop Loss") == "stop_loss"
    assert facts.canonical_predicate("风险偏好") == "risk_preference"
    assert facts.canonical_predicate("持仓成本") == "holding_cost"


def test_normalize_builds_an_ingestible_fact():
    normalized = facts.normalize([{
        "subject": "user", "predicate": "止损", "object": "5",
        "fact_type": "constraint", "confidence": 0.9, "time_hint": "去年",
    }], now=NOW, source_event_ids=[11, 12])

    assert len(normalized) == 1
    fact = normalized[0]
    assert fact["predicate"] == "stop_loss_pct"
    assert fact["object"] == "5"
    assert fact["fact_type"] == "constraint"
    # 相对时间在写入时解析成绝对时间（这是双时间戳的地基）
    assert fact["event_time"] == "2025-01-01T00:00:00"
    assert fact["raw_time_phrase"] == "去年"
    assert fact["confirmed"] is False
    assert fact["trust"] == "medium"
    assert fact["source_event_ids"] == [11, 12]


def test_unparseable_time_hint_keeps_the_phrase_but_no_timestamp():
    normalized = facts.normalize([{
        "subject": "user", "predicate": "purpose", "object": "长期持有",
        "time_hint": "那会儿",
    }], now=NOW)

    assert normalized[0]["event_time"] is None
    # 原话必须留着，将来要回答"我什么时候说的"
    assert normalized[0]["raw_time_phrase"] == "那会儿"


def test_blank_or_low_confidence_facts_are_dropped():
    assert facts.normalize([
        {"subject": "", "predicate": "止损", "object": "5"},
        {"subject": "user", "predicate": "", "object": "5"},
        {"subject": "user", "predicate": "止损", "object": ""},
        {"subject": "user", "predicate": "止损", "object": "5", "confidence": 0.1},
    ], now=NOW) == []


def test_unknown_fact_type_falls_back_to_observation():
    normalized = facts.normalize([{
        "subject": "user", "predicate": "止损", "object": "5", "fact_type": "很厉害的类型",
    }], now=NOW)
    assert normalized[0]["fact_type"] == "observation"


def test_same_key_within_a_session_keeps_the_latest_statement():
    """同一段对话里说了两次同一个键：保留最新说法，但置信度取较高者，避免自我强化。"""
    normalized = facts.normalize([
        {"subject": "user", "predicate": "止损", "object": "8", "confidence": 0.9},
        {"subject": "user", "predicate": "止损", "object": "5", "confidence": 0.5},
    ], now=NOW)

    assert len(normalized) == 1
    assert normalized[0]["object"] == "5"
    assert normalized[0]["confidence"] == 0.9


def test_different_subjects_are_different_keys():
    normalized = facts.normalize([
        {"subject": "user", "predicate": "止损", "object": "5"},
        {"subject": "600519", "predicate": "止损", "object": "8"},
    ], now=NOW)
    assert len(normalized) == 2


def test_batch_is_capped():
    many = [{"subject": "user", "predicate": f"p{i}", "object": "v"} for i in range(50)]
    assert len(facts.normalize(many, now=NOW)) == facts.MAX_FACTS_PER_SESSION


def test_non_list_input_is_tolerated():
    for garbage in (None, {}, "facts", 42):
        assert facts.normalize(garbage, now=NOW) == []


# ==================== 注入渲染 ====================

FACT = {
    "subject": "user",
    "predicate": "stop_loss_pct",
    "object": "5",
    "factType": "constraint",
    "confidence": 0.9,
    "confirmed": True,
    "trust": "high",
    "recordedAt": "2026-09-16T10:00:00",
    "previousValue": "-8",
    "previousRecordedAt": "2026-09-01T10:00:00",
}


def test_facts_are_rendered_with_change_history():
    """变更链必须展示：用户看到"5%（此前为 8%）"才知道系统没搞错。"""
    block = recall.render({"today": "2026-09-16", "facts": [FACT]})

    assert "长期设定" in block
    assert "- 止损：5（" in block
    assert "此前为 -8" in block
    assert "2026-09-16" in block


def test_facts_are_grouped_by_type():
    facts_payload = [
        FACT,
        {"subject": "600519", "predicate": "holding_cost", "object": "1500", "factType": "holding",
         "confidence": 0.9, "confirmed": True, "recordedAt": "2026-03-11T10:00:00"},
        {"subject": "user", "predicate": "target_return", "object": "年化10%", "factType": "goal",
         "confidence": 0.8, "confirmed": True, "recordedAt": "2026-08-02T10:00:00"},
    ]
    block = recall.render({"today": "2026-09-16", "facts": facts_payload})

    assert "长期设定" in block
    assert "持仓与关注" in block
    assert "目标与已做的决定" in block
    assert "600519 持仓成本：1500" in block


def test_low_confidence_unconfirmed_facts_are_marked_as_inference():
    inferred = dict(FACT, confirmed=False, confidence=0.4)
    block = recall.render({"today": "2026-09-16", "facts": [inferred]})
    assert "（推断）" in block


def test_confirmed_facts_are_not_marked_as_inference():
    block = recall.render({"today": "2026-09-16", "facts": [FACT]})
    assert "（推断）" not in block


def test_raw_time_phrase_and_data_as_of_are_surfaced():
    fact = dict(FACT, rawTimePhrase="去年", dataAsOf="2025-03-11T00:00:00")
    block = recall.render({"today": "2026-09-16", "facts": [fact]})

    assert '你当时说的是"去年"' in block
    assert "数据截至 2025-03-11" in block


def test_fact_lines_are_bounded():
    many = [dict(FACT, predicate=f"p{i}", object="值" * 300) for i in range(30)]
    block = recall.render({"today": "2026-09-16", "facts": many})

    assert len(block) <= recall.MEMORY_MAX_CHARS + len("\n…（记忆内容过长已截断）\n</memory>")
    assert block.count("- ") <= recall.MAX_FACTS


def test_facts_render_tolerates_garbage():
    for garbage in ([None, 42, "x"], "not-a-list", {"a": 1}):
        block = recall.render({"today": "2026-09-16", "facts": garbage})
        assert "</memory>" in block
