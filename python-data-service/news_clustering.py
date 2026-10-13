# -*- coding: utf-8 -*-
"""N4 事件聚类层：把"同一件事的多条报道"归成事件线（专题）。

<h3>这一层只做归组，不做判断 —— 与 N1 同一条纪律</h3>
"这两条在讲同一件事"是**代码可判定**的（标题相似度 + 时间窗 + 标的锚点），
全部确定性、可单测、可复现。让 LLM 进这一层会让"聚类错了"和"模型看错了"
变成同一个症状，而且每天几百条的聚类成本会乘上去。
语义层面的"同主题不同事件"归并（比如"美联储降息"与"人民币汇率走高"），
交给总编 LLM 一次调用去做（见 news_topics.py），不在这层。

<h3>与 N1 跨源归并（merge_cross_source）的分工</h3>
N1 归并处理的是**逐字转载**（相似度 ≥0.55，归并后另一条消失）；
本层处理的是**同一事件的不同报道**（相似度门槛更低，成员全部保留 ——
聚类是"组织信息"，不是"删信息"）。同一事件先被 N1 归并成一条（带
also_reported_by），后续报道标题变化更大，由本层把它们串成事件线。

<h3>配对规则（全部确定性）</h3>
- 同标的对：symbol 相同且非空 → 时间窗 36h 内、相似度（含锚点加成）≥ 0.45；
- 跨标的对（含宏观）：时间窗 72h 内、相似度 ≥ 0.50；
- 公司名锚点加成 +0.15：同一 symbol，或一方的 name 出现在对方标题里 ——
  "公告（带 symbol）+ 媒体报道（不带 symbol）"是事件线最常见的形态，
  公告标题与报道标题的 2-gram 相似度天然偏低，锚点就是为它们准备的。

<h3>顺序与幂等</h3>
聚类是纯函数：同一输入永远同一输出。Java 侧按成员 event id 与已有专题
做增量匹配（见 NewsTopicService），不依赖这里的任何状态。
"""

import logging
from datetime import datetime, timedelta

from text_similarity import title_similarity, title_tokens

logger = logging.getLogger(__name__)

# ===== 配对参数（聚类是组织信息不删信息，门槛低于 N1 归并的 0.55）=====
CLUSTER_THRESHOLD = 0.45          # 同标的对的"相似度 + 锚点加成"门槛
CLUSTER_THRESHOLD_CROSS = 0.50    # 无共同标的对（含宏观）的门槛，严一档
NAME_ANCHOR_BONUS = 0.15          # 公司名锚点加成
SAME_SYMBOL_WINDOW = timedelta(hours=36)   # 同标的：公告→解读→研报一般当天到次日
CROSS_WINDOW = timedelta(hours=72)         # 跨标的/宏观：后续报道可拖两三天
MIN_CLUSTER_SIZE = 2              # 单条不成"线"，留在 unclustered

# 单次聚类的输入上限：7 天窗口的库内事件（Java 侧截断），这里是最后防线
MAX_INPUT_ITEMS = 500


def _moment(item: dict) -> datetime | None:
    text = str(item.get("published_at") or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _related(a: dict, b: dict, tokens_a: set, tokens_b: set,
             moment_a: datetime, moment_b: datetime) -> bool:
    """两条资讯是否在讲同一件事（确定性判据，见模块 docstring）。"""
    sim = title_similarity(tokens_a, tokens_b)
    if sim <= 0:
        return False
    name_a = str(a.get("name") or "").strip()
    name_b = str(b.get("name") or "").strip()
    symbol_a = str(a.get("symbol") or "").strip().upper()
    symbol_b = str(b.get("symbol") or "").strip().upper()
    same_symbol = bool(symbol_a) and symbol_a == symbol_b
    anchored = same_symbol or (name_a and name_a in str(b.get("title") or "")) \
        or (name_b and name_b in str(a.get("title") or ""))
    score = sim + (NAME_ANCHOR_BONUS if anchored else 0.0)
    gap = abs(moment_a - moment_b)
    if same_symbol:
        return gap <= SAME_SYMBOL_WINDOW and score >= CLUSTER_THRESHOLD
    return gap <= CROSS_WINDOW and score >= CLUSTER_THRESHOLD_CROSS


def _representative(members: list[dict]) -> dict:
    """事件线的代表条目（决定聚类的标题）：责任主体优先，其次稳定性（有 URL）、时间早。"""
    def key(item: dict):
        url = str(item.get("url") or "").strip()
        return (item.get("source_level") or 2, 0 if url else 1,
                str(item.get("published_at") or ""))
    return sorted(members, key=key)[0]


def _stamp(item: dict) -> str:
    return str(item.get("published_at") or "")


def cluster_events(items: list[dict]) -> dict:
    """把一批资讯归成事件线。纯函数，失败不抛（脏输入只影响自己那条）。

    Returns:
        `{"clusters": [...], "unclustered_count": n}`；每个 cluster：
        `{title, members(原样透传), member_count, first_at, last_at,
        sources, source_count, symbols, directions}`
    """
    usable: list[tuple[int, dict, set, datetime]] = []
    dropped = 0
    for position, item in enumerate((items or [])[:MAX_INPUT_ITEMS]):
        title = str(item.get("title") or "").strip()
        moment = _moment(item)
        tokens = title_tokens(title)
        # 无时间 → 无法验证"相近时间"；无 token → 无从判相似。都不参与配对
        #（条目本身不丢，留在 unclustered）。
        if not title or moment is None or not tokens:
            dropped += 1
            continue
        usable.append((position, item, tokens, moment))

    parent = list(range(len(usable)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            _, a, tokens_a, moment_a = usable[i]
            _, b, tokens_b, moment_b = usable[j]
            if _related(a, b, tokens_a, tokens_b, moment_a, moment_b):
                parent[find(i)] = find(j)

    groups: dict[int, list[dict]] = {}
    for index, (_, item, _, _) in enumerate(usable):
        groups.setdefault(find(index), []).append(item)

    clusters, unclustered = [], 0
    for members in groups.values():
        if len(members) < MIN_CLUSTER_SIZE:
            unclustered += len(members)
            continue
        # 成员按时间倒序（前端展示口径），first/last 取时间戳字符串极值
        members = sorted(members, key=_stamp, reverse=True)
        sources = sorted({str(m.get("source_name") or "").strip()
                          for m in members if str(m.get("source_name") or "").strip()})
        symbols = sorted({str(m.get("symbol") or "").strip().upper()
                          for m in members if str(m.get("symbol") or "").strip()})
        directions = [d for d in ("利好", "利空", "中性")
                      if any(m.get("direction") == d for m in members)]
        clusters.append({
            "title": str(_representative(members).get("title") or ""),
            "members": members,
            "member_count": len(members),
            "first_at": min(_stamp(m) for m in members if _stamp(m)) if any(_stamp(m) for m in members) else "",
            "last_at": max(_stamp(m) for m in members if _stamp(m)) if any(_stamp(m) for m in members) else "",
            "sources": sources,
            "source_count": len(sources),
            "symbols": symbols,
            "directions": directions,
        })
    # 事件线按最新活动时间倒序（最热的线排前面）
    clusters.sort(key=lambda c: c["last_at"], reverse=True)
    if dropped:
        logger.info("聚类输入里 %d 条缺时间/缺标题/纯数字标题，未参与配对", dropped)
    return {"clusters": clusters, "unclustered_count": unclustered + dropped}
