"""
qq_handler.py —— QQ 消息处理核心

根据意图路由到不同处理逻辑：
- quote: 查行情（直接调用 akshare_client）
- analyze: 技术分析（quant_model + LLM 解读）
- history: K线/历史
- watchlist: 自选股（查 user context）
- bind/unbind: 绑定/解绑流程
- help: 帮助菜单
- chat: 闲聊（LLM 兜底）
"""
import logging
from typing import Optional

import intent_router
import llm_service
import user_context
from akshare_client import get_quote, get_history, search_stocks
from quant_model import analyze_stock
from intent_router import INTENT_QUOTE, INTENT_ANALYZE, INTENT_HISTORY, INTENT_WATCHLIST, \
    INTENT_BIND, INTENT_UNBIND, INTENT_HELP, INTENT_CHAT

logger = logging.getLogger(__name__)


def handle_message(qq_id: str, message: str) -> list[str]:
    """
    处理一条 QQ 消息，返回要回复的内容（已经按 QQ 长度切分好）

    Args:
        qq_id: 发送消息的 QQ 号（string）
        message: 消息文本

    Returns:
        回复消息列表（每条 ≤ 400 字）
    """
    intent = intent_router.parse(message)
    logger.info(f"QQ [{qq_id}] '{message[:30]}' → intent={intent.type}, symbol={intent.symbol}, keyword={intent.keyword}")

    # 1. 绑定流程（不需要先绑定）
    if intent.type == INTENT_BIND:
        return _handle_bind(qq_id, intent.code)

    # 2. 绑定后才能用的功能：watchlist、analyze、history（需要拿 user 自选股等）
    # 其他不需要绑定：quote, help, chat, unbind

    # 3. 解绑
    if intent.type == INTENT_UNBIND:
        return _handle_unbind(qq_id)

    # 4. 帮助
    if intent.type == INTENT_HELP:
        return [intent_router.reply_for_help()]

    # 5. 查行情（不需要绑定）
    if intent.type == INTENT_QUOTE:
        return _handle_quote(qq_id, intent)

    # 6. 自选股（需要绑定）
    if intent.type == INTENT_WATCHLIST:
        return _handle_watchlist(qq_id)

    # 7. 技术分析（需要绑定，因为要给个性化建议）
    if intent.type == INTENT_ANALYZE:
        return _handle_analyze(qq_id, intent)

    # 8. 历史K线
    if intent.type == INTENT_HISTORY:
        return _handle_history(qq_id, intent)

    # 9. 兜底：闲聊
    return _handle_chat(qq_id, message)


# ==================== 各意图的具体处理 ====================

def _handle_bind(qq_id: str, code: Optional[str]) -> list[str]:
    if not code:
        return ["绑定格式: 绑定 123456（中间有空格，6位验证码）"]
    result = user_context.verify_and_bind_qq(qq_id, code)
    if result["success"]:
        return [f"✅ 绑定成功！\nQQ: {qq_id}\n用户: {result['data'].get('username', '?')}\n现在可以查自选股了"]
    return [f"❌ 绑定失败: {result['message']}\n先去网页登录 → 我的页面拿 6 位验证码"]


def _handle_unbind(qq_id: str) -> list[str]:
    user = user_context.lookup_user_by_qq(qq_id)
    if not user:
        return ["当前 QQ 未绑定任何账号"]
    return [f"📌 QQ {qq_id} 当前绑定用户: {user.get('username', '?')}\n解绑请去网页端 我的 → 解绑"]


def _require_bound(qq_id: str) -> Optional[str]:
    """检查用户是否已绑定，未绑定返回提示消息，已绑定返回 None"""
    user = user_context.lookup_user_by_qq(qq_id)
    if not user:
        return ("⚠️ 这个功能需要先绑定 QQ\n"
                "1️⃣ 去网页 stock-tracker 登录\n"
                "2️⃣ 进入「我的」页面生成验证码\n"
                "3️⃣ 在这发: 绑定 123456")
    return None


def _handle_quote(qq_id: str, intent) -> list[str]:
    symbol_or_keyword = intent.symbol or intent.keyword
    if not symbol_or_keyword:
        return ["请输入股票代码或名称，比如 600519、茅台"]
    data = get_quote(symbol_or_keyword)
    if not data:
        return [f"❌ 没找到 {symbol_or_keyword} 的行情"]

    name = data.get("名称", symbol_or_keyword)
    price = data.get("最新价", "N/A")
    change = data.get("涨跌幅", "N/A")
    return [f"📊 {name}({symbol_or_keyword})\n💰 最新价: {price}\n📈 涨跌幅: {change}%"]


def _handle_watchlist(qq_id: str) -> list[str]:
    err = _require_bound(qq_id)
    if err:
        return [err]
    user = user_context.lookup_user_by_qq(qq_id)
    favorites = user_context.get_user_watchlist(user["username"])

    if not favorites:
        return [f"📭 {user['username']} 的自选股为空\n去网页端添加吧～"]

    # 批量查行情
    lines = [f"⭐ {user['username']} 的自选股（共 {len(favorites)} 只）"]
    for fav in favorites[:10]:  # 限制 10 只
        sym = fav.get("symbol", "?")
        quote = get_quote(sym)
        if quote:
            name = quote.get("名称", sym)
            price = quote.get("最新价", "N/A")
            change = quote.get("涨跌幅", "N/A")
            lines.append(f"· {name}({sym}): {price} ({change}%)")
        else:
            lines.append(f"· {sym}: 数据获取失败")
    return llm_service.split_for_qq("\n".join(lines))


def _handle_analyze(qq_id: str, intent) -> list[str]:
    symbol_or_keyword = intent.symbol or intent.keyword
    if not symbol_or_keyword:
        return ["请告诉我要分析哪只股票，比如: 帮我分析下宁德时代"]

    # 取行情
    quote = get_quote(symbol_or_keyword)
    if not quote:
        return [f"❌ 没找到 {symbol_or_keyword} 的行情"]

    # 取历史
    records = get_history(symbol_or_keyword)
    if not records or len(records) < 20:
        return [f"⚠️ {symbol_or_keyword} 历史数据不足（{len(records) if records else 0} 条），无法做技术分析"]

    # 算技术指标
    prices = []
    for r in records:
        try:
            prices.append(float(r["close"]))
        except (KeyError, ValueError, TypeError):
            continue

    if len(prices) < 20:
        return ["⚠️ 有效价格数据不足"]

    analysis = analyze_stock(prices, symbol_or_keyword)
    name = quote.get("名称", symbol_or_keyword)
    price = quote.get("最新价", "N/A")
    change = quote.get("涨跌幅", "N/A")

    # 拼 context 给 LLM
    indicators = analysis.get("indicators", {})
    prediction = analysis.get("prediction")
    signal = analysis.get("signal", {})

    ctx = {
        "symbol": symbol_or_keyword,
        "name": name,
        "quote": f"最新价 {price}，涨跌幅 {change}%",
        "indicators": {
            "rsi": indicators.get("rsi"),
            "macd": indicators.get("macd", {}).get("hist"),
            "ma5": indicators.get("ma5"),
            "ma20": indicators.get("ma20"),
            "boll_pos": indicators.get("bollinger", {}),
            "signal": signal.get("signal"),
            "score": signal.get("score"),
        },
        "prediction": prediction,
    }

    # 调 LLM 生成自然语言分析
    user_msg = f"帮我分析 {name}({symbol_or_keyword})，结合技术指标和当前行情，{intent.keyword or ''}"
    reply = llm_service.chat(user_msg, scenario="stock_analyst", context=ctx)
    return llm_service.split_for_qq(reply)


def _handle_history(qq_id: str, intent) -> list[str]:
    symbol_or_keyword = intent.symbol or intent.keyword
    if not symbol_or_keyword:
        return ["请告诉我要看哪只股票的历史"]
    records = get_history(symbol_or_keyword)
    if not records:
        return [f"❌ 没找到 {symbol_or_keyword} 的历史数据"]
    lines = [f"📜 {symbol_or_keyword} 最近 {len(records)} 日行情"]
    for r in records[:10]:  # 最多展示 10 条
        date = r.get("date", "?")
        close = r.get("close", "?")
        lines.append(f"{date}: 收盘 {close}")
    return llm_service.split_for_qq("\n".join(lines))


def _handle_chat(qq_id: str, message: str) -> list[str]:
    """闲聊：让 LLM 自由发挥"""
    reply = llm_service.chat(message, scenario="chat", context={})
    return llm_service.split_for_qq(reply)
