"""
user_context.py —— 用户上下文

通过 QQ 号查内部 userId，再通过 userId 查自选股等用户数据。

设计：
- Python 调 Spring Boot 内部接口需要 JWT，复杂
- 当前阶段用最简方案：Python 直接调后端 /api/v1/internal/user/lookup-qq?qqId=xxx
  接口返回 {userId, username, qqBound} 等基本信息
- 获取自选股时用 service-to-service 鉴权（X-Internal-Token）
"""
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# 后端地址
SPRING_BASE_URL = os.getenv("SPRING_BASE_URL", "http://localhost:8080").rstrip("/")
INTERNAL_TOKEN = os.getenv("INTERNAL_API_TOKEN", "stock-tracker-internal-2026")
TIMEOUT = int(os.getenv("SPRING_TIMEOUT", "5"))


def _headers() -> dict:
    return {
        "X-Internal-Token": INTERNAL_TOKEN,
        "Content-Type": "application/json",
    }


def lookup_user_by_qq(qq_id: str) -> Optional[dict]:
    """
    通过 QQ 号查用户信息

    Returns:
        {userId, username, bound} 或 None
    """
    try:
        url = f"{SPRING_BASE_URL}/api/v1/internal/user/lookup-qq"
        resp = requests.get(
            url,
            params={"qqId": qq_id},
            headers=_headers(),
            timeout=TIMEOUT,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        # 后端返回 {code, message, data: {userId, username, qqBound}}
        if data.get("code") == 200 and data.get("data"):
            return data["data"]
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"lookup_user_by_qq 失败: {e}")
        return None


def get_user_watchlist(username: str) -> list[dict]:
    """
    查用户的自选股（通过后端 /api/v1/stocks/favorites）

    注意：这个接口需要用户 JWT，这里用 service token 模拟
    TODO: 后端需要加 service-to-service 鉴权的 favorites 接口
    """
    # 暂用占位实现：如果后端有 internal 接口走 internal
    # 否则用降级：让用户自己在QQ发"刷新自选"触发
    try:
        url = f"{SPRING_BASE_URL}/api/v1/internal/user/favorites"
        resp = requests.get(
            url,
            params={"username": username},
            headers=_headers(),
            timeout=TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == 200:
                return data.get("data", []) or []
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"get_user_watchlist 失败: {e}")
        return []


def verify_and_bind_qq(qq_id: str, code: str) -> dict:
    """
    调后端 internal API 验证 + 绑定 QQ

    Returns:
        {success, message, data: {...}}
    """
    try:
        url = f"{SPRING_BASE_URL}/api/v1/internal/user/bind-qq"
        resp = requests.post(
            url,
            json={"qqId": qq_id, "code": code},
            headers=_headers(),
            timeout=TIMEOUT,
        )
        data = resp.json()
        success = resp.status_code == 200 and data.get("code") == 200
        return {
            "success": success,
            "message": data.get("message", ""),
            "data": data.get("data"),
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"verify_and_bind_qq 失败: {e}")
        return {"success": False, "message": f"网络错误: {e}", "data": None}
