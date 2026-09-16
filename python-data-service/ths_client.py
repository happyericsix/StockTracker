# -*- coding: utf-8 -*-
"""
ths_client.py — 同花顺客户端（扫码登录 + 读取自选股）

⚠️ 本文档的「三步鉴权」和「自选股取数」实现，是从已实测通过的
   `ths_probe_v2.py` 原样搬过来的（2026-09-12 实测：扫码 → userid/sessionid
   → passport → signvalid → 8 个 cookie → 自选股 HTTP 200）。
   所以这两块**不要随意改**，改之前先回 ths_probe_v2.py 确认。

协议来源：
    扫码登录 + 自选股解析   ← djj45/thspypc（源码在 stock-tracker/vendor/）
    三步鉴权 + 自选股取数   ← 本项目实测（原 ths_probe_v2.py）
    RSA 加密                ← 本项目 _ths_crypto_shim（纯标准库，无第三方依赖）

对外 3 个方法（app.py 和 Java 都靠这几个，改动必须保持兼容）：

    client.create_qr()                       -> {"qrid":..., "qr_url":...}
    client.poll_qr(qrid, wait_seconds)       -> {"account":..., "password":...}
    client.get_self_stocks(account, password)-> [{"code":..., "market":..., "price":..., "addedAt":...}]

> 为什么没有 list_groups：同花顺的自定义分组对"把自选股同步进项目"这件事
> 没有用处（项目只需要一份股票列表），而且那个接口一直没验证过、多一个失败点。
> 已按需求移除。若将来真要多分组同步，再按 thspypc blocks.py 的
> /optdata/selfgroup/open/api/group/v1/query 加回来。

完整链路（记住这个，很多坑都源于误解它）：

    扫码 ──→ account + password        ← 这是【账号+密码】，不是登录态
              │
              ├─ 3.1 GET  auth.10jqka.com.cn:80/verify2?reqtype=do_rsa  → RSA 公钥
              ├─ 3.2 GET  /verify2?reqtype=unified_login&account=<RSA>&passwd=<RSA>
              │                                              → userid + sessionid
              ├─ 3.3 GET  /verify2?reqtype=mainverify&userid=..&sessionid=..&imei=..
              │                                              → passport（内含 signvalid）
              └─ 3.4 GET  upass.10jqka.com.cn/docookie2.php  → HTTP cookies
                                                               ↓
                                              自选股接口（ugc.10jqka.com.cn）只认这个 cookie
"""
from __future__ import annotations

import base64
import math
import os
import re
import socket
import sys

# ==================== 1. 把 vendored 依赖挂上 sys.path ====================
#
# 必须在 `import thspypc` 之前执行。
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_THSPYPC_SRC = os.path.join(_REPO_ROOT, "vendor", "thspypc_src", "src")
_QRCODE_LIB = os.path.join(_REPO_ROOT, "vendor", "qrcode_lib")
_CRYPTO_SHIM = os.path.join(_HERE, "_ths_crypto_shim")


def _bootstrap() -> None:
    """让 `import thspypc` / `import qrcode` / `import Crypto` 都能找到。"""
    if not os.path.isdir(_THSPYPC_SRC):
        raise RuntimeError(
            f"找不到 vendored thspypc：{_THSPYPC_SRC}\n"
            "请先在 python-data-service 目录运行：\n"
            "    .\\.venv\\Scripts\\python.exe _fetch_ths.py"
        )

    # qrcode：终端渲染二维码用（没有真 qrcode 才用 vendor 副本）
    try:
        import qrcode  # noqa: F401
    except ImportError:
        if os.path.isdir(_QRCODE_LIB):
            sys.path.insert(0, _QRCODE_LIB)

    if os.path.isdir(_CRYPTO_SHIM):
        sys.path.insert(0, _CRYPTO_SHIM)

    sys.path.insert(0, _THSPYPC_SRC)


_bootstrap()

# 下面这些 import 必须在 _bootstrap() 之后写
import requests  # noqa: E402
from thspypc import qr_login  # noqa: E402
from thspypc import generate_imei, generate_mac64  # noqa: E402


def _generate_imei() -> str:
    """生成 mainverify 用的设备指纹（32 位大写 hex）。

    就是 thspypc.generate_imei 的别名。这里包一层是因为：
    验证脚本（test_auth_only.py 等）习惯用 `_generate_imei()` 这个名字，
    而且这些脚本现在统一从本模块取函数，不再各自实现一份。
    """
    return generate_imei()


# ==================== 2. 常量（接口变更改这里） ====================

UPASS = "https://upass.10jqka.com.cn"
AUTH_HOST = "auth.10jqka.com.cn"     # 注意：走 80 端口明文 HTTP，不是 https
AUTH_PORT = 80

# userid/sessionid 在 passport 里
TA_APPID = "2022021114090152"
QSID = "6800"
PRODUCT = "E02"
VERSION_HTTP = "9.60.20.0031"
SECURITIES = "同花顺统一版"
UA_GBK = "同花顺/7.0.10 CFNetwork/1333.0.4 Darwin/21.5.0"

# 自选股接口只认手机端 UA，用 Mozilla/5.0 会拿不到数据
_PHONE_UA = (
    "Hexin_Gphone/11.28.03 (Royal Flush) hxtheme/0 "
    "innerversion/G037.09.028.1.32 followPhoneSystemTheme/0 "
    "userid/000000000 getHXAPPAccessibilityMode/0 hxNewFont/1 isVip/0 "
    "getHXAPPFontSetting/normal getHXAPPAdaptOldSetting/0 okhttp/3.14.9"
)

UGC_BASE = "https://ugc.10jqka.com.cn"
SELFSTOCK_QUERY_PATH = "/optdata/selfstock/open/api/v1/query"
SELFSTOCK_DETAIL_URL = "https://ugc.10jqka.com.cn/selfstock_detail"

# 自选股取数参数。实测 from=thspc_hevo 是 thspypc 内部用的，
# 网上流传的 sjcg_gphone 也能返回但格式不如这个稳定。
_SELFSTOCK_FROM = "thspc_hevo"


# ==================== 3. 异常 ====================


class ThsApiError(Exception):
    """同花顺接口出错。

    message 是可以直接给用户看的中文；
    code    是给**程序**判断用的类型标识（见下面几个常量）。

    ⚠️ 为什么要有 code：上层需要区分"手机还没扫码（正常，继续等）"和
    "真出错了（该报错）"。曾经用 `if "超时" in message` 来判断，这是反模式——
    文案一改判断就失效，别的错误消息里恰好含"超时"还会误判。
    """

    # 类型标识常量
    FAILED = "failed"            # 通用失败
    TIMEOUT = "timeout"          # 等待扫码超时（正常状态：手机还没扫）
    QR_EXPIRED = "qr_expired"    # 二维码本身过期
    BAD_CREDENTIALS = "bad_credentials"  # 账号/密码失效，需要重新扫码

    def __init__(self, message: str, code: str = FAILED):
        super().__init__(message)
        self.message = message
        self.code = code


# ==================== 4. 三步鉴权（搬自 ths_probe_v2，已实测） ====================


def _http_get(path: str, timeout: float = 30) -> bytes:
    """裸 socket 发 HTTP GET 到 auth.10jqka.com.cn:80。

    为什么不用 requests：这个鉴权接口走的是 **80 端口明文 HTTP**，
    而且服务端对 UA 有要求（识别同花顺客户端）。
    这段是 ths_probe_v2 里实测通过的写法，原样保留。
    """
    req = (
        f"GET {path} HTTP/1.1\r\nHost: {AUTH_HOST}\r\n"
        f"User-Agent: {UA_GBK}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode("gbk", "replace")

    sock = socket.create_connection((AUTH_HOST, AUTH_PORT), timeout=timeout)
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


def _extract(text, pattern: str) -> str:
    """从文本里用正则取第 1 个捕获组，取不到返回空串。"""
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1) if m else ""


def _extract_bytes(data: bytes, pattern: bytes) -> bytes:
    m = re.search(pattern, data, re.DOTALL)
    return m.group(1) if m else b""


def _rsa_encrypt(plaintext: str, pem: str) -> str:
    """RSA PKCS#1 v1.5 加密，返回 base64。

    用本项目的纯 Python 垫片（_ths_crypto_shim），不依赖 cryptography 或
    pycryptodome —— 那两个都装不上，装了也可能在别人环境里缺失。
    """
    from Crypto.PublicKey import RSA
    from Crypto.Cipher import PKCS1_v1_5

    return base64.b64encode(
        PKCS1_v1_5.new(RSA.import_key(pem)).encrypt(plaintext.encode("gbk"))
    ).decode()


def http_auth(account: str, password: str) -> dict:

    import urllib.parse

    # ---- 3.1 RSA 公钥 ----
    text = _http_get("/verify2?reqtype=do_rsa&type=get_pubkey").decode("gb2312", "replace")
    pem = _extract(
        text, r'pubkey="(-----BEGIN PUBLIC KEY-----.*?-----END PUBLIC KEY-----)"'
    )
    rsa_version = _extract(text, r'rsa_version="([^"]+)"') or "default_5"
    if not pem:
        raise ThsApiError(f"拿不到 RSA 公钥：{text[:200]}")

    # ---- 3.2 统一登录 ----
    acct_enc = _rsa_encrypt(account, pem)
    pwd_enc = _rsa_encrypt(password, pem)
    path = (
        f"/verify2?account={urllib.parse.quote(acct_enc, safe='')}"
        f"&msg=1&passwd={urllib.parse.quote(pwd_enc, safe='')}"
        f"&reqtype=unified_login&rsa_version={rsa_version}"
        f"&ta_appid={TA_APPID}"
    )
    text = _http_get(path).decode("gb18030", "replace")
    userid = _extract(text, r'userid="([^"]*)"')
    sessionid = _extract(text, r'sessionid="([^"]*)"')
    if not userid or not sessionid:
        raise ThsApiError(f"统一登录失败（账号或密码可能已失效）：{text[-200:]}")

    # ---- 3.3 主验证 ----
    # imei 是设备指纹，错的 imei 会被写进 passport 导致后续失败
    imei = generate_imei()
    path = (
        f"/verify2?reqtype=mainverify&userid={userid}&sessionid={sessionid}"
        f"&qsid={QSID}&product={PRODUCT}&version={VERSION_HTTP}&imei={imei}&sdsn="
        f"&rsa_version={rsa_version}&nohqlist=0"
        f"&securities={urllib.parse.quote(SECURITIES.encode('gbk'), safe='')}"
    )
    raw = _http_get(path)
    seg = _extract_bytes(raw, rb"<mainverify>(.*?)</mainverify>")
    if not seg:
        raise ThsApiError(f"主验证失败：{raw[-200:]!r}")
    passport = _extract_bytes(seg, rb'passport="(.*?)"')
    if not passport:
        raise ThsApiError("主验证没返回 passport")

    # passport 是 "k=v|k=v|..." 格式，signvalid 藏在里面，换 cookie 要用
    signvalid = ""
    for field in passport.split(b"|"):
        if field.startswith(b"signvalid="):
            signvalid = field[len(b"signvalid="):].decode("ascii", "replace")
            break

    return {
        "userid": userid,
        "sessionid": sessionid,
        "signvalid": signvalid,
        "passport_bytes": passport,
    }


def get_http_cookies(auth: dict) -> dict:
    """3.4 用 passport 的三个字段去 docookie2.php 换 HTTP cookies。

    这一步是自选股的关键：ugc.10jqka.com.cn 只认这个 cookie，
    不认 passport 本身。少了它自选股必然 401。
    """
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
        raise ThsApiError("docookie2.php 没有返回 cookies（凭证可能已失效）")
    return cookies


# ==================== 5. 自选股解析（搬自 ths_probe_v2，已实测） ====================

# 市场码映射。
# 为什么不直接用 thspypc 的 market_abbr()：它表里没有 16/32（指数码），
# 实测返回 "1A0001|399006|,17|16|32|33|" 里的 16/32 会变成裸数字。
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


def _abbr_by_code(code: str) -> str:
    """市场码表里没有时，按代码规则推断市场（防止显示成裸数字）。"""
    if code.startswith(("60", "68", "90", "11", "13")):
        return "SH"
    if code.startswith(("00", "30", "20", "12", "15", "16", "18", "39")):
        return "SZ"
    if code.startswith(("4", "8", "92")):
        return "BJ"
    return "?"


def parse_selfstock(raw: str) -> list:
    """解析 selfstock 字段 -> [{"code","market","market_type"}, ...]

    格式（实测）：
        "688023|1A0001|399006|300033|,17|16|32|33|"
         └──── 代码，竖线分隔 ────┘ └─ 市场码，同样竖线分隔 ─┘
    用第一个逗号切两半，按下标一一对应；末尾多一个空串要滤掉。
    """
    if not raw:
        return []

    parts = raw.split(",", 1)
    codes = [c for c in parts[0].split("|") if c]
    markets = [m for m in parts[1].split("|") if m] if len(parts) > 1 else []

    result = []
    for i, code in enumerate(codes):
        mtype = markets[i] if i < len(markets) else ""
        result.append({
            "code": code,
            "market": _MARKET_TYPE_ABBR.get(mtype) or _abbr_by_code(code),
            "market_type": mtype,
        })
    return result


# ==================== 5.5 数值解析 ====================
#
# 这是整个模块里**唯一**把外部字符串转成 float 的地方，
# 所以也是唯一可能产生"非法 JSON"的地方，必须小心处理。


def _safe_float(raw):
    """把同花顺给的字符串转成 float；转不了或不是有限数就返回 None。

    ⚠️ 为什么不能只写 try: float(raw) except ValueError: None
       因为 `float('nan')` 和 `float('inf')` **不会抛异常**，
       它们会成功返回 nan / inf，然后 json.dumps 输出成：

           {"price": NaN}          ← 这不是合法 JSON！
           {"price": Infinity}     ← 同样非法

       Java 的 Jackson 遇到会直接抛 JsonParseException，
       而且报错是 "Non-standard token 'NaN'"，很难联想到根因。
       （实测确认：见 test_type_contract.py 的 [2] 段）

    所以必须额外用 math.isfinite() 挡掉 nan / inf。
    """
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    # nan / inf / -inf 都不合法，当成"没有值"处理
    if not math.isfinite(value):
        return None
    return value


# ==================== 6. 客户端 ====================


class ThsClient:
    """同花顺客户端。每次请求新建实例（无状态，避免 cookie 串号）。"""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        # 扫码要同一个 Session 接力（create 时拿到的 cookie，poll 时要继续用）
        self._session = None

    # ---------- 扫码登录 ----------
    #
    # 这两步仍用 thspypc 的 qr_login（它内部就是 requests.Session，
    # 且请求头/body/状态码都已验证正确）。我们只做一层包装：
    # 把它的对象/异常翻译成字典/ThsApiError。

    def create_qr(self) -> dict:
        """生成登录二维码。

        返回 {"qrid": "usk_xxx", "qr_url": "http://mobile.10jqka.com.cn/?source=PC&qrid=..."}
        qr_url 是给前端画成二维码的内容，手机同花顺 App 扫它。
        """
        try:
            session = qr_login._session()
            qrid = qr_login.create_qrcode(session)
            qr_url = qr_login.get_scan_url(qrid)
        except Exception as exc:
            raise ThsApiError(f"取二维码失败：{exc}") from exc

        self._session = session      # poll 时复用（同花顺要同一会话的 cookie）
        return {"qrid": qrid, "qr_url": qr_url}

    def poll_qr(self, qrid: str, wait_seconds: int = 120, on_status=None) -> dict:
        """轮询等待手机扫码确认。

        Args:
            qrid: create_qr() 返回的二维码 ID
            wait_seconds: 最多等多久（二维码有效期约 120 秒）
            on_status: 可选回调 fn(status_dict)，能看到轮询过程；
                       回调抛异常不影响轮询结果。

        返回 {"account": "mx_xxx", "password": "<32位hex>", "expire_time": 0 或时间戳}

        ⚠️ account/password 是**账号和密码**，不是 token。
        要读自选股还得拿它去做三步鉴权（见 get_self_stocks）。
        """
        if not qrid:
            raise ThsApiError("缺少 qrid")

        session = self._session or qr_login._session()

        def _safe_on_status(data):
            if on_status is None:
                return
            try:
                on_status(data)
            except Exception:
                pass          # 回调只是给人看的提示，不能让它弄挂轮询

        try:
            result = qr_login.poll_scan_status(
                qrid, session, timeout=float(wait_seconds), on_status=_safe_on_status
            )
        except TimeoutError as exc:
            # 这是"手机还没扫"，属于正常状态 —— 用 TIMEOUT 标识让上层能区分
            raise ThsApiError(
                f"等待扫码超时，请重新生成二维码（{exc}）",
                code=ThsApiError.TIMEOUT,
            ) from exc
        except Exception as exc:
            # 二维码本身过期（thspypc 在 status=1 且 expired=0 时抛 RuntimeError）
            msg = str(exc)
            code = ThsApiError.QR_EXPIRED if "过期" in msg else ThsApiError.FAILED
            raise ThsApiError(f"轮询扫码状态失败：{exc}", code=code) from exc

        return {
            "account": result.account,
            "password": result.password,
            "qrid": result.qrid,
            "expire_time": result.expire_time,
        }

    # ---------- 内部：登录并拿 cookie ----------

    def _login(self, account: str, password: str) -> dict:
        """账号密码 → HTTP cookies。三步鉴权 + docookie2 都在这。"""
        if not account or not password:
            raise ThsApiError("缺少账号或密码")
        auth = http_auth(account, password)
        return get_http_cookies(auth)

    # ---------- 自选股 ----------

    @staticmethod
    def _to_dict(item: dict) -> dict:
        """把 parse_selfstock 的结果补上价格/时间，转成对外格式。"""
        return {
            "code": item["code"],
            "market": item["market"],
            "price": item.get("price"),
            "addedAt": item.get("addedAt"),
        }

    def _fetch_detail(self, cookies: dict, userid: str) -> dict:
        """拉「加入价/加入时间」明细，返回 {(code, market_type): {...}}。

        接口：ugc.10jqka.com.cn/selfstock_detail
        返回 XML，其中 selfstock_detail 属性是 base64 编码的 JSON，
        每项字段 C=代码 M=市场码 P=价格 T=时间。

        ⚠️ 索引 key 用**原始市场码**，不用 market_abbr() 的结果。
           因为 thspypc 的 market_abbr 表里缺 16/32，会导致指数查不到明细。
        这是尽力而为：拿不到不影响主流程。
        """
        try:
            resp = requests.get(
                SELFSTOCK_DETAIL_URL,
                params={"reqtype": "download", "app_flag": "0E", "userid": userid},
                headers={"User-Agent": _PHONE_UA, "userid": userid},
                cookies=cookies,
                timeout=15,
            )
            if resp.status_code != 200:
                return {}

            # 复用 thspypc 的 XML + base64 + JSON 解析
            from thspypc.blocks import _parse_selfstock_detail_response

            _version, detail_list = _parse_selfstock_detail_response(resp.text)
        except Exception:
            return {}

        index = {}
        for entry in detail_list:
            code = entry.get("C", "")
            mtype = str(entry.get("M", ""))
            if not code:
                continue
            price = _safe_float(entry.get("P"))
            index[(code, mtype)] = {"price": price, "addedAt": entry.get("T")}
        return index

    def get_self_stocks(self, account: str, password: str) -> list:
        """读取「我的自选」。

        返回 [{"code": "688023", "market": "SH", "price": 123.45, "addedAt": "20240101"}, ...]

        ⚠️ price 是**加入自选时的参考价**，不是真实买入成本。
           所以第 5 步同步入库时**不要写进 buy_price**，否则会污染盈亏计算。
        """
        cookies = self._login(account, password)
        userid = cookies.get("userid", "")

        # ---- 取代码列表 ----
        try:
            resp = requests.get(
                f"{UGC_BASE}{SELFSTOCK_QUERY_PATH}",
                headers={"User-Agent": _PHONE_UA, "userid": userid} if userid
                        else {"User-Agent": _PHONE_UA},
                params={"support_all": "0", "from": _SELFSTOCK_FROM},
                cookies=cookies,
                timeout=15,
            )
            payload = resp.json()
        except Exception as exc:
            raise ThsApiError(f"读取自选股失败：{exc}") from exc

        if payload.get("status_code") != 0:
            raise ThsApiError(
                f"读取自选股被拒绝（{payload.get('status_msg') or payload}）"
            )

        raw = (payload.get("data") or {}).get("selfstock", "")
        stocks = parse_selfstock(raw)

        # ---- 补加入价/加入时间（尽力而为）----
        if stocks:
            detail = self._fetch_detail(cookies, userid)
            for st in stocks:
                meta = detail.get((st["code"], str(st["market_type"]))) or {}
                st["price"] = meta.get("price")
                st["addedAt"] = meta.get("addedAt")

        return [self._to_dict(s) for s in stocks]


# ==================== 7. 本地自测 ====================
#
# 直接运行本文件 = 跑一遍扫码 → 读自选，不依赖 FastAPI。
#     .\.venv\Scripts\python.exe ths_client.py
#
if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    client = ThsClient()
    print("=" * 60)
    print("ths_client 自测：扫码 → 鉴权 → 读自选股")
    print("=" * 60)
    print(f"设备指纹 imei  = {generate_imei()}")
    print(f"设备指纹 mac64 = {generate_mac64()}")

    qr = client.create_qr()
    print(f"\nqrid   = {qr['qrid']}")
    print(f"二维码 = {qr['qr_url']}")
    print("\n" + qr_login.render_qr_ascii(qr["qr_url"]))
    print("\n请用手机「同花顺 App」扫码并确认 ...")

    def show_status(data):
        label = {1: "还没扫码", 2: "已扫码待确认", 3: "已确认"}.get(
            data.get("status"), f"状态={data.get('status')}"
        )
        print(f"    {label}")

    cred = client.poll_qr(qr["qrid"], wait_seconds=120, on_status=show_status)
    print(f"\n✓ 扫码成功 account={cred['account']}")

    print("\n[自选股] 读取中（三步鉴权 + 换 cookie）...")
    stocks = client.get_self_stocks(cred["account"], cred["password"])
    print(f"\n共 {len(stocks)} 只：")
    for s in stocks:
        line = f"  {s['market']}{s['code']}"
        if s.get("price") is not None:
            line += f"   加入价={s['price']}"
        if s.get("addedAt"):
            line += f"   加入时间={s['addedAt']}"
        print(line)

