# -*- coding: utf-8 -*-
"""策略规范名（ACCOUNT-GATE Part A，2026-11-04）：名字是配置的**输出**，不是宣传语。

<h3>为什么需要确定性命名</h3>
真实事故：策略名叫"中国银行-价格上穿**60日线**趋势跟随"，实际配置是
`price_cross_ma window=2`——名字是 LLM 起的（提示词对命名零约束），参数是另写的。
四个写入口（LLM 生成/手动创建/编辑/revision 审批保留旧名）没有一个校验一致性，
名字于是成了广告位。规范名从配置**确定性**生成：改任何参数，名字跟着变；
名字与参数的错配从此在保存那一刻就不可能存在。

<h3>与 _describe_conditions 的关系</h3>
`react_agent._describe_conditions` 是"给人看的条件描述"（含"未配置"兜底），本模块的
`describe_conditions` 是它的规范化版本：补齐了 bias_pct / volume_ratio / pe_percentile
三种此前落到 else 只输出类型名的条件，并统一数字格式（-5.0 → -5）。react_agent
侧改为委托调用，两处描述从此不会漂移。
"""
from __future__ import annotations

from typing import Any

MAX_NAME_CHARS = 100


def _num(value: Any) -> str:
    """数字的人读格式：-5.0 → -5，7.5 → 7.5；解析不了原样返回。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return str(int(f))
    return f"{f:g}"


def describe_condition(cond: dict) -> str:
    """单个条件的规范中文描述（全类型覆盖；不认识的原样返回类型名）。"""
    ctype = str((cond or {}).get("type", "") or "")
    value = (cond or {}).get("value")
    window = (cond or {}).get("window")
    direction = (cond or {}).get("direction")

    if ctype == "ma_cross":
        arrow = "上穿" if direction == "above" else "下穿"
        return f"MA{_num(cond.get('fast'))}{arrow}MA{_num(cond.get('slow'))}"
    if ctype == "price_cross_ma":
        arrow = "上穿" if direction == "above" else "下穿"
        return f"价格{arrow}MA{_num(window)}"
    if ctype in ("price_above_ma", "price_below_ma"):
        rel = "高于" if ctype == "price_above_ma" else "低于"
        return f"价格{rel}MA{_num(window)}"
    if ctype == "macd_cross":
        return "MACD金叉" if direction == "above" else "MACD死叉"
    if ctype == "rsi_above":
        return f"RSI≥{_num(value)}"
    if ctype == "rsi_below":
        return f"RSI≤{_num(value)}"
    if ctype == "price_above":
        return f"价格≥{_num(value)}"
    if ctype == "price_below":
        return f"价格≤{_num(value)}"
    if ctype == "bias_pct":
        # 乖离率过滤（防追高）：(现价-均线)/均线×100 ≤ 阈值
        return f"乖离MA{_num(window)}≤{_num(value)}%"
    if ctype == "volume_ratio":
        rel = "放量" if direction == "above" else "缩量"
        return f"{rel}{_num(value)}倍"
    if ctype == "pe_percentile":
        rel = "≥" if direction == "above" else "≤"
        return f"PE分位({_num(window)}日){rel}{_num(value)}"
    if ctype == "stop_loss_pct":
        return f"止损{_num(value)}%"
    if ctype == "take_profit_pct":
        return f"止盈{_num(value)}%"
    if ctype == "trailing_stop_pct":
        return f"移动止损{_num(value)}%"
    return ctype or "未知条件"


def describe_conditions(conditions, logic: str = "all") -> str:
    """条件列表的规范描述；logic=all 用"且"、any 用"或"连接（与策略 DSL 同义）。"""
    conds = [c for c in (conditions or []) if isinstance(c, dict)]
    if not conds:
        return "未配置"
    joiner = "且" if str(logic or "all") == "all" else "或"
    return joiner.join(describe_condition(c) for c in conds)


def _position_desc(position: dict) -> str:
    ptype = str((position or {}).get("type") or "")
    size = (position or {}).get("size_pct")
    try:
        size_value = float(size) if size is not None else None
    except (TypeError, ValueError):
        size_value = None
    if ptype == "full" or (size_value is not None and size_value == 100):
        return "全仓"
    if size_value is not None:
        return f"仓位{_num(size_value)}%"
    return "仓位未声明"


def canonical_name(config: dict) -> str:
    """从策略配置确定性生成规范名。

    形如 ``601988·价格上穿MA2→价格下穿MA10或止损-5%·全仓``——参数口径一眼可见，
    "60日线趋势跟随"这种与参数无关的宣传语没有藏身之处。截断到 100 字符。
    """
    cfg = config if isinstance(config, dict) else {}
    symbol = str(cfg.get("symbol") or "").strip() or "策略"
    entry = cfg.get("entry") or {}
    exit_rule = cfg.get("exit") or {}
    entry_desc = describe_conditions(entry.get("conditions"), entry.get("logic"))
    exit_desc = describe_conditions(exit_rule.get("conditions"),
                                    "any" if not exit_rule.get("logic") else exit_rule.get("logic"))
    position_desc = _position_desc(cfg.get("position"))
    name = f"{symbol}·{entry_desc}→{exit_desc}·{position_desc}"
    return name[:MAX_NAME_CHARS]
