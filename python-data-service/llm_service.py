"""
llm_service.py —— LLM 客户端（DeepSeek）

职责：
- 加载 prompts 模板
- 调用 DeepSeek Chat API（兼容 OpenAI 协议）
- 拼装 system + user prompt，调用 LLM 生成回复
- parse_intent: LLM 意图识别，把用户消息解析成 JSON 操作计划

环境变量（优先级：系统 env > .env 文件）：
- DEEPSEEK_API_KEY: 必填（单 key）
- DEEPSEEK_API_KEYS: 多 key 池（逗号分隔，优先于单 key 变量）——账号级限速下
  每把 key 是独立限速桶，429 冷却只冷却那把 key，其余 key 继续服务
- DEEPSEEK_BASE_URL: 默认 https://api.deepseek.com
- DEEPSEEK_MODEL: 默认 deepseek-chat

用户自带 Key（BYOK，2026-10-25）：env 供应商只服务**系统级**调用（新闻解读等
共享内容流水线）。用户触发的 AI（聊天/委员会/盯盘/反思/记忆巩固）由 Java 把
每个用户自己配置的主/备 Key 随请求体下发（llm_providers 字段，AES-GCM 加密
落库、调用时解密），本模块按调用接收（providers 参数）并完全替换 env 供应商。
契约：带 llm_providers 字段（哪怕空数组）= 用户级调用，只用用户自己的 Key；
不带 = 系统级调用。见 user_providers()。
"""
import json
import logging
import os
import re
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
# chat/completions 的路径后缀按供应商不同：DeepSeek/硅基流动是 /v1/chat/completions，
# 智谱（bigmodel.cn）的 OpenAI 兼容端点是 /api/paas/v4/chat/completions —— base 配
# https://open.bigmodel.cn/api/paas/v4、后缀配 /chat/completions 即可对上。
FALLBACK_CHAT_PATH = os.getenv("LLM_FALLBACK_CHAT_PATH", "/v1/chat/completions")
PROVIDER_COOLDOWN_S = float(os.getenv("LLM_PROVIDER_COOLDOWN", "600"))

def _primary_keys() -> list:
    """主供应商（DeepSeek）的 key 池：`DEEPSEEK_API_KEYS`（逗号分隔）优先，回退单 key 变量。"""
    raw = (os.getenv("DEEPSEEK_API_KEYS") or "").strip()
    keys = [k.strip() for k in raw.split(",")
            if k.strip() and k.strip() != "your-api-key-here"]
    if keys:
        return keys
    return [API_KEY] if (API_KEY and API_KEY != "your-api-key-here") else []


def _providers() -> list:
    """env 供应商序列：主供应商每把 key 一个实例 + 可选备用。

    元素是六元组 (名字, base_url, key, model, chat_path, 主槽语义)：
    - 多 key（2026-10-22）：DeepSeek 的限速在账号（key）级，多把 key = 多个独立限速桶。
      每把 key 沿用同一套冷却/切换机制——持续 429 冷却的是**那把 key**，不是整个供应商；
      调用顺序即列表顺序（key1 → key2 → … → 备用供应商）。
    - 主槽语义（2026-10-25，取代旧的 `_is_primary(name)` 按名字判断）：model_override
      （委员会 reasoner 路由）与 reasoning_effort 注入是否对该实例生效。env 主供应商
      每把 key 都是 True（第二把 key 应答时行为不能悄悄变），env 备用是 False——
      改成元组携带是因为用户自带 Key（见 user_providers）的名字不带 "primary" 前缀，
      按名字判断会把用户主槽漏掉。
    """
    ps = []
    for i, key in enumerate(_primary_keys()):
        name = "primary" if i == 0 else f"primary:{i + 1}"
        ps.append((name, BASE_URL, key, MODEL, "/v1/chat/completions", True))
    if FALLBACK_BASE_URL and FALLBACK_API_KEY and FALLBACK_MODEL:
        ps.append(("fallback", FALLBACK_BASE_URL, FALLBACK_API_KEY, FALLBACK_MODEL, FALLBACK_CHAT_PATH, False))
    return ps

# 内存态冷却表：重启归零无害（最多多打几次注定失败的请求）。
_provider_cooldown_until: dict = {}

def _mark_cooldown(name: str):
    import time as _t
    now = _t.time()
    _provider_cooldown_until[name] = now + PROVIDER_COOLDOWN_S
    # 用户自带 Key 后冷却名按用户命名空间（u{id}:primary），条目数随用户数增长——
    # 超过阈值时顺手清掉已到期的，防止长跑进程的字典无限膨胀。
    if len(_provider_cooldown_until) > 512:
        for stale in [k for k, until in _provider_cooldown_until.items() if until <= now]:
            del _provider_cooldown_until[stale]
    logger.warning("LLM 供应商 %s 进入 %.0f 分钟冷却（持续过载）", name, PROVIDER_COOLDOWN_S / 60)

def _post_chat(payload: dict, model_override: str = "", thinking: Optional[bool] = None,
               timeout_s: Optional[float] = None, providers: Optional[list] = None) -> dict:
    """跨供应商的 chat/completions：重试扛抖动（单供应商内），冷却+切换扛持续故障。
    返回解析后的 JSON 体（model 字段已按实际供应商覆盖）。全部供应商失败时抛最后异常。

    按调用覆盖（2026-10-18，终审角色路由到 reasoner）：
    - `model_override`：只对主供应商生效——模型名是供应商私有的，跨供应商没有意义；
      备用供应商应答时仍用它自己的模型（该用哪个模型是"这个端点有什么"的问题）。
    - `thinking=True`：显式要思考——绝不注入 reasoning_effort=none（终审角色买的
      就是深度思考，被全局开关掐掉等于白花 reasoner 的钱）。
    - `timeout_s`：按调用超时（reasoner 比 chat 慢得多，委员会 240s 预算允许等它）。
    - `providers`（2026-10-25，BYOK）：用户级调用传入用户自己的供应商列表
      （见 user_providers），完全替换 env 供应商；None = 系统级调用，读 env。
    """
    import time as _t
    import copy
    now = _t.time()
    pool = providers if providers is not None else _providers()
    providers = [p for p in pool if _provider_cooldown_until.get(p[0], 0) <= now]
    if not providers:                      # 全在冷却：仍试第一家（冷却不是停机）
        providers = [pool[0]]
    last_exc = None
    for name, base, key, model, chat_path, primary in providers:
        try:
            body = dict(payload,
                        model=(model_override or model) if primary else model)
            if primary and DISABLE_THINKING and thinking is not True:
                body.setdefault("reasoning_effort", "none")
            try:
                resp = _post_with_retry(f"{base}{chat_path}",
                                        json=body,
                                        headers={"Authorization": f"Bearer {key}",
                                                 "Content-Type": "application/json"},
                                        timeout=timeout_s or TIMEOUT)
            except requests.exceptions.HTTPError as http_exc:
                # response_format 兜底：个别端点不认这个字段直接 400。剥掉重试一次——
                # 输出格式退回"提示词约束 + 解析器"的旧路径，总比整次调用失败好
                # （盯盘的解析失败会静默降级成 hold，那比格式松一点更糟）。
                if ("response_format" in body
                        and getattr(http_exc.response, "status_code", None) == 400):
                    logger.warning("供应商 %s 不支持 response_format，剥掉后重试一次", name)
                    body.pop("response_format", None)
                    resp = _post_with_retry(f"{base}{chat_path}",
                                            json=body,
                                            headers={"Authorization": f"Bearer {key}",
                                                     "Content-Type": "application/json"},
                                            timeout=timeout_s or TIMEOUT)
                else:
                    raise
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


def _is_available(providers: Optional[list] = None) -> bool:
    """LLM 是否可用。

    - 系统级调用（providers=None）：读 env，有主 key 或配了备用即可用；
    - 用户级调用（providers 非 None，含空列表 BYOK 契约）：**只看用户自己的列表**——
      空列表 = 用户没配 Key = 不可用（绝不能静默落到部署者的全局 key 上，
      "谁来付钱"必须由 llm_providers 字段说清）。
    """
    if providers is not None:
        return bool(providers)
    if _primary_keys():
        return True
    return bool(FALLBACK_API_KEY)   # 主 key 缺失但配了备用：仍然可用


# ==================== 用户自带 Key（BYOK，2026-10-25） ====================
# 每个用户在个人中心配置自己的主/备 Key：Java 侧 AES-GCM 加密落库，调用时解密
# 随请求体下发（llm_providers 字段）。契约一个字段说清"谁来付钱"：
# - 请求体**带** llm_providers（哪怕是空数组）= 用户级调用，只用用户自己的供应商；
# - 请求体**不带**该字段 = 系统级调用（新闻解读等共享内容流水线），继续用 env 供应商。
USER_LLM_CHAT_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]*$")


def _is_deepseek_endpoint(base_url: str) -> bool:
    """是否 DeepSeek 官方端点（主槽语义里的 reasoner 路由 / thinking 注入只认它：
    deepseek-reasoner、reasoning_effort 都是 DeepSeek 私有的，端到别家只会 400）。"""
    return not base_url or "deepseek.com" in base_url


def bind_user_calls(providers: list):
    """把用户供应商列表绑定成 (completion, completion_stream) 调用对。

    app.py 用它把 user_providers 的产物接进 agent 层现成的 completion 注入缝隙
    （run_agent/run_committee/intraday_decide 等都以 `completion or llm_service.*`
    收 LLM 调用）——用户级调用不改 agent 代码就换成用户自己的供应商。
    """
    import functools

    return (functools.partial(chat_completion, providers=providers),
            functools.partial(chat_completion_stream, providers=providers))


def completion_available(completion=None) -> bool:
    """门控探测（BYOK 感知）：completion 是 bind_user_calls 的产物时按**用户**
    供应商判定可用性；其余（None / 测试注入的普通函数）按 env 判定。

    委员会/盯盘/修订提案的开会门控用它代替裸 _is_available()——用户级调用
    必须按用户的 Key 判定：没配 Key 时按 env 判"可用"会让委员会跑出一整轮
    占位文本；配了 Key 而 env 为空时误判"不可用"则根本开不了会。
    """
    keywords = getattr(completion, "keywords", None)
    providers = keywords.get("providers") if isinstance(keywords, dict) else None
    if providers is not None:
        return bool(providers)
    return _is_available()


def user_providers(raw, scope_id) -> Optional[list]:
    """把请求体的 llm_providers 规范成供应商六元组列表（冷却名按用户隔离）。

    - raw 为 None（请求体没带字段）→ 返回 None：系统级调用，调用方继续用 env 供应商；
    - raw 为空列表 → 返回 []：用户级调用但没配 Key，调用方据此走降级（不烧全局额度）；
    - 每条形如 {name: "primary"|"fallback", base_url, api_key, model, chat_path?}，
      primary 的 base_url/model 留空 = DeepSeek 预设；fallback 三个都要配齐。

    校验失败抛 ValueError（消息可直接透给调用方）。scope_id 进冷却名
    （u{scope}:primary），保证用户 A 的 429 不会冷却用户 B 的同款 Key。
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("llm_providers 必须是数组")
    scope = str(scope_id or "x").strip() or "x"
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("llm_providers 的每一项必须是对象")
        name = str(item.get("name") or "").strip()
        if name not in ("primary", "fallback"):
            raise ValueError(f"llm_providers.name 非法：{name or '(空)'}")
        api_key = str(item.get("api_key") or "").strip()
        if not api_key:
            raise ValueError(f"{name} 槽缺少 api_key")
        base_url = str(item.get("base_url") or "").strip().rstrip("/")
        if name == "primary":
            model = str(item.get("model") or "").strip() or MODEL
        else:
            model = str(item.get("model") or "").strip()
            if not (base_url and model):
                raise ValueError("备用槽的 base_url 与 model 必须配齐")
        if base_url and not base_url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ValueError(f"{name} 槽 base_url 必须是 https（本机调试除外）：{base_url[:60]}")
        chat_path = str(item.get("chat_path") or "").strip() or "/v1/chat/completions"
        if not USER_LLM_CHAT_PATH_RE.match(chat_path):
            raise ValueError(f"{name} 槽 chat_path 非法：{chat_path[:60]}")
        # 主槽语义只认 DeepSeek 官方端点的 primary：用户自定义主槽（如硅基流动）
        # 没有 deepseek-reasoner、也不认 reasoning_effort，路由过去只会 400——
        # 与 env 备用供应商"应答时用它自己的模型"同一语义。
        primary_semantics = name == "primary" and _is_deepseek_endpoint(base_url)
        out.append((f"u{scope}:{name}", base_url or BASE_URL, api_key, model, chat_path,
                    primary_semantics))
    return out


def test_providers(providers: list) -> list:
    """逐条探测供应商连通性（Java「保存前测试」按钮的后端）。

    刻意不走 _post_chat：探测要**独立**测每一家（不带冷却/切换/退避语义，
    一次成不成直接说），而且要快（15s 超时、max_tokens=8，烧的是用户自己的钱）。
    错误信息截断到 200 字符且不含 Key（Key 只在请求头里，异常文本不会带出）。
    """
    import time as _t
    results = []
    for name, base, key, model, chat_path, _primary in providers:
        started = _t.time()
        try:
            resp = requests.post(
                f"{base}{chat_path}",
                json={"model": model,
                      "messages": [{"role": "user", "content": "ping"}],
                      "max_tokens": 8, "temperature": 0, "stream": False},
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                timeout=15)
            resp.raise_for_status()
            results.append({"name": name, "ok": True,
                            "latency_ms": int((_t.time() - started) * 1000),
                            "model": model})
        except Exception as exc:  # noqa: BLE001 —— 探测的职责就是把失败原样带回
            results.append({"name": name, "ok": False,
                            "latency_ms": int((_t.time() - started) * 1000),
                            "model": model, "error": str(exc)[:200]})
    return results


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


def chat_completion(messages, tools=None, temperature=0.2, max_tokens=1200,
                    model=None, thinking=None, response_format=None, timeout_s=None,
                    providers=None):
    """agent 循环用的底层调用：返回 choices[0]，**同时把用量记进计量（P1）**。

    为什么要在这里记而不是在调用方：这是所有 LLM 调用的必经之路（agent 循环、摘要巩固、
    评测都走它）。记在调用方就意味着"以后新增的调用点忘了记"—— 而成本数字一旦漏记，
    比没有数字更糟（会让人以为很便宜）。

    按调用覆盖（2026-10-18）：
    - `model`：覆盖模型（仅主供应商生效，见 _post_chat）——委员会终审角色路由
      到 deepseek-reasoner 用的就是它；
    - `thinking=True`：要求思考，绕过全局 DEEPSEEK_DISABLE_THINKING；
    - `response_format`：API 层 JSON 模式（如 {"type": "json_object"}），盯盘用——
      解析失败→hold 的静默降级从此在 API 层就防住；
    - `timeout_s`：按调用超时（reasoner 慢，委员会等得起，盯盘等不起）。
    - `providers`（2026-10-25，BYOK）：用户自己的供应商列表（user_providers 的返回值），
      None = 系统级调用走 env。
    """
    if not _is_available(providers):
        if providers is not None:
            return {"message": {"role": "assistant",
                                "content": "AI 服务未配置：请在个人中心绑定你自己的 API Key"}}
        return {"message": {"role": "assistant", "content": "AI 服务暂不可用"}}
    payload = {"model": MODEL, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        payload["tools"] = tools
    if response_format:
        payload["response_format"] = response_format
    started = time.time()
    try:
        body = _post_chat(payload, model_override=str(model or ""),
                          thinking=thinking, timeout_s=timeout_s, providers=providers)
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


def chat_completion_stream(messages, tools=None, temperature=0.2, max_tokens=1200,
                           model=None, thinking=None, on_delta=None, providers=None):
    """chat_completion 的流式版（2026-10-20，前端流式输出用）：边生成边把文本增量
    回调给 on_delta(text_piece)，最终返回与 chat_completion **相同形状**的 choice
    （content 与 tool_calls 都拼装完整），调用方逻辑不用为流式分叉。

    为什么单独一个函数而不是给 chat_completion 加开关：非流式版本被委员会/摘要/
    盯盘大量共用，而流式的失败语义不同——**已经向前端吐过增量就不能换供应商重试**，
    否则用户会看到同一段话重复两遍。分开写，各自的失败语义各自清楚：

    - 首个增量之前失败（连接/4xx/5xx）：与 _post_chat 同款兜底——标记冷却、换下一家；
    - 已吐过增量后失败：直接抛，让上层兜底（宁可半截报错，不能重试拼接）。

    `providers`（2026-10-25，BYOK）与非流式版同语义。
    """
    if not _is_available(providers):
        if providers is not None:
            return {"message": {"role": "assistant",
                                "content": "AI 服务未配置：请在个人中心绑定你自己的 API Key"}}
        return {"message": {"role": "assistant", "content": "AI 服务暂不可用"}}
    payload = {"model": MODEL, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens,
               "stream": True, "stream_options": {"include_usage": True}}
    if tools:
        payload["tools"] = tools
    import time as _t
    now = _t.time()
    pool = providers if providers is not None else _providers()
    providers = [p for p in pool if _provider_cooldown_until.get(p[0], 0) <= now]
    if not providers:                      # 全在冷却：仍试第一家（与非流式同语义）
        providers = [pool[0]]
    last_exc = None
    for name, base, key, model_name, chat_path, primary in providers:
        body = dict(payload, model=(str(model or "") or model_name) if primary else model_name)
        if primary and DISABLE_THINKING and thinking is not True:
            body.setdefault("reasoning_effort", "none")
        emitted = {"any": False}
        try:
            choice, usage = _stream_one(f"{base}{chat_path}", body, key, on_delta, emitted)
        except Exception as exc:  # noqa: BLE001
            if not emitted["any"]:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in RETRYABLE_STATUS:
                    _mark_cooldown(name)
                last_exc = exc
                logger.warning("流式供应商 %s 失败（未输出任何增量），换下家: %s", name, exc)
                continue
            # 半截已送达前端：不能重试，抛给上层兜底
            raise
        if not primary:
            logger.info("LLM(chat_completion_stream) 由备用供应商应答")
        _record_llm_usage(ok=True, latency_ms=int((_t.time() - now) * 1000),
                          body={"usage": usage} if usage else None)
        # 推理型模型兜底：思考烧光预算时 content 为空而 reasoning_content 有货（同 chat_completion）
        msg = choice.get("message") or {}
        if not str(msg.get("content") or "").strip() and msg.get("reasoning_content"):
            msg["content"] = msg["reasoning_content"]
        if usage:
            choice.setdefault("usage", usage)
        return choice
    raise last_exc or RuntimeError("LLM stream: no provider available")


def _stream_one(url, body, key, on_delta, emitted):
    """跑一家供应商的流式请求：解析 SSE 行，拼装完整 choice，返回 (choice, usage)。

    requests 的 timeout 在 stream=True 下是"连接/字节间隔"超时而非总时长——
    正是流式要的语义：生成可以慢，卡死会被掐。
    回调异常吞掉：增量回调挂了不该弄死整次生成。
    """
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "Accept": "text/event-stream"}
    resp = requests.post(url, json=body, headers=headers, timeout=TIMEOUT, stream=True)
    if resp.status_code == 400 and "stream_options" in body:
        # 个别 OpenAI 兼容端点不认 stream_options：剥掉同一家再试一次
        #（与 _post_chat 对 response_format 的处理同思路）
        resp.close()
        stripped = dict(body)
        stripped.pop("stream_options")
        resp = requests.post(url, json=stripped, headers=headers, timeout=TIMEOUT, stream=True)
    resp.raise_for_status()
    content_parts, reasoning_parts = [], []
    tool_calls = {}
    usage = None
    try:
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            for ch in chunk.get("choices") or []:
                if not isinstance(ch, dict):
                    continue
                delta = ch.get("delta") or {}
                text = delta.get("content")
                if text:
                    content_parts.append(text)
                    emitted["any"] = True
                    if on_delta is not None:
                        try:
                            on_delta(text)
                        except Exception:  # noqa: BLE001
                            pass
                if delta.get("reasoning_content"):
                    reasoning_parts.append(delta["reasoning_content"])
                for tc in delta.get("tool_calls") or []:
                    if not isinstance(tc, dict):
                        continue
                    slot = tool_calls.setdefault(tc.get("index", 0), {
                        "id": "", "type": "function",
                        "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    # name 按官方语义整段送达，直接覆盖；arguments 必须累加
                    if fn.get("name"):
                        slot["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]
    finally:
        resp.close()
    message = {"role": "assistant", "content": "".join(content_parts)}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls:
        message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
    return {"message": message}, usage


def _record_llm_usage(*, ok, latency_ms, body=None):
    """记一次 LLM 调用的用量。计量失败**绝不影响** LLM 调用本身。"""
    try:
        from agent import metering

        metering.record_llm(model=MODEL, latency_ms=latency_ms, ok=ok,
                            **metering.parse_usage(body))
    except Exception as exc:  # noqa: BLE001
        logger.debug("LLM 用量记录失败: %s", exc)
