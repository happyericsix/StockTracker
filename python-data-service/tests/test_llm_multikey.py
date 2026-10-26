# -*- coding: utf-8 -*-
"""多 key 池（2026-10-22）：同一 DeepSeek 供应商的多把 key = 多个独立限速桶。

核心契约：
1. `DEEPSEEK_API_KEYS`（逗号分隔）优先于单 key 变量，每把 key 展开成一个供应商实例；
2. 某把 key 冷却时只冷却它自己——流量落到下一把 key，而不是整个供应商趴下；
3. 主供应商语义（reasoning_effort 注入）对**每把 key** 都成立，第二把 key 应答时行为不能悄悄变；
4. 单 key 变量完全向后兼容，占位符 key 被忽略。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm_service  # noqa: E402


class _Response:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._body

    def __init__(self, body):
        self._body = body


def test_keys_env_expands_into_provider_instances(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEYS", "sk-a,sk-b, sk-c ")
    monkeypatch.setattr(llm_service, "API_KEY", "sk-single")
    monkeypatch.setattr(llm_service, "FALLBACK_BASE_URL", "")

    providers = llm_service._providers()

    assert [p[0] for p in providers] == ["primary", "primary:2", "primary:3"]
    assert [p[2] for p in providers] == ["sk-a", "sk-b", "sk-c"], "逗号分隔要容忍空白"
    assert all(p[2] != "sk-single" for p in providers), "KEYS 池优先于单 key 变量"


def test_single_key_variable_still_works(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEYS", raising=False)
    monkeypatch.setattr(llm_service, "API_KEY", "sk-only")
    monkeypatch.setattr(llm_service, "FALLBACK_BASE_URL", "")

    providers = llm_service._providers()
    assert [p[0] for p in providers] == ["primary"]
    assert providers[0][2] == "sk-only"


def test_placeholder_keys_are_ignored(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEYS", "your-api-key-here,,  ,sk-real")
    monkeypatch.setattr(llm_service, "API_KEY", "")
    monkeypatch.setattr(llm_service, "FALLBACK_BASE_URL", "")   # 本机 .env 可能配了备用，测试要隔离

    providers = llm_service._providers()
    assert [p[2] for p in providers] == ["sk-real"]


def test_cooled_key_falls_through_to_the_next_key(monkeypatch):
    """key1 冷却 → 请求由 key2 应答，且 key2 拿到主供应商的全部语义。"""
    monkeypatch.setenv("DEEPSEEK_API_KEYS", "sk-a,sk-b")
    monkeypatch.setattr(llm_service, "FALLBACK_BASE_URL", "")
    monkeypatch.setattr(llm_service, "DISABLE_THINKING", True)
    seen = []

    def post(url, json=None, headers=None, timeout=None):
        seen.append(headers["Authorization"])
        assert json.get("reasoning_effort") == "none", \
            "第二把 key 也是主供应商，思考开关必须同样生效"
        return _Response({"choices": [{"message": {"role": "assistant", "content": "ok"}}],
                          "usage": {"total_tokens": 5}})

    monkeypatch.setattr(llm_service.requests, "post", post)
    llm_service._provider_cooldown_until.clear()
    try:
        llm_service._provider_cooldown_until["primary"] = time.time() + 600   # 只冷却第一把

        data = llm_service._post_chat({"model": "deepseek-chat", "messages": []})

        assert data["_provider"] == "primary:2"
        assert seen == ["Bearer sk-b"], "冷却中的 key 不该再收到请求"
    finally:
        llm_service._provider_cooldown_until.clear()


def test_is_available_with_only_the_keys_pool(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEYS", "sk-a")
    monkeypatch.setattr(llm_service, "API_KEY", "")
    monkeypatch.setattr(llm_service, "FALLBACK_API_KEY", "")

    assert llm_service._is_available() is True
