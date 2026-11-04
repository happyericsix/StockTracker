# -*- coding: utf-8 -*-
"""数据护栏（ACCOUNT-GATE 阶段0，2026-11-04）：盘中结算只消费「已完结、未过期、连续」的 bar。

<h3>为什么必须有这一层（三类真实事故，见 docs/ACCOUNT-GATE.md §2）</h3>
1. **完结口径**：分钟 bar 的标签是窗口**起点**（`akshare_client._minute_bar_start_label`
   为前端图表把新浪的结束标签回退成起点），而 Java 的已完结门控按「标签时刻」判定——
   标签口径改了门控没跟，bar 开始几秒即可放行；新浪最后一根又恰是**正在走的柱**
   （close=最新价），于是半根柱进了信号计算（2026-10-14 10:12:12 的成交即此）。
   修法：**在 Python 侧评估前丢掉未走完的柱**——单一真相源，Java 门控退居二线。
2. **数据年龄**：原有门控只挡「未来」（bar 没走完），不挡「过去」。数据源卡住/停牌/
   缓存穿透失败时，旧行情照样结算、照样刷市值。修法：最新完结 bar 距墙钟超过
   `2×period + 缓冲` → stale_data（fail-visible，对齐 Prometheus staleness 纪律）。
3. **序列连续性**：断档此前被当成「没有信号」。修法：bar 标签非严格递增、或当日
   根数明显少于已走交易分钟数 → data_gap（对齐 FIX 跳号检测语义：数据问题必须
   与市场结论分开）。顺带挡住盘前竞价柱（09:31 前出现的当日柱不可结算——
   2026-10-12 09:27:04 的盘前买入事故）。

<h3>刻意的取舍</h3>
- **只拦盘中（realtime）**：日线结算有自己的 bar_date_missing 门控，且收盘后日线
  「最后一根=今天」的年龄天然大，不该被分钟口径误伤。
- **连续性容忍宽（8 根）**：宁可漏报不可误伤——行情源偶尔少一两根是常态，
  年龄护栏才是硬闸（它对"数据源整体卡死"免疫不了的情况几乎不存在）。
- **失败也带 bar_time**：Java 侧空 bar_time 会静默 return，护栏失败就看不见了；
  带上最后一根完结柱的标签，让结算侧能落一行 skip 痕迹（dedupe 一天一原因一行）。
"""
from __future__ import annotations

import logging
from datetime import datetime, time as dtime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

SKIP_STALE_DATA = "stale_data"
SKIP_DATA_GAP = "data_gap"

#: 年龄上限缓冲：bar 完结后数据源落库 + 15s 轮询 + 45s 节流的正常链路延迟，再放一倍余量。
AGE_BUFFER_SECONDS = 90
#: 连续性容忍：当日实际根数比"已走交易分钟"少这么多根以上才算断档。
CONTINUITY_TOLERANCE = 8

_BAR_DT_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")


def _parse_bar_dt(text) -> Optional[datetime]:
    """bar 标签 → datetime（容两种格式）；解析不了返回 None，交给调用方决定。"""
    raw = str(text or "").strip()
    for fmt in _BAR_DT_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def expected_bars_today(now: datetime) -> int:
    """截至墙钟时刻，今天本应存在多少根 1 分钟 bar（按**起点标签**数）。

    A 股连续竞价 09:30-11:30 / 13:00-15:00：标签 09:30..11:29 各 120 根 +
    13:00..14:59 各 120 根。09:31 前返回 0（第一根 09:30 标签的柱 09:31 才完结），
    由此顺带定义了"盘前竞价柱不可结算"：expected=0 时当日任何柱都是竞价/预创建柱。
    """
    minute = now.hour * 60 + now.minute
    if now.time() < dtime(9, 31):
        return 0
    if now.time() <= dtime(11, 30):
        return max(0, minute - (9 * 60 + 30))
    if now.time() < dtime(13, 1):
        return 120
    if now.time() <= dtime(15, 0):
        return 120 + max(0, minute - (13 * 60))
    return 240


def drop_forming_bar(records: list[dict], period_minutes: int,
                     now: Optional[datetime] = None) -> tuple[list[dict], bool]:
    """丢掉最后一根未走完的柱（标签=窗口起点，起点+period > 墙钟即未完结）。

    返回 (新列表, 是否丢了)。标签解析不了的**不丢**：下游 evaluate_bar 按日期
    精确匹配，匹配不上自己会跳过，这里不替它做更激进的事。
    """
    records = list(records or [])
    if not records:
        return records, False
    now = now or datetime.now()
    dt = _parse_bar_dt(records[-1].get("date"))
    if dt is None:
        return records, False
    if now < dt + timedelta(minutes=int(period_minutes)):
        return records[:-1], True
    return records, False


def guard_minute_records(records: Optional[list[dict]], period_minutes: int,
                         now: Optional[datetime] = None) -> tuple[list[dict], Optional[dict]]:
    """三道护栏的统一入口（**仅盘中 realtime 路径调用**）。

    返回 ``(records, None)``：数据可用，records 已去掉未完结柱，放心评估；
    返回 ``(records, payload)``：本轮**不该用这份做任何决策**，调用方应把
    payload 原样回传给结算侧留痕（skip_reason/detail/…，见模块头"失败也带 bar_time"）。
    """
    now = now or datetime.now()
    period_minutes = max(1, int(period_minutes))
    records, dropped = drop_forming_bar(records, period_minutes, now)
    if not records:
        return records, _payload(SKIP_STALE_DATA, "盘中序列里没有任何已完结的 bar",
                                 {"forming_dropped": dropped, "bars": 0})

    parsed = [_parse_bar_dt(r.get("date")) for r in records]
    if any(d is None for d in parsed):
        # 标签脏值：整份不可信（指标序列与标签错位是 worst case），按断档处理
        return records, _payload(SKIP_DATA_GAP, "bar 标签存在无法解析的值",
                                 {"bars": len(records)})
    # ① 单调性：标签必须严格递增（乱序/回放数据会造出"假交叉"）
    if any(b <= a for a, b in zip(parsed, parsed[1:])):
        return records, _payload(SKIP_DATA_GAP, "bar 标签非严格递增（数据源异常）",
                                 {"bars": len(records)})

    last = parsed[-1]
    # ② 数据年龄：完结太久 = 数据源卡住/停牌/缓存穿透失败，宁可不动也不拿旧行情算
    age_seconds = (now - (last + timedelta(minutes=period_minutes))).total_seconds()
    if age_seconds > period_minutes * 60 * 2 + AGE_BUFFER_SECONDS:
        return records, _payload(
            SKIP_STALE_DATA,
            f"最新完结 bar 已落后墙钟 {age_seconds / 60:.0f} 分钟（疑似停牌/数据源卡住）",
            {"age_seconds": round(age_seconds, 1), "last_bar": str(records[-1].get("date"))})

    # ③ 连续性（只对最后一段会话有意义；expected=0 = 盘前，当日任何柱都不可结算）
    today = str(records[-1].get("date"))[:10]
    today_count = sum(1 for r in records if str(r.get("date", "")).startswith(today))
    expected = expected_bars_today(now)
    if expected <= 0:
        if today_count > 0:
            # 2026-10-12 事故的护栏：盘前竞价/预创建柱混进了序列
            return records, _payload(SKIP_DATA_GAP, "连续竞价开始前的当日柱不可结算（盘前/竞价数据）",
                                     {"today_bars": today_count})
    elif expected - today_count > CONTINUITY_TOLERANCE:
        return records, _payload(
            SKIP_DATA_GAP,
            f"当日应有约 {expected} 根分钟 bar，实际 {today_count} 根（断档）",
            {"expected": expected, "today_bars": today_count})
    return records, None


def _payload(skip_reason: str, detail: str, extra: dict) -> dict:
    return {"skip_reason": skip_reason, "detail": detail, **extra}
