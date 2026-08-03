"""
FastAPI 入口 —— 为 Java 后端提供 RESTful 接口，替代原有的 Choice API 数据源。
"""

import logging
import asyncio
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
    version="0.1.0",
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


# ==================== 股票搜索（Autocomplete） ====================

@app.get("/api/v1/stocks/search")
def stock_search(keyword: str = Query(default="", description="搜索关键词（代码或名称）")):
    """股票代码/名称自动补全搜索。启动时缓存全 A 股名单，支持代码前缀 + 名称模糊匹配。"""
    results = search_stocks(keyword)
    return {"keyword": keyword, "count": len(results), "results": results}


# ==================== 实时行情 ====================

@app.get("/api/v1/quote/{symbol}", response_model=StockQuoteResponse)
def stock_quote(symbol: str):
    """获取股票实时行情，返回格式与 Java StockQuoteResponse 兼容。"""
    data = get_quote(symbol)
    if data is None:
        return StockQuoteResponse(
            globalQuote=None,
            note="No data"
        )

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
    """获取股票日线历史数据，返回格式与 Java StockHistoryResponse 兼容。"""
    records = get_history(symbol, start_date=start_date, end_date=end_date)
    if records is None:
        return StockHistoryResponse(
            metaData=MetaData(symbol=symbol),
            timeSeries={},
        )

    time_series = {}
    for r in records:
        day_key = r.get("date", "")
        time_series[day_key] = DailyPrice(
            open=r.get("open", "0"),
            high=r.get("high", "0"),
            low=r.get("low", "0"),
            close=r.get("close", "0"),
            volume=r.get("volume", "0"),
        )

    return StockHistoryResponse(
        metaData=MetaData(symbol=symbol),
        timeSeries=time_series,
    )


# ==================== 基本面概况 ====================

@app.get("/api/v1/overview/{symbol}", response_model=StockOverviewResponse)
def stock_overview(symbol: str):
    """获取股票基本面概况，返回格式与 Java StockOverviewResponse 兼容。"""
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


# ==================== QQ Bot 接口 ====================

import requests
import qq_handler
NAPCAT_API = "http://localhost:8081"

def send_qq(user_id, text):
    try:
        requests.post(f"{NAPCAT_API}/", json={
            "user_id": user_id,
            "message": text
        }, timeout=5)
    except Exception as e:
        print(f"QQ 发送失败: {e}")

@app.post("/qq_msg")
async def qq_webhook(req: Request):
    """
    NapCat QQ 消息 webhook

    NapCat 推送的消息格式通常是:
    {
        "user_id": "12345678",
        "message": "用户消息内容"
    }
    """
    data = await req.json()
    user_id = str(data.get("user_id", "")).strip()
    message = data.get("message", "").strip()
    print(f"QQ 消息 [{user_id}]: {message}")

    if not message or not user_id:
        return {"status": "ok"}

    try:
        # 路由到 handler 处理
        replies = await asyncio.to_thread(qq_handler.handle_message, user_id, message)
        # 多条消息按顺序发送
        for r in replies:
            await asyncio.to_thread(send_qq, user_id, r)
    except Exception as e:
        logger.error(f"QQ 消息处理异常: {e}", exc_info=True)
        await asyncio.to_thread(send_qq, user_id, "⚠️ 处理出错了，稍后再试")

    return {"status": "ok"}
