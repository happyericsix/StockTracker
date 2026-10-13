# -*- coding: utf-8 -*-
"""新闻知识客户端（agent 互通 P1）：Python 工具 → Java 内部知识端点。

<h3>方向与边界（与 agent/memory_store 同一条纪律）</h3>
新闻库（news_events / news_topics）的唯一真相在 Java/MySQL，Python 侧
没有任何数据库凭据，只能经 {@code /api/v1/internal/news/*} 只读取数。
"agent 不能修改数据层"由接口形状保证 —— 这里全部是 GET。

<h3>失败一律 fail-open</h3>
知识端点是增强能力：Java 未启动、令牌不匹配、网络抖动……都只能退化成
"这次没有新闻知识"，绝不能让聊天/决策链路报错。所有函数不抛异常。

<h3>为什么单独一个模块而不塞进 memory_store</h3>
超时/缓存/错误文案都不同（新闻查询是 3~8s 的库查询，记忆是亚秒级；
新闻要 5 分钟缓存压 Java 查询量），混在一起会让两边互相迁就。
"""
from __future__ import annotations

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 300          # 5 分钟：新闻是分钟级更新，压住 agent 连续追问的查询量
_cache: dict[str, tuple[float, object]] = {}


def _base_url() -> str:
    # 命名与 memory_store 同源（JAVA_BASE_URL / SPRING_BASE_URL 双认，
    # 理由见那边注释：配置名对不上导致的静默失效已经发生过）
    return (os.getenv("JAVA_BASE_URL") or os.getenv("SPRING_BASE_URL")
            or "http://localhost:8080").rstrip("/")


def _headers() -> dict:
    # .env 由 llm_service 导入时加载，令牌必须延迟读取
    token = os.getenv("INTERNAL_API_TOKEN", "").strip()
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Internal-Token"] = token
    return headers


def _timeout() -> float:
    try:
        return float(os.getenv("NEWS_KNOWLEDGE_HTTP_TIMEOUT", "8"))
    except ValueError:
        return 8.0


def _get(path: str):
    """GET + 5 分钟缓存 + fail-open（失败返回 None，绝不抛）。"""
    cache_key = path
    now = time.time()
    hit = _cache.get(cache_key)
    if hit and now - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]
    try:
        response = requests.get(f"{_base_url()}/api/v1/internal/news{path}",
                                headers=_headers(), timeout=_timeout())
        if response.status_code == 404:
            _cache[cache_key] = (now, None)
            return None
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - 知识端点是增强能力，失败只降级
        logger.debug("新闻知识端点不可用 %s: %s", path, exc)
        return None
    _cache[cache_key] = (now, payload)
    return payload


def event_facts(event_id) -> dict | None:
    """单条事件完整事实（含分析/可信度/正文）；不存在或不可达返回 None。"""
    try:
        raw_id = int(event_id)
    except (TypeError, ValueError):
        return None
    payload = _get(f"/events/{raw_id}")
    return payload if isinstance(payload, dict) else None


def briefing(symbol: str, days: int = 7, max_events: int = 12) -> dict | None:
    """个股信息面简报（事件 + 专题）；不可达返回 None。"""
    payload = _get(f"/briefing?symbol={symbol}&days={days}&maxEvents={max_events}")
    return payload if isinstance(payload, dict) else None


def topics(symbol: str, days: int = 7) -> list | None:
    """某标的已撰稿专题列表；不可达返回 None（与空列表区分：None=不知道，[]=没有）。"""
    payload = _get(f"/topics?symbol={symbol}&days={days}")
    if isinstance(payload, list):
        return payload
    return None
