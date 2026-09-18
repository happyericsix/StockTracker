"""
数据客户端
- 实时行情/概况：腾讯单股 API（快速，带 15s TTL 缓存）
- K线历史：腾讯 ifzq API
- 批量/分析数据：akshare（后续 LLM 使用）
"""
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Optional
import requests
import threading
import akshare

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0"}


def normalize_symbol(symbol: str) -> str:
    """转为腾讯格式：sh600519 / sz000001 / hk00700 / usAAPL

    ⚠️ 美股代码必须**大写**（`usAAPL`）。小写 `usaapl` 腾讯会回 `v_pv_none_match`
    （实测 2026-09-16），改造前这里是小写，于是 `get_quote("AAPL")` 一直返回 None ——
    行情接口"看起来在工作、只是这只票没有数据"，这类静默失败最难发现。
    """
    s = symbol.strip().upper()
    if s.startswith("HK"):
        return f"hk{s[2:].zfill(5)}"
    if s.startswith("SH"):
        return f"sh{s[2:]}"
    if s.startswith("SZ"):
        return f"sz{s[2:]}"
    if s.startswith("BJ"):
        return f"bj{s[2:]}"
    if s.startswith("US"):
        return f"us{s[2:].upper()}"
    if s.isdigit():
        # 4/5 位纯数字：港股（如 00700/09618）；6 位：A 股
        if len(s) == 4 or len(s) == 5:
            return f"hk{s.zfill(5)}"
        if len(s) == 6:
            if s.startswith("6"):
                return f"sh{s}"
            if s[0] in "023":
                return f"sz{s}"
            return f"bj{s}"  # 4/8/92 开头 → 北交所
        return s
    if s.isalpha() and s.isascii():
        # 美股代码（纯字母，如 AAPL/TSLA）→ 腾讯 API 需要 us 前缀 + 大写代码
        return f"us{s.upper()}"
    return s


# 常见股票名 → 代码 映射（快速路径，避免每次搜索）
NAME_TO_CODE = {
    "茅台": "600519", "贵州茅台": "600519",
    "五粮液": "000858",
    "宁王": "300750", "宁德": "300750", "宁德时代": "300750",
    "比亚迪": "002594",
    "平安": "601318", "中国平安": "601318",
    "招行": "600036", "招商银行": "600036",
    "工行": "601398", "工商银行": "601398",
    "中芯": "688981", "中芯国际": "688981",
    "京东": "9618", "阿里": "9988", "腾讯": "0700",
    "苹果": "AAPL", "特斯拉": "TSLA", "微软": "MSFT", "英伟达": "NVDA",
    "谷歌": "GOOGL", "亚马逊": "AMZN", "Meta": "META", "Netflix": "NFLX",
    "中石油": "601857", "中石化": "600028", "中行": "601988",
    "建行": "601939", "农行": "601288", "交行": "601328",
    "美的": "000333", "格力": "000651", "海尔": "600690",
    "恒瑞": "600276", "药明": "603259", "迈瑞": "300760",
    "隆基": "601012", "宁王转债": "123224",
    "海康": "002415", "海康威视": "002415",
    "科大讯飞": "002230", "中兴": "000063", "中兴通讯": "000063",
    "京东方": "000725", "立讯": "002475", "立讯精密": "002475",
    "韦尔": "603501", "韦尔股份": "603501",
    "三一": "600031", "三一重工": "600031",
    "万科": "000002", "保利": "600048",
    "药明康德": "603259", "片仔癀": "600436",
    "紫金": "601899", "紫金矿业": "601899",
    "牧原": "002714", "牧原股份": "002714",
    "阳光": "300274", "阳光电源": "300274",
    "中免": "601888", "中国中免": "601888",
}


def resolve_symbol(symbol_or_keyword: str) -> Optional[str]:
    """
    把任意输入（数字代码 / 带前缀代码 / 中文名 / 英文名）转成纯数字代码或带前缀格式。

    Returns:
        标准化后的代码（如 "600519" / "sh600519" / "AAPL"），找不到返回 None
    """
    if not symbol_or_keyword:
        return None
    s = symbol_or_keyword.strip()
    if not s:
        return None

    # 1. 快速词典查
    if s in NAME_TO_CODE:
        return NAME_TO_CODE[s]

    # 2. 已经是代码格式（数字 / 带前缀 / 美股）
    upper = s.upper()
    if upper.startswith(("SH", "SZ", "HK", "BJ")):
        return upper
    if s.isdigit() and len(s) == 6:
        return s
    if s.isdigit() and len(s) in (4, 5):
        # 港股代码（如 0700 腾讯 → 00700），避免掉进 A 股搜索的"静默查不到"
        return s.zfill(5)
    if upper.isalpha() and upper.isascii() and 1 <= len(upper) <= 5:
        return upper  # 美股代码如 AAPL TSLA

    # 3. 字典查不到，用 akshare 搜全 A 股名单
    try:
        results = search_stocks(s)
        if results:
            return results[0]["code"]
    except Exception as e:
        logger.warning("search_stocks 异常: %s -> %s", s, e)

    return None


# ==================== 行情缓存（15s TTL） ====================

_quote_cache: dict[str, tuple[float, dict]] = {}
_quote_cache_lock = threading.Lock()
QUOTE_CACHE_TTL = 15  # 秒


# ==================== K线历史缓存（1h TTL） ====================
# K线历史是收盘价，白天几乎不变；分钟 K 单独 30s TTL（盘中要近实时）
_history_cache: dict[tuple, tuple[float, list[dict]]] = {}
_history_cache_lock = threading.Lock()
HISTORY_CACHE_TTL = 3600  # 日/周/月 K：1 小时
MINUTE_CACHE_TTL = 30     # 分钟 K：30 秒

# 一次批量行情最多取多少只（腾讯接口对 URL 长度敏感，50 只约 500 字符，很安全）
QUOTE_BATCH_LIMIT = 50


def _parse_quote_text(text: str) -> Optional[dict]:
    """解析腾讯行情返回的一段 `v_xxx="a~b~c";`，异常形状返回 None。

    抽出来是因为批量行情（`get_quotes`）用的是同一个响应格式：
    两处各写一遍解析，就是两处各自会漂移的地方。
    """
    if not text or "=" not in text or '"' not in text:
        return None
    parts = text.split('"')[1].split("~")
    if len(parts) < 40:
        return None
    return {
        "代码": parts[2],
        "名称": parts[1],
        "最新价": parts[3],
        "昨收": parts[4],
        "今开": parts[5],
        "最高": parts[33],
        "最低": parts[34],
        "成交量": parts[6],
        "成交额": parts[37],
        "涨跌幅": parts[32],
        "涨跌额": parts[31],
        "总市值": parts[45] if len(parts) > 45 else "0",
        "流通市值": parts[44] if len(parts) > 44 else "0",
        "市盈率-动态": parts[39] if len(parts) > 39 else "0",
    }


def get_quote(symbol: str) -> Optional[dict]:
    """获取实时行情（腾讯单股 API，毫秒级），带 15s 缓存。

    支持: 数字代码 (600519) / 带前缀 (sh600519) / 中文名 (茅台) / 美股 (AAPL)
    """
    # 1. 检查缓存
    now = time.time()
    with _quote_cache_lock:
        if symbol in _quote_cache:
            ts, cached = _quote_cache[symbol]
            if now - ts < QUOTE_CACHE_TTL:
                return cached
            # 过期了删掉
            del _quote_cache[symbol]

    # 2. 请求行情
    try:
        resolved = resolve_symbol(symbol)
        if not resolved:
            logger.warning("无法识别股票代码/名称: %s", symbol)
            return None
        code = normalize_symbol(resolved)
        r = requests.get(f"http://qt.gtimg.cn/q={code}", headers=HEADERS, timeout=10)
        r.encoding = "gbk"
        result = _parse_quote_text(r.text.strip())
        if result is None:
            logger.warning("腾讯行情返回空或格式异常: %s (resolved=%s)", symbol, code)
            return None

        # 3. 写入缓存
        with _quote_cache_lock:
            _quote_cache[symbol] = (now, result)

        return result
    except Exception as e:
        logger.error("get_quote 异常: %s -> %s", symbol, e)
        return None


def _code_key(value) -> str:
    """把"各种写法的代码"归一成同一个查找键。

    腾讯的批量响应回来的代码**不一定**是你请求的那一个：
    美股会带上交易所后缀（实测请求 `usAAPL` 回来的是 `AAPL.OQ`）。
    按原样比较就会静默少一只股票 —— 而"少了一只"在下游看起来像是"这只票没行情"。
    """
    text = str(value or "").strip().upper()
    text = re.sub(r"^(SH|SZ|BJ|HK|US)", "", text)
    return text.split(".")[0]


def get_quotes(symbols) -> dict:
    """批量行情：**一次** HTTP 请求取多只标的（腾讯行情支持逗号分隔）。

    实测（2026-09-16，本机）：5 只标的 0.05s，一次往返；逐个调用则是 5 次往返。
    全部走"先查 15s 缓存、只把缺失的合并成一次请求"，所以与 `get_quote` 共享同一份缓存语义。

    为什么不用"整市场快照"（`akshare.stock_zh_a_spot_em`）：实测它按 59 页翻页抓取，
    本机直接 `ConnectionError`（7.9s 后断开）—— 用一个会失败的 59 次请求去省 N 次请求，
    是拿稳定性换一个并不存在的性能问题。

    返回 {调用方给的符号: 行情 or None}；解析不出代码的符号直接给 None，不抛异常。
    """
    wanted = [str(s).strip() for s in (symbols or []) if str(s).strip()]
    # 去重但保持顺序：模型可能把同一只股票写两遍
    unique = list(dict.fromkeys(wanted))[:QUOTE_BATCH_LIMIT]
    result: dict = {symbol: None for symbol in unique}
    if not unique:
        return result

    now = time.time()
    missing: list[tuple[str, str]] = []
    with _quote_cache_lock:
        for symbol in unique:
            entry = _quote_cache.get(symbol)
            if entry and now - entry[0] < QUOTE_CACHE_TTL:
                result[symbol] = entry[1]
            else:
                resolved = resolve_symbol(symbol)
                if not resolved:
                    logger.warning("无法识别股票代码/名称: %s", symbol)
                    continue
                missing.append((symbol, normalize_symbol(resolved)))

    if not missing:
        return result

    try:
        codes = ",".join(code for _, code in missing)
        r = requests.get(f"http://qt.gtimg.cn/q={codes}", headers=HEADERS, timeout=15)
        r.encoding = "gbk"
        # 响应的顺序**不保证**与请求一致，所以按代码回填，而不是按位置；
        # 代码还要归一化（美股会带 .OQ/.N 之类的后缀）
        by_code: dict = {}
        for chunk in r.text.split(";"):
            quote = _parse_quote_text(chunk.strip())
            if quote:
                by_code[_code_key(quote.get("代码"))] = quote

        with _quote_cache_lock:
            for symbol, code in missing:
                quote = by_code.get(_code_key(code))
                if quote:
                    result[symbol] = quote
                    _quote_cache[symbol] = (now, quote)
    except Exception as e:
        logger.error("get_quotes 异常: %s -> %s", unique, e)
    return result


# 行情复权口径（**唯一真相源**）：既是取数时的参数，也是执行契约里报告的 `adjust_mode`。
# 为什么做成常量而不是散在 f-string 里：痕迹要记录"这条记录用的是哪种复权口径"，
# 而口径一旦改变（除权后前复权价会被重算），历史数据的解释依据就变了。
ADJUST_MODE = "qfq"

# 一次取多少根（默认最近 500 根 ≈ 两年日线）。
DEFAULT_HISTORY_COUNT = 500

# <h3>源头一页最多 640 根 —— 这是探针实测出来的，不是猜的</h3>
# 腾讯 `fqkline` 的 `limit` 有两个坑，两个都会**静默出错**：
#   ① 它是"区间内**最近** N 根"。请求 2023-01-01 ~ 2026-09-17 配 limit=500，
#      回来的是 2024-08-27 之后那 500 根 —— 起点被砍掉，于是
#      "从 2023 年起做样本外验证"实际只跑了最后两年；
#   ② `limit` 超过 640 时接口直接返回**空**（探针：limit=3000 → 0 根），
#      于是"给我整段历史"会变成"这只票没有历史数据"。
# 所以：根数不许超过这个上限，区间更长时**分页**取，而不是把 limit 开大。
SOURCE_MAX_BARS = 640
# 一页覆盖多少自然日（640 交易日 ≈ 2.6 年；留出安全余量，宁可多取一页再裁）
CHUNK_CALENDAR_DAYS = 900
# 分页上限（≈ 15 年日线）。再长就不是日线该干的事了 —— 有上限，成本才是可预期的。
MAX_PAGES = 6
# 请求起点与源头最早一根之间，多少天以内的差距算"起点那天没有交易"而不是"覆盖不足"。
# 元旦连休可以到 9 天，所以给 10 天。
COVERAGE_GRACE_DAYS = 10


def _days_between(start: str, end: str) -> int:
    try:
        return abs((datetime.strptime(end, "%Y-%m-%d")
                    - datetime.strptime(start, "%Y-%m-%d")).days)
    except ValueError:
        return 0


def _limit_for_range(start_date: str, end_date: str, count: Optional[int]) -> int:
    """每次请求要多少根：显式 `count` 优先，其余取满一页（区间由分页负责）。"""
    if count:
        return max(1, min(int(count), SOURCE_MAX_BARS))
    if not start_date and not end_date:
        return DEFAULT_HISTORY_COUNT
    return SOURCE_MAX_BARS


def _windows_for(start_date: str, end_date: str) -> list[tuple[str, str]]:
    """把请求区间切成若干**连续**页，每页不超过一页能取到的根数。

    没有区间时只有一页（`("", "")`，交给接口取最近 N 根）。
    切不出来（区间格式不对 / 起止颠倒）时也退回单页，由调用方按默认根数取。
    """
    if not (start_date or end_date):
        return [("", "")]
    start, end = start_date, end_date
    try:
        if not start:
            # 只给了终点：往前推一个可分页的量，具体多少根仍由接口决定
            start = (datetime.strptime(end, "%Y-%m-%d")
                     - timedelta(days=CHUNK_CALENDAR_DAYS * MAX_PAGES)).strftime("%Y-%m-%d")
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d") if end else datetime.now()
    except ValueError:
        logger.warning("区间格式无法解析（%s ~ %s），按单页取", start_date, end_date)
        return [("", "")]
    if end_dt <= start_dt:
        return [("", "")]

    windows = []
    cursor = start_dt
    # 先把候选页全部切出来（留一个不至于失控的上限），**再**决定丢哪一头。
    # 边切边丢是最容易写错的地方：见下面那条实测出来的注释。
    while cursor < end_dt and len(windows) < MAX_PAGES * 4 + 8:
        page_end = min(cursor + timedelta(days=CHUNK_CALENDAR_DAYS - 1), end_dt)
        windows.append((cursor.strftime("%Y-%m-%d"), page_end.strftime("%Y-%m-%d")))
        cursor = page_end + timedelta(days=1)

    if len(windows) > MAX_PAGES:
        # <h3>页数不够时，**必须丢最早的一段，保住决策日附近**</h3>
        # 这是真跑发现的 bug：agent 端点只给 end_date（决策日），于是"往前推 6 页"
        # 正好把最后一页挤掉 —— 拿回来的最后一批数据停在**一个月前**，
        # 而 agent 照样在上面做决策、报告上完全看不出来（价格、指标、辩论全都"合理"）。
        dropped = windows[:len(windows) - MAX_PAGES]
        windows = windows[-MAX_PAGES:]
        logger.warning("区间需要 %d 页、超过上限 %d：%s 之前（%d 页）的历史不再取，"
                       "保留最近 %d 页以保证**决策日附近的数据一定在**",
                       len(dropped) + MAX_PAGES, MAX_PAGES, dropped[0][0], len(dropped), MAX_PAGES)

    # 最后一页必须**真的落到请求的终点**上。
    # 只给终点时，起点是按"往前推 MAX_PAGES 页"算的，而页长整除的结果会差一天 ——
    # 于是"取到今天"实际只取到昨天。差一天听起来无所谓，但它会让 decision-day 的存在性判断
    # （agent 那条"决策日必须有 bar"）在边界上偶发失败，而失败的样子是"今天休市"。
    if end and windows[-1][1] != end:
        windows[-1] = (windows[-1][0], end)
    return windows


def _fetch_page(code: str, period: str, start_date: str, end_date: str,
                limit: int) -> list[dict]:
    """取一页 K 线（腾讯 ifzq）。接口给的原始行 → `{date, open, close, high, low, volume}`。"""
    url = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{code},{period},{start_date},{end_date},{limit},{ADJUST_MODE}"}
    r = requests.get(url, params=params, headers=HEADERS, timeout=15)
    payload = r.json()

    code_key = code.lower()
    klines = None
    # 响应的 data 键会**原样回显**请求里的大小写（实测：请求 usAAPL 得到键 usAAPL，
    # 请求 usaapl 得到 usaapl），所以按大小写不敏感匹配，别再假设它是小写。
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    for key, body in (data or {}).items():
        if str(key).lower() != code_key or not isinstance(body, dict):
            continue
        klines = body.get(f"qfq{period}") or body.get(period)
        break
    if not klines:
        return []

    return [{"date": str(k[0]), "open": str(k[1]), "close": str(k[2]),
             "high": str(k[3]), "low": str(k[4]), "volume": str(k[5])}
            for k in klines]


def get_history(symbol: str, start_date: str = "", end_date: str = "",
                period: str = "day", count: Optional[int] = None) -> Optional[list[dict]]:
    """获取 K 线历史（腾讯 ifzq API），带 1 小时缓存。

    <h3>`start_date` / `end_date` 现在**真的生效**了</h3>
    在此之前这两个参数是废的：请求被写死成 `,,,500,qfq`（不传区间、恒取最近 500 根），
    于是"这条规则在**别的时段**上还行不行"根本无法验证 —— 而样本外验证是判断
    "策略到底有没有效"的唯一手段。缓存键也必须带上区间，
    否则"最近 500 根"会把"某年某段"的结果顶掉（那是最难查的一类错：数据看着有，其实不对）。

    现在有两层保证：
    ① **分页**：区间需要的根数超过源头一页的上限（`SOURCE_MAX_BARS`）时，
       按自然日切成连续几页分别取、再合并 —— 否则"从 2023 年起"会被悄悄砍成"最近 640 根"；
    ② **本地裁到区间内**：接口给多了给少了，最终口径都由我们负责，
       并在覆盖不到请求起点时留一条 warning（新股、或源头就没有更早的数据）。

    复权口径 `ADJUST_MODE` 是前复权：分页各页都以**最新**价格为基准，
    所以拼起来仍然是一致的一条序列（这也是分页在前复权下才成立的原因）。

    Args:
        symbol: 股票代码/名称
        start_date / end_date: `YYYY-MM-DD`，留空表示不限（由 `count` 决定取多少根）
        period: day / week / month（默认 day）
        count: 最多取多少根（上限 `SOURCE_MAX_BARS`，超过按上限截断）
    """
    period = (period or "day").lower()
    if period not in ("day", "week", "month"):
        logger.warning("不支持的 period: %s，回退为 day", period)
        period = "day"

    start_date = str(start_date or "").strip()
    end_date = str(end_date or "").strip()
    limit = _limit_for_range(start_date, end_date, count)

    # 1. 查缓存（键必须含区间与数量：少了它们，不同请求会互相顶掉）
    cache_key = (symbol, period, start_date, end_date, limit)
    now = time.time()
    with _history_cache_lock:
        if cache_key in _history_cache:
            ts, cached = _history_cache[cache_key]
            if now - ts < HISTORY_CACHE_TTL:
                return cached
            del _history_cache[cache_key]

    try:
        resolved = resolve_symbol(symbol)
        if not resolved:
            logger.warning("无法识别股票代码/名称: %s", symbol)
            return None
        code = normalize_symbol(resolved)

        windows = _windows_for(start_date, end_date)
        merged: dict[str, dict] = {}
        for page_start, page_end in windows:
            page_limit = limit if len(windows) == 1 else SOURCE_MAX_BARS
            for record in _fetch_page(code, period, page_start, page_end, page_limit):
                merged[record["date"]] = record      # 同一天重复出现时后一页为准（内容相同）

        if not merged:
            logger.warning("历史数据为空: %s (period=%s, code=%s)", symbol, period, code)
            return None

        records = [merged[date] for date in sorted(merged)]

        # 本地裁到请求区间（含端点）。日期是 ISO 字符串，字典序即时间序。
        if start_date or end_date:
            before = len(records)
            if start_date:
                records = [r for r in records if r["date"] >= start_date]
            if end_date:
                records = [r for r in records if r["date"] <= end_date]
            if records and start_date and records[0]["date"] > start_date:
                # 取到了区间，但源头没有更早的数据：新股、或源头就没有那么早的历史。
                # 说清楚，别让"从某年起"的验证悄悄变成"从能取到的那天起"。
                # 但**起点落在非交易日**（元旦/周末）是常态，那不是"覆盖不足" ——
                # 天天报警的告警等于没有告警，所以只在小缺口时报 info。
                gap_days = _days_between(start_date, records[0]["date"])
                if gap_days > COVERAGE_GRACE_DAYS:
                    logger.warning("历史数据覆盖不足: %s 请求自 %s 起，实际最早 %s（差 %d 天，%d→%d 根）",
                                   symbol, start_date, records[0]["date"], gap_days, before, len(records))
                else:
                    logger.info("%s 区间起点 %s 无交易日，实际自 %s 起", symbol, start_date, records[0]["date"])

        # 2. 写缓存
        with _history_cache_lock:
            _history_cache[cache_key] = (now, records)
        return records or None
    except Exception as e:
        logger.error("get_history 异常: %s (period=%s) -> %s", symbol, period, e)
        return None


def get_minute_kline(symbol: str, period: int = 5) -> Optional[list[dict]]:
    """获取分钟 K 线（akshare），带 30 秒缓存。

    Args:
        symbol: 股票代码（如 600519 / sh600519 / 茅台）
        period: 分钟周期，可选 1 / 5 / 15 / 30 / 60
    """
    if period not in (1, 5, 15, 30, 60):
        logger.warning("不支持的分钟周期: %s，回退为 5", period)
        period = 5

    # 1. 查缓存
    cache_key = (symbol, f"m{period}")
    now = time.time()
    with _history_cache_lock:
        if cache_key in _history_cache:
            ts, cached = _history_cache[cache_key]
            if now - ts < MINUTE_CACHE_TTL:
                return cached
            del _history_cache[cache_key]

    try:
        resolved = resolve_symbol(symbol)
        if not resolved:
            logger.warning("无法识别股票代码/名称: %s", symbol)
            return None
        # akshare minute kline (sina source) requires exchange prefix, e.g. sh600519 / sz000001 / bj430047
        code = normalize_symbol(resolved).lower()
        if not (code.startswith(("sh", "sz", "bj")) and len(code) == 8 and code[2:].isdigit()):
            logger.warning("分钟 K 仅支持 A 股: %s", symbol)
            return None

        # use raw prices: qfq merge makes today's unfinished bars NaN, hiding the current price
        df = akshare.stock_zh_a_minute(symbol=code, period=str(period), adjust="")
        if df is None or df.empty:
            logger.warning("分钟 K 数据为空: %s (period=%s)", symbol, period)
            return None

        records = []
        for _, row in df.iterrows():
            records.append({
                "date": str(row["day"]),
                "open": str(row["open"]),
                "close": str(row["close"]),
                "high": str(row["high"]),
                "low": str(row["low"]),
                "volume": str(row.get("volume", 0)),
            })

        # 2. 写缓存
        with _history_cache_lock:
            _history_cache[cache_key] = (now, records)
        return records
    except Exception as e:
        logger.error("get_minute_kline 异常: %s (period=%s) -> %s", symbol, period, e)
        return None


def get_overview(symbol: str) -> Optional[dict]:
    """获取基本面概况（复用腾讯行情数据）。"""
    return get_quote(symbol)


# ==================== 全 A 股名单缓存 ====================

_stock_list_cache: list[dict] = []
_stock_list_lock = threading.Lock()
_stock_list_loaded = False
_stock_list_failed_at = 0.0
# 名单加载失败后的冷却：失败一次要 6 秒（实测 5564 条 / 18 页），
# 不设冷却的话，上游一挂，**每一次** search_stock 都要白等 6 秒才报错。
STOCK_LIST_RETRY_COOLDOWN = 60


def _load_stock_list() -> list[dict]:
    """一次性加载全 A 股名单并缓存（~5000 条，实测 5564 条 / 6.2s / 18 页）。"""
    global _stock_list_cache, _stock_list_loaded, _stock_list_failed_at
    if _stock_list_loaded:
        return _stock_list_cache
    with _stock_list_lock:
        if _stock_list_loaded:
            return _stock_list_cache
        if _stock_list_failed_at and time.time() - _stock_list_failed_at < STOCK_LIST_RETRY_COOLDOWN:
            raise RuntimeError(
                "全 A 股名单加载失败，正在冷却（约 60 秒后自动重试）；"
                "请稍后再试，或直接使用 6 位股票代码（如 600519）")
        logger.info("正在加载全 A 股名单...")
        try:
            df = akshare.stock_info_a_code_name()
        except Exception:
            _stock_list_failed_at = time.time()
            raise
        _stock_list_cache = [{"code": str(r["code"]), "name": str(r["name"])} for _, r in df.iterrows()]
        _stock_list_loaded = True
        _stock_list_failed_at = 0.0
        logger.info("全 A 股名单加载完成，共 %d 条", len(_stock_list_cache))
    return _stock_list_cache


def warm_stock_list(background: bool = True) -> None:
    """提前把全 A 股名单加载进内存。

    为什么值得：首个 `search_stock` 要等 6 秒（上游按 18 页翻），而它的工具超时是 20 秒 ——
    冷启动时用户会真的等这么久。启动时预热把它挪到没人等的时段；
    后台线程失败也不影响任何事，`search_stocks` 仍然会自己按冷却重试。
    """
    if _stock_list_loaded:
        return

    def _warm():
        try:
            _load_stock_list()
        except Exception as e:  # noqa: BLE001 —— 预热失败不是错误，只是没省下这 6 秒
            logger.warning("全 A 股名单预热失败（首次搜索时会重试）：%s", e)

    if not background:
        _warm()
        return
    threading.Thread(target=_warm, name="warm-stock-list", daemon=True).start()


def search_stocks(keyword: str) -> list[dict]:
    """按关键词搜索股票：代码前缀 + 名称模糊匹配，最多返回 20 条。"""
    if not keyword or not keyword.strip():
        return []
    keyword_upper = keyword.strip().upper()
    results = [
        s for s in _load_stock_list()
        if s["code"].startswith(keyword_upper) or keyword_upper in s["name"].upper()
    ]
    return results[:20]


# ==================== 外部数据集（T2a）：新闻 / 财报 ====================
#
# 这里**刻意不做缓存**：TTL 与健康状态统一归 `agent/external_source.py`（按数据集分级）。
# 一个函数里既取数又管缓存又管降级，最后一定会有人加错地方（本项目的历史上，
# 15s/1h/30s 三档 TTL 就散在三处）。这里只负责"把上游的表格变成结构化数据"。

# 单条正文上限：实测 `stock_news_em` 的正文均值只有 119 字，
# 超过这个长度的基本都是通稿/研报摘要，留标题和开头就够判断"这条讲什么"。
NEWS_ITEM_CHARS = 300
NEWS_MAX_ITEMS = 30
FINANCIAL_MAX_PERIODS = 8

# 财务摘要里值得给模型的指标（原来一张表 80 行 × 100+ 列，直接塞进上下文是灾难）
FINANCIAL_METRICS = (
    "归母净利润", "营业总收入", "净利润", "扣非净利润", "股东权益合计(净资产)",
    "经营现金流量净额", "基本每股收益", "每股净资产", "净资产收益率(ROE)",
    "毛利率", "销售净利率", "资产负债率",
)
# 金额类指标统一换算成"亿元"：原值是元，44516880421.86 这种数字模型没法一眼比较
FINANCIAL_YI_METRICS = frozenset({
    "归母净利润", "营业总收入", "净利润", "扣非净利润", "股东权益合计(净资产)", "经营现金流量净额",
})
FINANCIAL_UNITS = {
    **{name: "亿元" for name in FINANCIAL_YI_METRICS},
    "基本每股收益": "元", "每股净资产": "元",
    "净资产收益率(ROE)": "%", "毛利率": "%", "销售净利率": "%", "资产负债率": "%",
}


def _clip_text(value, limit=NEWS_ITEM_CHARS) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _news_rows(df, source_kind: str) -> list[dict]:
    """把东财新闻/快讯的 DataFrame 归一成同一种结构，并**按时间升序**排列。

    <h3>为什么必须显式排序（这是实测踩到的坑）</h3>
    `akshare.stock_news_em` 返回的顺序**不是时间序**（实测 600519：第一条 2026-08-15、
    第二条 2026-09-14、第三条 2026-09-08……）。而下游的字符裁剪是"保留尾部"
    （因为行情类数据尾部最新）。两者一撞，"裁剪后剩下的"就是一批**随机**的新闻 ——
    看起来正常、实际全是过期消息，是最难发现的那类错误。

    所以：归一化阶段统一排成升序（旧 → 新），让"留尾部"重新等于"留最新"。
    `stock_info_global_em` 本身是降序（最新在前），也一并反过来。
    """
    items = []
    if df is None or getattr(df, "empty", True):
        return items
    for _, row in df.iterrows():
        if source_kind == "symbol":
            title = row.get("新闻标题")
            summary = _clip_text(row.get("新闻内容"))
            published = row.get("发布时间")
            origin = row.get("文章来源")
            url = row.get("新闻链接")
        else:
            title = row.get("标题")
            summary = _clip_text(row.get("摘要"))
            published = row.get("发布时间")
            origin = "东方财富·全球快讯"
            url = row.get("链接")
        items.append({
            "title": _clip_text(title, 120),
            "summary": summary,
            "published_at": str(published or "").strip(),
            "source": _clip_text(origin, 40),
            "url": str(url or "").strip(),
        })
    # 时间缺失的排最前（它们最可能是脏数据，正好在"留尾部"时优先被丢掉）
    items.sort(key=lambda item: item["published_at"])
    return items


def is_a_share(symbol: str) -> bool:
    """是不是 6 位纯数字 A 股代码（东财的个股接口只认这个）。"""
    text = str(symbol or "").strip()
    return len(text) == 6 and text.isdigit()


def get_symbol_news(symbol: str, limit: int = 10) -> dict:
    """个股新闻（东方财富）。`symbol` 必须是 6 位 A 股代码（调用方先解析）。"""
    size = _clamp_int(limit, 10, NEWS_MAX_ITEMS)
    df = akshare.stock_news_em(symbol=str(symbol))
    items = _news_rows(df, "symbol")
    return {"scope": "symbol", "symbol": str(symbol), "items": items[-size:],
            "note": "个股新闻（东方财富），已按发布时间升序，最后一条最新。"}


def get_market_news(limit: int = 10) -> dict:
    """全市场财经快讯（东方财富），按发布时间升序。"""
    size = _clamp_int(limit, 10, NEWS_MAX_ITEMS)
    df = akshare.stock_info_global_em()
    items = _news_rows(df, "market")
    return {"scope": "market", "symbol": None, "items": items[-size:],
            "note": "全市场财经快讯（东方财富），已按发布时间升序，最后一条最新。"}


def get_news(symbol: str = "", limit: int = 10) -> dict:
    """取新闻：给了 A 股代码就取个股新闻，否则取全市场快讯。

    Returns:
        {"scope": "symbol"/"market", "symbol": 代码 or None, "items": [...]}
        —— 空结果返回 `{"items": []}`，由上层决定怎么说（"今天没消息"是合法答案）
    """
    if symbol and str(symbol).strip():
        resolved = str(resolve_symbol(str(symbol).strip()) or "").strip()
        if is_a_share(resolved):
            return get_symbol_news(resolved, limit)
        # 东财个股新闻只认 6 位 A 股代码；港股/美股/北交所走市场快讯，别假装查到了
        logger.info("get_news 不支持该标的的个股新闻，回退市场快讯: %s", symbol)
    return get_market_news(limit)


def _clamp_int(value, default: int, maximum: int) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _period_label(period: str) -> str:
    """20260630 → 2026年中报（让人一眼看懂，不必自己数月份）。"""
    period = str(period or "")
    if len(period) != 8 or not period.isdigit():
        return period
    year, month = period[:4], period[4:6]
    return {"03": f"{year}一季报", "06": f"{year}中报",
            "09": f"{year}三季报", "12": f"{year}年报"}.get(month, f"{year}-{month}")


def _to_yi(value):
    try:
        return round(float(value) / 1e8, 2)
    except (TypeError, ValueError):
        return None


def get_financial_abstract(symbol: str, periods: int = 4) -> Optional[dict]:
    """取财务摘要（东方财富），只保留少数关键指标与最近若干报告期。

    上游是 80 行 × 100+ 列的宽表（每个报告期一列，最早到 1998 年）——
    原样进上下文既烧钱又没用。这里做两件事：挑关键指标、只取最近几个报告期，
    并顺手算两个同比（归母净利润、营业总收入），因为"同比"才是看财报的第一个问题。
    """
    if not symbol or not str(symbol).strip():
        return None
    resolved = str(resolve_symbol(str(symbol).strip()) or "").strip()
    if not is_a_share(resolved):
        logger.info("财务摘要仅支持 A 股 6 位代码: %s", symbol)
        return None

    size = _clamp_int(periods, 4, FINANCIAL_MAX_PERIODS)
    code = resolved

    df = akshare.stock_financial_abstract(symbol=code)
    if df is None or getattr(df, "empty", True):
        return None

    columns = [str(c) for c in df.columns if str(c).isdigit() and len(str(c)) == 8]
    latest = sorted(columns, reverse=True)[:size]
    if not latest:
        return None

    by_indicator: dict = {}
    for _, row in df.iterrows():
        indicator = str(row.get("指标") or "").strip()
        if indicator in FINANCIAL_METRICS and indicator not in by_indicator:
            by_indicator[indicator] = row

    result_periods = []
    for period in latest:
        metrics: dict = {}
        for indicator, row in by_indicator.items():
            raw = row.get(period)
            if raw is None or (isinstance(raw, float) and raw != raw):  # NaN
                continue
            metrics[indicator] = _to_yi(raw) if indicator in FINANCIAL_YI_METRICS else _clean_number(raw)
        yoy: dict = {}
        for indicator in ("归母净利润", "营业总收入"):
            if indicator not in by_indicator or len(period) != 8:
                continue
            previous = f"{int(period[:4]) - 1}{period[4:]}"
            current_value = _to_yi(by_indicator[indicator].get(period))
            previous_value = _to_yi(by_indicator[indicator].get(previous))
            if current_value is None or not previous_value:
                continue
            yoy[indicator] = round((current_value / previous_value - 1) * 100, 2)
        result_periods.append({
            "period": period, "period_label": _period_label(period),
            "metrics": metrics, "yoy_pct": yoy,
        })

    return {
        "symbol": code,
        "name": _stock_name(code),
        "periods": result_periods,
        "units": {name: FINANCIAL_UNITS.get(name, "") for name in by_indicator},
        "note": ("按报告期披露的财务摘要（东方财富）；yoy_pct 是与去年同期的同比增速。"
                 "财报是已发生的事实，不能用来预测股价。"),
    }


def _clean_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    if number != number:  # NaN
        return None
    # 比率类可能是小数（0.1234）也可能是百分数，保持原值，只做精度收敛
    return round(number, 4) if abs(number) < 1000 else round(number, 2)


def _stock_name(code: str) -> str:
    """从名字缓存里取股票名；取不到就返回空串（不为了一个装饰字段去拉网络）。"""
    if not _stock_list_loaded:
        return ""
    for item in _stock_list_cache:
        if item.get("code") == code:
            return str(item.get("name") or "")
    return ""
