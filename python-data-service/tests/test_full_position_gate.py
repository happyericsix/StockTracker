# -*- coding: utf-8 -*-
"""满仓硬门禁（2026-10-15 红队 F2 修复）：静默满仓实战复现后的代码层收口。

两层门：
1. finalize_strategy 工具层——满仓且未带 position_confirmed=true 一律 valid=False；
2. 正文直出层——模型在回复正文里输出的满仓 JSON（不经 finalize）同样打回，
   逼模型先向用户披露并确认。

schema 的 position 默认值就是 full/100，所以"模型漏写 position 字段"与
"明确写了满仓"在判定上是一回事——都过不了门。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent.tool_contract as tc
from agent.tool_registry import _is_full_position, _tool_finalize_strategy

# 避免触发真实工具装填/HTTP：沿用 test_react_agent 的替身手法
import agent.react_agent as ra
import tests.test_react_agent as tra


FULL_STRATEGY = {
    "schema_version": "1.0", "name": "满仓策略", "symbol": "600519",
    "position": {"type": "percent", "size_pct": 100},
    "entry": {"logic": "all", "conditions": [
        {"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}]},
    "exit": {"logic": "any", "conditions": [{"type": "stop_loss_pct", "value": -8}]},
}

HALF_STRATEGY = {
    "schema_version": "1.0", "name": "半仓策略", "symbol": "600519",
    "position": {"type": "percent", "size_pct": 50},
    "entry": {"logic": "all", "conditions": [
        {"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}]},
    "exit": {"logic": "any", "conditions": [{"type": "stop_loss_pct", "value": -8}]},
}


# ==================== 工具层门禁 ====================

def test_full_position_without_confirmation_is_rejected():
    out = _tool_finalize_strategy({"strategy_json": FULL_STRATEGY})
    data = tc.data_of(out)
    assert data["valid"] is False
    assert "满仓" in data["error"]
    assert "position_confirmed" in data["error"]


def test_omitted_position_defaults_to_full_and_is_rejected():
    """模型漏写 position 字段：schema 默认 full/100 —— 同样过不了门。"""
    no_position = {k: v for k, v in FULL_STRATEGY.items() if k != "position"}
    out = _tool_finalize_strategy({"strategy_json": no_position})
    assert tc.data_of(out)["valid"] is False


def test_type_full_is_rejected_even_with_small_size_pct():
    strategy = dict(FULL_STRATEGY)
    strategy["position"] = {"type": "full", "size_pct": 30}
    out = _tool_finalize_strategy({"strategy_json": strategy})
    assert tc.data_of(out)["valid"] is False


def test_full_position_with_confirmation_passes():
    out = _tool_finalize_strategy({"strategy_json": FULL_STRATEGY,
                                   "position_confirmed": True})
    data = tc.data_of(out)
    assert data["valid"] is True
    assert data["strategy_json"]["position"]["size_pct"] == 100


def test_non_full_position_needs_no_confirmation():
    out = _tool_finalize_strategy({"strategy_json": HALF_STRATEGY})
    assert tc.data_of(out)["valid"] is True


def test_is_full_position_detection():
    class _Pos:
        def __init__(self, type_, size):
            self.type = type_
            self.size_pct = size
    class _Cfg:
        def __init__(self, pos):
            self.position = pos
    assert _is_full_position(_Cfg(_Pos("full", None)))
    assert _is_full_position(_Cfg(_Pos("percent", 100)))
    assert _is_full_position(_Cfg(_Pos("percent", 120)))   # 越界值也按满仓拦
    assert not _is_full_position(_Cfg(_Pos("percent", 99.9)))
    assert not _is_full_position(_Cfg(_Pos("percent", 50)))
    assert not _is_full_position(_Cfg(None))


# ==================== 正文直出层门禁 ====================

def test_full_position_json_in_prose_is_rejected_and_model_asks_user():
    """红队 F2 复现场景：模型正文直出满仓 JSON（没调 finalize）——
    必须打回；模型的下一句应当转向披露/询问，而不是带出 strategy_json。"""
    calls = {"n": 0}

    def fake(messages, tools=None, temperature=0.2, max_tokens=1200):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"message": {"role": "assistant",
                                "content": "已生成策略\n```json\n"
                                           + json.dumps(FULL_STRATEGY, ensure_ascii=False)
                                           + "\n```"}}
        # 被打回后的正确行为：向用户披露并询问
        return {"message": {"role": "assistant",
                            "content": "仓位我默认填了 100%（满仓，没有现金缓冲），要降低吗？"}}

    out = tra._run_with_completion(fake)
    assert out["strategy_json"] is None
    assert out.get("backtest") is None
    assert any("满仓" in r or "仓位" in r for r in out["replies"])
    assert calls["n"] == 2   # 确实打回重问了一次


def test_confirmed_full_position_via_finalize_tool_still_works():
    """确认过的满仓走 finalize 工具（带 position_confirmed）仍然放行——
    门禁拦的是"未经确认"，不是"满仓"本身。"""
    calls = {"n": 0}

    def fake_execute_tool(name, args):
        if name == "finalize_strategy":
            # 真实 handler（不是替身）：验证门禁与确认标记的联动
            return _tool_finalize_strategy(args)
        return tra.fake_execute_tool(name, args)

    def fake(messages, tools=None, temperature=0.2, max_tokens=1200):
        calls["n"] += 1
        if calls["n"] == 1:
            return tra._tool_call("finalize_strategy",
                                  {"strategy_json": FULL_STRATEGY, "position_confirmed": True})
        return {"message": {"role": "assistant", "content": "已按确认的满仓生成策略。"}}

    original = ra.execute_tool
    ra.execute_tool = fake_execute_tool
    try:
        out = tra._run_with_completion(fake)
    finally:
        ra.execute_tool = original
    assert out["strategy_json"] is not None
    assert out["strategy_json"]["position"]["size_pct"] == 100
