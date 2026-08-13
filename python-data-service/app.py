"""
FastAPI 入口 — 为 stock-tracker Java 后端提供实时行情接口。
"""

import logging
import asyncio
import os
from datetime import date, timedelta

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from akshare_client import get_quote, get_history, get_overview, search_stocks
from quant_model import analyze_stock
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
        globalQuote=GlobalQuote(symbol=symbol, price=price, lastTradingDay=today_str),
    )


# ==================== K 线历史 ====================

@app.get("/api/v1/history/{symbol}", response_model=StockHistoryResponse)
def stock_history(
    symbol: str,
    start_date: str = Query(default="", description="起始日期 yyyyMMdd"),
    end_date: str = Query(default="", description="结束日期 yyyyMMdd"),
):
    records = get_history(symbol, start_date=start_date, end_date=end_date)
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


# ==================== QQ Bot 接口 ====================

import qq_handler


@app.post("/qq_msg")
async def qq_webhook(req: Request):
    """
    接收 qq_standalone.py 转发的 QQ 消息

    数据流向:
      NapCat → qq_standalone.py (3003) → 本接口 → qq_handler 处理 → 返回 replies
    """
    data = await req.json()
    user_id = str(data.get("user_id", "")).strip()
    message = data.get("message", "").strip()
    logger.info(f"QQ webhook [{user_id}]: {message[:80]}")

    if not message or not user_id:
        return {"replies": []}

    try:
        replies = await asyncio.to_thread(qq_handler.handle_message, user_id, message)
        return {"replies": replies}
    except Exception as e:
        logger.error(f"QQ 消息处理异常: {e}", exc_info=True)
        return {"replies": ["⚠️ 处理出错了，稍后再试"]}
