# -*- coding: utf-8 -*-
"""A 股市场规则的唯一出处（Python 侧）：官方费率按生效日期查表，板块规则按推导。

与 Java 侧 ``MarketRulesService`` 同一套公告史（两边种子数据必须保持一致，
MoneyPolicy.version 同时标记口径）。新公告 = 加一行种子，结算/回测代码零改动。
"""
from __future__ import annotations

from datetime import date

# (rule_key, effective_from, rate_pct, source_note)
STAMP_TAX_SELL = "STAMP_TAX_SELL"
TRANSFER_FEE = "TRANSFER_FEE"

SEED: list[tuple[str, date, float, str]] = [
    (STAMP_TAX_SELL, date(2008, 9, 19), 0.1, "印花税改单边卖出 0.1%（财政部/税务总局）"),
    (STAMP_TAX_SELL, date(2023, 8, 28), 0.05, "财政部 税务总局公告 2023 年第 39 号：减半征收"),
    (TRANSFER_FEE, date(2015, 1, 1), 0.002, "过户费双边 0.002%（中国结算旧标准）"),
    (TRANSFER_FEE, date(2022, 4, 29), 0.001, "中国结算 2022-04：过户费下调 50% 至 0.001%（双边）"),
]

_seed_index: dict[str, list[tuple[date, float]]] = {}
for _key, _eff, _rate, _ in SEED:
    _seed_index.setdefault(_key, []).append((_eff, _rate))
for _v in _seed_index.values():
    _v.sort()


def rate_pct(rule_key: str, on_date: date | str | None = None) -> float:
    """结算日适用的官方费率（百分数）。早于种子范围的取最早一条（近似，仅造数据会遇到）。"""
    entries = _seed_index.get(rule_key)
    if not entries:
        return 0.0
    if isinstance(on_date, str):
        try:
            on_date = date.fromisoformat(on_date[:10])
        except ValueError:
            on_date = None   # 合成/脏日期（如测试的 bar 序号）：按"今天"处理，别让费率查表炸结算
    d = on_date or date.today()
    applicable = 0.0
    for eff, rate in entries:
        if eff <= d:
            applicable = rate
        else:
            break
    return applicable


def limit_pct_for(symbol: str, stock_name: str | None = None) -> float:
    """涨跌幅限制（%）：主板 10 / 创业板·科创板 20 / 北交所 30；主板 ST 5。"""
    s = (symbol or "").upper()
    n = (stock_name or "").upper()
    if s.startswith(("688", "689", "300", "301", "302")):
        return 20.0
    if s.startswith(("43", "83", "87", "88", "92")):
        return 30.0
    return 5.0 if "ST" in n else 10.0


def lot_for(symbol: str) -> int:
    """整手（股）：科创板 200 起购，北交所与主板 100。"""
    s = (symbol or "").upper()
    return 200 if s.startswith(("688", "689")) else 100


TICK = 0.01  # 最小变动价位（元）


def tick_round(raw_fill: float, round_up: bool) -> float:
    """成交价取整到 0.01 网格：买入向上、卖出向下（保守侧）。"""
    import math
    units = round(raw_fill / TICK, 9)
    ticks = math.ceil(units - 1e-9) if round_up else math.floor(units + 1e-9)
    return round(ticks * TICK, 2)   # round 回 2 位：消掉 7.0200000000000005 这类浮点尾差
