from __future__ import annotations
import numpy as np
from agent.strategy_schema import StrategyConfig

def _sma(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        out[w-1:] = np.convolve(a, np.ones(w)/w, mode="valid")
    return out

def _ema(a, w):
    out = np.full(len(a), np.nan)
    if len(a) == 0:
        return out
    k = 2 / (w + 1)
    prev = a[0]
    out[0] = prev
    for i in range(1, len(a)):
        prev = a[i] * k + prev * (1 - k)
        out[i] = prev
    return out

def _rsi(a, w=14):
    out = np.full(len(a), np.nan)
    if len(a) <= w:
        return out
    deltas = np.diff(a)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g = gains[:w].mean()
    avg_l = losses[:w].mean()
    for i in range(w, len(a)):
        avg_g = (avg_g * (w - 1) + gains[i-1]) / w
        avg_l = (avg_l * (w - 1) + losses[i-1]) / w
        out[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out

def compute_indicators(records):
    closes = np.array([float(r["close"]) for r in records], dtype=float)
    highs = np.array([float(r["high"]) for r in records], dtype=float)
    lows = np.array([float(r["low"]) for r in records], dtype=float)
    dates = [r.get("date", str(i)) for i, r in enumerate(records)]
    ind = {"closes": closes, "highs": highs, "lows": lows, "dates": dates}
    for w in (5, 10, 20, 60):
        ind[f"ma_{w}"] = _sma(closes, w)
    ind["rsi"] = _rsi(closes, 14)
    dif = _ema(closes, 12) - _ema(closes, 26)
    dea = _ema(np.nan_to_num(dif), 9)
    ind["macd_dif"] = dif
    ind["macd_dea"] = dea
    return ind

def _ma_for(ind, w):
    key = f"ma_{w}"
    arr = ind.get(key)
    if arr is None:
        return _sma(ind["closes"], w)
    return arr

def _crossed(a, b, i, direction):
    if i == 0:
        return False
    if direction == "above":
        return a[i-1] <= b[i-1] and a[i] > b[i]
    return a[i-1] >= b[i-1] and a[i] < b[i]

def _cond_met(c, ind, i, position):
    t = c.type
    if t == "ma_cross":
        fast_ma = _ma_for(ind, c.fast)
        slow_ma = _ma_for(ind, c.slow)
        if np.isnan(fast_ma[i]) or np.isnan(slow_ma[i]):
            return False
        return _crossed(fast_ma, slow_ma, i, c.direction)
    if t == "macd_cross":
        if i == 0 or np.isnan(ind["macd_dif"][i]) or np.isnan(ind["macd_dea"][i]):
            return False
        return _crossed(ind["macd_dif"], ind["macd_dea"], i, c.direction)
    if t == "rsi_above":
        return not np.isnan(ind["rsi"][i]) and ind["rsi"][i] >= c.value
    if t == "rsi_below":
        return not np.isnan(ind["rsi"][i]) and ind["rsi"][i] <= c.value
    if t == "price_above":
        return ind["closes"][i] >= c.value
    if t == "price_below":
        return ind["closes"][i] <= c.value
    if position is None:
        return False
    entry = position.get("entry_price")
    if t == "stop_loss_pct":
        return bool(entry) and (ind["closes"][i] / entry - 1) * 100 <= c.value
    if t == "take_profit_pct":
        return bool(entry) and (ind["closes"][i] / entry - 1) * 100 >= c.value
    if t == "trailing_stop_pct":
        hwm = position.get("high_watermark") or entry or ind["closes"][i]
        return (hwm - ind["closes"][i]) / hwm * 100 >= c.value
    return False

def evaluate_rule(rule, ind, i, position=None):
    flags = [_cond_met(c, ind, i, position) for c in rule.conditions]
    names = [c.type for c in rule.conditions]
    hit = all(flags) if rule.logic == "all" else any(flags)
    reasons = [n for n, f in zip(names, flags) if f]
    return hit, reasons


def run_backtest(config: dict, records: list[dict]) -> dict:
    cfg = StrategyConfig.model_validate(config)
    if len(records) <= 20:
        return {
            "symbol": cfg.symbol,
            "error": "data insufficient",
            "equity_curve": [],
            "trade_log": [],
        }
    ind = compute_indicators(records)
    cash = float(cfg.initial_capital)
    shares = 0.0
    entry_price = 0.0
    hwm = 0.0
    trades = []
    equity_curve = []
    benchmark_equity_curve = []
    round_trip_pnls = []
    comm = cfg.risk.commission_pct / 100.0
    slip = cfg.risk.slippage_pct / 100.0

    start_price = float(ind["closes"][20])
    benchmark_shares = float(cfg.initial_capital) / start_price
    start_equity = float(cfg.initial_capital)
    last_buy_cost = 0.0

    for i in range(20, len(records)):
        price = float(ind["closes"][i])
        if shares == 0:
            ok, reasons = evaluate_rule(cfg.entry, ind, i, None)
            if ok:
                fill = price * (1 + slip)
                budget = cash if cfg.position.type == "full" else cash * (cfg.position.size_pct or 100) / 100.0
                shares = budget * (1 - comm) / fill
                buy_cost = shares * fill * (1 + comm)
                cash -= buy_cost
                last_buy_cost = buy_cost
                entry_price = fill
                hwm = fill
                trades.append({"date": ind["dates"][i], "side": "buy", "price": fill,
                               "shares": shares, "amount": shares * fill, "reason": ",".join(reasons)})
        else:
            hwm = max(hwm, float(ind["highs"][i]))
            ok, reasons = evaluate_rule(cfg.exit, ind, i, {"entry_price": entry_price, "high_watermark": hwm})
            if ok:
                fill = price * (1 - slip)
                sell_proceeds = shares * fill * (1 - comm)
                cash += sell_proceeds
                round_trip_pnls.append(sell_proceeds - last_buy_cost)
                trades.append({"date": ind["dates"][i], "side": "sell", "price": fill,
                               "shares": shares, "amount": shares * fill, "reason": ",".join(reasons)})
                shares = 0.0
                entry_price = 0.0
                hwm = 0.0
                last_buy_cost = 0.0
        equity_curve.append({"date": ind["dates"][i], "equity": cash + shares * price, "close": price})
        benchmark_equity_curve.append({
            "date": ind["dates"][i],
            "equity": benchmark_shares * price,
            "close": price,
        })

    final = cash + shares * float(ind["closes"][-1])
    buy_and_hold_final = benchmark_equity_curve[-1]["equity"] if benchmark_equity_curve else start_equity
    total_return = final / cfg.initial_capital - 1
    buy_and_hold_return = buy_and_hold_final / cfg.initial_capital - 1

    equity_values = [point["equity"] for point in equity_curve]
    max_drawdown = _max_drawdown_pct(equity_values)
    annualized_return = _annualized_return_pct(total_return, len(equity_values))
    sharpe_ratio = _sharpe_ratio(equity_values)
    wins = [pnl for pnl in round_trip_pnls if pnl > 0]
    win_rate = (len(wins) / len(round_trip_pnls) * 100) if round_trip_pnls else 0.0

    return {
        "symbol": cfg.symbol, "initial_capital": cfg.initial_capital,
        "final_value": round(final, 2),
        "total_return_pct": round(total_return * 100, 2),
        "buy_and_hold_return_pct": round(buy_and_hold_return * 100, 2),
        "excess_return_pct": round((total_return - buy_and_hold_return) * 100, 2),
        "annualized_return_pct": round(annualized_return, 2),
        "sharpe_ratio": round(sharpe_ratio, 3),
        "max_drawdown_pct": round(max_drawdown, 2),
        "win_rate": round(win_rate, 2),
        "closed_trades": len(round_trip_pnls),
        "trade_count": len(trades),
        "data_points": len(records),
        "trade_log": trades, "equity_curve": equity_curve,
        "benchmark_equity_curve": benchmark_equity_curve,
        "start_date": ind["dates"][20], "end_date": ind["dates"][-1],
    }


def _max_drawdown_pct(equity_values):
    if not equity_values:
        return 0.0
    values = np.asarray(equity_values, dtype=float)
    running_high = np.maximum.accumulate(values)
    drawdowns = (values - running_high) / running_high * 100
    return float(np.min(drawdowns))


def _sharpe_ratio(equity_values):
    values = np.asarray(equity_values, dtype=float)
    if len(values) < 2:
        return 0.0
    returns = np.diff(values) / values[:-1]
    std = float(np.std(returns))
    if std == 0:
        return 0.0
    return float(np.mean(returns) / std * np.sqrt(252))


def _annualized_return_pct(total_return, bars):
    if bars <= 0 or total_return <= -1:
        return 0.0
    years = bars / 252.0
    if years <= 0:
        return 0.0
    return ((1 + total_return) ** (1 / years) - 1) * 100


def evaluate_bar(config: dict, records: list[dict], date: str, position: dict | None) -> dict:
    cfg = StrategyConfig.model_validate(config)
    ind = compute_indicators(records)
    idx = next((i for i, d in enumerate(ind["dates"]) if d == date), None)
    if idx is None:
        # 请求的 bar（如"今天"）在数据里不存在 → 说明不是交易日/数据未出，
        # 明确返回 bar_date_missing=true，调用方应跳过本日结算，禁止用旧 bar 冒充当日成交。
        if not date:
            idx = len(records) - 1
        else:
            return {"signal": "hold", "matched_conditions": [], "date": date,
                    "bar_date_missing": True, "price": None}
    if idx < 20:
        return {"signal": "hold", "matched_conditions": [], "date": date,
                "price": float(ind["closes"][idx])}
    if position and position.get("shares", 0) > 0:
        ok, reasons = evaluate_rule(cfg.exit, ind, idx, position)
        return {"signal": "sell" if ok else "hold", "matched_conditions": reasons,
                "date": date, "price": float(ind["closes"][idx])}
    ok, reasons = evaluate_rule(cfg.entry, ind, idx, None)
    return {"signal": "buy" if ok else "hold", "matched_conditions": reasons,
            "date": date, "price": float(ind["closes"][idx])}


# ==================== 真实化执行引擎（A 股近似规则） ====================
# 与 run_backtest(理想模型) 的差异：
#   1) 信号用第 i 根 bar 收盘价计算，成交一律在第 i+1 根 bar 的【开盘价】撮合
#      （你不可能在收盘瞬间成交，最早只能次日开盘买/卖）；
#   2) 买入按 A 股整手取整（默认 100 股/手；科创板 688/689 起购 200 股，北交所 100 股）；
#   3) 佣金双边 + 单笔最低 5 元；卖出另收印花税（默认 0.05%，以最新法规为准）；
#   4) 涨跌停挡单：当日开盘涨停（≥昨收*(1+limit)）买不进，跌停（≤昨收*(1-limit)）卖不出；
#   5) T+1 语义自动满足：买入成交在 bar f 开盘，最早卖出成交在 bar f+1 开盘（即下一个交易日）；
#   6) 请求结算日无对应 bar（节假日/数据未出）时不成交（由 evaluate_bar 的 bar_date_missing 守卫）。

def _default_limit_pct(symbol: str) -> float:
    s = (symbol or "").upper()
    if s.startswith(("688", "689", "300", "301", "302")):  # 科创板/创业板 → 20%
        return 20.0
    if s.startswith(("43", "83", "87", "88", "92")):       # 北交所 → 30%
        return 30.0
    return 10.0                                            # 沪深主板 → 10%


def _comm_fee(turnover: float, comm_rate: float) -> float:
    """佣金：费率>0 时按 max(成交额*费率, 5元)；费率=0（测试/内部）则 0。"""
    if comm_rate <= 0 or turnover <= 0:
        return 0.0
    return max(turnover * comm_rate, 5.0)


def run_backtest_realistic(
    config: dict,
    records: list[dict],
    lot_size: int | None = None,
    limit_up_pct: float | None = None,
    stamp_tax_pct: float | None = None,
) -> dict:
    """真实化执行回测。返回键与 run_backtest 兼容，另附 execution/暴露等说明字段。"""
    cfg = StrategyConfig.model_validate(config)
    if len(records) <= 21:  # 需要至少 1 根"信号 bar" + 1 根"成交 bar"
        return {
            "symbol": cfg.symbol,
            "error": "data insufficient",
            "equity_curve": [],
            "trade_log": [],
        }

    opens = np.array([float(r["open"]) for r in records], dtype=float)
    closes = np.array([float(r["close"]) for r in records], dtype=float)
    highs = np.array([float(r["high"]) for r in records], dtype=float)
    dates = [r.get("date", str(i)) for i, r in enumerate(records)]

    lot = int(lot_size or 100)
    limit = float(limit_up_pct if limit_up_pct is not None else _default_limit_pct(cfg.symbol))
    stamp = float(stamp_tax_pct if stamp_tax_pct is not None else 0.05)  # 卖出印花税 %
    comm = cfg.risk.commission_pct / 100.0
    slip = cfg.risk.slippage_pct / 100.0

    cash = float(cfg.initial_capital)
    shares = 0.0
    entry_price = 0.0
    buy_cost = 0.0
    hwm = 0.0
    trades = []
    equity_curve = []
    benchmark_equity_curve = []
    round_trip_pnls = []
    holding_days = 0

    start_price = float(closes[20])
    benchmark_shares = float(cfg.initial_capital) / start_price

    def fill_open(idx: int, side: str) -> float:
        """第 idx 根 bar 开盘价 + 滑点（买加、卖减）。"""
        return float(opens[idx]) * (1.0 + slip if side == "buy" else 1.0 - slip)

    def limit_blocked(idx: int, side: str) -> bool:
        """用开盘价相对昨收判断涨跌停挡单。"""
        if idx <= 0:
            return False
        prev_close = float(closes[idx - 1])
        o = float(opens[idx])
        if side == "buy" and o >= prev_close * (1.0 + limit / 100.0):
            return True   # 一字/接近涨停开盘：买不进
        if side == "sell" and o <= prev_close * (1.0 - limit / 100.0):
            return True   # 跌停开盘：卖不出
        return False

    ind = compute_indicators(records)

    for i in range(20, len(records)):
        price = float(closes[i])

        if shares <= 0:
            ok, reasons = evaluate_rule(cfg.entry, ind, i, None)
            if ok and i + 1 < len(records):
                f = i + 1  # 次日开盘成交
                if not limit_blocked(f, "buy"):
                    o = fill_open(f, "buy")
                    if o > 0:
                        budget = cash if cfg.position.type == "full" else cash * (cfg.position.size_pct or 100) / 100.0
                        lots = int(budget / (o * (1.0 + comm) * lot)) if o > 0 else 0
                        sh = float(lots * lot)
                        while sh > 0:
                            fee = _comm_fee(sh * o, comm)
                            if sh * o + fee <= cash:
                                break
                            sh -= lot
                        if sh > 0:
                            fee = _comm_fee(sh * o, comm)
                            cash -= sh * o + fee
                            buy_cost = sh * o + fee
                            shares = sh
                            entry_price = o
                            hwm = o
                            trades.append({
                                "date": dates[f], "side": "buy",
                                "price": round(o, 4), "shares": sh,
                                "amount": round(sh * o, 2), "fee": round(fee, 2),
                                "reason": ",".join(reasons), "fill_mode": "next_open",
                            })
        else:
            hwm = max(hwm, float(highs[i]))
            ok, reasons = evaluate_rule(
                cfg.exit, ind, i, {"entry_price": entry_price, "high_watermark": hwm})
            if ok and i + 1 < len(records):
                f = i + 1
                if not limit_blocked(f, "sell"):
                    o = fill_open(f, "sell")
                    if o > 0:
                        fee = _comm_fee(shares * o, comm)
                        stamp_amt = shares * o * stamp / 100.0
                        proceeds = shares * o - fee - stamp_amt
                        cash += proceeds
                        round_trip_pnls.append(proceeds - buy_cost)
                        trades.append({
                            "date": dates[f], "side": "sell",
                            "price": round(o, 4), "shares": shares,
                            "amount": round(shares * o, 2), "fee": round(fee + stamp_amt, 2),
                            "reason": ",".join(reasons), "fill_mode": "next_open",
                        })
                        shares = 0.0
                        entry_price = 0.0
                        buy_cost = 0.0
                        hwm = 0.0

        if shares > 0:
            holding_days += 1
        equity_curve.append({"date": dates[i], "equity": cash + shares * price, "close": price})
        benchmark_equity_curve.append({
            "date": dates[i], "equity": benchmark_shares * price, "close": price,
        })

    final = cash + shares * float(closes[-1])
    buy_and_hold_final = benchmark_equity_curve[-1]["equity"] if benchmark_equity_curve else cash
    total_return = final / float(cfg.initial_capital) - 1
    buy_and_hold_return = buy_and_hold_final / float(cfg.initial_capital) - 1

    equity_values = [point["equity"] for point in equity_curve]
    max_drawdown = _max_drawdown_pct(equity_values)
    annualized_return = _annualized_return_pct(total_return, len(equity_values))
    sharpe_ratio = _sharpe_ratio(equity_values)
    wins = [pnl for pnl in round_trip_pnls if pnl > 0]
    win_rate = (len(wins) / len(round_trip_pnls) * 100) if round_trip_pnls else 0.0

    return {
        "symbol": cfg.symbol, "initial_capital": cfg.initial_capital,
        "execution": "realistic",
        "final_value": round(final, 2),
        "total_return_pct": round(total_return * 100, 2),
        "buy_and_hold_return_pct": round(buy_and_hold_return * 100, 2),
        "excess_return_pct": round((total_return - buy_and_hold_return) * 100, 2),
        "annualized_return_pct": round(annualized_return, 2),
        "sharpe_ratio": round(sharpe_ratio, 3),
        "max_drawdown_pct": round(max_drawdown, 2),
        "win_rate": round(win_rate, 2),
        "closed_trades": len(round_trip_pnls),
        "trade_count": len(trades),
        "exposure_pct": round(holding_days / len(equity_curve) * 100, 2) if equity_curve else 0.0,
        "rules": {
            "lot_size": lot, "limit_pct": limit,
            "stamp_tax_pct": stamp, "commission_pct": cfg.risk.commission_pct,
            "min_commission_yuan": 5.0, "fill": "next_open",
        },
        "data_points": len(records),
        "trade_log": trades, "equity_curve": equity_curve,
        "benchmark_equity_curve": benchmark_equity_curve,
        "start_date": ind["dates"][20], "end_date": ind["dates"][-1],
    }


def compare_executions(config: dict, records: list[dict]) -> dict:
    """理想成交 vs 真实化成交的 A/B 对照（同一策略、同一数据）。"""
    ideal = run_backtest(config, records)
    realistic = run_backtest_realistic(config, records)
    out = {
        "symbol": config.get("symbol"),
        "bars": len(records),
        "ideal": ideal,
        "realistic": realistic,
    }
    if "error" in ideal or "error" in realistic:
        out["error"] = ideal.get("error") or realistic.get("error")
        return out
    out["gap_total_return_pct"] = round(
        realistic["total_return_pct"] - ideal["total_return_pct"], 2)
    out["gap_max_drawdown_pct"] = round(
        realistic["max_drawdown_pct"] - ideal["max_drawdown_pct"], 2)
    return out

