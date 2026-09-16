# -*- coding: utf-8 -*-
"""经验记忆的规范化，以及"工具结果入账"（经验的原料）。

经验 = 症状 → 试过什么 → 最后怎么做成的 → 可复用做法。
它会被注入到以后的对话里，所以两个边界必须钉死：
1. **没有解法的"经验"直接丢弃**（那只是一句抱怨）；
2. **task_type 必须收敛成同一个键**，否则同一个坑的复发次数统计不出来，
   而复发次数正是"值不值得升格成 skill"的判据。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent.react_agent as ra
from agent import lessons, memory_store, tool_contract as tc


# ==================== task_type 收敛 ====================

def test_task_type_aliases_collapse_to_canonical_keys():
    assert lessons.canonical_task_type("生成策略") == "generate_strategy"
    assert lessons.canonical_task_type("回测") == "backtest"
    assert lessons.canonical_task_type("查行情") == "quote_lookup"
    assert lessons.canonical_task_type("Backtest") == "backtest"
    assert lessons.canonical_task_type("") == "general"
    # 认不出来时保留规范化后的原文，而不是硬塞进某个体例
    assert lessons.canonical_task_type("Some New Task") == "some_new_task"


# ==================== 规范化 ====================

def test_lesson_is_normalized():
    normalized = lessons.normalize([{
        "task_type": "回测",
        "symptom": "回测提示历史数据不足",
        "attempts": ["原样重跑", "换参数"],
        "resolution": "先取更长周期的数据",
        "reusable_rule": "回测前确认历史条数≥20",
        "confidence": 0.8,
    }], source_event_ids=[11, 12])

    assert len(normalized) == 1
    lesson = normalized[0]
    assert lesson["task_type"] == "backtest"
    assert lesson["symptom"] == "回测提示历史数据不足"
    assert lesson["attempts"] == ["原样重跑", "换参数"]
    assert lesson["reusable_rule"] == "回测前确认历史条数≥20"
    assert lesson["provenance"] == "model"
    # 经验可能被污染，注入时永远只是"参考"
    assert lesson["trust"] == "medium"
    assert lesson["evidence_event_ids"] == [11, 12]


def test_lesson_without_a_resolution_is_dropped():
    """没有"怎么解决的"就不是经验，只是一句抱怨。"""
    assert lessons.normalize([{"task_type": "回测", "symptom": "回测很慢"}]) == []


def test_lesson_without_a_symptom_is_dropped():
    assert lessons.normalize([{"task_type": "回测", "resolution": "换数据源"}]) == []


def test_low_confidence_lesson_is_dropped():
    assert lessons.normalize([{
        "task_type": "回测", "symptom": "慢", "resolution": "换数据源", "confidence": 0.05,
    }]) == []


def test_same_symptom_appears_once_per_session():
    raw = [
        {"task_type": "回测", "symptom": "数据不足", "resolution": "换标的"},
        {"task_type": "backtest", "symptom": "数据不足", "resolution": "换周期"},
    ]
    normalized = lessons.normalize(raw)

    # 收敛成同一个键之后只剩一条（去重发生在写入之前，避免同一次巩固里自我重复）
    assert len(normalized) == 1


def test_reusable_rule_alone_is_enough():
    normalized = lessons.normalize([{
        "task_type": "回测", "symptom": "数据不足", "reusable_rule": "先检查条数",
    }])
    assert len(normalized) == 1
    assert normalized[0]["reusable_rule"] == "先检查条数"


def test_batch_is_capped():
    raw = [{"task_type": "backtest", "symptom": f"症状{i}", "resolution": "做法"}
           for i in range(20)]
    assert len(lessons.normalize(raw)) == lessons.MAX_LESSONS_PER_SESSION


def test_garbage_input_is_tolerated():
    for garbage in (None, {}, "lessons", 42):
        assert lessons.normalize(garbage) == []


# ==================== 工具结果入账 ====================

def test_failed_tool_becomes_a_ledger_entry_with_the_error_text():
    envelope = tc.fail(tc.INSUFFICIENT_DATA, "历史数据不足 20 条，无法回测")

    entry = ra._tool_log_entry("backtest_strategy", envelope)

    # 抽取经验的提示词要看到具体现象（"历史数据不足"），而不是笼统的"出错了"
    assert "backtest_strategy" in entry["content"]
    assert "历史数据不足" in entry["content"]
    assert entry["kind"] == "tool_result"
    assert entry["provenance"] == "tool"
    # 工具输出属于低可信内容，绝不能被当成用户说过的话
    assert entry["trust"] == "low"
    assert json.loads(entry["meta"])["ok"] is False


def test_successful_business_rejection_is_also_recorded():
    """业务层面的否定结论（valid=false）往往就是"症状"本身，也要记。"""
    entry = ra._tool_log_entry("backtest_strategy",
                               tc.ok({"valid": False, "error": "数据不足"}))

    assert "未通过" in entry["content"]
    assert "数据不足" in entry["content"]


def test_successful_tool_entry_is_terse():
    entry = ra._tool_log_entry("get_history", tc.ok({"count": 120}))

    assert "get_history" in entry["content"]
    assert "120" in entry["content"]
    assert json.loads(entry["meta"])["ok"] is True


def test_tool_log_is_written_through_the_store(monkeypatch):
    captured = {}

    def fake_append(user_id, session_key, events):
        captured.update(user_id=user_id, session_key=session_key, events=events)
        return {"saved": len(events)}

    monkeypatch.setattr(memory_store, "append_events", fake_append)

    ra._write_tool_log(7, "7:2026-09-16", [{"kind": "tool_result", "content": "x"}])

    assert captured["user_id"] == 7
    assert captured["session_key"] == "7:2026-09-16"
    assert captured["events"] == [{"kind": "tool_result", "content": "x"}]


def test_tool_log_write_failure_is_swallowed(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("java down")

    monkeypatch.setattr(memory_store, "append_events", boom)

    # 账本写不进去不影响任何事
    ra._write_tool_log(7, "7:2026-09-16", [{"kind": "tool_result", "content": "x"}])


def test_queue_skips_when_there_is_no_identity():
    calls = []
    original = ra._TOOL_LOG_EXECUTOR.submit
    ra._TOOL_LOG_EXECUTOR.submit = lambda *args, **kwargs: calls.append(args)
    try:
        ra._queue_tool_log(None, "1:2026-09-16", [{"a": 1}])
        ra._queue_tool_log(1, None, [{"a": 1}])
        ra._queue_tool_log(1, "1:2026-09-16", [])
        assert calls == []
    finally:
        ra._TOOL_LOG_EXECUTOR.submit = original
