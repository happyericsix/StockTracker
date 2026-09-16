# -*- coding: utf-8 -*-
"""验证扫码端点的「容忍无效 JWT」修复（一次性脚本）"""
import json
import sys
import urllib.error
import urllib.request

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BASE = "http://localhost:8080"
BOGUS = "Bearer eyJhbGciOiJSUzI1NiJ9.INVALID.SIGNATURE"


def call(desc, method, path, with_bogus=False):
    url = BASE + path
    headers = {"Authorization": BOGUS} if with_bogus else {}
    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            code, body = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code, body = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        code, body = None, f"{type(e).__name__}: {e}"

    shown = body[:150].replace("\n", " ")
    print(f"  {desc}")
    print(f"      -> HTTP {code}")
    if shown:
        print(f"      -> {shown}")
    print()
    return code


print("=" * 66)
print("扫码端点「容忍无效 JWT」修复验证")
print("=" * 66)
print()

print("【A. 扫码端点（permitAll）—— 应全部 200】")
c1 = call("① 无 JWT + POST /ths/qr/create", "POST", "/api/v1/ths/qr/create")
c2 = call("② 无效 JWT + POST /ths/qr/create  ★ 修复目标", "POST",
          "/api/v1/ths/qr/create", with_bogus=True)
c3 = call("③ 无效 JWT + GET /ths/qr/poll?qrSessionId=bogus", "GET",
          "/api/v1/ths/qr/poll?qrSessionId=bogus", with_bogus=True)

print("【B. 保护端点（需 JWT）—— 应仍为 401】")
c4 = call("④ 无效 JWT + GET /ths/status", "GET", "/api/v1/ths/status", with_bogus=True)
c5 = call("⑤ 无效 JWT + POST /ths/sync", "POST", "/api/v1/ths/sync", with_bogus=True)
c6 = call("⑥ 无 JWT + GET /ths/status", "GET", "/api/v1/ths/status")

print("=" * 66)
ok = True
if c1 != 200 or c2 != 200 or c3 != 200:
    print(f"❌ 扫码端点异常：qr/create 无JWT={c1}, 有无效JWT={c2}, poll={c3}")
    ok = False
if c4 != 401 or c5 != 401 or c6 != 401:
    print(f"❌ 保护端点漏了！ status={c4}, sync={c5}, status(无JWT)={c6} —— 应全为 401")
    ok = False
if ok:
    print("✅ 全部符合预期：扫码端点容忍无效 JWT，保护端点仍严格 401")
print("=" * 66)
sys.exit(0 if ok else 1)
