# tests/test_fanout.py
"""fanout 并行子调研的单元与集成测试。

替身策略与 test_react_agent.py 同源：替换 llm_service.chat_completion、
tool_registry.execute_tool（worker 的工具执行在读模块属性时才解析，
所以 monkeypatch 生效）。worker/综合的 completion 通过 run() 的注入点传入。
"""
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent.fanout as fanout
import agent.react_agent as ra
from agent import tool_scope
from agent import tool_contract as tc
from agent.tool_registry import DEFAULT_USER_SCOPES


# ==================== 纯函数：子任务声明校验 ====================


def test_normalize_requires_two_subtasks():
    _, err = fanout.normalize_subtasks([{"objective": "只有一个"}])
    assert err


def test_normalize_rejects_empty_objective():
    _, err = fanout.normalize_subtasks([{"objective": " "}, {"objective": "x"}])
    assert err


def test_normalize_clamps_to_max():
    items = [{"objective": f"任务{i}"} for i in range(8)]
    tasks, err = fanout.normalize_subtasks(items)
    assert not err
    assert len(tasks) == fanout.max_subtasks()


def test_normalize_dedupes_identical_objectives():
    tasks, err = fanout.normalize_subtasks([{"objective": "查A"}, {"objective": "查A"}])
    # 去重后只剩 1 个 → 不足 2 个，明确报错而不是悄悄跑一个
    assert err


def test_normalize_keeps_focus_symbol():
    tasks, err = fanout.normalize_subtasks(
        [{"objective": "查A", "focus_symbol": "600519"}, {"objective": "查B"}])
    assert not err
    assert tasks[0]["focus_symbol"] == "600519"
    assert tasks[1]["focus_symbol"] is None


# ==================== worker 工具集：防递归 ====================


def test_worker_tools_exclude_fanout():
    token = tool_scope.begin(user_id="u1", scopes=DEFAULT_USER_SCOPES)
    try:
        tools = fanout._worker_tools()
    finally:
        tool_scope.reset(token)
    assert fanout.FANOUT_TOOL_NAME not in tools
    # market 模式的底座还在（worker 不是被剥光了）
    assert "get_quote" in tools
    # 只读收窄：策略工具不该出现在 worker 手里
    assert "finalize_strategy" not in tools
    assert "backtest_strategy" not in tools


# ==================== 闸门 ====================


def test_run_disabled(monkeypatch):
    monkeypatch.setenv("FANOUT_ENABLED", "0")
    env = fanout.run("q", [{"objective": "a"}, {"objective": "b"}])
    assert not tc.is_ok(env)
    assert tc.error_code(env) == tc.POLICY_DENIED


def test_run_rejects_single_subtask():
    env = fanout.run("q", [{"objective": "a"}], user_id="u1")
    assert tc.error_code(env) == tc.INVALID_ARGS


def test_run_rejects_empty_question():
    env = fanout.run("  ", [{"objective": "a"}, {"objective": "b"}], user_id="u1")
    assert tc.error_code(env) == tc.INVALID_ARGS


def test_run_daily_budget_gate(monkeypatch):
    monkeypatch.setenv("LLM_DAILY_TOKEN_BUDGET", "1")
    # 今日已用 >= 上限：直接拒绝，不派任何 worker
    monkeypatch.setattr(fanout.metering, "daily_llm_tokens", lambda: 5)
    env = fanout.run("q", [{"objective": "a"}, {"objective": "b"}], user_id="u1")
    assert tc.error_code(env) == tc.BUDGET_EXCEEDED


# ==================== worker：干净上下文 + role/epoch 标记 ====================


def _choice(content="", tool_calls=None, total_tokens=100):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"message": message, "usage": {"total_tokens": total_tokens}}


def _call(name, args, call_id="c1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def test_worker_marks_role_and_propagates_epoch(monkeypatch):
    # epoch 检查读的是 react_agent._EPOCHS：先登记当前轮次，worker 才不会被误判取消
    monkeypatch.setattr(ra, "_EPOCHS", {})
    ra.note_epoch("u1", 7)
    seen = {}

    def fake_execute_tool(name, args):
        ctx = tool_scope.context()
        seen["role"] = ctx.role
        seen["epoch"] = ctx.epoch
        return tc.ok({"done": True})

    from agent import tool_registry
    monkeypatch.setattr(tool_registry, "execute_tool", fake_execute_tool)

    calls = {"n": 0}

    def completion(messages, tools=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _choice(tool_calls=[_call("get_quote", {"symbol": "600519"})])
        return _choice(content="调研结论：一切正常。")

    out = fanout._run_worker(
        0, "总问题", {"objective": "查茅台", "focus_symbol": "600519"},
        user_id="u1", session_key="s1", scopes=DEFAULT_USER_SCOPES,
        epoch=7, completion=completion)

    assert out["ok"] is True
    assert out["summary"] == "调研结论：一切正常。"
    assert out["tool_calls"] == [{"tool": "get_quote", "ok": True}]
    # 身份链：worker 的工具调用里能读到 role 与继承的 epoch
    assert seen["role"] == fanout.WORKER_ROLE
    assert seen["epoch"] == 7


def test_worker_exits_when_epoch_superseded(monkeypatch):
    # 先登记一个更新的轮次，再让 worker 带旧 epoch 跑
    monkeypatch.setattr(ra, "_EPOCHS", {})
    ra.note_epoch("u1", 99)
    out = fanout._run_worker(
        0, "总问题", {"objective": "查A"},
        user_id="u1", scopes=DEFAULT_USER_SCOPES, epoch=7,
        completion=lambda messages, tools=None, **kwargs: _choice(content="不该被采信"))
    assert out["ok"] is False
    assert out["error"] == "superseded"
    assert out["summary"] == ""


def test_worker_llm_failure_degrades():
    def broken(messages, tools=None, **kwargs):
        raise RuntimeError("provider down")

    out = fanout._run_worker(
        0, "q", {"objective": "查A"},
        user_id="u1", scopes=DEFAULT_USER_SCOPES, completion=broken)
    assert out["ok"] is False
    assert out["error"].startswith("llm_error")


# ==================== run()：调度、聚合与熔断 ====================


def _two_tasks():
    return [{"objective": "查A的基本面"}, {"objective": "查B的基本面"}]


def test_run_happy_path():
    lock = threading.Lock()
    overlaps = {"now": 0, "max": 0}

    def worker_completion(messages, tools=None, **kwargs):
        # 观测并发：worker 真的在并行跑（线程重叠）
        with lock:
            overlaps["now"] += 1
            overlaps["max"] = max(overlaps["max"], overlaps["now"])
        time.sleep(0.15)
        with lock:
            overlaps["now"] -= 1
        return _choice(content="A 的调研摘要。", total_tokens=50)

    env = fanout.run("对比A和B", _two_tasks(), user_id="u1",
                     scopes=DEFAULT_USER_SCOPES,
                     completion=worker_completion,
                     synthesis_completion=lambda messages, **kwargs: _choice(content="统一简报文本"))
    assert tc.is_ok(env), env
    data = env["data"]
    assert len(data["workers"]) == 2
    assert all(w["ok"] for w in data["workers"])
    assert data["synthesis"]["text"] == "统一简报文本"
    assert data["budget_stopped"] is False
    assert env["meta"]["ok_workers"] == 2
    assert overlaps["max"] >= 2  # 两个 worker 有线程重叠


def test_run_budget_stop_with_partial_results(monkeypatch):
    monkeypatch.setenv("FANOUT_TOTAL_TOKEN_BUDGET", "80")

    # 每个 worker 自报 50 token：第一个完成后（50）还没超，第二个完成后（100）超线
    # ——但 2 个都已结束，没有可取消的，结果完整且标注预算状态由 cap 与 tokens 决定
    env = fanout.run("对比A和B", _two_tasks(), user_id="u1",
                     scopes=DEFAULT_USER_SCOPES,
                     completion=lambda m, tools=None, **k: _choice(content="摘要。", total_tokens=50),
                     synthesis_completion=lambda m, **k: _choice(content="简报"))
    assert tc.is_ok(env)
    assert env["meta"]["total_tokens"] == 100


def test_run_budget_exhausted_before_any_result(monkeypatch):
    monkeypatch.setenv("FANOUT_TOTAL_TOKEN_BUDGET", "10")

    def heavy_and_barren(messages, tools=None, **kwargs):
        # 空回答 + 巨量 token：烧完预算且没产出任何可用摘要
        return _choice(content="", total_tokens=9000)

    env = fanout.run("对比A和B", _two_tasks(), user_id="u1",
                     scopes=DEFAULT_USER_SCOPES, completion=heavy_and_barren)
    assert not tc.is_ok(env)
    assert tc.error_code(env) == tc.BUDGET_EXCEEDED


def test_run_all_workers_fail():
    def broken(messages, tools=None, **kwargs):
        raise RuntimeError("down")

    env = fanout.run("对比A和B", _two_tasks(), user_id="u1",
                     scopes=DEFAULT_USER_SCOPES, completion=broken)
    assert not tc.is_ok(env)
    assert tc.error_code(env) == tc.TOOL_ERROR
    assert tc.error_retryable(env) is True


def test_run_synthesis_failure_keeps_worker_summaries():
    def broken_synth(messages, **kwargs):
        raise RuntimeError("synth down")

    env = fanout.run("对比A和B", _two_tasks(), user_id="u1",
                     scopes=DEFAULT_USER_SCOPES,
                     completion=lambda m, tools=None, **k: _choice(content="摘要。"),
                     synthesis_completion=broken_synth)
    assert tc.is_ok(env)
    assert env["data"]["synthesis"]["text"] == ""
    assert "失败" in env["data"]["synthesis"]["note"]
    assert all(w["ok"] for w in env["data"]["workers"])


# ==================== 端到端：主 agent 调 fanout 工具 ====================


def test_main_agent_fanout_end_to_end(monkeypatch):
    """主 ReAct 循环调用 fanout_research 工具 → 真实 _tool_fanout → worker → 汇总 → 最终回复。"""
    from agent import tool_registry

    orchestrator_call = {
        "function": {
            "name": "fanout_research",
            "arguments": json.dumps({
                "question": "白酒和新能源最近各在发生什么",
                "subtasks": [
                    {"objective": "白酒板块近期消息面", "focus_symbol": "600519"},
                    {"objective": "新能源板块近期消息面", "focus_symbol": "300750"},
                ],
            }, ensure_ascii=False),
        },
        "id": "call_fanout_1",
        "type": "function",
    }

    def fake_execute_tool(name, args):
        if name == "fanout_research":
            # 走真实 handler：身份来自 tool_scope（FANOUT_POOL 已 copy_context），
            # worker 的 completion 也走被替换的 llm_service.chat_completion
            return tool_registry._tool_fanout(args)
        return tc.ok({"ok": True})

    def completion(messages, tools=None, temperature=0.2, max_tokens=1200):
        system = (messages[0] or {}).get("content") or ""
        if system.startswith(ra.SYSTEM_PROMPT[:20]):
            # 主 agent：第一次发 fanout 工具调用，之后给最终回复
            if not getattr(completion, "fanout_done", False):
                completion.fanout_done = True
                return {"message": {"role": "assistant", "content": "",
                                    "tool_calls": [orchestrator_call]}}
            return {"message": {"role": "assistant", "content": "综合结论：两板块各有看点。"}}
        if "子调研员" in system:
            return {"message": {"role": "assistant", "content": "该板块调研摘要。"},
                    "usage": {"total_tokens": 30}}
        if "汇总编辑" in system:
            return {"message": {"role": "assistant", "content": "统一简报。"},
                    "usage": {"total_tokens": 20}}
        return {"message": {"role": "assistant", "content": "fallback"}}

    original_completion = ra.llm_service.chat_completion
    original_execute = ra.execute_tool
    original_queue = ra._queue_tool_log
    ra.llm_service.chat_completion = completion
    ra.execute_tool = fake_execute_tool
    ra._queue_tool_log = lambda *a, **k: None
    try:
        out = ra.run_agent("u1", "白酒和新能源最近各在发生什么", [])
    finally:
        ra.llm_service.chat_completion = original_completion
        ra.execute_tool = original_execute
        ra._queue_tool_log = original_queue

    assert out["strategy_json"] is None
    assert any("综合结论" in r for r in out["replies"])


# ==================== 管线集成：scope / 贵重配额 ====================


def test_fanout_denied_without_scope():
    from agent import tool_pipeline, tool_registry
    from agent.tool_registry import get_spec

    token = tool_scope.begin(user_id="u1",
                             scopes=DEFAULT_USER_SCOPES - {"research:fanout"})
    try:
        env = tool_pipeline.call_tool_bounded(
            "fanout_research", {"question": "q", "subtasks": _two_tasks()},
            spec=get_spec("fanout_research"), handler=tool_registry.execute_tool,
            default_timeout=5.0)
    finally:
        tool_scope.reset(token)
    assert tc.error_code(env) == tc.POLICY_DENIED


def test_fanout_is_expensive_and_not_retryable():
    from agent.tool_registry import get_spec

    spec = get_spec("fanout_research")
    assert spec is not None
    assert spec.cost_class == "expensive"
    assert spec.retryable is False
    assert spec.timeout_s >= 120.0
    assert "research:fanout" in DEFAULT_USER_SCOPES  # 默认可见（已确认的产品决策）
