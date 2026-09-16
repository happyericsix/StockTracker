"""
Pydantic models matching Java DTO exact JSON structure.
Java side uses Jackson @JsonProperty, so field names must match exactly.
"""

from pydantic import BaseModel, Field
from typing import Optional


class GlobalQuote(BaseModel):
    """Matches StockQuoteResponse.GlobalQuote in Java"""
    symbol: str = Field(default="", alias="01. symbol")
    price: Optional[str] = Field(default=None, alias="05. price")
    lastTradingDay: str = Field(default="", alias="07. latest trading day")
    name: str = Field(default="")

    # 涨跌三兄弟。akshare_client.get_quote() 从腾讯行情里已经解析出「昨收」「涨跌额」「涨跌幅」，
    # 但此前没有出现在这个模型里，于是到了 Java 就被 Jackson 丢掉、前端拿不到涨跌方向。
    # 编号沿用本文件既有的 Alpha Vantage 风格，Java 侧用同名 @JsonProperty 对齐。
    previousClose: Optional[str] = Field(default=None, alias="08. previous close")
    change: Optional[str] = Field(default=None, alias="09. change")
    changePercent: Optional[str] = Field(default=None, alias="10. change percent")

    model_config = {"populate_by_name": True}


class StockQuoteResponse(BaseModel):
    """Matches Java StockQuoteResponse record"""
    globalQuote: Optional[GlobalQuote] = Field(default=None, alias="Global Quote")
    note: Optional[str] = Field(default=None, alias="Note")

    model_config = {"populate_by_name": True}


class DailyPrice(BaseModel):
    """Matches StockHistoryResponse.DailyPrice in Java"""
    open: str = Field(default="0", alias="1. open")
    high: str = Field(default="0", alias="2. high")
    low: str = Field(default="0", alias="3. low")
    close: str = Field(default="0", alias="4. close")
    volume: str = Field(default="0", alias="5. volume")

    model_config = {"populate_by_name": True}


class MetaData(BaseModel):
    """Matches StockHistoryResponse.MetaData in Java"""
    symbol: str = Field(default="", alias="2. Symbol")

    model_config = {"populate_by_name": True}


class StockHistoryResponse(BaseModel):
    """Matches Java StockHistoryResponse record"""
    metaData: Optional[MetaData] = Field(default=None, alias="Meta Data")
    timeSeries: dict[str, DailyPrice] = Field(default_factory=dict, alias="Time Series (Daily)")

    model_config = {"populate_by_name": True}


class StockOverviewResponse(BaseModel):
    """Matches Java StockOverviewResponse record"""
    symbol: str = Field(default="", alias="Symbol")
    name: str = Field(default="", alias="Name")
    description: str = Field(default="", alias="Description")
    sector: str = Field(default="", alias="Sector")
    industry: str = Field(default="", alias="Industry")
    marketCapitalization: str = Field(default="", alias="MarketCapitalization")
    peRatio: str = Field(default="", alias="PERatio")
    dividendYield: str = Field(default="", alias="DividendYield")

    model_config = {"populate_by_name": True}