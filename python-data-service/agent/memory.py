"""对话记忆与上下文预算。

改造前的事实（值得记下来，因为这是用户直接能感觉到的 bug 来源）：

- `app.py` 调的是 `run_agent(user_id, message)`，**没传 history**；
- Java 的 `ChatService.callLlm` 只发 `user_id` + `message`，也从来不发历史；
- `run_agent` 的 `user_id` 参数在函数体里从未被使用（代码注释自己写着"是个装饰"）。

结果就是每条消息都交给一个全新、失忆的 agent。用户说"贵州茅台现在多少钱"，
再问"它呢？""把止损改成 5%"，agent 完全不知道"它"是谁、要改哪个策略。

这里做两件事，都是**确定性**的（不额外调用 LLM，因此不增加延迟和不确定性）：

1. `normalize_history`：只信任 user / assistant 两种角色，清洗非法结构。
   tool / system 这类中间过程不进历史 —— Java 侧只落 CHAT_USER / CHAT_BOT，
   但接口是公开的，不能假设调用方一定干净。
2. `build_messages`：在字符预算内取最近若干条，超预算时从最旧的开始丢，
   并在 system prompt 上追加一句"更早的对话已省略"，让模型知道这不是全部历史，
   而不是误以为用户从来没提过股票名。

字符预算用字符数而不是 token 数：DeepSeek 中文大致 1 token ≈ 1 字符，量级足够判断，
而且不需要引入 tokenizer 依赖。
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

# 送进上下文的历史消息条数上限（服务端这一层兜底，Java 侧也会截断一次）
MAX_HISTORY_MESSAGES = 12
# 历史部分的总字符预算
MAX_HISTORY_CHARS = 6000
# 单条历史消息的字符上限：用户可能贴一整份回测报告进来
MAX_MESSAGE_CHARS = 2000

ALLOWED_ROLES = ("user", "assistant")

BUDGET_NOTE = "（注：更早的对话已按上下文预算省略，如需历史信息请直接向用户确认。）"


def normalize_history(history: Any) -> list[dict]:
    """把外部传入的 history 清洗成 [{role, content}]，非法项直接丢弃。"""
    if not isinstance(history, (list, tuple)):
        return []

    cleaned: list[dict] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in ALLOWED_ROLES:
            continue
        content = item.get("content")
        if not isinstance(content, str):
            if content is None:
                continue
            content = str(content)
        content = content.strip()
        if not content:
            continue
        if len(content) > MAX_MESSAGE_CHARS:
            content = content[:MAX_MESSAGE_CHARS] + "…（已截断）"
        cleaned.append({"role": role, "content": content})
    return cleaned


def window_history(
    history: Iterable[dict],
    max_messages: int = MAX_HISTORY_MESSAGES,
    max_chars: int = MAX_HISTORY_CHARS,
) -> tuple[list[dict], bool]:
    """取最近若干条历史。

    Returns:
        (窗口内的历史, 是否丢弃过更早的消息)
    """
    items = list(history or [])
    if not items:
        return [], False

    allowed = max(0, int(max_messages))
    window: list[dict] = []
    used = 0
    for item in reversed(items):
        if allowed and len(window) >= allowed:
            break
        size = len(item.get("content") or "")
        # 至少保留最后一条，避免预算太小导致完全失忆
        if window and used + size > max_chars:
            break
        window.append(item)
        used += size
    window.reverse()
    return window, len(window) < len(items)


def build_messages(
    system_prompt: str,
    history: Any = None,
    message: str = "",
    max_messages: int = MAX_HISTORY_MESSAGES,
    max_chars: int = MAX_HISTORY_CHARS,
    context_block: str = None,
) -> list[dict]:
    """构造一轮 agent 的初始 messages（system + 历史窗口 + 当前消息）。

    `context_block` 是记忆层渲染出来的文本（今天几号 + 前情提要，见 agent/recall.py）。
    它拼在 system 里而不是作为单独一条 system 消息：兼容性更稳（部分兼容端点对
    多条 system 消息的处理不一致），而且位置固定在最前面，方便排查提示词问题。
    """
    window, dropped = window_history(normalize_history(history), max_messages, max_chars)
    system = system_prompt
    if dropped:
        system = f"{system_prompt}\n\n{BUDGET_NOTE}"
    if context_block:
        system = f"{system}\n\n{context_block}"

    messages: list[dict] = [{"role": "system", "content": system}]
    messages.extend(window)
    messages.append({"role": "user", "content": message})
    return messages


def history_stats(history: Any) -> dict:
    """给日志用的轻量统计：条数与总字符数。"""
    cleaned = normalize_history(history)
    return {
        "messages": len(cleaned),
        "chars": sum(len(item["content"]) for item in cleaned),
    }


def last_user_mention(history: Any) -> Optional[str]:
    """最近一条用户消息（调试用：确认多轮指代时上下文里到底有什么）。"""
    for item in reversed(normalize_history(history)):
        if item["role"] == "user":
            return item["content"]
    return None
