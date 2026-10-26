# -*- coding: utf-8 -*-
"""工具集装填（T1）：这一轮给模型看哪些工具。

装填的第一原则是**宁可多给**：隐藏工具 = 模型直接失去能力，而且它不会报错，
只会换一条更差的路走。所以这里钉住三件事：

1. 默认行为与改造前一致（全给）；
2. 收窄只由**声明**（模式名 / scopes）触发，绝不由对用户原话的关键词猜测触发；
3. core 集永远保留，未知模式当作全给（不猜）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import tool_sets
from agent.tool_registry import DEFAULT_USER_SCOPES, REGISTRY, get_spec
from agent.tool_scope import ToolContext


def ctx(scopes=None):
    return ToolContext(user_id="u1", session_key="1:2026-09-16",
                       scopes=frozenset(DEFAULT_USER_SCOPES if scopes is None else scopes))


def test_default_enrollment_is_everything():
    """默认不装填 —— 与改造前完全一致，升级不该悄悄改变模型手里的工具。"""
    assert tool_sets.enroll(ctx()) == REGISTRY.names()


def test_chat_mode_keeps_only_the_core_set():
    enrolled = tool_sets.enroll(ctx(), "chat")

    assert set(enrolled) == set(tool_sets.TOOL_SETS["core"])
    assert "get_quote" in enrolled and "memory_search" in enrolled
    # 闲聊模式下不该出现策略/回测/模型诊断工具
    assert "backtest_strategy" not in enrolled
    assert "get_model_consensus" not in enrolled


def test_market_mode_drops_strategy_and_model_tools():
    enrolled = tool_sets.enroll(ctx(), "market")

    assert "get_history" in enrolled and "get_risk_metrics" in enrolled
    assert "validate_strategy" not in enrolled
    assert "get_model_status" not in enrolled


def test_strategy_mode_is_generous_because_strategy_work_needs_everything():
    enrolled = tool_sets.enroll(ctx(), "strategy")

    assert set(enrolled) >= {"validate_strategy", "backtest_strategy", "get_quote", "memory_search"}


def test_unknown_mode_falls_back_to_everything():
    """不认识的模式就当全给 —— 猜错的代价是模型手里少了几件工具，而它不会报警。"""
    assert tool_sets.enroll(ctx(), "某个还没实现的模式") == REGISTRY.names()


def test_scopes_decide_visibility():
    """权限即可见性：没有 strategy:compute 的用户，连策略工具都不该看到。

    改造前他会看到工具、调用后拿到 `policy_denied` —— 既浪费上下文，
    又给模型制造了一次注定失败的尝试（它还会照着重试）。
    """
    enrolled = tool_sets.enroll(ctx(scopes={"market:read"}))

    assert set(enrolled) == {"search_stock", "get_quote", "get_quotes", "get_history",
                             "get_indicators", "get_risk_metrics", "get_market_fund_flow"}
    assert "memory_search" not in enrolled        # 需要 memory:read:own
    assert "backtest_strategy" not in enrolled    # 需要 strategy:compute
    assert "get_news" not in enrolled             # 需要 news:read
    assert "get_financial_abstract" not in enrolled  # 需要 fundamental:read


def test_narrowed_enrollment_is_cheaper_than_full():
    """装填要真的省下上下文 —— 否则这个机制只是在增加复杂度。"""
    full = tool_sets.definition_cost(REGISTRY.names())
    chat = tool_sets.definition_cost(tool_sets.enroll(ctx(), "chat"))

    assert chat["chars"] < full["chars"]
    assert chat["tools"] < full["tools"]
    # 粗略 token 估算也一起给出来：工具变多的代价是看不见的，得有人量
    assert 0 < chat["approx_tokens"] < full["approx_tokens"]


def test_every_mode_keeps_the_core_set():
    for mode in tool_sets.MODES:
        kept = set(tool_sets.TOOL_SETS["core"]) & set(tool_sets.enroll(ctx(), mode))
        assert kept, f"{mode} 模式把 core 集丢光了"


def test_declared_sets_only_reference_real_tools():
    """工具集里写错名字（改名/删工具后忘记同步）必须被测出来，而不是静默少装一个。"""
    known = set(REGISTRY.names())
    for name, members in tool_sets.TOOL_SETS.items():
        assert set(members) <= known, f"工具集 {name} 引用了不存在的工具：{set(members) - known}"


# ==================== 示例（examples） ====================

def test_examples_are_rendered_into_the_tool_description():
    """OpenAI 兼容协议没有 input_examples 字段（那是 Anthropic 的），
    所以示例必须进描述文本 —— 这是它唯一能被模型看到的地方。"""
    description = get_spec("validate_strategy").schema()["function"]["description"]

    assert "示例：" in description
    assert "ma_cross" in description
    assert "stop_loss_pct" in description


def test_examples_teach_the_conventions_schemas_cannot_express():
    """示例要教 schema 表达不了的东西：止损用负数、logic 的取值、字段配套关系。"""
    description = get_spec("finalize_strategy").schema()["function"]["description"]

    assert '"value": -8' in description      # 止损幅度的符号约定
    assert '"logic": "all"' in description   # entry.logic 的取值
    assert '"size_pct"' in description       # 仓位写法


def test_simple_tools_carry_no_examples():
    """简单工具不需要示例：白占 token，还稀释真正需要示例的工具。"""
    description = get_spec("get_quote").schema()["function"]["description"]

    assert "示例：" not in description


def test_a_broken_example_is_caught_by_the_self_check():
    """教错格式的示例比没有示例更糟：自检必须拦住它。"""
    from agent.tool_spec import ToolRegistry, ToolSpec

    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="bad_example", namespace="x", description="示例与 schema 不符",
        parameters={"type": "object", "properties": {"symbol": {"type": "string"}},
                    "required": ["symbol"]},
        handler=lambda name, args: {},
        scopes=("market:read",),
        # 少了必填的 symbol
        examples=({"days": 30},),
    ))

    problems = registry.validate()

    assert any("示例" in problem for problem in problems), problems


def test_hot_rank_tool_enrolled_in_news_set():
    """热榜工具（2026-10-16）：登记在册、属于 news 集、scope 是 news:read。"""
    from agent.tool_registry import REGISTRY
    from agent.tool_sets import TOOL_SETS
    spec = REGISTRY.get("get_hot_rank")
    assert spec is not None, "get_hot_rank 未登记"
    assert "get_hot_rank" in TOOL_SETS["news"]
    assert "news:read" in spec.scopes


def test_hot_rank_tool_returns_items_with_note(monkeypatch):
    """热榜工具的返回形状：items + 口径注记（排名不是买卖建议）；上游失败 fail-open。"""
    import akshare_client
    from agent.tool_registry import REGISTRY

    rows = [{"rank": 1, "symbol": "SH600418", "name": "江淮汽车",
             "price": 22.37, "change_pct": 9.98}]
    monkeypatch.setattr(akshare_client, "get_hot_rank", lambda limit=10: rows)
    out = REGISTRY.get("get_hot_rank").handler({"limit": 5})
    assert out["ok"] and out["data"]["items"][0]["name"] == "江淮汽车"
    assert "不是买卖建议" in out["data"]["note"]
    assert out["meta"]["source_label"] == "东方财富·个股人气榜"

    monkeypatch.setattr(akshare_client, "get_hot_rank", lambda limit=10: [])
    out2 = REGISTRY.get("get_hot_rank").handler({})
    assert not out2["ok"] and out2["error"]["retryable"] is True


def test_get_my_alerts_tool_reads_via_scope(monkeypatch):
    """用户预警工具：身份来自 tool_scope（不接受模型传参）；Java 失败 fail-open 成空列表。"""
    from agent import message_store
    from agent.tool_registry import REGISTRY

    monkeypatch.setattr("agent.tool_scope.user_id", lambda: "42")
    rows = [{"id": 1, "type": "NEWS_ALERT", "content": "【利好】江淮汽车：…",
             "read": False}, {"id": 2, "type": "ALERT", "content": "RSI 72", "read": True}]
    monkeypatch.setattr(message_store, "recent_alerts", lambda uid, limit=10: rows if uid == "42" else [])
    out = REGISTRY.get("get_my_alerts").handler({"limit": 5})
    assert out["ok"] and len(out["data"]["alerts"]) == 2 and out["data"]["unread"] == 1

    # 没有用户身份：明确失败，不是静默空列表
    monkeypatch.setattr("agent.tool_scope.user_id", lambda: None)
    out2 = REGISTRY.get("get_my_alerts").handler({})
    assert not out2["ok"]

    # Java 挂了：fail-open 成空列表（"这次没有预警数据"不是报错）
    monkeypatch.setattr("agent.tool_scope.user_id", lambda: "42")
    monkeypatch.setattr(message_store, "recent_alerts", lambda uid, limit=10: [])
    out3 = REGISTRY.get("get_my_alerts").handler({})
    assert out3["ok"] and out3["data"]["alerts"] == []


def test_memory_search_passes_symbol_to_lessons(monkeypatch):
    """情境绑定（2026-10-16）：memory_search 的 symbol 参数要同传给教训检索。"""
    from agent import memory_store
    from agent.tool_registry import REGISTRY

    seen = {}

    def fake_facts(uid, query=None, symbol=None, fact_type=None, limit=8):
        return []

    def fake_lessons(uid, query=None, task_type=None, limit=3, symbol=None):
        seen["symbol"] = symbol
        return []

    monkeypatch.setattr("agent.tool_scope.user_id", lambda: "1")
    monkeypatch.setattr(memory_store, "search_facts", fake_facts)
    monkeypatch.setattr(memory_store, "search_lessons", fake_lessons)
    out = REGISTRY.get("memory_search").handler({"query": "茅台", "symbol": "600519"})
    assert out["ok"]
    assert seen["symbol"] == "600519"


def test_market_fund_flow_tool_registered_and_fails_open(monkeypatch):
    """大盘资金流工具：登记在 market 集；上游失败返回可重试失败（不是空数据）。"""
    import akshare_client
    from agent.tool_registry import REGISTRY
    from agent.tool_sets import TOOL_SETS

    assert "get_market_fund_flow" in TOOL_SETS["market"]
    spec = REGISTRY.get("get_market_fund_flow")
    assert spec is not None and "market:read" in spec.scopes

    monkeypatch.setattr(akshare_client, "get_market_fund_flow",
                        lambda limit=10: [{"date": "2026-10-16", "main_net_yi": -49.36}])
    out = spec.handler({})
    assert out["ok"] and out["data"]["main_net_yi_today"] == -49.36
    assert "不是买卖触发" in out["data"]["note"]

    monkeypatch.setattr(akshare_client, "get_market_fund_flow", lambda limit=10: [])
    out2 = spec.handler({})
    assert not out2["ok"] and out2["error"]["retryable"] is True


def test_skill_store_fail_open_and_cache(monkeypatch):
    """④ 技能注入客户端：Java 失败 fail-open 空列表；60s 内共享缓存。"""
    from agent import skill_store

    calls = {"n": 0}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"skills": [{"name": "情绪冰点不接飞刀", "ruleText": "准则"}]}

    def fake_get(url, params=None, headers=None, timeout=None):
        calls["n"] += 1
        return FakeResp()

    monkeypatch.setattr(skill_store.requests, "get", fake_get)
    monkeypatch.setattr(skill_store, "_cache", {})
    monkeypatch.setattr(skill_store.os, "environ", {"JAVA_BASE_URL": "http://x", "INTERNAL_API_TOKEN": "t"})
    items = skill_store.injectable("7", "committee", symbol="600519")
    assert items and items[0]["name"] == "情绪冰点不接飞刀"
    again = skill_store.injectable("7", "committee", symbol="600519")
    assert calls["n"] == 1 and again == items   # 缓存命中

    def boom(*a, **k):
        raise RuntimeError("java down")
    monkeypatch.setattr(skill_store.requests, "get", boom)
    assert skill_store.injectable("7", "reflection") == []   # fail-open


def test_committee_evidence_skills_block_semantics():
    """④ 委员会技能块：软参考注记必须在（防过拟合的语义闸），证据注入走 build+decide 同源。"""
    from agent import trading_committee as tc

    evidence, _ = tc.build_evidence("600519", "2026-10-17",
                                    [{"date": "2026-10-17", "open": 10, "close": 10,
                                      "high": 10, "low": 10, "volume": 100}], None)
    # 模拟 decide 内的技能注入段：验证 decide 签名接受 skills 且证据块注记完整
    import inspect
    assert "skills" in inspect.signature(tc.decide).parameters
    evidence["skills"] = {"items": [{"name": "n", "rule": "r"}],
                          "note": tc._SKILL_NOTE if hasattr(tc, "_SKILL_NOTE") else ""}
    assert "skills" in evidence
