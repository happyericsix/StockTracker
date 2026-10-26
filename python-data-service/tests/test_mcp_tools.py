# tests/test_mcp_tools.py
"""MCP 挂载器的测试：假会话注入，零真实网络。

两层分开测：
- 挂载推导（build_spec / mount_all 进独立 ToolRegistry）：命名、scope、信任级别、
  数据集注册、只读过滤、数量上限、validate 干净；
- 执行路径（make_handler 的产物 + external_source.fetch）：信封、<external_data>
  包裹、scope 拒绝、熔断摘除。external_source 用独立 registry 实例
  （同 test_external_tools.py 的 fresh_registry 手法）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import agent.mcp_client as mcp_client
import agent.mcp_tools as mcp_tools
from agent import external_source, tool_scope, tool_pipeline
from agent import tool_contract as tc
from agent.tool_registry import DEFAULT_USER_SCOPES
from agent.tool_spec import ToolRegistry


# ==================== 替身 ====================


class FakeSession:
    """假 MCP 会话：预设工具清单与调用结果，记录调用。"""

    def __init__(self, tools, results=None, fail_calls=False, fail_connect=False):
        self.tools = tools
        self.results = results or {}
        self.fail_calls = fail_calls
        self.fail_connect = fail_connect
        self.connected = False
        self.calls = []

    def connect(self):
        if self.fail_connect:
            raise mcp_client.McpError("connect refused")
        self.connected = True

    def list_tools(self):
        if not self.connected:
            raise AssertionError("mount_all must connect before list_tools")
        return self.tools

    def call_tool(self, name, args):
        self.calls.append((name, dict(args or {})))
        if self.fail_calls:
            raise mcp_client.McpError("transport down")
        return self.results.get(name, {"is_error": False, "text": "ok", "structured": None})


def _config(name="fetch", trust="low", ttl=300.0, max_tools=20, timeout=20.0):
    return mcp_client.McpServerConfig(name=name, transport="http",
                                      url="http://127.0.0.1:9101/mcp",
                                      trust=trust, ttl_s=ttl, max_tools=max_tools,
                                      timeout_s=timeout)


def _tool(name="fetch_url", read_only=True, description="Fetch a URL as markdown."):
    return {"name": name, "title": name, "description": description,
            "input_schema": {"type": "object",
                             "properties": {"url": {"type": "string"}},
                             "required": ["url"]},
            "read_only_hint": read_only}


@pytest.fixture
def fresh_external(monkeypatch):
    """独立 external_source 实例 + 数据集/上游表副本（挂载测试不污染全局）。"""
    monkeypatch.setattr(external_source, "REGISTRY",
                        external_source.ExternalSourceRegistry())
    monkeypatch.setattr(external_source, "DATASETS", dict(external_source.DATASETS))
    monkeypatch.setattr(external_source, "PROVIDERS", dict(external_source.PROVIDERS))


# ==================== 挂载推导 ====================


def test_build_spec_shape():
    spec = mcp_tools.build_spec(_config(), _tool(), handler=lambda args: tc.ok({}))
    assert spec.name == "mcp_fetch_fetch_url"
    assert spec.namespace == "mcp"
    assert spec.scopes == ("mcp:fetch:read",)
    assert spec.provenance == "external"
    assert spec.trust == "low"
    assert spec.cost_class == "expensive"
    assert spec.side_effect == "none"
    assert spec.source == "mcp:fetch:fetch_url"
    assert spec.parameters["required"] == ["url"]
    assert "MCP:fetch" in spec.description


def test_mount_all_registers_datasets_and_specs(fresh_external):
    registry = ToolRegistry()
    session = FakeSession([_tool(), _tool("search_docs", True, "Search docs.")])
    report = mcp_tools.mount_all(
        registry=registry, session_factory=lambda cfg: session,
        configs=[_config()], force=True)

    assert report["servers"][0]["tools"] == 2
    assert "mcp_fetch_fetch_url" in registry.names()
    assert "mcp_fetch_search_docs" in registry.names()
    # 数据集登记（熔断/TTL 的前提）——模块表与运行中的 registry 实例两处都有
    assert "mcp:fetch:fetch_url" in external_source.DATASETS
    assert "mcp:fetch:fetch_url" in external_source.REGISTRY.snapshot()["datasets"]
    assert external_source.DATASETS["mcp:fetch:fetch_url"].ttl_s == 300
    assert "mcp:fetch" in external_source.PROVIDERS
    # 声明自检干净（外部来源的 source/trust/cost 规则全过）
    assert registry.validate() == []


def test_mount_all_skips_non_readonly_tools(fresh_external):
    registry = ToolRegistry()
    session = FakeSession([_tool(), _tool("write_thing", False), _tool("maybe_write", False)])
    report = mcp_tools.mount_all(
        registry=registry, session_factory=lambda cfg: session,
        configs=[_config()], force=True)

    assert report["servers"][0]["tools"] == 1
    assert report["servers"][0]["skipped"] == 2
    assert "mcp_fetch_write_thing" not in registry.names()


def test_mount_all_allowlist_mounts_unannotated_tools(fresh_external):
    """ALLOW_TOOLS 白名单：服务器没声明 readOnlyHint 的工具，运营者显式放行也挂。

    Python 参考实现的 MCP 服务器普遍不填 annotations——没有白名单它们全部进不来。
    """
    registry = ToolRegistry()
    session = FakeSession([_tool("fetch", read_only=False, description="Fetch a URL."),
                           _tool("write_thing", False)])
    report = mcp_tools.mount_all(
        registry=registry, session_factory=lambda cfg: session,
        configs=[mcp_client.McpServerConfig(
            name="fetch", transport="http", url="http://x/mcp",
            trust="low", ttl_s=300, max_tools=20, timeout_s=20,
            allow_tools=("fetch",))],
        force=True)

    assert report["servers"][0]["tools"] == 1
    assert "mcp_fetch_fetch" in registry.names()
    assert "mcp_fetch_write_thing" not in registry.names()  # 不在白名单，仍拒绝
    assert registry.validate() == []


def test_mount_all_caps_tool_count(fresh_external):
    registry = ToolRegistry()
    session = FakeSession([_tool(f"t{i}") for i in range(8)])
    report = mcp_tools.mount_all(
        registry=registry, session_factory=lambda cfg: session,
        configs=[_config(max_tools=3)], force=True)

    assert report["servers"][0]["tools"] == 3
    assert report["servers"][0]["skipped"] == 5


def test_mount_all_server_down_recorded_not_raised(fresh_external):
    registry = ToolRegistry()
    session = FakeSession([], fail_connect=True)
    report = mcp_tools.mount_all(
        registry=registry, session_factory=lambda cfg: session,
        configs=[_config()], force=True)
    assert report["servers"][0]["status"] == "down"
    assert registry.names() == []


def test_mount_all_is_idempotent(fresh_external):
    registry = ToolRegistry()
    session = FakeSession([_tool()])
    mcp_tools.mount_all(registry=registry, session_factory=lambda cfg: session,
                        configs=[_config()], force=True)
    again = mcp_tools.mount_all(registry=registry, session_factory=lambda cfg: session,
                                configs=[_config()])
    assert again["total_tools"] == 1
    assert len(registry.names()) == 1  # 没有重复注册


def test_mount_all_noop_without_servers():
    mcp_tools.reset_for_tests()
    report = mcp_tools.mount_all(registry=ToolRegistry(),
                                 session_factory=lambda cfg: None,
                                 configs=[])
    assert report["servers"] == []
    mcp_tools.reset_for_tests()


# ==================== 默认可见性：mcp:* scope 默认放开（2026-10-20 用户决策） ====================


def test_default_scopes_include_mcp_by_default():
    class _Spec:
        def __init__(self, scopes):
            self.scopes = scopes

    from agent.tool_registry import _derive_default_scopes

    derived = _derive_default_scopes([_Spec(("market:read", "mcp:fetch:read"))])
    assert "market:read" in derived
    assert "mcp:fetch:read" in derived


def test_default_scopes_hide_mcp_when_flag_off(monkeypatch):
    class _Spec:
        def __init__(self, scopes):
            self.scopes = scopes

    from agent.tool_registry import _derive_default_scopes

    monkeypatch.setenv("MCP_DEFAULT_VISIBLE", "0")
    derived = _derive_default_scopes([_Spec(("market:read", "mcp:fetch:read"))])
    assert "market:read" in derived
    assert "mcp:fetch:read" not in derived


def test_real_registry_default_scopes_unchanged():
    # 未挂载 MCP 时全局默认能力与改造前一致（fanout 的 scope 应在；mcp 没有
    # 挂载中的服务器，所以也不应有 mcp:* scope）
    assert "research:fanout" in DEFAULT_USER_SCOPES
    assert not any(s.startswith("mcp:") for s in DEFAULT_USER_SCOPES)


# ==================== 执行路径 ====================


def _enroll_and_call(spec, args, scopes):
    """经真实管线执行（策略/预算/超时全生效）。

    管线层 handler 是双参 (name, args)，ToolSpec 的 handler 是单参 (args)
    （生产路径由 tool_registry.execute_tool 适配）——这里同样适配一层。
    """
    token = tool_scope.begin(user_id="u1", scopes=frozenset(scopes))
    try:
        return tool_pipeline.call_tool_bounded(
            spec.name, args, spec=spec,
            handler=lambda name, call_args: spec.handler(call_args),
            default_timeout=5.0)
    finally:
        tool_scope.reset(token)


def _declare(dataset="mcp:fetch:fetch_url", provider="mcp:fetch", ttl=300.0):
    """执行路径测试没走挂载，手工登记数据集（生产里由 mount_all 做）。"""
    if dataset not in external_source.DATASETS:
        external_source.REGISTRY.declare_dataset(external_source.DatasetSpec(
            name=dataset, provider=provider, label="测试数据集", ttl_s=ttl))


def test_handler_success_envelope_with_external_meta(fresh_external):
    session = FakeSession([], results={"fetch_url": {
        "is_error": False, "text": "# 某页内容", "structured": {"title": "某页"}}})
    handler = mcp_tools.make_handler(
        "mcp_fetch_fetch_url", "fetch_url", "mcp:fetch:fetch_url", session, "low")
    spec = mcp_tools.build_spec(_config(), _tool(), handler=handler)

    env = _enroll_and_call(spec, {"url": "https://example.com"}, DEFAULT_USER_SCOPES | {"mcp:fetch:read"})
    assert tc.is_ok(env)
    assert env["data"]["content"] == "# 某页内容"
    assert env["data"]["structured"]["title"] == "某页"
    assert env["meta"]["source"] == "mcp:fetch:fetch_url"
    assert env["meta"]["trust"] == "low"
    assert session.calls == [("fetch_url", {"url": "https://example.com"})]

    # 内容进上下文时被 <external_data> 包裹（注入防护边界）
    content = tc.to_tool_content(spec.name, env, max_chars=4000, spec=spec)
    assert "<external_data" in content
    assert "</external_data>" in content


def test_handler_scope_denied(fresh_external):
    session = FakeSession([], results={"fetch_url": {"is_error": False, "text": "x"}})
    handler = mcp_tools.make_handler(
        "mcp_fetch_fetch_url", "fetch_url", "mcp:fetch:fetch_url", session, "low")
    spec = mcp_tools.build_spec(_config(), _tool(), handler=handler)

    env = _enroll_and_call(spec, {"url": "https://example.com"}, DEFAULT_USER_SCOPES)
    assert tc.error_code(env) == tc.POLICY_DENIED
    assert session.calls == []  # 拒绝发生在调用之前


def test_handler_server_side_error_is_answer_not_failure(fresh_external):
    session = FakeSession([], results={"fetch_url": {
        "is_error": True, "text": "404 not found", "structured": None}})
    handler = mcp_tools.make_handler(
        "mcp_fetch_fetch_url", "fetch_url", "mcp:fetch:fetch_url", session, "low")
    spec = mcp_tools.build_spec(_config(), _tool(), handler=handler)

    env = _enroll_and_call(spec, {"url": "https://nope"}, DEFAULT_USER_SCOPES | {"mcp:fetch:read"})
    # ok=true：远端工具的错误答案是"答案"，不是上游故障（不能触发熔断语义）
    assert tc.is_ok(env)
    assert env["data"]["is_error"] is True
    assert "404" in env["data"]["error_text"]


def test_handler_transport_failure_trips_breaker(fresh_external, monkeypatch):
    # 熔断阈值调小，避免测试里打满 3 次
    monkeypatch.setattr(external_source, "REGISTRY",
                        external_source.ExternalSourceRegistry(failure_threshold=2, cooldown_s=300.0))
    _declare()
    session = FakeSession([], fail_calls=True)
    handler = mcp_tools.make_handler(
        "mcp_fetch_fetch_url", "fetch_url", "mcp:fetch:fetch_url", session, "low")
    spec = mcp_tools.build_spec(_config(), _tool(), handler=handler)

    scopes = DEFAULT_USER_SCOPES | {"mcp:fetch:read"}
    for _ in range(2):
        env = _enroll_and_call(spec, {"url": "https://example.com"}, scopes)
        assert tc.error_code(env) == tc.SOURCE_UNAVAILABLE

    # 连续失败后：数据集被判定不可用 → tool_sets 装填会把它摘掉
    assert not external_source.available("mcp:fetch:fetch_url")
    from types import SimpleNamespace

    from agent import tool_sets

    enrolled = tool_sets.enroll(SimpleNamespace(scopes=scopes))
    assert "mcp_fetch_fetch_url" not in enrolled


def test_handler_result_cached_by_ttl(fresh_external):
    _declare()
    session = FakeSession([], results={"fetch_url": {"is_error": False, "text": "same"}})
    handler = mcp_tools.make_handler(
        "mcp_fetch_fetch_url", "fetch_url", "mcp:fetch:fetch_url", session, "low")
    spec = mcp_tools.build_spec(_config(), _tool(), handler=handler)
    scopes = DEFAULT_USER_SCOPES | {"mcp:fetch:read"}

    _enroll_and_call(spec, {"url": "https://example.com"}, scopes)
    env2 = _enroll_and_call(spec, {"url": "https://example.com"}, scopes)
    assert env2["meta"].get("cached") is True
    assert len(session.calls) == 1  # 第二次命中 TTL 缓存，没打远端


# ==================== 配置解析 ====================


def test_load_server_configs_parses_env(monkeypatch):
    monkeypatch.setenv("MCP_SERVERS", "fetch, fs ")
    monkeypatch.setenv("MCP_FETCH_TRANSPORT", "http")
    monkeypatch.setenv("MCP_FETCH_URL", "http://127.0.0.1:9101/mcp")
    monkeypatch.setenv("MCP_FETCH_TRUST", "high")  # 非法 → 回落 low
    monkeypatch.setenv("MCP_FS_TRANSPORT", "stdio")
    monkeypatch.setenv("MCP_FS_COMMAND", "node")
    monkeypatch.setenv("MCP_FS_ARGS", "server.js, --port, 9100")
    monkeypatch.setenv("MCP_FS_TTL_S", "120")

    configs = mcp_client.load_server_configs()
    by_name = {c.name: c for c in configs}
    assert set(by_name) == {"fetch", "fs"}
    assert by_name["fetch"].trust == "low"          # high 被拒
    assert by_name["fs"].command == "node"
    assert by_name["fs"].args == ("server.js", "--port", "9100")
    assert by_name["fs"].ttl_s == 120.0


def test_load_server_configs_empty(monkeypatch):
    monkeypatch.delenv("MCP_SERVERS", raising=False)
    assert mcp_client.load_server_configs() == []


def test_load_server_configs_missing_url_skipped(monkeypatch):
    monkeypatch.setenv("MCP_SERVERS", "broken")
    monkeypatch.setenv("MCP_BROKEN_TRANSPORT", "http")
    # 没配 URL
    configs = mcp_client.load_server_configs()
    assert configs == []
