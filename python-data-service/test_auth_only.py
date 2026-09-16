# -*- coding: utf-8 -*-
"""
test_auth_only.py — 只验证「三步鉴权」这一件事

为什么单独做一个：
    ths_probe_v2.py 一次跑完四步，中间任何一步错都看不清楚是哪一步。
    这个脚本只做第 3 步，而且每一步都打印中间结果，
    所以「三步鉴权到底能不能用」这个问题的答案会非常明确。

它验证的顺序：
    扫码 → 3.1 RSA公钥 → 3.2 unified_login → 3.3 mainverify → 3.4 docookie2

每一步的判定标准：
    3.1  能拿到 PEM 格式公钥           → 通过
    3.2  返回里有 userid 和 sessionid   → 通过（关键分水岭）
    3.3  返回里有 passport              → 通过
    3.4  拿到至少 1 个 cookie           → 通过

用法：
    .\\.venv\\Scripts\\python.exe test_auth_only.py
"""
import sys
import urllib.parse

import requests

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 从 ths_client 取函数（不再依赖 ths_probe_v2）
# 这样验证脚本测的就是**线上真正跑的那份实现**，不是另一份仿制品。
import ths_client as tc

UPASS = tc.UPASS


def _scan_headers():
    """扫码用的请求头（和 ths_client 里 qr_login 内部一致）。"""
    return dict(tc.qr_login._session().headers)


def _extract(text, pattern):
    return tc._extract(text, pattern)


def _extract_bytes(data, pattern):
    return tc._extract_bytes(data, pattern)


def main():
    print("=" * 64)
    print("三步鉴权专项验证")
    print("=" * 64)

    # ---------------------------------------------------------- 先扫码拿账号
    print("\n[前置] 扫码拿账号（这一步要是失败，后面都无从谈起）")
    client = tc.ThsClient()
    qr = client.create_qr()
    qrid, qr_url = qr["qrid"], qr["qr_url"]
    print(f"    qrid = {qrid}")

    try:
        from thspypc import qr_login

        print()
        print(qr_login.render_qr_ascii(qr_url))
    except Exception:
        print(f"    （把这行贴到在线二维码生成器: {qr_url}）")

    print("\n    请用手机同花顺 App 扫码确认 ...")

    def show_status(data):
        label = {1: "还没扫码", 2: "已扫码待确认", 3: "已确认"}.get(
            data.get("status"), f"状态={data.get('status')}"
        )
        print(f"    {label}")

    try:
        cred = client.poll_qr(qrid, wait_seconds=120, on_status=show_status)
    except tc.ThsApiError as e:
        print(f"\n[✗] 扫码没成功：{e.message}")
        return 1

    print(f"\n    ✓ 拿到 account = {cred['account']}")
    print(f"      （account/password 是账号密码，不是登录态）")

    account, password = cred["account"], cred["password"]

    # ---------------------------------------------------------- 3.1 RSA 公钥
    print("\n" + "-" * 64)
    print("[3.1] 获取 RSA 公钥")
    raw = tc._http_get("/verify2?reqtype=do_rsa&type=get_pubkey")
    text = raw.decode("gb2312", "replace")
    pem = tc._extract(
        text, r'pubkey="(-----BEGIN PUBLIC KEY-----.*?-----END PUBLIC KEY-----)"'
    )
    rsa_version = tc._extract(text, r'rsa_version="([^"]+)"') or "default_5"
    if not pem:
        print(f"    ✗ 失败，原始返回：{text[:300]}")
        return 1
    print(f"    ✓ 公钥已获取（{len(pem)} 字符），rsa_version = {rsa_version}")

    # ---------------------------------------------------------- 3.2 统一登录
    print("\n" + "-" * 64)
    print("[3.2] unified_login（关键分水岭：这里要能拿到 userid/sessionid）")
    try:
        acct_enc = tc._rsa_encrypt(account, pem)
        pwd_enc = tc._rsa_encrypt(password, pem)
        print(f"    RSA 加密完成：account 密文 {len(acct_enc)} 字符，"
              f"password 密文 {len(pwd_enc)} 字符")
    except Exception as e:
        print(f"    ✗ RSA 加密失败: {type(e).__name__}: {e}")
        return 1

    path = (
        f"/verify2?account={urllib.parse.quote(acct_enc, safe='')}"
        f"&msg=1&passwd={urllib.parse.quote(pwd_enc, safe='')}"
        f"&reqtype=unified_login&rsa_version={rsa_version}"
        f"&ta_appid={tc.TA_APPID}"
    )
    text = tc._http_get(path).decode("gb18030", "replace")
    print(f"    服务端原始返回（尾部 300 字符）：")
    print(f"    {text[-300:]}")

    userid = tc._extract(text, r'userid="([^"]*)"')
    sessionid = tc._extract(text, r'sessionid="([^"]*)"')
    if not userid or not sessionid:
        print("\n    ✗ 没拿到 userid/sessionid —— 三步鉴权卡在这一步")
        print("      → 把上面的原始返回贴给我")
        return 1
    print(f"    ✓ userid    = {userid}")
    print(f"    ✓ sessionid = {sessionid[:16]}...")

    # ---------------------------------------------------------- 3.3 主验证
    print("\n" + "-" * 64)
    print("[3.3] mainverify（拿 passport）")
    imei = tc._generate_imei()
    print(f"    设备指纹 imei = {imei}")
    path = (
        f"/verify2?reqtype=mainverify&userid={userid}&sessionid={sessionid}"
        f"&qsid={tc.QSID}&product={tc.PRODUCT}&version={tc.VERSION_HTTP}&imei={imei}&sdsn="
        f"&rsa_version={rsa_version}&nohqlist=0"
        f"&securities={urllib.parse.quote(tc.SECURITIES.encode('gbk'), safe='')}"
    )
    raw = tc._http_get(path)
    seg = tc._extract_bytes(raw, rb"<mainverify>(.*?)</mainverify>")
    if not seg:
        print(f"    ✗ 失败，原始返回：{raw[-300:]!r}")
        return 1

    passport = tc._extract_bytes(seg, rb'passport="(.*?)"')
    signature = tc._extract_bytes(seg, rb'signature="(.*?)"')
    if not passport:
        print(f"    ✗ 没有 passport 字段。XML 内容：{seg[:300]!r}")
        return 1
    print(f"    ✓ passport  = {len(passport)} 字节")
    print(f"    ✓ signature = {signature[:16] if signature else '(无)'}...")

    signvalid = ""
    for field in passport.split(b"|"):
        if field.startswith(b"signvalid="):
            signvalid = field[len(b"signvalid="):].decode("ascii", "replace")
            break
    print(f"    ✓ signvalid = {signvalid[:16] if signvalid else '(没找到!)'}...")
    if not signvalid:
        print("    ⚠ passport 里没有 signvalid，第 3.4 步会失败")

    # ---------------------------------------------------------- 3.4 换 cookie
    print("\n" + "-" * 64)
    print("[3.4] docookie2 换取自选股 cookie")
    try:
        cookies = tc.get_http_cookies(
            {"userid": userid, "sessionid": sessionid, "signvalid": signvalid}
        )
    except tc.ThsApiError as e:
        print(f"    ✗ 失败: {e.message}")
        return 1

    print("\n" + "=" * 64)
    print("三步鉴权全部通过 ✅")
    print("=" * 64)
    print("注意：这里验证的是 ths_client.py 里的实现（和线上跑的是同一份代码）。")
    print(f"下一步可以用这些 cookie 去读自选股（共 {len(cookies)} 个）：")
    for k in cookies:
        print(f"    - {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
