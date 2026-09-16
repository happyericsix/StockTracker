"""相对时间解析：把"去年""上个月""上周三"变成绝对时间区间。

<h3>为什么必须在这里做，而不是交给模型</h3>
模型算不对日期（这是公开的已知弱点），而记忆系统里算错日期的代价特别高：
"用户去年买的时候成本 1500" 一旦被解析成本周，后面每一次回答都在一个错误的时间锚点上推理，
而且没有任何报错。所以：
- **相对时间在写入时解析**（那时"现在"是什么时候是确定的），把绝对区间落库；
- **原始措辞一起保留**（{@code raw_time_phrase}），将来就能回答"你当时说的是'去年'"；
- 解析失败就<b>不猜</b>：返回 None，宁可不带时间，也不要一个错的。

<h3>时区</h3>
固定 UTC+8（中国无夏令时），不依赖 zoneinfo/tzdata —— Windows 上没装 tzdata 时
{@code ZoneInfo("Asia/Shanghai")} 会直接抛异常。

<h3>输出格式</h3>
一律 ISO 日期时间（{@code 2025-03-01T00:00:00}）：Java 侧是 {@code LocalDateTime}，
只给 {@code 2025-03-01} 会反序列化失败。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from agent.memory_store import CN_TZ

# 区间精度：告诉调用方这个时间的"粒度"，比如"去年"只精确到年
PRECISION_DAY = "day"
PRECISION_WEEK = "week"
PRECISION_MONTH = "month"
PRECISION_YEAR = "year"


@dataclass(frozen=True)
class TimeSpan:
    """一个半开区间 [start, end]，两端都是本地时间字符串。"""

    start: str
    end: str
    label: str
    precision: str

    @property
    def point(self) -> str:
        """取一个"时间点"（区间的起点）——用于事实的 event_time。"""
        return self.start


def _now() -> datetime:
    return datetime.now(CN_TZ)


def _day_start(d: date) -> str:
    return f"{d.isoformat()}T00:00:00"


def _day_end(d: date) -> str:
    return f"{d.isoformat()}T23:59:59"


def _span(start: date, end: date, label: str, precision: str) -> TimeSpan:
    return TimeSpan(_day_start(start), _day_end(end), label, precision)


def month_range(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = date(year + (month // 12), (month % 12) + 1, 1) - timedelta(days=1)
    return start, end


def resolve(phrase, now: datetime = None) -> TimeSpan | None:
    """把一句时间说法解析成绝对区间；认不出来就返回 None（不猜）。"""
    if not phrase:
        return None
    text = str(phrase).strip()
    if not text:
        return None
    now = now or _now()
    today = now.date()

    span = _absolute(text)
    if span is not None:
        return span

    return _relative(text, today)


# ==================== 绝对时间 ====================

_ABS_YMD = re.compile(r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?")
_ABS_YM = re.compile(r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*月?")
_ABS_MD = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def _absolute(text: str) -> TimeSpan | None:
    match = _ABS_YMD.fullmatch(text) or _ABS_YMD.match(text)
    if match:
        try:
            d = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
        return _span(d, d, text, PRECISION_DAY)

    match = _ABS_YM.fullmatch(text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if not 1 <= month <= 12:
            return None
        start, end = month_range(year, month)
        return _span(start, end, text, PRECISION_MONTH)

    match = _ABS_MD.fullmatch(text)
    if match:
        # 只给月日时按"最近的一次"理解：提到"3月11日"通常是在说过去的事。
        # 若今年那一天还没到（且不是近在眼前），更可能是去年的那次。
        month, day = int(match.group(1)), int(match.group(2))
        today = _now().date()
        for year in (today.year, today.year - 1):
            try:
                d = date(year, month, day)
            except ValueError:
                continue
            if (d - today).days > 30:
                continue
            return _span(d, d, text, PRECISION_DAY)
    return None


# ==================== 相对时间 ====================

_WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_NUM_CN = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_AGO = re.compile(r"([0-9]+|[一两二三四五六七八九十]+)\s*(天|日|周|个?月|年)\s*(?:前|以前|之前)")


def _relative(text: str, today: date) -> TimeSpan | None:
    if text in ("今天", "今日", "本日"):
        return _span(today, today, text, PRECISION_DAY)
    if text in ("昨天", "昨日"):
        d = today - timedelta(days=1)
        return _span(d, d, text, PRECISION_DAY)
    if text == "前天":
        d = today - timedelta(days=2)
        return _span(d, d, text, PRECISION_DAY)
    if text == "大前天":
        d = today - timedelta(days=3)
        return _span(d, d, text, PRECISION_DAY)

    # 本周 / 上周 / 这周
    if text in ("本周", "这周", "这个星期", "本星期"):
        start = today - timedelta(days=today.weekday())
        return _span(start, start + timedelta(days=6), text, PRECISION_WEEK)
    if text in ("上周", "上个星期", "上星期"):
        start = today - timedelta(days=today.weekday() + 7)
        return _span(start, start + timedelta(days=6), text, PRECISION_WEEK)

    # 周X / 上周X / 这周X / 下周X
    # 注意前缀分支里的"周"是可选的：'上周三' 的"周"已经在前缀里了，
    # 如果要求前缀之后必须再有一个"周"，'上周三'就会解析失败（真实的 bug，已被测试钉住）。
    match = re.fullmatch(
        r"(?:(上上周|上周|下周|这周|本周)\s*(?:周|星期|礼拜)?|(?:周|星期|礼拜))\s*([一二三四五六日天])",
        text)
    if match:
        prefix, weekday_char = match.group(1), match.group(2)
        base = today - timedelta(days=today.weekday())
        if prefix == "上周":
            base -= timedelta(days=7)
        elif prefix == "上上周":
            base -= timedelta(days=14)
        elif prefix == "下周":
            base += timedelta(days=7)
        d = base + timedelta(days=_WEEKDAYS[weekday_char])
        return _span(d, d, text, PRECISION_DAY)

    if text in ("本月", "这个月", "当月"):
        start, end = month_range(today.year, today.month)
        return _span(start, end, text, PRECISION_MONTH)
    if text in ("上个月", "上月"):
        year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        start, end = month_range(year, month)
        return _span(start, end, text, PRECISION_MONTH)

    if text in ("今年", "本年"):
        return _span(date(today.year, 1, 1), today, text, PRECISION_YEAR)
    if text in ("去年", "上年"):
        return _span(date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), text, PRECISION_YEAR)
    if text == "前年":
        return _span(date(today.year - 2, 1, 1), date(today.year - 2, 12, 31), text, PRECISION_YEAR)

    # 去年 3 月 / 前年 12 月
    match = re.fullmatch(r"(去年|前年|今年)\s*(\d{1,2})\s*月(?:份)?", text)
    if match:
        offset = {"今年": 0, "去年": 1, "前年": 2}[match.group(1)]
        month = int(match.group(2))
        if not 1 <= month <= 12:
            return None
        start, end = month_range(today.year - offset, month)
        return _span(start, end, text, PRECISION_MONTH)

    # 去年这个时候 / 去年今天
    match = re.fullmatch(r"(去年|前年)\s*(?:这个?时候|同期|今天)", text)
    if match:
        offset = {"去年": 1, "前年": 2}[match.group(1)]
        try:
            d = date(today.year - offset, today.month, today.day)
        except ValueError:  # 2 月 29 日
            d = date(today.year - offset, today.month, 28)
        return _span(d, d, text, PRECISION_DAY)

    # N 天/周/个月/年前
    match = _AGO.search(text)
    if match:
        amount = _parse_amount(match.group(1))
        unit = match.group(2)
        if amount is None:
            return None
        if unit in ("天", "日"):
            d = today - timedelta(days=amount)
            return _span(d, d, text, PRECISION_DAY)
        if unit == "周":
            start = today - timedelta(days=amount * 7 + today.weekday())
            return _span(start, start + timedelta(days=6), text, PRECISION_WEEK)
        if unit.endswith("月"):
            total = today.year * 12 + (today.month - 1) - amount
            start, end = month_range(total // 12, total % 12 + 1)
            return _span(start, end, text, PRECISION_MONTH)
        if unit == "年":
            year = today.year - amount
            return _span(date(year, 1, 1), date(year, 12, 31), text, PRECISION_YEAR)

    return None


def _parse_amount(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    if not raw:
        return None
    # 十一 / 二十 / 两 这类简单中文数字
    if raw == "十":
        return 10
    if raw.startswith("十"):
        tail = _NUM_CN.get(raw[1:], None)
        return None if tail is None else 10 + tail
    if len(raw) == 1:
        return _NUM_CN.get(raw)
    return None
