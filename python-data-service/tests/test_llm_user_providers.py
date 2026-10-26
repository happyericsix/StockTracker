# -*- coding: utf-8 -*-
"""用户自带 Key（BYOK，2026-10-25）：llm_providers 契约与供应商替换语义。

核心契约（"谁付钱"必须由一个字段说清）：
1. 请求体带 llm_providers（哪怕空数组）= 用户级调用 → 只用用户自己的供应商，
   env 的 DEEPSEEK_API_KEY 绝不能被烧到；
2. 不带该字段 = 系统级调用（新闻解读等共享流水线）→ 走 env 供应商，行为不变；
3. 用户的冷却按 u{id}:slot 命名空间隔离 —— 用户 A 的 429 不冷却用户 B 的同款 Key；
4. reasoner 路由 / thinking 注入只对「DeepSeek 官方端点的主槽」生效，
   用户自定义主槽应答时用它自己的模型（与 env 备用供应商同一语义）。
"""
import functools
import sys
import time
from pathlib import Path
from unittest.mock import patch

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


def _ok_body():
    return {"choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"total_tokens": 5}}


# ===== user_providers：契约三态 =====

def test_none_means_system_scope(monkeypatch):
    """不带 llm_providers 字段 = 系统级调用：返回 None，调用方继续用 env。"""
    assert llm_service.user_providers(None, 7) is None


def test_empty_list_means_unconfigured_user():
    """带空数组 = 用户级调用但没配 Key：返回 []（不得静默回落 env）。"""
    assert llm_service.user_providers([], 7) == []


def test_primary_slot_defaults_to_deepseek_preset():
    providers = llm_service.user_providers(
        [{"name": "primary", "api_key": "sk-user"}], 7)
    assert len(providers) == 1
    name, base, key, model, chat_path, primary = providers[0]
    assert name == "u7:primary", "冷却名必须带用户命名空间"
    assert base == llm_service.BASE_URL and model == llm_service.MODEL
    assert key == "sk-user" and chat_path == "/v1/chat/completions"
    assert primary is True, "DeepSeek 预设主槽保持主槽语义（reasoner 路由/thinking 注入）"


def test_custom_primary_is_not_deepseek_semantics():
    providers = llm_service.user_providers(
        [{"name": "primary", "api_key": "sk-u", "base_url": "https://api.siliconflow.cn",
          "model": "Qwen/Qwen2.5-72B-Instruct"}], 9)
    _, base, _, model, _, primary = providers[0]
    assert base == "https://api.siliconflow.cn"
    assert model == "Qwen/Qwen2.5-72B-Instruct"
    assert primary is False, "自定义主槽没有 deepseek-reasoner/reasoning_effort，路由过去只会 400"


def test_fallback_slot_keeps_chat_path():
    providers = llm_service.user_providers(
        [{"name": "fallback", "api_key": "sk-f", "base_url": "https://open.bigmodel.cn/api/paas/v4",
          "model": "glm-4.6", "chat_path": "/chat/completions"}], 3)
    name, base, _, _, chat_path, primary = providers[0]
    assert (name, base, chat_path, primary) == (
        "u3:fallback", "https://open.bigmodel.cn/api/paas/v4", "/chat/completions", False)


# ===== user_providers：校验拒绝 =====

def test_rejects_missing_api_key():
    try:
        llm_service.user_providers([{"name": "primary"}], 1)
        raise AssertionError("缺 api_key 必须抛 ValueError")
    except ValueError as e:
        assert "api_key" in str(e)


def test_rejects_unknown_slot_name():
    try:
        llm_service.user_providers([{"name": "evil", "api_key": "sk"}], 1)
        raise AssertionError("非法槽名必须抛 ValueError")
    except ValueError:
        pass


def test_fallback_requires_base_and_model():
    try:
        llm_service.user_providers([{"name": "fallback", "api_key": "sk"}], 1)
        raise AssertionError("备用槽缺 base_url/model 必须抛 ValueError")
    except ValueError:
        pass


def test_rejects_plain_http_except_localhost():
    for base in ("http://api.example.com", "ftp://x"):
        try:
            llm_service.user_providers(
                [{"name": "primary", "api_key": "sk", "base_url": base}], 1)
            raise AssertionError(f"{base} 必须被拒绝（SSRF 面）")
        except ValueError:
            pass
    # 本机调试例外
    providers = llm_service.user_providers(
        [{"name": "primary", "api_key": "sk", "base_url": "http://localhost:8001",
          "model": "x"}], 1)
    assert providers[0][1] == "http://localhost:8001"


def test_rejects_bad_chat_path():
    try:
        llm_service.user_providers(
            [{"name": "primary", "api_key": "sk", "chat_path": "../etc"}], 1)
        raise AssertionError("chat_path 必须走白名单正则")
    except ValueError:
        pass


# ===== 供应商替换：用户级调用绝不能烧 env 的 key =====

def test_user_key_used_env_key_never_touched(monkeypatch):
    monkeypatch.setattr(llm_service, "API_KEY", "sk-env-deployer")
    monkeypatch.setattr(llm_service, "FALLBACK_BASE_URL", "")
    seen = []

    def post(url, json=None, headers=None, timeout=None):
        seen.append((url, headers["Authorization"]))
        return _Response(_ok_body())

    monkeypatch.setattr(llm_service.requests, "post", post)
    providers = llm_service.user_providers(
        [{"name": "primary", "api_key": "sk-user-own"}], 7)

    choice = llm_service.chat_completion(
        [{"role": "user", "content": "hi"}], providers=providers)

    assert choice["message"]["content"] == "ok"
    assert seen == [("https://api.deepseek.com/v1/chat/completions", "Bearer sk-user-own")], \
        "用户级调用必须花用户自己的 key，env key 一分钱都不能烧"


def test_empty_user_providers_degrades_even_when_env_configured(monkeypatch):
    """没配 Key 的用户：即使 env 配了 key 也必须降级（带引导文案），不能静默用全局 key。"""
    monkeypatch.setattr(llm_service, "API_KEY", "sk-env-deployer")
    monkeypatch.setattr(llm_service, "FALLBACK_BASE_URL", "")
    llm_service._provider_cooldown_until.clear()

    choice = llm_service.chat_completion([{"role": "user", "content": "hi"}], providers=[])

    assert "个人中心" in choice["message"]["content"], "降级文案要能引导用户去配置"


def test_user_fallback_answers_when_primary_fails(monkeypatch):
    monkeypatch.setattr(llm_service, "FALLBACK_BASE_URL", "")
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append(url)
        if "deepseek" in url:
            raise llm_service.requests.exceptions.HTTPError("503 overload")
        return _Response(_ok_body())

    monkeypatch.setattr(llm_service.requests, "post", post)
    providers = llm_service.user_providers(
        [{"name": "primary", "api_key": "sk-a"},
         {"name": "fallback", "api_key": "sk-b", "base_url": "https://api.siliconflow.cn",
          "model": "Qwen/Qwen2.5-72B-Instruct"}], 7)

    choice = llm_service.chat_completion([{"role": "user", "content": "hi"}], providers=providers)

    assert choice["message"]["content"] == "ok"
    assert len(calls) == 2, "主槽失败一次后必须切到用户自己的备用"


def test_cooldown_isolated_per_user(monkeypatch):
    """用户 1 的主槽在冷却 ≠ 用户 2 的同槽同 key 也冷却（命名空间隔离）。"""
    monkeypatch.setattr(llm_service, "FALLBACK_BASE_URL", "")
    seen = []

    def post(url, json=None, headers=None, timeout=None):
        seen.append(headers["Authorization"])
        return _Response(_ok_body())

    monkeypatch.setattr(llm_service.requests, "post", post)
    llm_service._provider_cooldown_until.clear()
    try:
        llm_service._provider_cooldown_until["u1:primary"] = time.time() + 600
        providers = llm_service.user_providers([{"name": "primary", "api_key": "sk-shared"}], 2)

        llm_service.chat_completion([{"role": "user", "content": "hi"}], providers=providers)

        assert seen == ["Bearer sk-shared"], "用户 2 的调用不该被用户 1 的冷却挡住"
    finally:
        llm_service._provider_cooldown_until.clear()


def test_model_override_skipped_for_custom_primary(monkeypatch):
    """委员会 reasoner 路由（model_override）只对 DeepSeek 官方主槽生效。"""
    monkeypatch.setattr(llm_service, "FALLBACK_BASE_URL", "")
    seen = []

    def post(url, json=None, headers=None, timeout=None):
        seen.append(json["model"])
        return _Response(_ok_body())

    monkeypatch.setattr(llm_service.requests, "post", post)
    llm_service._provider_cooldown_until.clear()
    providers = llm_service.user_providers(
        [{"name": "primary", "api_key": "sk-u", "base_url": "https://api.siliconflow.cn",
          "model": "Qwen/Qwen2.5-72B-Instruct"}], 7)

    llm_service.chat_completion([{"role": "user", "content": "hi"}],
                                model="deepseek-reasoner", providers=providers)

    assert seen == ["Qwen/Qwen2.5-72B-Instruct"], \
        "自定义端点不认识 deepseek-reasoner，必须用它自己的模型"

    # 对照：DeepSeek 预设主槽要维持既有 reasoner 路由行为
    seen.clear()
    providers = llm_service.user_providers([{"name": "primary", "api_key": "sk-u"}], 7)
    llm_service.chat_completion([{"role": "user", "content": "hi"}],
                                model="deepseek-reasoner", providers=providers)
    assert seen == ["deepseek-reasoner"]


# ===== bind_user_calls / completion_available（门控探测） =====

def test_bind_user_calls_and_completion_available(monkeypatch):
    providers = llm_service.user_providers([{"name": "primary", "api_key": "sk-u"}], 7)
    completion, _stream = llm_service.bind_user_calls(providers)

    assert isinstance(completion, functools.partial)
    assert llm_service.completion_available(completion) is True

    empty = llm_service.bind_user_calls([])
    assert llm_service.completion_available(empty[0]) is False, \
        "没配 Key 的用户在门控处就该被拦下（而不是开完一整轮占位文本）"

    # 非绑定调用（系统级 / 测试注入的普通函数）按 env 判定
    monkeypatch.setattr(llm_service, "API_KEY", "sk-env")
    monkeypatch.setattr(llm_service, "FALLBACK_BASE_URL", "")
    assert llm_service.completion_available(None) is True
    assert llm_service.completion_available(lambda **kw: None) is True


# ===== test_providers：保存前探测 =====

def test_test_providers_reports_each_slot_independently(monkeypatch):
    def post(url, json=None, headers=None, timeout=None):
        if "siliconflow" in url:
            raise llm_service.requests.exceptions.HTTPError("401 bad key")
        return _Response(_ok_body())

    monkeypatch.setattr(llm_service.requests, "post", post)
    providers = llm_service.user_providers(
        [{"name": "primary", "api_key": "sk-good"},
         {"name": "fallback", "api_key": "sk-bad", "base_url": "https://api.siliconflow.cn",
          "model": "Qwen/Qwen2.5-72B-Instruct"}], 7)

    results = llm_service.test_providers(providers)

    by_name = {r["name"]: r for r in results}
    assert by_name["u7:primary"]["ok"] is True
    assert by_name["u7:fallback"]["ok"] is False
    assert "401" in by_name["u7:fallback"]["error"]
    assert "sk-bad" not in str(results), "错误信息绝不能带出 key"


# ===== HTTP 契约：llm_providers 必须一路到达 agent =====

def test_llm_providers_bound_into_agent_call():
    """契约：带 llm_providers 的请求，run_agent 收到的 completion 必须绑定了
    用户自己的供应商（没有这个绑定 = 静默烧全局 key，比报错更糟）。"""
    import app as app_module
    from fastapi.testclient import TestClient

    token = getattr(app_module, "INTERNAL_API_TOKEN", "") or ""
    headers = {"X-Internal-Token": token} if token else {}
    client = TestClient(app_module.app)
    fake = {"replies": ["ok"], "strategy_json": None}

    with patch("agent.react_agent.run_agent", return_value=fake) as mocked:
        r = client.post("/api/v1/agent/chat", headers=headers, json={
            "user_id": "alice", "memory_user_id": 9, "message": "hi",
            "llm_providers": [{"name": "primary", "api_key": "sk-mine"}]})

    assert r.status_code == 200, r.text
    kwargs = mocked.call_args.kwargs
    bound = kwargs["completion"]
    assert isinstance(bound, functools.partial)
    (name, _base, key, _model, _path, primary), *_ = bound.keywords["providers"]
    assert (name, key, primary) == ("u9:primary", "sk-mine", True), \
        "scope 必须取 memory_user_id（与记忆账本同一身份口径）"
    assert isinstance(kwargs["completion_stream"], functools.partial)


def test_llm_providers_absent_means_system_scope():
    """契约：不带字段 = 系统级调用，completion 必须是 None（走 env 供应商）。"""
    import app as app_module
    from fastapi.testclient import TestClient

    token = getattr(app_module, "INTERNAL_API_TOKEN", "") or ""
    headers = {"X-Internal-Token": token} if token else {}
    client = TestClient(app_module.app)
    fake = {"replies": ["ok"], "strategy_json": None}

    with patch("agent.react_agent.run_agent", return_value=fake) as mocked:
        r = client.post("/api/v1/agent/chat", headers=headers,
                        json={"user_id": "alice", "message": "hi"})

    assert r.status_code == 200, r.text
    kwargs = mocked.call_args.kwargs
    assert kwargs["completion"] is None and kwargs["completion_stream"] is None


def test_llm_test_endpoint_probes_each_provider():
    import app as app_module
    from fastapi.testclient import TestClient

    token = getattr(app_module, "INTERNAL_API_TOKEN", "") or ""
    headers = {"X-Internal-Token": token} if token else {}
    client = TestClient(app_module.app)

    with patch.object(llm_service.requests, "post", return_value=_Response(_ok_body())):
        r = client.post("/api/v1/llm/test", headers=headers, json={
            "scope": 9,
            "providers": [{"name": "primary", "api_key": "sk-mine"}]})

    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results and results[0]["ok"] is True and results[0]["name"] == "u9:primary"
