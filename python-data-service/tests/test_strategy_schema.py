import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.strategy_schema import (
    MAX_CONDITIONS_PER_GROUP,
    extract_strategy_json,
    strategy_complexity,
    validate_strategy_config,
)

VALID = {
    "schema_version": "1.0",
    "name": "MA cross",
    "symbol": "600519",
    "initial_capital": 100000,
    "data": {"period": "day", "lookback_days": 250},
    "position": {"type": "percent", "size_pct": 100},
    "entry": {"logic": "all", "conditions": [
        {"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}
    ]},
    "exit": {"logic": "any", "conditions": [
        {"type": "ma_cross", "fast": 20, "slow": 60, "direction": "below"},
        {"type": "stop_loss_pct", "value": -8},
        {"type": "take_profit_pct", "value": 15}
    ]},
    "risk": {"commission_pct": 0.1, "slippage_pct": 0.1},
}

def test_valid_strategy():
    cfg, err = validate_strategy_config(VALID)
    assert err is None and cfg.name == "MA cross"

def test_unknown_condition_rejected():
    bad = {**VALID, "entry": {"logic": "all", "conditions": [{"type": "vibe"}]}}
    cfg, err = validate_strategy_config(bad)
    assert cfg is None and err

def test_ma_cross_requires_fast_slow_direction():
    bad = {**VALID, "entry": {"logic": "all", "conditions": [{"type": "ma_cross", "fast": 20}]}}
    cfg, err = validate_strategy_config(bad)
    # 消息是给最终用户看的（策略编辑面板直接展示），必须是中文人话
    assert cfg is None and "均线交叉" in err and "慢线" in err

def test_extract_json_from_fence():
    text = 'Here:\n```json\n{"schema_version":"1.0"}\n```'
    assert extract_strategy_json(text) == {"schema_version": "1.0"}


# ==================== 价格与均线的关系（补 DSL 缺口） ====================


def _with_conditions(conditions):
    return {**VALID, "entry": {"logic": "all", "conditions": conditions}}


def test_price_cross_ma_accepted():
    """用户说"上穿 60 日线买入""跌破 60 日线卖出"，在补上这个类型之前 DSL 无法表达。"""
    cfg, err = validate_strategy_config(_with_conditions(
        [{"type": "price_cross_ma", "window": 60, "direction": "above"}]))
    assert err is None and cfg.entry.conditions[0].window == 60


def test_price_ma_state_conditions_accepted():
    for condition_type in ("price_above_ma", "price_below_ma"):
        cfg, err = validate_strategy_config(_with_conditions(
            [{"type": condition_type, "window": 20}]))
        assert err is None, (condition_type, err)


def test_price_cross_ma_requires_window_and_direction():
    cfg, err = validate_strategy_config(_with_conditions(
        [{"type": "price_cross_ma", "window": 60}]))
    assert cfg is None and "价格穿越均线" in err and "方向" in err

    cfg, err = validate_strategy_config(_with_conditions(
        [{"type": "price_cross_ma", "direction": "below"}]))
    assert cfg is None and "价格穿越均线" in err and "均线天数" in err


def test_price_ma_requires_window():
    cfg, err = validate_strategy_config(_with_conditions([{"type": "price_below_ma"}]))
    assert cfg is None and "均线天数" in err


def test_price_ma_window_must_be_at_least_two():
    """窗口为 1 时"价格与均线的关系"恒等于价格与自身，没有意义。"""
    cfg, err = validate_strategy_config(_with_conditions(
        [{"type": "price_below_ma", "window": 1}]))
    assert cfg is None and "至少为 2" in err

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)


# ==================== bias_pct / volume_ratio（2026-10-13，委员会自己点的菜） ====================
# 这两个条件是回放里委员会两次试图发明、被 schema 拦下的——词表现在补齐，
# 用例钉住参数校验的中文报错（口径④：错误消息给最终用户看）。

def _cfg_with_entry(condition):
    return {"schema_version": "1.0", "name": "t", "symbol": "600519",
            "entry": {"logic": "all", "conditions": [condition]},
            "exit": {"logic": "any", "conditions": [
                {"type": "stop_loss_pct", "value": -8}]}}


def test_bias_pct_accepts_and_rejects():
    cfg, err = validate_strategy_config(
        _cfg_with_entry({"type": "bias_pct", "window": 60, "value": 5}))
    assert err is None and cfg is not None

    _, err = validate_strategy_config(
        _cfg_with_entry({"type": "bias_pct", "window": 60}))
    assert err and "乖离率" in err

    _, err = validate_strategy_config(
        _cfg_with_entry({"type": "bias_pct", "window": 60, "value": -1}))
    assert err and "非负" in err

    _, err = validate_strategy_config(
        _cfg_with_entry({"type": "bias_pct", "window": 1, "value": 5}))
    assert err and "至少为 2" in err


def test_volume_ratio_accepts_and_rejects():
    cfg, err = validate_strategy_config(
        _cfg_with_entry({"type": "volume_ratio", "window": 20,
                         "value": 1.2, "direction": "above"}))
    assert err is None and cfg is not None

    _, err = validate_strategy_config(
        _cfg_with_entry({"type": "volume_ratio", "window": 20, "value": 1.2}))
    assert err and "方向" in err

    _, err = validate_strategy_config(
        _cfg_with_entry({"type": "volume_ratio", "window": 20,
                         "value": 0, "direction": "above"}))
    assert err and "正数" in err


# ==================== 条件数上限与复杂度计数（2026-10-16，奥卡姆剃刀落地） ====================


def test_condition_count_capped():
    """每组条件的硬边界：超过上限直接拒，报错必须是中文人话（会透传到编辑面板）。"""
    conds = [{"type": "price_above_ma", "window": 20}] * (MAX_CONDITIONS_PER_GROUP + 1)
    cfg, err = validate_strategy_config(_with_conditions(conds))
    assert cfg is None
    assert "上限" in err and str(MAX_CONDITIONS_PER_GROUP) in err


def test_condition_count_at_cap_accepted():
    """恰好在上限内的合法配置不受影响。"""
    conds = [{"type": "price_above_ma", "window": 20}] * MAX_CONDITIONS_PER_GROUP
    cfg, err = validate_strategy_config(_with_conditions(conds))
    assert err is None and len(cfg.entry.conditions) == MAX_CONDITIONS_PER_GROUP


def test_strategy_complexity_counts_entry_and_exit():
    # VALID：entry 1 条 + exit 3 条
    assert strategy_complexity(VALID) == 4


def test_strategy_complexity_is_defensive():
    """喂原始 payload（未过校验）也不许抛异常：读不懂的部分按 0 条计。"""
    assert strategy_complexity({}) == 0
    assert strategy_complexity(None) == 0
    assert strategy_complexity({"entry": "坏结构", "exit": None}) == 0
    mixed = {"entry": {"conditions": ["不是字典", {"type": "rsi_above", "value": 70}]}}
    assert strategy_complexity(mixed) == 1


# ==================== 估值分位条件（2026-10-16，pe_percentile） ====================


def _pe_cond(**over):
    cond = {"type": "pe_percentile", "window": 250, "direction": "below", "value": 30}
    cond.update(over)
    return cond


def test_pe_percentile_accepted():
    cfg, err = validate_strategy_config(_with_conditions([_pe_cond()]))
    assert err is None and cfg.entry.conditions[0].window == 250


def test_pe_percentile_needs_all_three_params():
    for broken in (_pe_cond(direction=None), _pe_cond(value=None), _pe_cond(window=None)):
        cfg, err = validate_strategy_config(_with_conditions([broken]))
        assert cfg is None and ("估值分位" in err or "阈值" in err or "窗口" in err), err


def test_pe_percentile_bounds():
    # 阈值越界 / 窗口太短（分位变噪声）/ 窗口太长
    for bad in (_pe_cond(value=130), _pe_cond(window=60), _pe_cond(window=99999)):
        cfg, err = validate_strategy_config(_with_conditions([bad]))
        assert cfg is None and err
