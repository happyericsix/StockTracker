# -*- coding: utf-8 -*-
"""相对时间解析。

这是记忆系统里最容易被低估、又最容易造成长期错误的一块：
"去年买的时候成本 1500" 一旦被解析成"本周"，之后每一次回答都在错误的时间锚点上推理，
而且全程没有任何报错。所以这里的断言全部是**绝对日期**，不含糊。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import timeutil

# 2026-09-16 是星期三（周一 = 09-14，上周一 = 09-07）
NOW = datetime(2026, 9, 16, 10, 30, tzinfo=timeutil.CN_TZ)


def span(text):
    result = timeutil.resolve(text, NOW)
    return (result.start, result.end, result.precision) if result else None


def test_single_day_phrases():
    assert span("今天") == ("2026-09-16T00:00:00", "2026-09-16T23:59:59", "day")
    assert span("昨天") == ("2026-09-15T00:00:00", "2026-09-15T23:59:59", "day")
    assert span("前天") == ("2026-09-14T00:00:00", "2026-09-14T23:59:59", "day")
    assert span("大前天") == ("2026-09-13T00:00:00", "2026-09-13T23:59:59", "day")


def test_week_phrases():
    assert span("本周") == ("2026-09-14T00:00:00", "2026-09-20T23:59:59", "week")
    assert span("上周") == ("2026-09-07T00:00:00", "2026-09-13T23:59:59", "week")
    # 具体到星期几
    assert span("上周三")[0] == "2026-09-09T00:00:00"
    assert span("这周三")[0] == "2026-09-16T00:00:00"
    assert span("本周一")[0] == "2026-09-14T00:00:00"
    assert span("礼拜五")[0] == "2026-09-18T00:00:00"


def test_month_and_year_phrases():
    assert span("本月") == ("2026-09-01T00:00:00", "2026-09-30T23:59:59", "month")
    assert span("上个月") == ("2026-08-01T00:00:00", "2026-08-31T23:59:59", "month")
    assert span("去年") == ("2025-01-01T00:00:00", "2025-12-31T23:59:59", "year")
    assert span("前年") == ("2024-01-01T00:00:00", "2024-12-31T23:59:59", "year")
    assert span("今年") == ("2026-01-01T00:00:00", "2026-09-16T23:59:59", "year")


def test_year_boundary_month():
    # 1 月份问"上个月"要跨年，这是最容易写错的一个分支
    january = datetime(2026, 1, 15, 9, 0, tzinfo=timeutil.CN_TZ)
    result = timeutil.resolve("上个月", january)
    assert (result.start, result.end) == ("2025-12-01T00:00:00", "2025-12-31T23:59:59")


def test_year_offset_phrases():
    assert span("去年3月") == ("2025-03-01T00:00:00", "2025-03-31T23:59:59", "month")
    assert span("前年12月")[0] == "2024-12-01T00:00:00"
    # "去年这个时候"要落到同一天，而不是整年
    assert span("去年这个时候") == ("2025-09-16T00:00:00", "2025-09-16T23:59:59", "day")


def test_relative_ago_phrases():
    assert span("3天前")[0] == "2026-09-13T00:00:00"
    assert span("两周前") == ("2026-08-31T00:00:00", "2026-09-06T23:59:59", "week")
    assert span("三个月前") == ("2026-06-01T00:00:00", "2026-06-30T23:59:59", "month")
    assert span("一年前")[0] == "2025-01-01T00:00:00"
    assert span("十年前")[0] == "2016-01-01T00:00:00"


def test_absolute_dates():
    assert span("2025年3月11日") == ("2025-03-11T00:00:00", "2025-03-11T23:59:59", "day")
    assert span("2025-03-11")[0] == "2025-03-11T00:00:00"
    assert span("2025-03") == ("2025-03-01T00:00:00", "2025-03-31T23:59:59", "month")


def test_month_day_without_year_prefers_the_past():
    # 9 月 16 日提到"3月11日" → 今年的 3 月 11 日
    assert span("3月11日")[0] == "2026-03-11T00:00:00"
    # 提到"12月20日" → 今年还没到，应该理解成去年那次，而不是未来
    assert span("12月20日")[0] == "2025-12-20T00:00:00"


def test_unparseable_phrases_return_none_instead_of_guessing():
    for phrase in ("", "   ", "那会儿", "某个时候", "刚刚那个", "13月45日", None, 42):
        assert timeutil.resolve(phrase, NOW) is None, phrase


def test_invalid_dates_do_not_raise():
    assert timeutil.resolve("2025年2月30日", NOW) is None
    assert timeutil.resolve("2025-13", NOW) is None


def test_span_point_is_the_start():
    result = timeutil.resolve("去年", NOW)
    assert result.point == result.start == "2025-01-01T00:00:00"


def test_output_is_iso_datetime_not_date():
    """Java 侧是 LocalDateTime：只给 2025-03-01 会反序列化失败，必须是完整 ISO。"""
    result = timeutil.resolve("去年3月", NOW)
    assert "T" in result.start and "T" in result.end
    assert len(result.start) == 19


def test_defaults_to_now_when_not_provided():
    result = timeutil.resolve("今天")
    today = datetime.now(timeutil.CN_TZ).date().isoformat()
    assert result.start.startswith(today)


def test_weekday_of_today_is_what_the_fixture_assumes():
    """固定住基准日期，否则上面的断言会在别的日期悄悄失去意义。"""
    assert NOW.weekday() == 2  # 星期三
    assert (NOW + timedelta(days=-2)).date().isoformat() == "2026-09-14"
