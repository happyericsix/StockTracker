# -*- coding: utf-8 -*-
"""N4 事件聚类单测。

<h3>测试数据的边界都经过离线验证</h3>
聚类的判据是相似度阈值，而中文 2-gram 相似度**没有直觉可言**——
"看起来差不多的两条标题"可能只有 0.2。本文件的每一对标题的相似度
都先用 text_similarity 算过（见用例内注释），保证"应聚/不应聚"落在
规则的真实边界两侧，而不是碰巧。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import news_clustering  # noqa: E402


def item(title, published, symbol="", name="", level=2, source="媒体", url="",
         direction=None, id=None):
    return {"id": id, "symbol": symbol, "name": name, "title": title,
            "content": title, "url": url, "source_level": level,
            "source_name": source, "event_type_raw": "", "published_at": published,
            "direction": direction}


def hours_ago(h: int) -> str:
    return (datetime.now() - timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S")


# ==================== 同标的事件线（公告 + 媒体跟进） ====================


def test_same_symbol_announcement_and_media_cluster():
    """业绩说明会的公告+媒体稿：sim≈0.345 + 同标的锚点 0.15 = 0.495 ≥ 0.45。"""
    items = [
        item("贵州茅台关于召开2026年半年度业绩说明会的公告", hours_ago(10),
             symbol="SH600519", name="贵州茅台", level=1, source="东方财富·公告",
             url="https://e/1"),
        item("贵州茅台将于9月25日举行半年度业绩说明会", hours_ago(6),
             symbol="SH600519", name="贵州茅台", level=2, source="证券时报网",
             url="https://e/2"),
    ]
    result = news_clustering.cluster_events(items)
    assert len(result["clusters"]) == 1
    cluster = result["clusters"][0]
    assert cluster["member_count"] == 2
    # 代表条目 = 责任主体优先：公告(1) 的标题成为事件线标题
    assert cluster["title"].startswith("贵州茅台关于召开")
    assert cluster["sources"] == ["东方财富·公告", "证券时报网"]
    assert cluster["symbols"] == ["SH600519"]
    assert result["unclustered_count"] == 0


def test_different_events_same_company_stay_apart():
    """同一天既发中报又发回购公告：sim≈0.13 + 0.15 = 0.28 < 0.45，两条独立事件。"""
    items = [
        item("贵州茅台2026年半年度报告摘要", hours_ago(8),
             symbol="SH600519", name="贵州茅台", level=1, url="https://e/a"),
        item("贵州茅台关于回购公司股份的公告", hours_ago(5),
             symbol="SH600519", name="贵州茅台", level=1, url="https://e/b"),
    ]
    result = news_clustering.cluster_events(items)
    assert result["clusters"] == []
    assert result["unclustered_count"] == 2


def test_same_symbol_beyond_window_not_clustered():
    """同标的窗口 36h：两天前的公告与今天的新闻不聚。"""
    items = [
        item("贵州茅台关于召开2026年半年度业绩说明会的公告", hours_ago(50),
             symbol="SH600519", name="贵州茅台", level=1, url="https://e/1"),
        item("贵州茅台将于9月25日举行半年度业绩说明会", hours_ago(2),
             symbol="SH600519", name="贵州茅台", level=2, url="https://e/2"),
    ]
    assert news_clustering.cluster_events(items)["clusters"] == []


# ==================== 宏观/跨标的事件线 ====================


def test_macro_followups_cluster_without_symbol():
    """宏观通稿变体：sim≈0.62 ≥ 0.50（无标的锚点，门槛更高、窗口 72h）。"""
    items = [
        item("央行开展3000亿元MLF操作中标利率不变", hours_ago(20), source="东方财富·全球快讯"),
        item("央行开展3000亿元MLF操作 利率维持不变", hours_ago(15), source="新浪财经"),
        item("国常会部署一揽子增量政策", hours_ago(12), source="央视·新闻联播"),
    ]
    result = news_clustering.cluster_events(items)
    assert len(result["clusters"]) == 1
    cluster = result["clusters"][0]
    assert cluster["member_count"] == 2
    assert cluster["symbols"] == []
    assert result["unclustered_count"] == 1


def test_cross_window_72h_boundary():
    items = [
        item("央行开展3000亿元MLF操作中标利率不变", hours_ago(80)),
        item("央行开展3000亿元MLF操作 利率维持不变", hours_ago(2)),
    ]
    assert news_clustering.cluster_events(items)["clusters"] == []


# ==================== 输入健壮性 ====================


def test_undated_or_titleless_items_do_not_participate():
    """缺时间/缺标题的条目不参与配对，也不丢（留在 unclustered_count）。"""
    items = [
        item("央行开展3000亿元MLF操作中标利率不变", ""),
        item("央行开展3000亿元MLF操作 利率维持不变", hours_ago(1)),
        item("", hours_ago(1)),
    ]
    result = news_clustering.cluster_events(items)
    assert result["clusters"] == []
    assert result["unclustered_count"] == 3


def test_cluster_shape_and_member_order():
    """成员按时间倒序（前端口径）；directions 汇总成员已判方向。"""
    items = [
        item("贵州茅台关于召开2026年半年度业绩说明会的公告", hours_ago(10),
             symbol="SH600519", name="贵州茅台", level=1, url="https://e/1",
             direction=None),
        item("贵州茅台将于9月25日举行半年度业绩说明会", hours_ago(6),
             symbol="SH600519", name="贵州茅台", level=2, url="https://e/2",
             direction="利好"),
        item("贵州茅台半年度业绩说明会9月25日举行", hours_ago(3),
             symbol="SH600519", name="贵州茅台", level=2, url="https://e/3",
             direction="中性"),
    ]
    cluster = news_clustering.cluster_events(items)["clusters"][0]
    stamps = [m["published_at"] for m in cluster["members"]]
    assert stamps == sorted(stamps, reverse=True)
    assert cluster["directions"] == ["利好", "中性"]     # 按利好/利空/中性固定序
    assert cluster["member_count"] == 3
    assert cluster["first_at"] == stamps[-1] and cluster["last_at"] == stamps[0]
    # id 原样透传（Java 侧靠它映射回库里的 event）
    assert {m["id"] for m in cluster["members"]} == {None}


def test_input_hard_cap():
    """输入上限是最后防线（Java 侧先截断，这里防直连滥用）。"""
    items = [item(f"互不相关的第{i}号快讯内容各不相同", hours_ago(1)) for i in range(600)]
    result = news_clustering.cluster_events(items)
    assert result["unclustered_count"] <= news_clustering.MAX_INPUT_ITEMS


def test_pure_function_deterministic():
    items = [
        item("贵州茅台关于召开2026年半年度业绩说明会的公告", hours_ago(10),
             symbol="SH600519", name="贵州茅台", level=1, url="https://e/1"),
        item("贵州茅台将于9月25日举行半年度业绩说明会", hours_ago(6),
             symbol="SH600519", name="贵州茅台", level=2, url="https://e/2"),
        item("央行开展3000亿元MLF操作中标利率不变", hours_ago(5)),
        item("央行开展3000亿元MLF操作 利率维持不变", hours_ago(4)),
    ]
    first = news_clustering.cluster_events(items)
    second = news_clustering.cluster_events(items)
    assert first == second


def test_anchor_via_name_in_title_without_symbol():
    """锚点的另一条腿：一方的 name 出现在对方标题里（公告带 symbol，媒体报道不带）。

    sim≈0.345 + 0.15 = 0.495，而跨标的分支门槛是 0.50 —— **差一点不聚**。
    这是已知边界：跨标的锚点门槛比同标的严一档（无 symbol 印证，误聚风险更高）。
    把它钉在这里：改阈值的人必须意识到这条边界会翻转。
    """
    items = [
        item("贵州茅台关于召开2026年半年度业绩说明会的公告", hours_ago(10),
             symbol="SH600519", name="贵州茅台", level=1, url="https://e/1"),
        item("贵州茅台将于9月25日举行半年度业绩说明会", hours_ago(6),
             symbol="", name="", level=2, url="https://e/2"),
    ]
    result = news_clustering.cluster_events(items)
    assert result["clusters"] == []
