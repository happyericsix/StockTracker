"""工具执行上下文（T0：从"两个身份字段"升级为 ToolContext）。

改造前这里只有 `user_id` / `session_key` 两个值，够 `memory_search` 用，但支撑不了策略层：
授权要 scope、预算要计数器、审计要 request/trace id、审批要"这一轮批过没有"。

<h3>为什么用 contextvars</h3>
身份与预算必须跟着"这一次请求"走，而不是跟着进程走。contextvars 天生按线程/协程隔离，
并发处理两个用户时不会串。

<h3>一个必须注意的细节（P0 踩过）</h3>
工具在线程池里执行，而 `ThreadPoolExecutor.submit()` **不会**把上下文带进工作线程。
所以 `tool_pipeline.BoundedToolPool` 里显式做了 `copy_context().run(...)`。
少了这一步，工具里读到的永远是空的 —— `memory_search` 就会变成"查不到任何人的记忆"。
注意：复制的是上下文**引用**，所以 `budget` 这类可变对象在全轮工具调用间是共享的（这正是我们要的）。

<h3>身份只能由服务端注入</h3>
`user_id` / `scopes` 绝不能让模型自己填 —— 那等于把"查谁的数据、能做什么"交给模型决定。
模型能提供的只有工具参数。
"""
from __future__ import annotations

import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.tool_spec import COST_EXPENSIVE


@dataclass
class ToolBudget:
    """一轮对话内的工具配额。

    为什么要有它：模型在 ReAct 循环里可能反复调同一个工具（尤其是回测这类重活），
    没有配额就只能靠"步数上限"兜底，而步数上限既不精确也不公平
    （查一次行情和跑一次回测的成本差几个数量级）。
    """

    max_calls_per_turn: int = 12
    max_expensive_per_turn: int = 3

    _calls: int = field(default=0, repr=False)
    _expensive: int = field(default=0, repr=False)
    _lock: Any = field(default_factory=threading.Lock, repr=False)

    def reserve(self, spec) -> Optional[str]:
        """预扣一次调用额度。返回 None 表示通过，否则返回拒绝原因。"""
        expensive = getattr(spec, "cost_class", None) == COST_EXPENSIVE
        with self._lock:
            if self._calls >= self.max_calls_per_turn:
                return f"这一轮的工具调用次数已用完（上限 {self.max_calls_per_turn} 次）"
            if expensive and self._expensive >= self.max_expensive_per_turn:
                return f"这一轮的重操作已用完（上限 {self.max_expensive_per_turn} 次）"
            self._calls += 1
            if expensive:
                self._expensive += 1
        return None

    def snapshot(self) -> dict:
        with self._lock:
            return {"calls": self._calls, "expensive": self._expensive,
                    "max_calls_per_turn": self.max_calls_per_turn,
                    "max_expensive_per_turn": self.max_expensive_per_turn}


@dataclass
class TurnCache:
    """一轮之内的只读结果复用。

    为什么需要：工具选择的 golden set 真调模型时抓到模型**在同一轮里把同一个工具
    用同样的参数调了两次**（"我上次说的止损是多少" → memory_search ×2）。
    两次调用 = 两倍延迟与配额消耗，而答案完全一样。这类重复在 ReAct 循环里很常见
    （模型先探一次、再"确认"一次）。

    只对**只读**工具生效（`side_effect == none`）：有副作用的动作绝不能被"缓存"掉，
    那是漏执行。
    """

    _entries: dict = field(default_factory=dict, repr=False)
    _lock: Any = field(default_factory=threading.Lock, repr=False)

    def get(self, key) -> Optional[dict]:
        with self._lock:
            return self._entries.get(key)

    def put(self, key, envelope: dict) -> None:
        with self._lock:
            self._entries[key] = envelope

    def size(self) -> int:
        with self._lock:
            return len(self._entries)


@dataclass(frozen=True)
class ToolContext:
    """一次请求内的工具执行上下文（不可变，budget/cache 内部自带锁）。"""

    user_id: Any = None
    session_key: Optional[str] = None
    request_id: Optional[str] = None
    trace_id: Optional[str] = None
    turn_index: int = 0
    # 调用者被授予的能力；空集合 = 只能调 L1 纯计算工具
    scopes: frozenset = frozenset()
    # 本轮已获用户确认的工具（审批门禁看它；真实流程在 T3 接前端/MRTR）
    approvals: frozenset = frozenset()
    budget: ToolBudget = field(default_factory=ToolBudget)
    # 本轮已算过的只读结果（同参重调直接复用，见 tool_pipeline）
    turn_cache: "TurnCache" = field(default_factory=lambda: TurnCache())
    # 角色（W2）：None = 主 agent（用户正在对话的那个），非空 = 子角色（critic/coach…）。
    #
    # 为什么身份里必须有它：账本与用量事件都是"记在用户身上"的，而子角色的中间步骤
    # **不是用户说过的话**。没有这个字段，子 agent 一进来就会把它的工具日志混进
    # 用户的前情提要，再被抽成"事实/经验"长期保存（consolidate 只认 kind，
    # 认不出一段过程是谁产生的）。
    #
    # 放在字段末尾是刻意的：ToolContext 是 frozen dataclass，既有调用点用的是
    # 关键字参数，但把新字段追加在最末尾能保证任何位置参数构造都不会被改变含义。
    role: Optional[str] = None

    def as_dict(self) -> dict:
        """只导出非空的标识字段。

        `current()` 的既有语义是"没有请求上下文时返回空 dict"，
        所以这里过滤掉 None，别把一堆 None 塞给调用方。
        """
        payload = {}
        for key in ("user_id", "session_key", "request_id", "trace_id", "role"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.turn_index:
            payload["turn_index"] = self.turn_index
        return payload


_EMPTY = ToolContext()
_CONTEXT: ContextVar[ToolContext] = ContextVar("agent_tool_context", default=_EMPTY)


def begin(user_id=None, session_key=None, scopes=None, request_id=None,
          trace_id=None, turn_index=0, approvals=None, budget=None, role=None):
    """建立本次请求的工具上下文，返回 token（供 reset 用）。

    `role` 由**服务端**注入（与 user_id/scopes 同级）：它决定这段过程记进账本时
    是否属于"用户的对话"。模型无权指定自己的角色。
    """
    context = ToolContext(
        user_id=user_id,
        session_key=session_key,
        request_id=request_id,
        trace_id=trace_id,
        turn_index=turn_index,
        scopes=frozenset(scopes or ()),
        approvals=frozenset(approvals or ()),
        budget=budget or ToolBudget(),
        role=str(role).strip() or None if role else None,
    )
    return _CONTEXT.set(context)


def reset(token) -> None:
    try:
        _CONTEXT.reset(token)
    except (ValueError, LookupError):
        # 在错误的上下文里 reset（例如跨线程）时忽略：不能让上下文清理影响对话
        pass


def context() -> ToolContext:
    return _CONTEXT.get() or _EMPTY


def set_scope(**kwargs):
    """兼容入口（P0 的 API）：只设置身份字段，其余用默认值。"""
    return begin(**kwargs)


def current() -> dict:
    return context().as_dict()


def user_id():
    return context().user_id


def session_key():
    return context().session_key


def scopes() -> frozenset:
    return context().scopes


def role() -> Optional[str]:
    """当前这一轮的身份角色（None = 主 agent）。"""
    return context().role
