# -*- coding: utf-8 -*-
"""策略规范名（ACCOUNT-GATE Part A）的单元测试。

核心断言对着真实事故写：名叫"60日线趋势跟随"实际 window=2 的策略，
规范名必须把 2 写在脸上——名实错配在生成那一刻就不可能存在。
"""
from agent.strategy_naming import canonical_name, describe_condition, describe_conditions


def cond(ctype, **kw):
    return {"type": ctype, **kw}


class TestDescribeCondition:
    def test_price_cross_ma(self):
        assert describe_condition(cond("price_cross_ma", direction="above", window=2)) == "价格上穿MA2"
        assert describe_condition(cond("price_cross_ma", direction="below", window=60)) == "价格下穿MA60"

    def test_ma_cross(self):
        assert describe_condition(cond("ma_cross", fast=5, slow=20, direction="above")) == "MA5上穿MA20"

    def test_stop_types_human_readable(self):
        # 此前输出 "stop_loss_pct -5.0"（类型名+原始数字）
        assert describe_condition(cond("stop_loss_pct", value=-5.0)) == "止损-5%"
        assert describe_condition(cond("trailing_stop_pct", value=8.0)) == "移动止损8%"
        assert describe_condition(cond("take_profit_pct", value=12.5)) == "止盈12.5%"

    def test_newly_added_types_covered(self):
        # react_agent._describe_conditions 旧实现里这三种只落 else 输出类型名
        assert describe_condition(cond("bias_pct", window=20, value=3)) == "乖离MA20≤3%"
        assert describe_condition(cond("volume_ratio", window=5, value=2.0, direction="above")) == "放量2倍"
        assert describe_condition(cond("pe_percentile", window=250, value=30, direction="below")) == "PE分位(250日)≤30"

    def test_unknown_passthrough(self):
        assert describe_condition(cond("mystery")) == "mystery"


class TestCanonicalName:
    def test_the_601988_incident(self):
        config = {
            "symbol": "601988",
            "name": "中国银行-价格上穿60日线趋势跟随",   # LLM 吹牛的名字
            "entry": {"logic": "all",
                      "conditions": [{"type": "price_cross_ma", "direction": "above", "window": 2}]},
            "exit": {"logic": "any",
                     "conditions": [{"type": "price_cross_ma", "direction": "below", "window": 10},
                                    {"type": "stop_loss_pct", "value": -5.0}]},
            "position": {"type": "percent", "size_pct": 100.0},
        }
        name = canonical_name(config)
        # 名字里必须有真实的参数口径；"60日线"绝无藏身之处
        assert "上穿MA2" in name and "MA10" in name and "止损-5%" in name and "全仓" in name
        assert "60日" not in name
        assert name.startswith("601988·")

    def test_deterministic(self):
        config = {
            "symbol": "600519",
            "entry": {"logic": "all", "conditions": [{"type": "rsi_below", "value": 30}]},
            "exit": {"logic": "any", "conditions": [{"type": "take_profit_pct", "value": 10.0}]},
            "position": {"type": "percent", "size_pct": 50},
        }
        assert canonical_name(config) == canonical_name(config)
        assert canonical_name(config) == "600519·RSI≤30→止盈10%·仓位50%"

    def test_config_change_changes_name(self):
        base = {
            "symbol": "000001",
            "entry": {"logic": "all",
                      "conditions": [{"type": "price_cross_ma", "direction": "above", "window": 2}]},
            "exit": {"logic": "any", "conditions": [{"type": "stop_loss_pct", "value": -5.0}]},
            "position": {"type": "percent", "size_pct": 50},
        }
        changed = dict(base)
        changed["entry"] = {"logic": "all",
                            "conditions": [{"type": "price_cross_ma", "direction": "above", "window": 60}]}
        assert canonical_name(base) != canonical_name(changed)

    def test_empty_and_garbage(self):
        assert canonical_name({}) .startswith("策略·")
        assert canonical_name(None).startswith("策略·")

    def test_length_capped(self):
        config = {
            "symbol": "600000",
            "entry": {"logic": "all",
                      "conditions": [{"type": "price_cross_ma", "direction": "above", "window": 2}] * 30},
            "exit": {"logic": "any",
                     "conditions": [{"type": "price_cross_ma", "direction": "below", "window": 10}] * 30},
            "position": {"type": "percent", "size_pct": 50},
        }
        assert len(canonical_name(config)) <= 100


class TestDescribeConditions:
    def test_logic_joiners(self):
        conds = [{"type": "rsi_below", "value": 30}, {"type": "price_cross_ma",
                                                      "direction": "above", "window": 2}]
        assert "且" in describe_conditions(conds, logic="all")
        assert "或" in describe_conditions(conds, logic="any")

    def test_empty(self):
        assert describe_conditions(None) == "未配置"
