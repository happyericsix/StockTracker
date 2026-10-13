"""策略模拟盘数据与 Java 内部接口之间的客户端（复盘闭环的取数层）。

<h3>方向与边界</h3>
与 {@code memory_store} 同一条纪律：数据在 Java（MySQL），Python 侧没有任何数据库
凭据，只能通过 {@code /api/v1/internal/strategies/*} 读，且接口形状本身限制了能力 ——
这里只有两个只读动作：列清单、取复盘数据包。没有创建/修改/删除可以调用，
"agent 不能改数据层"这条约束继续成立（改策略仍然只能走 finalize_strategy 的
正常业务路径）。

<h3>身份不接受模型传入</h3>
user_id 一律由调用方（tool_scope）注入，与 memory_search 同一条越权防线：
"复盘谁的数据"不能交给模型决定。

<h3>失败一律 fail-open</h3>
复盘是增强功能。Java 没起、令牌不对、策略不存在……都只能是"这次没有数据"，
返回 None / 空列表并打日志，绝不抛异常打断对话。
"""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

_TIMEOUT_S = 8.0


def _base_url() -> str:
    # 与 memory_store 同一套配置名：两边名字对不上导致静默失效的事在这个项目发生过
    return (os.getenv("JAVA_BASE_URL") or os.getenv("SPRING_BASE_URL")
            or "http://localhost:8080").rstrip("/")


def _headers() -> dict:
    # .env 由 llm_service 在导入时加载，这里必须延迟读取（同 memory_store 的教训）
    token = os.getenv("INTERNAL_API_TOKEN", "").strip()
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Internal-Token"] = token
    return headers


def list_strategies(user_id: str) -> list[dict]:
    """用户的策略清单。失败/无数据都返回空列表（fail-open）。"""
    try:
        resp = requests.get(
            f"{_base_url()}/api/v1/internal/strategies/list",
            params={"userId": user_id}, headers=_headers(), timeout=_TIMEOUT_S)
        if resp.status_code != 200:
            logger.debug("strategy list failed: %s %s", resp.status_code, resp.text[:200])
            return []
        data = resp.json() or {}
        items = data.get("strategies")
        return items if isinstance(items, list) else []
    except Exception as e:  # noqa: BLE001 —— 复盘取不到数据不能打断对话
        logger.debug("strategy list error: %s", e)
        return []


def review(user_id: str, strategy_id: int) -> dict | None:
    """单条策略的复盘数据包（配置/账户/净值/成交/痕迹/日志/预期）。

    返回 None 表示取不到（网络、鉴权、策略不存在或不属于该用户）。
    单节数据缺失由 Java 侧置 null，这里不再补。
    """
    try:
        resp = requests.get(
            f"{_base_url()}/api/v1/internal/strategies/{int(strategy_id)}/review",
            params={"userId": user_id}, headers=_headers(), timeout=_TIMEOUT_S)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            logger.debug("strategy review failed: %s %s", resp.status_code, resp.text[:200])
            return None
        return resp.json()
    except Exception as e:  # noqa: BLE001
        logger.debug("strategy review error: %s", e)
        return None
