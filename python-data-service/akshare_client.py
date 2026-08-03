"""
数据客户端
- 实时行情/概况：腾讯单股 API（快速）
- K线历史：腾讯 ifzq API
- 批量/分析数据：akshare（后续 LLM 使用）
"""
import logging
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


def get_quote(symbol: str) -> Optional[dict]:
    """获取实时行情（腾讯单股 API，毫秒级）。

    支持: 数字代码 (600519) / 带前缀 (sh600519) / 中文名 (茅台) / 美股 (AAPL)
    """
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
    except Exception as e:
        logger.error("get_quote 异常: %s -> %s", symbol, e)
        return None


def get_history(symbol: str, start_date: str = "", end_date: str = "") -> Optional[list[dict]]:
    """获取日线 K 线历史（腾讯 ifzq API）。

    支持: 数字代码 / 带前缀 / 中文名 / 美股（A 股和港股，美股没数据会返回 None）
    """
    try:
        resolved = resolve_symbol(symbol)
        if not resolved:
            logger.warning("无法识别股票代码/名称: %s", symbol)
            return None
        code = normalize_symbol(resolved)
        url = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        params = {"param": f"{code},day,,,500,qfq"}
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        data = r.json()

        code_key = code.lower()
        klines = None
        if code_key in data.get("data", {}):
            day_data = data["data"][code_key]
            klines = day_data.get("qfqday") or day_data.get("day")

        if not klines:
            logger.warning("历史数据为空: %s (resolved=%s)", symbol, code)
            return None

        records = []
        for k in klines:
            records.append({
                "date": str(k[0]), "open": str(k[1]), "close": str(k[2]),
                "high": str(k[3]), "low": str(k[4]), "volume": str(k[5]),
            })
        return records
    except Exception as e:
        logger.error("get_history 异常: %s -> %s", symbol, e)
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
