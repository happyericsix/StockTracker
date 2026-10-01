# -*- coding: utf-8 -*-
"""新闻/资讯取数管道（N1）。

契约：`docs/superpowers/plans/2026-09-14-news-module.md` §N1；
事件字段名与 `docs/superpowers/specs/2026-09-05-news-module-design.md` §6 对齐。

<h3>这一层只做取数，不做任何判断</h3>
利好/利空是 N2（`news_understanding`）的事。这里唯一的"智能"是分级、去重、
排序与裁剪 —— 全部确定性、可单测、可复现。让 LLM 进这一层会让"字段映射错了"
和"模型读错了"变成同一个症状。

<h3>失败语义：单一信源挂掉绝不能让整次搜索失败</h3>
每个 `fetch_*` 只在 `logger.warning` 后返回 `[]`，从不抛异常（spec §13 风险表）。
理由不是"宽容"，而是这四条源是四个独立的外部服务：东财公告接口挂了不该导致
"连新闻也搜不到"。调用方通过 `search_news` 的 `errors` 字段知道哪条源没回来。
"""

import html
import logging
import re
from datetime import date, datetime, timedelta

# 资讯窗口按北京时间口径：published_at 是北京时间字符串，Docker 镜像默认 UTC 时
# date.today() 会把"今天的公告"取成昨天（北京时间 0-8 点之间）。统一走 trading_calendar.CN_TZ。
from trading_calendar import CN_TZ
from urllib.parse import urlsplit, urlunsplit

import akshare
import requests

# 复用既有实现，而不是各写一套 —— 两处 `_clip_text` 迟早会在空白折叠/省略号上分叉，
# 而"同一篇文章在新闻页和个股页显示长度不一样"属于没人会去查的那种 bug。
# `_clip_text` / `_clamp_int` 在 akshare_client 里是模块级私有名，这里按既有惯例跨模块复用。
from akshare_client import (
    NEWS_ITEM_CHARS,
    _clip_text,
    _stock_name,
    is_a_share,
    normalize_symbol,
    resolve_symbol,
)

logger = logging.getLogger(__name__)

# ===== 源级别（spec §4）=====
# ⚠️ 这是"责任主体"排序，**不是可信度权重排序**。plan §0.5 的 source_trust 是
#    公告 1.0 / 研报 0.8 / 媒体 0.7 / 舆情 0.4 —— 研报(3) 的权重高于媒体(2)，
#    而级别数字恰好相反。下游按 `source_level` 取权重时必须查那张表，不能用 `1/level`。
LEVEL_NOTICE = 1   # 法定披露（东财公告，同步自巨潮）
LEVEL_MEDIA = 2    # 财经媒体 / 全球快讯
LEVEL_REPORT = 3   # 券商研报
LEVEL_FORUM = 4    # 社区舆情（本阶段无数据源，spec Phase C）

SOURCES_WITH_DATA = (LEVEL_NOTICE, LEVEL_MEDIA, LEVEL_REPORT)

# ===== 数量上限（防止单次请求把全市场数据拖进来）=====
# 实测（2026-09-19，akshare 1.18.64）：当日全市场公告 1032 条、研报 771 条/股、
# 全球快讯 200 条。这些量级必须在这里截断，不能指望调用方自觉。
NOTICE_MAX_PER_DAY = 2000
MARKET_MAX = 200
REPORT_MAX = 50
# 单次搜索返回的条目上限。宏观查询（symbol 为空）会把当日全市场公告都收进来
# （实测 1032 条），不加这道闸就会把上千条 JSON 推过 HTTP —— 而 N1 的职责是
# "取最近的资讯"，历史与全量检索是 N3 落库之后的事（库才是档案）。
MAX_ITEMS = 300

# ===== 字段映射集中表（接口改版只改这一处）=====
# 列名全部为 2026-09-19 真实网络实测结果，不是照抄文档假设。
_MAP = {
    "notice": {
        "symbol": "代码", "name": "名称", "title": "公告标题",
        "type": "公告类型", "published": "公告日期", "url": "网址",
    },
    "news": {
        "title": "新闻标题", "content": "新闻内容", "published": "发布时间",
        "source": "文章来源", "url": "新闻链接",
    },
    "report": {
        "name": "股票简称", "title": "报告名称", "rating": "东财评级",
        "org": "机构", "industry": "行业", "published": "日期", "url": "报告PDF链接",
    },
    "market": {
        "title": "标题", "content": "摘要", "published": "发布时间", "url": "链接",
    },
    # 财新要闻（stock_news_main_cx）：实测只有 tag / summary / url，
    # **没有标题、没有发布时间** —— 所以它不能进时间轴（见 fetch_caixin_news）。
    "caixin": {"tag": "tag", "content": "summary", "url": "url"},
}

_NAN_LIKE = {"", "nan", "none", "null", "nat", "—", "-"}


# ==================== 基础工具 ====================


def normalize_url(url: str) -> str:
    """URL 归一化，作为去重主键（spec §7）。

    去掉 query / fragment / 尾斜杠并统一 scheme 与 host 大小写，因为同一篇文章
    在不同入口会带上不同的追踪参数：实测财新链接形如
    `https://database.caixin.com/2026-09-19/102486442.html?cxapp_link=true`，
    同一篇文章从 App 分享出来会带 `?cxapp_link=true`、从网页点开则不带 ——
    不归一化就会把同一篇存成两条，"越用库越全"变成"越用库越脏"。
    """
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        if not parts.scheme or not parts.netloc:
            # 不是标准绝对 URL（可能是脏数据），只做去空白 + 去尾斜杠
            return raw.rstrip("/").lower()
        netloc = parts.netloc.lower()
        path = parts.path.rstrip("/")
        return urlunsplit((parts.scheme.lower(), netloc, path, "", ""))
    except ValueError:
        return raw.lower()


def parse_datetime(value) -> str:
    """把各源五花八门的时间统一成 `"YYYY-MM-DD HH:MM:SS"`（落库前唯一形态）。

    实测要处理的形态（每条都真实出现过）：
      - 公告 `公告日期`：`"2026-09-19"`（**纯日期字符串，不是 date 类型**）
      - 新闻 `发布时间`：`"2026-09-19 10:07:26"`
      - 研报 `日期`：`"2026-08-21"`
      - DataFrame 缺失值：pandas 给的是 `float('nan')`，`str()` 出来是 `"nan"`

    纯日期一律补 `00:00:00`。⚠️ 这意味着**同一天的公告之间没有可靠的先后顺序**
    （东财公告只给到日）。排序时同级同日的条目顺序是不确定的，下游不要把它当因果序。
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).strftime("%Y-%m-%d %H:%M:%S")

    text = str(value).strip()
    if text.lower() in _NAN_LIKE:
        return ""
    # 统一分隔符后按"降精度"逐个试：秒 → 分 → 日。
    # 切片长度按格式本身给（19/16/10），不要用 len(fmt) 推算 ——
    # 格式串里的 `%` 占位符长度和输出长度不是一回事，那正是第一次写错的地方。
    cleaned = text.replace("/", "-")
    for fmt, size in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(cleaned[:size], fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return ""


def _row_get(row, column: str) -> str:
    """按列名取单元格的字符串值；列缺失或值为 NaN 时返回空串。"""
    if not column:
        return ""
    try:
        value = row.get(column)
    except (AttributeError, TypeError):
        return ""
    return str(value).strip() if value is not None else ""


def _event(*, symbol: str = "", name: str = "", title: str = "", content: str = "",
           url: str = "", source_level: int = LEVEL_MEDIA, source_name: str = "",
           event_type_raw: str = "", published_at: str = "") -> dict:
    """构造统一的内部事件结构（键名即 N2 与落库共用的契约）。"""
    return {
        "symbol": symbol,
        "name": name,
        "title": _clip_text(title, 120),
        "content": _clip_text(content, NEWS_ITEM_CHARS),
        "url": str(url or "").strip(),
        "source_level": source_level,
        "source_name": _clip_text(source_name, 40),
        "event_type_raw": _clip_text(event_type_raw, 40),
        "published_at": published_at,
    }


def _out_symbol(symbol: str) -> str:
    """输入任意写法 → 输出统一的大写带前缀形态 `SH600519`。

    复用 `normalize_symbol`（它产出腾讯格式小写 `sh600519`）再整体大写，
    与 Java 侧 `ThsSyncService.bareCode` 的前缀语义保持一致 ——
    两边各自实现一套"代码长什么样"是历史包袱的开始。
    """
    text = str(symbol or "").strip()
    if not text:
        return ""
    return normalize_symbol(text).upper()


def _bare_code(symbol: str) -> str:
    """取 6 位纯数字 A 股代码（东财的个股接口只认这个）；非 A 股返回空串。

    ⚠️ 必须自己剥前缀：`resolve_symbol` 对 `"sh600519"` 返回的是**带前缀的**
    `"SH600519"`（它只负责识别市场，不负责剥壳），而 `is_a_share` 只认 6 位纯数字。
    少了这一步，`"sh600519"` 会被判成"非 A 股" → 静默拿不到个股新闻。

    只剥 SH/SZ，**不剥 BJ**：东财个股新闻对北交所是否可用**未实测**，
    在拿到证据之前不要悄悄打开一条没验证过的路径（拿不到新闻会明确报出来，
    比"看起来查到了其实查错了"好）。裸 6 位代码（含北交所）行为不变，走 `is_a_share`。
    """
    resolved = str(resolve_symbol(str(symbol or "").strip()) or "").strip().upper()
    if resolved.startswith(("SH", "SZ")):
        resolved = resolved[2:]
    return resolved if is_a_share(resolved) else ""


def _frame_rows(df) -> list:
    """DataFrame → 逐行列表；空/异常返回 []。"""
    if df is None or getattr(df, "empty", True):
        return []
    try:
        return list(df.iterrows())
    except Exception as exc:  # noqa: BLE001
        logger.warning("资讯 DataFrame 遍历失败: %s", exc)
        return []


def _in_window(published_at: str, days: int) -> bool:
    """`published_at` 是否落在最近 `days` 天内。

    没有时间的条目**返回 True**：宁可多显示一条无时间的记录，也不要因为
    "源站没给时间"就把一条真实公告永久隐藏 —— 看不见的错误比看得见的多余更危险。
    """
    if not published_at:
        return True
    try:
        moment = datetime.strptime(published_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return moment >= datetime.now(CN_TZ).replace(tzinfo=None) - timedelta(days=max(0, int(days)))


def _sort_key(item: dict):
    """按时间倒序，**无时间的排最后**。

    直接 `sorted(reverse=True)` 会让空字符串排到最前面（空串小于一切），
    于是"时间未知"的条目会霸占列表首屏 —— 那正好是最不该被优先展示的一批。
    """
    published = item.get("published_at") or ""
    return (published != "", published)


# ==================== L1 公告 ====================


def fetch_announcements(day: str = "", symbol: str = "") -> list[dict]:
    """当日全市场公告（东方财富，同步自巨潮）。

    Args:
        day: `"YYYYMMDD"`，缺省今天。
        symbol: 可选，只留这一只（端点按单股查询时用，避免把上千条推过网络）。

    Returns:
        统一事件结构列表；失败返回 `[]`。
    """
    _day = str(day or "").strip() or datetime.now(CN_TZ).strftime("%Y%m%d")
    try:
        df = akshare.stock_notice_report(symbol="全部", date=_day)
    except Exception as exc:  # noqa: BLE001
        logger.warning("公告取数失败 day=%s: %s", _day, exc)
        return []

    want = _bare_code(symbol) if symbol else ""
    columns = _MAP["notice"]
    items = []
    for _, row in _frame_rows(df)[:NOTICE_MAX_PER_DAY]:
        code = _row_get(row, columns["symbol"])
        if want and code.strip() != want:
            continue
        items.append(_event(
            symbol=_out_symbol(code),
            name=_row_get(row, columns["name"]),
            title=_row_get(row, columns["title"]),
            # 公告没有正文，标题本身就是全部内容（东财公告页才有正文）。
            # 与其留空让 LLM 去猜，不如显式复用标题 —— N2 的 prompt 依赖它。
            content=_row_get(row, columns["title"]),
            url=_row_get(row, columns["url"]),
            source_level=LEVEL_NOTICE,
            source_name="东方财富·公告",
            event_type_raw=_row_get(row, columns["type"]),
            published_at=parse_datetime(_row_get(row, columns["published"])),
        ))
    logger.info("公告取数 day=%s 命中 %d 条（全市场 %s 条）", _day, len(items), len(df) if df is not None else 0)
    return items


# ==================== L2 媒体 ====================


def fetch_stock_news(symbol: str) -> list[dict]:
    """个股新闻（东方财富）。

    ⚠️ **最多 10 条，且只有这 10 条**（实测 2026-09-19，与 plan §0.2 一致）：
    东财个股新闻页本身就只有这么多。所以"某只股票的最近 30 天新闻"**不可能**
    靠这个接口现拉 —— 它必须靠每日增量落库累积（N3 的"库优先 + 实时兜底"
    因此是必需项而不是优化）。这个接口的真实角色是"给库里补最新几条"。
    """
    code = _bare_code(symbol)
    if not code:
        logger.info("个股新闻跳过（非 A 股代码，东财只认 6 位数字）: %s", symbol)
        return []
    try:
        df = akshare.stock_news_em(symbol=code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("个股新闻取数失败 symbol=%s: %s", code, exc)
        return []

    columns = _MAP["news"]
    # 带上股票名：它既是展示字段，也是**相关度判据**（见 is_about_the_stock）。
    # `_stock_name` 从应用启动时预热好的全 A 股名单里取，取不到返回空串、不拉网络。
    name = _stock_name(code)
    return [_event(
        symbol=_out_symbol(code),
        name=name,
        title=_row_get(row, columns["title"]),
        content=_row_get(row, columns["content"]),
        url=_row_get(row, columns["url"]),
        source_level=LEVEL_MEDIA,
        source_name=_row_get(row, columns["source"]) or "东方财富·个股新闻",
        published_at=parse_datetime(_row_get(row, columns["published"])),
    ) for _, row in _frame_rows(df)]


def is_about_the_stock(item: dict):
    """L2 相关度初筛：**「提及」不等于「关于」**。三态返回。

    <h3>为什么要这一步（实测依据）</h3>
    `stock_news_em` 是**关键词匹配**：问 600519 的新闻，返回的 10 条里混着大量只是
    "提到了这只票"的大盘综述。2026-09-19 真实链路实测，前 5 条里有 3 条是
    「深沪北百元股数量达217只」「主力动向：9月16日特大单净流入367.77亿元」这类 ——
    挂在这只票名下，但和它没有任何关系。这就是 spec §1.2 明确反对的资讯噪音。

    <h3>为什么是三态而不是布尔</h3>
    `True` = 明确关于这只票（标题/正文里出现了**股票名**，或标题里出现了代码）；
    `False` = **明确不是**（拿到了股票名，但正文里根本没出现过）；
    `None` = **无从判断**（既没有名字，标题里也没有代码 —— 例如宏观/行业资讯，
    或是股票名缓存还没预热）。

    把 `None` 和 `False` 合并成"不相关"是最容易犯的错：它会把"我查不到这只票叫什么"
    当成"这条新闻与它无关"，于是所有宏观与行业资讯被静默丢掉 ——
    而它们恰恰是"真正影响股价、但标题里不写代码"的那一半（见 `NewsService.timeline`
    合并 `news_stock_rel` 的同一理由）。

    <h3>为什么名字可靠、代码不可靠</h3>
    实测：`_stock_name` 能拿到名字时，规则很准；拿不到名字（名单未预热）时，
    **只看代码且只看标题**。若把正文也算进去，规则会失效 ——
    「深沪北百元股数量达217只」这类大盘综述的正文里就列着 600519
    （东财的关键词搜索正是因此命中它），但它与这只票毫无关系。
    """
    name = str(item.get("name") or "").strip()
    code = _bare_code(item.get("symbol") or "")
    if not name and not code:
        return None
    # ⚠️ **只看标题**，标题与正文都算的话规则会失效（这是实测出来的，不是保守起见）。
    # 反例：`stock_news_em` 返回的「深沪北百元股数量达217只」「百元股数量达209只」，
    # 它们的**正文**里就列着"贵州茅台"和 600519（百元股名单），于是"名字/代码出现在
    # 正文"这个判据会把它们全判成"关于茅台"。而它们与这只票毫无关系。
    #
    # 代价是漏掉"标题不提公司名、正文才提"的那类报道（实测「i茅台再调整规则…」）。
    # 这个取舍是**故意偏精确**：判错的代价是花一次模型调用去解读一篇大盘综述，
    # 而不判的代价只是那条显示为"未解读"——它仍然留在时间轴里，不会被删掉。
    title = str(item.get("title") or "")
    if name:
        return name in title
    if code:
        return True if code in title else None
    return None


def fetch_market_news() -> list[dict]:
    """全市场财经快讯（东方财富 `stock_info_global_em`）—— 宏观/大盘信号。

    <h3>为什么不是施工图写的财新 `stock_news_main_cx`（实测偏离，保留记录）</h3>
    plan §N1 写的是"财新要闻，宏观"。2026-09-19 实测两个接口：

    | 接口 | 条数 | 列 |
    |---|---|---|
    | `stock_news_main_cx`（财新） | 100 | `tag, summary, url` |
    | `stock_info_global_em`（东财快讯） | 200 | `标题, 摘要, 发布时间, 链接` |

    财新**没有标题、没有发布时间**。而本模块的展示形态是时间轴 + 行级标题，
    这两个字段缺一不可（没标题就只能把 200 字摘要当标题，没时间就排不进时间轴）。
    硬用它的结果是"宏观条目永远堆在列表末尾、且没有标题可扫"——
    看起来像 bug 的展示效果。所以：**宏观取数改走东财快讯**，
    财新保留在 `fetch_caixin_news()` 里，只服务"宏观段落生成"这类**不需要标题与
    时间轴位置**的用途（N5a 的行业/宏观段落）。
    """
    try:
        df = akshare.stock_info_global_em()
    except Exception as exc:  # noqa: BLE001
        logger.warning("全球快讯取数失败: %s", exc)
        return []

    columns = _MAP["market"]
    return [_event(
        title=_row_get(row, columns["title"]),
        content=_row_get(row, columns["content"]),
        url=_row_get(row, columns["url"]),
        source_level=LEVEL_MEDIA,
        source_name="东方财富·全球快讯",
        published_at=parse_datetime(_row_get(row, columns["published"])),
    ) for _, row in _frame_rows(df)][:MARKET_MAX]


def fetch_caixin_news() -> list[dict]:
    """财新要闻（宏观/政策），**只供生成段落用，不进时间轴**。

    返回条目带 `title=""` 与 `published_at=""`（源站就没给），
    调用方必须能接受这一点 —— 这也是它不进时间轴的原因（见 `fetch_market_news`）。
    """
    try:
        df = akshare.stock_news_main_cx()
    except Exception as exc:  # noqa: BLE001
        logger.warning("财新要闻取数失败: %s", exc)
        return []

    columns = _MAP["caixin"]
    return [_event(
        title="",  # 源站无标题，由 N2/简报侧按 tag 自行组织，不在这里编造
        content=_row_get(row, columns["content"]),
        url=_row_get(row, columns["url"]),
        source_level=LEVEL_MEDIA,
        source_name="财新",
        event_type_raw=_row_get(row, columns["tag"]),
        published_at="",
    ) for _, row in _frame_rows(df)]


# ==================== L3 研报 ====================


def fetch_research_reports(symbol: str, days: int = 30) -> list[dict]:
    """券商研报（东方财富），只留最近 `days` 天、最多 `REPORT_MAX` 条。

    实测 `stock_research_report_em("600519")` 返回 **771 行**且含三年盈利预测 ——
    全量落库既不划算也没意义（其中绝大多数是历史报告）。所以这里按 `days` 截窗口，
    并额外把 `行业`/`东财评级` 带出来：它们是 spec §0.6.5「行业环境」与
    「机构预期共识」唯一的现成数据源（`favorite_stocks` 没有行业列）。
    """
    code = _bare_code(symbol)
    if not code:
        logger.info("研报取数跳过（非 A 股代码）: %s", symbol)
        return []
    try:
        df = akshare.stock_research_report_em(symbol=code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("研报取数失败 symbol=%s: %s", code, exc)
        return []

    columns = _MAP["report"]
    items = []
    for _, row in _frame_rows(df):
        published = parse_datetime(_row_get(row, columns["published"]))
        if not _in_window(published, days):
            continue
        title = _row_get(row, columns["title"])
        rating = _row_get(row, columns["rating"])
        org = _row_get(row, columns["org"])
        industry = _row_get(row, columns["industry"])
        # 研报没有正文；把"机构 + 评级 + 行业"拼成内容，让 N2 单看这一条也能判方向。
        # 评级（买入/增持/中性/减持）是这套数据里唯一明确表达倾向的字段。
        detail = " / ".join(part for part in (org, rating, f"行业:{industry}" if industry else "") if part)
        items.append(_event(
            symbol=_out_symbol(code),
            name=_row_get(row, columns["name"]),
            title=title,
            content=f"{detail}｜{title}" if detail else title,
            url=_row_get(row, columns["url"]),
            source_level=LEVEL_REPORT,
            source_name=org or "东方财富·研报",
            # 源站自带的"分类"在这里就是评级；行业单独放在 content 里。
            event_type_raw=rating,
            published_at=published,
        ))
    items.sort(key=_sort_key, reverse=True)
    return items[:REPORT_MAX]


# ==================== 公告正文（按需补抓）====================
#
# <h3>为什么必须补这一层</h3>
# 东财公告接口只给 `代码/名称/公告标题/公告类型/公告日期/网址`，**没有正文**。
# 于是 N2 拿到公告时 content == title，模型只能回"信息不足，不判断方向" ——
# 真实链路 10 条抽查里 6 条公告全部如此，而公告恰恰是信任度最高的信源(1.0)。
# 结果是"最该被解读的一类资讯反而完全无法解读"。
#
# <h3>正文从哪来（2026-09-19 实测）</h3>
# | 来源 | 结果 |
# |---|---|
# | 详情页 HTML | 200，但去标签后主要是导航外壳，正文不干净 |
# | **正文 JSON 接口** `np-cnotice-stock/api/content/ann?art_code=…` | ✅ 200，`notice_content` 取到真实正文 |
# | 巨潮 `stock_zh_a_disclosure_report_cninfo` | ❌ JSONDecodeError，接口已坏 |
#
# 所以走第二条。`art_code` 不需要新字段：它就在我们已经落库的公告 URL 里
# （`…/notices/detail/000859/AN202609181829626803.html`）。
#
# <h3>为什么是"按需"而不是"每天全量"</h3>
# 每补一条正文 = 1~2 次 HTTP 请求。每天全市场 1000+ 条公告，全量补正文就是
# 每天上千次请求 —— 既慢又容易被限流，而其中 99% 与用户无关（plan §0.6.3 的漏斗逻辑）。
# 所以只在**用户真的要看某只股票**时才补，且只补最近的若干条；补过的正文落库后不再重复抓。

ANNOUNCEMENT_BODY_LIMIT = 10
# 正文剪裁长度：实测单页 `notice_content` 约 5000 字，多页公告更长。
# 2000 字足够覆盖"金额/条款/主体"这些判方向必需的信息（公告的关键要素几乎都在开头），
# 同时把 N2 的批量输入控制在 5 条 × 2000 字 ≈ 1 万字符（约 5–7k token），
# 这样 `BATCH_SIZE = 5` 仍然安全，不必为了长正文把批量降到 2–3。
ANNOUNCEMENT_BODY_CHARS = 2000
ANNOUNCEMENT_BODY_MAX_PAGES = 2
# 分页尺寸的实测观测值：page_index=1 返回的 `notice_content` 恰好 5000 字
# （2026-09-19 实测一份长问询函回复）。据此判断"本页没写满 ⇒ 这就是最后一页"，
# 省掉一次注定拿不到东西的请求 —— 短公告（董事会决议之类）是**多数**，
# 不省的话个股页补 10 条正文要发 20 次请求，其中 10 次纯浪费。
# 取 3000 而不是 5000：留足余量，宁可多要一次也不要因为分页尺寸变动而截断正文。
_PAGE_FULL_CHARS = 3000
_BODY_URL = "https://np-cnotice-stock.eastmoney.com/api/content/ann"
_BODY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    # 实测带上 Referer 才稳定返回 JSON（与详情页同源校验）
    "Referer": "https://data.eastmoney.com/",
}
_ART_CODE_RE = re.compile(r"/(AN\d+)\.html", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)


def extract_art_code(url: str) -> str:
    """从东财公告 URL 里取 `art_code`（形如 `AN202609181829626803`）。

    取不到就返回空串 —— 调用方据此跳过该条，而不是拿 URL 去猜。
    """
    match = _ART_CODE_RE.search(str(url or ""))
    return match.group(1).upper() if match else ""


def _strip_html(raw: str) -> str:
    """去掉 HTML 标签与脚本块，并把实体还原成字符。

    `notice_content` 是带标签的 HTML 片段（实测含 `<p>`/`<table>`），
    直接把带标签的字符串喂给模型会白白消耗 token，还会让它把标签当内容读。
    """
    text = _SCRIPT_RE.sub(" ", str(raw or ""))
    text = _TAG_RE.sub(" ", text)
    return " ".join(html.unescape(text).split())


def fetch_announcement_body(art_code: str, max_pages: int = ANNOUNCEMENT_BODY_MAX_PAGES) -> str:
    """按需抓一条公告的正文（去标签、剪裁）。失败返回 `""`，从不抛异常。

    分页：`notice_content` 每页约 5000 字，长公告要按 `page_index` 翻页
    （实测接口一次只给一页）。`max_pages` 封顶，避免一份超长年报把请求数拉爆。
    """
    code = str(art_code or "").strip()
    if not code:
        return ""
    chunks = []
    for page in range(1, max(1, int(max_pages)) + 1):
        try:
            response = requests.get(
                _BODY_URL,
                params={"art_code": code, "client_source": "web", "page_index": page},
                headers=_BODY_HEADERS, timeout=15,
            )
            response.raise_for_status()
            body = response.json()
        except Exception as exc:  # noqa: BLE001 - 失败不能让整次搜索失败
            logger.warning("公告正文抓取失败 art_code=%s page=%s: %s", code, page, exc)
            break
        data = body.get("data") if isinstance(body, dict) else None
        content = _strip_html((data or {}).get("notice_content") or "")
        if not content:
            break
        chunks.append(content)
        if len(" ".join(chunks)) >= ANNOUNCEMENT_BODY_CHARS:
            break
        if len(content) < _PAGE_FULL_CHARS:
            break          # 本页没写满 ⇒ 已经是最后一页，不必再翻

    text = " ".join(chunks)
    return text[:ANNOUNCEMENT_BODY_CHARS] + ("…" if len(text) > ANNOUNCEMENT_BODY_CHARS else "")


def enrich_announcement_bodies(items: list[dict], limit: int = ANNOUNCEMENT_BODY_LIMIT) -> dict:
    """给最近的若干条公告补正文。返回 `{"items": [...], "fetched": n, "errors": [...]}`。

    只补前 `limit` 条（传入前请先按时间倒序排好）。**补不到就保持原样**，
    不抛异常、不删除条目 —— 拿不到正文的公告仍然有标题和分类，仍有价值。
    已有正文（`content` 与 `title` 不同）的条目会被跳过；同一次调用里 `art_code`
    相同的条目也只抓一次，所以重复调用不会重复抓。
    """
    result = {"items": [], "fetched": 0, "errors": []}
    remaining = max(0, int(limit))
    failed = 0
    # 调用内也要按 art_code 去重：`content != title` 那条守卫只对**已经补过**的条目不重复抓，
    # 而同一条未补正文的公告在同一次调用里出现两次（去重前）会各抓一次 —— 白付一次请求。
    # 真实路径上 `search_news` 会先按 URL 去重，但这里不该依赖调用方的自觉。
    seen_codes: set[str] = set()
    for item in items or []:
        if item.get("source_level") != LEVEL_NOTICE or remaining <= 0:
            result["items"].append(item)
            continue
        if item.get("content") and item["content"] != item.get("title"):
            result["items"].append(item)          # 已经补过，别重复抓
            continue
        art_code = extract_art_code(item.get("url", ""))
        if not art_code or art_code in seen_codes:
            result["items"].append(item)
            continue
        seen_codes.add(art_code)
        body = fetch_announcement_body(art_code)
        remaining -= 1
        if not body:
            result["items"].append(item)
            failed += 1
            continue
        result["items"].append(dict(item, content=body, body_fetched=True))
        result["fetched"] += 1
    if failed:
        # 说清"有几条只有标题"，而不是让调用方以为公告本来就长这样
        result["errors"].append(f"有 {failed} 条公告正文抓取失败，仅保留标题")
    return result


# ==================== 个股公告历史（按股票拉）====================
#
# <h3>为什么必须新增这条路径（用户实测反馈："公告一个都没有"）</h3>
# `stock_notice_report` 只能查"全市场**某一天**"，所以库里只有"服务跑过的那几天"的公告。
# 600519 今天/昨天/前天都没有公告 → 它的**全部**历史公告一条都没入库，
# 而它近 90 天其实发了 8 条（半年报、董事会决议、权益分派…）。用户看到的就是"公告一个都没有"。
#
# 本接口一次给一只票的全部公告历史（实测 `total_hits=1074`），而且**直接带 `art_code`** ——
# 正文抓取因此不必再从 URL 里解析。URL 仍然按市场级路径的同一形状拼出来，
# 这样两条路径抓到的同一份公告会命中同一个去重键，不会重复入库。

SYMBOL_NOTICE_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
SYMBOL_NOTICE_PAGE_SIZE = 50
SYMBOL_NOTICE_MAX_PAGES = 3
# 一次给一只票回补多少条历史公告。用户在看这只票，所以给得比"当天增量"宽；
# 但也不能无限翻页（1074 条要 22 页），30 条足够覆盖个股页展示的时间轴。
SYMBOL_NOTICE_LIMIT = 30


def fetch_symbol_announcements(symbol: str, days: int = 90,
                               limit: int = SYMBOL_NOTICE_LIMIT) -> list[dict]:
    """按股票拉历史公告（东财个股公告列表接口，2026-09-19 实测可用）。

    失败返回 `[]` 并 `logger.warning`，从不抛异常（与其它取数函数同一约定）。
    """
    code = _bare_code(symbol)
    if not code:
        logger.info("个股公告跳过（非沪/深 A 股代码）: %s", symbol)
        return []

    name = _stock_name(code)
    cutoff = (datetime.now(CN_TZ).date() - timedelta(days=max(1, int(days)))).isoformat()
    items: list[dict] = []
    for page in range(1, max(1, SYMBOL_NOTICE_MAX_PAGES) + 1):
        if len(items) >= limit:
            break
        try:
            response = requests.get(
                SYMBOL_NOTICE_URL,
                params={"sr": -1, "page_size": SYMBOL_NOTICE_PAGE_SIZE, "page_index": page,
                        "ann_type": "A", "client_source": "web", "stock_list": code,
                        "f_node": 0, "s_node": 0},
                headers=_BODY_HEADERS, timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - 单一信源失败不阻断整体
            logger.warning("个股公告取数失败 symbol=%s page=%s: %s", code, page, exc)
            break

        rows = ((payload or {}).get("data") or {}).get("list") or []
        if not rows:
            break
        reached_cutoff = False
        for row in rows:
            notice_day = str(row.get("notice_date") or "")[:10]
            if not notice_day:
                continue
            if notice_day < cutoff:
                reached_cutoff = True
                break
            art_code = str(row.get("art_code") or "").strip()
            title = str(row.get("title") or "").strip()
            # 标题形如"贵州茅台:贵州茅台关于…"，重复的公司名去掉，读起来干净
            short = title.split(":", 1)[-1].strip() if ":" in title else title
            columns = row.get("columns") or []
            items.append(_event(
                symbol=_out_symbol(code),
                name=name or _short_name_from_row(row),
                title=short or title,
                content=short or title,          # 列表接口没有正文；正文由 enrich 按需补
                # 与市场级路径拼成**同一形状**的 URL，两条路径因此命中同一个去重键
                url=f"https://data.eastmoney.com/notices/detail/{code}/{art_code}.html"
                    if art_code else "",
                source_level=LEVEL_NOTICE,
                source_name="东方财富·公告",
                event_type_raw=(columns[0].get("column_name") if columns else "") or "",
                published_at=parse_datetime(row.get("notice_date")),
            ))
            if len(items) >= limit:
                break
        if reached_cutoff:
            break

    logger.info("个股公告取数 symbol=%s 命中 %d 条（近 %d 天）", code, len(items), days)
    return items


def _short_name_from_row(row: dict) -> str:
    codes = row.get("codes") or []
    return str((codes[0] or {}).get("short_name") or "") if codes else ""


# ==================== 去重与搜索 ====================


def dedup(items: list[dict]) -> list[dict]:
    """两级去重（spec §7）：① URL 归一化后相同；② 公告无 URL 时用三元组兜底。

    保留**先出现**的那条，所以调用方应先排序再去重 —— 否则"保留哪一条"是随机的。
    """
    seen_urls: set[str] = set()
    seen_triples: set[tuple] = set()
    result = []
    for item in items:
        key_url = normalize_url(item.get("url", ""))
        if key_url:
            if key_url in seen_urls:
                continue
            seen_urls.add(key_url)
        else:
            triple = (item.get("symbol", ""), item.get("title", ""), item.get("published_at", ""))
            if triple in seen_triples:
                continue
            seen_triples.add(triple)
        result.append(item)
    return result


def search_news(symbol: str = "", keyword: str = "", types: list[int] | None = None,
                days: int = 7, with_body: bool = False, only_relevant: bool = False) -> dict:
    """N1 的唯一对外入口：聚合 → 去重 → 排序 → 截窗口（可选补公告正文）。

    Args:
        with_body: 是否给最近的公告补正文。**默认 False**，因为补正文要按条发 HTTP 请求
            （见 `enrich_announcement_bodies`）。只有"用户正在看某只股票"这类场景
            （个股页事件区）才该打开它；搜索页打开它等于替用户付 10 次请求的等待。
        only_relevant: 是否丢掉"只是提及该股"的媒体条目（见 `is_about_the_stock`）。
            个股页开 `True` —— 那页上每条都该是"关于这只票"的，否则用户看到的是
            "显示了一堆却不解释"。搜索页保持 `False`（用户主动搜关键词时要看到全貌）。

    Returns:
        `{"items": [...], "counts": {...}, "errors": [...], "bodies_fetched": n}`

    <h3>宏观快讯只在"没有指定标的"时收进来</h3>
    问"600519 最近的新闻"时把 200 条全球快讯混进去，会让结果里 95% 与这只票无关 ——
    这正是 spec §1.2 明确反对的资讯噪音。所以只有 `symbol` 为空（用户在搜宏观/关键词）
    才带上快讯。
    """
    wanted = set(int(t) for t in types) if types else set(SOURCES_WITH_DATA)
    keyword_text = str(keyword or "").strip().lower()
    counts = {"notice": 0, "news": 0, "report": 0, "market": 0}
    errors: list[str] = []
    collected: list[dict] = []

    if LEVEL_FORUM in wanted:
        # 显式说出来，不要返回"空结果"让调用方以为"社区没人在讨论"。
        errors.append("社区舆情暂无数据源（spec Phase C 未接入）")

    if LEVEL_NOTICE in wanted:
        # 指定标的时走**个股公告历史**接口（否则库里只有"服务跑过的那几天"的公告 ——
        # 用户实测反馈"公告一个都没有"就是这么来的）；无标的时仍是当日全市场增量。
        #
        # 空不等于失败：当天可能真没公告。四个源各自的失败已在 fetch_* 内 warning 过，
        # 这里不重复断言，避免把"今天没消息"报成"服务坏了"。
        notices = (fetch_symbol_announcements(symbol, days=max(int(days), 30))
                   if symbol else fetch_announcements())
        collected.extend(notices)
        counts["notice"] = len(notices)

    if LEVEL_MEDIA in wanted:
        if symbol:
            if not _bare_code(symbol):
                errors.append("该标的暂无个股新闻源（东财个股新闻仅支持沪深 A 股）")
            news = fetch_stock_news(symbol)
            if only_relevant:
                # 「提及」不等于「关于」。大盘综述既不该占模型调用，**也不该出现在个股页上**：
                # 显示了却不解释，用户的直接反应就是"那 AI 设置的意义是什么"。
                kept = [item for item in news if is_about_the_stock(item) is not False]
                if len(kept) != len(news):
                    logger.info("个股新闻按相关度过滤掉 %d 条（仅提及该股）：%s",
                                len(news) - len(kept),
                                [item["title"][:22] for item in news if item not in kept])
                news = kept
            collected.extend(news)
            counts["news"] = len(news)
        else:
            market = fetch_market_news()
            collected.extend(market)
            counts["market"] = len(market)

    if LEVEL_REPORT in wanted and symbol:
        reports = fetch_research_reports(symbol, days=max(int(days), 1))
        collected.extend(reports)
        counts["report"] = len(reports)

    items = [item for item in collected if _in_window(item.get("published_at", ""), days)]
    if keyword_text:
        items = [item for item in items
                 if keyword_text in (item.get("title", "") + item.get("content", "")).lower()]
    items.sort(key=_sort_key, reverse=True)
    items = dedup(items)

    # 补正文放在**去重之后**：同一份公告可能同时来自公告源与媒体转述，
    # 先去重能让"补几次正文"等于"真的有几条不同公告"，省掉重复请求。
    # 只在指定了标的时补：`with_body` 的语义是"用户正在看这只股票"，
    # 没有标的时"最近的公告"是随机公司的，补它们的正文纯属浪费（10 次请求）。
    bodies_fetched = 0
    if with_body and symbol:
        enriched = enrich_announcement_bodies(items)
        items = enriched["items"]
        bodies_fetched = enriched["fetched"]
        errors.extend(enriched["errors"])

    # 截断放在计数**之前**：counts 与 items 是同一个口径，前端会并排显示
    # "公告 x / 媒体 y"，两者对不上会让用户以为丢了数据。
    truncated = len(items) > MAX_ITEMS
    items = items[:MAX_ITEMS]

    # 计数以**去重后**为准：前端要显示"公告 2 / 媒体 1"，而这两个数字加起来
    # 必须等于列表里肉眼能数出的条数，否则用户会以为丢了数据。
    final_counts = {name: 0 for name in counts}
    for item in items:
        level = item.get("source_level")
        if level == LEVEL_NOTICE:
            final_counts["notice"] += 1
        elif level == LEVEL_REPORT:
            final_counts["report"] += 1
        elif level == LEVEL_MEDIA:
            # 无 symbol 的媒体条目 = 宏观快讯
            final_counts["market" if not item.get("symbol") else "news"] += 1

    return {"items": items, "counts": final_counts, "errors": errors,
            "bodies_fetched": bodies_fetched, "truncated": truncated}
