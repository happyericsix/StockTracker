"""news_credibility 的规则引擎用例。

核心断言不是"分数是多少"（权重会调），而是**排序关系与警示语义**：
公告 > 研报 > 聚合；传闻词必须触发 rumor_flag 且降分；交叉印证必须加分；
同类信源转载不算印证；同标的但不同日不算印证。规则引擎的价值在于
这些关系可复现——改权重可以，破坏关系就是回归。
"""
from datetime import datetime

import pytest

from news_credibility import assess_items, attach


def item(**kw):
    base = {
        "symbol": "SH600519", "source_level": 2, "source_name": "新浪财经",
        "title": "贵州茅台发布2026年半年度报告", "content": "营收增长1.47%",
        "published_at": "2026-09-18 08:30:00",
    }
    base.update(kw)
    return base


NOW = datetime(2026, 9, 18, 12, 0, 0)


def test_source_ordering_announcement_beats_aggregator():
    announcement = assess_items([item(source_level=1, source_name="上交所")], now=NOW)[0]
    aggregator = assess_items([item(source_level=4, source_name="某财经号")], now=NOW)[0]
    assert announcement["credibility"] > aggregator["credibility"]
    assert announcement["credibility_grade"] in ("高", "较高")


def test_trusted_source_named_boost():
    trusted = assess_items([item(source_name="财新")], now=NOW)[0]
    unknown = assess_items([item(source_name="某unknown媒体")], now=NOW)[0]
    assert trusted["credibility"] > unknown["credibility"]


def test_rumor_wording_flags_and_penalizes():
    clean = assess_items([item(title="甲公司中标5亿元项目")], now=NOW)[0]
    rumor = assess_items([item(title="据传甲公司或将获得大单")], now=NOW)[0]
    assert rumor["rumor_flag"] is True
    assert clean["rumor_flag"] is False
    assert rumor["credibility"] < clean["credibility"]
    assert any("传闻" in r for r in rumor["credibility_reasons"])


def test_announcement_legal_wording_is_not_rumor():
    # 公告里的"拟""或"是法律措辞：同一条措辞在公告渠道不应触发传闻警示
    announcement = assess_items(
        [item(source_level=1, source_name="上交所", title="甲公司拟发行可转债")], now=NOW)[0]
    assert announcement["rumor_flag"] is False


def test_sensational_headline_penalizes():
    calm = assess_items([item(title="甲公司上半年净利润同比下滑2%")], now=NOW)[0]
    hype = assess_items([item(title="惊天大利好！甲公司暴涨在即速看")], now=NOW)[0]
    assert hype["sensational_flag"] is True
    assert hype["credibility"] < calm["credibility"]


def test_verifiable_specifics_boost():
    vague = assess_items([item(title="甲公司业绩大幅增长", content="表现很好")], now=NOW)[0]
    specific = assess_items(
        [item(title="甲公司上半年营收911.7亿元", content="同比增长1.47%")], now=NOW)[0]
    assert specific["credibility"] > vague["credibility"]
    assert any("数字" in r for r in specific["credibility_reasons"])


def test_cross_corroboration_boosts_and_reports_source():
    pair = [
        item(source_name="财新", title="甲公司控股股东计划增持股份"),
        item(source_name="证券时报", title="甲公司控股股东拟增持股份", published_at="2026-09-18 09:00:00"),
    ]
    solo = assess_items([pair[0]], now=NOW)[0]
    with_peer = assess_items(pair, now=NOW)[0]
    assert with_peer["corroborated"] is True
    assert solo["corroborated"] is False
    assert with_peer["credibility"] > solo["credibility"]
    assert any("证券时报" in r for r in with_peer["credibility_reasons"])


def test_same_source_repost_is_not_corroboration():
    repost = [
        item(source_name="新浪财经", title="甲公司控股股东计划增持"),
        item(source_name="新浪财经", title="甲公司控股股东计划增持股份",
             published_at="2026-09-18 09:00:00"),
    ]
    assert assess_items(repost, now=NOW)[0]["corroborated"] is False


def test_different_day_is_not_corroboration():
    apart = [
        item(title="甲公司增持计划公布"),
        item(title="甲公司增持计划公布", published_at="2026-09-10 09:00:00"),
    ]
    assert assess_items(apart, now=NOW)[0]["corroborated"] is False


def test_different_symbol_is_not_corroboration():
    apart = [
        item(symbol="SH600519", title="甲公司增持计划公布"),
        item(symbol="SZ000001", title="甲公司增持计划公布"),
    ]
    assert assess_items(apart, now=NOW)[0]["corroborated"] is False


def test_output_shape_and_order_preserved():
    items = [item(title=f"标题{i}") for i in range(3)]
    results = assess_items(items, now=NOW)
    assert len(results) == 3
    for r in results:
        assert set(r) == {"credibility", "credibility_grade", "credibility_components",
                          "credibility_reasons", "rumor_flag", "sensational_flag",
                          "corroborated"}
        assert 0 <= r["credibility"] <= 100
        assert set(r["credibility_components"]) == {"source", "content", "corroboration"}


def test_attach_merges_without_reordering():
    items = [item(title="第一条"), item(title="第二条")]
    merged = attach(items, now=NOW)
    assert [m["title"] for m in merged] == ["第一条", "第二条"]
    assert all("credibility" in m and "symbol" in m for m in merged)


def test_peers_enable_single_item_corroboration():
    # analyze_one 的场景：条目本身 + 同伴列表分开传
    single = item(source_name="财新", title="甲公司控股股东计划增持股份")
    peers = [item(source_name="证券时报", title="甲公司控股股东拟增持股份",
                  published_at="2026-09-18 09:00:00")]
    without = assess_items([single], now=NOW)[0]
    with_peers = assess_items([single], now=NOW, peers=peers + [single])[0]
    assert with_peers["corroborated"] is True
    assert without["corroborated"] is False


@pytest.mark.parametrize("grade_floor", [("高", 80), ("较高", 65), ("中", 50), ("较低", 35)])
def test_grade_bands(grade_floor):
    grade, floor = grade_floor
    # 构造各档分数：直接用 assess 的内部带子校验映射一致性
    from news_credibility import _GRADE_BANDS
    bands = dict(_GRADE_BANDS)
    assert bands[floor] == grade
