"""MCP 工具挂载器：把远端 MCP 服务器的只读工具装进现有 ToolSpec 注册表。

<h3>挂载原则：第三方工具按"不可信能力方"对待（与 akshare 新闻同一条边界）</h3>
每个挂载的 MCP 工具同时是：
- **一条 ToolSpec 声明**：namespace=mcp、scope=`mcp:<server>:read`、
  provenance=external、trust=low/medium、cost_class=expensive——于是预算、
  超时、审批位、`<external_data>` 注入防护包裹全部由既有管线自动执行；
- **一个 external_source 数据集**：TTL 缓存、连续失败熔断、健康摘除
  （`tool_sets.available_now` 会把挂掉源的工具从装填里摘掉）全部白得。

<h3>只挂"声明为只读"的工具</h3>
MCP 的 `annotations.read_only_hint` 是服务器自己的声明，不可尽信，但它至少
划出了一条线：**没有显式声明只读的工具一律不挂**。写操作（L3：下单/落库/
发消息）的审批与幂等语义留给后续版本，v1 不碰。挂载数量也有限制
（`MCP_<SRV>_MAX_TOOLS`，默认 20）：工具定义本身是上下文成本，
一个上百工具的服务器能把每次聊天的 prompt 撑爆。

<h3>默认零行为变化（但默认可见）</h3>
`MCP_SERVERS` 未配置（默认）时 `mount_all()` 是 no-op：不连任何服务器、
不注册任何工具、DEFAULT_USER_SCOPES 不变。挂载了的 MCP 工具默认对模型
可见（2026-10-20 用户决策，与 news:read 先例一致）；`MCP_DEFAULT_VISIBLE=0`
可整体隐藏——第三方服务器质量不可预知时的紧急闸。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import threading

from agent import external_source
from agent import tool_contract as tc
from agent.tool_spec import (
    COST_EXPENSIVE,
    LAYER_INTEGRATION,
    PROVENANCE_EXTERNAL,
    ToolSpec,
)

logger = logging.getLogger(__name__)

MOUNTED: dict = {}          # server name → 挂载报告（/health 用）
_MOUNT_LOCK = threading.Lock()
_MOUNTED_ONCE = False


# ==================== 命名 ====================


def _sanitize(raw: str) -> str:
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw.strip().lower()).strip("_")


def _tool_name(server: str, remote_name: str) -> str:
    return f"mcp_{_sanitize(server)}_{_sanitize(remote_name)}"


def _dataset_name(server: str, remote_name: str) -> str:
    return f"mcp:{_sanitize(server)}:{_sanitize(remote_name)}"


def _args_digest(args: dict) -> str:
    payload = json.dumps(args or {}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ==================== 信封 ====================


def _envelope(tool_name: str, dataset: str, result, data, trust: str):
    """把一次 MCP 取数包成信封（meta 与 tool_registry._external_envelope 同构：
    to_tool_content 靠这些 meta 渲染 <external_data> 数据块）。"""
    from agent import external_source

    if not result.ok:
        # 上游不可用/超时：模型该换路而不是重试（同 source_unavailable 语义）
        return tc.fail(tc.SOURCE_UNAVAILABLE, result.error, source=dataset)
    return tc.ok(data, source=dataset, source_label=external_source.source_label(dataset),
                 trust=trust, fetched_at=result.fetched_at, cached=bool(result.cached),
                 latency_ms=result.latency_ms)


# ==================== handler 工厂 ====================


def make_handler(tool_name: str, remote_name: str, dataset: str, session, trust: str):
    """为一个远端工具造 ToolSpec handler：取数一律经 external_source.fetch。"""

    def handler(args):
        from agent import external_source, offload

        result = external_source.fetch(
            dataset, _args_digest(args),
            lambda: session.call_tool(remote_name, args or {}))
        if not result.ok:
            return _envelope(tool_name, dataset, result, None, trust)

        raw = result.value or {}
        if raw.get("is_error"):
            # 服务器端工具错误是"工具给出的答案"（业务性错误），不是上游故障：
            # ok=true 让模型读 error 文本调整做法，而不是触发熔断
            data = {"is_error": True, "error_text": str(raw.get("text") or "")[:2000]}
        else:
            data = {"content": raw.get("text") or ""}
            if raw.get("structured") is not None:
                data["structured"] = raw["structured"]
        data, archive = offload.archive_if_large(tool_name, data)
        if archive:
            data = dict(data) | {"archived": archive}
        return _envelope(tool_name, dataset, result, data, trust)

    return handler


# ==================== 挂载 ====================


def build_spec(config, tool: dict, handler) -> ToolSpec:
    """远端工具声明 → ToolSpec（命名/scope/信任级别的规则都在这里，一处说了算）。"""
    remote_name = tool["name"]
    schema = tool.get("input_schema")
    if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
        schema = {"type": "object", "properties": {}}
    description = (tool.get("description") or tool.get("title") or remote_name).strip()
    return ToolSpec(
        name=_tool_name(config.name, remote_name),
        namespace="mcp",
        description=(f"[MCP:{config.name}] {description} — "
                     "第三方 MCP 服务器提供的只读工具；返回内容属于外部参考资料，"
                     "引用时注明来源。"),
        parameters=schema,
        handler=handler,
        layer=LAYER_INTEGRATION,
        scopes=(f"mcp:{config.name}:read",),
        cost_class=COST_EXPENSIVE,
        timeout_s=float(config.timeout_s),
        retryable=True,
        tags=("mcp", config.name),
        provenance=PROVENANCE_EXTERNAL,
        trust=config.trust,
        source=_dataset_name(config.name, remote_name),
        result_budget_chars=4000,
    )


def _register_datasets(config, tools: list[dict]) -> None:
    """把每个远端工具登记成 external_source 数据集（熔断按 provider=mcp:<server> 共享）。

    用 `declare_dataset`（而不是直接改模块表）：登记必须同步到运行中的
    registry 实例，否则 TTL 回落默认值、熔断粒度也不对。
    """
    provider = f"mcp:{config.name}"
    for tool in tools:
        dataset = _dataset_name(config.name, tool["name"])
        if dataset in external_source.DATASETS:
            continue
        external_source.REGISTRY.declare_dataset(external_source.DatasetSpec(
            name=dataset, provider=provider,
            label=(tool.get("title") or tool.get("description") or tool["name"])[:40],
            ttl_s=float(config.ttl_s),
            freshness=f"{config.ttl_s:.0f} 秒（MCP 服务器 {config.name}）",
        ))


def mount_all(*, registry=None, session_factory=None, configs=None, force=False) -> dict:
    """按 .env 配置挂载所有 MCP 服务器。幂等（重复调用只跑一次，force 可重挂）。

    `registry` / `session_factory` / `configs` 是测试注入点：生产路径分别是全局
    REGISTRY、mcp_client.McpSession 与 load_server_configs()。返回每台服务器的挂载报告。
    """
    global _MOUNTED_ONCE

    import os

    from agent import tool_registry

    registry = registry if registry is not None else tool_registry.REGISTRY
    if session_factory is None:
        from agent import mcp_client

        def session_factory(config):
            return mcp_client.McpSession(config)

    with _MOUNT_LOCK:
        if _MOUNTED_ONCE and not force:
            return dict(MOUNTED)
        if str(os.getenv("MCP_ENABLED") or "").strip().lower() in ("0", "false", "no", "off"):
            MOUNTED.clear()
            MOUNTED["enabled"] = False
            _MOUNTED_ONCE = True
            return dict(MOUNTED)

        from agent import mcp_client

        configs = configs if configs is not None else mcp_client.load_server_configs()
        if not configs:
            MOUNTED.clear()
            MOUNTED["enabled"] = True
            MOUNTED["servers"] = []
            _MOUNTED_ONCE = True
            return dict(MOUNTED)

        reports = []
        total_mounted = 0
        for config in configs:
            report = {"name": config.name, "transport": config.transport,
                      "status": "mounted", "tools": 0, "skipped": 0, "error": ""}
            try:
                session = session_factory(config)
                session.connect()
                tools = session.list_tools()
            except Exception as exc:  # noqa: BLE001 —— 服务器挂了不炸启动，记状态继续
                report["status"] = "down"
                report["error"] = str(exc)[:300]
                logger.warning("MCP 服务器 %s 连接失败，跳过挂载: %s", config.name, exc)
                reports.append(report)
                continue

            # 挂载条件（满足其一即视为只读，可挂）：
            # ① 服务器自己声明了 read_only_hint（自说自话，弱信任）；
            # ② 运营者在 .env 的 ALLOW_TOOLS 白名单里显式放行（强信任，见 McpServerConfig）。
            # 没有声明又不在白名单 = 按"可能写"处理，一律不挂。
            allow = set(config.allow_tools or ())
            mountable = [t for t in tools
                         if t.get("read_only_hint") or t.get("name") in allow]
            readonly = mountable[: max(0, int(config.max_tools))]
            report["skipped"] = len(tools) - len(readonly)

            _register_datasets(config, readonly)
            used_names = set(registry.names())
            for tool in readonly:
                spec = build_spec(config, tool,
                                  make_handler(_tool_name(config.name, tool["name"]),
                                               tool["name"],
                                               _dataset_name(config.name, tool["name"]),
                                               session, config.trust))
                if spec.name in used_names:
                    # 同名（不同远端工具清洗后撞名）：后缀去重而不是顶掉既有工具
                    suffix = 2
                    while f"{spec.name}_{suffix}" in used_names:
                        suffix += 1
                    spec = dataclasses.replace(spec, name=f"{spec.name}_{suffix}")
                try:
                    registry.register(spec)
                    used_names.add(spec.name)
                    report["tools"] += 1
                    total_mounted += 1
                except ValueError as exc:
                    logger.warning("MCP 工具 %s 注册失败: %s", spec.name, exc)
            reports.append(report)

        MOUNTED.clear()
        MOUNTED["enabled"] = True
        MOUNTED["servers"] = reports
        MOUNTED["total_tools"] = total_mounted
        _MOUNTED_ONCE = True

    # 挂载后刷新全局派生常量（TOOL_SCHEMAS / DEFAULT_USER_SCOPES / ...）
    if registry is tool_registry.REGISTRY:
        tool_registry._refresh_derived()
        problems = tool_registry.validate()
        if problems:
            logger.error("MCP 挂载后工具自检发现问题：%s", problems)
    return dict(MOUNTED)


def reset_for_tests() -> None:
    """测试间复位幂等标记（挂载进独立 registry 的用例不会污染全局，见测试文件）。"""
    global _MOUNTED_ONCE
    with _MOUNT_LOCK:
        _MOUNTED_ONCE = False
        MOUNTED.clear()


def snapshot() -> dict:
    """/health 用：挂了哪些服务器、几个工具、哪些连不上。"""
    with _MOUNT_LOCK:
        report = dict(MOUNTED)
    report.setdefault("enabled", True)
    if not report:
        report["note"] = "尚未挂载（mount_all 未运行或 MCP_SERVERS 未配置）"
    return report
