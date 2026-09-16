# -*- coding: utf-8 -*-
"""
test_ths_endpoints.py — 用 HTTP 接口跑一遍扫码登录（教程第 3.4 节的 curl 验证）

和 ths_client.py 自测的区别：
    ths_client.py          直接调 Python 函数（不经过网络）
    本文件                  走 http://localhost:8000 的接口（和 Java 后端一样的路径）

所以这个更接近真实情况：跑通了，就说明 Java 侧照着调也能通。

用法：
    # 1) 先另开一个终端把服务跑起来（会一直挂着，别关）：
    .\\.venv\\Scripts\\python.exe -m uvicorn app:app --port 8000

    # 2) 再开一个终端跑本文件：
    .\\.venv\\Scripts\\python.exe test_ths_endpoints.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BASE = "http://localhost:8000"

# 服务间鉴权：app.py 的中间件要求 /api/v1/ 开头的请求带这个头。
# Java 后端调用时也会自动带（配置项 internal.api-token）。
# 值必须和 python-data-service/.env 里的 INTERNAL_API_TOKEN 一致。
TOKEN = "stock-tracker-internal-2026"


def call(path: str) -> tuple:
    """发一个带内部 token 的 GET，返回 (状态码, 解析后的 JSON 或原始文本)。"""
    req = urllib.request.Request(BASE + path, headers={"x-internal-token": TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        status = e.code
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def post_json(path: str, payload: dict) -> tuple:
    """发一个带内部 token 的 POST（凭证走 body，避免进访问日志）。"""
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"x-internal-token": TOKEN, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        status = e.code
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def main() -> int:
    print("=" * 62)
    print("扫码登录接口验证（走 HTTP，和 Java 后端一样的调用路径）")
    print("=" * 62)

    # ---- 1. 建扫码会话 ----
    status, data = call("/api/v1/ths/qr/create")
    if status != 200 or not data.get("ok"):
        print(f"\n[!] /qr/create 失败 ({status}): {data}")
        print("    服务起来了吗？另开终端跑：")
        print("    .\\.venv\\Scripts\\python.exe -m uvicorn app:app --port 8000")
        return 1

    info = data["data"]
    qr_session_id = info["qrSessionId"]
    qr_url = info["qrUrl"]
    print(f"\n[1] 扫码会话已建好")
    print(f"    qrSessionId = {qr_session_id}")
    print(f"    qrUrl       = {qr_url}")

    # 终端直接把二维码画出来
    try:
        import ths_client  # noqa: F401  触发布 bootstrap
        from thspypc import qr_login

        print("\n" + qr_login.render_qr_ascii(qr_url))
    except Exception as exc:
        print(f"    （终端画二维码失败: {exc}，把上面那行 URL 贴到在线生成器）")

    print("\n[2] 请用手机「同花顺 App」扫码并确认 ...")
    print(f"    二维码 {info['expiresInSec']} 秒内有效，前端轮询间隔 "
          f"{info['pollIntervalMs']}ms")

    # ---- 2. 轮询 ----
    deadline = time.time() + info["expiresInSec"]
    attempt = 0
    session = None
    while time.time() < deadline:
        attempt += 1
        time.sleep(info["pollIntervalMs"] / 1000.0)
        status, data = call(f"/api/v1/ths/qr/poll?qrSessionId={qr_session_id}")
        left = int(deadline - time.time())

        if status != 200 or not data.get("ok"):
            print(f"    [!] 轮询失败 ({status}): {data}")
            return 1

        st = data["data"].get("status")
        print(f"    [{attempt}] status={st}  剩余 {left}s")

        if st == "ok":
            session = data["data"]["session"]
            break

    if session is None:
        print("\n[!] 二维码过期了，重新跑一次本脚本即可。")
        return 1

    print(f"\n[3] ✓ 扫码成功")
    print(f"    account     = {session['account']}")
    print(f"    expire_time = {session['expire_time']} "
          f"({'手机勾选了30天免登录' if session['expire_time'] else '未勾选，凭证仍可用'})")

    # ---- 3. 读自选股（POST，凭证走 body 而不是 URL）----
    print("\n[4] 用扫码拿到的凭证去读自选股 ...")
    print("    （POST + JSON body：避免账号密码出现在服务端访问日志里）")
    status, data = post_json("/api/v1/ths/selfstocks", {
        "account": session["account"],
        "password": session["password"],
    })

    if status != 200 or not data.get("ok"):
        print(f"\n[!] 读自选股失败 ({status}): {data}")
        print("    这通常是 HTTP 三步鉴权没过 —— 把上面输出贴出来一起看。")
        return 1

    stocks = data["data"]
    print(f"\n[5] ✓ 共 {len(stocks)} 只自选股：")
    for s in stocks:
        line = f"    {s['market']}{s['code']}"
        if s.get("price") is not None:
            line += f"   加入价={s['price']}"
        if s.get("addedAt"):
            line += f"   加入时间={s['addedAt']}"
        print(line)

    print("\n" + "=" * 62)
    print("第 3 步完成 ✅  接口通了，接下来可以开始第 4 步（Java ThsClient）")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
