
import base64
import hashlib
import sys
import time

import requests

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://upass.10jqka.com.cn/login",
    "Origin": "https://upass.10jqka.com.cn",
}

UPASS = "https://upass.10jqka.com.cn"
AUTH_HOST = "auth.10jqka.com.cn"     # 注意：走 80 端口明文，不是 https

# 手机端 UA。同花顺的 ugc.10jqka.com.cn 自选接口只认这套 UA，
# 用浏览器的 Mozilla/5.0 会拿不到数据。
_PHONE_UA = (
    "Hexin_Gphone/11.28.03 (Royal Flush) hxtheme/0 "
    "innerversion/G037.09.028.1.32 followPhoneSystemTheme/0 "
    "userid/000000000 getHXAPPAccessibilityMode/0 hxNewFont/1 isVip/0 "
    "getHXAPPFontSetting/normal getHXAPPAdaptOldSetting/0 okhttp/3.14.9"
)
def step1_create_qr(s):
    createcode = s.get(f"{UPASS}/scan/creatCode", headers=HEADERS, timeout=15)
    qrid = createcode.json()["qrid"]
    qr_url = "http://mobile.10jqka.com.cn/?source=PC&qrid=" + qrid
    return qrid, qr_url
def step2_poll(s, qrid, interval=4, max_seconds=120):
    ms_url = f"{UPASS}/scan/getInfoNew"
    data = {
        "qrid": qrid,
        "state": "1",
        "source": "pc_web",
        "page_source": "web_screen",
        "request_type": "login",
    }

    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed > max_seconds:
            print("超过 120 秒，停止轮询")
            return None

        try:
            resp = s.post(ms_url, data=data, headers=HEADERS, timeout=10)
            result = resp.json()
            status = result.get("status")
            label = {1: "还没扫码", 2: "已扫码待确认", 3: "已确认"}.get(status, "?")
            print(f"已用 {elapsed:.0f} 秒，状态：{status}（{label}）")

            if status == 3:
                print("扫码确认，开始换取正式 Cookie")
                return {
                    "account": result.get("account"),
                    "password": result.get("password"),
                }

            # status=1 且 expired=0，说明二维码本身过期了，别白等
            if status == 1 and result.get("expired") == 0:
                print("二维码已过期，请重新运行")
                return None

        except requests.exceptions.Timeout:
            print("本次请求超时，继续")
        except requests.exceptions.RequestException as e:
            # 你原来只捕获 Timeout，其它异常会直接把脚本崩掉
            print(f"请求出错（继续重试）: {e}")

        time.sleep(interval)

def step3_auth(account, password):
    print("\n  ── 3.1 获取 RSA 公钥 ──")
    raw = _http_get("/verify2?reqtype=do_rsa&type=get_pubkey")
    text = raw.decode("gb2312", "replace")
    pem = _extract(text, r'pubkey="(-----BEGIN PUBLIC KEY-----.*?-----END PUBLIC KEY-----)"')
    rsa_version = _extract(text, r'rsa_version="([^"]+)"') or "default_5"
    if not pem:
        raise RuntimeError(f"拿不到 RSA 公钥: {text[:200]}")
    print(f"     ✓ rsa_version = {rsa_version}")

    print("  ── 3.2 统一登录（unified_login）──")
    import urllib.parse

    acct = _rsa_encrypt(account, pem)
    passwd = _rsa_encrypt(password, pem)
    path = (
        f"/verify2?account={urllib.parse.quote(acct, safe='')}"
        f"&msg=1&passwd={urllib.parse.quote(passwd, safe='')}"
        f"&reqtype=unified_login&rsa_version={rsa_version}"
        f"&ta_appid=2022021114090152"
    )
    text = _http_get(path).decode("gb18030", "replace")
    userid = _extract(text, r'userid="([^"]*)"')
    sessionid = _extract(text, r'sessionid="([^"]*)"')
    if not userid or not sessionid:
        raise RuntimeError(f"统一登录失败: {text[-300:]}")
    print(f"     ✓ userid={userid}  sessionid={sessionid[:12]}...")

    print("  ── 3.3 主验证（mainverify）──")
    imei = _generate_imei()
    print(f"     设备指纹 imei = {imei}")
    path = (
        f"/verify2?reqtype=mainverify&userid={userid}&sessionid={sessionid}"
        f"&qsid=6800&product=E02&version=9.60.20.0031&imei={imei}&sdsn="
        f"&rsa_version={rsa_version}&nohqlist=0"
        f"&securities={urllib.parse.quote('同花顺统一版'.encode('gbk'), safe='')}"
    )
    raw = _http_get(path)
    seg = _extract_bytes(raw, rb"<mainverify>(.*?)</mainverify>")
    if not seg:
        raise RuntimeError(f"主验证失败: {raw[-300:]!r}")
    passport = _extract_bytes(seg, rb'passport="(.*?)"')
    if not passport:
        raise RuntimeError("主验证没返回 passport")
    print(f"     ✓ passport 已拿到（{len(passport)} 字节）")

    # passport 是 "k=v|k=v|..." 格式，signvalid 藏在里面，下一步换 cookie 要用
    signvalid = ""
    for field in passport.split(b"|"):
        if field.startswith(b"signvalid="):
            signvalid = field[len(b"signvalid="):].decode("ascii", "replace")
            break
    print(f"     ✓ signvalid = {signvalid[:12]}...")

    return {"userid": userid, "sessionid": sessionid, "signvalid": signvalid}


def step3b_get_cookies(auth):
    print("  ── 3.4 docookie2 换取自选股 cookie ──")
    resp = requests.get(
        f"{UPASS}/docookie2.php",
        params={
            "userid": auth["userid"],
            "sessionid": auth["sessionid"],
            "signvalid": auth["signvalid"],
        },
        headers={"User-Agent": _PHONE_UA},
        timeout=15,
    )
    cookies = resp.cookies.get_dict()
    if not cookies:
        raise RuntimeError("docookie2.php 没有返回 cookies")
    print(f"     ✓ 拿到 {len(cookies)} 个 cookie: {list(cookies.keys())}")
    return cookies
_MARKET_TYPE_ABBR = {
    "17": "SH",    # 沪市 A 股
    "33": "SZ",    # 深市 A 股
    "16": "SH",    # 沪市指数（上证指数等）
    "32": "SZ",    # 深市指数（创业板指等）
    "18": "SH",    # 科创板
    "20": "SH",    # 沪市 ETF
    "36": "SZ",    # 深市 ETF
    "48": "ZS",    # 中证指数
    "71": "BJ",    # 北交所
    "151": "BJ",   # 北交所（另一种写法）
}


def _abbr_by_code(code):
    """市场码表里没有时，按股票代码规则推断市场。

    这样即使同花顺新增了市场码，也不至于显示成一串数字。
    """
    if code.startswith(("60", "68", "90", "11", "13")):
        return "SH"
    if code.startswith(("00", "30", "20", "12", "15", "16", "18", "39")):
        return "SZ"
    if code.startswith(("4", "8", "92")):
        return "BJ"
    return "?"


def parse_selfstock(raw):
    if not raw:
        return []

    parts = raw.split(",", 1)                       # 第一个逗号处切两半
    codes = [c for c in parts[0].split("|") if c]
    markets = [m for m in parts[1].split("|") if m] if len(parts) > 1 else []

    result = []
    for i, code in enumerate(codes):
        mtype = markets[i] if i < len(markets) else ""
        abbr = _MARKET_TYPE_ABBR.get(mtype) or _abbr_by_code(code)
        result.append({"code": code, "market": abbr, "market_type": mtype})
    return result


def step4_get_favorites(cookies, userid=""):
    favstock = "https://ugc.10jqka.com.cn/optdata/selfstock/open/api/v1/query"

    headers = {"User-Agent": _PHONE_UA}
    if userid:
        headers["userid"] = userid

    resp = requests.get(
        favstock,
        headers=headers,
        params={"support_all": "0", "from": "thspc_hevo"},
        cookies=cookies,          # ← 你原来缺的就是这个
        timeout=15,
    )
    return resp

def _http_get(path, timeout=30):
    import socket

    req = (
        f"GET {path} HTTP/1.1\r\nHost: {AUTH_HOST}\r\n"
        f"User-Agent: 同花顺/7.0.10 CFNetwork/1333.0.4 Darwin/21.5.0\r\n"
        f"Connection: close\r\n\r\n"
    ).encode("gbk", "replace")

    sock = socket.create_connection((AUTH_HOST, 80), timeout=timeout)
    sock.settimeout(timeout)
    raw = b""
    try:
        sock.sendall(req)
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            raw += chunk
    except socket.timeout:
        pass
    finally:
        sock.close()
    return raw


def _extract(text, pattern):
    import re

    m = re.search(pattern, text, re.DOTALL)
    return m.group(1) if m else ""


def _extract_bytes(data, pattern):
    import re

    m = re.search(pattern, data, re.DOTALL)
    return m.group(1) if m else b""


def _rsa_encrypt(plaintext, pem):
    """RSA PKCS#1 v1.5 加密。用本项目的纯 Python 垫片，不依赖第三方库。"""
    import os as _os

    sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "_ths_crypto_shim"))
    from Crypto.PublicKey import RSA
    from Crypto.Cipher import PKCS1_v1_5

    return base64.b64encode(
        PKCS1_v1_5.new(RSA.import_key(pem)).encrypt(plaintext.encode("gbk"))
    ).decode()


def _generate_imei():
    """设备指纹。你原来用 f"{mac:012x}" 少了连字符，正确的是大写+连字符。"""
    import ctypes

    class IP_ADAPTER_INFO(ctypes.Structure):
        pass

    IP_ADAPTER_INFO._fields_ = [
        ("Next", ctypes.POINTER(IP_ADAPTER_INFO)),
        ("ComboIndex", ctypes.c_ulong),
        ("AdapterName", ctypes.c_char * 260),
        ("Description", ctypes.c_char * 132),
        ("AddressLength", ctypes.c_uint),
        ("Address", ctypes.c_ubyte * 8),
        ("Index", ctypes.c_ulong),
        ("Type", ctypes.c_uint),
        ("DhcpEnabled", ctypes.c_uint),
        ("CurrentIpAddress", ctypes.c_void_p),
    ]

    buf = (ctypes.c_char * 8192)()
    size = ctypes.c_ulong(8192)
    ctypes.windll.iphlpapi.GetAdaptersInfo(buf, ctypes.byref(size))

    ptr = ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_INFO))
    mac = None
    while ptr:
        info = ptr.contents
        if info.AddressLength == 6:
            mac = bytes(info.Address[:6])
            break
        ptr = info.Next
    if mac is None:
        raise RuntimeError("找不到网卡 MAC")

    # 正确格式：38-A7-46-43-C0-6E（大写、连字符），不是 38a74643c06e
    mac_str = "-".join(f"{b:02X}" for b in mac)
    return hashlib.md5((mac_str + "0" * 30).encode("ascii")).hexdigest().upper()

def main():
    s = requests.session()
    qrid, qr_url = step1_create_qr(s)
    print("    qrid   =", qrid)
    print("    二维码 =", qr_url)

    try:
        import ths_client

        from thspypc import qr_login
        print(qr_login.render_qr_ascii(qr_url))
    except Exception:
        print("    （终端画二维码失败，把上面 URL 贴到在线生成器）")

    print("\n[2] 轮询等待扫码")
    cred = step2_poll(s, qrid)
    if cred is None:
        return 1
    print(f"    account = {cred['account']}")
    try:
        auth = step3_auth(cred["account"], cred["password"])
        cookies = step3b_get_cookies(auth)
    except Exception as e:
        print(f"    ✗ 失败: {type(e).__name__}: {e}")
        return 1

    print("\n[4] 读自选股")
    try:
        resp = step4_get_favorites(cookies, auth["userid"])
    except Exception as e:
        print(f"    ✗ 请求失败: {type(e).__name__}: {e}")
        return 1

    print(f"    HTTP {resp.status_code}")
    payload = resp.json()
    if payload.get("status_code") != 0:
        print(f"    ✗ 接口报错: {payload}")
        return 1

    data = payload.get("data", {})
    raw = data.get("selfstock", "")
    print(f"    原始 selfstock = {raw!r}")

    stocks = parse_selfstock(raw)
    if not stocks:
        print("\n    （自选股是空的）")
        return 0

    print(f"\n    共 {len(stocks)} 只：")
    print(f"    {'内部规范代码':<14}{'原始代码':<12}{'市场码':<8}")
    print("    " + "-" * 40)
    for st in stocks:
        symbol = f"{st['market']}{st['code']}"     # 内部规范：SH600519 这种
        print(f"    {symbol:<14}{st['code']:<12}{st['market_type']:<8}")
    print("\n[5] 获取加入价/加入时间")
    try:
        # 注意：userid 是 auth["userid"]，不是裸变量 userid
        # （之前这里写成裸 userid，直接 NameError，连请求都没发出去）
        detail_resp = requests.get(
            "https://ugc.10jqka.com.cn/selfstock_detail",
            params={"reqtype": "download", "app_flag": "0E",
                    "userid": auth["userid"]},
            headers={"User-Agent": _PHONE_UA, "userid": auth["userid"]},
            cookies=cookies,
            timeout=15,
        )
        print(f"    HTTP {detail_resp.status_code}")
        if detail_resp.status_code != 200:
            print(f"    ✗ 接口返回非 200：{detail_resp.text[:200]}")
            return 0

        # 用 thspypc 自带的解析函数拆 XML + base64 + JSON
        from thspypc.blocks import _parse_selfstock_detail_response

        version, detail_list = _parse_selfstock_detail_response(detail_resp.text)
        print(f"    ✓ 解析出 {len(detail_list)} 条明细（版本 {version}）")

        # 自己建索引：key 用 (代码, 原始市场码)，两边都用原始码就不会错位
        detail_index = {}
        for entry in detail_list:
            code = entry.get("C", "")
            mtype = str(entry.get("M", ""))
            if not code:
                continue
            raw_price = entry.get("P")
            price = None
            if raw_price not in (None, ""):
                try:
                    price = float(raw_price)
                except (TypeError, ValueError):
                    pass
            detail_index[(code, mtype)] = {"price": price, "added_at": entry.get("T")}

        if detail_index:
            print(f"\n    {'内部规范代码':<14}{'加入价':<12}{'加入时间'}")
            print("    " + "-" * 42)
            for st in stocks:
                meta = detail_index.get((st["code"], str(st["market_type"]))) or {}
                price = meta.get("price")
                added = meta.get("added_at") or "-"
                symbol = f"{st['market']}{st['code']}"
                print(f"    {symbol:<14}"
                      f"{(f'{price:g}' if price is not None else '-'):<12}{added}")
            print("\n    ⚠️ 「加入价」是同花顺记录的加入自选时价格，不是你真实的买入成本，")
            print("       不要拿它算盈亏（这点你的设计文档 §10 里也写明了）。")
        else:
            print("    （明细为空）")
    except requests.exceptions.RequestException as e:
        # 网络/HTTP 层出错
        print(f"    （请求失败: {type(e).__name__}: {e}）")
    except Exception as e:
        # 其它问题（解析失败、代码 bug 等）——单独标出来，避免和网络问题混淆
        print(f"    （处理出错，这不是网络问题: {type(e).__name__}: {e}）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
