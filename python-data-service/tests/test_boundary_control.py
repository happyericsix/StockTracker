# -*- coding: utf-8 -*-
"""边界收口（P4）：鉴权默认值、单一入口、脱敏、摘要不吸收外来内容。

这一组测试针对的是"看起来有、其实没有"的边界。它们的共同点是：
**不改变正常路径的行为**，只在异常条件下把漏洞堵上，
所以每一条都配一个"如果没做会怎样"的断言。
"""
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SERVICE_ROOT = Path(__file__).resolve().parents[1]


# ==================== P4a：内部鉴权 fail-closed ====================

def _client():
    from fastapi.testclient import TestClient
    import app as app_module

    return TestClient(app_module.app), app_module


def test_missing_internal_token_rejects_instead_of_allowing(monkeypatch):
    """未配置密钥时**拒绝**，而不是放行。

    改造前是 fail-open：`.env` 丢一行，`/agent/chat`、`/strategies/*`、
    以及能读回外部原文的 `/external/archive/{id}` 全部无鉴权。
    """
    client, app_module = _client()
    monkeypatch.setattr(app_module, "INTERNAL_API_TOKEN", "")

    response = client.post("/api/v1/agent/chat", json={"user_id": "u", "message": "hi"})

    assert response.status_code == 503
    assert "not configured" in response.text


def test_missing_internal_token_still_leaves_health_reachable(monkeypatch):
    """`/health` 必须保持可访问：否则运维连"为什么全 503"都看不到。"""
    client, app_module = _client()
    monkeypatch.setattr(app_module, "INTERNAL_API_TOKEN", "")

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded", "鉴权没配好是降级状态，不是 ok"
    assert body["internal_auth"] == {"configured": False, "mode": "fail_closed"}


def test_wrong_token_is_401_and_right_token_passes(monkeypatch):
    client, app_module = _client()
    monkeypatch.setattr(app_module, "INTERNAL_API_TOKEN", "s3cret")

    denied = client.post("/api/v1/agent/chat", json={"user_id": "u", "message": "hi"},
                         headers={"X-Internal-Token": "wrong"})
    assert denied.status_code == 401

    # 正确的 token 会走到业务逻辑（这里用空 message 触发它的早退，不真的调 LLM）
    allowed = client.post("/api/v1/agent/chat", json={"user_id": "u", "message": ""},
                          headers={"X-Internal-Token": "s3cret"})
    assert allowed.status_code == 200


def test_archive_endpoint_is_behind_the_same_gate(monkeypatch):
    """能读回外部原文的接口必须在同一道门后面（它比普通的读接口更敏感）。"""
    client, app_module = _client()
    monkeypatch.setattr(app_module, "INTERNAL_API_TOKEN", "")

    response = client.get("/api/v1/external/archive/get_news/deadbeefdeadbeef")

    assert response.status_code == 503


# ==================== P4b：工具层只有一个入口 ====================

def find_direct_tool_calls(source: str) -> list:
    """找出**直接调用** `execute_tool(...)` 的位置（也就是绕过 `tool_pipeline` 的入口）。

    <h3>为什么要用 tokenizer 而不是 grep</h3>
    第一版是逐行找字符串，结果它把文档里那句"改造前这里是直接调 `execute_tool(...)`"
    也判成了违规 —— 一个会惩罚写注释的守卫是坏的守卫（下一任维护者只会把注释删掉）。

    用 `tokenize` 之后规则变得很干净：**NAME 为 `execute_tool` 且紧跟 `(`** 才算"调用"。
    于是：
    - 字符串/注释里的提及不算（它们不是代码）；
    - `handler=tool_registry.execute_tool` 不算（是引用，不是调用）——
      这正是**允许**的那一种：声明交给管线，执行留给管线；
    - `def execute_tool(` 是定义，放行；
    - `tool_registry.execute_tool("get_quote", ...)` 就是违规。
    """
    import io
    import tokenize

    violations = []
    significant = (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                   tokenize.DEDENT, tokenize.COMMENT)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError) as exc:
        return [f"<无法解析：{exc}>"]

    for index, token in enumerate(tokens):
        if token.type != tokenize.NAME or token.string != "execute_tool":
            continue
        rest = [item for item in tokens[index + 1:] if item.type not in significant]
        if not rest or rest[0].string != "(" or rest[0].type != tokenize.OP:
            continue  # 只是引用（import / handler=...），不是调用
        before = [item for item in tokens[:index] if item.type not in significant]
        if before and before[-1].string == "def":
            continue  # 定义本身
        violations.append(f"{token.start[0]}: {token.line.strip()}")
    return violations


def test_the_guard_itself_catches_a_bypass():
    """守卫测试必须先能抓到"违规写法"，否则它只是个安慰剂。"""
    bad = 'return tool_registry.execute_tool("get_quote", {"symbol": code})\n'
    assert find_direct_tool_calls(bad), "直接调用必须被判定为违规"

    good = ('envelope = tool_pipeline.call_tool_bounded(\n'
            '    name, args, spec=spec, handler=tool_registry.execute_tool)\n')
    assert find_direct_tool_calls(good) == []
    assert find_direct_tool_calls("from agent.tool_registry import execute_tool\n") == []
    assert find_direct_tool_calls("def execute_tool(name, args):\n    return {}\n") == []
    # 文档里提到这个模式不该被判违规：否则守卫会逼着大家不写注释
    assert find_direct_tool_calls('"""改造前这里是直接调 `execute_tool(a, b)`。"""\n') == []


def test_no_production_module_bypasses_the_pipeline():
    """生产代码里不允许存在第二条工具调用路径（P4b 的核心断言）。

    它拦的不是今天的代码，而是**以后顺手写的那一行**：
    绕过策略层不会报错、不会失败，只会悄悄跳过授权、预算、门禁与计量。
    """
    targets = [SERVICE_ROOT / "app.py"] + sorted((SERVICE_ROOT / "agent").glob("*.py"))
    violations = {}
    for path in targets:
        found = find_direct_tool_calls(path.read_text(encoding="utf-8"))
        if found:
            violations[path.name] = found

    assert violations == {}, f"这些地方绕过了工具管线：{violations}"


def test_diagnostic_endpoint_goes_through_the_pipeline(monkeypatch):
    """诊断端点以前直调执行函数，现在必须走管线（授权/预算/门禁/计量都不能跳过）。"""
    from agent import tool_pipeline

    calls = []
    original = tool_pipeline.call_tool_bounded

    def spy(name, args, **kwargs):
        calls.append((name, kwargs.get("spec") is not None))
        return original(name, args, **kwargs)

    monkeypatch.setattr(tool_pipeline, "call_tool_bounded", spy)
    client, _app_module = _client()

    response = client.get("/api/v1/agent/diagnostic/600519",
                          headers={"X-Internal-Token": "test-internal-token"})

    assert response.status_code == 200
    assert [name for name, _has_spec in calls] == [
        "get_risk_metrics", "get_model_status", "get_model_consensus"]
    assert all(has_spec for _name, has_spec in calls), "走管线就必须带上声明（否则策略无处生效）"


def test_diagnostic_endpoint_survives_a_refusal(monkeypatch):
    """被拒绝时端点不能崩：契约是"裸负载"，拒绝就返回 None 字段 + 200。"""
    from agent import external_source

    registry = external_source.ExternalSourceRegistry(failure_threshold=1)
    monkeypatch.setattr(external_source, "REGISTRY", registry)
    registry.record_failure("eastmoney.news", "boom")
    client, _app_module = _client()

    response = client.get("/api/v1/agent/diagnostic/600519",
                          headers={"X-Internal-Token": "test-internal-token"})

    assert response.status_code == 200
    assert "risk" in response.json()


# ==================== P4c：脱敏真正生效 ====================

def test_redacted_args_never_reach_the_ledger():
    """`ToolSpec.redaction` 以前是装饰字段（声明了没人读），现在必须有唯一读取方。"""
    from agent import react_agent
    from agent.tool_spec import ToolRegistry, ToolSpec

    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="link_account", namespace="account", description="绑定资金账号",
        parameters={"type": "object", "properties": {"account": {"type": "string"}},
                    "required": ["account"]},
        handler=lambda name, args: {},
        scopes=("account:write",), redaction=("account",)))

    from agent import tool_registry

    original_get = tool_registry.get_spec
    tool_registry.get_spec = lambda name: registry.get(name) or original_get(name)
    try:
        import agent.react_agent as ra

        ra.get_spec = tool_registry.get_spec
        entry = ra._tool_log_entry("link_account", {"ok": True, "data": {}, "error": None,
                                                    "meta": {}},
                                   {"account": "6222-0000-1234", "note": "keep"})
    finally:
        tool_registry.get_spec = original_get
        ra.get_spec = original_get

    meta = json.loads(entry["meta"])
    assert meta["args"]["account"] == "***", "敏感字段必须被盖掉"
    assert meta["args"]["note"] == "keep", "非敏感字段不该被顺手删掉"
    assert "6222-0000-1234" not in entry["meta"]


def test_audit_args_are_bounded():
    """参数进审计要有上限：它是证据，不是结果，不需要全文。"""
    from agent import react_agent

    huge = {"strategy_json": {"name": "x" * 5000}}
    args = react_agent._audit_args("validate_strategy", huge)

    assert len(json.dumps(args, ensure_ascii=False)) <= react_agent.AUDIT_ARGS_MAX_CHARS + 40


def test_tool_log_entry_records_what_was_asked_for():
    """审计要能回答"它到底要了什么"，而不只是"调了哪个工具"。"""
    from agent import react_agent, tool_contract as tc

    entry = react_agent._tool_log_entry("get_quote", tc.ok({"quote": {}}), {"symbol": "600519"})
    meta = json.loads(entry["meta"])

    assert meta["args"] == {"symbol": "600519"}
    assert meta["provenance"] == "internal"


def test_audit_args_keep_scalar_types():
    """数字别变成字符串：审计要统计参数时，`"days": "30"` 会变成坑。"""
    from agent import react_agent

    args = react_agent._audit_args("get_history", {"symbol": "600519", "days": 30,
                                                   "nested": {"a": 1}})

    assert args["days"] == 30
    assert args["nested"] == '{"a": 1}'


# ==================== P4d：摘要不吸收用量与外部内容 ====================

def test_consolidation_skips_usage_and_external_events():
    """摘要 → 事实/经验 → 长期记忆：这条链路上不能出现用量数字与第三方内容。"""
    from agent import consolidate

    events = [
        {"id": 1, "kind": "chat_user", "role": "user", "content": "茅台的止损我想改成 5%",
         "occurredAt": "2026-09-16T10:00:00", "provenance": "user"},
        {"id": 2, "kind": "usage", "role": "system", "content": "本轮用量：LLM 3 次/9000 tokens",
         "occurredAt": "2026-09-16T10:00:05", "provenance": "system"},
        {"id": 3, "kind": "tool_result", "role": "tool", "content": "get_news -> 10 条",
         "occurredAt": "2026-09-16T10:00:06", "provenance": "external"},
        {"id": 4, "kind": "chat_bot", "role": "assistant", "content": "好的，已按 5% 记录",
         "occurredAt": "2026-09-16T10:00:07", "provenance": "model"},
    ]

    dialog, used_ids, _timestamps = consolidate._build_dialog(events)

    assert "止损我想改成 5%" in dialog
    assert "已按 5% 记录" in dialog
    assert "本轮用量" not in dialog, "用量是我们自己的计量数字，不是对话内容"
    assert "get_news" not in dialog, "外部来源的事件属于数据，不能进摘要"
    assert used_ids == [1, 4]


def test_consolidation_still_keeps_tool_failures_from_internal_tools():
    """别把"过滤外部"做过头：内部工具的结果是经验抽取的重要原料，必须留下。"""
    from agent import consolidate

    events = [
        {"id": 1, "kind": "tool_result", "role": "tool",
         "content": "backtest_strategy 失败：历史数据不足 20 条",
         "occurredAt": "2026-09-16T10:00:00", "provenance": "tool"},
    ]

    dialog, used_ids, _ = consolidate._build_dialog(events)

    assert "历史数据不足" in dialog
    assert used_ids == [1]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
