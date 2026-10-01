"""news_credibility.py —— 资讯**可信度**评估（确定性规则引擎，不调模型）。

为什么单独一个模块、而不是塞进 news_understanding：
可信度是"这条信息本身可不可信"，与"这条信息是利好还是利空"（news_understanding
的职责）是两个正交的问题。利空但可信、利好但存疑，对用户的动作含义完全不同，
所以字段分开算、分开存、分开展示。

<h2>设计原则（与 news_understanding.score 同一条纪律）</h2>

> **模型只出分类字段，算术由代码负责。**

可信度不打给 LLM 去问——"你觉得自己说的可信吗"得到的答案没有意义。
这里全部是可复现、可单测、可解释的规则：

| 维度 | 权重 | 判什么 |
|---|---|---|
| 信源 (source) | 45% | 渠道级别（公告>研报>持牌媒体>聚合/舆情）+ 已知可靠信源加成 |
| 内容 (content) | 35% | 传闻措辞、标题党用词、可验证细节（数字/日期/比例） |
| 交叉印证 (corroboration) | 20% | 同一标的同一天，是否有另一家**不同**信源讲同一件事 |

输出 0-100 的整数分 + 分档（高/较高/中/较低/低）+ **人话理由清单**——
"为什么可信/为什么存疑"必须与分数一起出现，否则用户只能盲信一个数字，
而盲信数字正是这个模块要消灭的行为。

<h2>边界（诚实声明）</h2>
- 这套规则**判不了事实层面的真伪**（"公告里说的数字是假的"这种事，
  只有对照工商/裁判文书/交易所问询函才能查）。它判的是
  **传播链路可信度**：谁说的、怎么说的、有没有别人也说。
- 交叉印证用的是"标题相似度 + 同标的 + 同日"，是**必要不充分**条件：
  转载（同一篇通稿发多家）会被误判成"多家印证"。source_name 不同但
  实为转载的情况，留给后续用正文相似度收紧；当前先在 reason 里如实写
  "另有信源讲述相似事件"，不声称"独立确认"。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ==================== 信源分级 ====================
#
# source_level 沿用 news_client 的口径（1公告 2媒体 3研报 4舆情）。
# 注意排序与"可信"不是一回事：研报(3)有立场但数据扎实，聚合转载(2)真假掺半，
# 所以这里的分值是"渠道先验"，再由已知信源名单修正。

_SOURCE_BASE = {1: 95, 2: 65, 3: 70, 4: 40}
_SOURCE_BASE_DEFAULT = 50  # 未知级别：不给好处也不一棍子打死

# 持牌/主流财经媒体与官方渠道：在这些渠道出现的条目加成。
# 名单宁缺毋滥——加错一家营销号，整个分数的公信力就没了。
_TRUSTED_SOURCES = frozenset({
    "上交所", "深交所", "巨潮资讯", "上海证券报", "中国证券报", "证券时报",
    "证券日报", "财新", "财新网", "21世纪经济报道", "第一财经", "每日经济新闻",
    "界面新闻", "经济观察报", "中国基金报", "新京报", "澎湃新闻", "澎湃",
})
_TRUSTED_BOOST = 8   # 已知可靠信源加成（封顶不越过 100）
_AGGREGATOR_HINT = frozenset({"号", "自媒体", "看点", "头条号"})  # 名单里带这些词 → 疑似聚合号

# ==================== 内容信号 ====================
#
# 全部是"措辞先验"：命中不等于造假，但命中越多，越需要用户带着怀疑看。
# 词表刻意短——规则引擎的可信度来自"每条规则都能讲出道理"，堆几百个词
# 就变成没人审得动的黑名单了。

# 传闻措辞：把"说法"包装成"事实"的典型用词
_RUMOR_PATTERNS = (
    "据传", "传闻", "网传", "据悉", "知情人士", "消息人士", "或将", "或拟",
    "有望", "传 ", "传出", "被曝", "疑",
)
# 标题党/情绪化用词：煽动性越强，信息密度通常越低
_SENSATIONAL_PATTERNS = (
    "暴涨", "暴跌", "炸裂", "惊天", "速看", "必看", "紧急通知", "重大利好",
    "重大利空", "秒板", "天地板", "疯了", "彻夜难眠", "最后机会",
)
# 可验证细节：具体数字/比例/金额是"可以被抓出来对质"的内容，
# 敢写具体数字的信源通常更谨慎（编数字的风险比形容词高）
_SPECIFIC_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?\s*%|\d+(?:\.\d+)?\s*(?:亿|万|千万)元?|"
    r"20\d{2}\s*[年-]\s*\d{1,2}\s*[月-]\s*\d{1,2}日?)")

_RUMOR_PENALTY = 12          # 每个命中的传闻词
_SENSATIONAL_PENALTY = 8     # 每个命中的标题党词（惩罚轻于传闻：夸大≠捏造）
_SPECIFIC_BOOST = 10         # 存在可验证细节（只加一次）

# ==================== 交叉印证 ====================
#
# 同一标的 + 同一天 + 标题相似（去停用词后 Jaccard ≥ 阈值）→ 记一次印证。
# "不同信源"以 source_name 区分；source_level 也不同则视为更强的独立信号。
_TITLE_SIM_THRESHOLD = 0.45
_CORROBORATED_SCORE = 80
_UNCORROBORATED_SCORE = 50   # 单一信源不是减分项（公告本来就只发一家），只是不加成
_STOPWORDS = frozenset("的了在是和与及为对该其将从被中公告关于发布".split())

# ==================== 总分 ====================

_WEIGHTS = {"source": 0.45, "content": 0.35, "corroboration": 0.20}

_GRADE_BANDS = (
    (80, "高"), (65, "较高"), (50, "中"), (35, "较低"),
)


def _clamp(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, value))


def _title_tokens(title: str) -> set[str]:
    """标题分词：中文没有天然分隔，按 2-gram + 去停用词近似。

    Jaccard 只用于"这两条是不是在讲同一件事"的粗筛，不需要真分词器——
    精度差一点顶多多算/少算一次印证，分数本身还有另外两个维度兜着。
    """
    text = re.sub(r"[\s\W]+", "", str(title or ""))
    grams = {text[i:i + 2] for i in range(len(text) - 1)}
    return {g for g in grams if g not in _STOPWORDS and not g.isdigit()}


def _title_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _score_source(item: dict) -> tuple[int, list[str]]:
    level = item.get("source_level")
    name = str(item.get("source_name") or "").strip()
    score = _SOURCE_BASE.get(level, _SOURCE_BASE_DEFAULT)
    reasons: list[str] = []

    labels = {1: "官方公告渠道", 2: "媒体渠道", 3: "研报渠道", 4: "舆情/聚合渠道"}
    if level in labels:
        reasons.append(f"{labels[level]}")

    if name:
        if any(name.endswith(h) or h in name for h in _TRUSTED_SOURCES):
            score = _clamp(score + _TRUSTED_BOOST)
            reasons.append(f"已知主流信源（{name}）")
        elif any(hint in name for hint in _AGGREGATOR_HINT):
            score = _clamp(score - 10)
            reasons.append(f"疑似聚合/自媒体渠道（{name}）")
    else:
        reasons.append("信源名称缺失，按未知渠道计")
    return score, reasons


def _score_content(item: dict) -> tuple[int, list[str], bool, bool]:
    title = str(item.get("title") or "")
    body = str(item.get("content") or item.get("body") or "")
    text = f"{title} {title} {body}"  # 标题权重×2：标题党词汇几乎都长在标题上

    score = 70  # 内容维度的基准分：没有信号 = 不奖不罚的中性
    reasons: list[str] = []

    rumor_hits = sorted({p for p in _RUMOR_PATTERNS if p in text})
    sensational_hits = sorted({p for p in _SENSATIONAL_PATTERNS if p in text})

    if rumor_hits:
        score -= _RUMOR_PENALTY * len(rumor_hits)
        shown = "、".join(rumor_hits[:3])
        reasons.append(f"含传闻类措辞（{shown}），请以官方公告为准")
    if sensational_hits:
        score -= _SENSATIONAL_PENALTY * len(sensational_hits)
        shown = "、".join(sensational_hits[:3])
        reasons.append(f"标题情绪化用词（{shown}）")

    if _SPECIFIC_PATTERN.search(text):
        score += _SPECIFIC_BOOST
        reasons.append("含具体数字/日期，可交叉核验")

    return _clamp(score), reasons, bool(rumor_hits), bool(sensational_hits)


def _parse_time(value) -> datetime | None:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _find_corroboration(item: dict, peers: list[dict]) -> tuple[bool, list[str]]:
    """在同批条目里找"同标的 + 同日 + 标题相似"的其他信源条目。"""
    symbol = str(item.get("symbol") or "").strip().upper()
    moment = _parse_time(item.get("published_at"))
    name = str(item.get("source_name") or "").strip()
    tokens = _title_tokens(item.get("title"))

    if not symbol or moment is None or not tokens:
        return False, []

    for peer in peers:
        if peer is item:
            continue
        if str(peer.get("symbol") or "").strip().upper() != symbol:
            continue
        peer_name = str(peer.get("source_name") or "").strip()
        if name and peer_name and peer_name == name:
            continue  # 同一家信源重复发布不算印证（多为转载自己的通稿）
        peer_time = _parse_time(peer.get("published_at"))
        if peer_time is None or abs((peer_time - moment).days) > 1:
            continue
        if _title_similarity(tokens, _title_tokens(peer.get("title"))) >= _TITLE_SIM_THRESHOLD:
            label = peer_name or "另一信源"
            return True, [f"另有信源（{label}）讲述相似事件"]
    return False, []


def assess_items(items: list[dict], now: datetime | None = None,
                 peers: list[dict] | None = None) -> list[dict]:
    """给一批条目逐条计算可信度。返回与输入**同序同长**的评估结果列表。

    交叉印证默认在**批内**比较：调用方（analyze_events / Java 的补解读链路）
    一次给的是"某标的最近 N 天"的一批条目，正是印证该发生的地方。
    `peers` 允许单条调用（analyze_one）把同标的的其它条目递进来；
    不给则 corroboration 维度按"未检出"计——宁可少一次加成，不瞎猜印证。
    """
    now = now or datetime.now()
    pool = peers if peers is not None else (items or [])
    results: list[dict] = []
    for item in items or []:
        try:
            source_score, source_reasons = _score_source(item)
            content_score, content_reasons, rumor, sensational = _score_content(item)
            corroborated, corrob_reasons = _find_corroboration(item, pool)

            corrob_score = _CORROBORATED_SCORE if corroborated else _UNCORROBORATED_SCORE
            total = round(
                _WEIGHTS["source"] * source_score
                + _WEIGHTS["content"] * content_score
                + _WEIGHTS["corroboration"] * corrob_score)

            grade = "低"
            for floor, label in _GRADE_BANDS:
                if total >= floor:
                    grade = label
                    break

            reasons = source_reasons + content_reasons + corrob_reasons
            if rumor and item.get("source_level") not in (None, 1):
                # 公告里的"拟/或"是法律措辞不是传闻，只有非公告渠道的传闻词才值得顶格警示
                reasons.insert(0, "⚠️ 传闻特征明显：未经证实，请等待官方口径")

            results.append({
                "credibility": _clamp(total),
                "credibility_grade": grade,
                "credibility_components": {
                    "source": source_score,
                    "content": content_score,
                    "corroboration": corrob_score,
                },
                "credibility_reasons": reasons[:6],
                "rumor_flag": rumor and item.get("source_level") not in (None, 1),
                "sensational_flag": sensational,
                "corroborated": corroborated,
            })
        except Exception as exc:  # noqa: BLE001 - 可信度是增强件，绝不拖垮解读主链路
            logger.warning("可信度评估失败（按缺失计）: %s", exc)
            results.append({
                "credibility": None,
                "credibility_grade": None,
                "credibility_components": None,
                "credibility_reasons": [],
                "rumor_flag": False,
                "sensational_flag": False,
                "corroborated": False,
            })
    return results


def attach(items: list[dict], now: datetime | None = None,
           peers: list[dict] | None = None) -> list[dict]:
    """便捷入口：把评估结果合并进条目本身（不改顺序、不丢字段）。"""
    assessed = assess_items(items, now=now, peers=peers)
    return [dict(item, **result) for item, result in zip(items, assessed)]
