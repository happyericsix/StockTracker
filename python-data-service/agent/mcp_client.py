"""MCP 客户端：官方 SDK 的同步 façade（2026-10-20）。

<h3>为什么需要一座"异步桥"</h3>
官方 `mcp` SDK 是全异步的（anyio/httpx2），而本服务是同步 requests +
`BoundedToolPool`（ThreadPoolExecutor）的线程模型。与其把整条工具管线改成
asyncio（牵动 tool_pipeline / react_agent / 全部测试），不如起一个**后台事件
循环线程**，把协程经 `asyncio.run_coroutine_threadsafe` 投过去同步等结果——
所有跨环调用都带 `wait_for` 超时，绝不无限等待（这是死锁防线，不是功能）。

<h3>失败语义（与 external_source 的笼子对接）</h3>
- **传输层失败**（连不上/断线/超时）：抛 `McpError`。调用方把调用包成
  `external_source.fetch` 的 producer，异常正好被记成"上游失败"，
  连续 3 次进熔断——我们**不**在客户端里自建第二套健康状态。
- **服务器端工具失败**（`is_error=True`）：不抛，作为正常 payload 返回
  （这是"工具给出了否定/错误答案"，不是"上游不可用"）。
- `list_tools` 做了分页兜底与数量上限，防一个失控服务器用工具清单灌爆内存。

<h3>配置（.env，风格与 DEEPSEEK_*/JAVA_BASE_URL 一致，惰性读取）</h3>
    MCP_SERVERS=fetch,db            # 逗号分隔短名；空 = 不挂载（默认）
    MCP_FETCH_TRANSPORT=http        # http（Streamable HTTP）| stdio（本地进程）
    MCP_FETCH_URL=http://127.0.0.1:8001/mcp
    MCP_FS_COMMAND=node             # stdio：命令与参数
    MCP_FS_ARGS=server.js,--port,9100
    MCP_FETCH_API_KEY=...           # http：Authorization: Bearer
    MCP_FETCH_TIMEOUT_S=20          # 单次调用超时
    MCP_FETCH_TRUST=low             # low|medium（high 一律拒绝：第三方内容不配）
    MCP_FETCH_TTL_S=300             # 结果缓存 TTL（external_source 数据集级）
    MCP_FETCH_MAX_TOOLS=20          # 该服务器最多挂载几个工具（上下文成本闸）
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 20.0
DEFAULT_TTL_S = 300.0
DEFAULT_MAX_TOOLS = 20
# list_tools 分页兜底：一个服务器声明超过这个数的工具就不再翻页
MAX_LIST_PAGES = 10

VALID_TRUST = ("low", "medium")


class McpError(RuntimeError):
    """传输层失败（连接/断线/超时）。上层用它喂 external_source 的熔断计数。"""


# ==================== 后台事件循环线程（进程内单例） ====================


class _LoopBridge:
    """一个 daemon 线程跑一个 asyncio 事件循环；首次使用时懒启动。"""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def _ensure(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop
            self._loop = asyncio.new_event_loop()
            thread = threading.Thread(target=self._run, name="mcp-async-bridge", daemon=True)
            thread.start()
            return self._loop

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def call(self, coro, timeout_s: float):
        """同步执行一个协程，带超时（超时会在循环侧取消协程，不留悬挂任务）。"""
        loop = self._ensure()
        future = asyncio.run_coroutine_threadsafe(
            asyncio.wait_for(coro, timeout_s), loop)
        try:
            return future.result(timeout_s + 5.0)
        except asyncio.TimeoutError as exc:
            raise McpError(f"MCP 调用超时（>{timeout_s:.0f}s）") from exc
        except Exception as exc:
            raise McpError(str(exc)) from exc


_BRIDGE = _LoopBridge()


# ==================== 服务器配置 ====================


@dataclass(frozen=True)
class McpServerConfig:
    """一台 MCP 服务器的全部声明。名字必须先过 `_safe_name` 再进来。"""

    name: str
    transport: str = "http"          # http | stdio
    url: str = ""
    command: str = ""
    args: tuple = ()
    api_key: str = ""
    timeout_s: float = DEFAULT_TIMEOUT_S
    trust: str = "low"
    ttl_s: float = DEFAULT_TTL_S
    max_tools: int = DEFAULT_MAX_TOOLS
    # 运营者显式放行的工具名：白名单里的工具**没有** readOnlyHint 声明也按只读挂载。
    # 为什么需要它：readOnlyHint 是服务器**自说自话**的声明（Python 参考实现普遍不填），
    # 运营者白名单是更强的信任决策——"我确认这台服务器上的这几个工具是只读的"。
    allow_tools: tuple = ()


def _safe_name(raw: str) -> str:
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw.strip().lower()).strip("_")


def _env(name: str, server: str) -> str:
    return os.getenv(f"MCP_{server.upper()}_{name}", "").strip()


def _env_int(name: str, server: str, default: float) -> float:
    raw = _env(name, server)
    try:
        return float(raw) if raw else default
    except (TypeError, ValueError):
        return default


def load_server_configs() -> list[McpServerConfig]:
    """解析 MCP_SERVERS + 每台的 MCP_<NAME>_* 声明。坏声明跳过并记日志，不炸启动。"""
    raw = os.getenv("MCP_SERVERS", "") or ""
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        return []

    configs: list[McpServerConfig] = []
    for raw_name in names:
        name = _safe_name(raw_name)
        if not name:
            continue
        transport = (_env("TRANSPORT", name) or "http").lower()
        if transport not in ("http", "stdio"):
            logger.warning("MCP 服务器 %s 的 TRANSPORT=%r 不认识，跳过", name, transport)
            continue
        trust = (_env("TRUST", name) or "low").lower()
        if trust not in VALID_TRUST:
            # high 一律不收：第三方内容以"高可信内部事实"的身份混进来正是
            # external_source 边界要防的事（tool_registry.validate 也会拦）
            logger.warning("MCP 服务器 %s 的 TRUST=%r 非法（只允许 low/medium），按 low 处理",
                           name, trust)
            trust = "low"
        if transport == "http" and not _env("URL", name):
            logger.warning("MCP 服务器 %s 是 http 传输但没有 URL，跳过", name)
            continue
        if transport == "stdio" and not _env("COMMAND", name):
            logger.warning("MCP 服务器 %s 是 stdio 传输但没有 COMMAND，跳过", name)
            continue
        args_raw = _env("ARGS", name)
        allow_raw = _env("ALLOW_TOOLS", name)
        configs.append(McpServerConfig(
            name=name,
            transport=transport,
            url=_env("URL", name),
            command=_env("COMMAND", name),
            args=tuple(a.strip() for a in args_raw.split(",") if a.strip()),
            api_key=_env("API_KEY", name),
            timeout_s=_env_int("TIMEOUT_S", name, DEFAULT_TIMEOUT_S),
            trust=trust,
            ttl_s=_env_int("TTL_S", name, DEFAULT_TTL_S),
            max_tools=int(_env_int("MAX_TOOLS", name, DEFAULT_MAX_TOOLS)),
            allow_tools=tuple(a.strip() for a in allow_raw.split(",") if a.strip()),
        ))
    return configs


# ==================== 会话 ====================


class McpSession:
    """一台服务器的持久会话：连接 → 列工具/调工具 → 关闭。

    断线后下一次调用前自动重连一次（进程内长期存活的服务不能指望
    启动时那一次握手管一辈子）。
    """

    def __init__(self, config: McpServerConfig):
        self.config = config
        self._session = None
        self._cms: list = []       # 打开的 async context manager 栈（关的时候逆序退出）
        self._http_client = None
        self._busy = threading.Lock()

    # ---------- 生命周期 ----------

    def connect(self) -> None:
        with self._busy:
            if self._session is not None:
                return
            try:
                _BRIDGE.call(self._connect(), self.config.timeout_s)
            except McpError:
                self._cleanup_async()
                raise

    def close(self) -> None:
        with self._busy:
            self._cleanup_async()
            self._session = None

    def _cleanup_async(self) -> None:
        cms, self._cms = self._cms, []
        session, self._session = self._session, None
        http_client, self._http_client = self._http_client, None
        if session is None and not cms:
            return
        try:
            _BRIDGE.call(self._close(cms, http_client), 10.0)
        except Exception as exc:  # noqa: BLE001 —— 关闭失败只记日志，不能影响后续重连
            logger.debug("MCP %s 关闭清理失败: %s", self.config.name, exc)

    async def _connect(self):
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client
        from mcp.client.stdio import StdioServerParameters
        from mcp.client.streamable_http import streamable_http_client

        cfg = self.config
        if cfg.transport == "stdio":
            cm = stdio_client(StdioServerParameters(
                command=cfg.command, args=list(cfg.args or ())))
            read, write = await cm.__aenter__()
            self._cms.append(cm)
        else:
            headers = {}
            if cfg.api_key:
                headers["Authorization"] = f"Bearer {cfg.api_key}"
            if headers:
                import httpx2

                self._http_client = httpx2.AsyncClient(headers=headers, timeout=cfg.timeout_s)
                cm = streamable_http_client(cfg.url, http_client=self._http_client)
            else:
                cm = streamable_http_client(cfg.url)
            # SDK 1.x 的 http 传输返回 3 元组（含 get_session_id），2.x 返回 2 元组；
            # 按位置取前两个，两种形状都吃得下
            streams = await cm.__aenter__()
            read, write = streams[0], streams[1]
            self._cms.append(cm)

        session_cm = ClientSession(read, write)
        self._session = await session_cm.__aenter__()
        self._cms.append(session_cm)
        await self._session.initialize()

    @staticmethod
    async def _close(cms, http_client):
        for cm in reversed(cms):
            try:
                await cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        if http_client is not None:
            try:
                await http_client.aclose()
            except Exception:  # noqa: BLE001
                pass

    def _ensure_connected(self) -> None:
        if self._session is None:
            self.connect()
            if self._session is None:  # connect 抛过异常则直接上抛
                raise McpError(f"MCP 服务器 {self.config.name} 连接失败")

    def _reconnect_once(self) -> None:
        """断线重连：清掉旧栈再连一次。仍失败就上抛（喂熔断计数）。"""
        self._cleanup_async()
        self.connect()

    # ---------- 能力 ----------

    def list_tools(self) -> list[dict]:
        """列工具（带分页兜底）。返回规范化 dict：name/description/input_schema/read_only_hint。"""
        self._ensure_connected()
        try:
            return _BRIDGE.call(self._list_tools(), self.config.timeout_s)
        except McpError:
            self._reconnect_once()
            return _BRIDGE.call(self._list_tools(), self.config.timeout_s)

    async def _list_tools(self) -> list[dict]:
        normalized: list[dict] = []
        cursor = None
        for _ in range(MAX_LIST_PAGES):
            result = await self._session.list_tools()
            for tool in (getattr(result, "tools", None) or []):
                annotations = getattr(tool, "annotations", None)
                normalized.append({
                    "name": str(getattr(tool, "name", "") or ""),
                    "title": str(getattr(tool, "title", "") or ""),
                    "description": str(getattr(tool, "description", "") or ""),
                    "input_schema": getattr(tool, "input_schema", None) or {"type": "object", "properties": {}},
                    # 只认显式 True：没有声明 read_only 的工具按"可能写"处理（挂载侧会拒绝）
                    "read_only_hint": bool(getattr(annotations, "read_only_hint", False)) if annotations else False,
                })
            cursor = getattr(result, "next_cursor", None)
            if not cursor:
                break
        return normalized

    def call_tool(self, name: str, args: dict) -> dict:
        """调用远端工具。传输失败抛 McpError；服务器端错误以 is_error=True 返回。"""
        self._ensure_connected()
        try:
            return _BRIDGE.call(self._call_tool(name, args), self.config.timeout_s)
        except McpError:
            self._reconnect_once()
            return _BRIDGE.call(self._call_tool(name, args), self.config.timeout_s)

    async def _call_tool(self, name: str, args: dict) -> dict:
        result = await self._session.call_tool(
            name, args or None, read_timeout_seconds=self.config.timeout_s)
        # 2.x 可能返回 InputRequiredResult 等中间形态：按"服务器端错误"收敛，
        # 不让它们以成功 payload 的身份混进上下文
        content = getattr(result, "content", None) or []
        texts = [getattr(item, "text", "") for item in content if getattr(item, "type", "") == "text"]
        return {
            "is_error": bool(getattr(result, "is_error", False)),
            "text": "\n".join(t for t in texts if t),
            "structured": getattr(result, "structured_content", None),
        }
