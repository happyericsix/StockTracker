"""
chat_handler.py —— 站内聊天助手核心（LLM-first 架构）

架构：
  用户消息
    ↓
  规则快速判断（帮助）→ 走规则
    ↓ 不匹配
  LLM parse_intent 解析意图 → JSON 操作计划
    ↓
  按 action 分发到对应 handler
    ↓
  返回 replies

网页端聊天助手（Assistant 页面）通过后端 ChatService 调用本模块。
"""
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from collections import deque
from typing import Optional

import llm_service
from akshare_client import get_quote, get_history, search_stocks
from quant_model import analyze_stock

logger = logging.getLogger(__name__)
# ==================== 会话上下文 ====================

_conversations: dict[str, deque[dict]] = {}
_conv_lock = threading.Lock()
MAX_HISTORY = 6  # 保留最近 3 轮对话 (3 user + 3 assistant)


def _get_history(user_id: str) -> list[dict]:
    """获取用户最近的对话历史"""
    with _conv_lock:
        hist = _conversations.get(user_id, deque(maxlen=MAX_HISTORY))
        return list(hist)


def _save_exchange(user_id: str, user_msg: str, bot_msg: str):
    """保存一轮对话到上下文"""
    with _conv_lock:
        if user_id not in _conversations:
            _conversations[user_id] = deque(maxlen=MAX_HISTORY)
        hist = _conversations[user_id]
        hist.append({"role": "user", "content": user_msg[:200]})
        hist.append({"role": "assistant", "content": bot_msg[:400]})



def handle_message(user_id: str, message: str) -> list[str]:
    """
    处理一条聊天消息，返回要回复的内容（已按合适长度切分好）

    采用 LLM-first 架构：
    - 规则只处理确定性系统命令（帮助）
    - 其他所有意图交给 LLM 解析
    """
    message = message.strip()
    if not message:
        return []

    logger.info(f"chat [{user_id}] '{message[:30]}'")

    # ========== 阶段 1: 规则快速判断（确定性系统命令） ==========

    # 1.3 帮助 / 菜单
    if message in ["帮助", "help", "?", "？", "菜单", "help me", "怎么用"]:
        replies = [_help_text()]
        return replies

    # ========== 阶段 2: 快速路径（绕过 LLM parse_intent，省 1-2 秒） ==========

    # 2.1 明显是追问/闲聊 -> 直接走 chat
    followup_words = ["解释", "简单", "说人话", "通俗", "直白", "举个例子",
                      "还有吗", "然后呢", "为什么", "什么意思", "总结", "概括"]
    is_followup = any(p in message for p in followup_words) and len(message) <= 20
    if is_followup:
        replies = _handle_chat(user_id, message)
        replies_text = " | ".join(replies) if replies else ""
        _save_exchange(user_id, message, replies_text)
        return replies

    # 2.2 纯股票代码/简单查询 -> 直接 get_quote（不调 LLM）
    code_match = re.search(r'(?<![a-zA-Z])(\d{6})(?![a-zA-Z])', message)
    name_match = None
    fast_names = ["茅台", "宁德", "比亚迪", "五粮液", "海康威视", "海康", "平安", "招行", "工行", "中芯", "隆基", "美的", "格力", "恒瑞", "特斯拉", "苹果", "科大讯飞", "中兴", "京东方", "立讯", "紫金", "片仔癀", "韦尔", "三一", "万科"]
    for name in fast_names:
        if name in message:
            name_match = name
            break
    stripped_msg = re.sub(r'[，,。.\s]', '', message)
    is_pure_code = code_match and len(stripped_msg) == 6
    is_pure_name = name_match and len(message.strip()) <= 4
    if is_pure_code or is_pure_name:
        sym = code_match.group(1) if code_match else name_match
        replies = _handle_quote_multi([sym])
        replies_text = " | ".join(replies) if replies else ""
        _save_exchange(user_id, message, replies_text)
        return replies

    # ========== 阶段 3: LLM 意图解析 + 执行 ==========

    history = _get_history(user_id)
    intent = llm_service.parse_intent(message, history=history)
    logger.info(f"LLM intent: {intent}")

    action = intent.get("action", "chat")
    replies: list[str] = []

    if action == "get_quote":
        replies = _handle_quote_multi(intent.get("symbols", []) or
                                      [intent.get("symbol", "")])
    elif action == "analyze":
        replies = _handle_analyze(user_id,
                                  intent.get("symbol", ""),
                                  intent.get("question", ""))
    elif action == "history":
        replies = _handle_history(user_id,
                                  intent.get("symbol", ""),
                                  intent.get("days", 30))
    elif action == "watchlist":
        replies = _handle_watchlist(user_id)
    elif action == "compare":
        replies = _handle_compare(user_id,
                                  intent.get("symbols", []),
                                  intent.get("aspects", ["price", "trend"]))
    elif action == "chat":
        replies = _handle_chat(user_id, intent.get("reply", message))
    else:
        # 未知 action，降级为闲聊
        replies = _handle_chat(user_id, message)

    # 存会话上下文（用于后续追问）
    replies_text = " | ".join(replies) if replies else ""
    _save_exchange(user_id, message, replies_text)


    return replies


# ==================== 行情查询（支持多只股票） ====================

def _handle_quote_multi(symbols: list[str]) -> list[str]:
    """查询多只股票行情（并发请求，大幅提速）"""
    if not symbols or (len(symbols) == 1 and not symbols[0]):
        return ["请输入股票代码或名称，比如 600519、茅台"]

    # 去重 + 去空
    clean = list(dict.fromkeys(s.strip() for s in symbols if s.strip()))

    # 并发请求所有股票行情
    results: list[tuple[str, Optional[dict]]] = []
    with ThreadPoolExecutor(max_workers=min(len(clean), 5)) as executor:
        future_map = {executor.submit(get_quote, sym): sym for sym in clean}
        for future in as_completed(future_map):
            sym = future_map[future]
            try:
                data = future.result()
                results.append((sym, data))
            except Exception as e:
                logger.warning(f"get_quote 并发异常: {sym} -> {e}")
                results.append((sym, None))

    # 按原始顺序输出
    sym_order = {s: i for i, s in enumerate(clean)}
    results.sort(key=lambda x: sym_order.get(x[0], 999))

    lines = []
    errors = []
    for sym, data in results:
        if data:
            name = data.get("名称", sym)
            price = data.get("最新价", "N/A")
            change = data.get("涨跌幅", "N/A")
            lines.append(f"📊 {name}\n   💰 {price}  📈 {change}%")
        else:
            errors.append(sym)

    if not lines:
        return [f"❌ 没找到: {', '.join(errors)}"]

    out = "\n\n".join(lines)
    if errors:
        out += f"\n\n⚠️ 未找到: {', '.join(errors)}"
    return llm_service.split_replies(out)

# ==================== 自选股 ====================

def _handle_watchlist(user_id: str) -> list[str]:
    """查看自选股"""
    return ["📌 自选股请在网页端首页查看，这里暂不支持"]
# ==================== 技术分析 ====================

def _handle_analyze(user_id: str, symbol: str, question: str) -> list[str]:
    """技术分析"""
    if not symbol:
        return ["请告诉我要分析哪只股票，比如: 帮我分析下宁德时代"]

    # 取行情
    # Resolve stock name to code
    original = symbol
    if not (symbol.isdigit() or any(symbol.lower().startswith(p) for p in ["sh", "sz", "bj", "hk"])):
        results = search_stocks(symbol)
        if results:
            symbol = results[0].get("code", symbol)
            logger.info("Resolved name %s -> code %s", original, symbol)

    quote = get_quote(symbol)
    if not quote:
        return [f"❌ 没找到 {symbol} 的行情"]

    # 取历史
    records = get_history(symbol)
    if not records or len(records) < 20:
        return [f"⚠️ {symbol} 历史数据不足（{len(records) if records else 0} 条），无法做技术分析"]

    # 算技术指标
    prices = []
    for r in records:
        try:
            prices.append(float(r["close"]))
        except (KeyError, ValueError, TypeError):
            continue

    if len(prices) < 20:
        return ["⚠️ 有效价格数据不足"]

    analysis = analyze_stock(prices, symbol)
    name = quote.get("名称", symbol)
    price = quote.get("最新价", "N/A")
    change = quote.get("涨跌幅", "N/A")

    # ===== Build compact context for LLM =====
    indicators = analysis.get("indicators", {})
    prediction = analysis.get("prediction", {})
    signal = analysis.get("signal", {})
    stats = analysis.get("stats", {})

    rsi_val = indicators.get("rsi", "?")
    macd_hist = indicators.get("macd", {}).get("hist", 0)
    ma5 = indicators.get("ma5", "?")
    ma20 = indicators.get("ma20", "?")
    signal_score = signal.get("score", 0)

    # Compact quote line
    quote_line = f"{name}({symbol}) {price} {change}%"

    # Compact indicators: only the essentials
    rsi_str = f"RSI{rsi_val}"
    if isinstance(rsi_val, (int, float)):
        rsi_str += "(??)" if rsi_val > 70 else ("(??)" if rsi_val < 30 else "")
    macd_str = f"MACD{'??' if isinstance(macd_hist,(int,float)) and macd_hist>0 else '??'}"
    ma_str = f"MA??{'??' if isinstance(ma5,(int,float)) and isinstance(ma20,(int,float)) and ma5>ma20 else '??'}"
    indicator_line = f"{rsi_str} | {macd_str} | {ma_str} | ???{signal_score:+.0f}"

    # Compact ML predictions
    ml_line = ""
    if prediction and "windows" in prediction:
        parts = []
        for label, tag in [("short", "?"), ("mid", "?"), ("long", "?")]:
            w = prediction["windows"].get(label, {})
            if w and "predicted_change_pct" in w:
                parts.append(f"{tag}{w['predicted_change_pct']:+.1f}%")
        consensus = prediction.get("consensus", "?")
        confidence = prediction.get("confidence", "?")
        ml_line = f"??: {' '.join(parts)} | ??{consensus}({confidence})"

    # Compact transformer
    tf = analysis.get("transformer")
    tf_line = f"Transformer: {tf['predicted_change_pct']:+.1f}%" if tf and tf.get("predicted_change_pct") else ""

    # Compact RL
    rl = analysis.get("rl_strategy")
    rl_line = f"RL??: {rl['total_return_pct']:+.1f}%" if rl else ""

    # Assemble compact context
    ctx_parts = [quote_line, indicator_line]
    if ml_line:
        ctx_parts.append(ml_line)
    if tf_line:
        ctx_parts.append(tf_line)
    if rl_line:
        ctx_parts.append(rl_line)

    ctx = {"data": "\n".join(ctx_parts), "symbol": symbol, "name": name}

    # ? LLM ????????
    user_msg = f"?? {name}({symbol})"
    if question:
        user_msg += f": {question}"
    reply = llm_service.chat(user_msg, scenario="stock_analyst", context=ctx, history=_get_history(user_id))
    return llm_service.split_replies(reply)



# ==================== 历史K线 ====================

def _handle_history(user_id: str, symbol: str, days: int = 30) -> list[str]:
    """查看历史K线，用 LLM 总结成人话"""
    if not symbol:
        return ["请告诉我要看哪只股票的历史"]

    records = get_history(symbol)
    if not records:
        return [f"❌ 没找到 {symbol} 的历史数据"]

    display = records[:min(len(records), days)]
    name = symbol
    quote = get_quote(symbol)
    if quote:
        name = quote.get("名称", symbol)

    # 计算关键指标
    closes = []
    for r in display:
        try:
            closes.append(float(r["close"]))
        except (ValueError, KeyError):
            continue

    if len(closes) < 5:
        return [f"⚠️ {name} 历史数据不足"]

    start_price = closes[0]
    end_price = closes[-1]
    total_change = (end_price - start_price) / start_price * 100
    high = max(closes)
    low = min(closes)

    # 近 5 日趋势
    recent = closes[-5:]
    up_days = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i-1])

    # 最近几天的摘要
    recent_summary = []
    for i, r in enumerate(reversed(display[:5])):
        recent_summary.append(f"{r['date']}: 收盘 {r['close']}")

    context = {
        "name": name,
        "symbol": symbol,
        "quote": f"最新价 {quote.get('最新价', '?')}，涨跌幅 {quote.get('涨跌幅', '?')}%" if quote else "",
        "history_summary": (
            f"近{len(display)}日: 从 {start_price:.2f} 到 {end_price:.2f}，"
            f"累计 {'涨' if total_change >= 0 else '跌'} {abs(total_change):.1f}%；"
            f"最高 {high:.2f}，最低 {low:.2f}；"
            f"近5日 {up_days} 涨 {4-up_days} 跌。"
            f"最近几天: {'; '.join(recent_summary)}"
        ),
    }

    prompt = f"用一两句大白话总结 {name} 近{len(display)}天走势，普通股民能看懂。"
    reply = llm_service.chat(prompt, scenario="stock_analyst", context=context, max_tokens=200)
    return llm_service.split_replies(reply)


# ==================== 对比多只股票 ====================

def _handle_compare(user_id: str, symbols: list[str], aspects: list[str]) -> list[str]:
    """对比多只股票（并发获取行情）"""
    if not symbols:
        return ["请告诉我要对比哪些股票，比如: 对比茅台和五粮液"]

    clean = list(dict.fromkeys(s.strip() for s in symbols if s.strip()))

    # 并发获取行情
    data_list = []
    with ThreadPoolExecutor(max_workers=min(len(clean), 5)) as executor:
        future_map = {executor.submit(get_quote, sym): sym for sym in clean}
        for future in as_completed(future_map):
            sym = future_map[future]
            try:
                data = future.result()
                if data:
                    data_list.append({"query": sym, "data": data})
            except Exception as e:
                logger.warning(f"compare get_quote 异常: {sym} -> {e}")

    if not data_list:
        return ["❌ 都没找到这些股票的数据"]

    # 按原始顺序排序
    sym_order = {s: i for i, s in enumerate(clean)}
    data_list.sort(key=lambda x: sym_order.get(x["query"], 999))

    # 让 LLM 组织对比分析
    prompt = f"""对比以下 {len(data_list)} 只股票的特点，简洁 150 字内:
{json.dumps(data_list, ensure_ascii=False, indent=2)}
用户关注: {', '.join(aspects) if aspects else '价格和趋势'}
"""
    reply = llm_service.chat(prompt, scenario="chat", max_tokens=300)
    return llm_service.split_replies(reply)

# ==================== 闲聊 ====================

def _handle_chat(user_id: str, message: str) -> list[str]:
    """闲聊：让 LLM 自由发挥（带对话上下文）"""
    reply = llm_service.chat(message, scenario="chat", context={}, history=_get_history(user_id))
    return llm_service.split_replies(reply)


# ==================== 帮助菜单 ====================

def _help_text() -> str:
    """帮助菜单"""
    return (
        "🤖 股小盯 · 命令菜单\n"
        "\n"
        "📊 查行情\n"
        "  · 600519 / 茅台 / 宁德\n"
        "  · 宁德茅台，海康 → 同时查多只\n"
        "\n"
        "📈 技术分析\n"
        "  · 宁德时代能买吗\n"
        "  · 帮我分析下 002594\n"
        "\n"
        "🔬 对比分析\n"
        "  · 对比茅台和五粮液\n"
        "\n"
        "💡 直接发股票名或代码就能查～"
    )