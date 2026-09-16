"""工具执行管线（T0）：策略阶段 + 有界执行 + 统一信封。

<h3>单一入口</h3>
所有工具调用都走 `call_tool_bounded`，于是策略只需要在一个地方实现：

    校验参数 → 授权(scopes) → 预算 → 审批 → 执行(超时/有界) → 信封

改造前这些事要么没有、要么散落在每个 handler 里（每个 handler 自己 `_require_symbol`、
自己 `try/except`），新增工具时全靠"记得也写一遍"。

<h3>与注册表的职责边界</h3>
- **注册表**负责"这个工具存在吗、怎么执行"（未登记的名字由它返回 `unknown_tool`）；
- **管线**负责"这一次调用允许吗"。

这样分开是为了让"策略"和"能力"各自可测：测试策略时不必准备真实工具，
测试工具时不必构造授权环境。也因此，管线对**未登记**的工具只做执行（不套策略）——
那种调用在生产里到不了这里（注册表先拦了），但测试替身需要它。

<h3>拒绝 ≠ 故障</h3>
`policy_denied` / `budget_exceeded` / `needs_approval` 与 `tool_error` / `tool_timeout`
是两类事：前者是"这次不该做"，模型该换做法或请用户确认；
后者是"做不成"，模型可以重试或换路。混在一起会让模型对着权限问题反复重试。
"""
from __future__ import annotations

import contextvars
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

from agent import tool_contract as tc
from agent import tool_scope
from agent.tool_spec import APPROVAL_NONE, COST_EXPENSIVE, LAYER_PURE, SIDE_EFFECT_NONE

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_WORKERS = 4
DEFAULT_CAPACITY = 8


class BoundedToolPool:
    """有界工具执行池：超时就报超时，排队满了就明确拒绝，绝不无界堆积。

    上一版实现（`_DaemonWorkerPool` + `threading.Event` 等待）有两个问题：

    1. 队列无上界、worker 只有 2 个 —— **一次卡住的 akshare 调用就能占满全部 worker**，
       之后的工具调用全部静默排队，整轮 agent 表现为"想很久然后超时"。
    2. 超时只是调用方不再等待，工具线程仍在跑（Python 线程无法被强杀）。
       所以这里不再假装"取消"，而是给出两种明确结果：
       - `tool_timeout`：等了超过预算，底层可能仍在跑，但结果已丢弃；
       - `tool_busy`：在跑的任务已达容量上限，直接拒绝，让模型知道"现在忙"。

    容量计数在任务**真正结束**时才释放，卡死的任务会一直占着名额，
    这正是我们想要的：宁可拒绝新调用，也不让线程无限堆积。
    """

    def __init__(self, max_workers=DEFAULT_MAX_WORKERS, capacity=DEFAULT_CAPACITY):
        self._capacity = max(1, int(capacity))
        self._slots = threading.BoundedSemaphore(self._capacity)
        self._pool = ThreadPoolExecutor(max_workers=max(1, int(max_workers)),
                                        thread_name_prefix="agent-tool")

    @property
    def in_flight(self):
        """当前占用名额的任务数（包含仍在后台跑、已经超时返回的"卡死"任务）。"""
        return self._capacity - self._slots._value

    def submit(self, fn, args, timeout):
        if not self._slots.acquire(blocking=False):
            return tc.fail(tc.TOOL_BUSY,
                           f"工具正在忙（同时最多 {self._capacity} 个），请稍后重试",
                           retryable=True)
        try:
            # 必须显式把上下文复制进工作线程：ThreadPoolExecutor.submit 不会自动传递
            # contextvars，少了这一步，工具里通过 tool_scope 读到的身份永远是空的
            # （memory_search 这类"查当前用户"的工具就废了）。
            context = contextvars.copy_context()
            future = self._pool.submit(context.run, fn, *args)
        except RuntimeError as exc:  # 池已关闭
            self._slots.release()
            return tc.fail(tc.TOOL_ERROR, f"工具执行池不可用：{exc}")

        # 名额在任务真正结束时释放（而不是等待结束时）
        future.add_done_callback(lambda _future: self._slots.release())

        try:
            return tc.normalize(future.result(timeout=timeout))
        except FutureTimeoutError:
            return tc.fail(tc.TOOL_TIMEOUT, f"tool {args[0]} timed out after {timeout}s",
                           retryable=True,
                           note="底层调用可能仍在后台执行，但结果已被丢弃。")
        except Exception as exc:  # noqa: BLE001 —— 执行边界兜住所有异常
            return tc.fail(tc.TOOL_ERROR, f"tool {args[0]} failed: {exc}", retryable=True)


EXECUTOR = BoundedToolPool()


# ==================== 参数校验（JSON Schema 的一个够用子集） ====================

_TYPE_CHECKS = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
}


def validate_args(spec, args) -> tuple:
    """校验并轻度规整入参。返回 (规整后的 args, 错误说明 or None)。

    宽容策略（有意）：模型常把数字写成字符串（"days": "60"），
    能安全转换就转，不去为难它 —— 因为"拒绝一个本来能跑的调用"是纯粹的体验损失。
    真正无法解释的类型才拒绝，并明确告诉它哪个字段错了。
    """
    if not isinstance(args, dict):
        return {}, f"参数必须是 JSON 对象，收到 {type(args).__name__}"

    schema = spec.parameters or {}
    properties = schema.get("properties") or {}
    cleaned = dict(args)

    for field in schema.get("required") or ():
        if cleaned.get(field) in (None, ""):
            return cleaned, f"缺少必填参数 {field}"

    for field, rule in properties.items():
        if field not in cleaned:
            if "default" in rule:
                cleaned[field] = rule["default"]     # 默认值在这里统一注入，handler 不必再兜
            continue
        value = cleaned[field]
        expected = rule.get("type")
        if expected == "integer" and isinstance(value, str) and value.strip().lstrip("-").isdigit():
            cleaned[field] = int(value)
            value = cleaned[field]
        elif expected == "number" and isinstance(value, str):
            try:
                cleaned[field] = float(value)
                value = cleaned[field]
            except ValueError:
                pass
        check = _TYPE_CHECKS.get(expected)
        if check and not check(value):
            return cleaned, f"参数 {field} 类型应为 {expected}，收到 {type(value).__name__}"
        if "enum" in rule and value not in rule["enum"]:
            return cleaned, f"参数 {field} 只能是 {rule['enum']} 之一"
        if expected in ("integer", "number"):
            if "minimum" in rule and value < rule["minimum"]:
                cleaned[field] = rule["minimum"]
            if "maximum" in rule and value > rule["maximum"]:
                cleaned[field] = rule["maximum"]
    return cleaned, None


# ==================== 策略阶段 ====================

def check_scope(spec, ctx) -> str:
    """授权：调用者是否被授予这个工具要求的能力。

    注意这里只做**能力**判断（"能不能用这类工具"）。**资源归属**（"这条数据是不是他的"）
    必须留在各自 handler 里 —— 只有它知道自己的资源怎么定位（记忆按 user_id 过滤即是一例）。
    """
    missing = [scope for scope in (spec.scopes or ()) if scope not in ctx.scopes]
    if missing:
        return f"当前用户没有调用 {spec.name} 的权限（缺少 {'、'.join(missing)}）"
    return ""


def check_approval(spec, ctx) -> str:
    """审批：有副作用的动作必须有人点头才执行。

    T0 只提供门禁（`approvals` 里有没有这一项）。真实交互在 T3：
    走 MCP 的 MRTR（工具返回 `input_required`，客户端带 `inputResponses` 重试）或前端确认。
    """
    if spec.approval == APPROVAL_NONE:
        return ""
    if spec.name in ctx.approvals:
        return ""
    return (f"{spec.name} 会改变系统状态，需要先确认再执行"
            f"（这在当前流程里还没有接通用户确认通道）")


def check_source(spec) -> str:
    """外部数据源可用性门禁（T2a）：上游已经判定不可用时，连调用都不发起。

    为什么放在管线而不是每个 handler 里：外部数据源挂掉时，最坏的结果不是报错，
    而是**每次调用都白等一次超时**（新闻接口 15s × 模型重试 3 次 = 整轮 45s）。
    门禁在调用前生效，才有机会把这个代价降为零。
    """
    source = getattr(spec, "source", None)
    if not source:
        return ""
    try:
        from agent import external_source

        return external_source.REGISTRY.unavailable_reason(source)
    except Exception as exc:  # noqa: BLE001 —— 门禁机制出问题不能把工具调用带崩（宁可放行）
        logger.warning("外部源门禁检查异常，本次放行 tool=%s: %s", spec.name, exc)
        return ""


def policy_check(spec, args, ctx):
    """按顺序跑策略阶段。返回 (规整后的 args, 拒绝信封 or None)。"""
    cleaned, problem = validate_args(spec, args)
    if problem:
        return cleaned, tc.fail(tc.INVALID_ARGS, problem)

    denied = check_scope(spec, ctx)
    if denied:
        return cleaned, tc.fail(tc.POLICY_DENIED, denied)

    approval = check_approval(spec, ctx)
    if approval:
        return cleaned, tc.fail(tc.NEEDS_APPROVAL, approval)

    unavailable = check_source(spec)
    if unavailable:
        return cleaned, tc.fail(tc.SOURCE_UNAVAILABLE, unavailable, source=spec.source)

    # 预算放在授权与审批之后：被拒绝的调用不该消耗额度
    try:
        reason = ctx.budget.reserve(spec)
    except Exception as exc:  # noqa: BLE001 —— 配额机制出问题不能把工具调用带崩
        logger.warning("工具预算检查异常，本次放行：%s", exc)
        reason = None
    if reason:
        # 把配额状态一起给出去：模型看到"还剩几次"比只看到"超了"更有用，
        # 运维排查时也不必再去猜当时扣到了什么程度。
        return cleaned, tc.fail(tc.BUDGET_EXCEEDED, reason, budget=ctx.budget.snapshot())

    return cleaned, None


def resolve_timeout(spec, explicit=None, default=DEFAULT_TIMEOUT) -> float:
    if explicit is not None:
        return explicit
    if spec is not None and spec.timeout_s:
        return spec.timeout_s
    return default


def _cache_key(name, args, spec=None) -> str:
    """同轮复用的键：优先用工具声明的归一化函数，否则按 JSON 规范序比较原文。

    归一化的意义：**语义等价的参数必须得到同一个键**。模型给的策略 JSON 与
    内部 pydantic 归一化后的 `model_dump()` 差一批默认值，按原文比较会让同一次回测
    在整轮里被执行两次（真实跑批实测到过，见设计文档 §12）。
    """
    import json

    if spec is not None and spec.cache_key is not None:
        try:
            normalized = spec.cache_key(args)
            if normalized:
                return f"{name}:{normalized}"
        except Exception as exc:  # noqa: BLE001 —— 归一化失败就退回原文比较，不能因此不执行
            logger.debug("cache_key 归一化失败，退回原文比较 tool=%s: %s", name, exc)
    return f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"


def call_tool_bounded(name, args, *, handler, spec=None, timeout=None,
                      default_timeout=DEFAULT_TIMEOUT, ctx=None, pool=None):
    """唯一入口：策略 → （同轮复用）→ 执行 → 信封。永不抛异常。

    <h3>为什么这里也负责计量（P1）</h3>
    因为这是**唯一入口**：拒绝、复用、执行三个结果都从这里出去，
    把计数放在这里就不可能出现"某条路径没记上"。这也是"先能看见，再能限制"的落点 ——
    后面的跨请求配额要用这里的数字定阈值。
    """
    from agent import metering

    ctx = ctx if ctx is not None else tool_scope.context()
    effective_timeout = resolve_timeout(spec, timeout, default_timeout)

    if spec is not None:
        args, rejected = policy_check(spec, args, ctx)
        if rejected is not None:
            code = (rejected.get("error") or {}).get("code") or "unknown"
            # 被拒绝的调用**不计入 tool_calls**：否则"这个工具被用了多少次"就被污染了
            metering.record_refusal(name, code)
            logger.info("工具调用被策略拒绝 tool=%s code=%s", name, code)
            return rejected

        # 同轮同参只算一次：只读工具可以复用，有副作用的动作绝不能（那是漏执行）
        if spec.side_effect == SIDE_EFFECT_NONE:
            cache_key = _cache_key(name, args, spec)
            cached = ctx.turn_cache.get(cache_key)
            if cached is not None:
                logger.info("工具结果本轮复用 tool=%s（省下一次真实调用）", name)
                metering.record_tool_reuse(name)
                replay = tc.normalize(cached)
                replay["meta"] = {**(replay.get("meta") or {}), "reused_in_turn": True}
                return replay
    elif not isinstance(args, dict):
        metering.record_refusal(name, tc.INVALID_ARGS)
        return tc.fail(tc.INVALID_ARGS, f"参数必须是 JSON 对象，收到 {type(args).__name__}")

    executor = pool if pool is not None else EXECUTOR
    started = time.time()
    envelope = executor.submit(handler, (name, args), effective_timeout)
    latency_ms = int((time.time() - started) * 1000)

    metering.record_tool_call(
        name, ok=tc.is_ok(envelope),
        code=(envelope.get("error") or {}).get("code", ""),
        latency_ms=latency_ms,
        result_chars=tc.size_of(envelope),
        cost_class=getattr(spec, "cost_class", "cheap") or "cheap")

    if spec is not None and spec.side_effect == SIDE_EFFECT_NONE and tc.is_ok(envelope):
        # 只缓存成功的只读结果：失败的原因可能是瞬时的，不该在整轮里被固化。
        # 注意：缓存键用的是**归一化后的键**，所以同一份策略 JSON 无论是否补齐默认值都命中同一个键。
        ctx.turn_cache.put(_cache_key(name, args, spec), envelope)
    return envelope


def budget_snapshot() -> dict:
    return tool_scope.context().budget.snapshot()


def is_expensive(spec) -> bool:
    return bool(spec) and spec.cost_class == COST_EXPENSIVE


def is_pure(spec) -> bool:
    return bool(spec) and spec.layer == LAYER_PURE
