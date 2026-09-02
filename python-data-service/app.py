"""
FastAPI 入口 — 为 stock-tracker Java 后端提供实时行情接口。
"""

import logging
import asyncio
import os
from datetime import date, timedelta

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from akshare_client import get_quote, get_history, get_minute_kline, get_overview, search_stocks
# 注意:quant_model(用了 sklearn) 改成 lazy import,
# 避免启动时因 sklearn 缺失导致整个 app 挂掉
# 真正的 import 在用到 analyze_stock 的 endpoint 函数里
from models import StockQuoteResponse, GlobalQuote, StockHistoryResponse, MetaData, DailyPrice, StockOverviewResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Stock Data Service (akshare)",
    description="为 stock-tracker Java 后端提供实时行情、K 线历史、基本面数据",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== 健康检查 ====================

@app.get("/health")
def health():
    import llm_service
    return {
        "status": "ok",
        "llm_available": llm_service._is_available(),
        "llm_model": llm_service.MODEL,
    }


# ==================== 股票搜索（Autocomplete）====================

@app.get("/api/v1/stocks/search")
def stock_search(keyword: str = Query(default="", description="搜索关键词（代码或名称）")):
    results = search_stocks(keyword)
    return {"keyword": keyword, "count": len(results), "results": results}


# ==================== 实时行情 ====================

@app.get("/api/v1/quote/{symbol}", response_model=StockQuoteResponse)
def stock_quote(symbol: str):
    data = get_quote(symbol)
    if data is None:
        return StockQuoteResponse(globalQuote=None, note="No data")

    price = data.get("最新价", "")
    if not price or price == "0.0":
        price = None
    today_str = str(date.today())

    return StockQuoteResponse(
        globalQuote=GlobalQuote(symbol=symbol, price=price, lastTradingDay=today_str,
                                name=data.get("名称", symbol)),
    )


# ==================== K 线历史 ====================

@app.get("/api/v1/history/{symbol}", response_model=StockHistoryResponse)
def stock_history(
    symbol: str,
    start_date: str = Query(default="", description="起始日期 yyyyMMdd"),
    end_date: str = Query(default="", description="结束日期 yyyyMMdd"),
    period: str = Query(default="day", description="周期：day / week / month"),
):
    records = get_history(symbol, start_date=start_date, end_date=end_date, period=period)
    if records is None:
        return StockHistoryResponse(metaData=MetaData(symbol=symbol), timeSeries={})

    time_series = {}
    for r in records:
        day_key = r.get("date", "")
        time_series[day_key] = DailyPrice(
            open=r.get("open", "0"), high=r.get("high", "0"),
            low=r.get("low", "0"), close=r.get("close", "0"),
            volume=r.get("volume", "0"),
        )

    return StockHistoryResponse(metaData=MetaData(symbol=symbol), timeSeries=time_series)


# ==================== 分钟 K 线（A 股）====================

@app.get("/api/v1/minute/{symbol}")
def stock_minute(
    symbol: str,
    period: int = Query(default=5, description="分钟周期：1 / 5 / 15 / 30 / 60"),
):
    """返回 A 股分钟 K 线（akshare 数据源）。仅支持 A 股。直接返回 list，兼容 DailyStockResponse。"""
    records = get_minute_kline(symbol, period=period)
    if records is None:
        return []
    out = []
    for r in records:
        try:
            out.append({
                "date": r.get("date", ""),
                # 保留数据源原始小数位（如 "1296.300"），转 float 会丢尾零
                "open": str(r.get("open", "0")),
                "close": str(r.get("close", "0")),
                "high": str(r.get("high", "0")),
                "low": str(r.get("low", "0")),
                "volume": int(float(r.get("volume", 0))),
            })
        except (ValueError, TypeError):
            continue
    return out


# ==================== 基本面概况 ====================

@app.get("/api/v1/overview/{symbol}", response_model=StockOverviewResponse)
def stock_overview(symbol: str):
    data = get_overview(symbol)
    if data is None:
        return StockOverviewResponse(symbol=symbol, name=symbol)

    return StockOverviewResponse(
        symbol=symbol,
        name=data.get("名称", symbol),
        marketCapitalization=data.get("总市值", "N/A"),
        peRatio=data.get("市盈率-动态", "N/A"),
        description="",
        sector="",
        industry="",
        dividendYield="N/A",
    )


# ==================== 量化分析 ====================

@app.get("/api/v1/indicators/{symbol}")
def stock_indicators(symbol: str):
    """量化指标分析 + ML 预测"""
    try:
        # lazy import:避免启动时因 sklearn 缺失导致整个 app 挂掉
        from quant_model import analyze_stock
        records = get_history(symbol)
        if records is None or len(records) < 20:
            return {"symbol": symbol, "error": "历史数据不足 20 条"}

        prices = []
        for r in records:
            try:
                prices.append(float(r["close"]))
            except (ValueError, KeyError):
                continue

        if len(prices) < 20:
            return {"symbol": symbol, "error": f"有效数据不足({len(prices)}条)"}

        result = analyze_stock(prices, symbol)
        return result
    except Exception as e:
        logger.error(f"量化分析失败 {symbol}: {e}")
        return {"symbol": symbol, "error": str(e)}


# ==================== 回测接口 ====================

@app.get("/api/v1/backtest/{symbol}")
def run_backtest_endpoint(
    symbol: str,
    capital: float = Query(default=100000, description="初始资金"),
):
    """
    对指定股票运行回测，返回所有策略的结果。

    策略包括：
    - signal: 基于技术信号（RSI/MACD/均线）
    - prediction: 基于 LightGBM 预测
    - buy_and_hold: 买入持有基准
    """
    try:
        from backtest import run_comprehensive_backtest, BacktestEngine
        # lazy import:同上,analyze_stock 也可能缺依赖
        from quant_model import analyze_stock

        records = get_history(symbol)
        if not records or len(records) < 20:
            return {"symbol": symbol, "error": "数据不足，至少需要 20 个交易日"}

        prices = [float(r["close"]) for r in records
                  if r.get("close") and float(r["close"]) > 0]

        if len(prices) < 20:
            return {"symbol": symbol, "error": "有效价格数据不足"}

        # 运行分析获取信号和预测
        analysis = analyze_stock(prices, symbol)
        signal = analysis.get("signal", {})
        predictions = analysis.get("prediction", {})
        rl_result = analysis.get("rl_strategy")

        # 运行回测
        results = run_comprehensive_backtest(
            symbol, prices,
            signal=signal,
            predictions=predictions,
            rl_result=rl_result,
            initial_capital=capital,
        )

        # 格式化输出
        output = {
            "symbol": symbol,
            "data_points": len(prices),
            "initial_capital": capital,
            "buy_and_hold_return_pct": round(results["buy_and_hold"] * 100, 2),
            "best_strategy": results["best_mode"],
            "strategies": {},
        }

        for mode, r in results.items():
            if mode in ("best_mode", "best_return", "buy_and_hold"):
                continue
            if hasattr(r, 'total_return_pct'):
                output["strategies"][mode] = {
                    "total_return_pct": r.total_return_pct,
                    "annualized_return_pct": round(r.annualized_return * 100, 2),
                    "sharpe_ratio": r.sharpe_ratio,
                    "max_drawdown_pct": r.max_drawdown_pct,
                    "win_rate": round(r.win_rate * 100, 1),
                    "total_trades": r.total_trades,
                    "excess_return_pct": round(r.excess_return * 100, 2),
                    "grade": BacktestEngine()._grade(r),
                }

        return output

    except Exception as e:
        logger.error(f"回测失败 {symbol}: {e}", exc_info=True)
        return {"symbol": symbol, "error": str(e)}


# ==================== 模型实验摘要 ====================

@app.get("/api/v1/models/summary")
def models_summary(symbol: str = Query(default=None, description="可选：按股票代码筛选")):
    """
    获取 MLflow 训练实验摘要。

    Returns:
        {"experiments": [...], "best_models": {...}}
    """
    try:
        from mlflow_utils import get_model_summary, load_best_model

        summary = get_model_summary(symbol)
        best = load_best_model()

        return {
            "total_runs": len(summary),
            "runs": summary[:20],  # 最多返回 20 条
            "best_model": {
                "run_id": best["run_id"] if best is not None and hasattr(best, '__getitem__') else None,
            } if best is not None else None,
        }
    except Exception as e:
        logger.error(f"获取模型摘要失败: {e}")
        return {"error": str(e)}


# ==================== Agent 与策略接口 ====================


@app.post("/api/v1/agent/chat")
async def agent_chat(req: Request):
    try:
        import agent.react_agent as react_agent
        data = await req.json()
        user_id = str(data.get("user_id", "")).strip()
        message = data.get("message", "").strip()
        if not user_id or not message:
            return {"replies": [], "strategy_json": None}
        result = await asyncio.to_thread(react_agent.run_agent, user_id, message)
        return result
    except Exception as e:
        logger.error(f"agent chat error: {e}", exc_info=True)
        return {"replies": ["⚠️ 处理出错了，稍后再试"], "strategy_json": None}


@app.post("/api/v1/strategies/validate")
async def validate_strategy_endpoint(req: Request):
    from agent.strategy_schema import validate_strategy_config
    try:
        data = await req.json()
        strategy_json = data.get("strategy_json", {})
        if not isinstance(strategy_json, dict):
            return {"valid": False, "error": "strategy_json must be an object", "normalized": None}
        cfg, err = validate_strategy_config(strategy_json)
        return {"valid": err is None, "error": err, "normalized": cfg.model_dump() if cfg else None}
    except Exception as e:
        logger.error(f"strategy validation error: {e}", exc_info=True)
        return {"valid": False, "error": str(e), "normalized": None}


@app.post("/api/v1/strategies/backtest")
async def backtest_strategy_endpoint(req: Request):
    from agent.strategy_schema import validate_strategy_config
    from agent.strategy_engine import run_backtest
    from akshare_client import get_history
    try:
        data = await req.json()
        cfg_json = data.get("strategy_json", {})
        cfg, err = validate_strategy_config(cfg_json)
        if err:
            return {"valid": False, "error": err}
        records = get_history(cfg.symbol)
        return {"valid": True, "backtest": run_backtest(cfg_json, records or [])}
    except Exception as e:
        logger.error(f"strategy backtest error: {e}", exc_info=True)
        return {"valid": False, "error": str(e)}


@app.post("/api/v1/strategies/evaluate-bar")
async def evaluate_bar_endpoint(req: Request):
    from agent.strategy_schema import validate_strategy_config
    from agent.strategy_engine import evaluate_bar
    from akshare_client import get_history
    try:
        data = await req.json()
        cfg, err = validate_strategy_config(data.get("strategy_json", {}))
        if err:
            return {"valid": False, "error": err}
        symbol = data.get("symbol") or cfg.symbol
        records = get_history(symbol)
        if not records:
            return {"error": "insufficient history data", "signal": "hold", "matched_conditions": []}
        position = data.get("position")
        if isinstance(position, dict) and "quantity" in position and "shares" not in position:
            position = dict(position)
            position["shares"] = position["quantity"]
        return evaluate_bar(cfg.model_dump(), records, data.get("date", ""), position)
    except Exception as e:
        logger.error(f"strategy evaluate-bar error: {e}", exc_info=True)
        return {"error": str(e), "signal": "hold", "matched_conditions": []}


# ==================== 聊天助手接口 ====================

# 注意:chat_handler 改成 lazy import,避免它的依赖缺失导致整个 app 挂掉
# 真正用到它的地方在 chat_webhook 函数内部


@app.post("/api/v1/chat")
async def chat_webhook(req: Request):
    """
    站内聊天助手入口

    数据流向:
      前端 Assistant → Spring Boot ChatService → 本接口 → chat_handler 处理 → 返回 replies
    """
    # lazy import:只在这个 endpoint 被调用时才加载 chat_handler
    import chat_handler

    data = await req.json()
    user_id = str(data.get("user_id", "")).strip()
    message = data.get("message", "").strip()
    logger.info(f"chat webhook [{user_id}]: {message[:80]}")

    if not message or not user_id:
        return {"replies": []}

    try:
        replies = await asyncio.to_thread(chat_handler.handle_message, user_id, message)
        return {"replies": replies}
    except Exception as e:
        logger.error(f"聊天处理异常: {e}", exc_info=True)
        return {"replies": ["⚠️ 处理出错了，稍后再试"]}
