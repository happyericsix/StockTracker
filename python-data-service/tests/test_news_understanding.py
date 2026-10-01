# -*- coding: utf-8 -*-
"""N2 分析层单测。

<h3>这一层的测试重点不是"正常路径能跑通"，而是"出错时会不会静默给出错误结论"</h3>
N2 最大的风险（spec §13 🔴）不是抛异常，而是**悄悄把一个错误的方向标签当成结论**
交给用户。所以下面的用力点全在降级链、枚举校验、低置信置空、以及"模型输出与输入
的对应关系"上 —— 一个张冠李戴的分析结果看起来完全正常，是这里最危险的一类错。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm_service  # noqa: E402
import news_understanding as nu  # noqa: E402


def make_item(**overrides):
    item = {
        "symbol": "SH600519", "name": "贵州茅台",
        "title": "贵州茅台:关于回购公司股份的公告",
        "content": "公司拟以自有资金回购股份，回购金额不低于 10 亿元。",
        "url": "https://example.com/a",
        "source_level": 1, "source_name": "东方财富·公告",
        "event_type_raw": "回购", "published_at": "2026-09-19 00:00:00",
    }
    item.update(overrides)
    return item


GOOD_ANALYSIS = {
    "event_type": "回购", "direction": "利好", "confidence": 0.82,
    "impact_level": "high", "related_symbols": ["SH600519"],
    "plain_summary": "公司拟回购不低于 10 亿元股份。对持有者意味着每股收益可能被抬升。",
}


def patch_llm(monkeypatch, replies, available=True):
    """把 LLM 换成按顺序吐出 `replies` 的桩；返回记录调用的列表。"""
    calls = []

    def fake_completion(messages, tools=None, temperature=0.2, max_tokens=1200):
        calls.append({"messages": messages, "temperature": temperature, "max_tokens": max_tokens})
        reply = replies[min(len(calls) - 1, len(replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return {"message": {"role": "assistant", "content": reply}}

    monkeypatch.setattr(llm_service, "chat_completion", fake_completion)
    monkeypatch.setattr(llm_service, "_is_available", lambda: available)
    return calls


# ==================== extract_json ====================

def test_extract_json_accepts_fenced_and_bare_forms():
    assert nu.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert nu.extract_json('```\n{"a": 1}\n```') == {"a": 1}
    assert nu.extract_json('{"a": 1}') == {"a": 1}
    assert nu.extract_json('```json\n[{"a": 1}]\n```') == [{"a": 1}]   # 顶层数组
    assert nu.extract_json('[{"a": 1}]') == [{"a": 1}]


def test_extract_json_returns_none_on_garbage():
    assert nu.extract_json("模型觉得这条是利好") is None
    assert nu.extract_json("") is None
    assert nu.extract_json(None) is None
    assert nu.extract_json('{"a": ') is None


# ==================== sanitize（合规后置过滤）====================

def test_sanitize_blocks_advisory_phrases():
    """spec §10 点名的句式一个都不能漏出去。"""
    for text in ("建议买入该股票", "建议卖出", "可以加仓", "立即清仓",
                 "必涨", "稳赚不赔", "马上买入", "推荐买入"):
        out = nu.sanitize(text)
        for banned in ("建议买入", "建议卖出", "可以加仓", "立即清仓", "必涨", "稳赚", "推荐买入"):
            assert banned not in out, f"{banned} 未被拦住: {text} -> {out}"


def test_sanitize_appends_disclaimer_once():
    once = nu.sanitize("公司拿到大额订单")
    assert nu.DISCLAIMER in once
    assert nu.sanitize(once) == once          # 幂等
    assert once.count(nu.DISCLAIMER) == 1


def test_risk_list_does_not_carry_disclaimer():
    """风险条目是短句，不该每条都拖一个免责句（那样红绿列表会被淹没）。"""
    analysis = nu.normalize_analysis(dict(GOOD_ANALYSIS, risks=["交付进度取决于客户项目"]),
                                     make_item())
    assert analysis["risks"] == ["交付进度取决于客户项目"]
    assert nu.DISCLAIMER in analysis["plain_summary"]       # 整条解读级别带一次
    assert nu.DISCLAIMER not in analysis["risks"][0]


# ==================== normalize_analysis（防幻觉最后一关）====================

def test_unknown_enum_becomes_null_not_neutral():
    """"模型说了个没见过的词"与"模型判了中性"是两件事，不能混为一谈。"""
    analysis = nu.normalize_analysis(dict(GOOD_ANALYSIS, direction="看多"), make_item())
    assert analysis["direction"] is None


def test_low_confidence_blanks_direction_and_says_so():
    analysis = nu.normalize_analysis(
        dict(GOOD_ANALYSIS, direction="利好", confidence=0.2), make_item())
    assert analysis["direction"] is None
    assert "不判断方向" in analysis["plain_summary"]


def test_missing_summary_falls_back_to_degraded_text():
    analysis = nu.normalize_analysis({"direction": None, "confidence": 0.0}, make_item())
    assert analysis["plain_summary"].startswith(nu._DEGRADED_SUMMARY)


def test_related_symbols_fall_back_to_item_symbol():
    analysis = nu.normalize_analysis(dict(GOOD_ANALYSIS, related_symbols=[]), make_item())
    assert analysis["related_symbols"] == ["SH600519"]


def test_related_symbols_normalized_to_prefixed_form():
    """模型回裸代码时要补前缀，否则前端按代码关联会遇到两种写法。"""
    analysis = nu.normalize_analysis(dict(GOOD_ANALYSIS, related_symbols=["600519"]),
                                     make_item())
    assert analysis["related_symbols"] == ["SH600519"]


def test_invalid_input_types_do_not_raise():
    for raw in (None, "字符串", 42, []):
        analysis = nu.normalize_analysis(raw, make_item())
        assert analysis["direction"] is None
        assert analysis["impact_level"] == "low"


# ==================== 预筛 ====================

def test_prefilter_passes_notices_and_reports_always():
    assert nu.passes_prefilter(make_item(source_level=1, title="关于变更会计师事务所"))
    assert nu.passes_prefilter(make_item(source_level=3, title="2026 中报点评"))


def test_prefilter_skips_media_that_only_mentions_the_stock():
    """L2 相关度：媒体必须**关于**这只票，命中关键词但与该票无关的照样跳过。

    实测依据（2026-09-19 真实链路）：问 600519 的新闻，前 5 条里有 3 条是
    「深沪北百元股数量达217只」这类大盘综述 —— 挂在茅台名下但和它没关系。
    """
    # 只是被"提及"：正文里既没有股票名也没有代码
    assert not nu.passes_prefilter(make_item(
        source_level=2, event_type_raw="", title="深沪北百元股数量达217只"))
    # 命中关键词但不关于这只票 —— 旧规则会放行，新规则正确地跳过
    assert not nu.passes_prefilter(make_item(
        source_level=2, event_type_raw="", title="某公司股东拟减持 2%"))


def test_prefilter_passes_relevant_media_even_without_keywords():
    """关于这只票的媒体**不再要求命中关键词表**。

    关键词表是为"全市场每日上千条公告"设计的量级闸门；单只票的媒体只有 ≤10 条，
    再用关键词卡一道会漏掉真正 material 的新闻 —— 例如
    "贵州茅台中报净利润同比下降1.95%" 不含任何关键词，但对持有者当然重要。
    """
    assert nu.passes_prefilter(make_item(
        source_level=2, event_type_raw="",
        title="贵州茅台600519.SH)：2026年中报净利润为445.17亿元、同比较去年同期下降1.95%",
        content="2026年8月15日，贵州茅台发布2026年中报。"))


def test_prefilter_keeps_keyword_gate_for_macro_media():
    """无从判断相关度的宏观/行业资讯仍走关键词表控量。

    否则 200 条全球快讯会全部涌入（一次 10 批模型调用）。
    """
    macro = make_item(source_level=2, event_type_raw="", symbol="", name="",
                      title="某国央行宣布加息", content="货币政策调整。")
    assert not nu.passes_prefilter(macro)
    macro["title"] = "某公司发布重大合同公告"
    assert nu.passes_prefilter(macro)


def test_prefilter_catches_enforcement_news():
    """"被执行"是真实链路里发现的漏网项：明确的负面事件，原关键词表一个都不匹配。"""
    assert nu.passes_prefilter(make_item(source_level=2, event_type_raw="",
                                         title="贵州茅台被执行158万元？公司回应",
                                         content="公司回应称系第三方公司内部合同纠纷。"))
    # 但不能因为放宽而误伤："执行董事"不该被当成风险事件
    assert not nu.passes_prefilter(make_item(
        source_level=2, event_type_raw="", title="关于选举执行董事的公告", content="例行人事安排。"))


def test_is_about_the_stock_is_three_state():
    """三态：True 关于 / False 明确不是 / None 无从判断。

    把 None 与 False 合并成"不相关"是最容易犯的错：它会把"查不到这只票叫什么"
    当成"这条新闻与它无关"，于是宏观与行业资讯被静默丢掉。
    """
    import news_client as nc

    assert nc.is_about_the_stock({"symbol": "SH600519", "name": "贵州茅台",
                                  "title": "贵州茅台拟回购", "content": ""}) is True
    assert nc.is_about_the_stock({"symbol": "SH600519", "name": "贵州茅台",
                                  "title": "白酒行业动销跟踪", "content": ""}) is False
    assert nc.is_about_the_stock({"symbol": "", "name": "",
                                  "title": "央行宣布降准", "content": ""}) is None
    # 只有代码也能判：正文里出现裸代码同样算"关于"
    assert nc.is_about_the_stock({"symbol": "SH600519", "name": "",
                                  "title": "600519 今日大宗交易", "content": ""}) is True


def test_is_about_the_stock_only_reads_the_title():
    """判据**只看标题** —— 把正文也算进去会被大盘综述骗过。

    这是实测出来的，不是保守起见：`stock_news_em` 返回的
    「深沪北百元股数量达217只」「百元股数量达209只」**正文里**就列着"贵州茅台"与 600519
    （百元股名单），于是"名字出现在正文"这个判据会把它们全判成"关于茅台"。
    """
    import news_client as nc

    roundup = {
        "symbol": "SH600519", "name": "贵州茅台",
        "title": "深沪北百元股数量达217只，科创板股票占46.08%",
        "content": "其中贵州茅台(600519)等个股入选百元股名单。",
    }
    assert nc.is_about_the_stock(roundup) is False, "大盘综述不该被判成「关于这只票」"
    assert not nu.passes_prefilter(dict(roundup, source_level=2, event_type_raw=""))


def test_prefilter_still_keeps_the_relevant_media_that_was_wrongly_skipped():
    """旧规则（只认关键词表）会漏掉这条：它不含任何关键词，但确实关于这只票。"""
    item = make_item(source_level=2, event_type_raw="",
                     title="贵州茅台600519.SH)：2026年中报净利润为445.17亿元、同比下降1.95%",
                     content="")
    assert nu.passes_prefilter(item)


# ==================== 降级条目的契约完整性（真实链路抓出来的 bug）====================

# spec §6 要求每条资讯都带齐这些字段 —— 前端按它取，缺一个就是 undefined
SPEC_FIELDS = ("event_type", "direction", "confidence", "impact_level",
               "related_symbols", "source_level", "plain_summary",
               "risks", "opportunities")


def test_every_item_carries_full_spec_schema_in_all_paths(monkeypatch):
    """降级条目也必须字段齐全。

    第一版 `_degraded()` 漏了 `related_symbols`，于是"被预筛跳过"的条目比"分析过"的
    条目少一个键 —— 而单测当时只断言了分析成功的路径，所以没发现。
    这个用例把三条路径（分析过 / 预筛跳过 / 模型不可用）放在一起断言同一份字段表，
    这类"某条支路少了字段"的错才守得住。
    """
    patch_llm(monkeypatch, [json.dumps({"results": [dict(GOOD_ANALYSIS, index=1)]},
                                       ensure_ascii=False)])
    noisy = make_item(source_level=2, title="深沪北百元股数量达217只", event_type_raw="",
                      url="https://x.com/noisy")
    result = nu.analyze_events([make_item(), noisy])
    for entry in result["items"]:
        missing = [f for f in SPEC_FIELDS if f not in entry]
        assert not missing, f"{entry.get('url')} 缺少字段 {missing}"

    single = nu.analyze_one(make_item())
    assert not [f for f in SPEC_FIELDS if f not in single]


def test_llm_unavailable_path_also_has_full_schema(monkeypatch):
    patch_llm(monkeypatch, ["不该被调用"], available=False)
    result = nu.analyze_events([make_item()])
    assert not [f for f in SPEC_FIELDS if f not in result["items"][0]]


def test_degraded_summary_carries_disclaimer(monkeypatch):
    """降级文案也要过合规过滤。

    否则同一页面上"模型给的解读"带免责句、"降级给的解读"不带，看起来像忘了加。
    """
    patch_llm(monkeypatch, ["不是 JSON", "还不是 JSON"])
    result = nu.analyze_events([make_item()])
    assert nu.DISCLAIMER in result["items"][0]["plain_summary"]
    patch_llm(monkeypatch, ["x"], available=False)
    assert nu.DISCLAIMER in nu.analyze_one(make_item())["plain_summary"]


def test_degraded_related_symbols_comes_from_item(monkeypatch):
    patch_llm(monkeypatch, ["x"], available=False)
    assert nu.analyze_one(make_item())["related_symbols"] == ["SH600519"]
    # 宏观条目没有 symbol，就该是空数组而不是 [""]
    assert nu.analyze_one(make_item(symbol=""))["related_symbols"] == []


def test_direction_note_is_not_duplicated():
    """模型自己已经写了"不判断方向"时，不要再追加一遍。

    真实链路里的实际输出是"信息不足，不判断方向。…（置信度不足，不判断方向）"，
    同一句话在一行里出现了两次。
    """
    analysis = nu.normalize_analysis(
        dict(GOOD_ANALYSIS, direction="利好", confidence=0.2,
             plain_summary="信息不足，不判断方向。只有标题，看不出金额。"), make_item())
    assert analysis["plain_summary"].count("不判断方向") == 1


def test_null_direction_with_high_confidence_gets_clamped():
    """`direction=null` + 高置信 = 模型拿 null 表达了本该由 `中性` 表达的意思。

    spec §6 的分工是：`中性` = 确认无方向性影响，`null` = 信息不足。
    处理方式是**压低置信度**而不是偷偷改成中性 —— 模型拒绝给方向时，
    代码不该替它发明一个。模型原话仍留在 plain_summary 里，信息没丢。
    """
    analysis = nu.normalize_analysis(
        dict(GOOD_ANALYSIS, direction=None, confidence=0.9,
             plain_summary="仅为程序性披露"), make_item())
    assert analysis["direction"] is None
    assert analysis["confidence"] < nu.MIN_CONFIDENCE
    assert "仅为程序性披露" in analysis["plain_summary"]


def test_neutral_direction_is_preserved():
    """真正的"中性"是合法结论，不能被当成"没判"处理掉。"""
    analysis = nu.normalize_analysis(
        dict(GOOD_ANALYSIS, direction="中性", confidence=0.8), make_item())
    assert analysis["direction"] == "中性"
    assert analysis["confidence"] == 0.8


# ==================== analyze_events ====================

def test_analyze_events_happy_path(monkeypatch):
    patch_llm(monkeypatch, [json.dumps({"results": [dict(GOOD_ANALYSIS, index=1)]},
                                       ensure_ascii=False)])
    result = nu.analyze_events([make_item()])
    assert result["analyzed"] == 1
    assert result["items"][0]["direction"] == "利好"
    assert result["items"][0]["analyzed"] is True
    # 原始字段必须还在（端点契约是"原始字段 + 分析字段"）
    assert result["items"][0]["title"].startswith("贵州茅台")
    assert result["items"][0]["source_level"] == 1


def test_analyze_events_maps_by_index_not_order(monkeypatch):
    """模型把 results 顺序调换了也必须对回去 —— 靠 index，不靠顺序。"""
    items = [make_item(title="第一条", url="https://x.com/1"),
             make_item(title="第二条", url="https://x.com/2")]
    patch_llm(monkeypatch, [json.dumps({"results": [
        {"index": 2, "direction": "利空", "confidence": 0.9, "event_type": "监管处罚",
         "impact_level": "high", "plain_summary": "第二条的结论"},
        {"index": 1, "direction": "利好", "confidence": 0.9, "event_type": "回购",
         "impact_level": "high", "plain_summary": "第一条的结论"},
    ]}, ensure_ascii=False)])
    result = nu.analyze_events(items)
    assert result["items"][0]["title"] == "第一条"
    assert result["items"][0]["direction"] == "利好"
    assert result["items"][1]["title"] == "第二条"
    assert result["items"][1]["direction"] == "利空"


def test_analyze_events_does_not_confuse_identical_items(monkeypatch):
    """两条内容完全相同的资讯，分析结果不能互相串。

    这是用 `list.index()` / `in` 定位条目时会踩的坑：两者对 dict 走 `==` 值比较，
    于是第二条会拿到第一条的结论 —— 而"看起来有一个方向结论"让这种错极难发现。
    所以实现改成全程按**下标**记账。
    """
    same = make_item()
    patch_llm(monkeypatch, [json.dumps({"results": [
        {"index": 1, "direction": "利好", "confidence": 0.9, "plain_summary": "第一条结论"},
        {"index": 2, "direction": "利空", "confidence": 0.9, "plain_summary": "第二条结论"},
    ]}, ensure_ascii=False)])
    result = nu.analyze_events([same, dict(same)])
    assert len(result["items"]) == 2
    assert "第一条结论" in result["items"][0]["plain_summary"]
    assert "第二条结论" in result["items"][1]["plain_summary"]


def test_analyze_events_splits_into_batches_of_five(monkeypatch):
    calls = patch_llm(monkeypatch, [json.dumps({"results": []})])
    items = [make_item(title=f"减持公告 {i}", url=f"https://x.com/{i}") for i in range(6)]
    nu.analyze_events(items)
    assert len(calls) == 2          # 5 + 1
    assert nu.BATCH_SIZE == 5


def test_analyze_events_retries_once_then_succeeds(monkeypatch):
    """第一次吐非 JSON、第二次吐合法 JSON —— 重试要真的发生。"""
    calls = patch_llm(monkeypatch, [
        "我觉得这条是利好（忘记输出 JSON 了）",
        json.dumps({"results": [dict(GOOD_ANALYSIS, index=1)]}, ensure_ascii=False),
    ])
    result = nu.analyze_events([make_item()])
    assert len(calls) == 2
    assert result["analyzed"] == 1
    # 重试时的消息里要明确指出问题，不能只是原样再问一遍
    retry_messages = calls[1]["messages"]
    assert any("不是合法 JSON" in m["content"] for m in retry_messages)


def test_analyze_events_degrades_after_two_bad_replies(monkeypatch):
    calls = patch_llm(monkeypatch, ["不是 JSON", "还不是 JSON"])
    result = nu.analyze_events([make_item()])
    assert len(calls) == 2                      # 只重试一次，不无限重试
    assert result["analyzed"] == 0
    assert result["items"][0]["direction"] is None
    assert result["items"][0]["analyzed"] is False
    assert result["items"][0]["plain_summary"].startswith("信息不足")
    assert result["items"][0]["title"]          # 原文照常保留（信息不足 ≠ 丢弃原文）
    assert result["errors"]


def test_analyze_events_llm_unavailable_degrades_without_calling(monkeypatch):
    """没配 key 时不许发请求，但必须**明说原因**，不能静默返回空方向。"""
    calls = patch_llm(monkeypatch, ["不该被调用"], available=False)
    result = nu.analyze_events([make_item()])
    assert calls == []
    assert result["items"][0]["direction"] is None
    assert any("未配置" in e for e in result["errors"])


def test_analyze_events_llm_exception_degrades(monkeypatch):
    patch_llm(monkeypatch, [RuntimeError("connection reset")])
    result = nu.analyze_events([make_item()])
    assert result["items"][0]["direction"] is None
    assert any("暂时不可用" in e for e in result["errors"])


def test_analyze_events_keeps_prefiltered_items_in_output(monkeypatch):
    """被预筛跳过的条目必须**出现在输出里**（只是没有分析字段）。

    丢掉它们等于悄悄删数据：用户在个股页会以为"这段时间没有资讯"。
    """
    patch_llm(monkeypatch, [json.dumps({"results": [dict(GOOD_ANALYSIS, index=1)]},
                                       ensure_ascii=False)])
    noisy = make_item(source_level=2, title="深沪北百元股数量达217只", event_type_raw="",
                      url="https://x.com/noisy")
    result = nu.analyze_events([make_item(), noisy])
    assert len(result["items"]) == 2
    assert result["items"][1]["url"] == "https://x.com/noisy"
    assert result["items"][1]["analyzed"] is False
    assert result["skipped"] == 1


def test_analyze_events_keeps_items_beyond_cap(monkeypatch):
    """超过单次上限的条目也不能消失（同样的问题，同样的理由）。"""
    monkeypatch.setattr(nu, "MAX_ITEMS_PER_CALL", 2)
    patch_llm(monkeypatch, [json.dumps({"results": []})])
    items = [make_item(title=f"公告 {i}", url=f"https://x.com/{i}") for i in range(5)]
    result = nu.analyze_events(items)
    assert len(result["items"]) == 5
    assert [i["url"] for i in result["items"]] == [f"https://x.com/{i}" for i in range(5)]


def test_analyze_events_output_order_matches_input(monkeypatch):
    """输出与输入同序 —— 调用方通常已按时间倒序排好，重排会让"最近一条"跑到中间。"""
    patch_llm(monkeypatch, [json.dumps({"results": [
        {"index": 3, "direction": "利好", "confidence": 0.9, "plain_summary": "三"},
        {"index": 1, "direction": "利空", "confidence": 0.9, "plain_summary": "一"},
        {"index": 2, "direction": "中性", "confidence": 0.9, "plain_summary": "二"},
    ]}, ensure_ascii=False)])
    items = [make_item(title=f"公告{i}", url=f"https://x.com/{i}") for i in (1, 2, 3)]
    result = nu.analyze_events(items)
    assert [i["title"] for i in result["items"]] == ["公告1", "公告2", "公告3"]
    assert [i["plain_summary"][:1] for i in result["items"]] == ["一", "二", "三"]


def test_analyze_events_empty_input_is_safe():
    assert nu.analyze_events([]) == {"items": [], "analyzed": 0, "skipped": 0, "errors": []}


def test_analyze_events_ignores_results_with_bad_index(monkeypatch):
    patch_llm(monkeypatch, [json.dumps({"results": [
        {"index": 99, "direction": "利好", "confidence": 0.9, "plain_summary": "越界的"},
        {"index": "x", "direction": "利好", "confidence": 0.9, "plain_summary": "非数字的"},
        {"index": 1, "direction": "中性", "confidence": 0.9, "plain_summary": "正常的"},
    ]}, ensure_ascii=False)])
    result = nu.analyze_events([make_item()])
    assert len(result["items"]) == 1
    assert "正常的" in result["items"][0]["plain_summary"]


# ==================== analyze_one ====================

def test_analyze_one_deep_returns_risks_and_opportunities(monkeypatch):
    patch_llm(monkeypatch, [json.dumps({
        "index": 1, "event_type": "回购", "direction": "利好", "confidence": 0.8,
        "impact_level": "high", "plain_summary": "拟回购 10 亿元",
        "risks": ["回购价格上限可能限制实际成交"], "opportunities": ["注销式回购可抬升每股收益"],
    }, ensure_ascii=False)])
    result = nu.analyze_one(make_item(), mode="deep")
    assert result["risks"] == ["回购价格上限可能限制实际成交"]
    assert result["opportunities"] == ["注销式回购可抬升每股收益"]
    assert result["analyzed"] is True


def test_analyze_one_uses_the_deep_prompt(monkeypatch):
    calls = patch_llm(monkeypatch, [json.dumps({"direction": "中性", "confidence": 0.9,
                                                "plain_summary": "例行公告"},
                                               ensure_ascii=False)])
    nu.analyze_one(make_item(), mode="deep")
    system_prompt = calls[0]["messages"][0]["content"]
    assert "解读员" in system_prompt          # news_deep_dive.md 的角色设定


def test_analyze_one_degrades_on_garbage(monkeypatch):
    patch_llm(monkeypatch, ["这不是 JSON", "这也不是 JSON"])
    result = nu.analyze_one(make_item())
    assert result["direction"] is None
    assert result["analyzed"] is False
    assert result["risks"] == [] and result["opportunities"] == []


def test_analyze_one_llm_unavailable(monkeypatch):
    calls = patch_llm(monkeypatch, ["不该被调用"], available=False)
    result = nu.analyze_one(make_item())
    assert calls == []
    assert result["direction"] is None


# ==================== score（确定性加权）====================

def test_score_is_deterministic_and_signed():
    assert nu.score("利好", 1.0, "high", 1) == 1.0
    assert nu.score("利空", 1.0, "high", 1) == -1.0
    assert nu.score("中性", 1.0, "high", 1) == 0.0
    assert nu.score(None, 1.0, "high", 1) == 0.0


def test_score_ranks_report_above_media():
    """研报的级别数字(3)比媒体(2)大，但信任度更高 —— 别写成 1/level。"""
    report = nu.score("利好", 1.0, "high", 3)
    media = nu.score("利好", 1.0, "high", 2)
    assert report > media
    assert nu.score("利好", 1.0, "high", 1) > report      # 公告最高
    assert nu.score("利好", 1.0, "high", 4) < media       # 舆情最低


def test_score_scales_with_impact_and_confidence():
    assert nu.score("利好", 1.0, "high", 1) > nu.score("利好", 1.0, "medium", 1) \
        > nu.score("利好", 1.0, "low", 1)
    assert nu.score("利好", 0.5, "high", 1) < nu.score("利好", 0.9, "high", 1)


# ==================== 综合解读（"读完这些新闻，这只票现在怎么看"）====================
#
# 用户的原话："你要去阅读实时的新闻去更新你的想法，而不是一个新闻一个想法，
# 这样是没有任何的意义的"。逐条方向标签回答不了"那合起来呢"，所以必须有这一层。

SYNTHESIS_JSON = json.dumps({
    "read": "最近这批信息里，唯一有实质内容的是公司对被执行传闻的澄清，公告口径谨慎；"
            "研报集中在八月中报点评，观点以买入为主但都提到短期业绩承压。"
            "合起来看，信息面偏中性，没有改变中期逻辑的新变量。",
    "highlights": ["公司澄清158万元被执行系第三方纠纷", "8 月中报点评以买入为主"],
    "skepticism": "澄清来自媒体转述，尚未见于公告",
}, ensure_ascii=False)


def _analyzed_item(**overrides):
    item = make_item()
    item.update({"plain_summary": "公司澄清被执行系第三方纠纷（仅供参考，不构成投资建议）",
                 "direction": "中性", "event_type": "其他", "impact_level": "low"})
    item.update(overrides)
    return item


def test_synthesize_read_happy_path(monkeypatch):
    calls = patch_llm(monkeypatch, [SYNTHESIS_JSON])
    result = nu.synthesize_read([_analyzed_item()], symbol="SH600519", name="贵州茅台",
                               quote={"price": "1257.12", "changePercent": "-0.78"})
    assert result["ok"] is True
    assert "信息面" in result["read"]
    assert len(result["highlights"]) == 2
    assert result["notice"] == nu.AI_NOTICE
    # 只喂**结论字段**，不重贴标题与正文：模型已经逐条读过一次，
    # 再塞原文会诱导它退化成"逐条复述"—— 而那正是用户明确反对的形态
    user_prompt = calls[0]["messages"][1]["content"]
    assert "方向=" in user_prompt and "解读：" in user_prompt
    assert "最近 1 条" in user_prompt


def test_synthesize_read_prompt_forbids_advice(monkeypatch):
    calls = patch_llm(monkeypatch, [SYNTHESIS_JSON])
    nu.synthesize_read([_analyzed_item()], symbol="SH600519")
    system_prompt = calls[0]["messages"][0]["content"]
    assert "绝不" in system_prompt
    assert "综合" in system_prompt


def test_synthesize_read_sanitizes_output(monkeypatch):
    """合规后置过滤同样要作用在综合解读上（它是最容易被写成"建议"的那一段）。"""
    bad = json.dumps({"read": "综合来看建议买入，必涨。", "highlights": [], "skepticism": ""},
                     ensure_ascii=False)
    patch_llm(monkeypatch, [bad])
    result = nu.synthesize_read([_analyzed_item()], symbol="SH600519")
    assert "建议买入" not in result["read"]
    assert "必涨" not in result["read"]
    assert nu.DISCLAIMER in result["read"]


def test_synthesize_read_empty_items_skips_the_model(monkeypatch):
    """没有已解读的条目就不该花一次调用 —— 空态文案由前端负责。"""
    calls = patch_llm(monkeypatch, [SYNTHESIS_JSON])
    result = nu.synthesize_read([], symbol="SH600519")
    assert calls == []
    assert result["ok"] is False and result["read"] == ""


def test_synthesize_read_llm_unavailable(monkeypatch):
    calls = patch_llm(monkeypatch, [SYNTHESIS_JSON], available=False)
    result = nu.synthesize_read([_analyzed_item()], symbol="SH600519")
    assert calls == []
    assert result["ok"] is False and result["read"] == ""


def test_synthesize_read_bad_json_degrades_to_hidden(monkeypatch):
    """输出无法解析时返回 ok=False + 空串，让调用方**隐藏整块**，而不是显示一段空话。"""
    patch_llm(monkeypatch, ["这不是 JSON", "这也不是 JSON"])
    result = nu.synthesize_read([_analyzed_item()], symbol="SH600519")
    assert result["ok"] is False
    assert result["read"] == ""


def test_synthesize_endpoint_envelope(monkeypatch):
    monkeypatch.setattr(nu, "synthesize_read", lambda *a, **k: {
        "read": "信息面偏中性。", "highlights": [], "skepticism": "",
        "notice": nu.AI_NOTICE, "ok": True})
    r = TestClient(main.app).post("/api/v1/news/synthesize",
                                  json={"symbol": "600519", "items": [make_item()]},
                                  headers=_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["read"] == "信息面偏中性。"


def test_synthesize_endpoint_rejects_oversized_input(monkeypatch):
    monkeypatch.setattr(nu, "synthesize_read",
                        lambda *a, **k: pytest.fail("超限时不该调用模型"))
    items = [make_item() for _ in range(main.NEWS_SYNTHESIS_MAX_ITEMS + 1)]
    r = TestClient(main.app).post("/api/v1/news/synthesize",
                                  json={"symbol": "600519", "items": items}, headers=_HEADERS)
    assert r.status_code == 400
    assert "最多综合" in r.json()["error"]


# ==================== 端点契约 POST /api/v1/news/analyze ====================

from fastapi.testclient import TestClient  # noqa: E402

import app as main  # noqa: E402

_HEADERS = {"X-Internal-Token": main.INTERNAL_API_TOKEN} if main.INTERNAL_API_TOKEN else {}


def test_analyze_endpoint_batch_envelope(monkeypatch):
    seen = {}

    def fake(items):
        seen["items"] = items
        return {"items": [make_item()], "analyzed": 1, "skipped": 0, "errors": []}

    monkeypatch.setattr(nu, "analyze_events", fake)
    r = TestClient(main.app).post("/api/v1/news/analyze",
                                  json={"items": [make_item()], "mode": "batch"},
                                  headers=_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(seen["items"]) == 1
    # 合规标识（spec §10）：任何 AI 输出都必须带免责说明
    assert body["data"]["notice"] == nu.AI_NOTICE


def test_analyze_endpoint_defaults_to_batch(monkeypatch):
    called = []
    monkeypatch.setattr(nu, "analyze_events", lambda items: called.append("batch") or
                        {"items": items, "analyzed": 0, "skipped": 0, "errors": []})
    monkeypatch.setattr(nu, "analyze_one", lambda *a, **k: called.append("deep") or {})
    r = TestClient(main.app).post("/api/v1/news/analyze", json={"items": [make_item()]},
                                  headers=_HEADERS)
    assert r.status_code == 200
    assert called == ["batch"]


def test_analyze_endpoint_deep_runs_per_item(monkeypatch):
    seen = []

    def fake_one(item, mode="deep"):
        seen.append((item["url"], mode))
        return dict(item, direction=None, confidence=0.0, impact_level="low",
                    plain_summary="信息不足，不判断方向", risks=[], opportunities=[],
                    analyzed=False)

    monkeypatch.setattr(nu, "analyze_one", fake_one)
    items = [make_item(url="https://x.com/1"), make_item(url="https://x.com/2")]
    r = TestClient(main.app).post("/api/v1/news/analyze",
                                  json={"items": items, "mode": "deep"}, headers=_HEADERS)
    assert r.status_code == 200
    assert [url for url, _ in seen] == ["https://x.com/1", "https://x.com/2"]
    assert all(mode == "deep" for _, mode in seen)
    assert r.json()["data"]["analyzed"] == 0


@pytest.mark.parametrize("payload,reason", [
    ({}, "缺少 items"),
    ({"items": []}, "空数组"),
    ({"items": "不是数组"}, "items 不是数组"),
    ({"items": ["字符串"]}, "items 元素不是对象"),
    ({"items": [{}], "mode": "unknown"}, "mode 非法"),
])
def test_analyze_endpoint_rejects_bad_params(monkeypatch, payload, reason):
    monkeypatch.setattr(nu, "analyze_events",
                        lambda items: pytest.fail(f"{reason} 时不该调用分析"))
    monkeypatch.setattr(nu, "analyze_one",
                        lambda *a, **k: pytest.fail(f"{reason} 时不该调用分析"))
    r = TestClient(main.app).post("/api/v1/news/analyze", json=payload, headers=_HEADERS)
    assert r.status_code == 400, reason
    assert r.json()["ok"] is False


def test_analyze_endpoint_caps_deep_mode(monkeypatch):
    """深读是一条一次调用，不能一个请求打出上百次模型调用。"""
    monkeypatch.setattr(nu, "analyze_one", lambda *a, **k: pytest.fail("超限时不该调用"))
    items = [make_item(url=f"https://x.com/{i}") for i in range(main.NEWS_ANALYZE_MAX_DEEP + 1)]
    r = TestClient(main.app).post("/api/v1/news/analyze",
                                  json={"items": items, "mode": "deep"}, headers=_HEADERS)
    assert r.status_code == 400
    assert "最多" in r.json()["error"]


def test_analyze_endpoint_fails_soft(monkeypatch):
    def boom(items):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(nu, "analyze_events", boom)
    r = TestClient(main.app).post("/api/v1/news/analyze", json={"items": [make_item()]},
                                  headers=_HEADERS)
    assert r.status_code == 502
    assert "暂时不可用" in r.json()["error"]


def test_analyze_endpoint_requires_internal_token(monkeypatch):
    monkeypatch.setattr(nu, "analyze_events", lambda items: pytest.fail("未鉴权不该进入业务逻辑"))
    r = TestClient(main.app).post("/api/v1/news/analyze", json={"items": [make_item()]})
    assert r.status_code == 401
