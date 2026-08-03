"""
llm_service.py —— LLM 客户端（DeepSeek）

职责：
- 加载 prompts 模板
- 调用 DeepSeek Chat API（兼容 OpenAI 协议）
- 拼装 system + user prompt，调用 LLM 生成回复

环境变量（优先级：系统 env > .env 文件）：
- DEEPSEEK_API_KEY: 必填
- DEEPSEEK_BASE_URL: 默认 https://api.deepseek.com
- DEEPSeek_MODEL: 默认 deepseek-chat
"""
import logging
import os
import re
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# ===== 加载 .env 文件（stdlib 实现，不需要 python-dotenv） =====
def _load_dotenv():
    """从项目根的 .env 文件加载环境变量（仅当系统 env 还没有时）"""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # 系统环境变量优先（已设的不覆盖）
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception as e:
        logger.warning(f"加载 .env 失败: {e}")


_load_dotenv()

# ===== 配置 =====
API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
TIMEOUT = int(os.getenv("DEEPSEEK_TIMEOUT", "30"))

# ===== Prompt 加载 =====
PROMPT_DIR = Path(__file__).parent / "prompts"
_PROMPT_CACHE: dict[str, str] = {}


def _load_prompt(name: str) -> str:
    """加载 prompts/{name}.md 文件内容"""
    if name in _PROMPT_CACHE:
        return _PROMPT_CACHE[name]
    path = PROMPT_DIR / f"{name}.md"
    if not path.exists():
        logger.warning(f"Prompt 文件不存在: {path}")
        return ""
    content = path.read_text(encoding="utf-8")
    _PROMPT_CACHE[name] = content
    return content


def _is_available() -> bool:
    return bool(API_KEY) and API_KEY != "your-api-key-here"


# ===== 核心调用 =====

def chat(
    user_message: str,
    scenario: str = "stock_analyst",
    context: Optional[dict] = None,
    temperature: float = 0.6,
    max_tokens: int = 800,
) -> str:
    """
    调用 LLM 生成回复

    Args:
        user_message: 用户的原始问题
        scenario: prompt 场景名 (stock_analyst / chat)
        context: 上下文数据 (symbol, name, quote, indicators, prediction...)
        temperature: 温度参数 (0-1)，越低越确定
        max_tokens: 最大输出 token

    Returns:
        LLM 生成的回复文本。如果 API key 缺失或调用失败，返回降级回复。
    """
    if not _is_available():
        return _fallback_reply(user_message, context, reason="LLM API key 未配置")

    system_prompt = _load_prompt(scenario)
    if not system_prompt:
        system_prompt = _load_prompt("chat")

    # 拼装 user prompt：context 数据 + 原始问题
    user_prompt = _build_user_prompt(user_message, context or {})

    # 构造请求
    url = f"{BASE_URL}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        logger.info(f"LLM 调用成功: scenario={scenario}, tokens={data.get('usage', {}).get('total_tokens', '?')}")
        return content
    except requests.exceptions.Timeout:
        logger.error("LLM 调用超时")
        return _fallback_reply(user_message, context, reason="LLM 响应超时")
    except requests.exceptions.HTTPError as e:
        logger.error(f"LLM HTTP 错误: {e.response.status_code} {e.response.text[:200]}")
        return _fallback_reply(user_message, context, reason=f"LLM 错误({e.response.status_code})")
    except Exception as e:
        logger.error(f"LLM 调用异常: {e}")
        return _fallback_reply(user_message, context, reason="LLM 调用失败")


def _build_user_prompt(user_message: str, context: dict) -> str:
    """把 context 数据拼成 user prompt"""
    parts = []

    # 数据部分
    if context:
        parts.append("## 数据")
        if "symbol" in context:
            parts.append(f"- 股票代码: {context['symbol']}")
        if "name" in context:
            parts.append(f"- 股票名称: {context['name']}")
        if "quote" in context:
            parts.append(f"- 实时行情: {context['quote']}")
        if "indicators" in context:
            parts.append(f"- 技术指标: {context['indicators']}")
        if "prediction" in context:
            parts.append(f"- 模型预测: {context['prediction']}")
        if "history_summary" in context:
            parts.append(f"- 历史摘要: {context['history_summary']}")
        if "watchlist" in context and context["watchlist"]:
            parts.append(f"- 用户的自选股: {context['watchlist']}")
        parts.append("")

    # 用户问题
    parts.append("## 用户问题")
    parts.append(user_message)

    return "\n".join(parts)


def _fallback_reply(user_message: str, context: dict, reason: str) -> str:
    """LLM 不可用时的降级回复：直接拼数据，不调模型"""
    logger.info(f"降级回复: reason={reason}")

    if not context:
        return f"（{reason}，先用模板回复你）\n" + _simple_greet(user_message)

    lines = []
    if "name" in context and "symbol" in context:
        lines.append(f"📊 {context['name']}({context['symbol']})")
    if "quote" in context:
        lines.append(f"💰 {context['quote']}")
    if "indicators" in context:
        indi = context["indicators"]
        if isinstance(indi, dict):
            if "signal" in indi:
                lines.append(f"📡 信号: {indi['signal']}")
            if "rsi" in indi:
                lines.append(f"📈 RSI: {indi['rsi']}")
    if "prediction" in context and context["prediction"]:
        pred = context["prediction"]
        if isinstance(pred, dict) and "predicted_change_pct" in pred:
            lines.append(f"🔮 模型预测明日: {pred['predicted_change_pct']:+.2f}%")

    if not lines:
        return f"（{reason}）\n" + _simple_greet(user_message)

    lines.append("\n以上仅为技术面参考，不构成投资建议")
    return "\n".join(lines)


def _simple_greet(msg: str) -> str:
    """闲聊兜底"""
    msg = msg.strip()
    if any(k in msg for k in ["你好", "hi", "hello", "在吗"]):
        return "你好！我是股小盯 📊 发股票代码或名称给你，比如 600519、宁德时代"
    if "你是" in msg or "你是什么" in msg:
        return "我是股小盯，一个股票对话机器人，可以帮你查行情、做技术分析"
    if "帮助" in msg or "help" in msg.lower() or "?" in msg:
        return "试试这些：\n📌 茅台行情\n📌 600519 技术分析\n📌 我的自选股\n📌 绑定 888888（绑定 QQ）"
    return "收到～可以试试问「茅台行情」「宁德时代能买吗」"


# ===== 工具方法：消息切分 =====

def split_for_qq(text: str, max_len: int = 400) -> list[str]:
    """
    把长文本切成 QQ 单条消息（默认 400 字/条）

    优先在换行处切，其次在句号处切
    """
    if len(text) <= max_len:
        return [text]

    chunks = []
    remaining = text
    while len(remaining) > max_len:
        # 找最近的换行
        cut_at = remaining.rfind("\n", 0, max_len)
        if cut_at < max_len // 2:
            # 找不到合适的换行，找句号
            cut_at = remaining.rfind("。", 0, max_len)
        if cut_at < max_len // 2:
            # 还找不到，硬切
            cut_at = max_len
        chunks.append(remaining[:cut_at].rstrip())
        remaining = remaining[cut_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks
