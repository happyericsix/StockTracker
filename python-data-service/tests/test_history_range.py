# -*- coding: utf-8 -*-
"""取数区间：**请求什么，就得到什么**。

<h3>这个文件在防什么（每一条都是实测出来的）</h3>
腾讯 `fqkline` 接口的 `limit` 有三个坑，且全都**静默出错**：

1. 它是"区间内**最近** N 根"。所以请求 `2023-01-01 ~ 2026-09-17` 却只给
   （默认的）500 根时，回来的其实是 `2024-08-27` 之后那 500 根 —— 起点被砍掉一年半；
2. 一页最多 **640** 根（探针实测：`limit=925` → 640 根）；
3. `limit` 超过 640 时接口直接返回**空**（`limit=3000` → 0 根），
   于是"给我整段历史"会伪装成"这只票没有历史数据"。

这三条合起来意味着：**"从某年起做样本外验证"这件事，靠调大 limit 是做不到的**，
必须分页。而这件事可怕的地方在于表上看不出来 —— 分段照切、数字照算、
`start_date` 照写 2023-01-01，看表的人会把它当成"跨越了三年牛熊"的证据。

所以这里钉三件事：
1. 页数由**区间**决定，每页不超过源头一页的上限；
2. 页面拼起来是**连续、无重、无漏**的一条序列；
3. 拿回来之后本地裁到区间内 —— 给多了给少了，最终口径都由我们负责。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import akshare_client  # noqa: E402
from akshare_client import (  # noqa: E402
    CHUNK_CALENDAR_DAYS,
    DEFAULT_HISTORY_COUNT,
    MAX_PAGES,
    SOURCE_MAX_BARS,
    _limit_for_range,
    _windows_for,
    get_history,
)

TODAY = datetime.now().strftime("%Y-%m-%d")


# ==================== 1. 每页取多少根 ====================


def test_no_range_keeps_the_default():
    assert _limit_for_range("", "", None) == DEFAULT_HISTORY_COUNT


def test_a_range_takes_a_full_page():
    """有区间就取满一页：区间由分页负责，根数不再靠"反推"。"""
    assert _limit_for_range("2023-01-01", "2026-09-17", None) == SOURCE_MAX_BARS


def test_an_explicit_count_is_honoured_but_capped():
    assert _limit_for_range("", "", 120) == 120
    # 超过源头一页的上限会**返回空**（探针验证过），所以按上限截断
    assert _limit_for_range("", "", 3000) == SOURCE_MAX_BARS


# ==================== 2. 分页：连续、不超上限、覆盖整个区间 ====================


def test_without_a_range_there_is_a_single_page():
    assert _windows_for("", "") == [("", "")]


def test_a_three_and_a_half_year_range_is_paginated():
    """2023-01-01 ~ 2026-09-17 ≈ 900 交易日，比一页的 640 根多，必须分页。"""
    windows = _windows_for("2023-01-01", "2026-09-17")

    assert len(windows) >= 2
    assert windows[0][0] == "2023-01-01"
    assert windows[-1][1] == "2026-09-17"


def test_pages_are_contiguous_and_do_not_overlap():
    """接缝处不能漏一天、也不能重叠 —— 漏了就少一根 K 线，重了就会多一笔假信号。"""
    windows = _windows_for("2023-01-01", "2026-09-17")

    for (start, end), (next_start, _) in zip(windows, windows[1:]):
        assert start <= end
        gap = (datetime.strptime(next_start, "%Y-%m-%d")
               - datetime.strptime(end, "%Y-%m-%d")).days
        assert gap == 1, f"{end} 与 {next_start} 之间不连续"


def test_every_page_stays_within_the_chunk_size():
    windows = _windows_for("2020-01-01", TODAY)
    for start, end in windows:
        span = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days
        assert span < CHUNK_CALENDAR_DAYS


def test_a_short_range_is_a_single_page():
    assert _windows_for("2026-01-01", "2026-03-01") == [("2026-01-01", "2026-03-01")]


def test_pages_are_capped_but_keep_the_end_near_the_decision_day():
    """**这条是真跑逼出来的。**

    只给 `end_date`（agent 端点就是这样调的：决策日）时，起点是"往前推 MAX_PAGES 页"算的，
    所以窗口数恰好等于上限。但只要上限调小或页跨度变小，就出现"页数不够" ——
    而当时实现丢的是**最近**那一页，于是拿回来的数据停在**一个月前**，
    agent 照样在上面做决策，价格、指标、辩论全都"合理"，报告上完全看不出来。

    页数不够时唯一正确的选择：丢最早的一段，**保住决策日附近**。
    """
    windows = _windows_for("1995-01-01", TODAY)

    assert len(windows) == MAX_PAGES
    assert windows[-1][1] == TODAY, "最后一页必须落到请求的终点上"
    # 只给终点的调用（agent 端点）也必须覆盖到终点
    open_ended = _windows_for("", TODAY)
    assert open_ended[-1][1] == TODAY


def test_dropping_pages_says_which_part_is_missing(caplog):
    """丢掉的那一段必须报出来（而不是静默地少给一段）。"""
    import logging

    with caplog.at_level(logging.WARNING):
        _windows_for("1995-01-01", TODAY)

    messages = [record.getMessage() for record in caplog.records]
    assert any("保留最近" in message for message in messages), messages


def test_an_unparsable_range_falls_back_instead_of_raising():
    assert _windows_for("去年", "今年") == [("", "")]
    assert _windows_for("2026-09-17", "2023-01-01") == [("", "")]


def test_an_open_ended_range_starts_before_today():
    windows = _windows_for("", TODAY)
    assert windows[0][0] < TODAY, "只给终点时要往前推，而不是退化成单页"


# ==================== 3. 本地裁剪：越界由我们自己负责 ====================

def _payload(dates, code="sh600519", period="day"):
    klines = [[date, "10", "10", "11", "9", "100"] for date in dates]
    return {"data": {code: {f"qfq{period}": klines}}}


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _patch_source(monkeypatch, payload_by_call, seen):
    """`payload_by_call`：可以是固定 payload，也可以是 (param) -> payload 的函数。"""
    def fake_get(url, params=None, headers=None, timeout=None):
        param = (params or {}).get("param", "")
        seen.append(param)
        payload = payload_by_call(param) if callable(payload_by_call) else payload_by_call
        return _Response(payload)

    monkeypatch.setattr(akshare_client.requests, "get", fake_get)
    monkeypatch.setattr(akshare_client, "resolve_symbol", lambda symbol: "sh600519")
    monkeypatch.setattr(akshare_client, "normalize_symbol", lambda symbol: "sh600519")
    akshare_client._history_cache.clear()


def _bars_for(param):
    """按请求区间造数据：每页给 3 天（真实接口的行为是"区间内最近 N 根"）。"""
    parts = param.split(",")
    start, end = parts[2], parts[3]
    return _payload([start, "2026-01-03", end] if start else ["2026-01-02"])


def test_history_is_clipped_to_the_requested_range(monkeypatch):
    """接口多给了前后各一天，返回的只能是区间内的那些。"""
    seen = []
    _patch_source(monkeypatch, _payload(
        ["2025-12-31", "2026-01-02", "2026-01-05", "2026-01-06"]), seen)

    bars = get_history("600519", "2026-01-01", "2026-01-05", "day")

    assert [bar["date"] for bar in bars] == ["2026-01-02", "2026-01-05"]


def test_the_request_carries_the_page_range_and_the_adjust_mode(monkeypatch):
    """参数是**原样**发出去的：区间要进 URL，复权口径要进 URL，根数不能超上限。"""
    seen = []
    _patch_source(monkeypatch, _payload(["2026-01-02"]), seen)

    get_history("600519", "2026-01-01", "2026-01-05", "day")

    assert seen, "应当发出一次请求"
    parts = seen[0].split(",")
    assert parts[0] == "sh600519" and parts[1] == "day"
    assert parts[2] == "2026-01-01" and parts[3] == "2026-01-05"
    assert int(parts[4]) <= SOURCE_MAX_BARS, "根数超过上限时接口返回空"
    assert parts[5] == akshare_client.ADJUST_MODE


def test_a_page_ending_today_is_fetched_with_an_open_end(monkeypatch):
    """终点落在"今天"的那页要用**开放终点**取数（2026-10-09 事故）。

    实测腾讯的封闭区间查询在特定根数下会悄悄丢掉最新一根：结算端点按
    as-of 带 end=当天 取数，15:30 / 16:10 / 17:10 三次结算与晚间补跑全部
    no_bar，而"最近 N 根"查询却看得到当天 bar —— 差别只在终点是否封死
    （同一区间 limit 640 返回 596 根止于前一日，end 留空才返回 597 根含当天）。
    as-of 语义由本地裁剪兜底（下一条用例盯着）。
    """
    seen = []
    _patch_source(monkeypatch, _bars_for, seen)

    bars = get_history("600519", "2023-01-01", TODAY, "day")

    assert bars, "应当取到数据"
    last_page_end = seen[-1].split(",")[3]
    assert last_page_end == "", "覆盖今天的页必须开放终点，否则最新一根可能被源头丢掉"


def test_open_end_fetch_still_clips_to_the_requested_as_of_date(monkeypatch):
    """开放终点多取到的数据不能越过 as-of：本地裁剪是第二道防线。"""
    def generous(param):
        # 最后一页（开放终点）连"明天"都给回来 —— 本地必须裁掉
        if param.split(",")[3] == "":
            return _payload(["2026-10-08", "2026-10-09", "2026-10-10"])
        return _bars_for(param)

    seen = []
    _patch_source(monkeypatch, generous, seen)

    bars = get_history("600519", "2023-01-01", TODAY, "day")
    dates = [bar["date"] for bar in bars]
    assert dates, "应当取到数据"
    assert dates[-1] <= TODAY, "as-of：不许有越过请求终点的数据"


def test_a_long_range_issues_one_request_per_page_and_merges_them(monkeypatch):
    """端到端的分页：三年半的区间要发多页请求，合并后是一整条序列。

    这条用例是这次实测的核心回归 —— 只发一页的话，
    "从 2023 年起"会被悄悄砍成"最近 640 根"。
    """
    seen = []
    _patch_source(monkeypatch, _bars_for, seen)

    bars = get_history("600519", "2023-01-01", "2026-09-17", "day")

    assert len(seen) >= 2, "区间超过一页时必须分页"
    dates = [bar["date"] for bar in bars]
    assert dates == sorted(dates), "合并后必须按时间升序"
    assert len(dates) == len(set(dates)), "接缝处不能重复"
    assert dates[0] == "2023-01-01" and dates[-1] == "2026-09-17"


def test_a_range_outside_the_available_data_returns_none(monkeypatch):
    """区间里一根都没有 → 没有数据（调用方据此报"取不到"，而不是给一段错的数据）。"""
    seen = []
    _patch_source(monkeypatch, _payload(["2020-01-02", "2020-01-03"]), seen)
    assert get_history("600519", "2026-01-01", "2026-01-05", "day") is None


def test_the_cache_key_includes_the_range(monkeypatch):
    """区间不同就是不同的数据：少了这一段，"最近 500 根"会顶掉"某年某段"。"""
    seen = []
    _patch_source(monkeypatch, _payload(["2026-01-02", "2026-01-05"]), seen)

    get_history("600519", "2026-01-01", "2026-01-05", "day")
    get_history("600519", "2026-01-01", "2026-01-05", "day")
    assert len(seen) == 1, "同一区间应命中缓存"

    get_history("600519", "2025-01-01", "2025-01-05", "day")
    assert len(seen) == 2, "换了区间就不能复用缓存"


def test_an_empty_page_does_not_wipe_the_other_pages(monkeypatch):
    """某一页空（新股早期、接口抽风）不能让整段变成"没有数据"。"""
    seen = []

    def flaky(param):
        return _payload([]) if "2024-01-01" <= param.split(",")[2] < "2025-01-01" else _bars_for(param)

    _patch_source(monkeypatch, flaky, seen)

    bars = get_history("600519", "2023-01-01", "2026-09-17", "day")

    assert bars, "一页空不等于整段空"
    assert all(bar["date"] for bar in bars)


# ==================== 4. 同源退避重试（2026-10-19/20 事故） ====================
#
# 连续两天 15:30 结算时段腾讯 ifzq 拒服 100+ 分钟，单次请求直接判死、当天结算
# data_unavailable。这里钉住三级语义：网络类异常要重试、重试用尽返回 None
# （失败交给调用方落痕）、"正常返回但为空"不重试（空是源的正常回答）。


def _patch_flaky_source(monkeypatch, plan, seen):
    """plan：第 n 次调用（从 1 起）的处置——'conn' 抛连接错、'value' 抛解析错、
    否则按该值当 payload 返回。重试退避清零，测试不等真实 sleep。"""
    monkeypatch.setattr(akshare_client, "KLINE_RETRY_BACKOFF_S", 0.0)
    calls = {"n": 0}

    def flaky_get(url, params=None, headers=None, timeout=None):
        calls["n"] += 1
        seen.append((params or {}).get("param", ""))
        action = plan[min(calls["n"], len(plan)) - 1]
        if action == "conn":
            raise akshare_client.requests.ConnectionError("connection refused")
        if action == "value":
            raise ValueError("expecting value: line 1 column 1 (char 0)")
        return _Response(action)

    monkeypatch.setattr(akshare_client.requests, "get", flaky_get)
    monkeypatch.setattr(akshare_client, "resolve_symbol", lambda symbol: "sh600519")
    monkeypatch.setattr(akshare_client, "normalize_symbol", lambda symbol: "sh600519")
    akshare_client._history_cache.clear()


def test_a_network_error_is_retried_and_then_succeeds(monkeypatch):
    """第一次连接被拒、第二次成功：get_history 照常返回——正是事故那天缺的一级兜底。"""
    seen = []
    _patch_flaky_source(monkeypatch, ["conn", _payload(["2026-01-02"])], seen)

    bars = get_history("600519")

    assert bars and bars[0]["date"] == "2026-01-02"
    assert len(seen) == 2, "失败一次后应当重试一次"


def test_a_json_parse_failure_is_also_retried(monkeypatch):
    """限流页/HTML 插页回来的是解析不了的 body（ValueError 分支），同样要重试。"""
    seen = []
    _patch_flaky_source(monkeypatch, ["value", _payload(["2026-01-02"])], seen)

    bars = get_history("600519")

    assert bars and bars[0]["date"] == "2026-01-02"
    assert len(seen) == 2


def test_all_attempts_failing_returns_none(monkeypatch):
    """重试用尽仍失败：返回 None，由调用方按 data_unavailable 落痕——不是抛异常。"""
    seen = []
    _patch_flaky_source(monkeypatch, ["conn", "conn", "conn"], seen)

    assert get_history("600519") is None
    assert len(seen) == akshare_client.KLINE_RETRY_ATTEMPTS


def test_an_empty_answer_is_not_retried(monkeypatch):
    """200 但为空是源的正常回答（新股/超出源头历史）：重试只会加倍延迟。"""
    seen = []
    _patch_flaky_source(monkeypatch, [_payload([])], seen)

    assert get_history("600519") is None
    assert len(seen) == 1, "空响应不该重试"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print("PASS", fn.__name__)
