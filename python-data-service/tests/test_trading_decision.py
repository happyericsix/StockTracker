# -*- coding: utf-8 -*-
"""agent 决策的解析：**读不懂就不动**，而且每一次归一化都留痕。

<h3>这个文件在防什么</h3>
把多角色辩论的结果变成可落库的决策，是"agent 真的能在模拟盘上操作"这条路上最容易被低估的一环。
它的失败方式全都**不会报错**：

- 模型写着 "HOLD, size=0.3" —— 到底持不持仓？
- 规范行说 BUY、JSON 说 SELL —— 听谁的？
- JSON 坏了、输出里全是散文 —— 要不要"替它决定"？
- size 写了 1.5 或 -0.2 —— 夹到 1 还是当 0？

TradingAgents 的选择是"解析失败 → 默认 BUY/SELL 仓位 0.25"（等于替模型下单）。
我们反过来：**读不懂就 SKIP**，并留下 `unknown_*` 原因 ——
"模型没说清楚"绝不能变成一个真实仓位。这几条逐条钉住。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import execution_contract as ec  # noqa: E402
from agent.trading_decision import (  # noqa: E402
    decision_prompt_contract,
    parse_trade_decision,
    prompt_version,
)


def _text(line, payload_json=None, prose="辩论结束。"):
    parts = [prose]
    if payload_json is not None:
        parts.append(f"```json\n{payload_json}\n```")
    parts.append(line)
    return "\n".join(parts)


# ==================== 1. 正常路径 ====================

def test_buy_with_size_becomes_a_buy_decision():
    decision = parse_trade_decision(_text(
        "FINAL TRANSACTION PROPOSAL: **BUY**",
        '{"signal": "BUY", "size_fraction": 0.5, "confidence": 0.62, "rationale": "上穿 60 日线"}'))

    assert decision.decision == ec.DECISION_BUY
    assert decision.signal == "buy"
    assert decision.size_fraction == 0.5
    assert decision.confidence == 0.62
    assert decision.rationale == "上穿 60 日线"
    assert decision.skip_reason is None
    assert decision.warnings == ()


def test_sell_with_size_becomes_a_sell_decision():
    decision = parse_trade_decision(_text(
        "FINAL TRANSACTION PROPOSAL: **SELL**",
        '{"signal": "SELL", "size_fraction": 1.0}'))

    assert decision.decision == ec.DECISION_SELL
    assert decision.size_fraction == 1.0


def test_the_canonical_line_wins_over_the_json_signal():
    """规范行是最终结论；JSON 只补结构化字段。

    两者不一致时**必须留警告**：那意味着模型自己前后矛盾（辩论被最后一次发言带跑了），
    这件事本身要被看见，而不是被静默地按某一个解释掉。
    """
    decision = parse_trade_decision(_text(
        "FINAL TRANSACTION PROPOSAL: **BUY**",
        '{"signal": "SELL", "size_fraction": 0.3}'))

    assert decision.decision == ec.DECISION_BUY
    assert decision.size_fraction == 0.3
    assert any("不一致" in warning for warning in decision.warnings)


def test_the_canonical_line_alone_is_enough():
    """没有 JSON 也能落决策：方向在规范行里，仓位按 0 → skip（而不是随便给个仓位）。"""
    decision = parse_trade_decision(_text("FINAL TRANSACTION PROPOSAL: **BUY**", None))

    assert decision.signal == "buy"
    assert decision.decision == ec.DECISION_SKIP
    assert decision.size_fraction == 0.0
    assert any("size_fraction=0" in warning for warning in decision.warnings)


# ==================== 2. HOLD 与"看多不下注" ====================

def test_hold_is_a_skip_with_zero_size():
    decision = parse_trade_decision(_text(
        "FINAL TRANSACTION PROPOSAL: **HOLD**",
        '{"signal": "HOLD", "size_fraction": 0.0, "confidence": 0.55}'))

    assert decision.decision == ec.DECISION_SKIP
    assert decision.signal == "hold"
    assert decision.size_fraction == 0.0
    assert decision.skip_reason == ec.SKIP_RULE_NOT_MET


def test_hold_with_a_nonzero_size_is_forced_to_zero_and_warned():
    decision = parse_trade_decision(_text(
        "FINAL TRANSACTION PROPOSAL: **HOLD**",
        '{"signal": "HOLD", "size_fraction": 0.3}'))

    assert decision.size_fraction == 0.0
    assert decision.decision == ec.DECISION_SKIP
    assert any("强制为 0" in warning for warning in decision.warnings)


# ==================== 3. 解析失败：不替模型下单 ====================

def test_unparsable_output_skips_instead_of_guessing_a_trade():
    """这条是与 TradingAgents 的**刻意分歧**。

    它那边解析失败默认 BUY/SELL 仓位 0.25 —— 等于模型输出坏了就替它下单。
    我们读不懂就不动，并把原因记成 `agent_unparsable`（真实原因，可统计：
    频次高说明提示词或模型有问题，而 `unknown_*` 只会说"我们没实现这条路"）。
    """
    decision = parse_trade_decision("我觉得可以再观察观察，等量能配合。")

    assert decision.decision == ec.DECISION_SKIP
    assert decision.size_fraction == 0.0
    assert decision.skip_reason == ec.SKIP_AGENT_UNPARSABLE
    assert any("既没有规范行" in warning for warning in decision.warnings)


def test_a_truncated_json_block_is_reported_as_truncation_not_ignored():
    """有 `{` 没有 `}` 最常见的原因是输出被截断（撞 max_tokens）——
    静默当成"没有结构化字段"会把一个真实故障藏起来。"""
    decision = parse_trade_decision(_text(
        "FINAL TRANSACTION PROPOSAL: **SELL**", '{"signal": "SELL", "size_fraction":'))

    assert decision.decision == ec.DECISION_SKIP, "方向有、仓位读不到 → 不动"
    assert decision.signal == "sell"
    assert any("疑似被截断" in warning for warning in decision.warnings)


def test_broken_json_keeps_the_canonical_direction_but_warns():
    decision = parse_trade_decision(_text(
        "FINAL TRANSACTION PROPOSAL: **SELL**", '{signal: SELL, size_fraction: 0.3}'))

    assert decision.decision == ec.DECISION_SKIP, "方向有、仓位读不到 → 不动"
    assert decision.signal == "sell"
    assert any("JSON 块无法解析" in warning for warning in decision.warnings)


def test_an_unknown_json_signal_is_ignored_with_a_warning():
    decision = parse_trade_decision(_text(
        "FINAL TRANSACTION PROPOSAL: **BUY**", '{"signal": "STRONG_BUY", "size_fraction": 0.4}'))

    assert decision.decision == ec.DECISION_BUY
    assert any("不认识" in warning for warning in decision.warnings)


# ==================== 4. 数值边界：夹紧、NaN、非数字 ====================

def test_an_out_of_range_size_is_clamped_and_warned():
    high = parse_trade_decision(_text(
        "FINAL TRANSACTION PROPOSAL: **BUY**", '{"signal": "BUY", "size_fraction": 1.5}'))
    low = parse_trade_decision(_text(
        "FINAL TRANSACTION PROPOSAL: **BUY**", '{"signal": "BUY", "size_fraction": -0.2}'))

    assert high.size_fraction == 1.0 and any("越界" in w for w in high.warnings)
    assert low.decision == ec.DECISION_SKIP, "负数夹到 0 → 等价于不下注"


def test_non_numeric_size_is_zero_not_an_exception():
    decision = parse_trade_decision(_text(
        "FINAL TRANSACTION PROPOSAL: **BUY**", '{"signal": "BUY", "size_fraction": "半仓"}'))

    assert decision.decision == ec.DECISION_SKIP
    assert any("不是数字" in warning for warning in decision.warnings)


def test_an_invalid_confidence_is_dropped_but_the_decision_survives():
    decision = parse_trade_decision(_text(
        "FINAL TRANSACTION PROPOSAL: **BUY**",
        '{"signal": "BUY", "size_fraction": 0.2, "confidence": 3}'))

    assert decision.decision == ec.DECISION_BUY
    assert decision.confidence is None
    assert any("confidence" in warning for warning in decision.warnings)


# ==================== 5. 输入形状的鲁棒性 ====================

def test_none_and_empty_input_do_not_crash():
    for body in (None, "", "   "):
        decision = parse_trade_decision(body)
        assert decision.decision == ec.DECISION_SKIP, body


def test_the_canonical_line_tolerates_full_width_colon_and_extra_spaces():
    decision = parse_trade_decision("FINAL  TRANSACTION   PROPOSAL ：  **buy**\n"
                                    '```json\n{"size_fraction": 0.25}\n```')

    assert decision.decision == ec.DECISION_BUY
    assert decision.size_fraction == 0.25


def test_a_bare_json_object_without_fences_is_accepted():
    decision = parse_trade_decision('{"signal": "sell", "size_fraction": 0.4}')

    assert decision.decision == ec.DECISION_SELL


def test_every_decision_is_in_the_closed_set():
    """所有分支的产出都必须落在封闭集里 —— 否则结算侧会把它归一成 unknown_*。"""
    samples = [
        _text("FINAL TRANSACTION PROPOSAL: **BUY**", '{"size_fraction": 0.5}'),
        _text("FINAL TRANSACTION PROPOSAL: **SELL**", '{"size_fraction": 0.5}'),
        _text("FINAL TRANSACTION PROPOSAL: **HOLD**", '{"size_fraction": 0.0}'),
        "完全读不懂",
    ]
    for body in samples:
        decision = parse_trade_decision(body)
        assert decision.decision in ec.DECISIONS, body
        assert 0.0 <= decision.size_fraction <= 1.0
        if decision.decision == ec.DECISION_SKIP:
            assert decision.skip_reason in ec.SKIP_REASONS


# ==================== 6. 输出契约与版本 ====================

def test_the_prompt_contract_teaches_the_parser_s_own_format():
    """提示词里的格式必须就是解析器认的那个 —— 两处各写一份必然会漂。"""
    contract = decision_prompt_contract()

    assert "FINAL TRANSACTION PROPOSAL" in contract
    assert "size_fraction" in contract
    assert "HOLD 的 size_fraction 必须为 0" in contract
    # 与解析器同源：契约里描述的行，解析器真的认
    assert parse_trade_decision("FINAL TRANSACTION PROPOSAL: **BUY**\n"
                                '```json\n{"size_fraction": 0.1}\n```').decision == ec.DECISION_BUY


def test_the_prompt_version_is_stable_and_twelve_chars():
    assert prompt_version() == prompt_version()
    assert len(prompt_version()) == 12


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print("PASS", fn.__name__)
