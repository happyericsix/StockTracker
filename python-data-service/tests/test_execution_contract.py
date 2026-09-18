# -*- coding: utf-8 -*-
"""执行契约（1/3）：常量封闭性、归一规则、快照形状、指纹与"能不能对比"。

<h3>这个文件在防什么</h3>
口径散落在代码里、隐式且不一致 —— 回测按**次日开盘**成交、模拟盘按**信号当根收盘**成交，
而没有任何地方记录"这条记录属于哪个口径"。于是两个数字从第一天起就不可比，
而"不可比"会被误读成"策略在实盘变差了"。

所以这里钉三件事：
1. **常量封闭**：枚举值是封闭集，未知值一律 `unknown_*`（不静默丢、也不自由发挥）；
2. **口径可记录**：每次结算/回测都带执行指纹，**只有指纹相同才允许对比**；
3. **快照不写死**：指标键由"策略实际用到了什么"推导，而不是固定那几个 MA ——
   以后加条件类型，快照与痕迹都不用改。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import execution_contract as ec  # noqa: E402


# ==================== 1. 常量封闭性 ====================


def _real_values():
    """真实的枚举值（不含 `unknown_` 前缀本身 —— 它是前缀，不是枚举值）。"""
    return (list(ec.DECISIONS) + list(ec.SETTLEMENT_KINDS) + list(ec.FILL_BASES)
            + list(ec.ADJUST_MODES) + list(ec.SKIP_REASONS))


def _every_value():
    return _real_values() + [ec.UNKNOWN_PREFIX]


def test_enum_sets_are_unique_and_ascii_snake_case():
    """枚举值必须唯一且是 ASCII 小写下划线：它们会落库、会被统计、会被跨语言比对。"""
    values = _every_value()
    assert len(values) == len(set(values)), "枚举值有重复"
    for value in values:
        assert value == value.lower() and value.isascii(), value
        assert all(ch.isalnum() or ch == "_" for ch in value), value


def test_unknown_prefix_does_not_collide_with_real_values():
    """`unknown_` 前缀不能与任何真实枚举值冲突，否则统计会把未知混进已知。"""
    for value in _real_values():
        assert not value.startswith(ec.UNKNOWN_PREFIX), value
    assert not ec.UNKNOWN_PREFIX.rstrip("_") in _real_values()


def test_skip_reasons_cover_the_paths_that_actually_exist():
    """这些是代码里真实存在的"没成交"路径（PaperTradingService 的 8 处静默 return + 引擎侧），
    每一个都必须有名字，否则"为什么没动"永远答不出来。"""
    for reason in (ec.SKIP_MARKET_CLOSED, ec.SKIP_SUSPENDED, ec.SKIP_NO_BAR,
                   ec.SKIP_DATA_UNAVAILABLE, ec.SKIP_LIMIT_BLOCKED, ec.SKIP_T1_BLOCKED,
                   ec.SKIP_EX_DIVIDEND_DAY, ec.SKIP_INSUFFICIENT_CASH_FOR_ONE_LOT,
                   ec.SKIP_INSUFFICIENT_CASH, ec.SKIP_INVALID_PRICE, ec.SKIP_RULE_NOT_MET):
        assert reason in ec.SKIP_REASONS


# ==================== 2. 归一规则 ====================


def test_known_values_pass_through():
    assert ec.normalize_skip_reason(ec.SKIP_T1_BLOCKED) == ec.SKIP_T1_BLOCKED
    assert ec.normalize_decision(" buy ") == ec.DECISION_BUY
    assert ec.normalize_settlement_kind("realtime") == ec.SETTLEMENT_REALTIME
    assert ec.normalize_adjust_mode("qfq") == ec.ADJUST_QFQ


def test_unknown_values_become_unknown_prefixed():
    """未知值**不许抛异常**（交付路径上一个枚举值不认识就中断，代价太大），
    但也**不许静默丢** —— 归一成 `unknown_*` 并告警，让未知永远可见。"""
    assert ec.normalize_skip_reason("something_new") == "unknown_something_new"
    assert ec.normalize_skip_reason(None) == "unknown_unspecified"
    assert ec.normalize_skip_reason("   ") == "unknown_unspecified"
    assert ec.normalize_decision("maybe") == "unknown_maybe"


def test_unknown_values_are_truncated_to_column_width():
    value = ec.normalize_skip_reason("x" * 100)
    assert len(value) == ec.MAX_ENUM_CHARS
    assert value.startswith(ec.UNKNOWN_PREFIX)


def test_is_known_skip_reason():
    assert ec.is_known_skip_reason(ec.SKIP_NO_BAR)
    assert not ec.is_known_skip_reason("unknown_no_bar")
    assert not ec.is_known_skip_reason(None)


# ==================== 3. 成交价口径映射 ====================


def test_fill_basis_mapping_is_the_single_source():
    assert ec.fill_basis_for(ec.SETTLEMENT_DAILY) == ec.FILL_CLOSE
    assert ec.fill_basis_for(ec.SETTLEMENT_REALTIME) == ec.FILL_REALTIME_LAST
    assert set(ec.FILL_BASIS_BY_SETTLEMENT) == set(ec.SETTLEMENT_KINDS), "每种结算类型都必须有口径"


def test_unknown_settlement_kind_does_not_default_to_close():
    """未登记的结算类型**不许默认取 close**：口径猜错会让"这条痕迹是哪个价"永远说不清。"""
    assert ec.fill_basis_for("hourly") == "unknown_hourly"


def test_next_open_is_declared_but_not_used_by_any_settlement_yet():
    """`next_open` 是回测的默认口径；模拟盘要用它必须走 P2 的"挂单 + 两阶段结算"。"""
    assert ec.FILL_NEXT_OPEN in ec.FILL_BASES
    assert ec.FILL_NEXT_OPEN not in ec.FILL_BASIS_BY_SETTLEMENT.values()


# ==================== 4. 快照：指标键由策略推导（不写死） ====================


def _config(entry, exit_):
    return {"entry": {"logic": "all", "conditions": entry},
            "exit": {"logic": "any", "conditions": exit_}}


def test_indicator_keys_follow_the_conditions_used():
    config = _config([{"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}],
                     [{"type": "stop_loss_pct", "value": -8}])
    assert ec.indicator_keys_for(config) == ["close", "ma_20", "ma_60"]


def test_indicator_keys_include_price_vs_ma_windows():
    config = _config([{"type": "price_cross_ma", "window": 120, "direction": "above"}], [])
    assert "ma_120" in ec.indicator_keys_for(config)


def test_indicator_keys_cover_rsi_and_macd():
    assert "rsi" in ec.indicator_keys_for(_config([{"type": "rsi_below", "value": 30}], []))
    keys = ec.indicator_keys_for(_config([{"type": "macd_cross", "direction": "above"}], []))
    assert {"macd_dif", "macd_dea"} <= set(keys)


def test_indicator_keys_tolerate_garbage():
    assert ec.indicator_keys_for(None) == ["close"]
    assert ec.indicator_keys_for("不是字典") == ["close"]
    assert ec.indicator_keys_for(_config([None, 42, {"type": "unknown_kind"}], [])) == ["close"]


def test_new_condition_type_only_needs_one_mapping_line():
    """这条是"不写死"的验收：加一个新条件类型时，只有 `indicator_keys_for` 需要改，
    快照与痕迹的形状不变（它们的键集合由这个函数推导）。"""
    config = _config([{"type": "bollinger_break", "window": 20, "direction": "above"}], [])
    # 目前不认识 → 至少不能崩，且仍然给出 close
    assert ec.indicator_keys_for(config) == ["close"]


# ==================== 5. 快照体 ====================


def _fingerprint(**overrides):
    base = dict(fill_basis=ec.FILL_CLOSE, adjust_mode=ec.ADJUST_NONE,
                money_policy_version=ec.MONEY.version, engine_version="deadbeef1234")
    base.update(overrides)
    return ec.ExecutionFingerprint(**base)


def test_snapshot_shape_is_frozen_and_versioned():
    snapshot = ec.build_snapshot(
        bar={"open": 1.0, "close": 2.0}, indicators={"ma_20": 1.5},
        params={"lookback_days": 250}, fingerprint=_fingerprint())
    assert tuple(snapshot) == ec.SNAPSHOT_KEYS
    assert snapshot["schema_version"] == ec.SNAPSHOT_SCHEMA_VERSION
    assert snapshot["bar"]["close"] == 2.0
    assert snapshot["indicators"] == {"ma_20": 1.5}
    assert snapshot["fingerprint"]["fill_basis"] == ec.FILL_CLOSE
    assert snapshot["extra"] == {}, "extra 必须总是存在（默认空字典），否则渲染层要判 None"


def test_snapshot_survives_missing_pieces():
    snapshot = ec.build_snapshot(bar=None, indicators=None, params=None,
                                fingerprint=_fingerprint(), extra=None)
    assert snapshot["bar"] == {} and snapshot["indicators"] == {} and snapshot["params"] == {}
    assert snapshot["extra"] == {}


# ==================== 6. 指纹与"能不能对比" ====================


def test_fingerprint_for_follows_the_settlement_kind():
    assert ec.fingerprint_for(ec.SETTLEMENT_DAILY).fill_basis == ec.FILL_CLOSE
    assert ec.fingerprint_for(ec.SETTLEMENT_REALTIME).fill_basis == ec.FILL_REALTIME_LAST
    assert ec.fingerprint_for(ec.SETTLEMENT_DAILY, adjust_mode=ec.ADJUST_QFQ).adjust_mode == ec.ADJUST_QFQ


def test_same_fingerprint_is_comparable():
    allowed, reason = ec.compare_allowed(_fingerprint(), _fingerprint())
    assert allowed and reason == ""


@pytest.mark.parametrize("field,value", [
    ("fill_basis", ec.FILL_NEXT_OPEN),
    ("adjust_mode", ec.ADJUST_QFQ),
    ("money_policy_version", 99),
    ("engine_version", "ffffffffffff"),
    ("decision_mode", ec.DECISION_MODE_AGENT),
])
def test_any_differing_field_blocks_comparison(field, value):
    """**核心规则**：口径不同就不可比 —— 宁可说"不可比"，也不硬凑一个差额。
    差额看起来像信息，实际是错误。"""
    allowed, reason = ec.compare_allowed(_fingerprint(), _fingerprint(**{field: value}))
    assert not allowed
    assert field in reason and "不可比" in reason


def test_rule_and_agent_results_are_structurally_incomparable():
    """这条是"把决策来源换掉"这件事的安全网。

    换了决策方式之后，净值曲线在**同一个账户上**会分成两段：规则段与 agent 段。
    把 decision_mode 放进指纹之后，这两段永远不会被当成同一条曲线来比较 ——
    否则报告会拿"规则跑的 3 个月"去对"agent 跑的 3 个月"，而读者看不出它们不是一回事。
    """
    rule = ec.fingerprint_for(ec.SETTLEMENT_DAILY, decision_mode=ec.DECISION_MODE_RULE)
    agent = ec.fingerprint_for(ec.SETTLEMENT_DAILY, decision_mode=ec.DECISION_MODE_AGENT)

    allowed, reason = ec.compare_allowed(rule, agent)
    assert not allowed
    assert "decision_mode" in reason
    assert rule.decision_mode == ec.DECISION_MODE_RULE, "默认必须是规则 —— 存量数据不会因为这次改动换口径"
    assert agent.as_dict()["decision_mode"] == ec.DECISION_MODE_AGENT


def test_an_unknown_decision_mode_is_normalised_not_guessed():
    """认不出的决策来源**不许**默认成 rule：那会让一份来路不明的结果看起来像规则跑的。"""
    fingerprint = ec.fingerprint_for(ec.SETTLEMENT_DAILY, decision_mode="half-human")
    assert fingerprint.decision_mode.startswith(ec.UNKNOWN_PREFIX)


def test_difference_lists_only_the_differing_fields():
    diff = _fingerprint().difference(_fingerprint(fill_basis=ec.FILL_REALTIME_LAST))
    assert set(diff) == {"fill_basis"}
    assert diff["fill_basis"] == {"this": ec.FILL_CLOSE, "other": ec.FILL_REALTIME_LAST}


def test_missing_fingerprint_blocks_comparison():
    allowed, reason = ec.compare_allowed(None, _fingerprint())
    assert not allowed and "缺少执行指纹" in reason


# ==================== 7. 引擎指纹由代码推导 ====================


def test_engine_version_is_derived_not_hand_maintained():
    version = ec.engine_version()
    assert len(version) == 12 and all(ch in "0123456789abcdef" for ch in version)


def test_engine_version_changes_when_a_contract_constant_changes(monkeypatch):
    """引擎或契约常量一改，历史痕迹的解释依据就变了 —— 把依据算成哈希，忘不了。"""
    before = ec.engine_version()
    monkeypatch.setattr(ec, "SKIP_REASONS", tuple(ec.SKIP_REASONS) + ("new_reason",))
    ec.reset_engine_version_cache()
    try:
        assert ec.engine_version() != before
    finally:
        ec.reset_engine_version_cache()


def test_describe_exposes_the_effective_contract():
    described = ec.describe()
    assert described["fill_basis_by_settlement"][ec.SETTLEMENT_DAILY] == ec.FILL_CLOSE
    assert described["skip_reasons"] == list(ec.SKIP_REASONS)
    assert described["money_policy"]["version"] == ec.MONEY.version
    assert described["engine_version"] == ec.engine_version()
    assert described["unknown_prefix"] == ec.UNKNOWN_PREFIX


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print("PASS", fn.__name__)
