"""
llm_service.py —— LLM 客户端（DeepSeek）

职责：
- 加载 prompts 模板
- 调用 DeepSeek Chat API（兼容 OpenAI 协议）
- 拼装 system + user prompt，调用 LLM 生成回复
- parse_intent: LLM 意图识别，把用户消息解析成 JSON 操作计划

环境变量（优先级：系统 env > .env 文件）：
- DEEPSEEK_API_KEY: 必填
- DEEPSEEK_BASE_URL: 默认 https://api.deepseek.com
- DEEPSEEK_MODEL: 默认 deepseek-chat
"""
import json
import logging
import os
import time
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
            value = value.strip().strip("\"'").strip("'")
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

# 503/超时退避重试（2026-10-15 场景实验室发现）：DeepSeek 高峰期会瞬时 503/读超时
# （官方错误码文档定性为过载、建议重试），而盯盘/委员会零重试 + 30s 超时意味着
# agent 恰好在市场最需要判断的时刻失明（10-15 上午全部 LLM 降级即此因）。
RETRY_ATTEMPTS = int(os.getenv("DEEPSEEK_RETRY", "3"))
RETRY_BACKOFF_S = float(os.getenv("DEEPSEEK_RETRY_BACKOFF", "2.0"))
RETRYABLE_STATUS = {502, 503, 504, 429}

# ==================== 备用供应商转移（2026-10-15） ====================
# 行业标准分层兜底（LiteLLM Router / OpenRouter 同款思路）：重试扛瞬时抖动，
# 持续过载则冷却主供应商、切备用（OpenAI 兼容端点：硅基流动/昆仑/Moonshot/GLM 皆可）。
# 配齐三个变量即启用：LLM_FALLBACK_BASE_URL / LLM_FALLBACK_API_KEY / LLM_FALLBACK_MODEL。
# 推理型模型思考开关（2026-10-15）：flash/v4-pro 默认带思考，盯盘 300 token 的预算
# 会被 reasoning 烧光、正式回答为空（当天实测：全部"模型返回空内容"降级）。
# 默认关闭思考（盯盘要秒级+短格式输出）；要开设 DEEPSEEK_DISABLE_THINKING=0。
DISABLE_THINKING = os.getenv("DEEPSEEK_DISABLE_THINKING", "1") not in ("0", "false", "False")

FALLBACK_BASE_URL = os.getenv("LLM_FALLBACK_BASE_URL", "").rstrip("/")
FALLBACK_API_KEY = os.getenv("LLM_FALLBACK_API_KEY", "")
FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "")
PROVIDER_COOLDOWN_S = float(os.getenv("LLM_PROVIDER_COOLDOWN", "600"))

def _providers() -> list:
    """(名字, base_url, key, model) 序列：主 DeepSeek + 可选备用。"""
    ps = [("primary", BASE_URL, API_KEY, MODEL)]
    if FALLBACK_BASE_URL and FALLBACK_API_KEY and FALLBACK_MODEL:
        ps.append(("fallback", FALLBACK_BASE_URL, FALLBACK_API_KEY, FALLBACK_MODEL))
    return ps

# 内存态冷却表：重启归零无害（最多多打几次注定失败的请求）。
_provider_cooldown_until: dict = {}

def _mark_cooldown(name: str):
    import time as _t
    _provider_cooldown_until[name] = _t.time() + PROVIDER_COOLDOWN_S
    logger.warning("LLM 供应商 %s 进入 %.0f 分钟冷却（持续过载）", name, PROVIDER_COOLDOWN_S / 60)

def _post_chat(payload: dict) -> dict:
    """跨供应商的 chat/completions：重试扛抖动（单供应商内），冷却+切换扛持续故障。
    返回解析后的 JSON 体（model 字段已按实际供应商覆盖）。全部供应商失败时抛最后异常。"""
    import time as _t
    import copy
    now = _t.time()
    providers = [p for p in _providers() if _provider_cooldown_until.get(p[0], 0) <= now]
    if not providers:                      # 全在冷却：仍试主供应商（冷却不是停机）
        providers = [_providers()[0]]
    last_exc = None
    for name, base, key, model in providers:
        try:
            body = dict(payload, model=model)
            if name == "primary" and DISABLE_THINKING:
                body.setdefault("reasoning_effort", "none")
            resp = _post_with_retry(f"{base}/v1/chat/completions",
                                    json=body,
                                    headers={"Authorization": f"Bearer {key}",
                                             "Content-Type": "application/json"},
                                    timeout=TIMEOUT)
            data = resp.json()
            data["_provider"] = name       # 供日志/计量知道这次是谁答的
            return data
        except Exception as e:             # noqa: BLE001 —— 换下一个供应商，全部挂了抛最后一个
            last_exc = e
            logger.warning("LLM 供应商 %s 失败：%s", name, str(e)[:120])
            _mark_cooldown(name)
    raise last_exc


def _post_with_retry(url, **kwargs):
    """带指数退避的 POST：只对过载类状态码与网络异常重试，4xx 语义错误立即抛。"""
    import time as _t
    last_exc = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.post(url, **kwargs)
            if resp.status_code in RETRYABLE_STATUS and attempt < RETRY_ATTEMPTS - 1:
                wait = RETRY_BACKOFF_S * (2 ** attempt)
                logger.warning("LLM %d（第 %d 次），%.1fs 后重试", resp.status_code, attempt + 1, wait)
                _t.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            if attempt < RETRY_ATTEMPTS - 1:
                wait = RETRY_BACKOFF_S * (2 ** attempt)
                logger.warning("LLM 网络异常（第 %d 次 %s），%.1fs 后重试", attempt + 1, type(e).__name__, wait)
                _t.sleep(wait)
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("unreachable")

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
    if API_KEY and API_KEY != "your-api-key-here":
        return True
    return bool(FALLBACK_API_KEY)   # 主 key 缺失但配了备用：仍然可用


# ===== 核心调用 =====

def chat(
    user_message: str,
    scenario: str = "stock_analyst",
    context: Optional[dict] = None,
    history: Optional[list[dict]] = None,
    temperature: float = 0.6,
    max_tokens: int = 800,
) -> str:
    """
    调用 LLM 生成回复

    Args:
        user_message: 用户的原始问题
        scenario: prompt 场景名 (stock_analyst / chat)
        context: 上下文数据 (symbol, name, quote, indicators, prediction...)
        history: 可选，最近对话历史 [{"role":"user","content":"..."}, ...]
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

    # 构造消息列表（含历史上下文）
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_prompt})

    # 构造请求
    url = f"{BASE_URL}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }

    try:
        data = _post_chat(payload)
        if data.get("_provider") == "fallback":
            logger.info("LLM 由备用供应商应答（主供应商冷却中）")
        content = data["choices"][0]["message"]["content"].strip()
        logger.info(f"LLM 调用成功: scenario={scenario}, tokens={data.get('usage', {}).get('total_tokens', '?')}")
        # Enforce short responses for stock_analyst
        if scenario == "stock_analyst" and len(content) > 1000:
            reply_lines = content.strip().split("\n")
            short_lines = []
            char_count = 0
            for line in reply_lines:
                if char_count + len(line) > 150:
                    break
                short_lines.append(line)
                char_count += len(line) + 1
            content = "\n".join(short_lines)

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
        return "试试这些：\n📌 茅台行情\n📌 600519 技术分析\n📌 我的自选股"
    return "收到～可以试试问「茅台行情」「宁德时代能买吗」"


# ===== 工具方法：消息切分 =====

def split_replies(text: str, max_len: int = 1600) -> list[str]:
    """
    把长文本切分成多条回复（默认 400 字/条）

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


def chat_completion(messages, tools=None, temperature=0.2, max_tokens=1200):
    """agent 循环用的底层调用：返回 choices[0]，**同时把用量记进计量（P1）**。

    为什么要在这里记而不是在调用方：这是所有 LLM 调用的必经之路（agent 循环、摘要巩固、
    评测都走它）。记在调用方就意味着"以后新增的调用点忘了记"—— 而成本数字一旦漏记，
    比没有数字更糟（会让人以为很便宜）。
    """
    if not _is_available():
        return {"message": {"role": "assistant", "content": "AI 服务暂不可用"}}
    payload = {"model": MODEL, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        payload["tools"] = tools
    started = time.time()
    try:
        body = _post_chat(payload)
        if body.get("_provider") == "fallback":
            logger.info("LLM(chat_completion) 由备用供应商应答")
        body.pop("_provider", None)   # 计量/调用方不认这个字段
    except Exception:
        _record_llm_usage(ok=False, latency_ms=int((time.time() - started) * 1000))
        raise
    _record_llm_usage(ok=True, latency_ms=int((time.time() - started) * 1000), body=body)
    choice = body["choices"][0]
    # 推理型模型兜底：思考烧光预算时 content 为空而 reasoning_content 有货——
    # 回退到思考内容（盯盘的严格格式行大概率解析失败转 hold，但至少可见、可排查）
    try:
        msg = choice.get("message") or {}
        if not str(msg.get("content") or "").strip() and msg.get("reasoning_content"):
            msg["content"] = msg["reasoning_content"]
    except Exception:  # noqa: BLE001
        pass
    # 把 usage **挂回 choice**：调用方拿到的是 choices[0]，而 usage 在响应体顶层。
    # 少了这一步，任何按"返回值的 usage"统计成本的调用方都会记成 0 ——
    # 实测踩过：多角色委员会的 token 预算因此**从未生效**（与 TradingAgents 在非价目表模型上
    # 报 cost=0 是同一类错：成本记成 0 比没有数字更糟，因为它看起来像"很便宜"）。
    if isinstance(choice, dict) and isinstance(body.get("usage"), dict):
        choice.setdefault("usage", body["usage"])
    return choice


def _record_llm_usage(*, ok, latency_ms, body=None):
    """记一次 LLM 调用的用量。计量失败**绝不影响** LLM 调用本身。"""
    try:
        from agent import metering

        metering.record_llm(model=MODEL, latency_ms=latency_ms, ok=ok,
                            **metering.parse_usage(body))
    except Exception as exc:  # noqa: BLE001
        logger.debug("LLM 用量记录失败: %s", exc)
