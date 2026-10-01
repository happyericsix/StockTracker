# -*- coding: utf-8 -*-
"""`/api/v1/quote/{symbol}` 与 `/api/v1/market/status` 的契约。

<h3>为什么这两条要一起测</h3>
它们是同一个 bug 的两半。用户报的是"明明是在休息日，却显示昨收"：

- `market/status` 提供"今天开不开市、休到哪天"的**事实**；
- `quote` 的 `lastTradingDay` 提供"这个价是**哪一天**的"。

只要后者还写 `date.today()`，前端就算拿到了休市事实也没法把话说对 ——
它会把周五的收盘价标成今天的数据。所以这里把两条一起钉住：
**休市日的 quote 必须自报"我是上一个交易日的价"。**

用 `TestClient`（不起真端口）而不是直接调函数：鉴权中间件、`Result`/错误信封、
Pydantic 序列化都在被测范围内 —— 而"字段名对不上"恰好是这类跨语言契约最常见的坑。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app as main
import trading_calendar as tc

TOKEN = getattr(main, "INTERNAL_API_TOKEN", "") or ""
HEADERS = {"X-Internal-Token": TOKEN} if TOKEN else {}

# 与 test_trading_calendar 同一份固定日历（贴合 akshare 实测：
# 2026-09-25 周五中秋调休休市、2026-10-01 ~ 10-07 国庆、10-08 开市）
_CALENDAR = sorted(
    __import__("datetime").date.fromisoformat(d)
    for d in ["2026-09-16", "2026-09-17", "2026-09-18",
              "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]
)


def _freeze(monkeypatch, moment):
    """把"现在"钉死：这两条接口的正确答案**每天都不同**，不钉就只能碰运气。

    替换的是 `trading_calendar._now`（模块自己的时钟钩子），不是
    `datetime.datetime.now` —— 后者是 C 实现的不可变类型，根本设不上去
    （实测 `TypeError: cannot set 'now' attribute of immutable type`）。
    """
    monkeypatch.setattr(tc, "_load", lambda force=False: (_CALENDAR, "test"))
    monkeypatch.setattr(tc, "_now", lambda: moment)


def test_market_status_endpoint_exposes_the_rest_window(monkeypatch):
    _freeze(monkeypatch, tc.datetime(2026, 9, 20, 9, 57, tzinfo=tc.CN_TZ))

    response = TestClient(main.app).get("/api/v1/market/status", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["tradingDay"] is False
    assert body["nextTradingDay"] == "2026-09-21"
    assert body["restDays"] == 2
    assert "连休 2 天" in body["note"], body["note"]
    # quoteDate 是"行情属于哪一天"——前端靠它把"更新 xx"改写成"最近交易日 xx 收盘"
    assert body["quoteDate"] == "2026-09-18"


def test_quote_reports_the_last_trading_day_instead_of_today(monkeypatch):
    """休市日的行情必须自报"我是 09-18 的价"，而不是 `date.today()`。

    改造前这里写的是 `str(date.today())`，于是周日的页面显示"更新 2026-09-20"，
    而价格其实是周五收盘 —— 用户看到的直接反应就是"休息日为什么还有今天的涨跌"。
    """
    _freeze(monkeypatch, tc.datetime(2026, 9, 20, 9, 57, tzinfo=tc.CN_TZ))
    monkeypatch.setattr(main, "get_quote", lambda symbol: {
        "代码": "600519", "名称": "贵州茅台", "最新价": "1400.00", "昨收": "1390.00",
        "涨跌幅": "0.72", "涨跌额": "10.00",
    })

    response = TestClient(main.app).get("/api/v1/quote/600519", headers=HEADERS)

    assert response.status_code == 200
    quote = response.json()["Global Quote"]
    assert quote["07. latest trading day"] == "2026-09-18", \
        "休市日必须是上一个交易日，不能是今天"
    # 涨跌三项照旧透传（它们本来就说的是 09-18 那天，口径一致）
    assert quote["08. previous close"] == "1390.00"
    assert quote["10. change percent"] == "0.72"


def test_quote_reports_today_once_the_market_is_open(monkeypatch):
    """交易日盘中：自报今天。不能为了修休市日而把正常情况也改错。"""
    _freeze(monkeypatch, tc.datetime(2026, 9, 21, 10, 12, tzinfo=tc.CN_TZ))
    monkeypatch.setattr(main, "get_quote", lambda symbol: {
        "代码": "600519", "名称": "贵州茅台", "最新价": "1420.00",
    })

    response = TestClient(main.app).get("/api/v1/quote/600519", headers=HEADERS)

    assert response.json()["Global Quote"]["07. latest trading day"] == "2026-09-21"


def test_quote_before_the_open_still_reports_the_previous_session(monkeypatch):
    """交易日开盘前：开市了，但最新价还是上一交易日的收盘价。

    这两件事必须分开表达（`tradingDay=true` + `quoteDate=上一交易日`）。
    合并成一个字段的话只有两种错法：要么把周五的价说成今天的，
    要么把今天说成休市。
    """
    _freeze(monkeypatch, tc.datetime(2026, 9, 21, 8, 0, tzinfo=tc.CN_TZ))
    monkeypatch.setattr(main, "get_quote", lambda symbol: {
        "代码": "600519", "名称": "贵州茅台", "最新价": "1400.00",
    })

    client = TestClient(main.app)
    quote = client.get("/api/v1/quote/600519", headers=HEADERS).json()["Global Quote"]
    status = client.get("/api/v1/market/status", headers=HEADERS).json()

    assert status["tradingDay"] is True
    assert status["phase"] == tc.PHASE_PRE_OPEN
    assert quote["07. latest trading day"] == "2026-09-18"


def test_market_status_requires_the_internal_token():
    """它和别的 /api/v1 端点一样受服务间鉴权保护（fail-closed）。"""
    response = TestClient(main.app).get("/api/v1/market/status", headers={"X-Internal-Token": "wrong"})
    assert response.status_code == 401
