# -*- coding: utf-8 -*-
"""
test_e2e_scan.py — 第 5 步端到端验收：真实扫码全链路

代替尚未实现的前端，跑完整个流程：
    Java(8080) 生成二维码 → 渲染到终端 → 你手机扫
      → Java 轮询 Python → Python 问同花顺
      → 扫到后 Java：建/找用户 → 加密存凭证 → 签发 JWT
      → 本脚本用拿到的 JWT 调 /ths/sync 同步自选股

⚠️ 会写数据库：
    - ths_bindings 新增/更新一行（加密凭证）
    - favorite_stocks 插入你自选里的股票（只增不改，不写 buyPrice）

用法：
    .\\.venv\\Scripts\\python.exe test_e2e_scan.py
"""
import json
import sys
import time
import urllib.error
import urllib.request

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BASE = "http://localhost:8080"


def call(method, path, token=None, timeout=60):
    """调 Java 接口，返回 (状态码, 解析后的 JSON)。"""
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"raw": body}
    except Exception as e:
        return None, {"error": f"{type(e).__name__}: {e}"}


def render_qr(url):
    """终端渲染二维码。"""
    try:
        import ths_client  # noqa: F401  触发 vendor 路径 bootstrap
        from thspypc import qr_login

        return qr_login.render_qr_ascii(url)
    except Exception as e:
        return f"（渲染失败: {e}）"


def main():
    print("=" * 66)
    print("第 5 步端到端验收：扫码 → JWT → 同步自选股")
    print("=" * 66)
    print()

    # ---------- ① 生成二维码 ----------
    print("[1] 调 Java 生成二维码 ...")
    status, body = call("POST", "/api/v1/ths/qr/create")
    if status != 200 or body.get("code") != 200:
        print(f"    ✗ 失败: HTTP {status} {body}")
        return 1

    d = body["data"]
    sid = d["qrSessionId"]
    qr_url = d["qrUrl"]
    print(f"    ✓ qrSessionId = {sid}")
    print(f"    ✓ qrUrl       = {qr_url}")
    print()
    print(render_qr(qr_url))
    print()
    print(f"    ⚠️ 二维码 {d['expiresInSec']} 秒内有效")
    print("    请用手机「同花顺 App」扫码并确认（建议勾选「30 天免登录」）")
    print()

    # ---------- ② 轮询 ----------
    print("[2] 轮询（前端每 4 秒做一次，这里同步做）...")
    deadline = time.time() + d["expiresInSec"]
    token = None
    poll_n = 0
    while time.time() < deadline:
        time.sleep(4)
        poll_n += 1
        status, body = call("GET", f"/api/v1/ths/qr/poll?qrSessionId={sid}")
        if status != 200 or body.get("code") != 200:
            print(f"    ✗ 第{poll_n}次 轮询失败: HTTP {status} {body}")
            return 1

        st = body["data"].get("status")
        left = int(deadline - time.time())
        print(f"    第{poll_n}次: status={st}  剩余 {left}s")

        if st == "ok":
            token = body["data"].get("token")
            print()
            print(f"    ✓ 登录成功")
            print(f"      username   = {body['data'].get('username')}")
            print(f"      firstLogin = {body['data'].get('firstLogin')}")
            print(f"      token      = {(token or '')[:40]}...")
            break
        if st == "expired":
            print(f"    ✗ 二维码过期: {body['data'].get('message')}")
            return 1

    if not token:
        print("\n    ✗ 超时未扫码")
        return 1

    # ---------- ③ 用 JWT 查绑定状态 ----------
    print()
    print("[3] 用拿到的 JWT 调 /ths/status ...")
    status, body = call("GET", "/api/v1/ths/status", token=token)
    print(f"    HTTP {status}")
    print(f"    {json.dumps(body, ensure_ascii=False)}")
    if status != 200:
        print("    ✗ 保护端点用 JWT 访问失败 —— 不应该发生")
        return 1

    # ---------- ④ 同步自选股 ----------
    print()
    print("[4] 调 /ths/sync 同步自选股 ...")
    status, body = call("POST", "/api/v1/ths/sync", token=token)
    print(f"    HTTP {status}")
    print(f"    {json.dumps(body, ensure_ascii=False)}")

    if status != 200 or body.get("code") != 200:
        print("\n    ⚠️ 同步失败（凭证可能刚失效，或同花顺侧拒绝）")
        return 1

    sd = body.get("data") or {}
    print()
    print("=" * 66)
    print("✅ 第 5 步端到端全部打通")
    print("=" * 66)
    print(f"  新增自选股   {sd.get('added')} 只")
    print(f"  已存在跳过   {sd.get('unchanged')} 只")
    print(f"  同花顺侧共   {sd.get('total')} 只")
    print(f"  同步时间     {sd.get('lastSyncAt')}")
    print()
    print("  验证要点：")
    print("    · 这些股票**没有** buyPrice（同花顺加入价不是成本价）")
    print("    · 再扫一次码应登进**同一个**账号（不会新建用户）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
