# -*- coding: utf-8 -*-
"""N1 真实链路验证脚本（**手工跑，不是 pytest 用例**）。

为什么单独一个脚本：N1 的两个致命故障只有真实网络才暴露 ——
东财改列名（字段映射静默返回 None）和接口限流/改版（返回残缺结构）。
单测用桩覆盖了字段映射的逻辑，但桩永远不知道"今天列名还叫不叫新闻标题"。

放在包根目录而不是 tests/：`pytest.ini` 的 `testpaths = tests` 保证它不会被
自动收集去真联网（这是既有 6 个手工脚本踩过的坑，见 pytest.ini 注释）。

跑法（控制台是 GBK，必须带 PYTHONIOENCODING，否则中文列名乱码）：
    cd python-data-service
    $env:PYTHONIOENCODING="utf-8"
    .\.venv\Scripts\python.exe test_news_client_live.py
"""

# 从 scripts/ 运行时把包根目录放进 sys.path（这些脚本 import 顶层模块如 ths_client）
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys
from datetime import date

import news_client

CHECKS = []


def check(label, ok, detail=""):
    CHECKS.append((label, bool(ok), detail))
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")


def main() -> int:
    today = date.today().strftime("%Y%m%d")
    print(f"=== N1 真实链路验证  day={today} ===\n")

    print("[L1 公告]")
    notices_all = news_client.fetch_announcements(today)
    check("全市场当日公告 > 0", len(notices_all) > 0, f"{len(notices_all)} 条")
    check("未超过 NOTICE_MAX_PER_DAY 上限",
          len(notices_all) <= news_client.NOTICE_MAX_PER_DAY,
          f"上限 {news_client.NOTICE_MAX_PER_DAY}")
    if notices_all:
        sample = notices_all[0]
        check("字段非空（symbol/title/url/type/published_at）",
              all(sample.get(k) for k in ("symbol", "title", "url", "event_type_raw", "published_at")),
              f"示例: {sample['symbol']} {sample['title'][:28]}… [{sample['event_type_raw']}]")

    notices_one = news_client.fetch_announcements(today, symbol="600519")
    print(f"  （600519 当日公告 {len(notices_one)} 条 — 周六可能本来就没有，不作为判据）")

    # 按代码过滤的**有效**验证：不能拿 600519 断言，因为它当天可能是 0 条，
    # 而 `all(... for i in [])` 恒为 True —— 那种"通过"等于没测。
    # 改成：从全市场结果里挑一只当天真有公告的股票，再用它验过滤。
    if notices_all:
        probe_symbol = notices_all[0]["symbol"]           # 形如 SH600519
        expected = sum(1 for i in notices_all if i["symbol"] == probe_symbol)
        filtered = news_client.fetch_announcements(today, symbol=probe_symbol)
        check("按代码过滤命中数与全市场切分一致且非空",
              len(filtered) == expected and len(filtered) > 0
              and all(i["symbol"] == probe_symbol for i in filtered),
              f"{probe_symbol}: 过滤 {len(filtered)} 条 / 全市场切分 {expected} 条")

    print("\n[公告正文（按需补抓）]")
    # 东财公告只有标题没有正文，而 N2 拿不到正文时对公告只能回"信息不足，不判断方向"。
    # 正文走实测可用的正文 JSON 接口；这里用当天真实存在的公告验一遍。
    with_code = next((i for i in notices_all if news_client.extract_art_code(i["url"])), None)
    if with_code is None:
        check("当天存在带 art_code 的公告", False, "没找到（无法验证正文链路）")
    else:
        art_code = news_client.extract_art_code(with_code["url"])
        body = news_client.fetch_announcement_body(art_code)
        check("正文抓到且非空", len(body) > 50, f"art_code={art_code} 正文 {len(body)} 字")
        check("正文已去 HTML 标签",
              not any(tag in body for tag in ("<p>", "</", "&nbsp;", "var ")),
              body[:60])
        check("正文已按上限剪裁",
              len(body) <= news_client.ANNOUNCEMENT_BODY_CHARS + 1,
              f"上限 {news_client.ANNOUNCEMENT_BODY_CHARS}")
        enriched = news_client.enrich_announcement_bodies([with_code])
        check("补正文后 content 不再等于 title",
              enriched["fetched"] == 1 and enriched["items"][0]["content"] != with_code["title"],
              f"fetched={enriched['fetched']}")
        again = news_client.enrich_announcement_bodies(enriched["items"])
        check("已有正文的条目不重复抓（省掉一次请求）",
              again["fetched"] == 0, f"fetched={again['fetched']}")

    print("\n[L2 个股新闻]")
    news = news_client.fetch_stock_news("600519")
    # 实测：东财个股新闻页本身就只有 10 条（plan §0.2）。这条断言是"防呆"——
    # 哪天它变成 50 条，说明接口改了，N3 的"库优先 + 实时兜底"策略要重新评估。
    check("返回条数 == 10（实测上限）", len(news) == 10, f"{len(news)} 条")
    if news:
        check("source_name 非空（文章来源）", all(i["source_name"] for i in news),
              f"示例来源: {news[0]['source_name']}")

    print("\n[L2 宏观快讯]")
    market = news_client.fetch_market_news()
    check("快讯 > 0 且有标题与时间",
          len(market) > 0 and all(i["title"] and i["published_at"] for i in market),
          f"{len(market)} 条")
    caixin = news_client.fetch_caixin_news()
    check("财新无标题无时间（已知缺陷，不可进时间轴）",
          len(caixin) > 0 and all(not i["title"] and not i["published_at"] for i in caixin),
          f"{len(caixin)} 条")

    print("\n[L3 研报]")
    reports = news_client.fetch_research_reports("600519", days=30)
    check("30 天窗口内研报 > 0", len(reports) > 0, f"{len(reports)} 条")
    if reports:
        check("机构/评级已拼进 content",
              all(r["source_name"] for r in reports) and "｜" in reports[0]["content"],
              f"示例: {reports[0]['source_name']} [{reports[0]['event_type_raw']}]")

    print("\n[聚合入口 search_news]")
    result = news_client.search_news(symbol="600519", days=30)
    print(f"  counts={result['counts']}  total={len(result['items'])}  errors={result['errors']}")
    check("三个源都进了聚合", sum(result["counts"][k] for k in ("notice", "news", "report")) > 0)
    check("计数之和 == 列表长度（前端会并排显示这两个数）",
          sum(result["counts"].values()) == len(result["items"]),
          f"{sum(result['counts'].values())} vs {len(result['items'])}")
    times = [i["published_at"] for i in result["items"] if i["published_at"]]
    check("时间倒序", times == sorted(times, reverse=True))
    check("去重后无重复 URL",
          len({news_client.normalize_url(i["url"]) for i in result["items"] if i["url"]})
          == len([i for i in result["items"] if i["url"]]))

    print("\n  前 5 条（人工看一眼有没有「看似正常其实全是旧闻」）:")
    for item in result["items"][:5]:
        print(f"    L{item['source_level']} {item['published_at'] or '(无时间)'} "
              f"[{item['source_name']}] {item['title'][:40]}")

    print("\n[边界]")
    check("港股/美股不发个股新闻请求且报出原因",
          news_client.search_news(symbol="00700", days=7)["errors"] != [])
    check("舆情请求报「暂无数据源」",
          any("舆情" in e for e in news_client.search_news(symbol="600519", types=[4])["errors"]))

    failed = [label for label, ok, _ in CHECKS if not ok]
    print(f"\n=== {len(CHECKS) - len(failed)}/{len(CHECKS)} 通过 ===")
    if failed:
        print("失败项:")
        for label in failed:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
