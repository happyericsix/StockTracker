from __future__ import annotations
import numpy as np

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
