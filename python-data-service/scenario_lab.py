# -*- coding: utf-8 -*-
"""场景实验室（2026-10-15）：拿真实极端行情喂给委员会/盯盘，采集 agent 的真实决策。

用法（容器内）：python scenario_lab.py [--only S1,S2]

每个场景 = 合成 K 线 + 持仓/批次/事件/新闻上下文 → 调真实 LLM（DeepSeek）→
记录 decision/action + size + reason。产出 JSON 与人读表格，供"agent 在极端行情下
会不会犯错"的审计。确定性护栏（涨跌停挡单/T+1/参与率/降级持仓）不在此测——
那些在 Java 单测里，本实验室只测【需要判断力的部分】。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

SYMBOL = "601988"          # 用真实标的的板块规则（主板 10%）
CONFIG = {
    "schema_version": "1.0", "initial_capital": 100000, "symbol": SYMBOL,
    "name": "中国银行-价格上穿60日线趋势跟随",
    "data": {"period": "day", "lookback_days": 250},
    "risk": {"commission_pct": 0.025, "slippage_pct": 0.1},
    "position": {"type": "percent", "size_pct": 100},
    "entry": {"logic": "all", "conditions": [{"type": "price_cross_ma", "direction": "above", "window": 2}]},
    "exit": {"logic": "any", "conditions": [{"type": "price_cross_ma", "direction": "below", "window": 10},
                                            {"type": "stop_loss_pct", "value": -5}]},
}
OPEN_AUTH = {"intraday_execution": True, "allow_open": True, "allow_close": True,
             "max_position_pct": 1.0, "notes": "测试授权：全开"}


def make_days(closes, start="2026-08-03", base_vol=3_000_000, vol=None):
    """按收盘序列合成日线（open=昨收，high/low 覆盖两端）。"""
    d = date.fromisoformat(start)
    rows = []
    prev = closes[0]
    for i, c in enumerate(closes):
        v = (vol[i] if vol else base_vol)
        rows.append({"date": d.isoformat(), "open": round(prev, 3), "close": round(c, 3),
                     "high": round(max(prev, c) * 1.004, 3), "low": round(min(prev, c) * 0.996, 3),
                     "volume": v})
        prev = c
        d += timedelta(days=1 if d.weekday() < 4 else 3)   # 粗略跳过周末
    return rows


def make_minutes(day, prices, base_vol=900_000):
    """合成某天的分钟线（prices: 每分钟收盘序列）。"""
    out = []
    hh, mm = 9, 31
    for i, p in enumerate(prices):
        out.append({"date": f"{day} {hh:02d}:{mm:02d}:00", "open": round(prices[i - 1] if i else p, 3),
                    "close": p, "high": round(p * 1.0008, 3), "low": round(p * 0.9992, 3),
                    "volume": base_vol})
        mm += 1
        if mm == 60:
            hh, mm = hh + 1, 0
    return out


def lot(opened, cost, shares, price_now, locked=False):
    pnl = round((price_now / cost - 1) * 100, 2)
    return {"opened": opened, "age_days": 1, "cost": cost, "shares": shares,
            "pnl_pct": pnl, "locked_today": locked}


# ==================== 场景定义 ====================

def s1_this_morning():
    """今晨情形复刻：昨买 7.13 现价 6.96（-2.4%），规则 MA 下穿卖出信号。"""
    now = 6.96
    pos = {"shares": 14500, "entry_price": 7.13, "high_watermark": 7.14,
           "lots": [lot("2026-10-14 10:12", 7.13, 14500, now)]}
    events = [{"type": "rule_signal", "detail": "规则引擎给出 sell（price_cross_ma below MA10）"}]
    minute = make_minutes("2026-10-15", [7.05, 7.03, 7.01, 6.99, now])
    days = make_days([7.0, 7.07, 7.06, 7.05, 7.05, 7.06, 7.05, 7.02, now], start="2026-10-09")
    return dict(kind="watch", name="S1 今晨复刻：浮亏2.4%+规则卖出信号", symbol=SYMBOL,
                events=events, position=pos, authorization=OPEN_AUTH, config=CONFIG,
                minute_bars=minute, day_records=days,
                today_context={"trades": [], "last_committee": "10-14 全委会：按计划持有",
                               "open_lots": pos["lots"]})


def s2_near_stop():
    """急跌逼近止损：成本 7.10 现价 6.78（-4.5%），止损线 -5%。"""
    now = 6.78
    pos = {"shares": 14500, "entry_price": 7.10, "high_watermark": 7.40,
           "lots": [lot("2026-10-08 10:00", 7.10, 14500, now)]}
    events = [{"type": "near_stop", "detail": "现价距止损线 -5% 还有 0.5 个百分点"}]
    minute = make_minutes("2026-10-15", [6.95, 6.90, 6.86, 6.82, now])
    days = make_days([7.1, 7.2, 7.1, 6.98, 6.9, 6.85, now], start="2026-10-08")
    return dict(kind="watch", name="S2 急跌-4.5%逼近止损线", symbol=SYMBOL,
                events=events, position=pos, authorization=OPEN_AUTH, config=CONFIG,
                minute_bars=minute, day_records=days, today_context={"open_lots": pos["lots"]})


def s3_limit_up_holding():
    """一字涨停持仓：浮盈 +8%，涨停事件。"""
    now = 7.80
    pos = {"shares": 14500, "entry_price": 7.22, "high_watermark": 7.80,
           "lots": [lot("2026-10-09 10:00", 7.22, 14500, now)]}
    events = [{"type": "limit_reached", "detail": "已触涨停 7.80（+10%）"}]
    minute = make_minutes("2026-10-15", [7.6, 7.7, 7.78, 7.80, 7.80], base_vol=200_000)
    days = make_days([7.1, 7.3, 7.5, 7.09, 7.3, 7.6, now], start="2026-10-08")
    return dict(kind="watch", name="S3 一字涨停持仓（浮盈8%）", symbol=SYMBOL,
                events=events, position=pos, authorization=OPEN_AUTH, config=CONFIG,
                minute_bars=minute, day_records=days, today_context={"open_lots": pos["lots"]})


def s4_chase_rally():
    """空仓 + 连涨 5 日 +8%，规则上穿信号：追不追。"""
    days = make_days([6.4, 6.45, 6.5, 6.6, 6.75, 6.9, 6.98, 7.05], start="2026-09-22")
    return dict(kind="committee", name="S4 空仓连涨5日+8%：追高吗", symbol=SYMBOL,
                as_of=days[-1]["date"], records=days, position=None, config=CONFIG)


def s5_ex_div_gap():
    """除权假跳空：昨收 7.30，今跳空 7.00（-4.1%，实为每股派 0.30 除息）。
    上下文里【没有】除权信息——观察 agent 是否把除息缺口当成急跌。"""
    now = 7.00
    pos = {"shares": 14500, "entry_price": 7.05, "high_watermark": 7.35,
           "lots": [lot("2026-10-08 10:00", 7.05, 14500, now)]}
    events = [{"type": "sharp_drop", "detail": "日内 -4.1%（相对昨收 7.30）"}]
    minute = make_minutes("2026-10-15", [7.05, 7.02, 7.01, 7.00, now])
    days = make_days([7.2, 7.28, 7.30, 7.18, 7.22, 7.30, 7.30], start="2026-10-08")
    return dict(kind="watch", name="S5 除权日假跳空-4.1%（上下文无除权信息）", symbol=SYMBOL,
                events=events, position=pos, authorization=OPEN_AUTH, config=CONFIG,
                minute_bars=minute, day_records=days, today_context={"open_lots": pos["lots"]})


def s5b_ex_div_informed():
    """S5 对照组：同行情，today_context 带除权说明——若不慌即证明缺口在上下文而非模型。"""
    sc = s5_ex_div_gap()
    sc = dict(sc)
    sc["name"] = "S5b 除权日跳空（today_context 带除权说明）"
    sc["today_context"] = {**sc["today_context"],
                           "corporate_action": "今日除权除息：每股派现 0.30 元，除权参考价 7.00；"
                                               "账户已按批次自动入账红利并调整成本/HWM，"
                                               "相对新成本价并非实际亏损"}
    return sc


def s6_two_lots():
    """多批次抉择：老批 +4.2%、新批 -2.4%，规则半仓卖出信号——size 怎么给。"""
    now = 7.06
    pos = {"shares": 29000, "entry_price": 6.965, "high_watermark": 7.14,
           "lots": [lot("2026-10-08 10:00", 6.78, 14500, now),
                    lot("2026-10-14 10:12", 7.15, 14500, now)]}
    events = [{"type": "rule_signal", "detail": "规则引擎给出 sell（price_cross_ma below MA10）"}]
    minute = make_minutes("2026-10-15", [7.12, 7.10, 7.08, 7.07, now])
    days = make_days([6.9, 7.07, 7.06, 7.05, 7.05, 7.06, 7.05, 7.02, now], start="2026-10-09")
    return dict(kind="watch", name="S6 双批次（老批+4.2%/新批-2.4%）+半仓卖出信号", symbol=SYMBOL,
                events=events, position=pos, authorization=OPEN_AUTH, config=CONFIG,
                minute_bars=minute, day_records=days,
                today_context={"open_lots": pos["lots"],
                               "lots_note": "卖出按先进先出对冲批次；locked_today=True 的批次今日不可卖"})


def s7_bad_news_profit():
    """利空新闻 + 浮盈 6%：news_shock。"""
    now = 7.5
    pos = {"shares": 14500, "entry_price": 7.08, "high_watermark": 7.55,
           "lots": [lot("2026-10-08 10:00", 7.08, 14500, now)]}
    events = [{"type": "news_shock"}]
    minute = make_minutes("2026-10-15", [7.55, 7.52, 7.5, 7.5, now])
    days = make_days([7.1, 7.2, 7.3, 7.4, 7.45, 7.5, now], start="2026-10-08")
    news = {"title": "公司被立案调查", "direction": "利空", "impact_level": "high",
            "credibility": 82, "plain_summary": "证监会立案调查公告，事项未明。"}
    return dict(kind="watch", name="S7 高可信利空新闻+浮盈6%", symbol=SYMBOL,
                events=events, position=pos, authorization=OPEN_AUTH, config=CONFIG,
                minute_bars=minute, day_records=days, news_alert=news,
                today_context={"open_lots": pos["lots"]})


def s8_fake_breakout():
    """缩量假突破：价创新高但量连续萎缩（委员会日结视角）。"""
    days = make_days([6.5, 6.8, 7.0, 6.85, 7.05, 7.1, 7.15, 7.05, 7.18, 7.22, 7.28],
                     start="2026-09-08",
                     vol=[5_000_000, 4_800_000, 4_200_000, 3_600_000, 3_000_000,
                          2_600_000, 2_200_000, 1_900_000, 1_600_000, 1_400_000, 1_200_000])
    return dict(kind="committee", name="S8 缩量新高（量价背离）空仓", symbol=SYMBOL,
                as_of=days[-1]["date"], records=days, position=None, config=CONFIG)


def s9_long_decline():
    """长阴跌 + 低估值 + 规则上穿：防"便宜了"式接飞刀（委员会）。"""
    days = make_days([8.0, 7.8, 7.65, 7.5, 7.35, 7.2, 7.1, 6.95, 6.85, 6.78, 6.85, 6.9, 6.95],
                     start="2026-09-01")
    val = {"pe": 5.1, "pe_percentile_5y": 4.0, "pb": 0.52,
           "note": "PE/PB 均处近 5 年最低分位"}
    return dict(kind="committee", name="S9 长阴跌-13%后低估值反抽", symbol=SYMBOL,
                as_of=days[-1]["date"], records=days, position=None, config=CONFIG,
                valuation_context=val)


ALL = [s1_this_morning, s2_near_stop, s3_limit_up_holding, s4_chase_rally,
       s5_ex_div_gap, s5b_ex_div_informed, s6_two_lots, s7_bad_news_profit,
       s8_fake_breakout, s9_long_decline]


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    only = {x.strip() for x in args.only.split(",") if x.strip()}

    from agent import trading_committee, intraday_watch

    results = []
    for factory in ALL:
        sc = factory()
        key = sc["name"].split()[0]
        if only and key not in only:
            continue
        print(f"\n===== {sc['name']} =====", flush=True)
        try:
            if sc["kind"] == "watch":
                out = intraday_watch.intraday_decide(
                    sc["symbol"], sc["events"], sc["position"], sc["authorization"],
                    sc["config"], sc["minute_bars"], sc["day_records"],
                    news_alert=sc.get("news_alert"), today_context=sc.get("today_context"))
                row = {"scenario": sc["name"], "target": "盯盘",
                       "action": out.get("action"), "size": out.get("size_fraction"),
                       "confidence": out.get("confidence"), "degraded": out.get("degraded"),
                       "reason": (out.get("reason") or "")[:400]}
            else:
                out = trading_committee.decide(
                    sc["symbol"], sc["as_of"], sc["records"], sc.get("position"),
                    settlement_kind="daily", config=sc["config"],
                    valuation_context=sc.get("valuation_context"))
                row = {"scenario": sc["name"], "target": "委员会",
                       "action": out.get("decision") or out.get("signal"),
                       "size": out.get("size_fraction"),
                       "confidence": None, "degraded": out.get("degraded"),
                       "skip_reason": out.get("skip_reason"),
                       "reason": ((out.get("committee", {}) or {}).get("summary")
                                  or out.get("reason") or json.dumps(out, ensure_ascii=False)[:300])[:400]}
        except Exception as e:  # noqa: BLE001
            row = {"scenario": sc["name"], "target": sc.get("kind", "?"), "error": str(e)[:200]}
        results.append(row)
        print(json.dumps(row, ensure_ascii=False, indent=2), flush=True)

    print("\n\n========== 汇总 ==========")
    for r in results:
        mark = r.get("error") or f"{r.get('action')} size={r.get('size')}"
        if r.get("degraded"):
            mark += " [degraded]"
        print(f"- {r['scenario'][:46]:<48} → {mark}")

    # 退出码门禁（2026-10-18，回归闭环）：本实验室是提示词/路由改动的验收场——
    # ① 任何场景抛异常（error）或"没问成模型"（degraded）= 环境性失败，红；
    # ② 委员会场景 skip_reason=agent_unparsable = 解析失败（reasoner 路由最需要盯的
    #    风险），红。主动 HOLD 的 skip_reason 是 rule_not_met，**不是**失败。
    # 刻意**不**门禁"决策对不对"：S5（除权假跳空）本来就允许答错，
    # 那类判读归 eval_domain 金标集管，这里只拦基础设施性失败。
    failures = [r for r in results
                if r.get("error") or r.get("degraded")
                or (r.get("target") == "委员会" and r.get("skip_reason") == "agent_unparsable")]
    if failures:
        print(f"\n[REGRESSION] {len(failures)}/{len(results)} 个场景未过门禁（异常/降级/不可解析）")
        return 1
    print(f"\n[REGRESSION] {len(results)} 个场景全部过门禁")
    return 0


if __name__ == "__main__":
    sys.exit(run())
