# -*- coding: utf-8 -*-
"""预警消息读取客户端（agent 互通，2026-10-16）：Python 工具 → Java 内部端点。

<h3>方向与边界（与 strategy_store / memory_store 同一条纪律）</h3>
预警消息的唯一真相在 Java/MySQL，Python 侧无数据库凭据，只能经
{@code GET /api/v1/internal/messages/alerts} 只读取数。接口形状本身限制能力：
只有"读最近的预警"一个动作，没有标记已读、没有删除——**agent 不能替用户
把预警读掉**，读回执是用户在消息中心的独占行为。

<h3>身份不接受模型传入</h3>
user_id 一律由调用方（tool_scope）注入：'看谁的预警'不能交给模型决定。

<h3>失败一律 fail-open</h3>
Java 未启动、令牌不对、网络抖动……都只能退化成"这次没有预警数据"，
返回空列表并打日志，绝不打断对话。
"""
from __future__ import annotations

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

_TIMEOUT_S = 8.0
_CACHE_TTL_S = 60


def _base_url() -> str:
    # 与 strategy_store 同一套配置名：两边名字对不上导致静默失效的事在这个项目发生过
    return (os.getenv("JAVA_BASE_URL") or os.getenv("SPRING_BASE_URL")
            or "http://localhost:8080").rstrip("/")


def _headers() -> dict:
    # .env 由 llm_service 在导入时加载，这里必须延迟读取（同 memory_store 的教训）
    token = os.getenv("INTERNAL_API_TOKEN", "").strip()
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Internal-Token"] = token
    return headers


def recent_alerts(user_id: str, limit: int = 10) -> list[dict]:
    """用户最近的预警（ALERT 价格指标类 + NEWS_ALERT 新闻类，新→旧）。

    失败/无数据都返回空列表（fail-open）；短缓存压 Java 查询量
    （预警是分钟级产物，60s 足够新鲜——比新闻知识端的 5 分钟短，
    因为"有没有新预警"是用户即时关心的事）。
    """
    key = f"alerts:{user_id}:{limit}"
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _CACHE_TTL_S:
        return cached[1]
    try:
        resp = requests.get(
            f"{_base_url()}/api/v1/internal/messages/alerts",
            params={"userId": user_id, "limit": limit},
            headers=_headers(), timeout=_TIMEOUT_S)
        if resp.status_code != 200:
            logger.debug("recent alerts failed: %s %s", resp.status_code, resp.text[:200])
            return cached[1] if cached else []
        data = resp.json() or {}
        items = data.get("alerts")
        items = items if isinstance(items, list) else []
        _CACHE[key] = (now, items)
        return items
    except Exception as e:  # noqa: BLE001 —— 预警取不到数据不能打断对话
        logger.debug("recent alerts error: %s", e)
        return cached[1] if cached else []


_CACHE: dict[str, tuple[float, list[dict]]] = {}
