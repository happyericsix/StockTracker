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
- search_history: 搜历史对话（向量数据库）
"""
import logging
import threading
import time
from typing import Optional

import intent_router
import llm_service
import user_context
from akshare_client import get_quote, get_history, search_stocks
from quant_model import analyze_stock
from intent_router import INTENT_QUOTE, INTENT_ANALYZE, INTENT_HISTORY, INTENT_WATCHLIST, \
    INTENT_BIND, INTENT_UNBIND, INTENT_HELP, INTENT_CHAT, INTENT_SEARCH_HISTORY

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

    # 搜历史对话（在主流程前判断，因为不需要走数据查询）
    if intent.type == INTENT_SEARCH_HISTORY:
        replies = _handle_search_history(qq_id, message)
        _save_to_vector_db(qq_id, message, replies)
        return replies

    # 1. 绑定流程（不需要先绑定）
    if intent.type == INTENT_BIND:
        replies = _handle_bind(qq_id, intent.code)
        if _should_save(replies): _save_to_vector_db(qq_id, message, replies)
        return replies

    # 2. 绑定后才能用的功能：watchlist、analyze、history（需要拿 user 自选股等）
    # 其他不需要绑定：quote, help, chat, unbind

    # 3. 解绑
    if intent.type == INTENT_UNBIND:
        replies = _handle_unbind(qq_id)
        if _should_save(replies): _save_to_vector_db(qq_id, message, replies)
        return replies

    # 4. 帮助
    if intent.type == INTENT_HELP:
        replies = [intent_router.reply_for_help()]
        if _should_save(replies): _save_to_vector_db(qq_id, message, replies)
        return replies

    # 5. 查行情（不需要绑定）
    if intent.type == INTENT_QUOTE:
        replies = _handle_quote(qq_id, intent)
        if _should_save(replies): _save_to_vector_db(qq_id, message, replies)
        return replies

    # 6. 自选股（需要绑定）
    if intent.type == INTENT_WATCHLIST:
        replies = _handle_watchlist(qq_id)
        if _should_save(replies): _save_to_vector_db(qq_id, message, replies)
        return replies

    # 7. 技术分析（需要绑定，因为要给个性化建议）
    if intent.type == INTENT_ANALYZE:
        replies = _handle_analyze(qq_id, intent)
        if _should_save(replies): _save_to_vector_db(qq_id, message, replies)
        return replies

    # 8. 历史K线
    if intent.type == INTENT_HISTORY:
        replies = _handle_history(qq_id, intent)
        if _should_save(replies): _save_to_vector_db(qq_id, message, replies)
        return replies

    # 9. 兜底：闲聊
    replies = _handle_chat(qq_id, message)
    if _should_save(replies): _save_to_vector_db(qq_id, message, replies)
    return replies


# 修复: 给所有 handler 加错误过滤，避免存错误回复
def _should_save(replies: list[str]) -> bool:
    """判断是否要存这次的对话（不要存错误回复）"""
    if not replies:
        return False
    text = " ".join(replies)
    # 不要存错误/空回复
    bad_patterns = ["⚠️", "❌", "📭", "ERROR", "暂时不可用", "网络错误", "处理失败"]
    return not any(p in text for p in bad_patterns)


# ==================== 向量数据库：自动存储 + 搜索 ====================

def _save_to_vector_db(qq_id: str, user_msg: str, replies: list[str]):
    """异步把对话存到向量数据库（不阻塞回复）"""
    def _do_save():
        try:
            import vector_store
            store = vector_store.get_store("chat_history")
            ts = int(time.time())
            # 存用户消息
            store.add(
                doc_id=f"{qq_id}-u-{ts}",
                text=user_msg,
                metadata={"qq_id": qq_id, "role": "user", "ts": ts, "ts_human": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))},
            )
            # 存机器人回复（多条合并）
            if replies:
                store.add(
                    doc_id=f"{qq_id}-a-{ts}",
                    text=" || ".join(replies),
                    metadata={"qq_id": qq_id, "role": "assistant", "ts": ts, "ts_human": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))},
                )
            logger.debug(f"已存入向量库: qq={qq_id}, user_msg={user_msg[:30]}")
        except Exception as e:
            logger.error(f"存向量库失败: {e}")

    # 用后台线程跑，不阻塞主流程
    threading.Thread(target=_do_save, daemon=True).start()


def _handle_search_history(qq_id: str, original_msg: str) -> list[str]:
    """搜历史对话"""
    # 提取搜索关键词
    keyword = original_msg
    for p in ["之前问过", "以前问过", "我之前问", "我以前问", "我问过的", "之前聊过", "上次问过", "历史对话", "历史记录", "我", "什么", "哪些"]:
        keyword = keyword.replace(p, "")
    keyword = keyword.strip().replace("?", "").replace("？", "").replace("。", "").replace("？", "")
    if not keyword:
        keyword = "股票"  # 默认搜股票相关

    try:
        import vector_store
        store = vector_store.get_store("chat_history")
        # 只搜这个用户的
        results = store.search(
            query=keyword,
            top_k=5,
            where={"qq_id": qq_id},
        )
    except Exception as e:
        logger.error(f"搜历史失败: {e}", exc_info=True)
        return [f"⚠️ 向量数据库错误: {str(e)[:100]}"]

    if not results:
        return [f"📭 没找到你关于「{keyword}」的历史对话"]

    lines = [f"🔍 你之前关于「{keyword}」的对话："]
    for i, r in enumerate(results, 1):
        meta = r.get("metadata", {})
        role = meta.get("role", "?")
        ts = meta.get("ts_human", "")
        icon = "👤 你" if role == "user" else "🤖 机器人"
        text = r.get("text", "").replace(" || ", " | ")
        # 控制单条长度
        if len(text) > 80:
            text = text[:80] + "..."
        lines.append(f"{i}. {icon} ({ts})")
        lines.append(f"   {text}")
    return lines


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
