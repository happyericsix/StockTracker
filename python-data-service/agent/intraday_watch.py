# -*- coding: utf-8 -*-
"""盘中盯盘（事件驱动的轻量 agent）：真正的"老手盯盘"，而不是每天收盘后复盘一次。

<h3>它和全委会（trading_committee）的分工</h3>
- 全委会：8 角色串行辩论，~2 万 token、几十秒 —— 只在日线结算（15:30）开，
  负责"复盘 + 划定次日的盘中授权边界"；
- 盯盘交易员（本模块）：**1 次** LLM 调用、几百 token、秒级 —— 盘中每次行情刷新
  都算指标（零成本），只有"值得老手抬头看一眼"的事件发生时才被叫起来判断，
  在委员会划定的边界内直接下单。

<h3>三层成本结构（这是它能实时跑起来的原因）</h3>
1. 指标运算：每次刷新都做（本模块的 detect_events / build_panel，纯代码零成本）；
2. 事件检测：纯代码阈值判断，无事件 = 无 LLM 调用；
3. 盯盘判断：事件触发才调 1 次模型。一天约 5~15 次事件 × 2~3k token，
   与全委会一次的成本同量级。

<h3>与既有纪律的关系</h3>
- 保守优先：模型输出读不懂 → hold（"读不懂就不动"，与 trading_decision 同一条纪律）；
  模型服务不可用 / 调用异常 → degraded=True（结算侧回落到"规则+授权"的现行行为，
  盘中绝不因为 LLM 故障瘫痪）；
- 止损硬通道仍然优先于本模块（结算侧在调用盯盘之前先判止损，那条路不等模型）；
- 全部留痕：每次判断（含 hold）随痕迹落库，trigger=watch。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from agent import execution_contract as ec
from agent.strategy_engine import compute_indicators

logger = logging.getLogger(__name__)

# ==================== 灵敏度（平衡档，2026-10-09 产品决策） ====================
# 档位刻意的取舍：阈值低 → 信号密但噪声多、token 贵；阈值高 → 便宜但错过行情。
# 全部是"值得老手抬头看一眼"的量级；改任何一个都应当对着一天的真实分钟线回放验证。

#: 单根 1 分钟 bar 的涨跌幅阈值（%）。
SHARP_MOVE_1M_PCT = 1.5
#: 最近 5 根 1 分钟 bar 的累计涨跌幅阈值（%）。
SHARP_MOVE_5M_PCT = 2.5
#: 最新一根的量 / 最近 20 根分钟均量 的倍数。
VOLUME_SPIKE_MULTIPLE = 2.5
#: 距止损/止盈/移动止损线的距离（百分点）——"快到线了"就该提前看一眼。
NEAR_STOP_PCT = 1.0
#: 逼近涨跌停的距离（百分点，按板块涨跌幅限制折算）。
NEAR_LIMIT_PCT = 2.0
#: 开盘跳空阈值（%）。只在开盘后前 5 根 bar 内判定（之后的缺口不再是"跳空"）。
GAP_PCT = 2.0

#: 盯盘 agent 单次输出的 max_tokens：action + size + confidence + 一句理由，300 足够，
#: 超了只会换来散文（而散文正是解析失败的来源）。
WATCH_MAX_TOKENS = 800   # 2026-10-15 提自 300：推理型模型即使关了思考，面板描述也偶超 300

#: 动作封闭集（结算侧按它执行；未知动作一律按 hold 处理）。
ACTION_HOLD = "hold"
ACTION_BUY = "buy"
ACTION_SELL = "sell"
ACTION_PARTIAL_SELL = "partial_sell"
ACTION_FLAG = "flag"   # 只标记给 15:30 的全委会 + 推送用户，不下单
WATCH_ACTIONS = (ACTION_HOLD, ACTION_BUY, ACTION_SELL, ACTION_PARTIAL_SELL, ACTION_FLAG)


# ==================== 数值防御（数据源全是字符串，坏值必须跳过而不是炸） ====================

def _f(value: Any) -> Optional[float]:
    try:
        out = float(value)
        return out if out == out else None   # NaN → None
    except (TypeError, ValueError):
        return None


def _bars_of_today(minute_bars: list[dict]) -> list[dict]:
    """今天（分钟序列最后一根所在日）的分钟 bar —— 盘中指标只用当日数据。"""
    if not minute_bars:
        return []
    today = str(minute_bars[-1].get("date", ""))[:10]
    return [bar for bar in minute_bars if str(bar.get("date", ""))[:10] == today]


def _limit_pct_for(symbol: str) -> float:
    """板块涨跌幅限制：科创板/创业板 20%，其余 10%（北交所 30% 这里按保守取 10% 口径不检测）。"""
    s = (symbol or "").lower()
    if s.startswith(("sh688", "sh689", "sz300")):
        return 20.0
    return 10.0


# ==================== 事件检测（纯代码，零 LLM 成本） ====================

def detect_events(minute_bars: list[dict], day_records: list[dict],
                  position: dict | None, config: dict | None,
                  *, signal: Optional[str] = None) -> list[dict]:
    """从分钟线 + 日线 + 持仓状态里找出"值得老手抬头看一眼"的事件。

    `signal` 是规则引擎在同一根 bar 上给出的买/卖（调用方注入）：agent 模式下
    规则信号不再直接执行，而是作为一条事件交给盯盘 agent 判断 ——
    "模型决定要不要动，代码决定怎么动"。

    返回 [{type, detail}]，type 是封闭集；无事件返回 []（调用方一次 LLM 都不花）。
    """
    events: list[dict] = []
    # 规则信号（调用方注入）在最前面放行：它不依赖分钟数据 —— 即使下面的盘中指标
    # 因为数据坏值/不足全部算不了，规则信号也必须作为事件到达盯盘 agent。
    if signal in ("buy", "sell"):
        events.append({"type": "rule_signal", "detail": f"规则引擎给出 {signal}"})
    today_bars = _bars_of_today(minute_bars or [])
    if len(today_bars) < 2:
        # 分钟数据不足（开盘第一根 / 数据源故障）：盘中事件算不了就算不了 ——
        # 宁可少触发，不可拿半截数据瞎触发。
        return events

    last = today_bars[-1]
    prev = today_bars[-2]
    price = _f(last.get("close"))
    prev_close = _f(prev.get("close"))
    if price is None or not price or prev_close is None:
        return events

    # ---- ① 急涨急跌（1 分钟 / 5 分钟两档） ----
    move_1m = (price / prev_close - 1.0) * 100.0 if prev_close else None
    if move_1m is not None and abs(move_1m) >= SHARP_MOVE_1M_PCT:
        events.append({"type": "sharp_move",
                       "detail": f"1 分钟 {'拉升' if move_1m > 0 else '跳水'} {move_1m:+.2f}%"})
    if len(today_bars) >= 5:
        base = _f(today_bars[-5].get("close"))
        if base:
            move_5m = (price / base - 1.0) * 100.0
            if abs(move_5m) >= SHARP_MOVE_5M_PCT:
                events.append({"type": "sharp_move",
                               "detail": f"5 分钟累计 {move_5m:+.2f}%"})

    # ---- ② 量能异常（最新 1 根 / 最近 20 根分钟均量） ----
    volumes = [_f(bar.get("volume")) or 0.0 for bar in today_bars[-21:-1]]
    last_volume = _f(last.get("volume")) or 0.0
    if volumes and sum(volumes) > 0:
        ratio = last_volume / (sum(volumes) / len(volumes))
        if ratio >= VOLUME_SPIKE_MULTIPLE:
            events.append({"type": "volume_spike",
                           "detail": f"当前分钟量能是近 20 根均量的 {ratio:.1f} 倍"})

    # ---- ③ 盘中穿越策略均线（长周期均线用日线算，盘中价与它比） ----
    day_closes = [_f(r.get("close")) for r in (day_records or [])]
    day_closes = [c for c in day_closes if c is not None]
    for window in _strategy_ma_windows(config):
        ma = _daily_ma(day_closes, window)
        if ma is None:
            continue
        prev_side = _side(prev_close, ma)
        last_side = _side(price, ma)
        if prev_side and last_side and prev_side != last_side and abs(prev_close - ma) / ma * 100 < 1.0:
            # 严格穿越（且不是"本来就贴着均线抖"）：前一根在另一侧 ±1% 容差外
            events.append({"type": "ma_cross_intraday",
                           "detail": f"盘中价格{'上穿' if last_side > 0 else '跌破'} MA{window}"
                                     f"（均线值 {ma:.2f}）"})

    # ---- ④ 距止损/止盈/移动止损线 ≤1 个百分点（持仓时） ----
    if isinstance(position, dict) and (_f(position.get("shares")) or 0) > 0:
        entry = _f(position.get("entry_price"))
        hwm = _f(position.get("high_watermark"))
        for kind, line, current in _stop_lines(position, config, price):
            if line is None or current is None:
                continue
            distance = abs(current - line)
            if distance <= NEAR_STOP_PCT:
                events.append({"type": "near_stop",
                               "detail": f"距{kind}线仅 {distance:.2f} 个百分点"
                                         f"（当前 {current:+.2f}% / 线 {line:+.2f}%）"})

    # ---- ⑤ 逼近涨跌停 ----
    day_change = _day_change_pct(today_bars, day_records)
    if day_change is not None:
        limit = _limit_pct_for(str((config or {}).get("symbol") or ""))
        threshold = limit - NEAR_LIMIT_PCT
        if day_change >= threshold or day_change <= -threshold:
            events.append({"type": "near_limit",
                           "detail": f"日内已 {day_change:+.2f}%（{limit:.0f}% 板块涨跌停"
                                     f"还剩 {limit - abs(day_change):.2f} 个百分点）"})

    # ---- ⑥ 开盘跳空（只在当日前 5 根内判定） ----
    if len(today_bars) <= 5 and day_closes:
        first_open = _f(today_bars[0].get("open"))
        prev_day_close = day_closes[-1]
        if first_open and prev_day_close:
            gap = (first_open / prev_day_close - 1.0) * 100.0
            if abs(gap) >= GAP_PCT:
                events.append({"type": "gap",
                               "detail": f"开盘{'高' if gap > 0 else '低'}走 {gap:+.2f}%"})

    return events


def _strategy_ma_windows(config: dict | None) -> list[int]:
    """策略真正用到的均线窗口（与引擎/快照同一来源，不另写一套解析）。"""
    try:
        return sorted({int(key[3:]) for key in ec.indicator_keys_for(config or {})
                       if key.startswith("ma_") and key[3:].isdigit()})
    except Exception:  # noqa: BLE001 —— 配置读不出来就退回最常用的两条
        return [20, 60]


def _daily_ma(closes: list[float], window: int) -> Optional[float]:
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def _side(price: float, reference: float) -> Optional[int]:
    if price > reference:
        return 1
    if price < reference:
        return -1
    return None


def _stop_lines(position: dict, config: dict | None, price: float):
    """yield (名称, 触发线(收益率%), 当前值(收益率%)) —— 三类风控线统一成收益率口径。"""
    entry = _f(position.get("entry_price"))
    if not entry:
        return
    current_return = (price / entry - 1.0) * 100.0
    for kind in ("stop_loss_pct", "take_profit_pct"):
        line = _risk_value(config, kind)
        if line is not None:
            yield kind, line, current_return
    hwm = _f(position.get("high_watermark"))
    trailing = _risk_value(config, "trailing_stop_pct")
    if hwm and trailing is not None:
        # 移动止损线：hwm*(1 - trailing/100) 对应的"从高点回撤"口径
        line_price = hwm * (1.0 - trailing / 100.0)
        current_drawdown = (hwm - price) / hwm * 100.0
        line_drawdown = trailing
        yield "trailing_stop_pct", line_drawdown, current_drawdown


def _risk_value(config: dict | None, kind: str) -> Optional[float]:
    """从策略 exit 条件里找风控参数值（与引擎同一结构：exit.conditions[].type/value）。"""
    try:
        for cond in ((config or {}).get("exit") or {}).get("conditions") or []:
            if cond.get("type") == kind:
                return _f(cond.get("value"))
    except (TypeError, AttributeError):
        return None
    return None


def _day_change_pct(today_bars: list[dict], day_records: list[dict]) -> Optional[float]:
    """日内涨跌幅：最新分钟价 vs 昨收（日线最后一根的 close —— 盘中日线源没有今天）。"""
    day_closes = [_f(r.get("close")) for r in (day_records or [])]
    day_closes = [c for c in day_closes if c is not None]
    if not day_closes or not today_bars:
        return None
    price = _f(today_bars[-1].get("close"))
    prev_day = day_closes[-1]
    if price is None or not prev_day:
        return None
    return (price / prev_day - 1.0) * 100.0


# ==================== 盯盘面板（给模型看的那一屏数据） ====================

def build_panel(minute_bars: list[dict], day_records: list[dict],
                position: dict | None, config: dict | None) -> dict:
    """一次盯盘需要的全部客观数据 —— 模型只看这一屏，不需要也不会去取数。"""
    today_bars = _bars_of_today(minute_bars or [])
    price = _f(today_bars[-1].get("close")) if today_bars else None
    panel: dict = {"price": price,
                   "day_change_pct": _round(_day_change_pct(today_bars, day_records))}
    volumes = [_f(bar.get("volume")) or 0.0 for bar in today_bars[-20:-1]]
    last_volume = _f(today_bars[-1].get("volume")) if today_bars else 0.0
    panel["volume_ratio_20m"] = (round(last_volume / (sum(volumes) / len(volumes)), 2)
                                 if volumes and sum(volumes) > 0 and last_volume else None)
    day_closes = [c for c in (_f(r.get("close")) for r in (day_records or [])) if c is not None]
    mas = {f"ma_{w}": _round(_daily_ma(day_closes, w)) for w in _strategy_ma_windows(config)}
    panel["daily_ma"] = mas
    if price:
        panel["price_vs_ma_pct"] = {key: _round((price / value - 1.0) * 100.0)
                                    for key, value in mas.items() if value}
    # 技术面快照（2026-10-14）：RSI 与现价对 MA60 的位置——盯盘不该只看一分钟级
    # 的量价抖动，日线结构（超买超卖、中长期均线上下方）是事件解读的坐标系。
    try:
        ind = compute_indicators(day_records or [])
        idx = len(ind.get("dates") or []) - 1
        rsi_series = ind.get("rsi")
        if idx >= 0 and rsi_series is not None and idx < len(rsi_series) and rsi_series[idx] == rsi_series[idx]:
            panel["rsi"] = _round(float(rsi_series[idx]))
        ma60 = _daily_ma(day_closes, 60)
        if ma60 and price:
            panel["price_vs_ma60_pct"] = _round((price / ma60 - 1.0) * 100.0)
    except Exception:  # noqa: BLE001 —— 指标算不出来就少这两项，不挡盯盘
        pass
    pos_out: dict = {"shares": _f((position or {}).get("shares")) or 0.0}
    entry = _f((position or {}).get("entry_price"))
    if entry and price:
        pos_out["avg_cost"] = entry
        pos_out["unrealized_pnl_pct"] = _round((price / entry - 1.0) * 100.0)
        hwm = _f((position or {}).get("high_watermark"))
        if hwm:
            pos_out["drawdown_from_high_pct"] = _round((hwm - price) / hwm * 100.0)
    lots = (position or {}).get("lots")
    if isinstance(lots, list) and lots:
        pos_out["lots"] = [
            {k: lot.get(k) for k in ("opened", "age_days", "cost", "shares", "pnl_pct", "locked_today")
             if isinstance(lot, dict) and lot.get(k) is not None}
            for lot in lots[:8]
        ]
        pos_out["lots_note"] = "卖出按先进先出对冲批次；locked_today=True 的批次今日不可卖（T+1）"
    panel["position"] = pos_out
    return panel


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(value, digits)


# ==================== 盯盘交易员（1 次 LLM 调用） ====================

def _watch_system_prompt() -> str:
    return (
        "你是职业盯盘交易员，正在替一条既定策略盯一只 A 股。你的任务是："
        "在事件发生后的几秒内，判断**现在该不该动**。\n"
        "只输出一个 JSON 对象（不要任何其它文字）：\n"
        '{"action": "hold|buy|sell|partial_sell|flag", '
        '"size_fraction": 0.0-1.0, "confidence": 0.0-1.0, "reason": "一句话（不超过60字）"}\n'
        "动作语义：\n"
        "- hold：不动（事件不值得动，或证据含糊 —— **证据不足时必须 hold**）；\n"
        "- buy / sell：开仓 / 清仓（size_fraction 是目标仓位比例，sell 时 1.0=清仓）；\n"
        "- partial_sell：按 size_fraction 比例减仓（会取整到整手）；\n"
        "- flag：不下单，但把这条事件标记给收盘后的委员会重点复盘（用于你看不准、"
        "但收盘后值得深挖的情形）。\n"
        "判断顺序（职业操盘手的三层框架）：**先环境（market_context 的大盘/相对强弱、"
        "today_context 里委员会最近的结论）→ 再价格结构与量价（panel 的均线位置、RSI、量比）"
        "→ 最后才是事件本身**。逆大盘的异动与顺大盘的异动不是同一件事；与委员会最近结论"
        "冲突的动作几乎总是错的——那类情形选 flag 交复盘，不要替委员会改主意。\n"
        "today_context 是**执行状态而非行情信号**：trades 非空只说明今天已成交过，"
        "它不构成看多或看空的理由。它带来的唯一硬约束是 T+1——**此刻再买入，当日将"
        "无法离场**。是否值得再进场由证据决定，但决定再买时必须先回答：买进后到明天"
        "开盘前，这笔仓位的风险拿得住吗？答不住就 hold/flag。\n"
        "events_now 里 type=news_shock 表示该标的近 24 小时出现了 high impact 新闻，"
        "news_alert 字段是它的详情（方向/可信度/一句话解读）。新闻是信号不是命令："
        "可信度低或方向含糊的按 hold/flag 处理，不要替新闻放大结论。\n"
        "硬约束：你在授权边界内行动（allow_open=false 时不能 buy；allow_close=false 时"
        "不能 sell/partial_sell；max_position_pct 是仓位上限）；当前是 T+1 市场，"
        "当日买入的份额卖不出去；止损类卖出不用你管（代码已经比你快）。\n"
        "克制是美德：一天里大部分事件的正确动作是 hold。"
    )


def _watch_user_prompt(symbol: str, events: list[dict], panel: dict,
                       position: dict | None, authorization: dict | None,
                       config: dict | None, news_alert: dict | None = None,
                       experience: dict | None = None,
                       today_context: dict | None = None,
                       market_context: dict | None = None) -> str:
    payload = {
        "symbol": symbol,
        "strategy_intent": {
            "name": (config or {}).get("name", ""),
            "entry": (config or {}).get("entry"),
            "exit": (config or {}).get("exit"),
        },
        "events_now": events,
        "panel": panel,
        "position": {"shares": _f((position or {}).get("shares")) or 0.0,
                     "avg_cost": _f((position or {}).get("entry_price")),
                     "high_watermark": _f((position or {}).get("high_watermark"))},
        "authorization": authorization or {"note": "无授权信息，按最保守处理：不开新仓"},
    }
    if isinstance(news_alert, dict) and news_alert:
        payload["news_alert"] = news_alert
    # 经验回流（P4）：自己的历史战绩（轻量版）—— 动手前先看自己过去对错
    if isinstance(experience, dict) and experience:
        payload["experience"] = experience
    # 盘中语境（2026-10-14）：当日已成交 + 上次全委会结论 —— "今天动过手吗、
    # 委员会最近怎么说"，缺了它盯盘会在不知道刚卖过的情况下把仓位买回来（实测踩过）
    if isinstance(today_context, dict) and today_context:
        payload["today_context"] = today_context
    # 大盘语境（2026-10-14）：指数/成交/涨跌家数（全市场共用 TTL 缓存，近零成本）
    if isinstance(market_context, dict) and market_context:
        payload["market_context"] = market_context
    return json.dumps(payload, ensure_ascii=False, default=str)


def parse_watch_decision(text: str) -> dict:
    """解析盯盘输出（确定性、保守优先）：读不懂 → hold + 警告，绝不猜。"""
    body = str(text or "")
    candidate = None
    start, end = body.find("{"), body.rfind("}")
    if 0 <= start < end:
        candidate = body[start:end + 1]
    if candidate:
        try:
            payload = json.loads(candidate)
        except ValueError:
            payload = None
    else:
        payload = None

    warnings: list[str] = []
    if not isinstance(payload, dict):
        warnings.append("盯盘输出不是 JSON，按 hold 处理")
        return _watch_result(ACTION_HOLD, 0.0, None, "输出无法解析", warnings)

    action = str(payload.get("action") or "").strip().lower()
    if action not in WATCH_ACTIONS:
        warnings.append(f"未知动作 {action!r}，按 hold 处理")
        action = ACTION_HOLD
    size = _f(payload.get("size_fraction"))
    if size is None or size < 0 or size > 1:
        if size is not None:
            warnings.append(f"size_fraction={size!r} 越界，按 0 处理")
        size = 0.0
    confidence = _f(payload.get("confidence"))
    if confidence is not None and not (0 <= confidence <= 1):
        confidence = None
    reason = str(payload.get("reason") or "").strip()[:120]
    return _watch_result(action, size, confidence, reason, warnings)


def _watch_result(action: str, size: float, confidence: Optional[float],
                  reason: str, warnings: list[str]) -> dict:
    return {"action": action, "size_fraction": round(size, 4),
            "confidence": confidence, "reason": reason,
            "warnings": warnings, "degraded": False}


def intraday_decide(symbol: str, events: list[dict], position: dict | None,
                    authorization: dict | None, config: dict | None,
                    minute_bars: list[dict], day_records: list[dict],
                    completion: Optional[Callable] = None,
                    news_alert: dict | None = None,
                    experience: dict | None = None,
                    today_context: dict | None = None,
                    market_context: dict | None = None) -> dict:
    """盯盘交易员的一次判断。**永不抛异常**：任何故障都退化成可执行的结论。

    返回 {action, size_fraction, confidence, reason, warnings, degraded, watch_tokens}：
    - degraded=True 表示**根本没有问成模型**（服务不可用/调用异常）——
      结算侧应回落到"规则+授权"的现行路径，盘中不因 LLM 故障瘫痪；
    - degraded=False + action=hold 表示模型被问过且决定不动（含输出读不懂）。

    `news_alert`（P3）：news_shock 事件的详情（标题/方向/可信度/一句话解读），
    由 Java 检测后随请求带来；None = 本次盯盘与新闻无关（原行为）。

    `experience`（P4，经验回流）：这条策略自己的成交成绩单（轻量版）——
    盯盘交易员在"这次事件值不值得动手"之前先看自己的过往战绩。
    None = 不带（fail-open，与 news_alert 同一条纪律）。
    """
    import llm_service

    panel = build_panel(minute_bars, day_records, position, config)
    if not events:
        return {**_watch_result(ACTION_HOLD, 0.0, None, "无事件", []), "watch_tokens": 0}

    checker = getattr(llm_service, "_is_available", None)
    if callable(checker):
        try:
            if not checker():
                return {**_watch_result(ACTION_HOLD, 0.0, None, "模型服务不可用",
                                        ["盯盘前探测：LLM 不可用"]),
                        "degraded": True, "watch_tokens": 0}
        except Exception:  # noqa: BLE001 —— 探测失败不应阻止尝试
            pass

    call = completion or llm_service.chat_completion
    try:
        choice = call(
            [{"role": "system", "content": _watch_system_prompt()},
             {"role": "user", "content": _watch_user_prompt(symbol, events, panel,
                                                           position, authorization, config,
                                                           news_alert, experience,
                                                           today_context, market_context)}],
            temperature=0.1, max_tokens=WATCH_MAX_TOKENS)
    except Exception as exc:  # noqa: BLE001 —— 调用异常 = 没问成 → 降级
        logger.warning("盯盘调用失败 symbol=%s: %s", symbol, exc)
        return {**_watch_result(ACTION_HOLD, 0.0, None, "调用失败",
                                [f"盯盘调用异常（{type(exc).__name__}）"]),
                "degraded": True, "watch_tokens": 0}

    content = ""
    try:
        content = str((((choice or {}).get("message") or {}).get("content")) or "")
    except Exception:  # noqa: BLE001
        content = ""
    if not content.strip():
        return {**_watch_result(ACTION_HOLD, 0.0, None, "模型返回空内容",
                                ["盯盘调用返回空"]),
                "degraded": True, "watch_tokens": 0}
    if "AI 服务暂不可用" in content:
        # chat_completion 不可用时返回占位文本而不抛异常 —— 那是"没问成"
        return {**_watch_result(ACTION_HOLD, 0.0, None, "模型服务不可用",
                                ["盯盘：模型服务占位响应"]),
                "degraded": True, "watch_tokens": 0}

    tokens = 0
    try:
        from agent import metering
        tokens = int(metering.parse_usage(choice if isinstance(choice, dict) else None)
                     .get("total_tokens", 0))
    except Exception:  # noqa: BLE001
        tokens = 0
    result = parse_watch_decision(content)
    return {**result, "watch_tokens": tokens}


def describe() -> dict:
    """给 /health 与调试用：事件类型、灵敏度、动作集。"""
    return {
        "actions": list(WATCH_ACTIONS),
        "sensitivity": {
            "sharp_move_1m_pct": SHARP_MOVE_1M_PCT,
            "sharp_move_5m_pct": SHARP_MOVE_5M_PCT,
            "volume_spike_multiple": VOLUME_SPIKE_MULTIPLE,
            "near_stop_pct": NEAR_STOP_PCT,
            "near_limit_pct": NEAR_LIMIT_PCT,
            "gap_pct": GAP_PCT,
        },
        "max_tokens_per_call": WATCH_MAX_TOKENS,
        "llm_calls_per_decision": 1,
    }
