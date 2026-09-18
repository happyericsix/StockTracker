"""工具集与装填策略（T1）。

<h3>为什么装填的第一原则是"宁可多给"</h3>
P0 里我们刚删掉一个关键词路由（`_looks_like_strategy_request`）：它把"今天要不要卖出茅台"
判成建策略请求，最后回一句"没理解，请换个说法描述你的策略"。
在**装填**这件事上，同样的错误会更贵：隐藏工具 = 模型直接失去能力，
而且它不会报错，只会换一条更差的路走（或者干脆编一个答案）。

所以：
- **默认全给**（与改造前行为一致，零风险）；
- 只有在**明确的模式**（调用方声明）或**权限**（scopes）约束下才收窄；
- 永远保留 `core` 集（查行情 + 查记忆是底座，任何模式都该有）；
- 收窄的依据必须是**声明**（scope / 模式名），不是对用户原话的关键词猜测。

<h3>为什么值得做（虽然现在只有 11 个工具）</h3>
一是**权限即可见性**：没有 `strategy:compute` 的用户不该看到策略工具，
现在他看得到、调了才被 `policy_denied` —— 既浪费上下文，又给模型制造了一次注定失败的尝试。
二是**规模准备**：工具定义本身是上下文成本，多 MCP 场景下能吃掉上万 token；
`namespace` + 装填规则现在加几乎零成本，等 40 个工具再补就要动所有调用点。
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# 工具集：按"能力域"分组。core 是任何模式都保留的底座。
TOOL_SETS = {
    "core": ("get_quote", "get_history", "memory_search", "get_news"),
    "market": ("search_stock", "get_quote", "get_quotes", "get_history",
               "get_indicators", "get_risk_metrics"),
    "strategy": ("validate_strategy", "backtest_strategy", "backtest_matrix", "finalize_strategy"),
    "memory": ("memory_search",),
    "model": ("get_model_status", "get_model_consensus"),
    # 外部内容型数据源（T2a）：单独成集，因为它们是唯一"内容不可信"的一类
    "news": ("get_news",),
    "fundamental": ("get_financial_abstract",),
}

# 模式 → 装填哪些集（core 自动并入，不必重复写）
MODES = {
    # 闲聊/问答：底座（行情 + 记忆 + 快讯）。快讯进 core 是刻意的：
    # "今天有什么消息"是典型的闲聊式提问，藏掉它模型只会凭训练数据编。
    "chat": ("core",),
    # 策略工作流：底座 + 行情 + 策略（含矩阵验证）+ 模型诊断（回测后常要解释模型参考）+ 基本面
    # `backtest_matrix` 刻意留在 strategy 集里：样本外验证不是"高级功能"，
    # 而是"这条规则到底行不行"的唯一答法。
    "strategy": ("core", "market", "strategy", "model", "fundamental"),
    # 行情/分析：底座 + 行情 + 基本面（不含策略工具）
    "market": ("core", "market", "fundamental"),
    # 内部诊断端点等显式场景
    "diagnostic": ("market", "model"),
    # None = 全给（默认，行为与改造前一致）
}


def sets_for(*names: str) -> set:
    """把若干工具集名展开成工具名集合（忽略未知集名）。"""
    picked: set = set()
    for name in names:
        picked.update(TOOL_SETS.get(name, ()))
    return picked


def visible_to(ctx) -> set:
    """权限可见性：调用者 scopes 能覆盖其声明 scopes 的工具。

    这是"套餐/权限决定工具可见性"，而不是让模型看见一堆它注定调不成的工具。
    """
    scopes = getattr(ctx, "scopes", frozenset()) or frozenset()
    return {spec.name for spec in _specs() if set(spec.scopes or ()) <= set(scopes)}


def available_now() -> set:
    """**上游还活着**的工具名（T2a）。

    外部源判定不可用时把它对应的工具摘掉：模型看不到一个取不到数的工具，
    比看到之后调用失败要好 —— 后者它还会重试，而重试的是同一个已经停用的源。
    摘除是**可逆**的（源恢复后自动回来），因为它依据的是运行时健康状态，不是静态声明。
    """
    try:
        from agent import external_source
    except Exception:  # noqa: BLE001 —— 摘除机制出问题就按"全都可用"处理（宁可多给）
        return set()
    down = set()
    for spec in _specs():
        source = getattr(spec, "source", None)
        if source and not external_source.available(source):
            down.add(spec.name)
    return down


def enroll(ctx, mode: Optional[str] = None) -> list:
    """算出这一轮该给模型看哪些工具（保持声明顺序）。

    - `mode=None`（默认）→ 权限可见的全部，**与改造前行为一致**；
    - `mode="chat"/"strategy"/...` → 该模式声明的集 ∪ core，再与权限可见性取交集；
    - **未知模式 → 全给**。这一点是刻意的：未知模式通常是调用方拼错了名字，
      这时候"只给 core"会悄悄收走 8 个工具，而模型不会报警、只会绕路。
      宁可多给，并且留一条日志让拼错可见。
    - 无论哪种模式，外部源已判定不可用的工具都会被摘除（T2a）。
    """
    visible = visible_to(ctx)
    down = available_now()
    if down:
        logger.info("外部源不可用，本轮摘除工具：%s", sorted(down))
    visible = visible - down

    if not mode:
        return _ordered(visible)
    if mode not in MODES:
        logger.warning("未知工具装填模式 %r，本次全给（不猜）", mode)
        return _ordered(visible)

    wanted = sets_for(*MODES[mode]) | sets_for("core")
    narrowed = visible & wanted
    # 万一声明把一切都过滤掉了，退回全给：宁可多给，也不能让模型手里没工具
    return _ordered(narrowed or visible)


def definition_cost(names: Iterable[str]) -> dict:
    """工具定义的上下文成本（字符数与粗略 token 估算）。

    暴露它是因为"工具变多"的代价是**看不见的**：没人会在 CI 里发现工具定义已经占了几千 token。
    （粗估按 1 token ≈ 3 字符，中英混排的保守值，用于趋势观察而不是精确计费。）
    """
    import json

    total_chars = 0
    for spec in _specs():
        if spec.name in set(names):
            total_chars += len(json.dumps(spec.schema(), ensure_ascii=False))
    return {"tools": len(set(names)), "chars": total_chars,
            "approx_tokens": round(total_chars / 3)}


def _specs():
    from agent.tool_registry import REGISTRY

    return REGISTRY.specs()


def _ordered(names: set) -> list:
    return [spec.name for spec in _specs() if spec.name in names]


def describe() -> dict:
    """给 /health 与调试用：装了哪些集、有多少工具、定义成本多少、外部源状态如何。"""
    from agent.tool_registry import REGISTRY, DEFAULT_USER_SCOPES

    class _DefaultContext:
        scopes = DEFAULT_USER_SCOPES

    everything = enroll(_DefaultContext())
    down = available_now()
    return {
        "sets": {name: list(members) for name, members in TOOL_SETS.items()},
        "modes": {name: list(members) for name, members in MODES.items()},
        "total_tools": len(REGISTRY.names()),
        "default_enrolled": len(everything),
        "definition_cost": definition_cost(everything),
        # 外部源挂了会摘掉工具，所以"现在到底给了几个"必须和"声明了几个"分开说
        "unavailable_sources": sorted(down),
        "external_tools": sorted(
            spec.name for spec in REGISTRY.specs()
            if getattr(spec, "provenance", "") == "external"),
    }
