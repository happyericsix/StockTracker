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


# 大盘口径的 search_news 默认会带舆情源（股吧人气榜）；测试必须离线，
# 统一桩掉它的上游取数（个别用例需要真实形状时自己再覆盖这个桩）。
@pytest.fixture(autouse=True)
def _no_market_opinion(monkeypatch):
    monkeypatch.setattr(news_client, "fetch_market_opinion", lambda limit=10: [])

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


def _patch(monkeypatch, name, value=None, error=None):
    """把 akshare 的某个接口换成桩：返回 DataFrame 或抛异常。"""
    def fake(**kwargs):
        if error is not None:
            raise error
        return value
    monkeypatch.setattr(news_client.akshare, name, fake)


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
    monkeypatch.setattr(news_client, "fetch_market_opinion", lambda: [
        {"symbol": "", "name": "", "title": "股吧人气榜", "content": "前十",
         "url": "", "source_level": 4, "source_name": "东方财富·股吧",
         "event_type_raw": "", "published_at": "2026-10-02 12:00:00"}])
    monkeypatch.setattr(news_client, "fetch_announcements", lambda: [])
    monkeypatch.setattr(news_client, "fetch_market_news", lambda: [])
    out = news_client.search_news(symbol="", keyword="", types=[4], days=1)
    assert out["counts"]["forum"] == 1
