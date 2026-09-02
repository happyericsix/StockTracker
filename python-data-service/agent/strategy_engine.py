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
    if len(records) < 20:
        return {"symbol": cfg.symbol, "error": "data insufficient"}
    ind = compute_indicators(records)
    cash = float(cfg.initial_capital)
    shares = 0.0
    entry_price = 0.0
    hwm = 0.0
    trades = []
    equity_curve = []
    comm = cfg.risk.commission_pct / 100.0
    slip = cfg.risk.slippage_pct / 100.0

    for i in range(20, len(records)):
        price = float(ind["closes"][i])
        if shares == 0:
            ok, reasons = evaluate_rule(cfg.entry, ind, i, None)
            if ok:
                fill = price * (1 + slip)
                budget = cash if cfg.position.type == "full" else cash * (cfg.position.size_pct or 100) / 100.0
                shares = budget * (1 - comm) / fill
                cash -= shares * fill * (1 + comm)
                entry_price = fill
                hwm = fill
                trades.append({"date": ind["dates"][i], "side": "buy", "price": fill,
                               "shares": shares, "amount": shares * fill, "reason": ",".join(reasons)})
        else:
            hwm = max(hwm, float(ind["highs"][i]))
            ok, reasons = evaluate_rule(cfg.exit, ind, i, {"entry_price": entry_price, "high_watermark": hwm})
            if ok:
                fill = price * (1 - slip)
                cash += shares * fill * (1 - comm)
                trades.append({"date": ind["dates"][i], "side": "sell", "price": fill,
                               "shares": shares, "amount": shares * fill, "reason": ",".join(reasons)})
                shares = 0.0
                entry_price = 0.0
                hwm = 0.0
        equity_curve.append({"date": ind["dates"][i], "equity": cash + shares * price, "close": price})

    final = cash + shares * float(ind["closes"][-1])
    return {
        "symbol": cfg.symbol, "initial_capital": cfg.initial_capital,
        "final_value": round(final, 2),
        "total_return_pct": round((final / cfg.initial_capital - 1) * 100, 2),
        "data_points": len(records),
        "trade_log": trades, "equity_curve": equity_curve,
        "start_date": ind["dates"][20], "end_date": ind["dates"][-1],
    }


def evaluate_bar(config: dict, records: list[dict], date: str, position: dict | None) -> dict:
    cfg = StrategyConfig.model_validate(config)
    ind = compute_indicators(records)
    idx = next((i for i, d in enumerate(ind["dates"]) if d == date), len(records) - 1)
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
