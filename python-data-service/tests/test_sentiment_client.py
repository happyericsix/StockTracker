"""sentiment_client 的用例：聚合形状、对照验证的判定纪律、缓存与降级。

validate_against_price 是本模块的灵魂——它的三档结论
（样本不足 / 未见显著关系 / 初步显示有信息量）必须钉死：
门槛改数值可以，语义漂移就是拿用户的信任冒险。
"""
from datetime import datetime

import pytest

import sentiment_client as sc


def test_bare_symbol_strips_prefixes():
    assert sc._bare("SH600519") == "600519"
    assert sc._bare("sz000001") == "000001"
    assert sc._bare("600519") == "600519"


def test_series_sorts_and_drops_bad_rows():
    rows = [
        {"交易日期": "2026-09-30", "用户关注指数": 94.8},
        {"交易日期": "2026-09-28", "用户关注指数": "not-a-number"},
        {"交易日期": "2026-09-29", "用户关注指数": 90.0},
        {"交易日期": "", "用户关注指数": 88.0},
    ]
    series = sc._series(rows, "交易日期", "用户关注指数")
    assert [item["date"] for item in series] == ["2026-09-29", "2026-09-30"]


def _closes(dates_prices: dict) -> list[dict]:
    return [{"date": d, "value": p} for d, p in sorted(dates_prices.items())]


def test_validation_insufficient_samples_is_honest():
    series = [{"date": f"2026-09-{d:02d}", "value": 50.0} for d in range(1, 6)]
    closes = _closes({f"2026-09-{d:02d}": 100.0 + d for d in range(1, 7)})
    out = sc.validate_against_price(series, closes)
    assert out["verdict"] == "样本不足"
    assert "samples" in out


def test_validation_detects_informative_signal():
    # 高情绪日（70）次日必涨，低情绪日（30）次日必跌 → 差值 1.0，应报"有信息量"。
    # 价格用**累积链**构造：close(d+1) = close(d) + delta(d)，否则 dict 后写覆盖前写，
    # "次日"会被当天自己的基准价覆盖（这本身就是夹具版的前视偏差）。
    series, closes = [], {}
    level = 100.0
    for d in range(1, 25):
        date = f"2026-08-{d:02d}"
        series.append({"date": date, "value": 70.0 if d % 2 == 0 else 30.0})
        closes[date] = level
        level += 1 if d % 2 == 0 else -1
    closes["2026-08-25"] = level  # 实现最后一天的"次日"
    out = sc.validate_against_price(series, _closes(closes))
    assert "有信息量" in out["verdict"], out
    assert out["up_ratio_diff"] >= 0.10
    # 克制措辞必须在场：近似筛查结论不许被当成严格检验引用
    assert "非严格检验" in out["verdict"]
    assert "噪声门槛" in out["verdict"], "必须把门槛摆出来，让读者自己判断可信度"


def test_validation_reports_no_relation_when_flat():
    # 情绪与次日涨跌完全无关：涨跌由日期奇偶决定，情绪值走独立的 7 日循环
    series, closes = [], {}
    level = 100.0
    for d in range(1, 41):
        date = f"2026-07-{d:02d}"
        series.append({"date": date, "value": 50.0 + (d % 7)})
        closes[date] = level
        level += 1 if d % 2 == 0 else -1
    closes["2026-07-41"] = level
    out = sc.validate_against_price(series, _closes(closes))
    assert out["verdict"].startswith("未见显著关系"), out


def test_get_sentiment_aggregates_and_soft_fails(monkeypatch):
    sc._sentiment_cache.clear()

    class _Df:
        def __init__(self, rows):
            self._rows = rows
        def to_dict(self, *_a, **_k):
            return self._rows

    def fake_desire(symbol):
        return _Df([
            {"交易日期": "2026-09-29", "参与意愿": 50.06, "5日平均参与意愿": 49.38, "参与意愿变化": -4.18},
            {"交易日期": "2026-09-30", "参与意愿": 56.44, "5日平均参与意愿": 50.61, "参与意愿变化": 6.38},
        ])

    def fake_focus(symbol):
        return _Df([
            {"交易日": "2026-09-29", "用户关注指数": 93.1},
            {"交易日": "2026-09-30", "用户关注指数": 94.8},
        ])

    def boom(symbol):
        raise RuntimeError("评分接口挂了")

    monkeypatch.setattr(sc.akshare, "stock_comment_detail_scrd_desire_em", fake_desire)
    monkeypatch.setattr(sc.akshare, "stock_comment_detail_scrd_focus_em", fake_focus)
    monkeypatch.setattr(sc.akshare, "stock_comment_detail_zhpj_lspf_em", boom)
    monkeypatch.setattr(sc, "get_history", lambda symbol: [
        {"date": "2026-09-29", "close": 100.0},
        {"date": "2026-09-30", "close": 101.0},
    ])

    out = sc.get_sentiment("sh600519")

    assert out["desire"]["latest"] == 56.44
    assert out["desire"]["avg5"] == 50.61
    assert out["focus"]["latest"] == 94.8
    # 子项失败只缺对应键，不拖垮整包
    assert out["score"]["latest"] is None
    assert out["score"]["series"] == []
    # 免责声明必须在场：未验证信号不许裸奔
    assert "不构成任何买卖依据" in out["disclaimer"]
    # 样本不足的验证结论也如实给出
    assert out["validation"]["focus"]["verdict"] == "样本不足"
    assert "快照累积" in out["validation"]["desire"]["note"]

    # 缓存：第二次调用不再打上游（fake 计数器验证）
    calls = []
    monkeypatch.setattr(sc.akshare, "stock_comment_detail_scrd_focus_em",
                        lambda symbol: calls.append(1) or fake_focus(symbol))
    sc.get_sentiment("sh600519")
    assert not calls, "TTL 内的第二次调用必须走缓存"


def test_cache_expires(monkeypatch):
    sc._sentiment_cache.clear()
    # 直接塞一条已过期的缓存，验证会重新取数
    sc._sentiment_cache["600519"] = (datetime.now().timestamp() - sc.SENTIMENT_TTL - 1,
                                     {"stale": True})
    class _Df:
        def to_dict(self, *_a, **_k):
            return []
    monkeypatch.setattr(sc.akshare, "stock_comment_detail_scrd_desire_em", lambda symbol: _Df())
    monkeypatch.setattr(sc.akshare, "stock_comment_detail_scrd_focus_em", lambda symbol: _Df())
    monkeypatch.setattr(sc.akshare, "stock_comment_detail_zhpj_lspf_em", lambda symbol: _Df())
    monkeypatch.setattr(sc, "get_history", lambda symbol: [])
    out = sc.get_sentiment("600519")
    assert "stale" not in out


@pytest.mark.parametrize("bad", [None, "", "   "])
def test_bad_symbol_returns_none(bad):
    assert sc.get_sentiment(bad) is None
