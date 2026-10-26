# -*- coding: utf-8 -*-
"""数据读取修复的回归测试（2026-10-19）。

<h3>这几组用例在防什么（都是用户实测报出来的）</h3>
1. **分钟K线"未来时间"**：新浪分钟柱以窗口**结束**时刻标注，未走完的最后一根
   因此永远带未来时间——11:22 打开页面，5分K 最后一根标着 11:25、60分K 标到
   11:30，看起来像"价格比现在时间还快"。修复 = 标签统一回退为窗口起点
   （open-time 口径），且同一窗口跨请求标签稳定（evaluate_bar 按日期字符串
   精确匹配 bar，标签漂移会让结算重试找不到自己的 bar）。
2. **资金流"每次打开都慢"**：旧实现失败**不写**缓存、命中判断又是
   `if cached and ...`（空列表是假值），上游一挂每个请求都重新冷启动撞一遍。
   修复 = 失败负缓存 60s + 过期先回旧值后台刷新 + 冷启动 single-flight。
3. **资金流"一直不可用"**：东财 push2his 拒连机房 IP（家宽正常）。修复 =
   东财失败自动落腾讯 hsfundtab（50 日主力 + 当日四档），前端按字段缺失降级。
"""
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import akshare_client  # noqa: E402
from akshare_client import (  # noqa: E402
    FUND_FLOW_MAX_STALE,
    MARKET_FUND_FLOW_TTL,
    _fund_flow_cache,
    _fetch_fund_flow_rows_tencent,
    _minute_bar_start_label,
    _yi,
    get_market_fund_flow,
    get_minute_kline,
)


@pytest.fixture(autouse=True)
def _restore_module_caches():
    """快照/恢复模块级缓存：本文件会往里写"成功"数据，残留会让后面
    test_market_context 的 fail-open 用例（不桩资金流源、依赖真实取数为空）
    读到这份缓存而红——单跑永远发现不了，只有全量跑才炸。"""
    fund_snapshot = dict(_fund_flow_cache)
    with akshare_client._history_cache_lock:
        history_keys = set(akshare_client._history_cache)
        history_snapshot = dict(akshare_client._history_cache)
    yield
    _fund_flow_cache.clear()
    _fund_flow_cache.update(fund_snapshot)
    with akshare_client._history_cache_lock:
        for key in set(akshare_client._history_cache) - history_keys:
            akshare_client._history_cache.pop(key, None)
        akshare_client._history_cache.update(history_snapshot)


# ==================== 1. 分钟K线标签 = 窗口起点 ====================


def test_label_shifts_to_window_start():
    assert _minute_bar_start_label("2026-10-19 11:25:00", 5) == "2026-10-19 11:20:00"
    # 早盘第一根：09:35（覆盖 09:30-09:35）→ 09:30
    assert _minute_bar_start_label("2026-10-19 09:35:00", 5) == "2026-10-19 09:30:00"
    # 午后第一根：13:05 → 13:00（跨午休不掉到 12:55 是数据本身没这根，不是换算错）
    assert _minute_bar_start_label("2026-10-19 13:05:00", 5) == "2026-10-19 13:00:00"
    # 60分K 的窗口终点 11:30 → 起点 10:30
    assert _minute_bar_start_label("2026-10-19 11:30:00", 60) == "2026-10-19 10:30:00"
    assert _minute_bar_start_label("2026-10-19 14:00:00", 60) == "2026-10-19 13:00:00"


def test_label_keeps_dirty_values_as_is():
    # 脏值不硬转：原样透传交给下游过滤，一个坏标签不该炸掉整段数据
    assert _minute_bar_start_label("nan", 5) == "nan"
    assert _minute_bar_start_label("", 5) == ""


def _fake_minute_df():
    # 三根 5 分K：两根已走完（09:35/09:40），最后一根 11:25 是未走完的当前柱
    return pd.DataFrame({
        "day": ["2026-10-19 09:35:00", "2026-10-19 09:40:00", "2026-10-19 11:25:00"],
        "open": ["1500.0", "1501.0", "1502.0"],
        "close": ["1501.0", "1502.0", "1503.5"],
        "high": ["1501.5", "1502.5", "1504.0"],
        "low": ["1499.5", "1500.5", "1501.5"],
        "volume": ["1200", "1300", "450"],
    })


def test_get_minute_kline_labels_are_window_start(monkeypatch):
    monkeypatch.setattr(akshare_client.akshare, "stock_zh_a_minute",
                        lambda symbol, period, adjust: _fake_minute_df())
    with akshare_client._history_cache_lock:
        akshare_client._history_cache.pop(("sh600519", "m5"), None)

    rows = get_minute_kline("sh600519", 5)

    # 标签全部回退一个窗口：第一根 09:30，未走完的当前柱标 11:20（不再是未来的 11:25）
    assert [r["date"] for r in rows] == [
        "2026-10-19 09:30:00", "2026-10-19 09:35:00", "2026-10-19 11:20:00",
    ]
    # 价格字段原样透传（复权口径的换算不在这层）
    assert rows[-1]["close"] == "1503.5"


def test_get_minute_kline_retries_a_network_error(monkeypatch):
    """分钟线（新浪源）与日线共用同源退避重试：盘中结算依赖它，
    抖一下就白白损失一整天的盘中评估（2026-10-21 与日线同一批补的兜底）。"""
    monkeypatch.setattr(akshare_client, "KLINE_RETRY_BACKOFF_S", 0.0)
    calls = {"n": 0}

    def flaky_minute(symbol, period, adjust):
        calls["n"] += 1
        if calls["n"] == 1:
            raise akshare_client.requests.ConnectionError("connection refused")
        return _fake_minute_df()

    monkeypatch.setattr(akshare_client.akshare, "stock_zh_a_minute", flaky_minute)
    with akshare_client._history_cache_lock:
        akshare_client._history_cache.pop(("sh600519", "m5"), None)

    rows = get_minute_kline("sh600519", 5)

    assert rows and rows[-1]["close"] == "1503.5"
    assert calls["n"] == 2, "失败一次后应当重试一次"


# ==================== 2. 资金流缓存语义（两个源统一走这套机器） ====================

# 源函数统一打桩：直接桩 _fetch_fund_flow_rows_em/_tencent，链路在
# _fetch_market_fund_flow_rows 里组装——缓存层测试不碰真网络。


def _fund_df(dates_main):
    """东财 stock_market_fund_flow 的最小替身：{日期: 主力净流入(元)}。"""
    return pd.DataFrame([
        {"日期": d, "上证-收盘价": 3000 + i, "上证-涨跌幅": 0.5,
         "主力净流入-净额": main, "超大单净流入-净额": main / 2,
         "大单净流入-净额": main / 2, "中单净流入-净额": -main / 4,
         "小单净流入-净额": -main / 2}
        for i, (d, main) in enumerate(dates_main)
    ])


OLD_DF = _fund_df([("2026-10-15", 1e9), ("2026-10-16", 2e9)])
FRESH_DF = _fund_df([("2026-10-15", 1e9), ("2026-10-16", 2e9), ("2026-10-19", 3e9)])


def _reset_fund_flow_cache(**state):
    _fund_flow_cache.update({"at": 0.0, "tried_at": 0.0, "rows": [], "ok": False,
                             "refreshing": False})
    _fund_flow_cache.update(state)


def test_failure_is_negatively_cached(monkeypatch):
    """两个源都失败也进缓存（短 TTL）：负缓存窗口内重复请求不再打上游。"""
    calls = {"em": 0, "tencent": 0}

    def em_fail():
        calls["em"] += 1
        raise ConnectionError("东财拒连")

    def tencent_fail():
        calls["tencent"] += 1
        raise ConnectionError("腾讯也挂了")

    monkeypatch.setattr(akshare_client, "_fetch_fund_flow_rows_em", em_fail)
    monkeypatch.setattr(akshare_client, "_fetch_fund_flow_rows_tencent", tencent_fail)
    _reset_fund_flow_cache()

    assert get_market_fund_flow() == []
    assert get_market_fund_flow() == []
    assert get_market_fund_flow() == []
    assert calls == {"em": 1, "tencent": 1}   # 旧实现这里每请求都冷启动撞两遍


def test_success_served_from_ttl_cache(monkeypatch):
    state = {"calls": 0}

    def em_ok():
        state["calls"] += 1
        return _em_shape(OLD_DF)

    monkeypatch.setattr(akshare_client, "_fetch_fund_flow_rows_em", em_ok)
    monkeypatch.setattr(akshare_client, "_fetch_fund_flow_rows_tencent", lambda max_days=50: [])
    _reset_fund_flow_cache()

    first = get_market_fund_flow()
    assert get_market_fund_flow() == first
    assert state["calls"] == 1        # 第二次命中 TTL 缓存，不再打源
    assert first[-1]["date"] == "2026-10-16"
    assert first[-1]["main_net_yi"] == 20.0   # 元 → 亿元
    assert first[-1]["super_net_yi"] == 10.0  # 四档历史齐全（东财口径）


def _em_shape(df):
    """直接调用东财整形主体（绕过 akshare 网络层）：拿 DataFrame 走同一段列映射。"""
    rows = []
    for _, r in df.iterrows():
        d = str(r.get("日期"))[:10]
        if len(d) != 10:
            continue
        row = {"date": d,
               "sh_close": akshare_client._clean_number(r.get("上证-收盘价")),
               "sh_pct": akshare_client._clean_number(r.get("上证-涨跌幅"))}
        for key, column in akshare_client._FUND_NET_COLUMNS.items():
            row[f"{key}_net_yi"] = _yi(r.get(column))
        rows.append(row)
    rows.sort(key=lambda x: x["date"])
    return rows


def test_em_failure_falls_back_to_tencent(monkeypatch):
    """东财拒连（机房场景）→ 腾讯源接管，行形状与东财同形（缺档 None）。"""
    tencent_rows = [{"date": "2026-10-18", "sh_close": 3000.0, "sh_pct": 0.5,
                     "main_net_yi": -12.34, "super_net_yi": None, "big_net_yi": None,
                     "mid_net_yi": None, "small_net_yi": None}]

    def em_fail():
        raise ConnectionError("机房 IP 被拒")

    monkeypatch.setattr(akshare_client, "_fetch_fund_flow_rows_em", em_fail)
    monkeypatch.setattr(akshare_client, "_fetch_fund_flow_rows_tencent",
                        lambda max_days=50: tencent_rows)
    _reset_fund_flow_cache()

    served = get_market_fund_flow()
    assert served == tencent_rows
    assert served[-1]["main_net_yi"] == -12.34
    assert served[-1]["super_net_yi"] is None    # 前端按缺档降级


def test_stale_served_immediately_and_refreshed_in_background(monkeypatch):
    """过期但未超龄：本次调用立刻回旧值（不等上游），后台线程把新值写进缓存。"""
    state = {"calls": 0}
    in_bg = threading.Event()   # 上游替身进门后先停住：让"同步路径没等上游"可断言

    def source():
        state["calls"] += 1
        in_bg.set()
        gate.wait(5)
        return _em_shape(FRESH_DF)

    gate = threading.Event()

    # 先让缓存里有一份真实整形的旧数据，再把时钟拨回"11 分钟前成功取到"
    monkeypatch.setattr(akshare_client, "_fetch_fund_flow_rows_em", lambda: _em_shape(OLD_DF))
    monkeypatch.setattr(akshare_client, "_fetch_fund_flow_rows_tencent", lambda max_days=50: [])
    _reset_fund_flow_cache()
    get_market_fund_flow()
    with akshare_client._fund_flow_cache_lock:
        old_rows = list(akshare_client._fund_flow_cache["rows"])
        _fund_flow_cache["at"] = time.time() - MARKET_FUND_FLOW_TTL - 5
        _fund_flow_cache["tried_at"] = time.time() - MARKET_FUND_FLOW_TTL - 5

    monkeypatch.setattr(akshare_client, "_fetch_fund_flow_rows_em", source)

    served = get_market_fund_flow()
    assert served == old_rows[-60:]          # 同步路径回的是旧值，没等上游
    assert state["calls"] <= 1               # 只可能是后台线程在打，同步路径没打
    gate.set()                               # 放行后台刷新

    deadline = time.time() + 5
    while time.time() < deadline:
        with akshare_client._fund_flow_cache_lock:
            refreshed = akshare_client._fund_flow_cache["rows"]
        if refreshed and refreshed[-1]["date"] == "2026-10-19":
            break
        time.sleep(0.02)
    assert state["calls"] == 1               # 总共恰好一次：后台刷新，没有重复
    assert get_market_fund_flow()[-1]["date"] == "2026-10-19"


def test_too_stale_refetches_synchronously(monkeypatch):
    """旧值超过 FUND_FLOW_MAX_STALE：不再当旧值回，同步重取。"""
    calls = []

    def source():
        calls.append(1)
        return _em_shape(FRESH_DF)

    monkeypatch.setattr(akshare_client, "_fetch_fund_flow_rows_em", source)
    monkeypatch.setattr(akshare_client, "_fetch_fund_flow_rows_tencent", lambda max_days=50: [])
    _reset_fund_flow_cache(ok=True, rows=[{"date": "2026-10-10", "main_net_yi": 1.0}],
                           at=time.time() - FUND_FLOW_MAX_STALE - 1,
                           tried_at=time.time() - FUND_FLOW_MAX_STALE - 1)

    served = get_market_fund_flow()
    assert served[-1]["date"] == "2026-10-19"
    assert len(calls) == 1


def test_cold_start_single_flight(monkeypatch):
    """并发冷启动只有一个线程真打上游，其余排队后直接读缓存。"""
    state = {"calls": 0}
    gate = threading.Event()

    def slow_source():
        state["calls"] += 1
        gate.wait(5)          # 让第一个调用者停住，制造并发排队窗口
        return _em_shape(OLD_DF)

    monkeypatch.setattr(akshare_client, "_fetch_fund_flow_rows_em", slow_source)
    monkeypatch.setattr(akshare_client, "_fetch_fund_flow_rows_tencent", lambda max_days=50: [])
    _reset_fund_flow_cache()

    results = []
    errors = []

    def worker():
        try:
            results.append(get_market_fund_flow())
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    time.sleep(0.2)            # 五个线程都过了缓存检查、在 fetch 锁前排队
    gate.set()
    for t in threads:
        t.join(5)

    assert not errors
    assert state["calls"] == 1
    assert all(r == results[0] for r in results)


# ==================== 3. 腾讯源整形（hsfundtab → 统一行形状） ====================


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _tencent_payload(last_date, *, today_block=True):
    today = datetime.now().strftime("%Y-%m-%d")
    return {"code": 0, "data": {
        "historyFundFlow": {"oneDayKlineList": [
            {"date": "2026-10-16", "mainNetIn": "-1613672789", "price": "3280.15", "avgIn": "0"},
            {"date": last_date, "mainNetIn": "-4652872559", "price": "3251.20", "avgIn": "0"},
        ]},
        "todayFundFlow": {
            "stockCode": "sh000001", "mainNetIn": "-4652872559",
            "superFlow": "-3128701384", "bigFlow": "-1524171175",
            "normalFlow": "-1396297532", "smallFlow": "6049170091",
        } if today_block else None,
    }}


def test_tencent_source_shapes_rows(monkeypatch):
    """主力净流入亿元化、涨跌幅用前收现算、当日四档只在"最后一根=今天"时合并。"""
    today = datetime.now().strftime("%Y-%m-%d")
    payload = _tencent_payload(today)
    monkeypatch.setattr(akshare_client.requests, "get",
                        lambda url, params, headers, timeout: _FakeResponse(payload))

    rows = _fetch_fund_flow_rows_tencent()

    assert [r["date"] for r in rows] == ["2026-10-16", today]
    assert rows[0]["main_net_yi"] == -16.14          # -1613672789 元 → 亿元
    assert rows[1]["main_net_yi"] == -46.53
    assert rows[0]["sh_close"] == 3280.15
    assert rows[0]["sh_pct"] is None                  # 序列第一根没有前收
    assert rows[1]["sh_pct"] == round((3251.20 / 3280.15 - 1) * 100, 2)
    # 最后一根是今天 → 当日四档快照合并进来
    assert rows[1]["super_net_yi"] == -31.29
    assert rows[1]["small_net_yi"] == 60.49
    # 历史行四档保持 None（前端据此降级，不渲染分解视图）
    assert rows[0]["super_net_yi"] is None


def test_tencent_source_skips_today_tiers_on_non_today_tail(monkeypatch):
    """最后一根不是今天（休市日/盘前）：不把 todayFundFlow 硬贴到旧日期上。"""
    payload = _tencent_payload("2026-10-16")   # 尾根停在 10-16，today 块另有所属
    monkeypatch.setattr(akshare_client.requests, "get",
                        lambda url, params, headers, timeout: _FakeResponse(payload))

    rows = _fetch_fund_flow_rows_tencent()
    assert rows[-1]["super_net_yi"] is None
    assert rows[-1]["main_net_yi"] == -46.53


def test_tencent_source_failures_return_empty(monkeypatch):
    def boom(url, params, headers, timeout):
        raise ConnectionError("腾讯也挂了")

    monkeypatch.setattr(akshare_client.requests, "get", boom)
    assert _fetch_fund_flow_rows_tencent() == []


def test_yi_helper():
    assert _yi("-1613672789") == -16.14
    assert _yi(2e9) == 20.0
    assert _yi(None) is None
    assert _yi("nan") is None


# ==================== 4. 个股资金流（K 线页主力资金卡） ====================


def test_get_stock_fund_flow_resolves_codes_and_caches(monkeypatch):
    """600519 解析成 sh600519 打腾讯源；TTL 内重复请求命中缓存只打一次。"""
    calls = []

    def fake_fetch(code, max_days=50):
        calls.append(code)
        return [{"date": "2026-10-19", "close": 1500.0, "pct": 0.5,
                 "main_net_yi": 3.83, "super_net_yi": 2.12, "big_net_yi": 1.71,
                 "mid_net_yi": None, "small_net_yi": None}]

    monkeypatch.setattr(akshare_client, "_fetch_fund_flow_tencent", fake_fetch)
    monkeypatch.setattr(akshare_client, "_stock_fund_flow_cache", {})

    rows = akshare_client.get_stock_fund_flow("600519", limit=10)
    again = akshare_client.get_stock_fund_flow("600519", limit=10)
    assert calls == ["sh600519"]          # 第二次命中缓存，没再打源
    assert rows == again
    assert rows[-1]["main_net_yi"] == 3.83
    assert rows[-1]["super_net_yi"] == 2.12


def test_get_stock_fund_flow_negative_cache(monkeypatch):
    """空结果也写短负缓存：坏代码/上游挂时不至于每次打开 K 线页都真打一遍。"""
    calls = []

    def fake_fetch(code, max_days=50):
        calls.append(code)
        return []

    monkeypatch.setattr(akshare_client, "_fetch_fund_flow_tencent", fake_fetch)
    monkeypatch.setattr(akshare_client, "_stock_fund_flow_cache", {})

    assert akshare_client.get_stock_fund_flow("600519") == []
    assert akshare_client.get_stock_fund_flow("600519") == []
    assert len(calls) == 1


def test_get_stock_fund_flow_unresolvable_symbol(monkeypatch):
    """resolve_symbol 认不出的代码直接 []，不打网络（真跑到 search_stocks 的路径
    会触发全 A 名单网络加载，这里只测"解析失败就不取数"这个守卫分支）。"""
    monkeypatch.setattr(akshare_client, "resolve_symbol", lambda s: None)
    monkeypatch.setattr(akshare_client, "_fetch_fund_flow_tencent",
                        lambda code, max_days=50: (_ for _ in ()).throw(AssertionError("不该打源")))
    monkeypatch.setattr(akshare_client, "_stock_fund_flow_cache", {})
    assert akshare_client.get_stock_fund_flow("whatever") == []
