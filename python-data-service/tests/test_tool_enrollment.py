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
                             "get_indicators", "get_risk_metrics"}
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
