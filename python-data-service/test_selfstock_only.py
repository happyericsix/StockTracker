# -*- coding: utf-8 -*-
"""
test_selfstock_only.py — 只验证「读取自选股」这一段

背景：三步鉴权已经实测通过（userid/sessionid/passport/signvalid/cookie 全部拿到）。
      现在要确认的是：用这些 cookie 到底怎么正确读出股票列表。

对比两种取法：
    A) 我 ths_probe_v2.py 里用的：/optdata/selfstock/open/api/v1/query
       params: {"from": "sjcg_gphone"}
    B) thspypc 内部用的：同一个路径，但
       params: {"support_all": "0", "from": "thspc_hevo"}
       另外会在 header 里加 userid

两种都跑一遍，把原始返回并排打出来，看哪个干净。

用法：
    .\\.venv\\Scripts\\python.exe test_selfstock_only.py
"""
import json
import sys
import urllib.parse

import requests

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import ths_client as tc

UPASS = tc.UPASS
FAVSTOCK = f"{tc.UGC_BASE}{tc.SELFSTOCK_QUERY_PATH}"

# 手机端 UA（和 ths_client 内部用的一致）
PHONE_UA = tc._PHONE_UA


def get_credentials():
    """扫码 → 三步鉴权 → 换 cookie。

    返回 (cookies, userid, account, password) —— 后面方式 C 要重新调用
    get_self_stocks，需要原始账号密码，所以一起返回。
    """
    client = tc.ThsClient()
    qr = client.create_qr()
    print(f"[1] qrid = {qr['qrid']}")

    try:
        from thspypc import qr_login

        print()
        print(qr_login.render_qr_ascii(qr["qr_url"]))
    except Exception:
        print(f"    （把这行贴到在线生成器: {qr['qr_url']}）")

    print("\n[2] 扫码 ...")
    try:
        cred = client.poll_qr(qr["qrid"], wait_seconds=120)
    except tc.ThsApiError as e:
        raise SystemExit(f"扫码失败: {e.message}")

    print("\n[3] 三步鉴权 ...")
    auth = tc.http_auth(cred["account"], cred["password"])
    cookies = tc.get_http_cookies(auth)
    return cookies, auth["userid"], cred["account"], cred["password"]


def try_fetch(label, cookies, userid, params, extra_headers=None):
    """发一次自选股请求，打印原始返回。"""
    headers = {"User-Agent": PHONE_UA}
    if extra_headers:
        headers.update(extra_headers)

    print(f"\n{'=' * 64}")
    print(f"【{label}】")
    print(f"  params  = {params}")
    print(f"  额外头  = {extra_headers or '（无）'}")
    resp = requests.get(
        FAVSTOCK, params=params, headers=headers, cookies=cookies, timeout=15
    )
    print(f"  HTTP {resp.status_code}")
    body = resp.text
    print(f"  原始返回: {body[:500]}")

    try:
        data = resp.json().get("data", {})
    except Exception:
        print("  （不是 JSON）")
        return None

    raw = data.get("selfstock", "")
    if not raw:
        print("  → selfstock 字段为空")
        return data

    # 用 thspypc 的官方解析函数解析
    from thspypc.blocks import parse_group_content, market_abbr

    entries = parse_group_content(raw)
    print(f"\n  解析出 {len(entries)} 只股票：")
    for e in entries:
        abbr = market_abbr(e.market_type)
        print(f"      code={e.code:<10} market_type={e.market_type:<4} "
              f"-> 前缀 {abbr}  =>  {abbr}{e.code}")
    return data


def main():
    print("=" * 64)
    print("自选股读取专项验证")
    print("=" * 64)

    cookies, userid, account, password = get_credentials()
    print(f"\n✓ cookie 就绪，userid = {userid}")

    # ---- 方式 A：我 v2 里用的（from=sjcg_gphone）----
    try_fetch(
        "A: from=sjcg_gphone（我 v2 里的写法）",
        cookies, userid,
        params={"from": "sjcg_gphone"},
    )

    # ---- 方式 B：thspypc 内部用的（from=thspc_hevo + userid 头）----
    try_fetch(
        "B: from=thspc_hevo（thspypc 的写法，带 userid 头）",
        cookies, userid,
        params={"support_all": "0", "from": "thspc_hevo"},
        extra_headers={"userid": userid},
    )

    # ---- 方式 C：项目实际用的那种（就是 ths_client.get_self_stocks）----
    print(f"\n{'=' * 64}")
    print("【C: ths_client.get_self_stocks —— 项目正式走的那条路】")
    try:
        client = tc.ThsClient()
        stocks = client.get_self_stocks(account, password)
        print(f"  共 {len(stocks)} 只：")
        for s in stocks:
            line = f"    {s['market']}{s['code']}"
            if s.get("price") is not None:
                line += f"   加入价={s['price']}"
            if s.get("addedAt"):
                line += f"   加入时间={s['addedAt']}"
            print(line)
    except Exception as e:
        print(f"  ✗ {type(e).__name__}: {e}")

    print("\n" + "=" * 64)
    print("验证结束。项目正式采用的是方式 B 的参数（from=thspc_hevo + userid 头），")
    print("方式 C 就是在验证它——两者结果应该完全一致。")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
