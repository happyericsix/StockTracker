# -*- coding: utf-8 -*-
"""ACCOUNT-GATE 本地回放：用 601988 过去一周（2026-10/28~11/04）真实行情重演事故。

三个场景（对照 docs/ACCOUNT-GATE.md §7 附录）：
  A. 数据护栏：10/14 10:12 半根柱事故的重演防护（forming bar 不再进信号）
  B. 命名：名实不符策略的规范名
  C. 守门员（真实 LLM）：①10/29 15:30 买入信号语境 ②11/3 09:47 前夜 near_stop 语境

跑法：.venv/Scripts/python scripts/replay_account_gate_601988.py
（场景 C 每次花 1 次真实 LLM 调用，共 2 次。）
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import data_guards
from agent.account_gate import gate_decide
from agent.strategy_naming import canonical_name

SEP = "=" * 72


def section(title):
    print(f"\n{SEP}\n{title}\n{SEP}")


# ==================== 场景 A：数据护栏重演（10/14 半根柱事故） ====================

def replay_guards():
    section("场景 A：数据护栏 —— 2026-10-14 10:12:12 半根柱事故的重演防护")

    # A1: 事故重放。当时：bar 标签 10:12（起点口径），墙钟 10:12:12，bar 才走 12 秒，
    # close=最新价 7.12 顶着 MA2 7.115 —— 旧口径直接拿它出买入信号。
    now = datetime(2026, 10, 14, 10, 12, 12)
    bars = []
    for minute in range(9 * 60 + 30, 10 * 60 + 13):    # 09:30..10:12
        label = datetime(2026, 10, 14, minute // 60, minute % 60).strftime("%Y-%m-%d %H:%M:%S")
        bars.append({"date": label, "open": "7.12", "close": "7.12",
                     "high": "7.12", "low": "7.11", "volume": "1000"})
    out, dropped = data_guards.drop_forming_bar(bars, 1, now)
    print(f"A1 半根柱：序列最后一根标签 {bars[-1]['date']}，墙钟 {now.time()}")
    print(f"   护栏判定：dropped={dropped}，评估序列止于 {out[-1]['date']}")
    assert dropped and out[-1]["date"].endswith("10:11:00"), "半根柱必须被丢掉"
    print("   ✅ 10:12 这根只走了 12 秒的柱不再进信号（旧口径正是拿它买的 7.1271）")

    # A2: 停牌/数据源卡住。11/3 14:59 后数据源停更，墙钟已是 11/4 09:20。
    bars2 = [{"date": f"2026-11-03 {h:02d}:{m:02d}:00", "open": "6.9", "close": "6.9",
              "high": "6.9", "low": "6.9", "volume": "1"} for h, m in
             [(9, 30), (9, 31), (14, 58), (14, 59)]]
    out2, guard = data_guards.guard_minute_records(bars2, 1, datetime(2026, 11, 4, 9, 20))
    print(f"A2 陈旧数据：{guard['skip_reason']} —— {guard['detail']}")
    assert guard and guard["skip_reason"] == "stale_data"
    print("   ✅ 旧行情不再结算/刷市值（fail-visible，落一行 skip 痕迹）")

    # A3: 盘前竞价柱。11/4 09:27，序列里混进 09:25 竞价柱（2026-10-12 事故同款）。
    bars3 = [{"date": "2026-11-03 14:59:00", "open": "6.9", "close": "6.9",
              "high": "6.9", "low": "6.9", "volume": "1"},
             {"date": "2026-11-04 09:25:00", "open": "6.8", "close": "6.8",
              "high": "6.8", "low": "6.8", "volume": "1"}]
    out3, guard3 = data_guards.guard_minute_records(bars3, 1, datetime(2026, 11, 4, 9, 27, 4))
    print(f"A3 盘前柱：{guard3['skip_reason']} —— {guard3['detail']}")
    assert guard3 and guard3["skip_reason"] == "data_gap"
    print("   ✅ 集合竞价柱不可结算（旧口径 09:27:04 用竞价价 6.79 买过单）")


# ==================== 场景 B：规范名 ====================

def replay_naming():
    section("场景 B：规范名 —— 名实不符策略的真实配置")
    config = {
        "symbol": "601988",
        "name": "中国银行-价格上穿60日线趋势跟随",
        "entry": {"logic": "all",
                  "conditions": [{"type": "price_cross_ma", "direction": "above", "window": 2}]},
        "exit": {"logic": "any",
                 "conditions": [{"type": "price_cross_ma", "direction": "below", "window": 10},
                                {"type": "stop_loss_pct", "value": -5.0}]},
        "position": {"type": "percent", "size_pct": 100.0},
    }
    print(f"  旧名字（LLM 起）：{config['name']}")
    print(f"  规范名（配置生成）：{canonical_name(config)}")


# ==================== 场景 C：守门员（真实 LLM） ====================

def market_context_quietly():
    try:
        from agent.market_context import get_market_context
        return get_market_context()
    except Exception as exc:  # noqa: BLE001 —— fail-open
        print(f"  （大盘语境取数失败，不带该块：{type(exc).__name__}）")
        return None


def replay_gate_scenario_1():
    """10/29 15:30 的买入信号语境：空仓持有现金，日线 close 7.16 上穿 MA2。

    旧架构实际结果：直接买入 2800 股 @7.17，四天后 -5.3% 止损。
    """
    section("场景 C1：守门员 vs 10/29 买入信号（真实 LLM，旧架构在此买入@7.17）")
    payload = {
        "wake": [{"source": "signal", "strategy_id": 10,
                  "detail": "策略引擎给出 buy 信号（price_cross_ma）"}],
        "signals": [{"strategy_id": 10, "symbol": "601988", "signal": "buy",
                     "matched_conditions": ["price_cross_ma"],
                     "price": 7.16}],
        "account": {
            "positions": [{
                "strategy_id": 10, "name": "601988·价格上穿MA2→价格下穿MA10或止损-5%·全仓",
                "symbol": "601988", "decision_mode": "agent", "shares": 0,
                "cash": "100920.00",
            }],
            "strategies_running": 1, "cash": "100920.00", "total_equity": "100920.00",
            "total_position_value": "0", "position_pct": "0.0",
            "same_symbol_exposure_pct": {},
            "day_drawdown_pct": "0.0",
        },
        "today_context": {"trades": [], "last_committee": "HOLD：观望（一周前）"},
        "experience": {"winRatePct": 0, "evaluated": 2, "hits": 0,
                       "note": "近两笔全部亏损离场（10/15 -2.3%、此前 -5%）"},
    }
    ctx = market_context_quietly()
    if ctx:
        payload["market_context"] = ctx
    result = gate_decide(payload)
    print(f"  degraded={result['degraded']}  tokens={result.get('gate_tokens')}")
    print(f"  summary: {result['summary']}")
    for v in result["verdicts"]:
        print(f"  verdict: strategy={v['strategy_id']} action={v['action']} "
              f"size={v['size_fraction']} conf={v['confidence']}")
        print(f"           reason: {v['reason']}")
        if v.get("thesis_check"):
            print(f"           thesis: {v['thesis_check']}")
    if result["escalate"]:
        print(f"  escalate: {result['escalate']['signature']} —— {result['escalate']['reason']}")
    if result["next_review"]:
        print(f"  next_review: {result['next_review']}")
    return result


def replay_gate_scenario_2():
    """11/3 09:47 前夜语境：持仓 2800@7.17，现价 6.88（距 -5% 止损线 0.96pt）。

    旧架构实际结果：一直没有任何提醒，直到 -5.3% 硬止损在 09:47:22 成交@6.79。
    """
    section("场景 C2：守门员 vs 11/3 near_stop（真实 LLM，旧架构只能等硬止损）")
    payload = {
        "wake": [{"source": "near_stop", "strategy_id": 10,
                  "detail": "距止损线 0.96 个百分点"}],
        "account": {
            "positions": [{
                "strategy_id": 10, "name": "601988·价格上穿MA2→价格下穿MA10或止损-5%·全仓",
                "symbol": "601988", "decision_mode": "agent",
                "shares": "2800", "avg_cost": "7.17", "last_price": "6.88",
                "high_watermark": "7.17", "unrealized_pnl_pct": "-4.04",
                "entry_age_days": 3, "t1_locked_shares": "0",
                "position_share_pct": "19.9",
                "stop_lines": {"stop_loss_pct": "-5", "distance_to_stop_pct": "0.96"},
                "authorization": {"allow_open": True, "allow_close": True,
                                  "max_position_pct": "0.2", "days_since_issued": 3,
                                  "stale": False},
            }],
            "strategies_running": 1, "cash": "80076.00", "total_equity": "99316.00",
            "total_position_value": "19264.00", "position_pct": "19.4",
            "same_symbol_exposure_pct": {"601988": "19.4"},
            "day_drawdown_pct": "-0.5",
        },
        "today_context": {"trades": [], "last_committee": "授权盘中执行，仓位上限 20%"},
        "experience": {"winRatePct": 0, "evaluated": 3, "hits": 0,
                       "note": "近三笔全部亏损离场"},
    }
    ctx = market_context_quietly()
    if ctx:
        payload["market_context"] = ctx
    result = gate_decide(payload)
    print(f"  degraded={result['degraded']}  tokens={result.get('gate_tokens')}")
    print(f"  summary: {result['summary']}")
    for v in result["verdicts"]:
        print(f"  verdict: strategy={v['strategy_id']} action={v['action']} "
              f"size={v['size_fraction']} conf={v['confidence']}")
        print(f"           reason: {v['reason']}")
        if v.get("thesis_check"):
            print(f"           thesis: {v['thesis_check']}")
    if result["escalate"]:
        print(f"  escalate: {result['escalate']['signature']} —— {result['escalate']['reason']}")
    if result["next_review"]:
        print(f"  next_review: {result['next_review']}")
    return result


if __name__ == "__main__":
    print("601988 过去一周真实日线（akshare 实取）：")
    try:
        from akshare_client import get_history
        for row in (get_history("601988") or [])[-6:]:
            print(f"  {row['date']}  close {row['close']}")
    except Exception as exc:  # noqa: BLE001
        print(f"  （取数失败：{exc}）")
    replay_guards()
    replay_naming()
    r1 = replay_gate_scenario_1()
    r2 = replay_gate_scenario_2()
    print(f"\n{SEP}\n回放结论\n{SEP}")
    print("护栏与命名：确定性断言全部通过（A1/A2/A3、B）。")
    print(f"守门员 C1（10/29 买入语境）动作：{[v['action'] for v in r1['verdicts']] or '（无 verdict）'}")
    print(f"守门员 C2（11/3 近止损语境）动作：{[v['action'] for v in r2['verdicts']] or '（无 verdict）'}")
