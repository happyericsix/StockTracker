"""
数据客户端
- 实时行情/概况：腾讯单股 API（快速，带 15s TTL 缓存）
- K线历史：腾讯 ifzq API
- 批量/分析数据：akshare（后续 LLM 使用）
"""
import logging
import time
from typing import Optional
import requests
import threading
import akshare

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0"}


def normalize_symbol(symbol: str) -> str:
    """转为腾讯格式：sh600519 / sz000001"""
    s = symbol.strip().upper()
    s = s.removeprefix("SH").removeprefix("SZ").removeprefix("HK").removeprefix("US")
    if s.startswith("6"):
        return f"sh{s}"
    elif s.startswith("0") or s.startswith("3") or s.startswith("2"):
        return f"sz{s}"
    elif s.startswith("8") or s.startswith("4") or s.startswith("92"):
        return f"bj{s}"
    elif s.isalpha() and s.isascii():
        # 美股代码（纯字母，如 AAPL/TSLA）→ 腾讯 API 需要 us 前缀
        return f"us{s.lower()}"
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
        text = r.text.strip()
        if "=" not in text:
            logger.warning("腾讯行情返回空: %s (resolved=%s)", symbol, code)
            return None

        parts = text.split('"')[1].split("~")
        if len(parts) < 40:
            logger.warning("腾讯行情格式异常: %s (parts=%d)", symbol, len(parts))
            return None

        result = {
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

        # 3. 写入缓存
        with _quote_cache_lock:
            _quote_cache[symbol] = (now, result)

        return result
    except Exception as e:
        logger.error("get_quote 异常: %s -> %s", symbol, e)
        return None


def get_history(symbol: str, start_date: str = "", end_date: str = "", period: str = "day") -> Optional[list[dict]]:
    """获取 K 线历史（腾讯 ifzq API），带 1 小时缓存。

    Args:
        symbol: 股票代码/名称
        period: 周期，可选 day / week / month（默认 day）
    """
    period = (period or "day").lower()
    if period not in ("day", "week", "month"):
        logger.warning("不支持的 period: %s，回退为 day", period)
        period = "day"

    # 1. 查缓存
    cache_key = (symbol, period)
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
        url = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        params = {"param": f"{code},{period},,,500,qfq"}
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        data = r.json()

        code_key = code.lower()
        klines = None
        if code_key in data.get("data", {}):
            day_data = data["data"][code_key]
            klines = day_data.get(f"qfq{period}") or day_data.get(period)

        if not klines:
            logger.warning("历史数据为空: %s (period=%s, code=%s)", symbol, period, code)
            return None

        records = []
        for k in klines:
            records.append({
                "date": str(k[0]), "open": str(k[1]), "close": str(k[2]),
                "high": str(k[3]), "low": str(k[4]), "volume": str(k[5]),
            })

        # 2. 写缓存
        with _history_cache_lock:
            _history_cache[cache_key] = (now, records)
        return records
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


def _load_stock_list() -> list[dict]:
    """一次性加载全 A 股名单并缓存（~5000 条）。"""
    global _stock_list_cache, _stock_list_loaded
    if _stock_list_loaded:
        return _stock_list_cache
    with _stock_list_lock:
        if _stock_list_loaded:
            return _stock_list_cache
        logger.info("正在加载全 A 股名单...")
        df = akshare.stock_info_a_code_name()
        _stock_list_cache = [{"code": str(r["code"]), "name": str(r["name"])} for _, r in df.iterrows()]
        _stock_list_loaded = True
        logger.info("全 A 股名单加载完成，共 %d 条", len(_stock_list_cache))
    return _stock_list_cache


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
