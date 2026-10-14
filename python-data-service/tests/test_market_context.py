# -*- coding: utf-8 -*-
"""市场语境三块（大盘/板块/估值）的口径用例。

网络函数不碰网：整形器用假数据钉住字段位与数值口径，取数器只测 fail-open 与
TTL 缓存行为。腾讯指数字段位（3/4/31/32/37）是实测钉死的——上游改字段序会
静默算错钱（成交额万元→亿），这页测试就是防它的。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from agent import market_context as mc  # noqa: E402


def fake_index_fields(name="上证指数", code="000001", level=3801.13,
                      pct=0.01, turnover_wan=39459208.0):
    fields = [""] * 45
    fields[1], fields[2] = name, code
    fields[3], fields[4] = str(level), str(level - 0.27)
    fields[31], fields[32] = "0.27", str(pct)
    fields[37] = str(turnover_wan)
    return fields


# ==================== ① 大盘 ====================

def test_index_fields_shape_and_units():
    shaped = mc._shape_index_fields(fake_index_fields())
    assert shaped["name"] == "上证指数"
    assert shaped["level"] == 3801.13
    assert shaped["pct"] == 0.01
    assert shaped["turnover_yi"] == 3945.9, "万元 → 亿（39459208 万 = 3945.9 亿）"


def test_index_fields_with_garbage_returns_none():
    assert mc._shape_index_fields(["x", "y"]) is None
    assert mc._shape_index_fields([]) is None


def test_breadth_shape_keeps_counts_and_as_of():
    df = pd.DataFrame({
        "item": ["上涨", "下跌", "涨停", "跌停", "平盘", "活跃度", "统计日期"],
        "value": [1752.0, 3471.0, 61.0, 5.0, 153.0, "30.43%", "2026-10-14 11:30:00"],
    })
    shaped = mc._shape_breadth(df)
    assert shaped["上涨"] == 1752 and shaped["跌停"] == 5
    assert shaped["as_of"].startswith("2026-10-14")
    assert "活跃度" not in shaped, "百分比活跃度不是家数，不进证据"


def test_breadth_with_empty_frame_returns_none():
    assert mc._shape_breadth(pd.DataFrame({"item": [], "value": []})) is None


def test_market_context_fails_open_when_all_sources_fail(monkeypatch):
    monkeypatch.setattr(mc, "_fetch_indices", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(mc, "_fetch_breadth", lambda: None)
    mc._clear_cache()
    assert mc.get_market_context() is None


def test_market_context_survives_one_source_failing(monkeypatch):
    """指数活着、涨跌家数死了 → 只带指数块（坏一块少一块，不是整块全弃）。"""
    monkeypatch.setattr(mc, "_fetch_indices",
                        lambda: [mc._shape_index_fields(fake_index_fields())])
    monkeypatch.setattr(mc, "_fetch_breadth", lambda: None)
    mc._clear_cache()
    out = mc.get_market_context()
    assert out is not None and "indices" in out and "breadth" not in out


# ==================== ② 板块 ====================

def fake_board_df():
    return pd.DataFrame({
        "板块名称": ["游戏", "白酒", "银行"],
        "涨跌幅": [3.84, -1.20, 0.35],
        "上涨家数": [21, 3, 18],
        "下跌家数": [2, 30, 4],
        "领涨股票": ["富春股份", "贵州茅台", "招商银行"],
    })


def test_sector_shape_ranks_and_counts():
    shaped = mc._shape_sector("白酒", fake_board_df())
    assert shaped["industry"] == "白酒"
    assert shaped["today_pct"] == -1.20
    assert shaped["rank"] == 3 and shaped["total_boards"] == 3
    assert shaped["up_count"] == 3 and shaped["down_count"] == 30


def test_sector_shape_unknown_industry_returns_none():
    assert mc._shape_sector("不存在的行业", fake_board_df()) is None


def test_sector_context_fails_open(monkeypatch):
    monkeypatch.setattr(mc, "_fetch_industry", lambda s: None)
    mc._clear_cache()
    assert mc.get_sector_context("600519") is None

    def boom(s):
        raise RuntimeError("push2 限流")
    monkeypatch.setattr(mc, "_fetch_industry", boom)
    assert mc.get_sector_context("600519") is None, "限流 → 板块块缺席，不炸"


# ==================== ③ 估值 ====================

def fake_valuation_df():
    """20 天序列：PE 从 10 涨到 20（当前是历史最贵），PB 从 1 到 2。"""
    rows = []
    from datetime import date, timedelta
    d = date(2026, 9, 1)
    for i in range(20):
        rows.append({
            "数据日期": d, "PE(TTM)": float(10 + i / 2), "市净率": float(1 + i / 20),
            "总市值": 1.5e12,
        })
        d += timedelta(days=1)
    return pd.DataFrame(rows)


def test_valuation_percentile_at_its_own_history():
    shaped = mc._shape_valuation(fake_valuation_df())
    assert shaped["pe_ttm"] == 19.5 and shaped["pb"] == 1.95
    assert shaped["pe_percentile_5y"] == 100.0, "当前是序列最大值 → 分位 100"
    assert shaped["pb_percentile_5y"] == 100.0
    assert shaped["market_cap_yi"] == 15000.0, "总市值 元 → 亿"


def test_valuation_percentile_mid_history():
    df = fake_valuation_df()
    df.loc[df.index[-1], "PE(TTM)"] = 12.0   # 序列里原有 10.0~12.0 共 5 个 + 替换后的末值
    shaped = mc._shape_valuation(df)
    assert shaped["pe_percentile_5y"] == 30.0, "20 个样本里 6 个 ≤12 → 30 分位"


def test_valuation_context_fails_open(monkeypatch):
    def boom(s):
        raise RuntimeError("接口挂了")
    monkeypatch.setattr(mc, "_fetch_valuation", boom)
    mc._clear_cache()
    assert mc.get_valuation_context("600519") is None


# ==================== TTL 缓存 ====================

def test_ttl_cache_hits_once_within_window(monkeypatch):
    mc._clear_cache()
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return {"v": 1}

    assert mc._cached("k", 60, fetch) == {"v": 1}
    assert mc._cached("k", 60, fetch) == {"v": 1}
    assert calls["n"] == 1, "TTL 窗口内第二次必须命中缓存（大盘快照全市场共用一份）"


def test_ttl_cache_failure_is_not_cached(monkeypatch):
    mc._clear_cache()
    state = {"fail": True}

    def fetch():
        if state["fail"]:
            raise RuntimeError("boom")
        return {"ok": 1}

    try:
        mc._cached("k2", 60, fetch)
    except RuntimeError:
        pass
    state["fail"] = False
    assert mc._cached("k2", 60, fetch) == {"ok": 1}, "失败不缓存：恢复后下一次取数应当成功"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print("PASS", fn.__name__)
        else:
            import inspect
            if "monkeypatch" in inspect.signature(fn).parameters:
                try:
                    from _pytest.monkeypatch import MonkeyPatch
                except ImportError:
                    print("SKIP (needs pytest)", fn.__name__)
                    continue
                mp = MonkeyPatch()
                try:
                    fn(mp)
                    print("PASS", fn.__name__)
                finally:
                    mp.undo()
