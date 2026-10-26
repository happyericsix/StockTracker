"""
数据客户端
- 实时行情/概况：腾讯单股 API（快速，带 15s TTL 缓存）
- K线历史：腾讯 ifzq API
- 批量/分析数据：akshare（后续 LLM 使用）
"""
import logging
import os
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


# ==================== 行情缓存（默认 3s，可配） ====================
# 2026-10-15 从 15 收紧到 3：图表页焦点票走 3 秒快车道（Java FocusQuoteStreamJob），
# 缓存 15s 会让"3 秒推一次"推的有 2/3 是旧价。腾讯免费快照本身约 3 秒一跳，
# TTL=3 意味着每次快车道取数几乎都是源上最新；自选 15s 批也因此从"最多 25~30s 旧价"
# 变成真 15s 新鲜度。上游压力 ≈ 每票每 3 秒一次 HTTP，浏览器行情页都是这个量级。
_quote_cache: dict[str, tuple[float, dict]] = {}
_quote_cache_lock = threading.Lock()
QUOTE_CACHE_TTL = int(os.getenv("QUOTE_CACHE_TTL", "3"))


# ==================== K线历史缓存（1h TTL） ====================
# K线历史是收盘价，白天几乎不变；分钟 K 单独 30s TTL（盘中要近实时）
_history_cache: dict[tuple, tuple[float, list[dict]]] = {}
_history_cache_lock = threading.Lock()
HISTORY_CACHE_TTL = 3600  # 日/周/月 K：1 小时
# 请求区间**覆盖今天**时的短新鲜度：日线在收盘后一段时间才陆续发布
#（实测 2026-10-08：15:30 结算当口腾讯源还没有当天 bar，17:05 才有）。
# 这类请求若也走 1 小时缓存，15:30 那次"没有当天 bar"会被一直端给后续调用 ——
# 模拟盘从启动那天起就永远结算不了。只回看历史的请求（回测/验证）不受影响。
# 2026-10-15 从 300 收紧到 60：图表页盘中 30 秒轮询日线，今日半根 K 线被 5 分钟缓存
# 冻结的话"实时刷新"名存实亡（用户实测图不动）。60s 让蜡烛最迟 1 分钟长一次，
# 上游压力 = 每分钟每票一次日线请求，单用户部署完全可承受。
HISTORY_RECENT_TTL = int(os.getenv("HISTORY_RECENT_TTL", "60"))
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

    # 2. 请求行情（腾讯主源；A 股失败时新浪兜底，见 _fetch_quotes_sina 的说明）
    resolved = resolve_symbol(symbol)
    if not resolved:
        logger.warning("无法识别股票代码/名称: %s", symbol)
        return None
    code = normalize_symbol(resolved)
    try:
        r = requests.get(f"https://qt.gtimg.cn/q={code}", headers=HEADERS, timeout=10)
        r.encoding = "gbk"
        result = _parse_quote_text(r.text.strip())
        if result is not None:
            with _quote_cache_lock:
                _quote_cache[symbol] = (now, result)
            return result
        logger.warning("腾讯行情返回空或格式异常: %s (resolved=%s)", symbol, code)
    except Exception as e:  # noqa: BLE001 - 降级链的一环
        logger.error("腾讯行情请求异常: %s -> %s", symbol, e)

    fallback: dict = {}
    if _fill_from_sina([(symbol, code)], fallback, now):
        return fallback[symbol]
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


# ==================== 行情源降级：腾讯 → 新浪（仅 A 股）====================
#
# 为什么需要降级：qt.gtimg.cn 目前是全部实时行情的唯一出口，它一抖（限流/机房
# 故障/接口改版），行情页、预警评估、模拟盘盘中结算一起瞎。调研（research/README
# 主线 A）推荐的多级数据源降级在这里落第一级：A 股实时价走新浪兜底。
#
# 为什么只兜 A 股：新浪 hq 接口的港股/美股是另一套字段布局（hk00700 的数组含义
# 与 A 股完全不同），照搬映射只会**静默给错数**——那比没有数据危险得多。
# 其它市场维持诚实的 None。
#
# 刻意不做 K 线历史降级：本项目 K 线契约钉死前复权（ADJUST_MODE="qfq" 是唯一
# 真相源，回测/模拟盘都按它解释），而新浪可用的日 K 接口是**不复权**的——
# 用不同复权口径的数据兜底，等于让回测悄悄换了一套价格。宁可不兜，不可错兜。
SINA_QUOTE_URL = "https://hq.sinajs.cn/list={codes}"
# 2021 年起新浪强制校验 Referer，缺失直接 403 —— 无 Key 公开接口最常见的坑
SINA_HEADERS = {"User-Agent": HEADERS["User-Agent"], "Referer": "https://finance.sina.com.cn"}


def _is_a_share(code: str) -> bool:
    """腾讯格式代码是否 A 股（sh/sz + 6 位数字）。北交所新浪不支持，不兜。"""
    return len(code) == 8 and code[:2] in ("sh", "sz") and code[2:].isdigit()


def _sina_quote_fields(code: str, parts: list) -> Optional[dict]:
    """新浪 A 股行情行 → 与 _parse_quote_text **同键同义** 的 dict。

    字段布局（逗号分隔）：0名称 1今开 2昨收 3最新 4最高 5最低 8成交量(股)
    9成交额 … 30日期 31时间。涨跌额/涨跌幅由昨收推导；总市值/流通市值/市盈率
    新浪 hq 不提供，填 "0"（与腾讯解析器对缺失字段的约定一致——契约键必须齐全）。
    """
    if len(parts) < 32 or not parts[3] or parts[3] in ("0.00", "0"):
        return None
    try:
        close = float(parts[3])
        prev = float(parts[2])
    except ValueError:
        return None
    change = round(close - prev, 2)
    pct = round(change / prev * 100, 2) if prev else 0.0
    try:
        volume_hands = round(float(parts[8]) / 100)  # 新浪给股，腾讯给手（1手=100股）
    except (ValueError, IndexError):
        volume_hands = 0
    return {
        "代码": code,
        "名称": parts[0],
        "最新价": parts[3],
        "昨收": parts[2],
        "今开": parts[1],
        "最高": parts[4],
        "最低": parts[5],
        "成交量": str(volume_hands),
        "成交额": parts[9] if len(parts) > 9 else "0",
        "涨跌幅": f"{pct:.2f}",
        "涨跌额": f"{change:.2f}",
        "总市值": "0",
        "流通市值": "0",
        "市盈率-动态": "0",
    }


def _fetch_quotes_sina(codes: list) -> dict:
    """新浪批量行情：一次请求多只，返回 {归一化代码: 行情dict}。失败返回空 dict。"""
    if not codes:
        return {}
    try:
        r = requests.get(SINA_QUOTE_URL.format(codes=",".join(codes)),
                         headers=SINA_HEADERS, timeout=8)
        r.encoding = "gbk"
    except Exception as e:  # noqa: BLE001 - 降级链的一环，失败要静默成"没有兜底"
        logger.warning("新浪行情兜底请求失败: %s", e)
        return {}
    out: dict = {}
    for chunk in r.text.strip().splitlines():
        if '="' not in chunk:
            continue
        head, _, payload = chunk.partition('="')
        code = head.strip().split("hq_str_")[-1]
        quote = _sina_quote_fields(code, payload.rstrip('";\r').split(","))
        if quote:
            out[_code_key(quote.get("代码"))] = quote
    return out


def _fill_from_sina(missing, result: dict, now: float) -> int:
    """把腾讯没给到的 A 股条目用新浪兜底填上（写缓存），返回兜底成功数。

    只处理 A 股子集并**合并成一次请求**：兜底是异常路径，别让它自己再变成
    N 次单点请求。
    """
    candidates = [(symbol, code) for symbol, code in missing if _is_a_share(code)]
    if not candidates:
        return 0
    by_code = _fetch_quotes_sina([code for _, code in candidates])
    filled = 0
    for symbol, code in candidates:
        quote = by_code.get(_code_key(code))
        if quote:
            result[symbol] = quote
            with _quote_cache_lock:
                _quote_cache[symbol] = (now, quote)
            filled += 1
    if filled:
        logger.info("行情兜底生效：新浪补回 %d/%d 只（腾讯缺失）", filled, len(candidates))
    return filled


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
    # 锁内只做缓存判定。resolve_symbol 对中文名/未知输入会走网络（冷启动拉全 A 股名单
    # 实测 6s+），放在锁里会让这 6 秒内所有 get_quote/get_quotes 全部排队 ——
    # 一次没预热的中文查询等于把整个行情服务冻住。
    misses: list[str] = []
    with _quote_cache_lock:
        for symbol in unique:
            entry = _quote_cache.get(symbol)
            if entry and now - entry[0] < QUOTE_CACHE_TTL:
                result[symbol] = entry[1]
            else:
                misses.append(symbol)

    missing: list[tuple[str, str]] = []
    for symbol in misses:
        resolved = resolve_symbol(symbol)
        if not resolved:
            logger.warning("无法识别股票代码/名称: %s", symbol)
            continue
        missing.append((symbol, normalize_symbol(resolved)))

    if not missing:
        return result

    try:
        codes = ",".join(code for _, code in missing)
        r = requests.get(f"https://qt.gtimg.cn/q={codes}", headers=HEADERS, timeout=15)
        r.encoding = "gbk"
        # 响应的顺序**不保证**与请求一致，所以按代码回填，而不是按位置；
        # 代码还要归一化（美股会带 .OQ/.N 之类的后缀）
        by_code: dict = {}
        for chunk in r.text.split(";"):
            quote = _parse_quote_text(chunk.strip())
            if quote:
                by_code[_code_key(quote.get("代码"))] = quote

        still_missing = []
        with _quote_cache_lock:
            for symbol, code in missing:
                quote = by_code.get(_code_key(code))
                if quote:
                    result[symbol] = quote
                    _quote_cache[symbol] = (now, quote)
                else:
                    still_missing.append((symbol, code))
        # 腾讯缺的 A 股合并成一次新浪请求兜底（非 A 股不兜，理由见 _fetch_quotes_sina）
        if still_missing:
            _fill_from_sina(still_missing, result, now)
    except Exception as e:
        logger.error("get_quotes 异常（尝试新浪兜底）: %s -> %s", unique, e)
        # 整批腾讯请求挂了（超时/断网）也别整批返回 None：A 股部分还能救
        _fill_from_sina(missing, result, now)
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

# <h3>K 线取数的同源退避重试（2026-10-19/20 事故）</h3>
# 连续两天 15:30 日线结算时段腾讯 ifzq 对本机拒绝服务 100+ 分钟，单次请求直接判死，
# 结算以 data_unavailable 跳过。这里补的是**同源重试**——不换源、不碰复权口径
# （qfq 单一真相源的约束见上方 ADJUST_MODE 与行情源降级区的"宁可不兜，不可错兜"），
# 只对网络类异常多试几次，写法沿用 llm_service._post_with_retry。
# 只重试异常（超时/连接/JSON 解析失败），**不重试"正常返回但为空"**：
# 空是源的正常回答（新股、超出源头历史），重试只会加倍延迟。
# 延迟预算：单页最坏 15s 超时 × 3 次 + 退避 2s + 4s ≈ 51s，仍在 Java evaluate-bar
# 的 60s 超时内；多页区间查询最坏耗时约增 3 倍，但那只发生在源整段宕掉时（原本也失败）。
KLINE_RETRY_ATTEMPTS = int(os.getenv("KLINE_RETRY_ATTEMPTS", "3"))
KLINE_RETRY_BACKOFF_S = float(os.getenv("KLINE_RETRY_BACKOFF_S", "2.0"))


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


def _retry_kline_fetch(what: str, fetch):
    """K 线取数的同源退避重试（参数与理由见 `KLINE_RETRY_ATTEMPTS` 注释）。

    只重试网络类异常（超时 / 连接 / JSON 解析失败——requests 的 JSONDecodeError
    是 ValueError 子类）；fetch 正常返回（包括空结果）不重试：空是源的正常回答，
    判断"空"并决定怎么办是调用方的事。其余异常（如参数错误）原样抛出。
    """
    for attempt in range(max(1, KLINE_RETRY_ATTEMPTS)):
        try:
            return fetch()
        except (requests.Timeout, requests.ConnectionError, ValueError) as e:
            if attempt >= KLINE_RETRY_ATTEMPTS - 1:
                raise
            wait = KLINE_RETRY_BACKOFF_S * (2 ** attempt)
            logger.warning("%s 取数异常（第 %d 次 %s），%.1fs 后重试",
                           what, attempt + 1, type(e).__name__, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _fetch_page(code: str, period: str, start_date: str, end_date: str,
                limit: int) -> list[dict]:
    """取一页 K 线（腾讯 ifzq）。接口给的原始行 → `{date, open, close, high, low, volume}`。"""
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{code},{period},{start_date},{end_date},{limit},{ADJUST_MODE}"}
    payload = _retry_kline_fetch(
        f"腾讯K线 {code}",
        lambda: requests.get(url, params=params, headers=HEADERS, timeout=15).json())

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

    # 1. 查缓存（键必须含区间与数量：少了它们，不同请求会互相顶掉）。
    #    区间覆盖"今天"的请求按短新鲜度服务：当天 bar 的发布有延迟，
    #    长缓存会把"15:30 那一刻还没有"冻结成一个小时（见 HISTORY_RECENT_TTL 的说明）。
    cache_key = (symbol, period, start_date, end_date, limit)
    now = time.time()
    covers_today = (not end_date) or (end_date >= time.strftime("%Y-%m-%d"))
    ttl = HISTORY_RECENT_TTL if covers_today else HISTORY_CACHE_TTL
    with _history_cache_lock:
        if cache_key in _history_cache:
            ts, cached = _history_cache[cache_key]
            if now - ts < ttl:
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
        today = time.strftime("%Y-%m-%d")
        for page_start, page_end in windows:
            page_limit = limit if len(windows) == 1 else SOURCE_MAX_BARS
            # 终点落在"今天"的那一页改用**开放式终点**取数：实测腾讯的封闭区间查询
            # 在特定根数下会悄悄丢掉最新一根 —— 2026-10-09 实证：start=2024-04-22、
            # end=当天、limit=640 返回 596 根止于前一日；同一区间 end 留空（或 limit+1）
            # 才返回 597 根含当天。日线结算/重试据此全天 no_bar，而"最近 N 根"查询
            # 却看得到当天 bar，问题只出在区间查询这一条路上。
            # as-of 语义不受影响：下方本地裁剪（<= end_date）本来就是第二道防线，
            # 开放终点多取到的未来数据（不存在）会被它挡住。
            fetch_end = "" if (page_end and page_end >= today) else page_end
            for record in _fetch_page(code, period, page_start, fetch_end, page_limit):
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


def _minute_bar_start_label(label: str, period: int) -> str:
    """新浪分钟柱的时间标签是窗口**结束**时刻（5分K 的 `11:25` 覆盖 11:20-11:25），
    统一回退为窗口**起点**（open-time 口径，TradingView 同款）。

    为什么必须改：未走完的最后一根柱因此带着"未来时间"——11:22 打开页面，
    分时图最后一根蜡烛标着 11:25（60分K 会标到 13:30），这就是"价格比现在
    时间还快"的来源。回退到起点后标签永远 <= 当前时刻，且**同一窗口跨请求
    标签稳定**（这条被 evaluate_bar 的日期精确匹配依赖：标签随取数时刻漂移
    的话，结算重试会找不到自己的 bar）。
    """
    text = str(label)
    try:
        end = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text   # 解析不了的脏值原样透传，交给下游过滤
    return (end - timedelta(minutes=period)).strftime("%Y-%m-%d %H:%M:%S")


def get_minute_kline(symbol: str, period: int = 5) -> Optional[list[dict]]:
    """获取分钟 K 线（akshare），带 30 秒缓存。

    时间标签为窗口起点（见 `_minute_bar_start_label`），最后一根是**正在走**
    的当前柱——它的 close 是最新价，前端 SSE 补丁会持续更新它。

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
        # （同源退避重试与日线同参：分钟线是另一条独立源，此前同样裸奔——
        #  盘中结算依赖它，抖一下就白白损失一整天的盘中评估。）
        df = _retry_kline_fetch(
            f"新浪分钟K {code}",
            lambda: akshare.stock_zh_a_minute(symbol=code, period=str(period), adjust=""))
        if df is None or df.empty:
            logger.warning("分钟 K 数据为空: %s (period=%s)", symbol, period)
            return None

        records = []
        for _, row in df.iterrows():
            records.append({
                "date": _minute_bar_start_label(row["day"], period),
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
        resolved = str(resolve_symbol(str(symbol).strip()) or "").strip().upper()
        # ⚠️ `resolve_symbol` 对 "sh600519" 返回的是**带前缀的** "SH600519"（它只负责识别市场，
        # 不负责剥壳），而 `is_a_share` 只认 6 位纯数字。少了剥前缀这一步，"sh600519" ——
        # 也就是 README「股票代码格式」表里写的标准写法 —— 会被判成"非 A 股"，
        # 于是 get_news 静默回退成全市场快讯：用户问"茅台最近有什么消息"，
        # 拿到的是宏观快讯，而整条链路一个错都不报。
        # 只剥 SH/SZ：北交所个股新闻是否可用**未实测**，不在这里顺手打开未验证的路径。
        if resolved.startswith(("SH", "SZ")):
            resolved = resolved[2:]
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


def get_financial_abstract(symbol: str, periods: int = 4, as_of: str = "") -> Optional[dict]:
    """取财务摘要（东方财富），只保留少数关键指标与最近若干报告期。

    上游是 80 行 × 100+ 列的宽表（每个报告期一列，最早到 1998 年）——
    原样进上下文既烧钱又没用。这里做两件事：挑关键指标、只取最近几个报告期，
    并顺手算两个同比（归母净利润、营业总收入），因为"同比"才是看财报的第一个问题。

    `as_of`（YYYY-MM-DD，可空）：as-of 回放（沙盘）用——只保留**当时已披露**的
    报告期。财报没有披露日字段，按"报告期 + 122 天（≈ 一个季度 + 一个月缓冲）"
    近似视为已披露：所有法定披露截止日（一季/三季报、半年报，年报次年 4 月末）
    都落在这个缓冲之后——宁可晚看到一个月，绝不偷看未披露的报告期。
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
    if as_of:
        try:
            cutoff = datetime.strptime(str(as_of)[:10].replace("-", ""), "%Y%m%d").date()
            columns = [c for c in columns
                       if datetime.strptime(c, "%Y%m%d").date() + timedelta(days=122) <= cutoff]
        except ValueError:
            pass  # as_of 格式坏 → 不过滤（按最新处理），不因调用方笔误挡数据
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


# ==================== 估值历史（DSL pe_percentile 条件与沙盘估值语境的数据原语） ====================

#: 估值日频更新：12h TTL 足够新鲜（与 agent.market_context 的 VALUATION_TTL 同口径）
VALUATION_HISTORY_TTL = 12 * 3600
_valuation_history_cache: dict = {}


def get_valuation_history(symbol: str) -> list[dict]:
    """东财数据中心 stock_value_em → PE-TTM 日线序列（升序）。

    返回 [{"date": "YYYY-MM-DD", "pe_ttm": float}]。负 PE（亏损期）与脏行剔除：
    亏损期的 PE 无序可言，混进分位会同时污染"贵"与"便宜"两端。12h TTL 按
    symbol 缓存。fail-open：取不到返回 []（调用方的估值条件恒不成立）。
    """
    code = str(resolve_symbol(str(symbol or "").strip()) or "").strip()
    if not code or not is_a_share(code):
        return []
    now = time.time()
    cached = _valuation_history_cache.get(code)
    if cached and now - cached[0] < VALUATION_HISTORY_TTL:
        return cached[1]
    rows: list[dict] = []
    try:
        df = akshare.stock_value_em(symbol=code)
        if df is not None and not getattr(df, "empty", True):
            for _, r in df.iterrows():
                try:
                    pe = float(r.get("PE(TTM)"))
                except (TypeError, ValueError):
                    continue
                if pe != pe or pe <= 0:  # NaN / 亏损期剔除
                    continue
                d = str(r.get("数据日期"))[:10]
                if len(d) == 10:
                    rows.append({"date": d, "pe_ttm": pe})
    except Exception as exc:  # noqa: BLE001 —— 估值是增强证据，缺席不挡任何主流程
        logger.info("估值历史取数失败 symbol=%s: %s", code, type(exc).__name__)
        return []
    rows.sort(key=lambda x: x["date"])
    _valuation_history_cache[code] = (now, rows)
    return rows


# ==================== 个股人气榜（资讯雷达「热榜」的数据原语，2026-10-16） ====================

#: 人气榜分钟级更新：5 分钟缓存足够新鲜（同类情报工具的热榜也是分钟粒度）
HOT_RANK_TTL = 5 * 60
_hot_rank_cache: dict = {"at": 0.0, "rows": []}


def get_hot_rank(limit: int = 20) -> list[dict]:
    """东财个股人气榜（stock_hot_rank_em）→ 热榜 TopN。

    实测列（2026-10-16）：当前排名 / 代码（SH600418）/ 股票名称 / 最新价 /
    涨跌额 / 涨跌幅。返回升序排名的 [{rank, symbol, name, price, change_pct}]，
    5 分钟 TTL 共享缓存（所有调用方看到同一份）。fail-open：取不到返回 []。
    """
    now = time.time()
    cached = _hot_rank_cache.get("rows")
    if cached and now - _hot_rank_cache.get("at", 0.0) < HOT_RANK_TTL:
        return cached[:max(1, int(limit))]
    rows: list[dict] = []
    try:
        df = akshare.stock_hot_rank_em()
        for _, r in df.iterrows():
            try:
                rank = int(r.get("当前排名"))
            except (TypeError, ValueError):
                continue
            rows.append({
                "rank": rank,
                "symbol": str(r.get("代码") or "").upper(),
                "name": str(r.get("股票名称") or "").strip(),
                "price": _clean_number(r.get("最新价")),
                "change_pct": _clean_number(r.get("涨跌幅")),
            })
        rows.sort(key=lambda x: x["rank"])
    except Exception as exc:  # noqa: BLE001 —— 热榜是增强视图，缺席不挡任何主流程
        logger.info("人气榜取数失败: %s", type(exc).__name__)
        return []
    _hot_rank_cache["at"], _hot_rank_cache["rows"] = now, rows
    return rows[:max(1, int(limit))]


# ==================== 大盘资金流（Dashboard 资金卡与委员会证据的数据原语，2026-10-16） ====================

#: 资金流日频更新（盘后出全天数）：10 分钟 TTL 足够新鲜
MARKET_FUND_FLOW_TTL = 10 * 60
#: 失败的负缓存窗口：刚失败过就别再打上游，一分钟后再试。
#: 原实现失败**不写**缓存、命中判断又是 `if cached and ...`（空列表是假值），
#: 于是上游一旦失败，每个请求都重新冷启动撞一遍 —— 那就是资金卡"每次打开都慢"的根因。
FUND_FLOW_NEG_TTL = 60
#: 旧值最多回多久（stale-while-revalidate 的上限）。日频数据：三天还没刷新成功，
#: 宁可让前端走"暂不可用"，也不把三天前的数当今天的给。
FUND_FLOW_MAX_STALE = 3 * 24 * 3600
#: at=上次**成功**取数时刻（驱动 TTL 与旧值年龄）；tried_at=上次**尝试**时刻
#: （成功失败都推进，驱动负缓存与刷新节流）；ok=rows 是否可用。
_fund_flow_cache: dict = {"at": 0.0, "tried_at": 0.0, "rows": [], "ok": False}
_fund_flow_cache_lock = threading.Lock()
#: 取数 single-flight：冷启动/后台刷新共用一把锁，同一时刻只有一个线程真打上游
_fund_flow_fetch_lock = threading.Lock()

#: stock_market_fund_flow 的净额列（元）→ 统一换算亿元输出；占比列直接透传
_FUND_NET_COLUMNS = {
    "main": "主力净流入-净额", "super": "超大单净流入-净额",
    "big": "大单净流入-净额", "mid": "中单净流入-净额", "small": "小单净流入-净额",
}


def _yi(value) -> Optional[float]:
    """元 → 亿元（None/NaN 安全）：资金流两个源的净额字段单位都是元。
    pandas 的缺失值是 float('nan')，float() 转换不报错——不挡的话 NaN 会一路
    进 JSON（非标准字面量），前端比较和渲染全都会出错。"""
    try:
        v = float(value) / 1e8
    except (TypeError, ValueError):
        return None
    if v != v:   # NaN 判等自身不成立
        return None
    return round(v, 2)


def _fetch_fund_flow_rows_em() -> list[dict]:
    """东财源（stock_market_fund_flow）：120 日 × 四档历史，口径最全。失败返回 []。"""
    try:
        df = akshare.stock_market_fund_flow()
    except Exception as exc:  # noqa: BLE001 —— 失败交给上层落腾讯源，不重试
        logger.info("东财大盘资金流取数失败: %s", type(exc).__name__)
        return []
    rows: list[dict] = []
    for _, r in df.iterrows():
        d = str(r.get("日期"))[:10]
        if len(d) != 10:
            continue
        row = {"date": d,
               "sh_close": _clean_number(r.get("上证-收盘价")),
               "sh_pct": _clean_number(r.get("上证-涨跌幅"))}
        for key, column in _FUND_NET_COLUMNS.items():
            row[f"{key}_net_yi"] = _yi(r.get(column))
        rows.append(row)
    rows.sort(key=lambda x: x["date"])
    return rows


def _fetch_fund_flow_tencent(code: str, max_days: int = 50) -> list[dict]:
    """腾讯源（proxy.finance.qq.com 的 hsfundtab，gu.qq.com 资金流向页同款接口）。

    `code` 是腾讯格式代码：大盘 sh000001 / 个股 sh600519——同一接口换代码即个股口径
    （2026-10-19 实测），这也是个股资金流从"东财端点全挂、做不了"变可行的原因。

    <h3>为什么需要它（2026-10-19 生产实测）</h3>
    东财 push2his 对阿里云机房 IP 持续拒连（家宽正常、服务器 RemoteDisconnected），
    生产上资金卡因此长期"暂不可用"。腾讯这条在机房可达，且与本项目行情/K线同源。
    口径：主力=超大单+大单（成交额 ≥20 万元或 ≥6 万股）。

    <h3>局限（前端按此降级）</h3>
    ① 日级历史最多 50 根（klineNeedDay>50 直接 HTTP 400）；
    ② 日级只有主力净流入，四档（超大/大/中/小）仅 todayFundFlow 一份**当日**快照——
       只在"最后一根就是今天"时合并进去，休市日不硬补（宁可缺档也不把旧快照贴错日）。
    返回行带中性的 close/pct 键（大盘调用方再映射成 sh_close/sh_pct 保持旧契约）。
    """
    try:
        r = requests.get(
            "https://proxy.finance.qq.com/cgi/cgi-bin/fundflow/hsfundtab",
            params={"code": code,
                    "type": "historyFundFlow,fiveDayFundFlow,todayFundTrend,todayFundFlow",
                    "klineNeedDay": str(max(1, min(max_days, 50)))},
            headers={"User-Agent": HEADERS["User-Agent"],
                     "Referer": f"https://gu.qq.com/{code}/zjjl"},
            timeout=10)
        r.raise_for_status()
        data = (r.json() or {}).get("data") or {}
    except Exception as exc:  # noqa: BLE001 —— 两源都挂才走负缓存
        logger.info("腾讯资金流取数失败 code=%s: %s", code, type(exc).__name__)
        return []
    hist = ((data.get("historyFundFlow") or {}).get("oneDayKlineList")) or []
    rows: list[dict] = []
    prev_close = None
    for item in hist:
        d = str(item.get("date", ""))[:10]
        if len(d) != 10:
            continue
        close = _clean_number(item.get("price"))
        pct = None
        if close is not None and prev_close is not None:
            pct = round((close / prev_close - 1) * 100, 2)   # 涨跌幅：源里没有，用前收现算
        prev_close = close if close is not None else prev_close
        rows.append({"date": d, "close": close, "pct": pct,
                     "main_net_yi": _yi(item.get("mainNetIn")),
                     "super_net_yi": None, "big_net_yi": None,
                     "mid_net_yi": None, "small_net_yi": None})
    today = data.get("todayFundFlow") or {}
    if rows and rows[-1]["date"] == time.strftime("%Y-%m-%d"):
        for key, field in (("super_net_yi", "superFlow"), ("big_net_yi", "bigFlow"),
                           ("mid_net_yi", "normalFlow"), ("small_net_yi", "smallFlow")):
            v = _yi(today.get(field))
            if v is not None:
                rows[-1][key] = v
    return rows


def _fetch_fund_flow_rows_tencent(max_days: int = 50) -> list[dict]:
    """大盘口径的腾讯源：中性键映射回 sh_close/sh_pct（既有大盘契约，市场卡与
    market_context 共用，不为个股复用它）。"""
    rows = _fetch_fund_flow_tencent("sh000001", max_days)
    return [{"date": r["date"], "sh_close": r["close"], "sh_pct": r["pct"],
             "main_net_yi": r["main_net_yi"], "super_net_yi": r["super_net_yi"],
             "big_net_yi": r["big_net_yi"], "mid_net_yi": r["mid_net_yi"],
             "small_net_yi": r["small_net_yi"]} for r in rows]


def _fetch_market_fund_flow_rows() -> list[dict]:
    """资金流取数链：东财优先（口径全），机房被拒/上游挂掉时落腾讯源。
    两个源各自内部已吞异常，这里再兜一层——源函数抛错按"该源失败"处理，
    链路永远不向缓存层抛异常。"""
    try:
        rows = _fetch_fund_flow_rows_em()
    except Exception as exc:  # noqa: BLE001 —— 防御：源内部异常不炸整条链
        logger.info("东财源异常兜底: %s", type(exc).__name__)
        rows = []
    if rows:
        return rows
    logger.info("东财大盘资金流不可用，改用腾讯源")
    try:
        return _fetch_fund_flow_rows_tencent()
    except Exception as exc:  # noqa: BLE001
        logger.info("腾讯源异常兜底: %s", type(exc).__name__)
        return []


def _fund_flow_refresh() -> None:
    """后台刷新（stale-while-revalidate 的下半场）：拿 fetch 锁防与冷启动并发，
    **成功才覆盖** rows/at；失败只推进 tried_at —— 旧值继续服务，重试被负缓存节流。"""
    try:
        with _fund_flow_fetch_lock:
            fetched = _fetch_market_fund_flow_rows()
        with _fund_flow_cache_lock:
            _fund_flow_cache["tried_at"] = time.time()
            if fetched:
                _fund_flow_cache["at"] = time.time()
                _fund_flow_cache["rows"] = fetched
                _fund_flow_cache["ok"] = True
    except Exception as exc:  # noqa: BLE001 —— 刷新失败不影响已回给前端的旧值
        logger.info("大盘资金流后台刷新异常: %s", type(exc).__name__)
    finally:
        with _fund_flow_cache_lock:
            _fund_flow_cache["refreshing"] = False


def get_market_fund_flow(limit: int = 60) -> list[dict]:
    """大盘主力资金流日线（东财 stock_market_fund_flow），净额统一成"亿元"。

    <h3>缓存语义（2026-10-19 重写）</h3>
    ① 新鲜（成功后 10 分钟内）：直接回；
    ② 过期但旧值未超 FUND_FLOW_MAX_STALE：**先回旧值**，后台线程刷新
       （stale-while-revalidate）——页面打开不再同步等上游；刷新尝试被
       tried_at 节流到至多每分钟一次，上游挂掉也不会每请求一撞；
    ③ 无可用旧值：失败结果负缓存 FUND_FLOW_NEG_TTL（60 秒），期间不打上游；
    ④ 冷启动：single-flight 同步取 —— 并发的后来者在 fetch 锁上排队，
       第一个取完后直接读缓存，不再重复打上游。

    <h3>为什么只有大盘口径（2026-10-16 实测）</h3>
    个股（stock_individual_fund_flow / _rank）与板块（stock_sector_fund_flow_*）的
    东财端点在本机与服务器双网络都 ConnectionError——akshare 1.18.64 的这批端点
    已失效。资金流不造假数据：个股口径等上游修复后在此接入（列名见
    _FUND_NET_COLUMNS，个股源只是多一个代码维度）。

    <h3>双源（2026-10-19）</h3>
    东财优先（120 日 × 四档历史）；机房 IP 被东财拒连时落腾讯 hsfundtab
    （50 日主力 + 当日四档，见 _fetch_fund_flow_rows_tencent）。两个源各自的
    失败与成功统一走同一套缓存语义（下述①-④），成功后**整批替换**，不跨源拼接。

    fail-open：取不到返回 []。升序按日期，最后一根是最新交易日。
    """
    limit = max(1, int(limit))
    now = time.time()
    with _fund_flow_cache_lock:
        ok = bool(_fund_flow_cache.get("ok"))
        rows = _fund_flow_cache.get("rows") or []
        data_age = now - _fund_flow_cache.get("at", 0.0)
        tried_age = now - _fund_flow_cache.get("tried_at", 0.0)
        if ok and rows and data_age < MARKET_FUND_FLOW_TTL:
            return rows[-limit:]
        if ok and rows and data_age <= FUND_FLOW_MAX_STALE:
            if tried_age >= FUND_FLOW_NEG_TTL and not _fund_flow_cache.get("refreshing"):
                _fund_flow_cache["refreshing"] = True
                threading.Thread(target=_fund_flow_refresh, daemon=True,
                                 name="fund-flow-refresh").start()
            return rows[-limit:]   # 旧值先回，新值后台追
        if tried_age < FUND_FLOW_NEG_TTL:
            return []              # 刚试过且没有可用数据：负缓存窗口内不再打上游

    # 冷路径（进程首调 / 一直失败 / 旧值超龄）：排队取数，双检后真打上游
    with _fund_flow_fetch_lock:
        with _fund_flow_cache_lock:
            tried_age = time.time() - _fund_flow_cache.get("tried_at", 0.0)
            if (_fund_flow_cache.get("ok") and _fund_flow_cache.get("rows")
                    and time.time() - _fund_flow_cache.get("at", 0.0) < MARKET_FUND_FLOW_TTL):
                return (_fund_flow_cache["rows"] or [])[-limit:]   # 排队期间已被取好
            if tried_age < FUND_FLOW_NEG_TTL:
                return []                                          # 排队期间别人刚试过且失败
        fetched = _fetch_market_fund_flow_rows()
        with _fund_flow_cache_lock:
            _fund_flow_cache["tried_at"] = time.time()
            if fetched:
                _fund_flow_cache["at"] = time.time()
                _fund_flow_cache["rows"] = fetched
                _fund_flow_cache["ok"] = True
                return fetched[-limit:]
            _fund_flow_cache["ok"] = False
            return []


# ==================== 个股资金流（腾讯源，K 线页主力资金卡，2026-10-19） ====================

#: 盘中"今日主力净流入"快照在动，2 分钟足够跟上；日级历史部分本来就不变
STOCK_FUND_FLOW_TTL = 120
#: 失败/空结果负缓存：坏代码或上游挂时，不至于每次打开 K 线页都真打一遍
STOCK_FUND_FLOW_NEG_TTL = 60
_stock_fund_flow_cache: dict = {}
_stock_fund_flow_lock = threading.Lock()


def get_stock_fund_flow(symbol: str, limit: int = 30) -> list[dict]:
    """个股主力资金流日线（腾讯 hsfundtab——与大盘同一接口，换代码即个股口径）。

    行形状：{date, close, pct, main_net_yi, super/big/mid/small_net_yi}，升序，
    最后一根最新；四档只有**当日**那根有值（腾讯日级只有主力，见
    _fetch_fund_flow_tencent 的局限说明）。个股的东财端点本就全挂（见
    get_market_fund_flow 注释），这里腾讯单源。fail-open：取不到返回 []。
    """
    limit = max(1, min(int(limit), 50))
    resolved = resolve_symbol(symbol)
    if not resolved:
        logger.info("个股资金流：无法识别代码 %s", symbol)
        return []
    code = normalize_symbol(resolved)
    now = time.time()
    with _stock_fund_flow_lock:
        entry = _stock_fund_flow_cache.get(code) or {}
        rows = entry.get("rows") or []
        age = now - entry.get("at", 0.0)
        if rows and age < STOCK_FUND_FLOW_TTL:
            return rows[-limit:]
        if not rows and 0 <= age < STOCK_FUND_FLOW_NEG_TTL:
            return []
    fetched = _fetch_fund_flow_tencent(code, max_days=50)
    with _stock_fund_flow_lock:
        _stock_fund_flow_cache[code] = {"at": time.time(), "rows": fetched}
    return fetched[-limit:]


def get_valuation_percentile_series(symbol: str, window: int = 1250) -> list[dict]:
    """PE-TTM 与其滚动历史分位的日线序列（升序），K 线页估值副图用。

    复用 get_valuation_history 的缓存（同一份原始行，不重复打上游）；
    每个日期的分位 = 该日 PE 在此前 window 根（含当日）中的分位，
    与 market_context 估值语境、DSL pe_percentile 条件**同一口径**——
    三处看到的分位必须是同一个数。窗口不足 PE_PCT_MIN_OBS(250) 根的
    早期日期分位为 None（前端断线）。fail-open：取不到返回 []。
    """
    rows = get_valuation_history(symbol)
    if not rows:
        return []
    pes = [r["pe_ttm"] for r in rows]
    out = []
    for i, r in enumerate(rows):
        lo = max(0, i - window + 1)
        slice_ = pes[lo:i + 1]
        pct = None
        if len(slice_) >= 250:
            cur = pes[i]
            pct = round(sum(1 for v in slice_ if v <= cur) / len(slice_) * 100.0, 1)
        out.append({"date": r["date"], "pe": r["pe_ttm"], "pct": pct})
    return out


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


# ==================== 分红送配（除权除息，2026-10-14） ====================
# 模拟盘的"分红除权处理"数据源：现金红利与送转比例按官方公告口径（东财数据）。
# 缓存 12 小时：除权日是提前公告的，盘前刷新一次足够；方案进度变化当天可见。
DIVIDEND_CACHE_TTL = 12 * 3600
_dividend_cache: dict[str, tuple[float, list[dict]]] = {}


def get_dividends(symbol: str) -> Optional[list[dict]]:
    """某只 A 股的分红送配历史（已定除权除息日的行）。

    返回字段（**每股**口径，已是"每 10 股派 X"除以 10 后的值）：
      ex_date          除权除息日（YYYY-MM-DD）
      cash_per_share   每股现金红利（含税，元）
      stock_per_share  每股送转比例（10 送 2 → 0.2；无送转为 0）
      plan             方案进度（如"实施分配"）
    """
    resolved = resolve_symbol(symbol)
    if not resolved:
        return None
    code = normalize_symbol(resolved).lower()
    if not (code.startswith(("sh", "sz", "bj")) and code[2:].isdigit()):
        return None
    pure = code[2:]

    now = time.time()
    with _history_cache_lock:
        cached = _dividend_cache.get(pure)
        if cached and now - cached[0] < DIVIDEND_CACHE_TTL:
            return cached[1]

    try:
        df = akshare.stock_fhps_detail_em(symbol=pure)
    except Exception as e:
        logger.warning("分红送配查询失败 %s: %s", pure, e)
        return cached[1] if cached else None   # 拉挂了给旧缓存，别让结算拿不到

    rows = []
    if df is not None and not df.empty:
        for _, r in df.iterrows():
            ex = r.get("除权除息日")
            if ex is None or str(ex) in ("NaT", "nan", ""):
                continue   # 还没定除权日（董事会预案阶段）——没有可执行的调整
            try:
                ex_date = str(ex)[:10]
            except Exception:
                continue

            def per10(v) -> float:
                try:
                    f = float(v)
                    return 0.0 if f != f else f / 10.0   # NaN → 0
                except (TypeError, ValueError):
                    return 0.0

            rows.append({
                "ex_date": ex_date,
                "cash_per_share": per10(r.get("现金分红-现金分红比例")),
                "stock_per_share": per10(r.get("送转股份-送转总比例")),
                "plan": str(r.get("方案进度") or ""),
            })
    rows.sort(key=lambda x: x["ex_date"])
    with _history_cache_lock:
        _dividend_cache[pure] = (now, rows)
    return rows
