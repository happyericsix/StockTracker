# agent/react_agent.py
from __future__ import annotations
import json
import logging
import queue
import threading

import llm_service
from agent.tool_registry import TOOL_SCHEMAS, execute_tool
from agent.strategy_schema import extract_strategy_json, validate_strategy_config

logger = logging.getLogger(__name__)
MAX_STEPS = 6
TOOL_TIMEOUT = 15

SYSTEM_PROMPT = """你是股票策略助手。用户可能让你生成交易策略。
可用工具用于查行情、校验和回测策略。若用户在描述策略，请逐步调用工具，
最终调用 finalize_strategy，并在最终回复中用 ```json 代码块给出策略 JSON。
JSON 必须符合 schema_version=1.0。若用户只是闲聊或查行情，直接回复。"""

CORRECTION_PROMPT = "请重新输出，必须用 ```json 代码块给出 schema_version=1.0 的策略 JSON"
DEGRADE_REPLY = "没理解，请换个说法描述你的策略"


class _ToolResult:
    def __init__(self):
        self._event = threading.Event()
        self._value = None
        self._exc = None

    def set_result(self, value):
        self._value = value
        self._event.set()

    def set_exception(self, exc):
        self._exc = exc
        self._event.set()

    def result(self, timeout=None):
        if not self._event.wait(timeout):
            raise TimeoutError("tool execution timed out")
        if self._exc is not None:
            raise self._exc
        return self._value


class _DaemonWorkerPool:
    def __init__(self, max_workers=2, thread_name_prefix="react-agent-tool"):
        self._queue = queue.Queue()
        self._threads = []
        for index in range(max_workers):
            thread = threading.Thread(
                target=self._worker,
                name=f"{thread_name_prefix}-{index}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def _worker(self):
        while True:
            item = self._queue.get()
            if item is None:
                break
            result, fn, name, args = item
            try:
                value = fn(name, args)
            except Exception as exc:
                result.set_exception(exc)
            else:
                result.set_result(value)

    def submit(self, fn, name, args):
        result = _ToolResult()
        self._queue.put((result, fn, name, args))
        return result


_TOOL_EXECUTOR = _DaemonWorkerPool(max_workers=2)


def _looks_like_strategy_attempt(text):
    lowered = (text or "").lower()
    return "```json" in lowered or "schema_version" in lowered


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


def _execute_tool_bounded(name, args, timeout=None):
    if timeout is None:
        timeout = TOOL_TIMEOUT
    result = _TOOL_EXECUTOR.submit(execute_tool, name, args)
    try:
        return result.result(timeout=timeout)
    except TimeoutError:
        return {"error": f"tool {name} timed out after {timeout}s"}
    except Exception as exc:
        return {"error": f"tool {name} failed: {exc}"}


def run_agent(user_id, message, history=None):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": message})
    retry_used = False

    for _ in range(MAX_STEPS):
        try:
            choice = llm_service.chat_completion(messages, tools=TOOL_SCHEMAS)
        except Exception as e:
            logger.error("agent llm error: %s", e)
            return {"replies": ["AI 服务暂不可用，请稍后再试"], "strategy_json": None}

        if not isinstance(choice, dict):
            return {"replies": ["AI 服务暂不可用，请稍后再试"], "strategy_json": None}

        msg = choice.get("message") or {}
        if not isinstance(msg, dict):
            msg = {}
        messages.append(msg)

        tool_calls = msg.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            tool_calls = []

        if tool_calls:
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                if not isinstance(fn, dict):
                    fn = {}
                name = fn.get("name") or ""
                args = _parse_tool_args(fn.get("arguments"))
                result = _execute_tool_bounded(name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": json.dumps(result, ensure_ascii=False),
                })
            continue

        text = msg.get("content") or "我没理解，请换个说法。"
        if not isinstance(text, str):
            text = str(text)

        strategy = extract_strategy_json(text)
        if isinstance(strategy, dict):
            cfg, err = validate_strategy_config(strategy)
            if err is None:
                return {
                    "replies": llm_service.split_replies(text),
                    "strategy_json": cfg.model_dump(),
                }

        if retry_used:
            return {"replies": [DEGRADE_REPLY], "strategy_json": None}

        if strategy is not None or _looks_like_strategy_attempt(text):
            retry_used = True
            messages.append({"role": "user", "content": CORRECTION_PROMPT})
            continue

        return {"replies": llm_service.split_replies(text), "strategy_json": None}

    return {"replies": ["这个请求步骤有点多，请简化你的策略描述再试一次。"], "strategy_json": None}
