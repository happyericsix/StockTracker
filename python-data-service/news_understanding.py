# -*- coding: utf-8 -*-
"""N2 分析层：把一条资讯抽成结构化事件字段。

契约是 spec §6 的 schema，**字段名一字不改**：
`event_type / direction / confidence / impact_level / related_symbols /
source_level / plain_summary / risks / opportunities`。

<h3>为什么是"单次结构化抽取"而不是让模型自由推理</h3>
plan §0.5 定了这条：判断利好/利空是**分类**任务，不是推理任务。让模型多步自由发挥
只会更容易幻觉 —— 而 `direction` 恰恰是本模块最不能错的字段（spec §13 把它列为
🔴 最大风险：误判方向比"没有这个功能"更糟，因为它会误导用户决策）。
所以这里只做一次固定 schema 的抽取，并在 prompt 与代码两侧同时约束。

<h3>三级降级：任何一环失败都要有可读的结论</h3>
LLM 未配置 → 输出不合法 JSON → 调用超时，三种情况都必须返回同一个降级结构
（`direction=null` + "信息不足，不判断方向"），并且**原文照常返回**。
"信息不足 ≠ 不入库，只是不判方向" —— 丢掉原文等于把"以后能分析"的机会一起丢了。

<h3>为什么用两个 prompt 文件</h3>
plan 只列了 `prompts/news_analyst.md`。这里拆成两个：批量**分类**（daily 量大、
每条只出方向/强度/一句话）与单条**深读**（用户点开某一条才跑，要红绿风险机会）。
两者任务不同、输出预算差一个量级，塞进一个 prompt 会让批量调用为不需要的
`risks/opportunities` 付 token，也会让"分类"这个最不能错的任务被"写作"任务稀释。
"""

import json
import logging
import re

import llm_service
from akshare_client import normalize_symbol
from news_client import is_about_the_stock
from news_credibility import attach as attach_credibility

logger = logging.getLogger(__name__)

# ===== spec §6 的枚举（值一字不改，前端与落库都按它判等）=====
EVENT_TYPES = ("业绩预告", "重大合同", "回购", "增减持", "监管处罚", "重组",
               "宏观政策", "行业动态", "其他")
DIRECTIONS = ("利好", "利空", "中性")
DIRECTION_SIGN = {"利好": 1, "利空": -1, "中性": 0}
IMPACT_LEVELS = ("high", "medium", "low")
IMPACT_WEIGHT = {"high": 1.0, "medium": 0.6, "low": 0.3}
# 信源信任度（plan §0.5）：公告 > 研报 > 媒体 > 舆情。
# ⚠️ 它与 news_client 的 `source_level`（1公告 2媒体 3研报 4舆情）**不是同一个排序**，
#    研报的级别数字(3)比媒体(2)大，但信任度(0.8)比媒体(0.7)高。别写成 1/level。
SOURCE_TRUST = {1: 1.0, 2: 0.7, 3: 0.8, 4: 0.4}
# 置信度低于这个值就不给方向（宁可不判，也不要一个 0.2 置信度的"利好"被当成结论）
MIN_CONFIDENCE = 0.4

BATCH_SIZE = 5
MAX_ITEMS_PER_CALL = 50
BATCH_MAX_TOKENS = 2000
DEEP_MAX_TOKENS = 1200
TEMPERATURE = 0.1

DISCLAIMER = "（仅供参考，不构成投资建议）"
AI_NOTICE = "AI 生成内容，仅供参考，不构成投资建议"
_DEGRADED_SUMMARY = "信息不足，不判断方向"

# 影响预筛关键词（plan §0.6.3）：只有可能改变中期逻辑的事件才值得花一次 LLM 调用。
# ⚠️ 这一步**必须是代码规则**，绝不能改成"让 LLM 判断这条重不重要" ——
# 全市场公告 1000+ 条/日，那会把成本乘两个数量级
# （plan §0.6.3 算过：漏斗后 50–150 条 → 10–30 次调用/日）。
IMPACT_KEYWORDS = (
    # 股东与股权
    "减持", "增持", "回购", "质押", "解禁", "股权转让", "股权激励",
    # 合规与风险
    "退市", "停牌", "复牌", "处罚", "立案", "问询", "警示", "违规", "诉讼", "仲裁",
    # 被执行/冻结/失信：真实链路里发现的漏网项 —— "贵州茅台被执行158万元" 是明确的
    # 负面事件，但原关键词表里一个都不匹配，于是被预筛当成噪音跳过了。
    # 注意用"被执行"而不是"执行"：后者会命中"执行董事"，把大量人事公告误判成风险事件。
    "被执行", "冻结", "失信", "罚款", "监管函", "关注函", "警示函", "下修", "终止",
    "商誉", "计提", "停业",
    # 资本运作
    "重组", "并购", "收购", "合并", "分立", "资产出售", "定增", "可转债",
    # 经营与业绩
    "业绩预告", "业绩快报", "预增", "预减", "预亏", "扭亏", "亏损",
    "重大合同", "中标", "投产", "产能", "涨价", "降价", "分红", "送转", "评级",
)

# ===== 合规后置过滤（spec §10）=====
# prompt 里已经写了禁令，但**只靠 prompt 是不够的**：模型输出不可审计，
# 而这里每一条都对应"系统给出了买卖指令"这种能出事的结果。两道锁都要有。
# 策略是**改写**而不是删除 —— 直接删掉会让句子残缺、读起来像乱码。
_SANITIZE_RULES = (
    (r"建议(买入|加仓|建仓|抄底|增持)", "存在偏多观点"),
    (r"建议(卖出|减仓|清仓|止损离场|减持)", "存在偏空观点"),
    (r"建议(持有|观望)", "存在中性观点"),
    (r"推荐买入", "存在偏多观点"),
    (r"(可以|应该|应当|不妨|赶紧|马上)(买入|加仓|建仓)", "存在偏多观点"),
    (r"(可以|应该|应当|赶紧|马上)(卖出|减仓|清仓)", "存在偏空观点"),
    (r"(立即|马上|尽快)(清仓|止损|离场)", "存在偏空观点"),
    # ⚠️ 备选词按**长词在前**排：正则的 alternation 是"最左优先"，
    # 把 `稳赚` 写在 `稳赚不赔` 前面会先匹配掉前半截，留下"不赔"这种残句。
    (r"(稳赚不赔|保证收益|铁定涨|一定涨|必涨|必跌|稳赚|包赚)", "走势存在不确定性"),
    (r"满仓", "较重仓位"),
    (r"(翻倍|暴涨)在即", "走势存在不确定性"),
)
_SANITIZE_COMPILED = tuple((re.compile(pattern), repl) for pattern, repl in _SANITIZE_RULES)


# ==================== 通用工具 ====================


def extract_json(text: str):
    """从模型回复里容错地取出 JSON（对象或数组）。

    与 `agent/strategy_schema.extract_strategy_json` 保持同一套容错风格
    （先 ```json 围栏，再裸 JSON），但多支持一种形态：**顶层数组**。
    批量抽取时让模型输出数组是最自然的形状，只支持对象会逼着 prompt 多包一层。
    """
    raw = str(text or "")
    match = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", raw, re.S)
    candidates = [match.group(1)] if match else []
    candidates.append(raw.strip())
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(parsed, (dict, list)):
            return parsed
    return None


def _rewrite(text: str) -> str:
    """只做投顾句式改写，不追加免责短句（供 risks/opportunities 这类短列表用）。"""
    result = str(text or "")
    for pattern, replacement in _SANITIZE_COMPILED:
        result = pattern.sub(replacement, result)
    return " ".join(result.split())


def sanitize(text: str) -> str:
    """合规后置过滤：改写投顾句式 + 补免责短句。

    幂等：已带免责短句的文本不会重复追加（否则简报里会拖出一长串括号）。
    """
    result = _rewrite(text)
    if result and DISCLAIMER not in result:
        result = f"{result}{DISCLAIMER}"
    return result


def _degraded(item: dict | None = None) -> dict:
    """三级降级共用的兜底结论。

    ⚠️ 必须**字段齐全**，包括 `related_symbols`：第一版漏了它，于是降级条目比正常条目
    少一个键，前端按 spec §6 取字段时会拿到 undefined。这个错是 N2 真实链路脚本
    抓出来的（单测没覆盖，因为桩路径没走全）—— 契约类字段就该由"逐条检查字段是否齐全"
    的检查来守，而不是靠人记得。
    """
    symbol = (item or {}).get("symbol") or ""
    return {
        "event_type": "其他",
        "direction": None,
        "confidence": 0.0,
        "impact_level": "low",
        "related_symbols": [symbol] if symbol else [],
        # 降级文案也要过合规过滤：否则同一页面上"模型给的解读"带免责句、
        # "降级给的解读"不带，看起来像忘了加。
        "plain_summary": sanitize(_DEGRADED_SUMMARY),
        "risks": [],
        "opportunities": [],
    }


def _clamp_confidence(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _enum(value, allowed, default):
    text = str(value or "").strip()
    return text if text in allowed else default


def _string_list(value) -> list[str]:
    """风险/机会列表：只做句式改写，**不追加免责短句**。

    早期版本直接调 `sanitize`，结果每条风险后面都挂了一个"（仅供参考，不构成投资建议）"，
    红绿标注列表被免责句淹没。免责句是**整条解读**级别的东西，不是每个短句级别的。
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [_rewrite(str(v)) for v in value if str(v or "").strip()][:5]


def _normalize_related(symbol) -> str:
    text = str(symbol or "").strip().upper()
    if not text:
        return ""
    # 模型有时回裸代码（600519）、有时回带前缀（SH600519）。统一成后者，
    # 否则前端按代码查关联时会遇到"同一只票两种写法"。
    if text.isdigit() and len(text) == 6:
        return normalize_symbol(text).upper()
    return text


def normalize_analysis(raw, item: dict) -> dict:
    """把模型输出的一条分析规整成 spec §6 的形状。

    这一步是**防幻觉的最后一关**，不是格式化：
    - 枚举值不在表里 → 落回默认，且 `direction` 落 `None` 而不是"中性"——
      "模型说了个没见过的词"和"模型判了中性"是两件不同的事，混为一谈会让
      一个跑偏的输出看起来像一次成功的分类；
    - `confidence` 低于 `MIN_CONFIDENCE` → `direction` 置空并说明（spec §6 硬约束）；
    - 缺失的 `plain_summary` 用降级文案，而不是留空让前端显示空白。
    """
    data = raw if isinstance(raw, dict) else {}
    confidence = _clamp_confidence(data.get("confidence"))
    direction = _enum(data.get("direction"), DIRECTIONS, None)
    if direction not in DIRECTIONS:
        direction = None
    if direction is None and confidence >= MIN_CONFIDENCE:
        # <h3>为什么这里要把置信度压下来（真实链路发现的语义冲突）</h3>
        # spec §6 把两个概念分给了两个字段：`中性` = "确认无方向性影响"，
        # `direction=null` = "信息不足，不判断方向"。但模型会给出
        # `direction=null` + `confidence=0.9`（"我很确定这件事没有方向性影响"）——
        # 这是拿 null 表达了本该由 `中性` 表达的意思。
        #
        # 处理成"压低置信度"而不是"偷偷改成中性"：模型明确拒绝给方向时，
        # 代码不该替它发明一个。压下来之后 schema 自洽（null ⇒ 低置信），
        # 而模型的原话仍完整留在 plain_summary 里（"…属程序性披露…"），信息没丢。
        confidence = min(confidence, MIN_CONFIDENCE - 0.01)
    if confidence < MIN_CONFIDENCE:
        direction = None

    summary = str(data.get("plain_summary") or "").strip()
    if direction is None:
        if not summary:
            summary = _DEGRADED_SUMMARY
        elif confidence < MIN_CONFIDENCE and "不判断方向" not in summary:
            # 只在模型**没说过**这句话时才补。模型很容易自己就写"信息不足，不判断方向"，
            # 无条件追加会让同一句话在一行里出现两次（真实链路里看到的实际效果）。
            summary = f"{summary}（置信度不足，不判断方向）"

    related = [s for s in (_normalize_related(x) for x in (data.get("related_symbols") or [])) if s]
    if not related and item.get("symbol"):
        related = [item["symbol"]]

    return {
        "event_type": _enum(data.get("event_type"), EVENT_TYPES, "其他"),
        "direction": direction,
        "confidence": confidence,
        "impact_level": _enum(data.get("impact_level"), IMPACT_LEVELS, "low"),
        "related_symbols": related[:5],
        "plain_summary": sanitize(summary),
        "risks": _string_list(data.get("risks")),
        "opportunities": _string_list(data.get("opportunities")),
    }


def passes_prefilter(item: dict) -> bool:
    """影响预筛（漏斗 L1 代码规则 + L2 相关度）。

    - **公告(L1) / 研报(L3)** 一律放行：它们本身就是"有人正式表过态"的信号。
    - **媒体(L2)** 分两种：
      - 明确**不是**关于这只票的（`is_about_the_stock is False`）→ 直接跳过。
        实测依据：`stock_news_em` 是关键词匹配，10 条里混着大量"提及该股"的大盘综述
        （"深沪北百元股数量达217只"之类），它们既不该占 LLM 调用，也不该出现在个股页。
      - 明确**关于**这只票的 → 放行，**不再要求命中关键词表**。关键词表是为
        "全市场每日上千条公告"设计的量级闸门；单只票的媒体条数本身就只有 ≤10 条，
        再用关键词卡一道会漏掉真正material的新闻
        （实测："贵州茅台中报净利润同比下降1.95%" 不含任何关键词，但它对持有者当然重要）。
      - **无从判断**的（宏观/行业资讯，既没有 symbol 也没解析出名字）→ 仍走关键词表控量，
        否则 200 条全球快讯会全部涌入。
    """
    level = item.get("source_level")
    if level in (1, 3):
        return True
    relevant = is_about_the_stock(item)
    if relevant is False:
        return False
    if relevant is None:
        haystack = f"{item.get('event_type_raw', '')} {item.get('title', '')}"
        return any(keyword in haystack for keyword in IMPACT_KEYWORDS)
    return True


# ==================== LLM 调用 ====================


def _chat_json(system_prompt: str, user_prompt: str, max_tokens: int, attempts: int = 2):
    """调模型并解析 JSON；**解析失败重试一次**（照 `react_agent` 的既有约定）。

    重试时把上一轮的输出作为 assistant 消息发回去，并明确指出"这不是合法 JSON"。
    这是模型最容易自己纠正的一类错误（多了说明文字、漏了引号、包了代码围栏之外的
    客套话），一次重试的收益明显高于它的一次 token 成本。

    Returns: `(parsed_or_None, last_text)` —— 失败时把原始文本带出来，
    让调用方能把"模型到底说了什么"写进日志（否则只能看到"解析失败"三个字）。
    异常不在这里吞：调用方要按"整个批次降级"处理。
    """
    last_text = ""
    for attempt in range(max(1, attempts)):
        messages = [{"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}]
        if attempt:
            messages.append({"role": "assistant", "content": last_text[:500]})
            messages.append({"role": "user",
                             "content": "上面的输出不是合法 JSON。只输出 JSON，不要任何其他文字。"})
        choice = llm_service.chat_completion(messages, temperature=TEMPERATURE,
                                             max_tokens=max_tokens)
        message = (choice or {}).get("message") or {}
        last_text = str(message.get("content") or "")
        parsed = extract_json(last_text)
        if parsed is not None:
            return parsed, last_text
    return None, last_text


def _index_prompt(items: list[tuple[int, dict]]) -> str:
    """把待分析条目编成紧凑输入，带 1-based `index`。

    带 index 是为了**防乱序**：模型返回的 results 顺序不保证与输入一致，
    靠它对回去。用 index 而不是标题/URL 对回去，是因为标题可能重复（同一份公告
    被多家媒体转述），而 index 在这一次调用内唯一。
    """
    blocks = []
    for position, (_, item) in enumerate(items):
        blocks.append(
            f"[{position + 1}] 来源级别={item.get('source_level')} "
            f"来源={item.get('source_name') or '未知'} "
            f"源站分类={item.get('event_type_raw') or '无'} "
            f"时间={item.get('published_at') or '未知'}\n"
            f"标题：{item.get('title') or '（无标题）'}\n"
            f"内容：{item.get('content') or '（无正文）'}"
        )
    return "\n\n".join(blocks)


def analyze_events(items: list[dict]) -> dict:
    """批量抽取。内部每批 ≤ `BATCH_SIZE` 条，返回带分析字段的完整条目列表。

    Returns:
        `{"items": […], "analyzed": n, "skipped": m, "errors": […]}`

    `items` 里**每一条**都会带上 spec §6 的分析字段（降级的也在内），另有额外的
    `analyzed` 布尔值区分"模型看过"与"没轮到分析" —— 没有这个标记，下游无法解释
    "为什么 9 成条目都没有方向"（是模型说信息不足，还是压根没问模型）。

    顺序契约：输出与输入**同序同长**。调用方通常已按时间排好序，
    这里重排会让"最近一条"跑到列表中间。
    """
    result = {"items": [], "analyzed": 0, "skipped": 0, "errors": []}
    indexed = list(enumerate(items or []))
    if not indexed:
        return result

    # ⚠️ 全程按**下标**记账，不用 `list.index()` / `in` 定位条目：
    # 那两者对 dict 走的是 `==` 值比较，两条内容相同的资讯会被当成同一条，
    # 于是分析结果张冠李戴（而"看起来有一个方向结论"让这种错更难发现）。
    eligible = [(i, item) for i, item in indexed if passes_prefilter(item)]
    to_analyze = eligible[:MAX_ITEMS_PER_CALL]
    # 超额的与预筛未过的都要**出现在输出里**（只是没有分析字段），
    # 不能因为截断就整批消失 —— 那等于悄悄丢数据。
    unanalyzed = eligible[MAX_ITEMS_PER_CALL:] + [
        (i, item) for i, item in indexed if not passes_prefilter(item)
    ]
    result["skipped"] = len(unanalyzed)

    def degraded_entries(pairs):
        return [(i, dict(item, **_degraded(item), analyzed=False)) for i, item in pairs]

    if not to_analyze:
        result["items"] = [entry for _, entry in sorted(degraded_entries(indexed), key=lambda p: p[0])]
        return result

    if not llm_service._is_available():
        # 不抛异常、不静默：明说"模型没配"，让运维一眼看出是配置问题而非代码问题
        result["errors"].append("AI 服务未配置（DEEPSEEK_API_KEY 为空），本次仅保留原文")
        result["items"] = [entry for _, entry in sorted(degraded_entries(indexed), key=lambda p: p[0])]
        return result

    system_prompt = llm_service._load_prompt("news_analyst")
    by_index: dict[int, dict] = {}
    for start in range(0, len(to_analyze), BATCH_SIZE):
        batch = to_analyze[start:start + BATCH_SIZE]
        try:
            parsed, text = _chat_json(system_prompt, _index_prompt(batch), BATCH_MAX_TOKENS)
        except Exception as exc:  # noqa: BLE001 - 降级链的一环
            logger.warning("资讯批量抽取失败（%d 条降级）: %s", len(batch), exc)
            result["errors"].append("AI 分析暂时不可用，本次仅保留原文")
            continue
        rows = parsed.get("results") if isinstance(parsed, dict) else parsed
        if not isinstance(rows, list):
            logger.warning("资讯批量抽取输出不是 JSON 数组: %.120s", text)
            result["errors"].append("AI 分析输出无法解析，本次仅保留原文")
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                position = int(row.get("index", 0)) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= position < len(batch):
                by_index[batch[position][0]] = row

    entries: dict[int, dict] = {}
    for original_index, item in to_analyze:
        raw = by_index.get(original_index)
        if raw is None:
            entries[original_index] = dict(item, **_degraded(item), analyzed=False)
        else:
            entries[original_index] = dict(item, **normalize_analysis(raw, item), analyzed=True)
            result["analyzed"] += 1
    for original_index, entry in degraded_entries(unanalyzed):
        entries[original_index] = entry

    # 可信度（信源/内容/交叉印证）对**全部**条目生效，包括降级的：
    # "模型没配"不该连带把"这条是传闻"的警示也藏起来。批内做交叉印证。
    result["items"] = attach_credibility(
        [entries[i] for i in sorted(entries)])
    result["errors"] = list(dict.fromkeys(result["errors"]))  # 多批失败时不要刷屏
    return result


def analyze_one(item: dict, mode: str = "deep", peers: list[dict] | None = None) -> dict:
    """单条事件解读。`mode="deep"` 额外产出 `risks` / `opportunities`（红绿标注）。

    用户点开某一条才跑，所以这里给足输出预算。失败照样返回降级结构
    —— 页面上"信息不足"远好过一个空白面板或 500。

    `peers`：同标的的**其它**条目。给了就在批内做交叉印证（可信度更准）；
    不给则 corroboration 维度按"未检出"计——宁可少一次加成，不瞎猜印证。
    """
    base = dict(item or {})
    if not llm_service._is_available():
        return attach_credibility(
            [dict(base, **_degraded(base), analyzed=False)], peers=peers)[0]

    prompt_name = "news_deep_dive" if mode == "deep" else "news_analyst"
    try:
        parsed, text = _chat_json(llm_service._load_prompt(prompt_name),
                                  _index_prompt([(0, base)]), DEEP_MAX_TOKENS)
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else None
        elif isinstance(parsed, dict) and isinstance(parsed.get("results"), list):
            parsed = parsed["results"][0] if parsed["results"] else None
        if not isinstance(parsed, dict):
            logger.warning("单条解读输出无法解析: %.120s", text)
            return dict(base, **_degraded(base), analyzed=False)
    except Exception as exc:  # noqa: BLE001 - 降级链的一环
        logger.warning("单条解读失败: %s", exc)
        return dict(base, **_degraded(base), analyzed=False)

    return attach_credibility(
        [dict(base, **normalize_analysis(parsed, base), analyzed=True)], peers=peers)[0]


def score(direction, confidence, impact_level, source_level) -> float:
    """确定性加权分（plan §0.5）——**不是让 LLM 打分**。

    可复现、可解释、可单测：模型只出三个分类字段，算术由代码负责。
    这样 `direction` 判错时能一眼定位是分类问题，而权重不合理只改常量、不动 prompt。
    """
    sign = DIRECTION_SIGN.get(direction or "", 0)
    trust = SOURCE_TRUST.get(source_level, 0.5)
    return sign * _clamp_confidence(confidence) * IMPACT_WEIGHT.get(impact_level, 0.3) * trust


# ==================== 综合解读（"读完这些新闻，这只票现在怎么看"）====================

SYNTHESIS_MAX_TOKENS = 1200
SYNTHESIS_MAX_EVENTS = 25


def _synthesis_prompt(items: list[dict], symbol: str, name: str, quote: dict | None) -> str:
    """把已结构化的事件编成"给解读员看"的输入。

    只喂**结论字段**（方向/强度/一句话），不重贴标题与正文：模型已经逐条读过一次，
    再把原文塞回去既费 token，又会诱导它退化成"逐条复述"——
    而这正是用户明确反对的形态（"一个新闻一个想法"）。
    """
    lines = [f"标的：{name or symbol}（{symbol or '未知'}）"]
    if quote:
        price = quote.get("price") or "未知"
        change = quote.get("changePercent")
        change_text = f"{change}%" if change not in (None, "") else "未知"
        lines.append(f"最新价：{price}　当日涨跌：{change_text}")
    lines.append(f"以下是最近 {len(items)} 条已结构化的事件（按时间倒序）：\n")
    for index, item in enumerate(items, 1):
        level = {1: "公告", 2: "媒体", 3: "研报", 4: "舆情"}.get(item.get("source_level"), "资讯")
        direction = item.get("direction") or "未判方向"
        summary = (item.get("plain_summary") or "").strip() or "（无解读）"
        lines.append(
            f"[{index}] {str(item.get('published_at') or '')[:10]}　{level}　"
            f"类型={item.get('event_type') or '其他'}　方向={direction}　"
            f"强度={item.get('impact_level') or 'low'}\n"
            f"    标题：{item.get('title') or ''}\n"
            f"    解读：{summary}"
        )
    lines.append("\n请按系统提示输出 JSON。")
    return "\n".join(lines)


def synthesize_read(items: list[dict], symbol: str = "", name: str = "",
                    quote: dict | None = None) -> dict:
    """把一批事件读成一段连贯判断（**一次 LLM 调用**）。

    与 `analyze_events` 的分工：那个是**逐条分类**（每条一个方向标签），
    这个是**综合**——用户真正想要的是后者："读完这些新闻，这只票现在到底是什么情况"。
    逐条标签回答不了这个问题，因为它把综合的责任推给了用户。

    Returns:
        `{"read": str, "highlights": [str], "skepticism": str, "notice": str, "ok": bool}`
        失败时 `ok=False` 且 `read=""` —— 调用方据此**隐藏整块**，
        而不是显示一段空话（宁可不显示，也不要用废话占住页面最显眼的位置）。
    """
    result = {"read": "", "highlights": [], "skepticism": "",
              "notice": AI_NOTICE, "ok": False}
    selected = [item for item in (items or [])][:SYNTHESIS_MAX_EVENTS]
    if not selected:
        # 没有事件本身就是结论（前端会显示空态文案），不必花一次调用
        return result
    if not llm_service._is_available():
        logger.info("综合解读跳过：AI 服务未配置")
        return result

    try:
        parsed, text = _chat_json(llm_service._load_prompt("news_synthesis"),
                                  _synthesis_prompt(selected, symbol, name, quote),
                                  SYNTHESIS_MAX_TOKENS)
    except Exception as exc:  # noqa: BLE001 - 失败要退化成"不显示"，不是 500
        logger.warning("综合解读失败: %s", exc)
        return result
    if not isinstance(parsed, dict):
        logger.warning("综合解读输出无法解析: %.120s", text)
        return result

    read = str(parsed.get("read") or "").strip()
    if not read:
        return result
    result["read"] = sanitize(read)
    result["highlights"] = _string_list(parsed.get("highlights"))
    result["skepticism"] = sanitize(str(parsed.get("skepticism") or "").strip())
    result["ok"] = True
    return result
