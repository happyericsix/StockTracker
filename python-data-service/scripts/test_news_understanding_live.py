# -*- coding: utf-8 -*-
"""N2 真实链路验证 + **人工质量抽查**（手工跑，不是 pytest 用例）。

<h3>为什么这一步不能省</h3>
N2 的单测全部用桩，因此它们只能证明"代码按契约处理了模型的输出"，
**不能证明"模型输出的方向是对的"**。而 `direction` 误判是本模块最大的风险
（spec §13 🔴：误判方向比"没有这个功能"更糟，因为它会误导决策）。
所以必须真的跑一遍真实公告，并把结果打出来让人看。

跑法（会产生**真实的模型调用费用**，默认 10 条 ≈ 2 批 + 2 次深读）：
    cd python-data-service
    $env:PYTHONIOENCODING="utf-8"
    .\.venv\Scripts\python.exe test_news_understanding_live.py            # 默认 10 条，公告只有标题
    .\.venv\Scripts\python.exe test_news_understanding_live.py 10 body   # 先补公告正文再分析

**为什么一定要跑一次带 `body` 的**：公告只有标题时 N2 对公告几乎判不出方向
（首次实测 6 条公告全部 direction=None）。补正文正是为了修这件事 ——
这个脚本的"方向覆盖率"就是那条修复的成败判据。
"""

# 从 scripts/ 运行时把包根目录放进 sys.path（这些脚本 import 顶层模块如 ths_client）
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys
from datetime import date

import news_client
import news_understanding as nu

CHECKS = []
SPEC_FIELDS = ("event_type", "direction", "confidence", "impact_level",
               "related_symbols", "source_level", "plain_summary",
               "risks", "opportunities")


def check(label, ok, detail=""):
    CHECKS.append((label, bool(ok), detail))
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")


def gather(limit: int, with_body: bool) -> list[dict]:
    """凑一批真实资讯：优先挑全市场公告里命中影响关键词的（漏斗 L1 的雏形）。

    ⚠️ **必须按公司轮转取样，不能直接取前 N 条**：全市场公告是按代码顺序返回的，
    直接切片会拿到同一家公司的连号公告（实测踩到过：6 条全来自"国风新材"的同一个
    重组流程，于是"方向覆盖率"统计出来是 1/6 —— 而真相是那 6 条本来就都是
    程序性披露，不是模型判不出来）。采样偏差会让这条指标完全失去意义。
    """
    notices = news_client.fetch_announcements(date.today().strftime("%Y%m%d"))
    hits = [i for i in notices if nu.passes_prefilter(i)]

    by_symbol: dict[str, list[dict]] = {}
    for item in hits:
        by_symbol.setdefault(item["symbol"], []).append(item)
    picked: list[dict] = []
    # 每家先取一条，取满或取完为止（保证"公司维度"的多样性）
    for rank in range(100):
        if len(picked) >= max(1, limit - 4):
            break
        for symbol in list(by_symbol):
            bucket = by_symbol[symbol]
            if rank < len(bucket) and len(picked) < max(1, limit - 4):
                picked.append(bucket[rank])

    if with_body:
        picked = news_client.enrich_announcement_bodies(picked)["items"]
    items = list(picked)
    items.extend(news_client.fetch_stock_news("600519")[:2])
    items.extend(news_client.fetch_research_reports("600519", days=30)[:2])
    return news_client.dedup(items)[:limit]


def coverage(entries: list[dict]) -> None:
    """方向覆盖率：按信源级别分别统计"判出了方向"的比例。

    这是"补公告正文"这条修复唯一的成败判据 —— 结构性检查全绿也可能一条方向都判不出来。
    """
    print("\n[方向覆盖率]")
    for level, name in ((1, "公告"), (2, "媒体"), (3, "研报")):
        group = [e for e in entries if e.get("source_level") == level]
        if not group:
            continue
        judged = [e for e in group if e["direction"]]
        print(f"  L{level} {name}: {len(judged)}/{len(group)} 条判出方向 "
              f"({', '.join(sorted({e['direction'] for e in judged})) or '无'})")
    judged_all = [e for e in entries if e["direction"]]
    print(f"  合计: {len(judged_all)}/{len(entries)} 条")


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    with_body = "body" in [a.lower() for a in sys.argv[2:]]
    print(f"=== N2 真实链路验证  items={limit}  补公告正文={with_body}  "
          f"model={nu.llm_service.MODEL} ===\n")

    if not nu.llm_service._is_available():
        print("!! DEEPSEEK_API_KEY 未配置，无法做真实链路验证。")
        print("   （这本身是合法状态：N2 会降级成'信息不足，不判断方向'，但无法抽查质量。）")
        return 1

    items = gather(limit, with_body)
    print(f"取到 {len(items)} 条真实资讯，开始批量抽取...\n")
    result = nu.analyze_events(items)
    analyzed = result["items"]

    print(f"analyzed={result['analyzed']}  skipped={result['skipped']}  errors={result['errors']}\n")
    print("=" * 100)
    print("【人工抽查区】逐条看下面 6 列：方向判对了吗？一句话说清了吗？有没有编造？")
    print("=" * 100)
    for position, entry in enumerate(analyzed, 1):
        print(f"\n{position}. [{entry.get('source_name')}] {entry.get('title', '')[:60]}")
        print(f"   源站分类={entry.get('event_type_raw') or '无'}  "
              f"时间={entry.get('published_at') or '无'}")
        print(f"   → event_type={entry['event_type']}  direction={entry['direction']}  "
              f"confidence={entry['confidence']}  impact={entry['impact_level']}  "
              f"analyzed={entry.get('analyzed')}")
        print(f"   → {entry['plain_summary']}")
    print("\n" + "=" * 100)

    print("\n[清洗前的 schema 检查]")
    check("每条都带齐 spec §6 的字段",
          all(all(field in entry for field in SPEC_FIELDS) for entry in analyzed))
    check("direction 取值合法",
          all(entry["direction"] in (None, *nu.DIRECTIONS) for entry in analyzed))
    check("confidence 在 [0,1] 内",
          all(0.0 <= entry["confidence"] <= 1.0 for entry in analyzed))
    check("impact_level 取值合法",
          all(entry["impact_level"] in nu.IMPACT_LEVELS for entry in analyzed))
    check("event_type 取值合法",
          all(entry["event_type"] in nu.EVENT_TYPES for entry in analyzed))
    check("plain_summary 非空",
          all(entry["plain_summary"].strip() for entry in analyzed))

    print("\n[合规后置过滤]")
    banned = ("建议买入", "建议卖出", "可以加仓", "立即清仓", "必涨", "稳赚", "推荐买入")
    offenders = [(e["plain_summary"], b) for e in analyzed for b in banned if b in e["plain_summary"]]
    check("没有任何投顾句式漏出", not offenders, f"命中 {offenders}" if offenders else "")
    check("每条解读都带免责短句",
          all(nu.DISCLAIMER in entry["plain_summary"] for entry in analyzed))

    print("\n[降级链现状]")
    degraded = [e for e in analyzed if not e.get("analyzed")]
    print(f"  未分析 {len(degraded)} / {len(analyzed)} 条"
          f"（预筛跳过或降级；预筛跳过是设计内的成本控制，不是故障）")
    coverage(analyzed)
    # 不变量：spec §6 把 direction=null 定义为"信息不足"，所以 null 不能是高置信。
    # （模型有时会用 null 表达"确认无方向性影响"，那是 `中性` 该表达的语义，
    #   normalize_analysis 会把这种情况的置信度压下来。）
    check("direction=null 的条目 confidence 必须低（spec §6 语义）",
          all(e["confidence"] < nu.MIN_CONFIDENCE for e in analyzed if e["direction"] is None),
          str([e["confidence"] for e in analyzed if e["direction"] is None]))

    print("\n[单条深读]（取第一条高影响事件，验证 risks/opportunities）")
    target = next((e for e in analyzed if e["impact_level"] == "high"), analyzed[0] if analyzed else None)
    if target is None:
        check("有可深读的条目", False, "没有条目")
    else:
        deep = nu.analyze_one(target, mode="deep")
        print(f"  {deep.get('title', '')[:60]}")
        print(f"  plain_summary: {deep['plain_summary']}")
        print(f"  risks: {deep['risks']}")
        print(f"  opportunities: {deep['opportunities']}")
        check("深读产出字段齐全", all(field in deep for field in SPEC_FIELDS))
        check("深读的风险/机会不是投顾句式",
              not any(b in (deep["risks"] + deep["opportunities"]) for b in banned))

    failed = [label for label, ok, _ in CHECKS if not ok]
    print(f"\n=== {len(CHECKS) - len(failed)}/{len(CHECKS)} 通过 ===")
    if failed:
        print("失败项:")
        for label in failed:
            print(f"  - {label}")
        return 1
    print("注意：上面的检查是**结构性**的。方向判得对不对只能靠人看一眼【人工抽查区】。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
