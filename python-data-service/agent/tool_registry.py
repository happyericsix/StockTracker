from __future__ import annotations
import json
from akshare_client import get_quote, get_history, search_stocks
from agent.strategy_schema import validate_strategy_config
from agent.strategy_engine import run_backtest


def _tool(name, desc, params):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}


def _obj(props, required):
    return {"type": "object", "properties": props, "required": required}


TOOL_SCHEMAS = [
    _tool("search_stock", "Resolve a Chinese stock name to a symbol code.",
          _obj({"keyword": {"type": "string"}}, ["keyword"])),
    _tool("get_quote", "Get latest quote for one symbol.",
          _obj({"symbol": {"type": "string"}}, ["symbol"])),
    _tool("get_history", "Get daily history bars.",
          _obj({"symbol": {"type": "string"}, "days": {"type": "integer", "default": 250}}, ["symbol"])),
    _tool("validate_strategy", "Validate a complete strategy JSON object. Required: schema_version=1.0, name, symbol, entry.logic, entry.conditions, exit.logic, exit.conditions. Conditions use type ma_cross/rsi_above/rsi_below/macd_cross/price_above/price_below/stop_loss_pct/take_profit_pct/trailing_stop_pct.",
          _obj({"strategy_json": {"type": "object"}}, ["strategy_json"])),
    _tool("backtest_strategy", "Backtest a strategy JSON object.",
          _obj({"strategy_json": {"type": "object"}}, ["strategy_json"])),
    _tool("finalize_strategy", "Finalize the complete strategy JSON and return a plain-language summary. Include schema_version=1.0, name, symbol, initial_capital, data, position, entry, exit, risk.",
          _obj({"strategy_json": {"type": "object"}}, ["strategy_json"])),
]


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
        return {"error": f"unknown tool {name}"}
    except Exception as e:
        return {"error": str(e)}
