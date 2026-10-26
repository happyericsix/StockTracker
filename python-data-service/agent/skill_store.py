# -*- coding: utf-8 -*-
"""技能注入客户端（④步，2026-10-16）：Python 委员会/盯盘链路 → Java 内部端点。

与 memory_store / message_store 同一条纪律：只读（GET）、身份由调用方注入、
失败 fail-open（"这次没有技能"绝不打断会议）。60s 短缓存压查询量。
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


def injectable(user_id: str, scene: str, symbol: str = "", strategy_id: int | None = None,
               limit: int = 3) -> list[dict]:
    """某场景可注入的技能（软参考）。失败/无技能返回空列表（fail-open）。"""
    if not user_id or not scene:
        return []
    key = f"skills:{user_id}:{scene}:{symbol}:{strategy_id}"
    now = time.time()
    cached = _cache.get(key)
    if cached and now - cached[0] < _CACHE_TTL_S:
        return cached[1][:max(1, int(limit))]
    try:
        params = {"userId": user_id, "scene": scene}
        if symbol:
            params["symbol"] = symbol
        if strategy_id is not None:
            params["strategyId"] = strategy_id
        resp = requests.get(f"{_base_url()}/api/v1/internal/skills/inject",
                            params=params, headers=_headers(), timeout=_TIMEOUT_S)
        if resp.status_code != 200:
            logger.debug("skills inject failed: %s %s", resp.status_code, resp.text[:200])
            return cached[1] if cached else []
        items = resp.json().get("skills") or []
        items = items if isinstance(items, list) else []
        _cache[key] = (now, items)
        return items[:max(1, int(limit))]
    except Exception as e:  # noqa: BLE001 —— 技能缺席不挡会议
        logger.debug("skills inject error: %s", e)
        return cached[1] if cached else []
