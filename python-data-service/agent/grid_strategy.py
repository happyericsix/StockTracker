# -*- coding: utf-8 -*-
"""自适应网格策略引擎（高波动震荡市专用，2026-10-17）。

<h3>为什么它不进 strategy_schema 的条件 DSL</h3>
现有 DSL 是"入场/出场条件列表"（均线交叉、RSI……），表达的是**事件驱动的一次性进出**；
网格策略的本体是**一张挂单梯子**：同一时刻在多个价位同时挂着买与卖，
"高抛低吸"发生在格子之间而不是条件成立的那一根 bar 上。
把挂单梯子硬塞进条件列表，等于用"若干次 Ma 交叉"去近似"一张网格"——
形状不对，怎么写都是近似。所以它是独立引擎，但**共用同一套执行契约**：
次日开盘 + 滑点成交、整手、佣金下限 5 元、印花税/过户费按官方生效日期表、
涨跌停挡单、T+1（与 strategy_engine.run_backtest_realistic 同一口径）。

<h3>结构化参数（测评一的合格标准：不是一段模糊描述）</h3>
- 基准价 base_price：建网锚点（首根可交易 bar 收盘，或手工指定）；
- 网格数量 grid_count：上下各 grid_count/2 格，等差排列；
- 动态间距公式：spacing_pct = atr_multiplier × ATR%(atr_window)，
  即"一格的距离 = k 倍 ATR"；波动大格变宽（少交易、单格利润厚）、波动小格变窄；
- 每格投入 per_grid_capital_pct：建网时一次性折算成每格股数（整手对齐）；
- 破网应对 breakout_policy：突破上/下边界的封闭策略集（halt / flatten / recenter）；
- 硬风控（确定性代码，不经模型裁量）：max_drawdown_stop_pct（组合级回撤止损，
  触发后清仓并永久停买）、max_position_pct（单票仓位上限，买单在这被夹紧）。
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Literal, Optional

import numpy as np
from pydantic import BaseModel, Field, model_validator, ValidationError

from agent import market_rules as mr

GRID_ENGINE_VERSION = "grid_v1"

# ---------------------------------------------------------------------------
# 参数 schema（校验消息中文——与 strategy_schema 同一纪律：错误要能直接给人看）
# ---------------------------------------------------------------------------


class GridConfig(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    name: str = "自适应网格"
    symbol: str
    initial_capital: float = 100000.0

    # ---- 网格核心 ----
    #: 基准价模式：anchor=在首个可交易 bar 以收盘价建网；manual=用 base_price 指定
    base_price_mode: Literal["anchor", "manual"] = "anchor"
    base_price: Optional[float] = None
    #: 上下各多少格（总格数 = 2×grid_count）；边界 = 基准 ± grid_count×间距
    grid_count: int = 5
    #: ATR 窗口与间距公式 spacing_pct = atr_multiplier × ATR%
    atr_window: int = 14
    atr_multiplier: float = 1.0
    #: 每格投入资金（占初始资金 %）；建网时折算成固定每格股数
    per_grid_capital_pct: float = 8.0

    # ---- 破网应对（封闭策略集）----
    #: halt      = 出界暂停买入（上破防追、下破防"破网不断买入"），回到区间后仍需
    #:             再等 recenter_cooldown_bars 根确认——V 型反弹的第一段 dips 不接刀
    #: flatten   = 破网即清仓，冷却后按新价重锚
    #: recenter  = 破网后冷却 recenter_cooldown_bars 根，仍在外则按现价重锚（保留持仓）
    breakout_policy: Literal["halt", "flatten", "recenter"] = "halt"
    recenter_cooldown_bars: int = 3

    # ---- 波动率复审（"动态"的第二条链路：不漂移、只在阈值处离散重建）----
    #: 每 atr_review_bars 根复审一次 ATR；较建网时偏离 ≥ spacing_rebuild_change_pct% 才重建
    atr_review_bars: int = 10
    spacing_rebuild_change_pct: float = 30.0

    # ---- 硬风控（确定性代码执行；模型/委员会不可绕过）----
    #: 组合高点回撤 ≥ 该值 → 清仓并永久停买（风险事件，与网格信号分开留痕）
    max_drawdown_stop_pct: float = 15.0
    #: 单票市值 / 净值 上限；买单在信号与成交两层都被它夹紧
    max_position_pct: float = 60.0

    commission_pct: float = 0.1
    slippage_pct: float = 0.1

    @model_validator(mode="after")
    def _check(self):
        if self.base_price_mode == "manual" and not (self.base_price and self.base_price > 0):
            raise ValueError("manual 模式必须给出正的 base_price（基准价）")
        if self.grid_count < 1 or self.grid_count > 50:
            raise ValueError("grid_count 需在 1-50（单侧格数；太大等于无限挂单）")
        if self.atr_window < 2 or self.atr_window > 120:
            raise ValueError("atr_window 需在 2-120")
        if self.atr_multiplier <= 0:
            raise ValueError("atr_multiplier 必须为正（一格 = k 倍 ATR）")
        if not (0 < self.per_grid_capital_pct <= 50):
            raise ValueError("per_grid_capital_pct 需在 (0, 50]（单格资金占比）")
        if self.recenter_cooldown_bars < 1:
            raise ValueError("recenter_cooldown_bars 至少为 1")
        if not (5 <= self.max_drawdown_stop_pct <= 50):
            raise ValueError("max_drawdown_stop_pct 需在 5-50（%），过小会被日常波动打死")
        if not (10 <= self.max_position_pct <= 100):
            raise ValueError("max_position_pct 需在 10-100（%）")
        return self


def validate_grid_config(data: dict):
    """与 strategy_schema.validate_strategy_config 同形状：(cfg, None) 或 (None, 中文错误)。"""
    try:
        return GridConfig.model_validate(data), None
    except ValidationError as e:
        msgs = []
        for err in e.errors()[:3]:
            raw = err.get("msg", "格式不正确")
            if raw.startswith("Value error,"):
                raw = raw[len("Value error,"):].strip()
            msgs.append(raw)
        if len(e.errors()) > 3:
            msgs.append(f"……还有 {len(e.errors()) - 3} 处问题")
        return None, "；".join(msgs)


def grid_signature(config: dict | GridConfig) -> str:
    """配置签名（sha256）：同一份配置在任何机器上签名一致——行为一致性测试的锚点。"""
    if isinstance(config, GridConfig):
        config = config.model_dump()
    canon = json.dumps(config, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 纯函数：ATR、网格几何（可独立单测；测评一要的"公式"就住在这里）
# ---------------------------------------------------------------------------


def compute_atr_pct(highs, lows, closes, window: int) -> np.ndarray:
    """Wilder ATR 占收盘价的百分比（as-of：第 i 根只用到 ≤ i 的 bar，无未来函数）。

    测评三的"过拟合诊断/Walk-Forward"要求任何指标都能回答"当时看得到什么"，
    所以 ATR 用递推式（RMA, alpha=1/w）而不是全样本滚动——全样本窗口的边界
    会把后面的波动率悄悄漏给前面。
    """
    n = len(closes)
    out = np.full(n, np.nan)
    if n < window + 1:
        return out
    tr = np.zeros(n)
    tr[0] = float(highs[0]) - float(lows[0])
    for i in range(1, n):
        tr[i] = max(
            float(highs[i]) - float(lows[i]),
            abs(float(highs[i]) - float(closes[i - 1])),
            abs(float(lows[i]) - float(closes[i - 1])),
        )
    atr = float(np.mean(tr[1:window + 1]))  # 种子：前 w 个 TR 的均值（跳过首根不完整 TR）
    out[window] = atr / float(closes[window]) * 100.0
    k = 1.0 / window
    for i in range(window + 1, n):
        atr = tr[i] * k + atr * (1 - k)
        out[i] = atr / float(closes[i]) * 100.0
    return out


def grid_levels(base: float, spacing_pct: float, count: int) -> list[float]:
    """等差网格价位：base ± 1..count × spacing（spacing_pct 是相对 base 的百分数）。"""
    step = base * spacing_pct / 100.0
    levels = [base + j * step for j in range(-count, count + 1)]
    return [round(x, 2) for x in levels]


def describe_grid(cfg: GridConfig, base: float, atr_pct: float) -> dict:
    """建网那一刻的**结构化参数快照**（测评一的合格标准输出）。"""
    spacing_pct = cfg.atr_multiplier * atr_pct
    levels = grid_levels(base, spacing_pct, cfg.grid_count)
    per_grid_capital = cfg.initial_capital * cfg.per_grid_capital_pct / 100.0
    lot = mr.lot_for(cfg.symbol)
    shares = int(per_grid_capital / (base * lot)) * lot
    return {
        "base_price": round(base, 2),
        "atr_pct": round(atr_pct, 4),
        "spacing_pct": round(spacing_pct, 4),          # 动态间距公式：k×ATR%
        "spacing_abs": round(base * spacing_pct / 100.0, 2),
        "grid_count_per_side": cfg.grid_count,
        "upper_boundary": round(levels[-1], 2),
        "lower_boundary": round(levels[0], 2),
        "per_grid_capital": round(per_grid_capital, 2),
        "per_grid_shares": shares,
        "levels": levels,
        "breakout_policy": cfg.breakout_policy,
        "max_drawdown_stop_pct": cfg.max_drawdown_stop_pct,
        "max_position_pct": cfg.max_position_pct,
    }


# ---------------------------------------------------------------------------
# 回测引擎（真实化执行；返回键与 run_backtest_realistic 对齐 + grid_* 扩展）
# ---------------------------------------------------------------------------

# 成交原因（封闭集）：留痕里"为什么成交"不许自由发挥
REASON_GRID_BUY = "grid_buy_level"
REASON_GRID_SELL = "grid_sell_level"
REASON_FLATTEN_BREAKOUT = "grid_breakout_flatten"
REASON_RISK_STOP = "risk_max_drawdown_stop"

# 跳过原因（封闭集）
SKIP_HALTED = "grid_buys_halted_breakout"
SKIP_POSITION_CAP = "grid_position_cap"
SKIP_NO_CASH = "grid_insufficient_cash"
SKIP_LOT_TOO_EXPENSIVE = "grid_lot_too_expensive"
SKIP_T1 = "grid_t1_guard"
SKIP_LIMIT = "grid_limit_blocked"
SKIP_RISK_STOPPED = "risk_stopped"
SKIP_NO_SELL_LOT = "grid_no_sell_lot"
#: 该格已有未平仓持仓（含挂单中的买）：经典网格一格一手，卖掉之前不重复接
SKIP_LEVEL_OCCUPIED = "grid_level_occupied"


def run_grid_backtest(
    config: dict | GridConfig,
    records: list[dict],
    *,
    start_date: str = "",
    lot_size: int | None = None,
    limit_up_pct: float | None = None,
) -> dict:
    """自适应网格回测。执行口径与 run_backtest_realistic 完全一致：
    bar i 收盘出信号、bar i+1 开盘 ± 滑点成交（tick 0.01 取整）、整手、
    佣金下限 5 元、印花税/过户费按成交日官方表、涨跌停挡单、T+1。

    ``start_date``：评测窗口起点——之前的 bar 只作为 ATR 预热，不建网不交易。
    这是"往前推 N 个交易日评测、更早行情只当上下文"的口径载体。
    """
    if isinstance(config, GridConfig):
        cfg = config
    else:
        cfg, err = validate_grid_config(config)
        if err:
            return {"symbol": (config or {}).get("symbol", ""), "error": err,
                    "equity_curve": [], "trade_log": []}
    if len(records) <= cfg.atr_window + 2:
        return {"symbol": cfg.symbol, "error": "data insufficient",
                "equity_curve": [], "trade_log": []}

    opens = np.array([float(r["open"]) for r in records], dtype=float)
    closes = np.array([float(r["close"]) for r in records], dtype=float)
    highs = np.array([float(r["high"]) for r in records], dtype=float)
    lows = np.array([float(r["low"]) for r in records], dtype=float)
    dates = [str(r.get("date", i)) for i, r in enumerate(records)]
    n = len(records)

    lot = int(lot_size or mr.lot_for(cfg.symbol))
    limit = float(limit_up_pct if limit_up_pct is not None else _default_limit_pct(cfg.symbol))
    comm = cfg.commission_pct / 100.0
    slip = cfg.slippage_pct / 100.0
    atr_pct = compute_atr_pct(highs, lows, closes, cfg.atr_window)

    # 评测窗口起点：start_date 之前只预热（ATR），不建网
    first_bar = cfg.atr_window + 1
    if start_date:
        idx = next((i for i, d in enumerate(dates) if d >= start_date), None)
        if idx is None:
            return {"symbol": cfg.symbol, "error": f"start_date {start_date} 不在数据范围内",
                    "equity_curve": [], "trade_log": []}
        first_bar = max(first_bar, idx)

    # ---- 状态 ----
    cash = float(cfg.initial_capital)
    inventory: list[dict] = []      # 持仓格：{level, shares, fill_bar, cost, queued}
    pending: list[dict] = []        # bar i 收盘决定、bar i+1 开盘成交的单子
    grid: dict | None = None        # 当前网格（describe_grid 的快照 + 运行态字段）
    outside_suspended = False       # 出界暂停买入（halt 策略；回区间后进入确认期）
    reentry_confirm_until = -1      # 回区间确认期截止 bar（halt 策略）：之前不放行买单
    outside_logged = False          # 本次出界是否已留痕（一次出界一条事件，不逐日刷屏）
    risk_stopped = False            # 硬回撤止损已触发（终态）
    hwm_equity = float(cfg.initial_capital)
    trades: list[dict] = []
    equity_curve: list[dict] = []
    events: list[dict] = []
    builds: list[dict] = []
    skip_counter: dict[str, int] = {}
    funding_note = None

    def note_skip(reason: str):
        skip_counter[reason] = skip_counter.get(reason, 0) + 1

    def fill_price(idx: int, side: str) -> float:
        raw = float(opens[idx]) * (1.0 + slip if side == "buy" else 1.0 - slip)
        return mr.tick_round(raw, round_up=(side == "buy"))

    def limit_blocked(idx: int, side: str) -> bool:
        if idx <= 0:
            return False
        prev_close = float(closes[idx - 1])
        o = float(opens[idx])
        if side == "buy" and o >= prev_close * (1.0 + limit / 100.0):
            return True
        if side == "sell" and o <= prev_close * (1.0 - limit / 100.0):
            return True
        return False

    def build_grid(i: int, cause: str, keep_base: bool = False):
        """建网/重锚：间距公式 = atr_multiplier × ATR%(i)（测评一要求的动态公式）。

        ``keep_base``：波动率复审的重建**只改间距、锚点不动**——锚点跟着价格跑
        等于网格追着行情补单（实测在 -6%/日的暴跌里，每次复审都把网往下搬，
        价格永远"在区间内"，halt 的防追跌语义被架空）。价格的永久迁移交给
        breakout → recenter 链路（带冷却地显式决策），不借波动率复审之手。
        """
        nonlocal grid, outside_suspended, outside_logged, reentry_confirm_until
        base = float(closes[i])
        if cfg.base_price_mode == "manual" and cfg.base_price:
            base = float(cfg.base_price)
        if keep_base and grid is not None:
            base = float(grid["base"])
        spacing_pct = cfg.atr_multiplier * float(atr_pct[i])
        snapshot = describe_grid(cfg, base, float(atr_pct[i]))
        grid = {
            **snapshot,
            "base": base,
            "spacing_pct": spacing_pct,
            "build_bar": i,
            "atr_pct_at_build": float(atr_pct[i]),
            "last_review_bar": i,
        }
        outside_suspended = False
        outside_logged = False
        reentry_confirm_until = -1
        builds.append({"date": dates[i], "cause": cause, **snapshot})
        events.append({"date": dates[i], "event": "grid_build" if cause == "init" else f"grid_rebuild_{cause}",
                       "base": snapshot["base_price"], "spacing_pct": snapshot["spacing_pct"]})

    def queue_flatten(i: int, reason: str):
        # queued 守卫：清仓单挂出后到成交前，隔日的再次破网不得把同一手重复挂卖
        todo = [l for l in inventory if not l.get("queued")]
        for lot_state in todo:
            lot_state["queued"] = True
            pending.append({"side": "sell", "shares": lot_state["shares"],
                            "lot": lot_state, "level": lot_state["level"], "reason": reason})
        if todo:
            events.append({"date": dates[i], "event": "flatten", "reason": reason,
                           "shares": sum(l["shares"] for l in todo)})

    def position_cap_shares(shares_now: float, price: float, equity: float) -> int:
        """仓位上限能容下的最大增仓股数（信号与成交两层都调它——口径只有一份）。"""
        cap_value = equity * cfg.max_position_pct / 100.0
        allowed = int((cap_value - shares_now * price) // (price * lot)) * lot
        return max(0, allowed)

    # ---- 主循环 ----
    for i in range(first_bar, n):
        price = float(closes[i])

        # (1) 开盘：执行上一根收盘挂出的单子（T+1 由 fill_bar 守卫显式保证）
        still_pending = []
        for order in pending:
            side = order["side"]
            if risk_stopped and side == "buy":
                note_skip(SKIP_RISK_STOPPED)
                continue
            lot_state = order.get("lot")
            if side == "sell" and lot_state is not None and lot_state.get("fill_bar", -1) >= i:
                still_pending.append(order)   # T+1：这手今天不能卖，顺延一天（防御层）
                note_skip(SKIP_T1)
                continue
            if limit_blocked(i, side):
                still_pending.append(order)   # 涨跌停挡单：顺延，不撤销
                note_skip(SKIP_LIMIT)
                continue
            o = fill_price(i, side)
            if o <= 0:
                continue
            equity_now = cash + sum(l["shares"] for l in inventory) * float(opens[i])
            if side == "buy":
                shares = int(order["shares"])
                # 成交层再夹一次仓位上限（信号层按昨收估过，开盘价可能更高）
                allowed = position_cap_shares(
                    sum(l["shares"] for l in inventory), o, equity_now)
                if shares > allowed:
                    shares = allowed
                if shares <= 0:
                    note_skip(SKIP_POSITION_CAP)
                    continue
                while shares > 0:
                    fee = _comm_fee(shares * o, comm) + shares * o * _transfer_on(dates[i]) / 100.0
                    if shares * o + fee <= cash:
                        break
                    shares -= lot
                if shares <= 0:
                    note_skip(SKIP_NO_CASH)
                    if funding_note is None:
                        funding_note = f"{dates[i]}：一格（{lot} 股 × {o:.2f} 元）都买不起，现金不足"
                    continue
                fee = _comm_fee(shares * o, comm) + shares * o * _transfer_on(dates[i]) / 100.0
                cash -= shares * o + fee
                inventory.append({"level": order.get("level", o), "shares": float(shares),
                                  "fill_bar": i, "cost": shares * o + fee})
                trades.append({"date": dates[i], "side": "buy", "price": round(o, 4),
                               "shares": float(shares), "amount": round(shares * o, 2),
                               "fee": round(fee, 2), "reason": order.get("reason", REASON_GRID_BUY),
                               "fill_mode": "next_open", "grid_level": order.get("level")})
            else:
                shares = int(order["shares"])
                if lot_state is not None:
                    shares = min(shares, int(lot_state["shares"]))
                fee = _comm_fee(shares * o, comm) + shares * o * _transfer_on(dates[i]) / 100.0
                stamp = shares * o * _stamp_on(dates[i]) / 100.0
                cash += shares * o - fee - stamp
                if lot_state is not None:
                    lot_state["shares"] -= shares
                    if lot_state["shares"] <= 0 and lot_state in inventory:
                        inventory.remove(lot_state)
                trades.append({"date": dates[i], "side": "sell", "price": round(o, 4),
                               "shares": float(shares), "amount": round(shares * o, 2),
                               "fee": round(fee + stamp, 2), "reason": order.get("reason", REASON_GRID_SELL),
                               "fill_mode": "next_open", "grid_level": order.get("level")})
        pending = still_pending

        shares_now = sum(l["shares"] for l in inventory)
        equity = cash + shares_now * price
        hwm_equity = max(hwm_equity, equity)

        # (2) 硬回撤止损（组合级）：确定性代码，先于一切网格逻辑
        dd = (hwm_equity - equity) / hwm_equity * 100.0 if hwm_equity > 0 else 0.0
        if not risk_stopped and dd >= cfg.max_drawdown_stop_pct:
            risk_stopped = True
            events.append({"date": dates[i], "event": "risk_stop",
                           "drawdown_pct": round(dd, 2),
                           "threshold_pct": cfg.max_drawdown_stop_pct})
            queue_flatten(i, REASON_RISK_STOP)

        # (3) 网格管理（建网/复审/破网/穿越挂单）——只在收盘价上判定（口径单一可复算）
        if i >= first_bar and not risk_stopped and not math.isnan(atr_pct[i]):
            buys_blocked = outside_suspended or i < reentry_confirm_until
            if grid is None:
                build_grid(i, "init")
            else:
                upper, lower = grid["upper_boundary"], grid["lower_boundary"]
                outside = price > upper or price < lower
                direction = "up" if price > upper else ("down" if price < lower else None)
                if outside:
                    if cfg.breakout_policy == "halt" and not outside_logged:
                        # 下破 = 暂停买入（防"破网不断买入"）；上破 = 防追高。
                        # 出界期间本来就没有更低的格子可挂，这条留痕的意义在于
                        # **回区间之后**还要过冷却确认期才恢复接货。
                        outside_suspended = True
                        outside_logged = True
                        events.append({"date": dates[i], "event": f"breakout_{direction}",
                                       "policy": "halt", "price": round(price, 2),
                                       "boundary": upper if direction == "up" else lower})
                    elif cfg.breakout_policy == "flatten" and inventory:
                        queue_flatten(i, REASON_FLATTEN_BREAKOUT)
                        outside_logged = True
                        events.append({"date": dates[i], "event": f"breakout_{direction}",
                                       "policy": "flatten", "price": round(price, 2)})
                    elif (cfg.breakout_policy == "recenter"
                          and i - grid["build_bar"] >= cfg.recenter_cooldown_bars):
                        build_grid(i, f"breakout_{direction}")
                elif outside_suspended:
                    # 回到区间：进入冷却确认期（recenter_cooldown_bars 根内不放行买单）
                    outside_suspended = False
                    outside_logged = False
                    reentry_confirm_until = i + cfg.recenter_cooldown_bars
                    events.append({"date": dates[i], "event": "re_enter_range",
                                   "confirm_until_bar": i + cfg.recenter_cooldown_bars})
                buys_blocked = outside_suspended or i < reentry_confirm_until
                # 波动率复审：不漂移，只在阈值处离散重建（间距公式的第二条生效链路）；
                # 重建保持锚点不变（见 build_grid 的 keep_base 说明）
                if (grid is not None and not outside
                        and i - grid["last_review_bar"] >= cfg.atr_review_bars):
                    grid["last_review_bar"] = i
                    change = abs(float(atr_pct[i]) / grid["atr_pct_at_build"] - 1.0) * 100.0
                    if change >= cfg.spacing_rebuild_change_pct:
                        build_grid(i, "vol_regime", keep_base=True)

            # 穿越挂单：收盘价从 prev → cur 跨过某格，次日开盘按该格方向成交。
            # 建网后**至少隔一根**才允许触发（i ≥ build_bar+2）：穿越用的 prev 必须
            # 也是建网后的 bar。否则基准价 round 到分位后常略低于建网收盘，
            # 第一根就把基准格判成"下穿"——对着价格已经离开的位置补挂单
            # （实测：按高于现价的格子买入，高价货从此搁浅，no_sell_lot 刷屏）。
            if grid is not None and i - grid["build_bar"] >= 2:
                prev_close = float(closes[i - 1])
                cur_close = price
                lo_c, hi_c = min(prev_close, cur_close), max(prev_close, cur_close)
                # 一格一手：已持有（含买单挂出未成交）的格不再重复接——没有这条，
                # 震荡市的每个周期都会在最高格上再堆一手（实测 4 手全压在同一价位，
                # 全部搁浅）。格子占用到卖出为止；重锚换网格后是新的价位集合。
                occupied = ({l["level"] for l in inventory}
                            | {o["level"] for o in pending if o["side"] == "buy"})
                for level in grid["levels"]:
                    if not (lo_c <= level <= hi_c):
                        continue
                    if prev_close > level >= cur_close:      # 下穿 → 低吸
                        if buys_blocked:
                            note_skip(SKIP_HALTED)
                            continue
                        if level in occupied:
                            note_skip(SKIP_LEVEL_OCCUPIED)
                            continue
                        shares = grid["per_grid_shares"]
                        if shares <= 0:
                            # 一格资金买不起一手（茅台 100 股 ≈ 14 万 vs 每格 1 万）：
                            # 这是配置/资金问题，不是仓位上限——归因必须分开，否则
                            # 用户看到的是"被风控拦了"，实际是"根本买不起"。
                            note_skip(SKIP_LOT_TOO_EXPENSIVE)
                            continue
                        equity_est = cash + shares_now * price
                        allowed = position_cap_shares(shares_now, price, equity_est)
                        if shares > allowed:
                            shares = allowed
                        if shares <= 0:
                            note_skip(SKIP_POSITION_CAP)
                            continue
                        pending.append({"side": "buy", "shares": shares,
                                        "level": level, "reason": REASON_GRID_BUY})
                    elif prev_close < level <= cur_close:    # 上穿 → 高抛
                        # 卖"这一格下面最近的一手"（网格配对：卖上格、清下格的货）。
                        # queued 守卫与清仓单同源：挂出未成交的手不能再被选为卖出目标
                        # ——同一根 bar 上穿多格、或挂单等待期的再次上穿，都会把
                        # 同一手卖两遍（现金凭空记两次账，整个账本就此失真）。
                        candidates = [l for l in inventory
                                      if l["shares"] > 0 and not l.get("queued")]
                        target = None
                        if candidates:
                            below = [l for l in candidates if l["level"] < level]
                            target = max(below, key=lambda l: l["level"]) if below else None
                        if target is None:
                            note_skip(SKIP_NO_SELL_LOT)
                            continue
                        target["queued"] = True
                        pending.append({"side": "sell", "shares": int(target["shares"]),
                                        "level": level, "lot": target,
                                        "reason": REASON_GRID_SELL})

        equity_curve.append({
            "date": dates[i], "equity": round(equity, 2), "close": round(price, 4),
            "cash": round(cash, 2), "shares": shares_now,
            "position_pct": round(shares_now * price / equity * 100.0, 2) if equity > 0 else 0.0,
            "drawdown_pct": round(dd, 2),
            "atr_pct": round(float(atr_pct[i]), 4) if not math.isnan(atr_pct[i]) else None,
            "risk_stopped": risk_stopped,
        })

    # ---- 收尾 ----
    final = cash + sum(l["shares"] for l in inventory) * float(closes[-1])
    start_price = float(closes[first_bar])
    bh_final = cfg.initial_capital / start_price * float(closes[-1])
    total_return = final / float(cfg.initial_capital) - 1
    bh_return = bh_final / float(cfg.initial_capital) - 1
    equity_values = [row["equity"] for row in equity_curve]

    from agent import perf_metrics as pm   # 局部 import：metrics 模块不反向依赖本引擎
    return {
        "symbol": cfg.symbol,
        "engine": GRID_ENGINE_VERSION,
        "config_signature": grid_signature(cfg),
        "execution": "realistic",
        "initial_capital": cfg.initial_capital,
        "final_value": round(final, 2),
        "total_return_pct": round(total_return * 100, 2),
        "buy_and_hold_return_pct": round(bh_return * 100, 2),
        "excess_return_pct": round((total_return - bh_return) * 100, 2),
        "max_drawdown_pct": pm.max_drawdown_stats(equity_values)["max_dd_pct"],
        "sharpe_ratio": pm.sharpe_ratio(equity_values),
        "trade_count": len(trades),
        "closed_trades": len(_closed_round_trips(trades)),
        "rules": {
            "lot_size": lot, "limit_pct": limit,
            "stamp_tax_pct": _stamp_on(dates[-1]) if dates else 0.05,
            "transfer_fee_pct": _transfer_on(dates[-1]) if dates else 0.001,
            "commission_pct": cfg.commission_pct, "min_commission_yuan": 5.0,
            "fill": "next_open", "signal": "close_cross",
            "fee_source": "官方生效日期表（agent/market_rules.py）",
            "engine_version": GRID_ENGINE_VERSION,
        },
        "funding_note": funding_note,
        "skip_counter": skip_counter,
        "grid_builds": builds,
        "grid_events": events,
        "risk_stopped": risk_stopped,
        "open_inventory": [
            {"level": l["level"], "shares": l["shares"], "cost": round(l["cost"], 2)}
            for l in inventory],
        "trade_log": trades,
        "equity_curve": equity_curve,
        "benchmark_equity_curve": [
            {"date": row["date"], "equity": round(cfg.initial_capital / start_price * row["close"], 2),
             "close": row["close"]} for row in equity_curve],
        "start_date": dates[first_bar] if equity_curve else None,
        "end_date": dates[-1] if equity_curve else None,
        "warmup_bars": first_bar,
        "data_points": len(records),
    }


def _closed_round_trips(trades: list[dict]) -> list[dict]:
    """FIFO 配对的已平仓回合（perf_metrics.round_trips 的本地复用入口）。"""
    from agent import perf_metrics as pm
    return pm.round_trips(trades)


def _default_limit_pct(symbol: str) -> float:
    s = (symbol or "").upper()
    if s.startswith(("688", "689", "300", "301", "302")):
        return 20.0
    if s.startswith(("43", "83", "87", "88", "92")):
        return 30.0
    return 10.0


def _comm_fee(turnover: float, comm_rate: float) -> float:
    if comm_rate <= 0 or turnover <= 0:
        return 0.0
    return max(turnover * comm_rate, 5.0)


def _stamp_on(d: str) -> float:
    try:
        return mr.rate_pct(mr.STAMP_TAX_SELL, d[:10])
    except Exception:  # noqa: BLE001 —— 合成日期查表失败按当前费率
        return 0.05


def _transfer_on(d: str) -> float:
    return mr.rate_pct(mr.TRANSFER_FEE, d[:10] if d else None)
