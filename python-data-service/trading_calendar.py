"""
A 股交易日历与市场状态。

<h3>为什么必须有这个模块</h3>
用户的原话是"明明是在休息日，却显示昨收，这里难道不应该提醒用户几天休息吗"。
这不只是文案问题 —— 它是一个**数据口径**问题：

腾讯行情在休市日仍会返回上一交易日的收盘价（`最新价` = 周五收盘、`昨收` = 周四收盘、
`涨跌幅` = 周五当天的涨跌）。而 `app.py` 的 `/api/v1/quote/{symbol}` 此前把
`lastTradingDay` 硬编码成 `str(date.today())`，于是**周日的行情被标成了周日的数据**。
前端把它渲染成"更新 2026-09-20"，用户看到的就是"今天休市，却给我一个今天的涨跌幅"。

所以这里要做两件事：① 提供"今天到底开不开市、休到哪天"的事实；
② 让行情能说清"这个价是哪一天的"（`quote_date`）。

<h3>数据来源与诚实边界</h3>
交易日历取自 akshare 的 `tool_trade_date_hist_sina()`（新浪的 A 股交易日列表，
含**已公布**的未来休市安排 —— 实测 2026-09-25 中秋调休、2026-10-01 起国庆都在里面）。
这份数据覆盖 `[min, max]`：超出这个范围的日期**我们不知道**，就必须说不知道，
按"工作日近似"给一个下限答案并标 `known=false`。
把"不知道"说成"知道"，比给不出一份日历更糟（与 `PaperSchedule` 的既定口径一致）。

<h3>为什么不自己算节假日</h3>
中国的休市安排由国务院办公厅每年公告，包含**调休上班的周末**。任何"排除周末 + 硬编码
国庆春节"的做法都会在调休那几天给出错误答案，而错误的表现恰好就是本模块要消灭的
那个 bug（把休市说成开市）。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 中国无夏令时，固定 UTC+8。不依赖 zoneinfo/tzdata —— Windows 上没装 tzdata 时
# ZoneInfo("Asia/Shanghai") 会直接抛异常（与 agent/timeutil.py 同一处理）。
CN_TZ = timezone(timedelta(hours=8))

# 日历缓存有效期。休市安排一年只公布一两次，12 小时足够新鲜，
# 又不会让服务重启后每次都去新浪拉一遍。
CACHE_TTL_SECONDS = 12 * 3600

# 落盘缓存：让"重启后第一次请求"不必等 akshare，也让上游挂掉时还有上一版可用。
# 文件名不带点前缀：本仓库的运行时产物约定是"显式列进 .gitignore"，
# 而点开头的路径在部分 Windows 环境（含受限沙箱）会被直接拒绝创建。
CACHE_FILE = Path(__file__).parent / "market_calendar_cache.json"

_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 交易时段（Asia/Shanghai）。09:15 起集合竞价、09:30 起连续竞价、11:30 午休、15:00 收盘。
AUCTION_START = (9, 15)
MORNING_START = (9, 30)
MORNING_END = (11, 30)
AFTERNOON_START = (13, 0)
CLOSE = (15, 0)

PHASE_PRE_OPEN = "pre_open"
PHASE_AUCTION = "auction"
PHASE_MORNING = "morning"
PHASE_NOON_BREAK = "noon_break"
PHASE_AFTERNOON = "afternoon"
PHASE_POST_CLOSE = "post_close"
PHASE_CLOSED = "closed"

PHASE_LABELS = {
    PHASE_PRE_OPEN: "未开盘",
    PHASE_AUCTION: "集合竞价",
    PHASE_MORNING: "交易中",
    PHASE_NOON_BREAK: "午间休市",
    PHASE_AFTERNOON: "交易中",
    PHASE_POST_CLOSE: "已收盘",
    PHASE_CLOSED: "今日休市",
}

_lock = threading.Lock()
# 拉上游时单独一把锁。只有 `_lock` 的话，启动预热与第一次请求会**各拉一次**新浪
# （缓存是在拉完之后才写进去的），而这是最容易发生并发的那一瞬间。
_fetch_lock = threading.Lock()
_state: dict = {"dates": None, "loaded_at": 0.0, "source": "none"}


def _now() -> datetime:
    """当前时间（Asia/Shanghai）。

    抽成一个函数是为了让用例能把"今天"钉死 —— 本模块最容易错的地方全在**边界日**上
    （调休的工作日、长假首尾、开盘前），而边界日一年只出现几次：
    不钉时间的话，用例只在真正跑的那天才验证得到正确的东西。
    与 `agent/timeutil.py` 的 `_now()` 是同一套做法。
    """
    return datetime.now(CN_TZ)


# ==================== 日历加载 ====================


def _fetch_from_akshare() -> list[date]:
    """从 akshare 取全量交易日（历史 + 已公布的未来）。失败抛异常，由调用方决定降级。"""
    import akshare

    frame = akshare.tool_trade_date_hist_sina()
    column = frame["trade_date"]
    days: list[date] = []
    for value in column:
        # akshare 给的是 datetime.date；但仍兼容字符串/时间戳（上游改格式不该让整块失效）
        if isinstance(value, datetime):
            days.append(value.date())
        elif isinstance(value, date):
            days.append(value)
        else:
            text = str(value).strip()[:10]
            try:
                days.append(date.fromisoformat(text))
            except ValueError:
                logger.debug("交易日历里有无法解析的日期，已跳过：%r", value)
    return sorted(set(days))


def _read_disk_cache() -> Optional[list[date]]:
    if not CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return sorted({date.fromisoformat(d) for d in payload.get("dates", [])})
    except Exception as e:  # noqa: BLE001 — 缓存坏了只该降级，不该让接口失败
        logger.warning("交易日历磁盘缓存不可用（忽略）：%s", e)
        return None


def _write_disk_cache(days: list[date]) -> None:
    try:
        CACHE_FILE.write_text(
            json.dumps({"dates": [d.isoformat() for d in days]}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("交易日历磁盘缓存写入失败（不影响本次结果）：%s", e)


def _load(force: bool = False) -> tuple[list[date], str]:
    """取交易日列表（升序）。返回 `(dates, source)`，source 用于把"数据有多新"说清楚。

    降级顺序：内存 → akshare（成功即落盘）→ 磁盘缓存 → 空列表（调用方按"未知"处理）。
    **绝不抛异常**：日历取不到不该让行情接口整个失败，那会把"我不知道今天开不开市"
    升级成"你看不到任何行情"。
    """
    now = time.time()
    with _lock:
        cached = _state["dates"]
        if not force and cached and now - _state["loaded_at"] < CACHE_TTL_SECONDS:
            return cached, _state["source"]

    with _fetch_lock:
        # 双检：抢锁期间别人可能已经拉回来了（启动预热与第一次请求就是这种时序）
        now = time.time()
        with _lock:
            cached = _state["dates"]
            if not force and cached and now - _state["loaded_at"] < CACHE_TTL_SECONDS:
                return cached, _state["source"]

        try:
            days = _fetch_from_akshare()
            if days:
                _write_disk_cache(days)
                with _lock:
                    _state.update(dates=days, loaded_at=now, source="akshare")
                logger.info("交易日历已更新：%s ~ %s（%d 个交易日）", days[0], days[-1], len(days))
                return days, "akshare"
            logger.warning("交易日历返回空列表，改用磁盘缓存")
        except Exception as e:  # noqa: BLE001
            logger.warning("交易日历获取失败，改用磁盘缓存：%s", e)

        disk = _read_disk_cache()
        if disk:
            with _lock:
                # loaded_at 用当前时间：磁盘缓存也要享受完整的 TTL，否则每次请求都会重试上游
                _state.update(dates=disk, loaded_at=now, source="cache")
            logger.info("交易日历使用磁盘缓存：%s ~ %s", disk[0], disk[-1])
            return disk, "cache"

        with _lock:
            _state.update(dates=[], loaded_at=now, source="none")
        return [], "none"


def warm(background: bool = True) -> None:
    """预热日历（与 `akshare_client.warm_stock_list` 同一模式）。失败不影响服务。"""
    def _warm() -> None:
        try:
            _load()
        except Exception as e:  # noqa: BLE001
            logger.warning("交易日历预热失败（不影响服务）：%s", e)

    if not background:
        _warm()
        return
    threading.Thread(target=_warm, name="calendar-warm", daemon=True).start()


# ==================== 交易日查询 ====================


def _bisect_left(days: list[date], target: date) -> int:
    """第一个 >= target 的下标（自己写二分，避免为一个函数引入 bisect 的边界心智）。"""
    low, high = 0, len(days)
    while low < high:
        mid = (low + high) // 2
        if days[mid] < target:
            low = mid + 1
        else:
            high = mid
    return low


def is_trading_day(day: date, days: Optional[list[date]] = None) -> bool:
    days = _load()[0] if days is None else days
    index = _bisect_left(days, day)
    return index < len(days) and days[index] == day


def previous_trading_day(day: date, days: Optional[list[date]] = None) -> Optional[date]:
    """**严格早于** `day` 的最近一个交易日。"""
    days = _load()[0] if days is None else days
    index = _bisect_left(days, day)
    return days[index - 1] if index > 0 else None


def next_trading_day(day: date, days: Optional[list[date]] = None) -> Optional[date]:
    """**严格晚于** `day` 的最近一个交易日。"""
    days = _load()[0] if days is None else days
    index = _bisect_left(days, day)
    while index < len(days) and days[index] <= day:
        index += 1
    return days[index] if index < len(days) else None


# ==================== 市场状态 ====================


def _minutes(hour: int, minute: int) -> int:
    return hour * 60 + minute


def session_phase(day: date, now: Optional[datetime] = None, trading: bool = True) -> str:
    """当天的盘中阶段。非交易日恒为 `closed`。"""
    if not trading:
        return PHASE_CLOSED
    current = now or _now()
    minute = _minutes(current.hour, current.minute)
    if minute < _minutes(*AUCTION_START):
        return PHASE_PRE_OPEN
    if minute < _minutes(*MORNING_START):
        return PHASE_AUCTION
    if minute < _minutes(*MORNING_END):
        return PHASE_MORNING
    if minute < _minutes(*AFTERNOON_START):
        return PHASE_NOON_BREAK
    if minute < _minutes(*CLOSE):
        return PHASE_AFTERNOON
    return PHASE_POST_CLOSE


def _quote_date(today: date, trading: bool, phase: str, days: list[date]) -> Optional[date]:
    """`最新价` 属于哪一个交易日。

    <h3>为什么不能直接用"今天"</h3>
    这是本模块要修的那个 bug 的根：休市日腾讯回的仍是上一交易日的收盘价，
    开盘前（09:15 之前）回的同样是上一交易日的收盘价。把它标成"今天"，
    用户就会以为"今天是交易日、这是今天的涨跌"。
    """
    if trading and phase not in (PHASE_PRE_OPEN,):
        return today
    return previous_trading_day(today, days)


def _rest_window(today: date, days: list[date]) -> tuple[Optional[date], Optional[date], int]:
    """包含 `today` 的**连续休市区间** `(from, to, 天数)`；今天开市时返回 `(None, None, 0)`。

    <h3>为什么要一整段，而不是"今天休市"</h3>
    用户问的是"几天休息"。只说"今天休市"回答不了国庆那 8 天 —— 那正是最需要
    提前知道的时候（要不要提前调仓、什么时候回来）。
    """
    previous = previous_trading_day(today, days)
    following = next_trading_day(today, days)
    if previous is None or following is None:
        return None, None, 0
    start = previous + timedelta(days=1)
    end = following - timedelta(days=1)
    if end < start or not (start <= today <= end):
        return None, None, 0
    return start, end, (end - start).days + 1


def _closed_reason(start: date, end: date) -> str:
    """整段休市里只要有一个工作日，就是节假日休市（而不是"周末"）。"""
    day = start
    while day <= end:
        if day.weekday() < 5:
            return "holiday"
        day += timedelta(days=1)
    return "weekend"


def _fmt(day: Optional[date]) -> str:
    return day.strftime("%m-%d") if day else ""


def _note(today: date, trading: bool, phase: str, known: bool,
          rest_from: Optional[date], rest_to: Optional[date], rest_days: int,
          following: Optional[date], source: str) -> str:
    """给用户看的一句话。**直接渲染，前端不必再拼中文。**

    语气遵循本项目既有约定：宁可不精确，也不说错。所以凡是近似/未知，
    文案里必须出现"约""按工作日"这类限定词。
    """
    weekday = _WEEKDAY_CN[today.weekday()]
    if not known:
        hint = "" if trading else "（按工作日近似，节假日可能仍有休市）"
        tail = f"，下一交易日约 {_fmt(following)}（{_WEEKDAY_CN[following.weekday()]}）" if following else ""
        return f"交易日历未覆盖今天（{weekday}）{hint}{tail}"

    if trading:
        label = PHASE_LABELS[phase]
        if phase == PHASE_PRE_OPEN:
            return f"{label} · 今日 {_fmt(today)}（{weekday}）09:30 开盘"
        if phase == PHASE_POST_CLOSE:
            return f"{label} · 今日 {_fmt(today)}（{weekday}）收盘价"
        return f"{label} · 今日 {_fmt(today)}（{weekday}）"

    reason = _closed_reason(rest_from, rest_to) if rest_from and rest_to else "weekend"
    kind = "周末休市" if reason == "weekend" else "节假日休市"
    span = ""
    if rest_days > 1:
        span = f" · 本轮连休 {rest_days} 天（{_fmt(rest_from)} ~ {_fmt(rest_to)}）"
    tail = ""
    if following:
        gap = (following - today).days
        when = "明天" if gap == 1 else f"{gap} 天后"
        tail = f" · 下一交易日 {_fmt(following)}（{_WEEKDAY_CN[following.weekday()]}，{when}）"
    return f"今日{kind}（{weekday}）{span}{tail}"


def status(now: Optional[datetime] = None) -> dict:
    """今天的市场状态。**这是前端"休市提示"的唯一数据来源。**

    `now` 可注入，便于用例把时间钉死（真实的"今天"每天都不一样，
    而本模块最容易错的地方恰好都在边界日：调休、长假首尾、开盘前）。
    """
    current = now or _now()
    today = current.date()
    days, source = _load()

    known = bool(days) and days[0] <= today <= days[-1]
    if known:
        trading = is_trading_day(today, days)
    else:
        # 日历不覆盖今天：只能按"工作日"近似（与 PaperSchedule 同口径），并标记 known=false
        trading = today.weekday() < 5

    phase = session_phase(today, current, trading) if trading else PHASE_CLOSED

    if known:
        following = next_trading_day(today, days)
        previous = previous_trading_day(today, days)
        rest_from, rest_to, rest_days = (None, None, 0) if trading else _rest_window(today, days)
        covered = True
    else:
        # 近似：下一交易日 = 下一个工作日；上一个交易日 = 上一个工作日
        following = _next_weekday(today)
        previous = _previous_weekday(today)
        rest_from, rest_to, rest_days = (None, None, 0)
        if not trading:
            rest_from = previous + timedelta(days=1)
            rest_to = following - timedelta(days=1)
            rest_days = (rest_to - rest_from).days + 1
        covered = False

    quote_day = today if (trading and phase != PHASE_PRE_OPEN) else previous

    return {
        "date": today.isoformat(),
        "weekday": _WEEKDAY_CN[today.weekday()],
        "known": known,
        "calendarCovered": covered,
        "calendarFrom": days[0].isoformat() if days else "",
        "calendarTo": days[-1].isoformat() if days else "",
        "calendarSource": source,
        "tradingDay": trading,
        "phase": phase,
        "phaseLabel": PHASE_LABELS[phase],
        "quoteDate": quote_day.isoformat() if quote_day else "",
        "previousTradingDay": previous.isoformat() if previous else "",
        "nextTradingDay": following.isoformat() if following else "",
        "restDays": rest_days,
        "restFrom": rest_from.isoformat() if rest_from else "",
        "restTo": rest_to.isoformat() if rest_to else "",
        "note": _note(today, trading, phase, known, rest_from, rest_to, rest_days, following, source),
    }


def _next_weekday(day: date) -> date:
    cursor = day + timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor += timedelta(days=1)
    return cursor


def _previous_weekday(day: date) -> date:
    cursor = day - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor
