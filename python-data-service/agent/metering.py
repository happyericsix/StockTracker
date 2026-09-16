"""计量（P1）：先能看见，再能限制。

<h3>为什么这是边界控制的第一步</h3>
"限多少"这个问题现在没法回答：配额是每轮 12 次，但**一轮是谁的一轮**、一天有多少轮、
外部源一天被调几次、LLM 花了多少 token —— 全都没有数。
没有数的限流只能拍脑袋，而拍脑袋的阈值一定会误伤正常用户（或者松到等于没有）。

所以这一层只做一件事：**把已经发生的事记下来**。它不改变任何行为、不做任何拒绝。

<h3>为什么不直接上 Prometheus / 不新建表</h3>
第一阶段的目标是"能回答问题"，不是"能画图"：

- 指标**落已有的账本**（`memory_events` 追加一条 `kind=usage`），复用现成的批量写入通道，
  于是"这个月用了多少"可以用 SQL 直接查，不需要新的基础设施；
- 进程内计数只服务 `/health` 与调试（重启即清零，这是可接受的：长期数据在账本里）；
- 真要画趋势时再上 Prometheus，那时候**指标名和维度已经被真实使用验证过了**。

<h3>与 external_source 的分工（避免两套数）</h3>
- `external_source` 记的是**健康状态**（连续失败几次、是否停用、每个数据集缓存了几条）——
  门禁要用它做判断，是"状态"；
- `metering` 记的是**用量**（调用数、失败数、延迟、结果大小、拒绝原因）—— 是"统计"。
两者职责不同，但**外部源的用量数字只有一个来源**：`snapshot()` 里从 `external_source` 派生，
不在这里重复计数（重复计数迟早会对不上，而对不上的那天没人知道该信哪个）。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class Meter:
    """进程内计量器（线程安全）。所有记录方法**永不抛异常**。"""

    def __init__(self):
        self._lock = threading.Lock()
        # 扁平计数器：{ "tools.calls": 3, "refusals.policy_denied": 1, ... }
        # 用扁平结构而不是嵌套 dict，是为了让"这一轮的增量"能用一次减法算出来。
        self._counters: dict = {}
        # 每个工具的明细（工具数量有界，不会无限增长）
        self._tools: dict = {}
        # 每个拒绝原因的出现次数（错误码有界）
        self._refusals: dict = {}
        self._llm: dict = {"calls": 0, "failures": 0, "prompt_tokens": 0,
                           "completion_tokens": 0, "total_tokens": 0, "latency_ms": 0}
        self._turns = 0

    # ---------- 内部 ----------

    def _bump(self, key: str, amount: int = 1) -> None:
        self._counters[key] = self._counters.get(key, 0) + amount

    def _tool(self, name: str) -> dict:
        return self._tools.setdefault(name, {
            "calls": 0, "ok": 0, "failed": 0, "reused": 0, "refused": 0,
            "latency_ms_total": 0, "latency_ms_max": 0,
            "result_chars_total": 0, "result_chars_max": 0,
        })

    # ---------- 记录 ----------

    def record_tool_call(self, name: str, *, ok: bool, code: str = "", latency_ms: int = 0,
                         result_chars: int = 0, cost_class: str = "cheap") -> None:
        """一次**真实执行**的工具调用（被拒绝的不算，那是 refusal）。"""
        try:
            with self._lock:
                detail = self._tool(name)
                detail["calls"] += 1
                detail["ok" if ok else "failed"] += 1
                detail["latency_ms_total"] += max(0, int(latency_ms))
                detail["latency_ms_max"] = max(detail["latency_ms_max"], int(latency_ms))
                detail["result_chars_total"] += max(0, int(result_chars))
                detail["result_chars_max"] = max(detail["result_chars_max"], int(result_chars))
                self._bump("tools.calls")
                self._bump("tools.ok" if ok else "tools.failed")
                if cost_class == "expensive":
                    self._bump("tools.expensive")
                if not ok and code:
                    self._bump(f"tools.errors.{code}")
        except Exception as exc:  # noqa: BLE001 —— 计量绝不能影响工具调用
            logger.debug("计量失败 record_tool_call: %s", exc)

    def record_tool_reuse(self, name: str) -> None:
        """同轮复用（没打到真实执行），单独记：它是"省下来的钱"，不是"花掉的钱"。"""
        try:
            with self._lock:
                self._tool(name)["reused"] += 1
                self._bump("tools.reused")
        except Exception as exc:  # noqa: BLE001
            logger.debug("计量失败 record_tool_reuse: %s", exc)

    def record_refusal(self, name: str, code: str) -> None:
        """被策略/门禁/预算拒绝的调用。

        **必须与真实调用分开计**：把拒绝混进 `tools.calls` 会让"这个工具被用了多少次"
        这个数字失去意义（模型试了 10 次被拒 10 次，看起来像高频使用）。
        """
        try:
            with self._lock:
                self._tool(name)["refused"] += 1
                self._refusals[code or "unknown"] = self._refusals.get(code or "unknown", 0) + 1
                self._bump("tools.refused")
                self._bump(f"refusals.{code or 'unknown'}")
        except Exception as exc:  # noqa: BLE001
            logger.debug("计量失败 record_refusal: %s", exc)

    def record_llm(self, *, model: str = "", prompt_tokens: int = 0, completion_tokens: int = 0,
                   total_tokens: int = 0, latency_ms: int = 0, ok: bool = True) -> None:
        try:
            with self._lock:
                self._llm["calls"] += 1
                if not ok:
                    self._llm["failures"] += 1
                self._llm["prompt_tokens"] += max(0, int(prompt_tokens))
                self._llm["completion_tokens"] += max(0, int(completion_tokens))
                self._llm["total_tokens"] += max(0, int(total_tokens))
                self._llm["latency_ms"] += max(0, int(latency_ms))
                self._bump("llm.calls")
                self._bump("llm.total_tokens", max(0, int(total_tokens)))
                if not ok:
                    self._bump("llm.failures")
        except Exception as exc:  # noqa: BLE001
            logger.debug("计量失败 record_llm: %s", exc)

    def record_turn(self) -> None:
        try:
            with self._lock:
                self._turns += 1
                self._bump("turns")
        except Exception as exc:  # noqa: BLE001
            logger.debug("计量失败 record_turn: %s", exc)

    # ---------- 每轮增量（给账本的 kind=usage 事件用） ----------

    def baseline(self) -> dict:
        """取当前计数器的快照（作为这一轮的起点）。"""
        with self._lock:
            return dict(self._counters)

    @staticmethod
    def delta(baseline: dict, current: dict) -> dict:
        """两个快照之差（只保留变化的键）。"""
        keys = set(baseline) | set(current)
        return {key: current.get(key, 0) - baseline.get(key, 0)
                for key in keys if current.get(key, 0) - baseline.get(key, 0)}

    def since(self, baseline: dict) -> dict:
        return self.delta(baseline or {}, self.baseline())

    # ---------- 观测 ----------

    def snapshot(self) -> dict:
        with self._lock:
            tools = {}
            for name, detail in sorted(self._tools.items()):
                calls = detail["calls"]
                tools[name] = dict(detail) | {
                    "latency_ms_avg": round(detail["latency_ms_total"] / calls) if calls else 0,
                    "result_chars_avg": round(detail["result_chars_total"] / calls) if calls else 0,
                }
            llm = dict(self._llm)
            llm["latency_ms_avg"] = round(llm["latency_ms"] / llm["calls"]) if llm["calls"] else 0
            totals = {
                "turns": self._turns,
                "tool_calls": self._counters.get("tools.calls", 0),
                "tool_failures": self._counters.get("tools.failed", 0),
                "tool_reused": self._counters.get("tools.reused", 0),
                "tool_refused": self._counters.get("tools.refused", 0),
                "expensive_calls": self._counters.get("tools.expensive", 0),
                "llm_calls": self._counters.get("llm.calls", 0),
                "llm_tokens": self._counters.get("llm.total_tokens", 0),
                "refusals": dict(sorted(self._refusals.items())),
            }
        return {
            "totals": totals,
            "llm": llm,
            "tools": tools,
            # 外部源的**用量**只有一个来源：从健康状态派生，不在这里重复计数
            "external": _external_rollup(),
        }

    def reset(self) -> None:
        """测试用：清空全部计数。生产不要调（没有意义且会掩盖历史）。"""
        with self._lock:
            self._counters.clear()
            self._tools.clear()
            self._refusals.clear()
            self._llm = {"calls": 0, "failures": 0, "prompt_tokens": 0,
                         "completion_tokens": 0, "total_tokens": 0, "latency_ms": 0}
            self._turns = 0


def _external_rollup() -> dict:
    try:
        from agent import external_source

        stats = external_source.REGISTRY.snapshot()
        return {"calls": stats.get("calls", 0), "cache_hits": stats.get("cache_hits", 0),
                "cache_entries": stats.get("cache_entries", 0),
                "offloads": stats.get("offloads", 0)}
    except Exception:  # noqa: BLE001 —— 外部层不可用不该让计量挂掉
        return {}


METER = Meter()


# ---------- 模块级便捷入口（调用方不必到处 import 单例） ----------

def record_tool_call(name, **kwargs) -> None:
    METER.record_tool_call(name, **kwargs)


def record_tool_reuse(name) -> None:
    METER.record_tool_reuse(name)


def record_refusal(name, code) -> None:
    METER.record_refusal(name, code)


def record_llm(**kwargs) -> None:
    METER.record_llm(**kwargs)


def record_turn() -> None:
    METER.record_turn()


def baseline() -> dict:
    return METER.baseline()


def since(baseline_snapshot) -> dict:
    return METER.since(baseline_snapshot)


def snapshot() -> dict:
    return METER.snapshot()


def describe_turn(delta: dict) -> str:
    """把一轮的用量渲染成一行给人看的中文（账本 `kind=usage` 事件的正文）。

    接受两种形状，因为它们**都**会被顺手传进来：
    - `since(baseline)` 的扁平增量（`tools.calls`）—— 本模块的主用法；
    - `snapshot()["totals"]` 的人类可读视图（`tool_calls`）—— 看指标时手边就有。
    让它在两种形状下都对，比要求调用方记住"必须传哪一种"更省事，也更不容易写出静默的错数。
    """
    flat = dict(delta or {})
    if "tools.calls" not in flat and "tool_calls" in flat:
        flat["tools.calls"] = flat.get("tool_calls", 0)
        flat["tools.failed"] = flat.get("tool_failures", 0)
        flat["tools.reused"] = flat.get("tool_reused", 0)
        flat["tools.refused"] = flat.get("tool_refused", 0)
        flat["llm.calls"] = flat.get("llm_calls", 0)
        flat["llm.total_tokens"] = flat.get("llm_tokens", 0)
        for code, count in (flat.get("refusals") or {}).items():
            flat[f"refusals.{code}"] = count

    llm_calls = flat.get("llm.calls", 0)
    tokens = flat.get("llm.total_tokens", 0)
    tool_calls = flat.get("tools.calls", 0)
    failures = flat.get("tools.failed", 0)
    reused = flat.get("tools.reused", 0)
    refused = flat.get("tools.refused", 0)
    parts = [f"LLM {llm_calls} 次/{tokens} tokens", f"工具 {tool_calls} 次"]
    if reused:
        parts.append(f"同轮复用 {reused} 次")
    if failures:
        parts.append(f"失败 {failures} 次")
    if refused:
        reasons = "、".join(code for code in sorted(flat) if code.startswith("refusals."))
        parts.append(f"被拒 {refused} 次（{reasons.replace('refusals.', '')}）")
    return "本轮用量：" + "；".join(parts)


def turn_started_at() -> float:
    return time.time()


def elapsed_ms(started_at: float) -> int:
    return int((time.time() - started_at) * 1000)


def parse_usage(payload: Optional[dict]) -> dict:
    """从 OpenAI 兼容响应里取 usage（缺失就是 0，不猜）。"""
    usage = (payload or {}).get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    def _int(value):
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0
    return {
        "prompt_tokens": _int(usage.get("prompt_tokens")),
        "completion_tokens": _int(usage.get("completion_tokens")),
        "total_tokens": _int(usage.get("total_tokens")),
    }
