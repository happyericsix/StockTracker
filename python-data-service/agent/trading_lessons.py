# -*- coding: utf-8 -*-
"""交易教训检索客户端（2026-10-18，P2 知识库回流）：Python 委员会/盯盘 → Java 教训库。

闭环缺口修的是这一段：周反思（/api/v1/agent/weekly-reflection）产出的结构化教训
一直落得到 Java MemoryLessonService 里，但**从来流不回决策现场**——委员会每次
开会仍是"从零读盘"，对账发现的错误模式只能等下一次周反思再被重新发现一遍。
这个模块把教训按 (用户, 标的) 检索出来，交给 trading_committee.decide /
intraday_watch.intraday_decide 注入证据包（软参考语义，见 lessons_block）。

与 skill_store 同一条纪律（三个对称：形状照抄、语义照抄、坑也照抄着避开）：
- **只读**：教训的写入只有 Java 周反思一条路（agent 侧没有任何教训写接口，防投毒）；
- **fail-open**：检索失败返回空列表，"这次没有教训"绝不打断会议/盯盘；
- **短缓存**：60s TTL 压查询量（教训按周更新，不差这一分钟）。
"""
from __future__ import annotations

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

_TIMEOUT_S = 6.0
_CACHE_TTL_S = 60
_cache: dict[str, tuple[float, list[dict]]] = {}


def _base_url() -> str:
    return (os.getenv("JAVA_BASE_URL") or os.getenv("SPRING_BASE_URL")
            or "http://localhost:8080").rstrip("/")


def _headers() -> dict:
    token = os.getenv("INTERNAL_API_TOKEN", "").strip()
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Internal-Token"] = token
    return headers


def injectable(user_id: str, scene: str, symbol: str = "",
               strategy_id: int | None = None, limit: int = 3) -> list[dict]:
    """某场景可注入的复盘教训。失败/无教训返回空列表（fail-open）。

    scene 目前只是缓存键的一部分（Java 端点按 用户+symbol 检索，不区分场景），
    留着它是为了缓存隔离：committee 与 watch 各自的 TTL 窗口互不挤占。
    """
    if not user_id:
        return []
    key = f"lessons:{user_id}:{scene}:{symbol}:{strategy_id}"
    now = time.time()
    cached = _cache.get(key)
    if cached and now - cached[0] < _CACHE_TTL_S:
        return cached[1][:max(1, int(limit))]
    try:
        params = {"userId": user_id, "limit": max(1, int(limit))}
        if symbol:
            params["symbol"] = symbol
        resp = requests.get(f"{_base_url()}/api/v1/internal/memory/lessons",
                            params=params, headers=_headers(), timeout=_TIMEOUT_S)
        if resp.status_code != 200:
            logger.debug("lessons inject failed: %s %s", resp.status_code, resp.text[:200])
            return cached[1] if cached else []
        items = resp.json().get("lessons") or []
        items = items if isinstance(items, list) else []
        _cache[key] = (now, items)
        return items[:max(1, int(limit))]
    except Exception as e:  # noqa: BLE001 —— 教训缺席不挡会议/盯盘
        logger.debug("lessons inject error: %s", e)
        return cached[1] if cached else []
