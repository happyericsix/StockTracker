# -*- coding: utf-8 -*-
"""盘中盯盘（事件驱动）：**检测必须确定性、判断必须保守**。

<h3>这个文件在防什么</h3>
把盘中决策交给一个轻量 agent 之后，能出问题的方式全都不报错：
- 事件检测拿半截数据瞎触发（开盘第一根/数据源坏值）→ 盯盘被噪声叫醒，token 白烧；
- 输出解析猜动作 → "读不懂"变成一次真实的下单；
- LLM 不可用没有降级标记 → 结算侧不知道该回落规则路径；
- 阈值符号/口径写反（涨跌幅、止损线距离）→ 永远不触发或永远触发。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import intraday_watch as iw
from agent import execution_contract as ec
import llm_service


# ==================== 合成数据工厂 ====================

def make_minute_bars(n=60, base=10.0, step=0.0, volume=1000.0, date="2026-10-09"):
    """n 根 1 分钟 bar（同一自然日，09:31 起）。step=0 → 完全平盘（无事件的底噪）。"""
    bars = []
    for i in range(n):
        bars.append({
            "date": f"{date} {9 + (31 + i) // 60:02d}:{(31 + i) % 60:02d}:00",
            "open": base, "close": base + step * i, "high": base + step * i,
            "low": base, "volume": volume,
        })
    return bars


def make_day_records(n=120, close=10.0):
    """n 根日线（到昨天为止），全部平盘在 close。"""
    return [{"date": f"2026-{(9 - 1) if i < 30 else 9:02d}-{(i % 28) + 1:02d}",
             "open": close, "close": close, "high": close, "low": close, "volume": 1_000_000}
            for i in range(n)]


BASE_CONFIG = {
    "name": "probe", "symbol": "sh601988",
    "entry": {"logic": "all", "conditions": [{"type": "price_above_ma", "window": 20}]},
    "exit": {"logic": "any", "conditions": [{"type": "stop_loss_pct", "value": -8}]},
}

HOLDING = {"shares": 10000, "entry_price": 10.0, "high_watermark": 10.5}


# ==================== 事件检测 ====================

def test_quiet_market_has_no_events():
    """平盘 + 常量量能 → 一条事件都没有（无事件 = 零 LLM 调用）。"""
    assert iw.detect_events(make_minute_bars(), make_day_records(), None, BASE_CONFIG) == []


def test_rule_signal_is_injected_as_event():
    """规则引擎的买/卖作为事件放行（agent 模式下规则退为事件源）。"""
    events = iw.detect_events(make_minute_bars(), make_day_records(), None, BASE_CONFIG,
                              signal="buy")
    assert [e["type"] for e in events] == ["rule_signal"]


def test_minute_data_too_short_only_rule_signal_passes():
    """开盘第一根（数据不足）：宁可只放行规则信号，不拿半截数据算盘中事件。"""
    events = iw.detect_events(make_minute_bars(n=1), make_day_records(), None,
                              BASE_CONFIG, signal="sell")
    assert [e["type"] for e in events] == ["rule_signal"]
    assert iw.detect_events(make_minute_bars(n=1), make_day_records(), None, BASE_CONFIG) == []


def test_sharp_move_one_minute():
    bars = make_minute_bars(volume=1000.0)
    bars[-1]["close"] = bars[-2]["close"] * 1.02   # 最后一根 +2%
    events = iw.detect_events(bars, make_day_records(), None, BASE_CONFIG)
    assert any(e["type"] == "sharp_move" and "+2.00%" in e["detail"] for e in events)


def test_sharp_move_five_minutes_accumulated():
    bars = make_minute_bars(volume=1000.0)
    # 5 分钟窗口 = bars[-5] → bars[-1]：窗口起点保持 10.0，后 4 根抬到 10.3
    # （相邻两根之间 +0%，1 分钟档不触发；窗口累计 +3% 触发 5 分钟档）
    for bar in bars[-4:]:
        bar["close"] = bars[-5]["close"] * 1.03
    events = iw.detect_events(bars, make_day_records(), None, BASE_CONFIG)
    assert any(e["type"] == "sharp_move" and "5 分钟" in e["detail"] for e in events)


def test_volume_spike():
    bars = make_minute_bars(volume=1000.0)
    bars[-1]["volume"] = 5000.0                    # 5× 均量
    events = iw.detect_events(bars, make_day_records(), None, BASE_CONFIG)
    assert any(e["type"] == "volume_spike" and "5.0 倍" in e["detail"] for e in events)


def test_ma_cross_intraday_on_strategy_window():
    # 策略用 MA20（日线 120 根全在 10.0 → MA20=10.0）；分钟价从 9.9 贴着爬上 10.05
    bars = make_minute_bars(base=9.90, volume=1000.0)
    bars[-2]["close"] = 9.90
    bars[-1]["close"] = 10.05
    events = iw.detect_events(bars, make_day_records(120, 10.0), None, BASE_CONFIG)
    assert any(e["type"] == "ma_cross_intraday" and "上穿 MA20" in e["detail"] for e in events)


def test_near_stop_loss_line():
    # 止损 -8%，成本 10.0 → 线在收益率 -8%；当前价 9.24 → 收益率 -7.6%，距线 0.4pp
    bars = make_minute_bars(base=9.24, volume=1000.0)
    events = iw.detect_events(bars, make_day_records(), HOLDING, BASE_CONFIG)
    assert any(e["type"] == "near_stop" for e in events)


def test_far_from_stop_has_no_event():
    bars = make_minute_bars(base=9.90, volume=1000.0)   # 收益率 -1%，距 -8% 线很远
    events = iw.detect_events(bars, make_day_records(), HOLDING, BASE_CONFIG)
    assert not any(e["type"] == "near_stop" for e in events)


def test_near_limit_up():
    # 昨收 10.0，最新分钟价 10.85 → 日内 +8.5%，距主板 10% 涨停 1.5pp（阈值 2pp 内）
    bars = make_minute_bars(base=10.0, volume=1000.0)
    bars[-1]["close"] = 10.85
    events = iw.detect_events(bars, make_day_records(), None, BASE_CONFIG)
    assert any(e["type"] == "near_limit" for e in events)


def test_open_gap_within_first_five_bars():
    # 当日仅 3 根、首根开盘 10.3（昨收 10.0）→ 高走 +3%
    bars = make_minute_bars(n=3, base=10.3, volume=1000.0)
    bars[0]["open"] = 10.3
    events = iw.detect_events(bars, make_day_records(), None, BASE_CONFIG)
    assert any(e["type"] == "gap" and "+3.00%" in e["detail"] for e in events)


def test_gap_not_fired_late_in_day():
    # 同样的首根跳空，但当日已有 30 根 bar → 不再是"跳空"事件
    bars = make_minute_bars(n=30, base=10.3, volume=1000.0)
    bars[0]["open"] = 10.3
    events = iw.detect_events(bars, make_day_records(), None, BASE_CONFIG)
    assert not any(e["type"] == "gap" for e in events)


def test_star_market_uses_20pct_limit():
    config = dict(BASE_CONFIG, symbol="sh688001")
    bars = make_minute_bars(base=10.0, volume=1000.0)
    bars[-1]["close"] = 10.85   # +8.5%：主板逼近涨停，科创板（20%）不算
    events = iw.detect_events(bars, make_day_records(), None, config)
    assert not any(e["type"] == "near_limit" for e in events)


def test_events_only_use_today_bars():
    """跨日的分钟序列：昨天的异动不该混进今天的事件（_bars_of_today 过滤）。"""
    yesterday = make_minute_bars(n=30, date="2026-10-08", volume=1000.0)
    yesterday[-1]["close"] = yesterday[-2]["close"] * 1.05   # 昨天尾盘 +5%
    today = make_minute_bars(n=30, date="2026-10-09", volume=1000.0)
    events = iw.detect_events(yesterday + today, make_day_records(), None, BASE_CONFIG)
    assert events == []


def test_bad_values_never_raise():
    """数据源坏值（None/字符串/NaN）：跳过而不是炸 —— 盘中路径不允许一次解析失败挡住结算。"""
    bars = make_minute_bars(n=10, volume=1000.0)
    bars[-1]["close"] = "—"      # 停牌占位符
    bars[-2]["volume"] = None
    events = iw.detect_events(bars, make_day_records(), HOLDING, BASE_CONFIG, signal="buy")
    assert [e["type"] for e in events] == ["rule_signal"]


# ==================== 面板 ====================

def test_panel_has_price_position_and_ma():
    panel = iw.build_panel(make_minute_bars(base=9.24), make_day_records(), HOLDING, BASE_CONFIG)
    assert panel["price"] == 9.24
    assert panel["daily_ma"]["ma_20"] == 10.0
    assert panel["position"]["shares"] == 10000
    assert panel["position"]["unrealized_pnl_pct"] == -7.6


# ==================== 输出解析（保守优先） ====================

def test_parse_normal_buy():
    out = iw.parse_watch_decision('{"action":"buy","size_fraction":0.5,'
                                  '"confidence":0.8,"reason":"放量上穿均线"}')
    assert out["action"] == "buy" and out["size_fraction"] == 0.5
    assert out["degraded"] is False and not out["warnings"]


def test_parse_unknown_action_falls_back_to_hold():
    out = iw.parse_watch_decision('{"action":"short","size_fraction":1}')
    assert out["action"] == "hold"
    assert any("未知动作" in w for w in out["warnings"])


def test_parse_out_of_range_size_clamped_with_warning():
    out = iw.parse_watch_decision('{"action":"partial_sell","size_fraction":1.7}')
    assert out["action"] == "partial_sell" and out["size_fraction"] == 0.0
    assert any("越界" in w for w in out["warnings"])


def test_parse_garbage_is_hold():
    out = iw.parse_watch_decision("我觉得应该卖了（散文）")
    assert out["action"] == "hold" and "无法解析" in out["reason"]


# ==================== 盯盘判断（FakeLLM） ====================

class FakeLLM:
    def __init__(self, content, tokens=120):
        self.content = content
        self.tokens = tokens
        self.calls = []

    def __call__(self, messages, temperature=0.2, max_tokens=800):
        self.calls.append(messages)
        return {"message": {"role": "assistant", "content": self.content},
                "usage": {"prompt_tokens": self.tokens, "completion_tokens": 0,
                          "total_tokens": self.tokens}}


EVENTS = [{"type": "sharp_move", "detail": "1 分钟拉升 +1.60%"}]


def test_decide_normal_path_asks_llm_once():
    llm = FakeLLM('{"action":"buy","size_fraction":0.4,"confidence":0.7,"reason":"放量急拉"}')
    out = iw.intraday_decide("sh601988", EVENTS, None, {"allow_open": True}, BASE_CONFIG,
                             make_minute_bars(), make_day_records(), completion=llm)
    assert len(llm.calls) == 1
    assert out["action"] == "buy" and out["size_fraction"] == 0.4
    assert out["watch_tokens"] == 120 and out["degraded"] is False
    # 授权边界必须进入提示词：模型越界时至少知道自己越界了
    assert "allow_open" in llm.calls[0][1]["content"]


def test_decide_no_events_skips_llm_entirely():
    llm = FakeLLM('{"action":"buy","size_fraction":1}')
    out = iw.intraday_decide("sh601988", [], None, None, BASE_CONFIG,
                             make_minute_bars(), make_day_records(), completion=llm)
    assert llm.calls == [] and out["action"] == "hold"


def test_decide_unparsable_output_is_hold_not_degraded():
    """输出读不懂：问过模型 → hold（保守不动），但**不是** degraded（不回落规则执行）。"""
    llm = FakeLLM("我看不懂但我建议卖")
    out = iw.intraday_decide("sh601988", EVENTS, None, None, BASE_CONFIG,
                             make_minute_bars(), make_day_records(), completion=llm)
    assert out["action"] == "hold" and out["degraded"] is False


def test_decide_llm_failure_is_degraded():
    """调用异常 = 没问成 → degraded=True，结算侧回落"规则+授权"路径。"""
    class Boom:
        def __call__(self, *args, **kwargs):
            raise RuntimeError("timeout")

    out = iw.intraday_decide("sh601988", EVENTS, None, None, BASE_CONFIG,
                             make_minute_bars(), make_day_records(), completion=Boom())
    assert out["action"] == "hold" and out["degraded"] is True


def test_decide_unavailable_service_is_degraded(monkeypatch):
    monkeypatch.setattr(llm_service, "_is_available", lambda: False)
    llm = FakeLLM('{"action":"buy","size_fraction":1}')
    out = iw.intraday_decide("sh601988", EVENTS, None, None, BASE_CONFIG,
                             make_minute_bars(), make_day_records(), completion=llm)
    assert llm.calls == [] and out["degraded"] is True


def test_decide_placeholder_response_is_degraded(monkeypatch):
    """chat_completion 不可用时返回占位文本而不抛异常 —— 那也是"没问成"。"""
    monkeypatch.setattr(llm_service, "_is_available", lambda: True)
    llm = FakeLLM("AI 服务暂不可用")
    out = iw.intraday_decide("sh601988", EVENTS, None, None, BASE_CONFIG,
                             make_minute_bars(), make_day_records(), completion=llm)
    assert out["degraded"] is True


# ==================== 契约登记（跨语言一致性由 test_execution_contract_java 盯） ====================

def test_watch_skip_reasons_registered_in_contract():
    assert ec.SKIP_WATCH_HOLD in ec.SKIP_REASONS
    assert ec.SKIP_INTRADAY_WATCH_BUDGET_EXCEEDED in ec.SKIP_REASONS


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"passed: {fn.__name__}")


# ==================== news_alert（agent 互通 P3，2026-10-09） ====================


def test_news_alert_enters_prompt_only_when_present():
    """news_shock 事件的详情必须进提示词（含方向），不带 news_alert 时提示词里没有这个键。"""
    llm = FakeLLM('{"action":"flag","size_fraction":0,"confidence":0.6,"reason":"传闻待证"}')
    alert = {"id": 321, "title": "某公司被立案调查", "direction": "利空",
             "credibility": 82, "plain_summary": "证监会立案调查公告。"}
    iw.intraday_decide("sh601988", [{"type": "news_shock"}], None, None, BASE_CONFIG,
                       make_minute_bars(), make_day_records(), completion=llm,
                       news_alert=alert)
    prompt = llm.calls[0][1]["content"]
    assert '"news_alert"' in prompt and "立案调查" in prompt and "利空" in prompt
    # 系统提示也要告诉模型 news_shock 的语义（新闻是信号不是命令）
    assert "news_shock" in iw._watch_system_prompt()

    llm2 = FakeLLM('{"action":"hold","size_fraction":0,"confidence":0.5,"reason":"无事件"}')
    iw.intraday_decide("sh601988", EVENTS, None, None, BASE_CONFIG,
                       make_minute_bars(), make_day_records(), completion=llm2)
    assert '"news_alert"' not in llm2.calls[0][1]["content"], "无新闻时不应带空键"


# ==================== experience（P4 经验回流，2026-10-12） ====================


def test_experience_enters_prompt_only_when_present():
    """策略自己的成绩单进盯盘提示词；不带 experience 时提示词里没有这个键。"""
    llm = FakeLLM('{"action":"hold","size_fraction":0,"confidence":0.5,"reason":"战绩太差不动手"}')
    experience = {"evaluatedTrades": 3, "winRatePct": 33.3,
                  "note": "以下是这条策略自己过去的成交成绩单（开会前先读自己的病历）。"}
    iw.intraday_decide("sh601988", EVENTS, None, None, BASE_CONFIG,
                       make_minute_bars(), make_day_records(), completion=llm,
                       experience=experience)
    prompt = llm.calls[0][1]["content"]
    assert '"experience"' in prompt and "winRatePct" in prompt

    llm2 = FakeLLM('{"action":"hold","size_fraction":0,"confidence":0.5,"reason":"无事件"}')
    iw.intraday_decide("sh601988", EVENTS, None, None, BASE_CONFIG,
                       make_minute_bars(), make_day_records(), completion=llm2)
    assert '"experience"' not in llm2.calls[0][1]["content"], "无成绩单时不应带空键"
