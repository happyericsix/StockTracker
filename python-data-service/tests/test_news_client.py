# -*- coding: utf-8 -*-
"""N1 取数管道单测。

<h3>为什么 fixture 用"实测样本的形状 + 相对日期"</h3>
列名与取值都取自 2026-09-19 的真实网络实测（见 plans/2026-09-14-news-module.md §0.2
与本地 `_probe_news_sources.py` 的结果），因为**字段名写错是这一层唯一的大故障**：
东财改列名后 `row.get("新闻标题")` 会静默返回 None，整条流水线"成功但全空"。

日期则一律相对 `now()` 生成，不写死 `2026-09-19`：窗口过滤依赖当前时间，
写死日期的用例在几天后必然变红，而那种红是"测试过时"不是"代码错了"——
两者混在一起会让人开始忽略红灯。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import news_client  # noqa: E402
_REAL_MARKET_OPINION = news_client.fetch_market_opinion
# autouse 桩会把模块名换成空实现，直接调用真实函数的用例必须用这份"进口时快照"
# （与 _REAL_MARKET_OPINION 同一模式）
_REAL_SINA_FLASH = news_client.fetch_sina_flash
_REAL_THS_FLASH = news_client.fetch_ths_flash
_REAL_FUTU_FLASH = news_client.fetch_futu_flash
_REAL_CCTV_NEWS = news_client.fetch_cctv_news


# 大盘口径的 search_news 默认会带舆情源（股吧人气榜）；测试必须离线，
# 统一桩掉它的上游取数（个别用例需要真实形状时自己再覆盖这个桩）。
# 2026-10-09 多源化：四条新快讯源同理桩成空 —— 大盘口径的既有用例
# 仍然只关心"东财快讯源返回了什么"，不该因为多了几家源就真打网络。
@pytest.fixture(autouse=True)
def _no_market_opinion(monkeypatch):
    monkeypatch.setattr(news_client, "fetch_market_opinion", lambda limit=10: [])
    monkeypatch.setattr(news_client, "fetch_sina_flash", lambda: [])
    monkeypatch.setattr(news_client, "fetch_ths_flash", lambda: [])
    monkeypatch.setattr(news_client, "fetch_futu_flash", lambda: [])
    monkeypatch.setattr(news_client, "fetch_cctv_news", lambda days=2: [])

# ==================== 工具 ====================


def days_ago(n: int, with_time: bool = False) -> str:
    moment = datetime.now() - timedelta(days=n)
    return moment.strftime("%Y-%m-%d %H:%M:%S" if with_time else "%Y-%m-%d")


def df(rows):
    return pd.DataFrame(rows)


# ==================== 实测样本（列名与取值形状逐字取自 2026-09-19 实测）====================
# 日期用相对值（格式与实测完全一致），理由见模块 docstring 末尾。

NOTICE_DAY = days_ago(0)
NOTICE_ROW = {
    "代码": "600519", "名称": "贵州茅台",
    "公告标题": "贵州茅台:关于调整2026年限制性股票激励计划相关事项的公告",
    "公告类型": "股权激励进展公告",
    "公告日期": NOTICE_DAY,
    "网址": "https://data.eastmoney.com/notices/detail/600519/AN202609181829626803.html?spm=track",
}

NEWS_PUBLISHED = days_ago(1, with_time=True)
NEWS_ROW = {
    "关键词": "600519",
    "新闻标题": "贵州茅台600519.SH)：2026年中报净利润为445.17亿元、同比较去年同期下降1.95%",
    "新闻内容": "2026年8月15日，贵州茅台发布2026年中报。公司营业总收入为922.78亿元。",
    "发布时间": NEWS_PUBLISHED,
    "文章来源": "界面新闻",
    "新闻链接": "http://finance.eastmoney.com/a/202608153842377958.html",
}

REPORT_ROW = {
    "序号": 1, "股票代码": "600519", "股票简称": "贵州茅台",
    "报告名称": "2026年中报点评：茅台酒稳健，系列酒主动调整",
    "东财评级": "买入", "机构": "西南证券", "近一月个股研报数": 2,
    "行业": "白酒Ⅱ", "日期": days_ago(3),
    "报告PDF链接": "https://pdf.dfcfw.com/pdf/H3_AP202608211828244348_1.pdf",
}

MARKET_PUBLISHED = days_ago(0, with_time=True)
MARKET_ROW = {
    "标题": "机构：预估2026年全球数据中心电力需求容量将达161GW 年增约31%",
    "摘要": "【机构：预估2026年全球数据中心电力需求容量将达161GW】TrendForce集邦咨询估算……",
    "发布时间": MARKET_PUBLISHED,
    "链接": "https://finance.eastmoney.com/a/202609193879171230.html",
}

CAIXIN_ROW = {
    "tag": "周刊提前读",
    "summary": "【本文系数据通用户提前专享】战事给这一中东经济枢纽带来了什么？",
    "url": "https://database.caixin.com/2026-09-19/102486442.html?cxapp_link=true",
}

# ===== 2026-10-09 多源化新增：形状逐字取自当日的真实网络实测 =====

SINA_PUBLISHED = days_ago(0, with_time=True)
SINA_ROW = {
    # 新浪只有 [时间, 内容]：标题藏在内容的【】里，没有链接
    "时间": SINA_PUBLISHED,
    "内容": "【皇岗口岸周日上午举行开通仪式】香港特区政府保安局局长邓炳强今日（9日）表示，"
            "皇岗口岸定于10月12日正式开通运行。",
}

THS_PUBLISHED = days_ago(0, with_time=True)
THS_ROW = {
    "标题": "中广核贝谷两款自主CT安检设备获中国民航局使用许可",
    "内容": "近日，由中广核核技术发展股份有限公司受托管理的中广核贝谷科技有限公司自主研发的"
            "BGCT‑0824中型CT安检设备正式取得《中国民用航空局安全检查设备使用许可证》。",
    "发布时间": THS_PUBLISHED,
    "链接": "https://news.10jqka.com.cn/20261009/c680515671.shtml",
}

FUTU_PUBLISHED = days_ago(0, with_time=True)
FUTU_ROW = {
    "标题": "凯投宏观：主要政府债券收益率曲线趋陡空间有限",
    "内容": "凯投宏观亚洲市场主管托马斯·马修斯表示，尽管近期期限溢价明显上升。",
    "发布时间": FUTU_PUBLISHED,
    "链接": "https://news.futunn.com/flash/1000769402/capital-economics-limited-scope",
}

CCTV_ROW = {
    # date 是 YYYYMMDD 纯日期，没有时刻也没有链接
    "date": "20261008",
    "title": "【新思想引领新征程】以人的需求为原点 激活消费大市场",
    "content": "习近平总书记强调，要把扩大消费同改善人民生活品质结合起来。" + "长正文。" * 300,
}


def _patch(monkeypatch, name, value=None, error=None):
    """把 akshare 的某个接口换成桩：返回 DataFrame 或抛异常。"""
    def fake(**kwargs):
        if error is not None:
            raise error
        return value
    monkeypatch.setattr(news_client.akshare, name, fake)


# 财联社电报是**直连 HTTP 源**（不走 akshare 的 _patch 桩体系）：默认桩成空，
# 免得既有用例在测试环境里真发网络请求（2026-10-16 接入 CLS 后抓出的口径问题）。
# 专门测电报的用例用 _REAL_FETCH_CLS 换回真函数再自桩 requests。
_REAL_FETCH_CLS = news_client.fetch_cls_telegraph


_REAL_FETCH_LHB = news_client.fetch_lhb_events


@pytest.fixture(autouse=True)
def _stub_cls_by_default(monkeypatch):
    monkeypatch.setattr(news_client, "fetch_cls_telegraph", lambda *a, **k: [])
    # 龙虎榜（2026-10-16）同为直连源：默认桩空，专门测它的用例换回真函数
    monkeypatch.setattr(news_client, "fetch_lhb_events", lambda *a, **k: [])


def _patch_notices(monkeypatch, rows, error=None):
    """公告源桩：**两条内部分支一起打上**。

    N1 有两套公告取数路径：按日拉全市场（`fetch_announcements`）与按股票拉历史
    （`fetch_symbol_announcements`，为修"公告一条都没有"而加），`search_news` 按是否给了
    symbol 选一条。用例只关心"公告源返回了什么"，不该关心走的是哪条 —— 所以两条一起桩。

    按股票那条直接复用真实映射的产物（先桩住市场级的接口，再用它跑一遍 `fetch_announcements`），
    这样不必在测试里手写一份 N1 事件结构。
    """
    if error is not None:
        _patch(monkeypatch, "stock_notice_report", error=error)
        monkeypatch.setattr(news_client, "fetch_symbol_announcements",
                            lambda *a, **k: [])
        return
    _patch(monkeypatch, "stock_notice_report", df(rows))
    items = news_client.fetch_announcements()
    monkeypatch.setattr(news_client, "fetch_symbol_announcements", lambda *a, **k: items)


# ==================== 基础工具 ====================


def test_parse_datetime_handles_every_measured_shape():
    """实测出现过的四种时间形态都要能解析（公告是纯日期、新闻带秒）。"""
    assert news_client.parse_datetime("2026-09-19") == "2026-09-19 00:00:00"
    assert news_client.parse_datetime("2026-09-19 10:07:26") == "2026-09-19 10:07:26"
    assert news_client.parse_datetime("2026/09/19") == "2026-09-19 00:00:00"
    assert news_client.parse_datetime(datetime(2026, 9, 19, 10, 7, 26)) == "2026-09-19 10:07:26"
    # pandas 的缺失值是 float('nan')，str() 出来是 "nan" —— 不挡住就会变成
    # "时间字段有值但排不进任何窗口"的静默脏数据
    assert news_client.parse_datetime(float("nan")) == ""
    assert news_client.parse_datetime(None) == ""
    assert news_client.parse_datetime("") == ""
    assert news_client.parse_datetime("不是时间") == ""


def test_normalize_url_strips_tracking_params():
    """同一篇文章的不同入口参数必须归一到同一个键，否则会重复入库。"""
    plain = "https://database.caixin.com/2026-09-19/102486442.html"
    with_param = plain + "?cxapp_link=true"
    assert news_client.normalize_url(with_param) == news_client.normalize_url(plain)
    # 大小写与尾斜杠也要归一
    assert news_client.normalize_url("HTTPS://Data.EastMoney.COM/a/x/") == \
        news_client.normalize_url("https://data.eastmoney.com/a/x")
    # 空值不炸
    assert news_client.normalize_url("") == ""
    assert news_client.normalize_url(None) == ""


def test_normalize_url_keeps_different_articles_distinct():
    """归一化不能过头 —— 把不同文章合成一条比留几条重复更糟。"""
    a = news_client.normalize_url("https://finance.eastmoney.com/a/202608153842377958.html")
    b = news_client.normalize_url("https://finance.eastmoney.com/a/202608153842377959.html")
    assert a != b


def test_out_symbol_unifies_prefix_case():
    """统一成大写带前缀形态（与 Java 侧 bareCode 的前缀语义一致）。"""
    assert news_client._out_symbol("600519") == "SH600519"
    assert news_client._out_symbol("sh600519") == "SH600519"
    assert news_client._out_symbol("000001") == "SZ000001"
    assert news_client._out_symbol("") == ""


def test_bare_code_only_accepts_a_shares():
    """东财个股接口只认 6 位数字，港股/美股必须在这里被挡住而不是传下去。"""
    assert news_client._bare_code("600519") == "600519"
    assert news_client._bare_code("sh600519") == "600519"
    assert news_client._bare_code("00700") == ""     # 港股（会被识别成 hk00700）
    assert news_client._bare_code("AAPL") == ""      # 美股
    assert news_client._bare_code("") == ""


# ==================== 各源字段映射 ====================


def test_fetch_announcements_maps_measured_columns(monkeypatch):
    _patch_notices(monkeypatch, [NOTICE_ROW])
    items = news_client.fetch_announcements("20260919")
    assert len(items) == 1
    item = items[0]
    assert item["symbol"] == "SH600519"
    assert item["name"] == "贵州茅台"
    assert item["event_type_raw"] == "股权激励进展公告"      # 公告类型 → 源站分类
    assert item["source_level"] == news_client.LEVEL_NOTICE
    assert item["source_name"] == "东方财富·公告"
    # 公告没有正文，content 显式复用标题（而不是留空让 LLM 去猜）
    assert item["content"] == item["title"]
    assert item["published_at"] == f"{NOTICE_DAY} 00:00:00"


def test_fetch_announcements_filters_by_symbol(monkeypatch):
    """全市场 1000+ 条按单股查询时必须只留这一只，别把全市场推过网络。"""
    other = dict(NOTICE_ROW, **{"代码": "000859", "名称": "国风新材"})
    _patch_notices(monkeypatch, [NOTICE_ROW, other])
    assert len(news_client.fetch_announcements("20260919")) == 2
    only = news_client.fetch_announcements("20260919", symbol="600519")
    assert [i["symbol"] for i in only] == ["SH600519"]


def test_fetch_stock_news_maps_measured_columns(monkeypatch):
    _patch(monkeypatch, "stock_news_em", df([NEWS_ROW]))
    items = news_client.fetch_stock_news("600519")
    assert len(items) == 1
    item = items[0]
    assert item["source_level"] == news_client.LEVEL_MEDIA
    assert item["source_name"] == "界面新闻"
    assert item["published_at"] == NEWS_PUBLISHED
    assert item["symbol"] == "SH600519"


def test_fetch_stock_news_skips_non_a_share(monkeypatch):
    """非 A 股不发请求（发出去了东财也只会回错数据），且不抛异常。"""
    def boom(**kwargs):
        raise AssertionError("非 A 股不该调用东财个股新闻接口")
    monkeypatch.setattr(news_client.akshare, "stock_news_em", boom)
    assert news_client.fetch_stock_news("00700") == []
    assert news_client.fetch_stock_news("AAPL") == []


def test_fetch_market_news_maps_measured_columns(monkeypatch):
    """宏观走东财快讯（有标题+时间），不走财新。"""
    _patch(monkeypatch, "stock_info_global_em", df([MARKET_ROW]))
    items = news_client.fetch_market_news()
    assert len(items) == 1
    assert items[0]["title"].startswith("机构：预估2026年全球数据中心")
    assert items[0]["published_at"] == MARKET_PUBLISHED
    assert items[0]["symbol"] == ""      # 宏观没有标的
    assert items[0]["source_level"] == news_client.LEVEL_MEDIA


def test_fetch_caixin_news_has_no_title_or_time(monkeypatch):
    """财新的先天缺陷要**被钉住**，而不是靠记性。

    它只有 tag/summary/url。所以 fetch_caixin_news 明确返回 title="" 与
    published_at=""（不编造），并且它不该被接进时间轴 —— 这个用例就是这条约束的守卫。
    """
    _patch(monkeypatch, "stock_news_main_cx", df([CAIXIN_ROW]))
    items = news_client.fetch_caixin_news()
    assert len(items) == 1
    assert items[0]["title"] == ""
    assert items[0]["published_at"] == ""
    assert items[0]["event_type_raw"] == "周刊提前读"
    assert items[0]["content"]


# ==================== 多源快讯（2026-10-09 接入，列名逐字取自当日实测） ====================


def test_fetch_sina_flash_extracts_title_from_brackets(monkeypatch):
    """新浪只有 [时间, 内容]：标题必须从内容的【】里剥出来，不能拿整段正文当标题。"""
    _patch(monkeypatch, "stock_info_global_sina", df([SINA_ROW]))
    items = _REAL_SINA_FLASH()
    assert len(items) == 1
    item = items[0]
    assert item["title"] == "皇岗口岸周日上午举行开通仪式"
    assert item["content"].startswith("【皇岗口岸")     # 正文保持原样（含前缀）
    assert item["source_name"] == "新浪财经"
    assert item["source_level"] == news_client.LEVEL_MEDIA
    assert item["published_at"] == SINA_PUBLISHED
    assert item["url"] == ""                            # 源站不给链接，去重靠三元组


def test_fetch_sina_flash_title_fallback_without_brackets(monkeypatch):
    """无【】前缀的条目：裁正文开头当标题（有时间可排序，缺的只是短标题）。"""
    _patch(monkeypatch, "stock_info_global_sina",
           df([{"时间": SINA_PUBLISHED, "内容": "没有括号前缀的一条快讯正文"}]))
    item = _REAL_SINA_FLASH()[0]
    assert item["title"].startswith("没有括号前缀")


def test_fetch_ths_flash_maps_measured_columns(monkeypatch):
    _patch(monkeypatch, "stock_info_global_ths", df([THS_ROW]))
    item = _REAL_THS_FLASH()[0]
    assert item["title"] == THS_ROW["标题"]
    assert item["url"] == THS_ROW["链接"]
    assert item["source_name"] == "同花顺"
    assert item["published_at"] == THS_PUBLISHED


def test_fetch_futu_flash_maps_measured_columns(monkeypatch):
    _patch(monkeypatch, "stock_info_global_futu", df([FUTU_ROW]))
    item = _REAL_FUTU_FLASH()[0]
    assert item["title"] == FUTU_ROW["标题"]
    assert item["url"] == FUTU_ROW["链接"]
    assert item["source_name"] == "富途"
    assert item["published_at"] == FUTU_PUBLISHED


def test_fetch_futu_flash_title_fallback_when_blank(monkeypatch):
    """实测 50 条里约一半标题为空（正文完整）：必须裁正文当标题，否则整条被落库丢弃。"""
    _patch(monkeypatch, "stock_info_global_futu",
           df([{"标题": "", "内容": "摩根大通：将霍尼韦尔科技的目标价从255美元下调至240美元。",
                "发布时间": FUTU_PUBLISHED, "链接": "https://news.futunn.com/flash/1"}]))
    item = _REAL_FUTU_FLASH()[0]
    assert item["title"].startswith("摩根大通")
    assert item["content"]


def test_fetch_cctv_news_builds_date_only_published(monkeypatch):
    """新闻联播：date 纯日期（YYYYMMDD）→ published_at 补 00:00:00（与公告同"同日无序"语义）。"""
    _patch(monkeypatch, "news_cctv", df([CCTV_ROW]))
    item = _REAL_CCTV_NEWS(days=1)[0]
    assert item["source_name"] == "央视·新闻联播"
    assert item["event_type_raw"] == "新闻联播"
    assert item["published_at"].endswith(" 00:00:00")
    assert len(item["published_at"]) == 19
    assert item["url"] == ""
    # 全文数千字必须裁剪（_event 统一裁到 NEWS_ITEM_CHARS，超长补省略号 +1），否则批量解读输入失控
    assert len(item["content"]) <= news_client.NEWS_ITEM_CHARS + 1
    assert item["content"].endswith("…")


def test_fetch_cctv_news_caps_days(monkeypatch):
    """窗口传得再大最多拉 CCTV_MAX_DAYS 天（每天一次 HTTP），老政策稿没有增量。"""
    calls = []

    def fake_news_cctv(date=None):
        calls.append(date)
        return df([dict(CCTV_ROW, date=date)])

    monkeypatch.setattr(news_client.akshare, "news_cctv", fake_news_cctv)
    items = _REAL_CCTV_NEWS(days=30)
    assert len(calls) == news_client.CCTV_MAX_DAYS
    assert len(items) == news_client.CCTV_MAX_DAYS


def test_fetch_cctv_news_single_day_failure_degrades(monkeypatch):
    """某一天接口挂了只丢那一天，其它天的稿照常返回。"""
    def flaky(date=None):
        if date == datetime.now().strftime("%Y%m%d"):
            raise RuntimeError("今天还没更新")
        return df([CCTV_ROW])

    monkeypatch.setattr(news_client.akshare, "news_cctv", flaky)
    items = _REAL_CCTV_NEWS(days=2)
    assert len(items) == 1


@pytest.mark.parametrize("call,akshare_name", [
    (lambda: _REAL_SINA_FLASH(), "stock_info_global_sina"),
    (lambda: _REAL_THS_FLASH(), "stock_info_global_ths"),
    (lambda: _REAL_FUTU_FLASH(), "stock_info_global_futu"),
    (lambda: _REAL_CCTV_NEWS(days=1), "news_cctv"),
])
def test_new_sources_fail_soft(monkeypatch, call, akshare_name):
    """多源化的核心契约：新源挂掉返回空，绝不拖垮其它源（与既有源同一约定）。

    走 `_REAL_*` 快照 —— autouse 桩盖住了模块名，直接调 `news_client.fetch_*`
    测到的是桩而不是真实实现（那样的"绿"是假的）。
    """
    _patch(monkeypatch, akshare_name, error=RuntimeError("502"))
    assert call() == []


def test_fetch_research_reports_window_and_content(monkeypatch):
    """研报 771 条里只留窗口内的；机构+评级+行业要拼进 content（研报无正文）。"""
    fresh = dict(REPORT_ROW, **{"日期": days_ago(3)})
    stale = dict(REPORT_ROW, **{"日期": days_ago(300), "报告名称": "去年的老报告"})
    _patch(monkeypatch, "stock_research_report_em", df([fresh, stale]))
    items = news_client.fetch_research_reports("600519", days=30)
    assert len(items) == 1
    item = items[0]
    assert item["source_level"] == news_client.LEVEL_REPORT
    assert item["source_name"] == "西南证券"
    assert item["event_type_raw"] == "买入"          # 源站分类 = 评级
    assert "西南证券" in item["content"] and "买入" in item["content"] and "白酒Ⅱ" in item["content"]


def test_fetch_research_reports_sorted_newest_first(monkeypatch):
    older = dict(REPORT_ROW, **{"日期": days_ago(10), "报告名称": "十天前"})
    newer = dict(REPORT_ROW, **{"日期": days_ago(1), "报告名称": "昨天"})
    _patch(monkeypatch, "stock_research_report_em", df([older, newer]))
    items = news_client.fetch_research_reports("600519", days=30)
    assert [i["title"] for i in items] == ["昨天", "十天前"]


# ==================== 失败语义：单源挂掉不拖垮整体 ====================

@pytest.mark.parametrize("call,akshare_name", [
    (lambda: news_client.fetch_announcements("20260919"), "stock_notice_report"),
    (lambda: news_client.fetch_stock_news("600519"), "stock_news_em"),
    (lambda: news_client.fetch_market_news(), "stock_info_global_em"),
    (lambda: news_client.fetch_caixin_news(), "stock_news_main_cx"),
    (lambda: news_client.fetch_research_reports("600519"), "stock_research_report_em"),
])
def test_every_source_fails_soft(monkeypatch, call, akshare_name):
    """每个源都要"返回空 + 记 warning"，绝不抛异常（spec §13）。"""
    _patch(monkeypatch, akshare_name, error=RuntimeError("东财 502"))
    assert call() == []


def test_search_news_one_broken_source_still_returns_others(monkeypatch):
    """核心契约：公告源挂了，新闻照常返回（四个源是四个独立外部服务）。"""
    _patch_notices(monkeypatch, [], error=RuntimeError("公告接口超时"))
    _patch(monkeypatch, "stock_news_em", df([NEWS_ROW]))
    _patch(monkeypatch, "stock_research_report_em", df([dict(REPORT_ROW, **{"日期": days_ago(1)})]))
    result = news_client.search_news(symbol="600519", days=30)
    titles = [i["title"] for i in result["items"]]
    assert any("贵州茅台600519.SH" in t for t in titles)
    assert any("中报点评" in t for t in titles)
    assert result["counts"]["notice"] == 0
    assert result["counts"]["news"] == 1
    assert result["counts"]["report"] == 1


# ==================== 去重 ====================

def test_dedup_by_normalized_url():
    a = {"url": "https://x.com/a/1?from=app", "title": "t", "symbol": "", "published_at": ""}
    b = {"url": "https://x.com/a/1/", "title": "t", "symbol": "", "published_at": ""}
    assert len(news_client.dedup([a, b])) == 1


def test_dedup_by_triple_when_url_missing():
    """公告无 URL 时按 (symbol, title, published_at) 兜底（spec §7）。"""
    a = {"url": "", "title": "同一份公告", "symbol": "SH600519", "published_at": "2026-09-19 00:00:00"}
    b = dict(a)
    c = dict(a, **{"title": "另一份公告"})
    assert len(news_client.dedup([a, b, c])) == 2
    # 没有 url 也不同的三元组要各自保留
    assert len(news_client.dedup([a, c])) == 2


def test_search_news_counts_match_deduped_list(monkeypatch):
    """展示用计数必须等于去重后的真实条数，否则用户会觉得"数据丢了"。"""
    dup = dict(NEWS_ROW)
    _patch(monkeypatch, "stock_news_em", df([NEWS_ROW, dup]))
    _patch_notices(monkeypatch, [NOTICE_ROW])
    _patch(monkeypatch, "stock_research_report_em", df([]))
    result = news_client.search_news(symbol="600519", days=30)
    assert len(result["items"]) == 2
    assert result["counts"]["news"] == 1


# ==================== 排序 / 窗口 / 关键词 ====================

def test_sort_puts_undated_items_last(monkeypatch):
    """无时间的条目（如财新）必须排最后 —— 不能霸占首屏。"""
    _patch(monkeypatch, "stock_news_em", df([
        dict(NEWS_ROW, **{"发布时间": days_ago(1, with_time=True)}),
        dict(NEWS_ROW, **{"新闻链接": "https://x.com/other", "发布时间": "nan"}),
    ]))
    _patch_notices(monkeypatch, [])
    _patch(monkeypatch, "stock_research_report_em", df([]))
    result = news_client.search_news(symbol="600519", days=30)
    assert result["items"][-1]["published_at"] == ""


def test_search_news_window_drops_old_items(monkeypatch):
    _patch(monkeypatch, "stock_news_em", df([
        dict(NEWS_ROW, **{"发布时间": days_ago(1, with_time=True), "新闻链接": "https://x.com/new"}),
        dict(NEWS_ROW, **{"发布时间": days_ago(200, with_time=True), "新闻链接": "https://x.com/old"}),
    ]))
    _patch_notices(monkeypatch, [])
    _patch(monkeypatch, "stock_research_report_em", df([]))
    items = news_client.search_news(symbol="600519", days=7)["items"]
    assert [i["url"] for i in items] == ["https://x.com/new"]


def test_search_news_keyword_filter(monkeypatch):
    _patch(monkeypatch, "stock_news_em", df([
        dict(NEWS_ROW, **{"新闻链接": "https://x.com/a", "新闻标题": "贵州茅台分红方案"}),
        dict(NEWS_ROW, **{"新闻链接": "https://x.com/b", "新闻标题": "某公司人事变动"}),
    ]))
    _patch_notices(monkeypatch, [])
    _patch(monkeypatch, "stock_research_report_em", df([]))
    items = news_client.search_news(symbol="600519", keyword="分红", days=30)["items"]
    assert [i["url"] for i in items] == ["https://x.com/a"]


def test_search_news_macro_only_when_no_symbol(monkeypatch):
    """指定标的时不能把 200 条全球快讯混进来（spec §1.2 反对资讯噪音）。"""
    _patch(monkeypatch, "stock_news_em", df([NEWS_ROW]))
    _patch(monkeypatch, "stock_info_global_em", df([MARKET_ROW]))
    _patch_notices(monkeypatch, [])
    _patch(monkeypatch, "stock_research_report_em", df([]))

    with_symbol = news_client.search_news(symbol="600519", days=30)
    assert with_symbol["counts"]["market"] == 0
    assert all(i["symbol"] for i in with_symbol["items"])

    macro = news_client.search_news(days=30)
    assert macro["counts"]["market"] == 1
    assert any("数据中心" in i["title"] for i in macro["items"])


def test_search_news_symbol_scope_skips_flash_sources(monkeypatch):
    """个股口径不该碰任何大盘快讯源 —— 多余的 HTTP 只是个股页的纯等待。"""
    def boom(*args, **kwargs):
        raise AssertionError("个股口径不该调用大盘快讯源")

    monkeypatch.setattr(news_client, "fetch_market_news", boom)
    monkeypatch.setattr(news_client, "fetch_sina_flash", boom)
    monkeypatch.setattr(news_client, "fetch_ths_flash", boom)
    monkeypatch.setattr(news_client, "fetch_futu_flash", boom)
    monkeypatch.setattr(news_client, "fetch_cctv_news", boom)
    _patch_notices(monkeypatch, [])
    _patch(monkeypatch, "stock_news_em", df([]))
    _patch(monkeypatch, "stock_research_report_em", df([]))
    assert news_client.search_news(symbol="600519", days=30)["items"] == []


def test_search_news_market_scope_aggregates_new_sources(monkeypatch):
    """大盘口径聚合四家媒体源（东财/新浪/同花顺/新闻联播），counts 同口径。
    2026-10-12 富途移出循环（JS 空壳页抓不到原文）：移除源必须同步改这里，
    防止"悄悄加回来/悄悄少一家"无人知晓。"""
    from news_client import LEVEL_FORUM, LEVEL_MEDIA

    def item(title, source, published, url="", symbol="", level=LEVEL_MEDIA, **extra):
        return {"symbol": symbol, "name": "", "title": title, "content": title,
                "url": url, "source_level": level, "source_name": source,
                "event_type_raw": "", "published_at": published, **extra}

    monkeypatch.setattr(news_client, "fetch_market_news", lambda: [
        item("东财独家的快讯", "东方财富·全球快讯", days_ago(0, with_time=True),
             url="https://eastmoney.com/a/1")])
    monkeypatch.setattr(news_client, "fetch_sina_flash", lambda: [
        item("新浪独家的快讯", "新浪财经", days_ago(0, with_time=True))])
    monkeypatch.setattr(news_client, "fetch_ths_flash", lambda: [
        item("同花顺独家的快讯", "同花顺", days_ago(0, with_time=True),
             url="https://10jqka.com/a/1")])
    monkeypatch.setattr(news_client, "fetch_cctv_news", lambda days=2: [
        item("新闻联播的政策稿", "央视·新闻联播", days_ago(1) + " 00:00:00")])
    _patch_notices(monkeypatch, [])
    result = news_client.search_news(days=3)
    titles = [i["title"] for i in result["items"]]
    assert len(titles) == 4
    assert result["counts"]["market"] == 4
    assert result["counts"].get("forum", 0) == 0
    assert LEVEL_FORUM not in [i["source_level"] for i in result["items"]]


# ==================== 跨源同事件归并（2026-10-09 多源化） ====================


def _flash(title, source, published, url="", symbol=""):
    return {"symbol": symbol, "name": "", "title": title, "content": title,
            "url": url, "source_level": news_client.LEVEL_MEDIA, "source_name": source,
            "event_type_raw": "", "published_at": published}


SAME_EVENT_TITLE = "央行开展3000亿元MLF操作中标利率维持不变"
OTHER_EVENT_TITLE = "某公司获得国家科技进步一等奖"


def test_merge_cross_source_collapses_same_event():
    """同一通稿被三家报道 → 保留一条（有 URL 的最早那条），其余名字进 also_reported_by。"""
    def hours_ago(h):
        return (datetime.now() - timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S")

    items = [
        _flash(SAME_EVENT_TITLE, "新浪财经", hours_ago(3)),
        _flash(SAME_EVENT_TITLE, "东方财富·全球快讯", hours_ago(2),
               url="https://eastmoney.com/a/mlf"),
        _flash(SAME_EVENT_TITLE, "同花顺", hours_ago(1),
               url="https://10jqka.com/a/mlf"),
        _flash(OTHER_EVENT_TITLE, "富途", hours_ago(0),
               url="https://futunn.com/a/other"),
    ]
    merged = news_client.merge_cross_source(items)
    assert len(merged) == 2
    kept = next(i for i in merged if i["title"] == SAME_EVENT_TITLE)
    assert kept["url"] == "https://eastmoney.com/a/mlf"      # 有 URL + 时间最早
    assert set(kept["also_reported_by"]) == {"新浪财经", "同花顺"}
    other = next(i for i in merged if i["title"] == OTHER_EVENT_TITLE)
    assert "also_reported_by" not in other                   # 独家报道不带这个键


def test_merge_cross_source_keeps_different_events():
    """标题不相似的条目绝不能被归并 —— 归并判错等于把独立新闻从时间轴上抹掉。"""
    items = [
        _flash("央行开展3000亿元MLF操作", "新浪财经", days_ago(0, with_time=True)),
        _flash("证监会发布上市公司分红新规", "同花顺", days_ago(0, with_time=True)),
    ]
    assert len(news_client.merge_cross_source(items)) == 2


def test_merge_cross_source_time_window():
    """48 小时外的"标题相似"是后续进展，该各自成条，不归并。"""
    now = days_ago(0, with_time=True)
    three_days_ago = days_ago(3, with_time=True)
    items = [
        _flash(SAME_EVENT_TITLE, "新浪财经", three_days_ago),
        _flash(SAME_EVENT_TITLE, "同花顺", now),
    ]
    assert len(news_client.merge_cross_source(items)) == 2


def test_merge_cross_source_ignores_undated_and_stock_items():
    """无时间的条目无从验证"相近时间"，不归并（保守）；个股条目不参与归并。"""
    now = days_ago(0, with_time=True)
    items = [
        _flash(SAME_EVENT_TITLE, "新浪财经", ""),               # 无时间
        _flash(SAME_EVENT_TITLE, "同花顺", now),
        _flash(SAME_EVENT_TITLE + "茅台", "富途", now, symbol="SH600519"),  # 个股
        dict(_flash(SAME_EVENT_TITLE, "东财", now), symbol="SH600519"),
    ]
    merged = news_client.merge_cross_source(items)
    assert len(merged) == 4


def test_merge_cross_source_preserves_order():
    """输出保持输入顺序（调用方已按时间排好），只删不重排。

    两条"独立事件"的标题必须真的不相似：中文 2-gram 下"甲公司中标海外大单"
    与"乙公司发布年度业绩预告"只有零星公共 gram，不会被误并。
    """
    now = days_ago(0, with_time=True)
    items = [
        _flash("甲公司中标海外光伏大单", "富途", now, url="https://f/a"),
        _flash(SAME_EVENT_TITLE, "新浪财经", now),
        _flash("乙公司发布年度业绩预告", "同花顺", now, url="https://t/b"),
        _flash(SAME_EVENT_TITLE, "同花顺", now, url="https://t/a"),
    ]
    merged = news_client.merge_cross_source(items)
    assert [i["title"] for i in merged] == [
        "甲公司中标海外光伏大单", "乙公司发布年度业绩预告", SAME_EVENT_TITLE]


def test_search_news_merges_cross_source_duplicates(monkeypatch):
    """端到端：三家报道同一事件 → 雷达只显示一条，counts 与列表一致（不吞也不虚增）。"""
    now = days_ago(0, with_time=True)
    monkeypatch.setattr(news_client, "fetch_market_news", lambda: [
        _flash(SAME_EVENT_TITLE, "东方财富·全球快讯", now, url="https://e/1")])
    monkeypatch.setattr(news_client, "fetch_sina_flash", lambda: [
        _flash(SAME_EVENT_TITLE, "新浪财经", now)])
    monkeypatch.setattr(news_client, "fetch_ths_flash", lambda: [
        _flash(SAME_EVENT_TITLE, "同花顺", now, url="https://t/1")])
    monkeypatch.setattr(news_client, "fetch_futu_flash", lambda: [])
    monkeypatch.setattr(news_client, "fetch_cctv_news", lambda days=2: [])
    _patch_notices(monkeypatch, [])

    result = news_client.search_news(days=3)
    assert len(result["items"]) == 1
    assert set(result["items"][0]["also_reported_by"]) == {"新浪财经", "同花顺"}
    assert result["counts"]["market"] == 1
    assert sum(result["counts"].values()) == len(result["items"])


def test_search_news_reports_missing_forum_source(monkeypatch):
    """舆情请求要说"暂无数据源"，不能返回空列表让调用方以为社区没人讨论。"""
    _patch_notices(monkeypatch, [])
    _patch(monkeypatch, "stock_news_em", df([]))
    _patch(monkeypatch, "stock_research_report_em", df([]))
    result = news_client.search_news(symbol="600519", types=[4], days=7)
    assert result["items"] == []
    assert any("舆情" in e for e in result["errors"])


def test_search_news_non_a_share_reports_no_source(monkeypatch):
    """港股/美股问个股新闻时必须说清"没有这个源"，而不是显示"暂无新闻"。"""
    _patch_notices(monkeypatch, [])
    _patch(monkeypatch, "stock_research_report_em", df([]))
    result = news_client.search_news(symbol="00700", days=7)
    assert any("仅支持沪深 A 股" in e for e in result["errors"])


# ==================== 既有 get_news 的前缀回归（写 N1 时发现的旧 bug）====================

@pytest.mark.parametrize("symbol", ["600519", "sh600519", "SH600519"])
def test_get_news_accepts_prefixed_symbol(monkeypatch, symbol):
    """`sh600519` 是 README「股票代码格式」表里的标准写法，但过去拿不到个股新闻。

    根因与本模块 `_bare_code` 踩的是同一个：`resolve_symbol("sh600519")` 返回带前缀的
    `"SH600519"`，而 `is_a_share` 只认 6 位纯数字 → 判定"非 A 股" → **静默回退全市场快讯**。
    症状是最难查的那种：用户问"茅台最近有什么消息"，拿到宏观快讯，全线无报错。
    既有用例只喂过裸代码（`test_external_tools.py` 全是 "600519"），所以一直没被发现。
    """
    import akshare_client

    monkeypatch.setattr(akshare_client.akshare, "stock_news_em", lambda **kwargs: df([NEWS_ROW]))
    monkeypatch.setattr(akshare_client.akshare, "stock_info_global_em",
                        lambda **kwargs: pytest.fail("带前缀的 A 股代码不该回退到市场快讯"))

    result = akshare_client.get_news(symbol, limit=5)
    assert result["scope"] == "symbol"
    assert result["symbol"] == "600519"


# ==================== 公告正文（按需补抓）====================
#
# 背景：东财公告接口只给标题，没有正文，于是 N2 对公告只能回"信息不足，不判断方向"。
# 正文走实测可用的 JSON 接口（np-cnotice-stock/api/content/ann）。
# fixture 用真实响应形状（data.notice_content 是带标签的 HTML）。

NOTICE_URL = "https://data.eastmoney.com/notices/detail/600519/AN202609181829626803.html"
BODY_HTML = ("<p>贵州茅台酒股份有限公司关于回购公司股份的公告</p>"
             "<p>本公司拟使用自有资金回购股份，回购金额不低于人民币10亿元，"
             "回购价格不超过2000元/股。</p><script>var x=1;</script>")


def _patch_body(monkeypatch, payloads):
    """把 requests.get 换成按 page_index 依次返回 payloads 的桩。"""
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            if isinstance(self._payload, Exception):
                raise self._payload

        def json(self):
            if isinstance(self._payload, Exception):
                raise self._payload
            return self._payload

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": dict(params or {}), "headers": headers})
        page = (params or {}).get("page_index", 1)
        index = min(page - 1, len(payloads) - 1)
        return FakeResponse(payloads[index])

    monkeypatch.setattr(news_client.requests, "get", fake_get)
    return calls


def body_payload(content):
    return {"data": {"art_code": "AN1", "notice_content": content}, "success": True}


def test_extract_art_code_from_real_url():
    assert news_client.extract_art_code(NOTICE_URL) == "AN202609181829626803"
    assert news_client.extract_art_code("https://x.com/nothing") == ""
    assert news_client.extract_art_code("") == ""


def test_fetch_announcement_body_strips_html(monkeypatch):
    calls = _patch_body(monkeypatch, [body_payload(BODY_HTML)])
    text = news_client.fetch_announcement_body("AN202609181829626803")
    assert "回购金额不低于人民币10亿元" in text
    assert "<p>" not in text and "<script>" not in text
    assert "var x=1" not in text                    # 脚本块要去掉
    assert calls[0]["params"]["art_code"] == "AN202609181829626803"
    # 实测带 Referer 才稳定返回 JSON，这个头不能丢
    assert "Referer" in calls[0]["headers"]


def test_fetch_announcement_body_stops_on_short_page(monkeypatch):
    """短页 = 末页：不许再发一次注定拿不到东西的请求。

    短公告（董事会决议之类）是**多数**。不做这个判断的话，个股页补 10 条正文要发
    20 次请求，其中 10 次纯浪费。
    """
    calls = _patch_body(monkeypatch, [body_payload("短" * 100)])
    text = news_client.fetch_announcement_body("AN1", max_pages=3)
    assert len(calls) == 1
    assert "短" in text


def test_fetch_announcement_body_pages_when_budget_exceeds_one_page(monkeypatch):
    """翻页分支：只有"正文预算 > 单页容量"时才需要第二页。

    当前常量下（预算 2000 < 整页 3000）单页永远够用，翻页实际上走不到 ——
    这对效率是好事，但意味着这条分支没有真实覆盖。所以这里**显式调参**把它逼出来，
    否则它就是一段没人验证过的代码。
    """
    monkeypatch.setattr(news_client, "ANNOUNCEMENT_BODY_CHARS", 6000)
    monkeypatch.setattr(news_client, "_PAGE_FULL_CHARS", 3000)
    calls = _patch_body(monkeypatch, [
        body_payload("甲" * 3000), body_payload("乙" * 3000), body_payload("丙" * 3000),
    ])
    text = news_client.fetch_announcement_body("AN1", max_pages=3)
    assert len(calls) == 2          # 两页就够 6000，不白翻第三页
    assert "乙" in text
    assert "丙" not in text


def test_fetch_announcement_body_clips_long_text(monkeypatch):
    _patch_body(monkeypatch, [body_payload("乙" * 9000)])
    text = news_client.fetch_announcement_body("AN1")
    assert text.endswith("…")
    assert len(text) == news_client.ANNOUNCEMENT_BODY_CHARS + 1


def test_fetch_announcement_body_fails_soft(monkeypatch):
    """正文抓不到必须返回空串（调用方保持原样），不能抛异常。"""
    _patch_body(monkeypatch, [RuntimeError("502 bad gateway")])
    assert news_client.fetch_announcement_body("AN1") == ""
    assert news_client.fetch_announcement_body("") == ""


def test_enrich_only_touches_notices_and_respects_limit(monkeypatch):
    _patch_body(monkeypatch, [body_payload(BODY_HTML)])
    notices = [
        {"source_level": 1, "url": NOTICE_URL, "title": "公告一", "content": "公告一"},
        {"source_level": 1, "url": NOTICE_URL, "title": "公告二", "content": "公告二"},
        {"source_level": 2, "url": "https://news.example/a", "title": "媒体", "content": "正文"},
    ]
    result = news_client.enrich_announcement_bodies(notices, limit=1)
    assert result["fetched"] == 1                       # limit=1，只补第一条
    assert result["items"][0]["body_fetched"] is True
    assert result["items"][0]["content"] != "公告一"
    assert result["items"][1]["content"] == "公告二"      # 超出 limit，保持原样
    assert result["items"][2]["content"] == "正文"        # 媒体不动


def test_enrich_skips_already_fetched_bodies(monkeypatch):
    """已经补过正文的条目不能重复抓（否则每次刷新个股页都要再发一轮请求）。"""
    def boom(**kwargs):
        raise AssertionError("已有正文的条目不该再发请求")
    monkeypatch.setattr(news_client.requests, "get", boom)
    items = [{"source_level": 1, "url": NOTICE_URL,
              "title": "公告一", "content": "这是已经抓到的正文，和标题不同"}]
    result = news_client.enrich_announcement_bodies(items)
    assert result["fetched"] == 0
    assert result["items"][0]["content"].startswith("这是已经抓到的正文")


def test_enrich_dedupes_by_art_code_within_one_call(monkeypatch):
    """同一条公告在同一次调用里出现两次，只该抓一次正文。

    `content != title` 那条守卫只对**已经补过**的条目生效；两条都还没补正文时，
    不去重就会白付一次 HTTP 请求。
    """
    calls = _patch_body(monkeypatch, [body_payload(BODY_HTML)])
    item = {"source_level": 1, "url": NOTICE_URL, "title": "公告一", "content": "公告一"}
    result = news_client.enrich_announcement_bodies([dict(item), dict(item)])
    assert result["fetched"] == 1
    assert len(calls) == 1


def test_enrich_reports_partial_failure(monkeypatch):
    """抓失败要说清"有几条只有标题"，不能让调用方以为公告本来就长这样。"""
    _patch_body(monkeypatch, [RuntimeError("timeout")])
    items = [{"source_level": 1, "url": NOTICE_URL, "title": "公告一", "content": "公告一"}]
    result = news_client.enrich_announcement_bodies(items)
    assert result["fetched"] == 0
    assert any("仅保留标题" in e for e in result["errors"])


def test_search_news_with_body_is_opt_in(monkeypatch):
    """默认不补正文（搜索页不该替用户付 10 次请求的等待）。"""
    calls = _patch_body(monkeypatch, [body_payload(BODY_HTML)])
    _patch(monkeypatch, "stock_news_em", df([]))
    _patch(monkeypatch, "stock_research_report_em", df([]))
    _patch_notices(monkeypatch, [NOTICE_ROW])

    plain = news_client.search_news(symbol="600519", days=30)
    assert calls == []
    assert plain["bodies_fetched"] == 0

    enriched = news_client.search_news(symbol="600519", days=30, with_body=True)
    assert enriched["bodies_fetched"] == 1
    notice = next(i for i in enriched["items"] if i["source_level"] == 1)
    assert "回购金额" in notice["content"]


def test_search_news_caps_items_and_counts_consistently(monkeypatch):
    """宏观查询会把当日全市场公告收进来（实测 1032 条），必须截断。

    截断必须发生在计数**之前**，否则 counts 会比列表长度大，前端并排显示时会像丢了数据。
    """
    monkeypatch.setattr(news_client, "MAX_ITEMS", 3)
    rows = [dict(NOTICE_ROW, **{"网址": f"https://x.com/n{i}", "公告标题": f"公告{i}"})
            for i in range(10)]
    _patch(monkeypatch, "stock_notice_report", df(rows))
    _patch(monkeypatch, "stock_info_global_em", df([]))
    result = news_client.search_news(days=30)
    assert len(result["items"]) == 3
    assert result["truncated"] is True
    assert sum(result["counts"].values()) == len(result["items"]) == 3


def test_search_news_truncated_flag_false_when_within_cap(monkeypatch):
    _patch_notices(monkeypatch, [NOTICE_ROW])
    _patch(monkeypatch, "stock_info_global_em", df([]))
    result = news_client.search_news(days=30)
    assert result["truncated"] is False


def test_with_body_requires_a_symbol(monkeypatch):
    """没有标的时"最近的公告"是随机公司的，补它们的正文纯属浪费请求。"""
    calls = _patch_body(monkeypatch, [body_payload(BODY_HTML)])
    _patch_notices(monkeypatch, [NOTICE_ROW])
    _patch(monkeypatch, "stock_info_global_em", df([]))
    result = news_client.search_news(days=30, with_body=True)      # 没有 symbol
    assert calls == []
    assert result["bodies_fetched"] == 0


# ==================== 个股公告历史（用户反馈"公告一条都没有"）====================

SYMBOL_NOTICE_ROW = {
    "art_code": "AN202608141827994407",
    "notice_date": "2026-08-15 00:00:00",
    "title": "贵州茅台:贵州茅台关于召开2026年半年度业绩说明会的公告",
    "columns": [{"column_code": "001002008", "column_name": "其他"}],
    "codes": [{"ann_type": "A,SHA", "short_name": "贵州茅台", "stock_code": "600519"}],
}


def _patch_symbol_notice_api(monkeypatch, pages):
    """桩住按股票拉公告的 HTTP 接口（按 page_index 返回给定的 data.list）。"""
    calls = []

    class FakeResponse:
        def __init__(self, rows):
            self._rows = rows

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"list": self._rows, "total_hits": sum(len(p) for p in pages)}, "success": 1}

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": dict(params or {})})
        page = int((params or {}).get("page_index", 1))
        rows = pages[page - 1] if page - 1 < len(pages) else []
        return FakeResponse(rows)

    monkeypatch.setattr(news_client.requests, "get", fake_get)
    return calls


def test_fetch_symbol_announcements_maps_list_fields(monkeypatch):
    """按股票拉历史公告：字段映射 + URL 形状必须与市场级路径**一致**（否则去重键对不上）。"""
    _patch_symbol_notice_api(monkeypatch, [[SYMBOL_NOTICE_ROW]])
    items = news_client.fetch_symbol_announcements("600519", days=90)
    assert len(items) == 1
    item = items[0]
    assert item["symbol"] == "SH600519"
    assert item["event_type_raw"] == "其他"                  # columns[0].column_name
    assert item["title"] == "贵州茅台关于召开2026年半年度业绩说明会的公告"   # 去掉重复的公司名前缀
    assert item["published_at"] == "2026-08-15 00:00:00"
    # 与 fetch_announcements 拼出的 URL 同一形状 —— 两条路径抓到的同一份公告必须命中同一个去重键
    assert item["url"] == ("https://data.eastmoney.com/notices/detail/600519/"
                           "AN202608141827994407.html")
    assert news_client.extract_art_code(item["url"]) == "AN202608141827994407"


def test_fetch_symbol_announcements_stops_at_cutoff(monkeypatch):
    """超出窗口的老公告不要（1074 条历史全都要进来就太夸张了）。"""
    old = dict(SYMBOL_NOTICE_ROW, **{"notice_date": "2020-01-01 00:00:00",
                                     "art_code": "AN202001010000000001"})
    calls = _patch_symbol_notice_api(monkeypatch, [[SYMBOL_NOTICE_ROW, old]])
    items = news_client.fetch_symbol_announcements("600519", days=90)
    assert len(items) == 1
    assert len(calls) == 1        # 撞到 cutoff 就停，不继续翻页


def test_fetch_symbol_announcements_fails_soft(monkeypatch):
    def boom(url, params=None, headers=None, timeout=None):
        raise RuntimeError("502 bad gateway")
    monkeypatch.setattr(news_client.requests, "get", boom)
    assert news_client.fetch_symbol_announcements("600519") == []
    assert news_client.fetch_symbol_announcements("00700") == []   # 非沪深标的直接跳过


def test_search_news_uses_per_symbol_announcements_when_symbol_given(monkeypatch):
    """给了 symbol 就必须走"按股票拉历史"那条路 —— 否则公告永远只有"服务跑过的那几天"。"""
    calls = _patch_symbol_notice_api(monkeypatch, [[SYMBOL_NOTICE_ROW]])
    _patch(monkeypatch, "stock_news_em", df([]))
    _patch(monkeypatch, "stock_research_report_em", df([]))
    result = news_client.search_news(symbol="600519", days=90)
    assert result["counts"]["notice"] == 1
    assert calls and calls[0]["params"]["stock_list"] == "600519"


def test_search_news_only_relevant_drops_incidental_media(monkeypatch):
    """"只是提及该股"的大盘综述不该出现在个股页上（显示了却不解释，用户会问"AI 有什么用"）。"""
    _patch_notices(monkeypatch, [])
    _patch(monkeypatch, "stock_research_report_em", df([]))
    _patch(monkeypatch, "stock_news_em", df([
        dict(NEWS_ROW, **{"新闻链接": "https://x.com/about",
                          "新闻标题": "贵州茅台被执行158万元？公司回应"}),
        dict(NEWS_ROW, **{"新闻链接": "https://x.com/roundup",
                          "新闻标题": "深沪北百元股数量达217只，科创板股票占46.08%"}),
    ]))
    monkeypatch.setattr(news_client, "_stock_name", lambda code: "贵州茅台")

    kept = news_client.search_news(symbol="600519", days=90, only_relevant=True)["items"]
    all_items = news_client.search_news(symbol="600519", days=90, only_relevant=False)["items"]
    assert [i["url"] for i in kept] == ["https://x.com/about"]
    assert len(all_items) == 2          # 不过滤时两条都在（搜索页要看全貌）


# ==================== 端点契约 POST /api/v1/news/fetch ====================

from fastapi.testclient import TestClient  # noqa: E402

import app as main  # noqa: E402

# conftest 把 INTERNAL_API_TOKEN 设成固定值（app.py 是 fail-closed 的）
TOKEN = getattr(main, "INTERNAL_API_TOKEN", "") or ""
HEADERS = {"X-Internal-Token": TOKEN} if TOKEN else {}

CANNED = {"items": [{"title": "t", "source_level": 1}], "counts": {"notice": 1}, "errors": []}


def _stub_search(monkeypatch, fn):
    monkeypatch.setattr(news_client, "search_news", fn)


def test_news_fetch_endpoint_ok_envelope(monkeypatch):
    _stub_search(monkeypatch, lambda *a, **k: CANNED)
    r = TestClient(main.app).post("/api/v1/news/fetch", json={"symbol": "600519"},
                                  headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"] == CANNED


def test_news_fetch_endpoint_passes_params_and_clamps_days(monkeypatch):
    seen = {}

    def spy(symbol, keyword, types, days, with_body=False, only_relevant=False):
        seen.update(symbol=symbol, keyword=keyword, types=types, days=days,
                    with_body=with_body, only_relevant=only_relevant)
        return CANNED

    _stub_search(monkeypatch, spy)
    TestClient(main.app).post(
        "/api/v1/news/fetch",
        json={"symbol": "sh600519", "keyword": "分红", "types": [1, 3], "days": 9999},
        headers=HEADERS)
    assert seen == {"symbol": "sh600519", "keyword": "分红", "types": [1, 3], "days": 365,
                    "with_body": False, "only_relevant": False}


def test_news_fetch_endpoint_passes_with_body(monkeypatch):
    """`withBody` 必须真的透传 —— 个股页靠它拿到公告正文，漏传就退回"只有标题"。"""
    seen = {}

    def spy(symbol, keyword, types, days, with_body=False, only_relevant=False):
        seen["with_body"] = with_body
        seen["only_relevant"] = only_relevant
        return CANNED

    _stub_search(monkeypatch, spy)
    TestClient(main.app).post("/api/v1/news/fetch",
                              json={"symbol": "600519", "withBody": True,
                                    "onlyRelevant": True},
                              headers=HEADERS)
    assert seen["with_body"] is True
    # 个股页必须同时打开相关度过滤：显示了却不解释，用户会问"那 AI 设置的意义是什么"
    assert seen["only_relevant"] is True


def test_news_fetch_endpoint_rejects_bad_json(monkeypatch):
    _stub_search(monkeypatch, lambda *a, **k: pytest.fail("不该进入业务逻辑"))
    r = TestClient(main.app).post("/api/v1/news/fetch", content="not json",
                                  headers=HEADERS)
    assert r.status_code == 400
    assert r.json()["ok"] is False


@pytest.mark.parametrize("payload", [
    {"days": "abc"},          # 非数字
    {"types": 1},             # 不是数组
    {"types": ["a"]},         # 元素不是数字
])
def test_news_fetch_endpoint_rejects_bad_params(monkeypatch, payload):
    _stub_search(monkeypatch, lambda *a, **k: pytest.fail("参数非法时不该调用取数"))
    r = TestClient(main.app).post("/api/v1/news/fetch", json=payload, headers=HEADERS)
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_news_fetch_endpoint_rejects_unknown_type(monkeypatch):
    """未知类型必须报错 —— 静默忽略会返回空结果，被误读成"这只票没有资讯"。"""
    _stub_search(monkeypatch, lambda *a, **k: pytest.fail("未知类型时不该调用取数"))
    r = TestClient(main.app).post("/api/v1/news/fetch", json={"types": [9]}, headers=HEADERS)
    assert r.status_code == 400
    assert "9" in r.json()["error"]


def test_news_fetch_endpoint_fails_soft_on_backend_error(monkeypatch):
    """取数层抛异常时给中文可读的兜底，而不是 500 或英文栈。"""
    def boom(*args, **kwargs):
        raise RuntimeError("upstream exploded")

    _stub_search(monkeypatch, boom)
    r = TestClient(main.app).post("/api/v1/news/fetch", json={"symbol": "600519"},
                                  headers=HEADERS)
    assert r.status_code == 502
    body = r.json()
    assert body["ok"] is False
    assert "暂时不可用" in body["error"]


def test_news_fetch_endpoint_requires_internal_token(monkeypatch):
    """fail-closed 的服务间鉴权：不带内部 token 一律 401。"""
    _stub_search(monkeypatch, lambda *a, **k: pytest.fail("未鉴权的请求不该进入业务逻辑"))
    r = TestClient(main.app).post("/api/v1/news/fetch", json={"symbol": "600519"})
    assert r.status_code == 401


# ==================== 社区舆论（股吧人气榜） ====================

def test_fetch_market_opinion_summarizes_top10(monkeypatch):
    """舆论源（LEVEL_FORUM 的数据，2026-10 接入）：一天一条汇总，不是一名一条。"""
    import pandas as pd
    monkeypatch.setattr(news_client.akshare, "stock_hot_rank_em", lambda: pd.DataFrame([
        {"当前排名": 1, "代码": "SH601127", "股票名称": "赛力斯", "最新价": 46.94, "涨跌额": 0.13, "涨跌幅": 0.28},
        {"当前排名": 2, "代码": "SH600418", "股票名称": "江淮汽车", "最新价": 27.47, "涨跌额": 1.51, "涨跌幅": 5.49},
    ]))
    items = _REAL_MARKET_OPINION()
    assert len(items) == 1
    item = items[0]
    assert item["source_level"] == news_client.LEVEL_FORUM
    assert "赛力斯" in item["title"] and "居首" in item["title"]
    assert "人气榜前十" in item["content"] and "江淮汽车" in item["content"]
    assert item["published_at"]  # 榜单条目也要有时间，否则进不了时间轴


def test_fetch_market_opinion_retries_once_then_degrades(monkeypatch):
    """上游秒级限流是常态：一次重试后再失败必须降级为空，而不是抛异常。"""
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        raise ConnectionError("Remote end closed connection")

    monkeypatch.setattr(news_client.akshare, "stock_hot_rank_em", flaky)
    monkeypatch.setattr(news_client.time, "sleep", lambda s: None)
    assert _REAL_MARKET_OPINION() == []
    assert calls["n"] == 2  # 首次 + 一次重试，不无限重试


def test_search_news_without_symbol_includes_opinion(monkeypatch):
    """大盘口径：舆情源进聚合（此前是'暂无数据源'的空占位）。"""
    # published_at 用"几小时前"动态生成：写死日历日期的话，测试一过窗口期就
    # 被 days 过滤掉、永远失败（2026-10-06 全量回归时就是这么挂的）。
    from datetime import datetime, timedelta

    recent = (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setattr(news_client, "fetch_market_opinion", lambda: [
        {"symbol": "", "name": "", "title": "股吧人气榜", "content": "前十",
         "url": "", "source_level": 4, "source_name": "东方财富·股吧",
         "event_type_raw": "", "published_at": recent}])
    monkeypatch.setattr(news_client, "fetch_announcements", lambda: [])
    monkeypatch.setattr(news_client, "fetch_market_news", lambda: [])
    out = news_client.search_news(symbol="", keyword="", types=[4], days=1)
    assert out["counts"]["forum"] == 1


# ==================== 财联社电报（2026-10-16，直连源） ====================


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_cls_telegraph_shapes_rows(monkeypatch):
    """电报行 → 统一事件结构：时间戳转北京时间、无标题剥正文、HTML 剥除。"""
    monkeypatch.setattr(news_client, "fetch_cls_telegraph", _REAL_FETCH_CLS)
    payload = {"data": {"roll_data": [
        {"title": "电报甲", "content": "正文<b>加粗</b>内容", "ctime": 1760586000,
         "shareurl": "https://www.cls.cn/detail/abc"},
        {"title": "", "content": "【无标题条】这条从正文剥标题", "ctime": 1760586060,
         "shareurl": ""},
    ]}}
    monkeypatch.setattr(news_client.requests, "get", lambda *a, **k: _FakeResp(payload))
    rows = news_client.fetch_cls_telegraph()
    assert len(rows) == 2
    assert rows[0]["source_name"] == "财联社电报" and rows[0]["source_level"] == 2
    assert "加粗" in rows[0]["content"] and "<b>" not in rows[0]["content"]
    assert rows[0]["published_at"].startswith("2025-10-16")  # 1760586000 = 北京时间 2025-10-16
    assert rows[1]["title"]  # 无标题条目从正文剥出标题，不留空（空标题会被 Java 落库丢弃）


def test_cls_telegraph_fail_open(monkeypatch):
    """网络失败返回 []：一家源挂了不拖垮其余 wire 源。"""
    monkeypatch.setattr(news_client, "fetch_cls_telegraph", _REAL_FETCH_CLS)

    def boom(*a, **k):
        raise news_client.requests.RequestException("blocked")
    monkeypatch.setattr(news_client.requests, "get", boom)
    assert news_client.fetch_cls_telegraph() == []


# ==================== 人气榜（2026-10-16，热榜数据原语） ====================


def test_hot_rank_shapes_and_caches(monkeypatch):
    """人气榜 → 排名升序的扁平结构；5 分钟窗口内共享缓存（不重复打源站）。"""
    import akshare_client

    calls = {"n": 0}

    class FakeDf:
        def __init__(self, rows):
            self.rows = rows

        def iterrows(self):
            return iter(enumerate(self.rows))

    def fake_em():
        calls["n"] += 1
        return FakeDf([
            {"当前排名": 2, "代码": "SZ000530", "股票名称": "冰山冷热", "最新价": 7.62, "涨跌幅": 9.96},
            {"当前排名": 1, "代码": "SH600418", "股票名称": "江淮汽车", "最新价": 22.37, "涨跌幅": 9.98},
            {"当前排名": "脏行", "代码": "SH000000", "股票名称": "坏数据", "最新价": None, "涨跌幅": None},
        ])

    monkeypatch.setattr(akshare_client.akshare, "stock_hot_rank_em", fake_em)
    monkeypatch.setattr(akshare_client, "_hot_rank_cache", {"at": 0.0, "rows": []})
    rows = akshare_client.get_hot_rank()
    assert calls["n"] == 1
    assert [r["rank"] for r in rows] == [1, 2]          # 排名升序，脏行剔除
    assert rows[0]["symbol"] == "SH600418" and rows[0]["name"] == "江淮汽车"
    again = akshare_client.get_hot_rank()
    assert calls["n"] == 1                                # 命中缓存，源站零请求
    assert again == rows


def test_hot_rank_fail_open(monkeypatch):
    import akshare_client

    def boom():
        raise RuntimeError("upstream down")
    monkeypatch.setattr(akshare_client.akshare, "stock_hot_rank_em", boom)
    monkeypatch.setattr(akshare_client, "_hot_rank_cache", {"at": 0.0, "rows": []})
    assert akshare_client.get_hot_rank() == []


# ==================== 大盘资金流（2026-10-16） ====================


def test_market_fund_flow_shapes_and_caches(monkeypatch):
    """净额元→亿元、升序、缓存复用；脏行（日期缺）剔除。"""
    import akshare_client

    calls = {"n": 0}

    class FakeDf:
        def __init__(self, rows):
            self.rows = rows

        def iterrows(self):
            return iter(enumerate(self.rows))

    def fake_flow():
        calls["n"] += 1
        return FakeDf([
            {"日期": __import__("datetime").date(2026, 10, 15),
             "上证-收盘价": 3700.0, "上证-涨跌幅": 0.5,
             "主力净流入-净额": -5_000_000_000.0, "超大单净流入-净额": 100_000_000.0,
             "大单净流入-净额": -5_100_000_000.0, "中单净流入-净额": -2_000_000_000.0,
             "小单净流入-净额": 7_000_000_000.0},
            {"日期": "2026-10-16",  # 字符串日期也要认（不同源给法不一）
             "上证-收盘价": 3716.28, "上证-涨跌幅": 0.24,
             "主力净流入-净额": -4_936_298_496.0, "超大单净流入-净额": 112_631_808.0,
             "大单净流入-净额": -5_048_930_304.0, "中单净流入-净额": -11_235_790_848.0,
             "小单净流入-净额": 16_172_085_248.0},
            {"日期": None, "主力净流入-净额": 1.0},  # 脏行：无日期剔除
        ])

    monkeypatch.setattr(akshare_client.akshare, "stock_market_fund_flow", fake_flow)
    monkeypatch.setattr(akshare_client, "_fund_flow_cache", {"at": 0.0, "rows": []})
    rows = akshare_client.get_market_fund_flow(limit=10)
    assert calls["n"] == 1 and len(rows) == 2
    assert rows[0]["date"] == "2026-10-15" and rows[1]["date"] == "2026-10-16"  # 升序
    assert rows[1]["main_net_yi"] == round(-4_936_298_496.0 / 1e8, 2)  # 元→亿元
    assert rows[1]["super_net_yi"] == 1.13
    again = akshare_client.get_market_fund_flow(limit=10)
    assert calls["n"] == 1 and again == rows  # 10 分钟窗口内共享缓存


def test_market_fund_flow_fail_open(monkeypatch):
    import akshare_client

    def boom():
        raise RuntimeError("upstream down")
    monkeypatch.setattr(akshare_client.akshare, "stock_market_fund_flow", boom)
    # 腾讯 fallback 也必须桩掉（2026-10-19 双源后）：不桩的话本机网络能真连上
    # 腾讯，"fail-open"用例会拿到 50 行真数据——测试密闭性被环境网络决定。
    monkeypatch.setattr(akshare_client, "_fetch_fund_flow_rows_tencent", lambda max_days=50: [])
    monkeypatch.setattr(akshare_client, "_fund_flow_cache",
                        {"at": 0.0, "tried_at": 0.0, "rows": [], "ok": False})
    assert akshare_client.get_market_fund_flow() == []


def test_lhb_events_shape(monkeypatch):
    """龙虎榜源（2026-10-16）：行→事件结构（symbol 归一、日级时间、解读进标题）。"""
    monkeypatch.setattr(news_client, "fetch_lhb_events", _REAL_FETCH_LHB)
    class FakeDf:
        empty = False   # _frame_rows 按 df.empty 判空，桩必须显式非空

        def __init__(self, rows):
            self.rows = rows
        def iterrows(self):
            return iter(enumerate(self.rows))
    payload = FakeDf([
        {"代码": "600418", "名称": "江淮汽车", "上榜日": "2026-10-16",
         "解读": "2家机构买入，成功率65%"},
        {"代码": "", "名称": "脏行", "上榜日": "x", "解读": ""},
    ])
    monkeypatch.setattr(news_client.akshare, "stock_lhb_detail_em", lambda **k: payload)
    rows = news_client.fetch_lhb_events()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "SH600418" and rows[0]["source_name"] == "东财·龙虎榜"
    assert "江淮汽车" in rows[0]["title"] and "机构买入" in rows[0]["title"]
    assert rows[0]["published_at"].startswith("2026-10-16")


def test_lhb_fail_open(monkeypatch):
    monkeypatch.setattr(news_client, "fetch_lhb_events", _REAL_FETCH_LHB)

    def boom(**k):
        raise RuntimeError("upstream")
    monkeypatch.setattr(news_client.akshare, "stock_lhb_detail_em", boom)
    assert news_client.fetch_lhb_events() == []
