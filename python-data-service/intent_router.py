"""
intent_router.py —— 意图识别

简单的规则 + 关键词匹配，覆盖 80% 场景。
复杂的兜底交给 LLM 自由回答（chat 场景）。
"""
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ===== 意图类型 =====
INTENT_QUOTE = "quote"                # 查行情
INTENT_ANALYZE = "analyze"            # 技术分析
INTENT_HISTORY = "history"            # 查K线/历史
INTENT_WATCHLIST = "watchlist"        # 自选股
INTENT_BIND = "bind"                  # 绑定
INTENT_UNBIND = "unbind"              # 解绑
INTENT_HELP = "help"                  # 帮助
INTENT_CHAT = "chat"                  # 闲聊/兜底
INTENT_SEARCH_HISTORY = "search_history"  # 搜历史对话（向量检索）


@dataclass
class Intent:
    type: str
    symbol: Optional[str] = None      # 股票代码（如果有）
    keyword: Optional[str] = None     # 股票名（如果有）
    code: Optional[str] = None        # 验证码（绑定场景）
    confidence: float = 1.0


# 常见股票名/代码词典（精简版，够用起步）
STOCK_KEYWORDS = {
    # 简称 → 可能的代码（akshare 会自动识别）
    "茅台": "sh600519",
    "贵州茅台": "sh600519",
    "宁王": "sz300750",
    "宁德": "sz300750",
    "宁德时代": "sz300750",
    "平安": "sh601318",
    "招行": "sh600036",
    "工行": "sh601398",
    "比亚迪": "sz002594",
    "五粮液": "sz000858",
    "中芯": "sh688981",
    "中芯国际": "sh688981",
    "京东": "baba",
    "苹果": "aapl",
    "特斯拉": "tsla",
    "微软": "msft",
    "腾讯": "hk00700",
    "阿里": "baba",
}


def parse(message: str) -> Intent:
    """
    解析用户消息，返回意图

    Examples:
        "茅台"          → quote, 茅台
        "600519"        → quote, 600519
        "海康威视"      → quote, 海康威视  (词典外也能识别)
        "宁德能买吗"    → analyze, 宁德
        "我的自选"      → watchlist
        "绑定 123456"   → bind, code=123456
        "你好"          → chat
    """
    msg = message.strip()
    if not msg:
        return Intent(type=INTENT_CHAT, confidence=1.0)

    msg_lower = msg.lower()

    # ========== 1. 绑定/解绑命令（优先级最高） ==========
    bind_match = re.match(r"^(绑定|bind)\s*(\d{6})\s*$", msg_lower)
    if bind_match:
        return Intent(type=INTENT_BIND, code=bind_match.group(2), confidence=1.0)

    if msg in ["解绑", "解绑qq", "unbind"]:
        return Intent(type=INTENT_UNBIND, confidence=1.0)

    # ========== 1.5 搜历史对话 ==========
    search_patterns = ["之前问过", "以前问过", "我之前问", "我以前问", "我问过的", "之前聊过", "上次问过", "历史对话", "历史记录"]
    if any(p in msg for p in search_patterns):
        return Intent(type=INTENT_SEARCH_HISTORY, confidence=0.95)

    # ========== 2. 帮助 ==========
    if msg in ["帮助", "help", "?", "？", "菜单", "help me", "怎么用"]:
        return Intent(type=INTENT_HELP, confidence=1.0)

    # ========== 3. 自选股相关 ==========
    if re.search(r"(我的|查看|看).*(自选|持仓|关注)", msg):
        return Intent(type=INTENT_WATCHLIST, confidence=0.95)
    if re.search(r"(自选|持仓).*(怎么样|表现|今天)", msg):
        return Intent(type=INTENT_WATCHLIST, confidence=0.95)

    # ========== 4. 提取股票代码/名称 ==========
    symbol = _extract_symbol(msg)
    keyword = _extract_keyword(msg) if not symbol else None

    # ========== 4.5 兜底：含中文但词典查不到，认为是股票名（让 handler 解析） ==========
    # 解决 "海康威视"/"科大讯飞" 等词典外的中文股被误判为 chat
    # 但要排除常见问候语/动词，避免 "你好"/"谢谢" 被误当股票
    CHAT_PHRASES = {"你好", "您好", "hi", "hello", "在吗", "在么", "谢谢", "感谢",
                    "再见", "拜拜", "晚安", "早安", "早上好", "下午好", "晚上好",
                    "你是谁", "你是什么", "你能做什么", "帮助", "help", "菜单", "怎么用"}
    if not symbol and not keyword and _contains_chinese(msg) and msg_lower not in CHAT_PHRASES:
        # 短词（1-2 字）很可能是问候，跳过
        chinese_chars = sum(1 for ch in msg if '\u4e00' <= ch <= '\u9fff')
        if chinese_chars >= 2:
            keyword = msg  # 整条消息当股票名

    # ========== 5. 根据关键词判断意图 ==========
    analyze_keywords = ["能买", "能卖", "怎么看", "分析", "建议", "走势", "该不该", "怎么样", "好不好", "如何", "趋势"]
    history_keywords = ["k线", "历史", "走势", "几天", "一个月", "半年", "周线", "月线"]

    is_analyze = any(k in msg for k in analyze_keywords)
    is_history = any(k in msg for k in history_keywords)

    if symbol or keyword:
        if is_history and not is_analyze:
            return Intent(type=INTENT_HISTORY, symbol=symbol, keyword=keyword, confidence=0.9)
        if is_analyze or is_history:
            return Intent(type=INTENT_ANALYZE, symbol=symbol, keyword=keyword, confidence=0.9)
        # 默认纯代码/名称 → 行情
        return Intent(type=INTENT_QUOTE, symbol=symbol, keyword=keyword, confidence=0.9)

    # ========== 6. 兜底：闲聊 ==========
    return Intent(type=INTENT_CHAT, confidence=0.5)


def _contains_chinese(text: str) -> bool:
    """判断字符串是否含中文字符"""
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff':
            return True
    return False


def _extract_symbol(msg: str) -> Optional[str]:
    """提取 6 位数字股票代码（A股）或 字母+数字（美股/港股）"""
    # 优先：带前缀的代码 sh600519 / sz000001（大小写都接受）
    m = re.search(r"((?:sh|sz|hk|bj)\d{6})", msg, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    # A股 6 位（前面不能是字母，避免 "sh600519" 中重复匹配 "600519"）
    m = re.search(r"(?<![a-zA-Z])(\d{6})(?![a-zA-Z])", msg)
    if m:
        return m.group(1)
    # 美股 1-5 个大写字母
    m = re.search(r"\b([A-Z]{1,5})\b", msg)
    if m:
        return m.group(1)
    return None


def _extract_keyword(msg: str) -> Optional[str]:
    """提取中文股票名（按长度优先匹配）"""
    # 按名字长度从长到短匹配
    for name in sorted(STOCK_KEYWORDS.keys(), key=len, reverse=True):
        if name in msg:
            return name
    return None


def reply_for_help() -> str:
    """帮助菜单"""
    return (
        "🤖 股小盯 · 命令菜单\n"
        "\n"
        "📊 查行情\n"
        "  · 600519 / 茅台 / 宁德\n"
        "\n"
        "📈 技术分析\n"
        "  · 宁德时代能买吗\n"
        "  · 帮我分析下 002594\n"
        "\n"
        "⭐ 我的自选\n"
        "  · 我的自选 / 看看持仓\n"
        "\n"
        "🔗 绑定 QQ\n"
        "  · 先去网页登录 → 绑定页面拿验证码\n"
        "  · 在这发: 绑定 888888\n"
        "\n"
        "💡 提示: 直接发股票名也能识别"
    )
