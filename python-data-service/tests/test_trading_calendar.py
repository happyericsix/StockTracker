# -*- coding: utf-8 -*-
"""交易日历与市场状态（休市提示的数据基础）。

<h3>为什么这些用例值得写</h3>
用户的原话是"明明是在休息日，却显示昨收，这里难道不应该提醒用户几天休息吗"。
这个 bug 的全部成因都在**边界日**上，而边界日每年只出现几次：

- 周末（休市，但前一天有收盘价）；
- 调休出来的工作日休市（实测 2026-09-25 周五，中秋）；
- 长假首尾（实测 2026-10-01 ~ 10-07，下一个交易日是 10-08）；
- 交易日开盘前（未开盘，最新价其实还是上一交易日的收盘价）。

所以这里把"今天"当成参数钉死，逐个断言。不这么写的话，用例只在真正跑的那天才有意义 ——
而它恰好会是一年里的某一天，绝不是上面这几种。
"""
from datetime import date, datetime

import pytest

import trading_calendar as tc

# 一份**贴近真实**的日历：含周末、含调休休市的工作日、含长假。
# 与 akshare 实测返回一致（2026-09-25 休、2026-10-01~10-07 休、10-08 开市）。
_REAL = [
    "2026-09-16", "2026-09-17", "2026-09-18",
    "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24",
    "2026-09-28", "2026-09-29", "2026-09-30",
    "2026-10-08", "2026-10-09",
]
_CALENDAR = sorted(date.fromisoformat(d) for d in _REAL)


@pytest.fixture
def calendar(monkeypatch):
    """把日历换成固定的一份，并**不落盘**（测试不该写工作目录）。

    直接替换 `_load` 而不是 `_fetch_from_akshare`：前者把缓存、降级、磁盘
    三条路径一起换掉，用例因此不会因为"CI 上没网"而变成另一种行为。
    """
    monkeypatch.setattr(tc, "_load", lambda force=False: (_CALENDAR, "test"))
    monkeypatch.setattr(tc, "_write_disk_cache", lambda days: None)
    return _CALENDAR


def _at(year, month, day, hour=10, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=tc.CN_TZ)


def test_weekend_reports_the_rest_run_and_the_next_session(calendar):
    """周日：要说出"连休几天"和"下一个交易日是哪天"，而不只是"数据是周五的"。"""
    status = tc.status(_at(2026, 9, 20, 9, 57))

    assert status["tradingDay"] is False
    assert status["phase"] == tc.PHASE_CLOSED
    assert status["quoteDate"] == "2026-09-18"          # 最新价属于周五
    assert (status["restFrom"], status["restTo"], status["restDays"]) == \
           ("2026-09-19", "2026-09-20", 2)
    assert status["nextTradingDay"] == "2026-09-21"
    assert "连休 2 天" in status["note"]
    assert "09-21" in status["note"]
    assert status["known"] is True


def test_holiday_makeup_workday_is_not_reported_as_a_trading_day(calendar):
    """调休休市的工作日（2026-09-25 周五）。

    这是"只排除周末"那套近似一定会说错的一天：它是个工作日，但市场不开。
    说错的表现恰好就是本次要消灭的那个 bug —— 把休市说成开市。
    """
    status = tc.status(_at(2026, 9, 25, 10, 0))

    assert status["tradingDay"] is False, "周五但中秋调休休市，不能按工作日算开市"
    assert status["quoteDate"] == "2026-09-24"
    assert (status["restFrom"], status["restTo"], status["restDays"]) == \
           ("2026-09-25", "2026-09-27", 3)
    assert status["nextTradingDay"] == "2026-09-28"
    assert "节假日休市" in status["note"], "整段里含工作日 → 是节假日休市，不是周末"


def test_long_holiday_reports_the_whole_span(calendar):
    """国庆：用户最需要提前知道"休几天、哪天回来"的场景。"""
    status = tc.status(_at(2026, 10, 2, 10, 0))

    assert status["tradingDay"] is False
    assert (status["restFrom"], status["restTo"], status["restDays"]) == \
           ("2026-10-01", "2026-10-07", 7)
    assert status["nextTradingDay"] == "2026-10-08"
    assert status["quoteDate"] == "2026-09-30"
    assert "连休 7 天" in status["note"]


def test_before_the_open_the_price_still_belongs_to_the_previous_session(calendar):
    """交易日开盘前（周一 08:00）：开市，但最新价还是上一交易日的收盘价。

    这两件事必须**分开**表达：`tradingDay=true` 但 `quoteDate=上一交易日`。
    合成一个字段的话，前端只有两种错法 —— 要么把周五的价说成今天的，
    要么把今天说成休市。
    """
    status = tc.status(_at(2026, 9, 21, 8, 0))

    assert status["tradingDay"] is True
    assert status["phase"] == tc.PHASE_PRE_OPEN
    assert status["quoteDate"] == "2026-09-18"
    assert "09:30 开盘" in status["note"]


def test_intraday_phases(calendar):
    """盘中各阶段的判据（用户看的是"现在是不是交易时间"）。"""
    assert tc.status(_at(2026, 9, 21, 9, 20))["phase"] == tc.PHASE_AUCTION
    assert tc.status(_at(2026, 9, 21, 10, 12))["phase"] == tc.PHASE_MORNING
    assert tc.status(_at(2026, 9, 21, 12, 0))["phase"] == tc.PHASE_NOON_BREAK
    assert tc.status(_at(2026, 9, 21, 14, 0))["phase"] == tc.PHASE_AFTERNOON
    assert tc.status(_at(2026, 9, 21, 16, 0))["phase"] == tc.PHASE_POST_CLOSE

    # 交易中的每个阶段，最新价都属于今天
    for hour, minute in ((9, 20), (10, 12), (12, 0), (14, 0), (16, 0)):
        assert tc.status(_at(2026, 9, 21, hour, minute))["quoteDate"] == "2026-09-21"


def test_unknown_coverage_is_reported_as_unknown(monkeypatch):
    """日历不覆盖今天时必须说"不知道"，并给一个明确标注为近似的下限答案。

    这是本项目的既有纪律（`PaperSchedule`）：把"不知道哪天休市"说成"知道"，
    比给不出一份日历更糟。用例覆盖"日历只到去年"这种真实会发生的状态
    （每年 12 月前后新浪才会放出下一年的安排）。
    """
    stale = [date(2025, 1, 2), date(2025, 1, 5)]
    monkeypatch.setattr(tc, "_load", lambda force=False: (stale, "cache"))

    status = tc.status(_at(2026, 9, 21, 10, 0))   # 周一

    assert status["known"] is False
    assert status["calendarCovered"] is False
    assert status["tradingDay"] is True, "按工作日近似：周一是工作日"
    assert "未覆盖" in status["note"]
    assert status["nextTradingDay"] == "2026-09-22"


def test_missing_calendar_never_raises(monkeypatch):
    """日历彻底拿不到（上游挂 + 无磁盘缓存）时接口仍要能用。

    它不该把"我不知道今天开不开市"升级成"你看不到任何行情"。
    """
    monkeypatch.setattr(tc, "_load", lambda force=False: ([], "none"))

    status = tc.status(_at(2026, 9, 20, 10, 0))   # 周日

    assert status["known"] is False
    assert status["tradingDay"] is False, "周末即使没有日历也是确定的休市"
    assert status["nextTradingDay"] == "2026-09-21"   # 近似：下一个工作日


def test_trading_day_helpers_are_strict(calendar):
    """`previous/next` 都是**严格**早于/晚于，不把当天算进去。

    松一点就会让"今天开市"时的 `restDays` 变成负数或 1，
    而那种错误在页面上看起来只是一个略奇怪的数字 —— 没人会去查。
    """
    assert tc.previous_trading_day(date(2026, 9, 21)) == date(2026, 9, 18)
    assert tc.next_trading_day(date(2026, 9, 21)) == date(2026, 9, 22)
    assert tc.is_trading_day(date(2026, 9, 21)) is True
    assert tc.is_trading_day(date(2026, 9, 20)) is False
