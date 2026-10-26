"""fanout 并行子调研（orchestrator-worker，2026-10-20）。

<h3>它买的是什么</h3>
设计参照 Anthropic 多 agent 研究系统公开的三条经验：可拆解的问题交给多个带
**干净上下文**的 worker 并行探索（用 token 换覆盖面与速度）、拆解方式由主 agent
自己决定（fanout_research 是它手里的一个普通工具，不是外置路由）、汇总由确定性
代码收口（worker 摘要原样呈现 + 一次预算内的综合调用，不做二次演绎）。

与委员会（trading_committee）的分工——两者是互补不是替代：
- 委员会是**串行辩论**，后一棒看得到前一棒的全部输出去修正结论，买的是"结论可靠性"；
- fanout 是**并行探索**，worker 之间互相看不见，买的是"信息覆盖面"。
收盘后单标的决策走委员会；"多标的横向比较 / 跨板块梳理"这类宽问题走 fanout。

<h3>纪律（全部继承现有机制，不新发明）</h3>
- **身份**：user_id / scopes / epoch 从 tool_scope 上下文取（服务端注入），
  模型只能提供 question 与 subtasks；
- **预算**：fanout 自身是 expensive 工具（每轮贵重配额 3 次自动生效）；
  worker 各持独立 ToolBudget（互不透支）；单次调用有总 token 上限，
  且与委员会共用 LLM_DAILY_TOKEN_BUDGET 日预算线；
- **取消**：epoch 双层检查——主循环在步骤边界退出（既有机制），worker 在
  自己的步骤边界也会发现这轮已被更新的消息超过；
- **防递归**：worker 的工具集硬剥 fanout_research——子调研不能再派子调研，
  否则一次失控拆解就是指数级 token；
- **可审计**：每个 worker 的摘要、token、工具调用清单全部进返回值，
  进上下文前照常过字符预算与（外部数据时的）数据块包裹。

<h3>线程模型</h3>
LLM 层是同步 requests，并行只能靠线程。fanout 自身**不进**全局 EXECUTOR
（一跑就是分钟级，占住 4 个工位里的 1 个会把 worker 的工具调用挤成 tool_busy），
走专用 FANOUT_POOL；worker 跑在自有线程池，它们的工具调用照常进全局 EXECUTOR
（≤4 并发 < 容量 8，不会打满）。
"""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import llm_service
from agent import metering
from agent import recall
from agent import tool_contract as tc
from agent import tool_scope
from agent.tool_pipeline import BoundedToolPool
from agent.tool_scope import ToolBudget

logger = logging.getLogger(__name__)

FANOUT_TOOL_NAME = "fanout_research"
WORKER_ROLE = "fanout_worker"

# fanout 工具自身的执行池：与全局 EXECUTOR 隔离。
# max_workers=2 → 进程内最多 2 个 fanout 同时在跑（第三个 tool_busy 拒绝，
# 这是刻意的背压：每个 fanout 身后还压着最多 5 个 worker 的 LLM 调用）。
FANOUT_POOL = BoundedToolPool(max_workers=2, capacity=3)

# worker 线程池：懒创建（只有真的 fanout 时才开线程）。
_WORKER_EXECUTOR: ThreadPoolExecutor | None = None

# 汇总简报的输入输出预算：每个 worker 摘要截到这个长度再进综合 prompt。
WORKER_SUMMARY_CHARS = 1500
# worker 单步工具结果的上下文预算（比主 agent 默认 4000 收紧：
# worker 只需要事实片段，宽进只会让它把步数烧在复读上）。
WORKER_RESULT_CHARS = 3000


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(str(raw).strip()) if raw is not None and str(raw).strip() else default
    except (TypeError, ValueError):
        return default


def enabled() -> bool:
    """总开关（env 惰性读：测试 monkeypatch setenv 后立即生效）。"""
    return _env_flag("FANOUT_ENABLED", True)


def max_subtasks() -> int:
    return max(2, _env_int("FANOUT_MAX_SUBTASKS", 5))


def worker_max_steps() -> int:
    return max(1, _env_int("FANOUT_WORKER_MAX_STEPS", 6))


def total_token_budget() -> int:
    """单次 fanout 调用的总 token 上限；0 = 不限（还有步数与工具配额兜底）。"""
    return max(0, _env_int("FANOUT_TOTAL_TOKEN_BUDGET", 30_000))


def worker_timeout_s() -> float:
    return float(max(30, _env_int("FANOUT_WORKER_TIMEOUT_S", 150)))


WORKER_SYSTEM_PROMPT = """你是并行调研团队里的一名子调研员。你只负责自己被分配到的子问题，
看不到其他子任务，也不需要关心总问题之外的事。

规则：
- 先用工具查证再回答；查不到就如实写"未查到"，绝不凭训练记忆编数字。
- 工具返回 ok=false 时看 error.code：source_unavailable / budget_exceeded 不要重试，
  直接把"这块没查到"写进结论。
- 结束时输出一份 300 字以内的分点摘要：查到的事实（带数字与日期、注明来自什么工具）
  → 一句话结论。不要写买卖建议，不要预测价格。
- 你的输出只交给汇总程序，不会直接给用户看，不需要客套与过渡句。"""

_SYNTHESIS_SYSTEM_PROMPT = """你是汇总编辑。下面是多名子调研员对同一个总问题下不同子任务的调研摘要。
写一份统一简报：按子任务归并事实，指出相互印证与冲突之处，最后一段给"共同指向"。

纪律：只使用摘要里出现过的事实与数字，不得补充任何外部信息；
失败或被截断的子任务要如实标注"未完成"；不给买卖建议，不预测价格；
简报控制在 600 字以内，先结论后细节。"""


# ==================== 子任务声明校验（确定性，先于任何花钱的动作） ====================


def normalize_subtasks(subtasks) -> tuple[list[dict], str]:
    """把模型给的 subtasks 收敛成规范形态。返回 (清单, 错误消息)；错误消息非空即拒绝。

    刻意**钳制而不是默默丢弃**：数量超上限时截断并让模型在返回值里看见截断，
    比"悄悄丢掉两个子任务"（模型以为全查了）诚实。
    """
    if not isinstance(subtasks, list) or not subtasks:
        return [], "subtasks 必须是非空数组，每个元素含 objective"
    if len(subtasks) < 2:
        return [], "至少拆成 2 个子任务——只有 1 个子任务不值得并行，直接查工具更省"

    normalized: list[dict] = []
    seen_objectives: set[str] = set()
    for item in subtasks:
        if not isinstance(item, dict):
            continue
        objective = str(item.get("objective") or "").strip()
        if not objective:
            return [], "每个子任务必须有非空 objective"
        if len(objective) > 300:
            objective = objective[:300]
        focus_symbol = str(item.get("focus_symbol") or "").strip() or None
        # 完全同文的子任务是模型偷懒（拆了个寂寞）：去重而不是重复查两遍
        if objective in seen_objectives:
            continue
        seen_objectives.add(objective)
        normalized.append({"objective": objective, "focus_symbol": focus_symbol})

    limit = max_subtasks()
    if len(normalized) > limit:
        normalized = normalized[:limit]
    if len(normalized) < 2:
        return [], "去重后不足 2 个有效子任务，请把子任务写成各自独立的问题"
    return normalized, ""


def _worker_directive(question: str, subtask: dict) -> str:
    """子任务 → worker 的 user 消息（总问题背景 + 分工 + 输出要求）。"""
    focus = subtask.get("focus_symbol")
    lines = [f"总问题（背景，仅供理解，不要展开回答它）：{question}", ""]
    lines.append(f"你负责的子问题：{subtask['objective']}")
    if focus:
        lines.append(f"聚焦标的：{focus}（查行情/新闻时优先围绕它）")
    return "\n".join(lines)


# ==================== worker：带干净上下文的 mini ReAct 循环 ====================


def _superseded(user_id, epoch) -> bool:
    """轮次是否已被更新的消息超过（延迟 import 避免 tool_registry → fanout → react_agent 环）。"""
    if epoch is None:
        return False
    from agent import react_agent as _ra

    try:
        return bool(_ra._epoch_superseded(user_id, epoch))
    except Exception:  # noqa: BLE001 —— 取消判断失败时按"未取消"继续，别让旁路炸主流程
        return False


def _worker_tools() -> list:
    """worker 的工具集：market 模式（只读：行情+资讯+基本面）再硬剥 fanout 自身。

    在 worker 自己的 tool_scope 上下文里调用（_run_worker 已 begin），enroll 按它的
    scopes 做权限可见性。硬剥是深度防御——正常路径 worker 根本看不到
    fanout_research 的 schema，但 enroll 的实现将来变了（比如模式改名单），
    这道仍要守住"子调研不派子调研"。
    """
    from agent import tool_sets

    enrolled = tool_sets.enroll(tool_scope.context(), "market")
    return [name for name in enrolled if name != FANOUT_TOOL_NAME]


def _run_worker(index: int, question: str, subtask: dict, *, user_id=None, session_key=None,
                scopes=None, epoch=None, completion=None) -> dict:
    """单个子调研员：干净上下文（无历史、无记忆召回），自己的小循环，永不抛异常。"""
    from agent import tool_registry

    started = time.monotonic()
    outcome = {
        "index": index,
        "objective": subtask.get("objective") or "",
        "focus_symbol": subtask.get("focus_symbol"),
        "ok": False,
        "summary": "",
        "steps": 0,
        "tokens": 0,
        "tool_calls": [],
        "error": "",
    }
    budget = ToolBudget(max_calls_per_turn=8, max_expensive_per_turn=2)
    token = tool_scope.begin(
        user_id=user_id,
        session_key=session_key,
        scopes=scopes,
        request_id=str(user_id) if user_id is not None else None,
        trace_id=session_key,
        role=WORKER_ROLE,
        budget=budget,
        epoch=epoch,
    )
    try:
        tools = tool_registry.schemas(enabled=_worker_tools())
        # 干净上下文：不带 history、不做记忆召回，只有时间锚点——
        # worker 的价值就在于不被主对话的上下文带偏（Anthropic 经验：干净窗口
        # 是并行探索收益的主要来源之一）。
        try:
            anchor = recall.render({})
        except Exception:  # noqa: BLE001
            anchor = ""
        messages = [
            {"role": "system", "content": WORKER_SYSTEM_PROMPT + ("\n\n" + anchor if anchor else "")},
            {"role": "user", "content": _worker_directive(question, subtask)},
        ]

        for _ in range(worker_max_steps()):
            outcome["steps"] += 1
            if _superseded(user_id, epoch):
                outcome["error"] = "superseded"
                return outcome
            if time.monotonic() - started > worker_timeout_s():
                outcome["error"] = "worker_timeout"
                return outcome
            try:
                choice = (completion or llm_service.chat_completion)(messages, tools=tools, max_tokens=900)
            except Exception as exc:  # noqa: BLE001 —— worker 失败是"这一路没查到"，不是整个 fanout 失败
                outcome["error"] = f"llm_error: {exc}"
                return outcome
            if not isinstance(choice, dict):
                outcome["error"] = "llm_error: empty choice"
                return outcome
            msg = choice.get("message") or {}
            if not isinstance(msg, dict):
                msg = {}
            messages.append(msg)
            usage = metering.parse_usage(choice)
            outcome["tokens"] += int(usage.get("total_tokens") or 0)

            tool_calls = msg.get("tool_calls") or []
            if not isinstance(tool_calls, list) or not tool_calls:
                text = msg.get("content")
                if isinstance(text, str) and text.strip():
                    outcome["summary"] = text.strip()
                    outcome["ok"] = True
                else:
                    outcome["error"] = outcome["error"] or "empty_final_answer"
                return outcome

            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") or {}
                if not isinstance(fn, dict):
                    fn = {}
                name = fn.get("name") or ""
                args = _parse_tool_args(fn.get("arguments"))
                envelope = _execute_worker_tool(name, args)
                outcome["tool_calls"].append({"tool": name, "ok": tc.is_ok(envelope)})
                content = _worker_tool_content(name, envelope)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": content,
                })

        outcome["error"] = outcome["error"] or "max_steps"
        return outcome
    finally:
        tool_scope.reset(token)


def _parse_tool_args(raw_args):
    if raw_args is None:
        return {}
    if isinstance(raw_args, dict):
        return raw_args
    if not isinstance(raw_args, str):
        return {}
    try:
        parsed = json.loads(raw_args)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _execute_worker_tool(name, args):
    """worker 的工具执行：与主循环同一管线（策略/预算/超时全生效），只是不走 fanout 专用池。"""
    from agent import tool_pipeline, tool_registry

    spec = tool_registry.get_spec(name)
    return tool_pipeline.call_tool_bounded(
        name, args,
        spec=spec,
        handler=tool_registry.execute_tool,
        default_timeout=15.0,
    )


def _worker_tool_content(name, envelope):
    from agent import tool_registry

    spec = tool_registry.get_spec(name)
    budget = getattr(spec, "result_budget_chars", 0) or WORKER_RESULT_CHARS
    return tc.to_tool_content(name, envelope, max_chars=min(int(budget), WORKER_RESULT_CHARS), spec=spec)


# ==================== 调度与聚合 ====================


def _worker_executor() -> ThreadPoolExecutor:
    global _WORKER_EXECUTOR
    if _WORKER_EXECUTOR is None:
        _WORKER_EXECUTOR = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="fanout-worker")
    return _WORKER_EXECUTOR


def _synthesize(question: str, workers: list[dict], completion=None) -> dict:
    """一次预算内的综合调用：把 worker 摘要归并成统一简报。失败不掩盖（如实标注）。"""
    ok_workers = [w for w in workers if w.get("ok") and w.get("summary")]
    if not ok_workers:
        return {"text": "", "note": "没有可用的子调研结果，跳过综合"}
    blocks = []
    for w in ok_workers:
        summary = str(w.get("summary") or "")[:WORKER_SUMMARY_CHARS]
        blocks.append(f"[子任务 {w['index']}] {w.get('objective')}\n{summary}")
    prompt = (
        f"总问题：{question}\n\n" + "\n\n".join(blocks)
        + "\n\n请输出统一简报。"
    )
    try:
        choice = (completion or llm_service.chat_completion)(
            [{"role": "system", "content": _SYNTHESIS_SYSTEM_PROMPT},
             {"role": "user", "content": prompt}],
            max_tokens=1600,
        )
        msg = (choice or {}).get("message") or {}
        text = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(text, str) and text.strip():
            return {"text": text.strip()}
        return {"text": "", "note": "综合调用未返回文本"}
    except Exception as exc:  # noqa: BLE001 —— 综合失败时 worker 摘要仍然全部在场
        logger.warning("fanout 综合失败: %s", exc)
        return {"text": "", "note": f"综合调用失败：{exc}"}


def run(question, subtasks, *, user_id=None, session_key=None, scopes=None,
        epoch=None, completion=None, synthesis_completion=None) -> dict:
    """执行一次并行子调研。**永远返回信封，永不抛异常**。

    调用方是 tool_registry 的 `_tool_fanout`：身份从 tool_scope 上下文取，
    这里的同名参数只是传递通道，不信任模型传入的任何身份字段。
    """
    started_at = time.monotonic()

    # ---- 闸门 1：总开关 ----
    if not enabled():
        return tc.fail(tc.POLICY_DENIED, "并行调研未启用（FANOUT_ENABLED=0）")

    question = str(question or "").strip()
    if not question:
        return tc.fail(tc.INVALID_ARGS, "question 不能为空")

    # ---- 闸门 2：子任务声明校验（先于任何花钱动作） ----
    tasks, err = normalize_subtasks(subtasks)
    if err:
        return tc.fail(tc.INVALID_ARGS, err)

    # ---- 闸门 3：日预算线（与委员会共用同一条线、同一口径） ----
    daily_budget = _env_int("LLM_DAILY_TOKEN_BUDGET", 0)
    if daily_budget and metering.daily_llm_tokens() >= daily_budget:
        return tc.fail(tc.BUDGET_EXCEEDED,
                       f"今日 LLM token 预算已用完（{metering.daily_llm_tokens()}/{daily_budget}）",
                       budget={"daily_llm_tokens": metering.daily_llm_tokens(),
                               "daily_budget": daily_budget})

    baseline = metering.baseline()
    executor = _worker_executor()
    futures = {
        executor.submit(_run_worker, i, question, task,
                        user_id=user_id, session_key=session_key,
                        scopes=scopes, epoch=epoch, completion=completion): (i, task)
        for i, task in enumerate(tasks)
    }

    # ---- 调度：完成一个检查一次总预算；超线即取消还没启动的 worker ----
    # 预算口径用 worker 自报的 usage 累计（choice.usage 逐次相加，含重试在内
    # 的每一次模型调用都算）：它与 metering 的进程级账本在真实路径上一致，
    # 但只属于这次 fanout——不会被并发的其它轮次污染。
    cap = total_token_budget()
    workers: list[dict] = [None] * len(tasks)  # type: ignore[list-item]
    tokens_used = 0
    budget_stopped = False
    pending = set(futures)
    overall_deadline = time.monotonic() + worker_timeout_s() + 60.0
    while pending:
        done, pending = wait(pending, timeout=10.0, return_when=FIRST_COMPLETED)
        for fut in done:
            i, _task = futures[fut]
            try:
                workers[i] = fut.result()
            except Exception as exc:  # noqa: BLE001 —— 单个 worker 崩溃不炸整个 fanout
                workers[i] = {"index": i, "objective": _task.get("objective") or "",
                              "ok": False, "summary": "", "error": f"crashed: {exc}",
                              "steps": 0, "tokens": 0, "tool_calls": []}
            tokens_used += int((workers[i] or {}).get("tokens") or 0)
        if cap and tokens_used >= cap:
            # 超线：取消还没启动的、给未完成的记 skipped。
            # 注意这里**不依赖 pending 非空**——worker 都在同一个 wait 批次里完成时
            # （快速失败场景），也要把"预算已爆"如实标出来。
            budget_stopped = True
            for fut in pending:
                fut.cancel()
            for fut in pending:
                i, task = futures[fut]
                if workers[i] is None:
                    workers[i] = {"index": i, "objective": task.get("objective") or "",
                                  "ok": False, "summary": "", "error": "skipped_budget",
                                  "steps": 0, "tokens": 0, "tool_calls": []}
            pending = set()
        if pending and time.monotonic() > overall_deadline:
            for fut in pending:
                fut.cancel()
            for fut in pending:
                i, task = futures[fut]
                if workers[i] is None:
                    workers[i] = {"index": i, "objective": task.get("objective") or "",
                                  "ok": False, "summary": "", "error": "wall_timeout",
                                  "steps": 0, "tokens": 0, "tool_calls": []}
            pending = set()

    ok_count = sum(1 for w in workers if w and w.get("ok"))
    used = metering.since(baseline)

    # 预算熔断且一个结果都没有：明确失败（区别于"部分完成"）
    if ok_count == 0 and budget_stopped:
        return tc.fail(tc.BUDGET_EXCEEDED,
                       f"并行子调研的 token 预算（{cap}）在得到任何结果前耗尽",
                       budget={"fanout_tokens": tokens_used, "fanout_token_budget": cap})
    # epoch 取消：让主循环在它的步骤边界统一处理，这里如实汇报
    if ok_count == 0 and all(w and w.get("error") == "superseded" for w in workers):
        return tc.fail(tc.POLICY_DENIED, "本轮已被更新的消息取代，子调研已中止",
                       superseded=True)
    if ok_count == 0:
        reasons = "; ".join(str(w.get("error") or "?") for w in workers if w)
        return tc.fail(tc.TOOL_ERROR, f"所有子调研都失败了：{reasons}", retryable=True)

    synthesis = _synthesize(question, workers, completion=synthesis_completion)
    payload = {
        "question": question,
        "workers": workers,
        "synthesis": synthesis,
        "budget_stopped": budget_stopped,
        "usage": {"llm_calls": used.get("llm.calls", 0),
                  "total_tokens": used.get("llm.total_tokens", 0),
                  "wall_ms": int((time.monotonic() - started_at) * 1000)},
    }
    from agent import offload

    payload, archive = offload.archive_if_large(FANOUT_TOOL_NAME, payload)
    if archive:
        payload = dict(payload) | {"archived": archive}
    return tc.ok(payload,
                 workers=len(workers), ok_workers=ok_count,
                 total_tokens=tokens_used,
                 wall_ms=payload["usage"]["wall_ms"])
