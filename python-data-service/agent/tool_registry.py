from __future__ import annotations

import json

import numpy as np

from akshare_client import get_quote, get_history, search_stocks
from agent.strategy_engine import compute_indicators, run_backtest
from agent.strategy_schema import validate_strategy_config


def _tool(name, desc, params):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}


def _obj(props, required):
    return {"type": "object", "properties": props, "required": required}


def _symbol_props():
    return _obj({"symbol": {"type": "string"}}, ["symbol"])


TOOL_SCHEMAS = [
    _tool("search_stock", "Resolve a Chinese stock name to a symbol code.",
          _obj({"keyword": {"type": "string"}}, ["keyword"])),
    _tool("get_quote", "Get the latest quote for one symbol.",
          _symbol_props()),
    _tool("get_history", "Get recent daily history bars. Use this for factual price data only.",
          _obj({"symbol": {"type": "string"}, "days": {"type": "integer", "default": 250}}, ["symbol"])),
    _tool("get_indicators", "Get current technical indicators computed from historical bars. These are rules, not price predictions.",
          _symbol_props()),
    _tool("get_risk_metrics", "Get volatility, drawdown, and distance from moving averages for risk control.",
          _symbol_props()),
    _tool("validate_strategy", "Validate a complete strategy JSON object. Required: schema_version=1.0, name, symbol, entry.logic, entry.conditions, exit.logic, exit.conditions. Conditions use type ma_cross/rsi_above/rsi_below/macd_cross/price_above/price_below/stop_loss_pct/take_profit_pct/trailing_stop_pct.",
          _obj({"strategy_json": {"type": "object"}}, ["strategy_json"])),
    _tool("backtest_strategy", "Backtest a strategy JSON object on historical data and return evidence-based results.",
          _obj({"strategy_json": {"type": "object"}}, ["strategy_json"])),
    _tool("finalize_strategy", "Finalize the complete strategy JSON and return a plain-language summary. Include schema_version=1.0, name, symbol, initial_capital, data, position, entry, exit, risk.",
          _obj({"strategy_json": {"type": "object"}}, ["strategy_json"])),
    _tool("get_model_status", "Diagnose whether prediction models are available and how old they are. Diagnostic only; never use this to decide buy/sell.",
          _symbol_props()),
    _tool("get_model_consensus", "Return model direction consensus and confidence as a weak optional reference. If unavailable or confidence is low, ignore it.",
          _symbol_props()),
]


def _prices_from_records(records):
    prices = []
    for record in records or []:
        try:
            price = float(record.get("close"))
        except (TypeError, ValueError):
            continue
        if price > 0:
            prices.append(price)
    return prices


def _indicator_summary(records):
    if len(records or []) < 20:
        return {"error": "历史数据不足 20 条"}

    ind = compute_indicators(records)
    closes = ind["closes"]
    latest = closes[-1]

    def last(arr):
        arr = np.asarray(arr, dtype=float)
        return None if arr.size == 0 or np.isnan(arr[-1]) else round(float(arr[-1]), 3)

    return {
        "date": ind["dates"][-1],
        "close": round(float(latest), 3),
        "ma5": last(ind.get("ma_5")),
        "ma10": last(ind.get("ma_10")),
        "ma20": last(ind.get("ma_20")),
        "ma60": last(ind.get("ma_60")),
        "rsi14": last(ind.get("rsi")),
        "macd_dif": last(ind.get("macd_dif")),
        "macd_dea": last(ind.get("macd_dea")),
    }


def _risk_summary(records):
    prices = _prices_from_records(records)
    if len(prices) < 20:
        return {"error": "历史数据不足 20 条"}

    closes = np.asarray(prices, dtype=float)
    daily_returns = np.diff(closes) / closes[:-1]
    annual_volatility = float(np.std(daily_returns) * np.sqrt(252) * 100) if len(daily_returns) > 1 else 0.0

    running_high = np.maximum.accumulate(closes)
    drawdowns = (closes - running_high) / running_high * 100
    max_drawdown = float(np.min(drawdowns))
    current_drawdown = float(drawdowns[-1])

    ma20 = float(np.mean(closes[-20:]))
    ma60 = float(np.mean(closes[-60:])) if len(closes) >= 60 else None
    distance_ma20 = (closes[-1] / ma20 - 1) * 100
    distance_ma60 = (closes[-1] / ma60 - 1) * 100 if ma60 else None

    rsi = None
    try:
        rsi = _indicator_summary(records).get("rsi14")
    except Exception:
        pass

    risk_level = "medium"
    if annual_volatility < 25 and abs(distance_ma20) < 3 and max_drawdown > -12:
        risk_level = "low"
    elif annual_volatility > 45 or max_drawdown < -25 or abs(distance_ma20) > 10:
        risk_level = "high"

    return {
        "date": records[-1].get("date"),
        "close": round(float(closes[-1]), 3),
        "annual_volatility_pct": round(annual_volatility, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "current_drawdown_pct": round(current_drawdown, 2),
        "distance_from_ma20_pct": round(distance_ma20, 2),
        "distance_from_ma60_pct": round(distance_ma60, 2) if distance_ma60 is not None else None,
        "rsi14": rsi,
        "risk_level": risk_level,
        "decision_note": "风险指标只用于仓位和止损控制，不能单独作为买卖结论。",
    }


def _model_diagnostic(symbol, include_consensus=False):
    records = get_history(symbol) or []
    prices = _prices_from_records(records)
    if len(prices) < 20:
        return {"available": False, "confidence": "low", "consensus": "neutral",
                "reason": "历史数据不足 20 条，无法诊断模型", "decision_use": False}

    try:
        import quant_model

        cached = quant_model._model_cache.get(symbol) if hasattr(quant_model, "_model_cache") else None
        disk_models = None
        try:
            disk_models = quant_model._try_load_disk(symbol, prices) if hasattr(quant_model, "_try_load_disk") else None
        except Exception:
            disk_models = None
    except Exception:
        return {"available": False, "confidence": "low", "consensus": "neutral",
                "reason": "模型模块不可用", "decision_use": False}

    available = bool(cached or disk_models)
    result = {
        "available": available,
        "cache_hit": bool(cached),
        "disk_model_available": bool(disk_models),
        "confidence": "unknown" if available else "low",
        "consensus": "neutral",
        "decision_use": False,
        "note": "预测模型只做低权重诊断，不能作为买卖依据。",
    }

    if include_consensus and disk_models:
        prediction = disk_models.get("prediction") or {}
        if isinstance(prediction, dict) and "consensus" in prediction:
            result["consensus"] = prediction.get("consensus", "neutral")
            result["confidence"] = prediction.get("confidence", "low")

    return result


def execute_tool(name, args):
    try:
        if name == "search_stock":
            return {"results": search_stocks(args.get("keyword", ""))[:10]}
        if name == "get_quote":
            return {"quote": get_quote(args.get("symbol", ""))}
        if name == "get_history":
            symbol = args.get("symbol", "")
            records = get_history(symbol) or []
            days = int(args.get("days", 250))
            return {"records": records[-days:]}
        if name == "get_indicators":
            records = get_history(args.get("symbol", "")) or []
            return _indicator_summary(records)
        if name == "get_risk_metrics":
            records = get_history(args.get("symbol", "")) or []
            return _risk_summary(records)
        if name == "validate_strategy":
            cfg, err = validate_strategy_config(args.get("strategy_json", {}))
            return {"valid": err is None, "error": err, "normalized": cfg.model_dump() if cfg else None}
        if name == "backtest_strategy":
            cfg_json = args.get("strategy_json", {})
            cfg, err = validate_strategy_config(cfg_json)
            if err:
                return {"valid": False, "error": err}
            records = get_history(cfg.symbol)
            return {"valid": True, "backtest": run_backtest(cfg_json, records or [])}
        if name == "finalize_strategy":
            cfg_json = args.get("strategy_json", {})
            cfg, err = validate_strategy_config(cfg_json)
            return {"valid": err is None, "error": err, "strategy_json": cfg_json}
        if name == "get_model_status":
            return _model_diagnostic(args.get("symbol", ""), include_consensus=False)
        if name == "get_model_consensus":
            return _model_diagnostic(args.get("symbol", ""), include_consensus=True)
        return {"error": f"unknown tool {name}"}
    except Exception as e:
        return {"error": str(e)}
