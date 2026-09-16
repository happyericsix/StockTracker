# -*- coding: utf-8 -*-
"""
ths_probe_simple.py — 用你自己那套 requests 写法做一遍（教学用）

这个文件存在的唯一目的：让你看清楚 **新代码和你的老代码其实是同一件事**。

thspypc 里面写的就是这样的代码，只是它被包成了函数。
本文件把那一层拆开，让你能一行一行对着你原来那份 ths_probe.py 看
（你原来那份已删除，现在对照 ths_probe_v2.py —— 它是照你原代码风格改的版本）。

对照表（左边是你的老写法，右边是本文件）：
    s = requests.session()              -> 第 39 行  mk_session()
    s.get(creatCode, headers=HEADERS)   -> 第 52 行  create_qr()
    s.post(getInfoNew, data={"qrid":})  -> 第 75 行  poll_qr()
    print(resp.text)                    -> 第 60/85 行 print(...)
    while + time.sleep(4)               -> 第 78-95 行 那个 while 循环

用法：
    .\\.venv\\Scripts\\python.exe ths_probe_simple.py
"""
import sys
import time

import requests

# Windows 控制台默认 GBK，打印二维码方块字符会报错，先改成 utf-8
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# ============================================================================
# 这一段和你的老代码一模一样 —— Session 就是 requests 的「浏览器标签页」
# ============================================================================
def mk_session():
    """建一个 Session。它会自动记住服务器发回来的 cookie。"""
    s = requests.Session()

    # 老代码只写了 User-Agent，这里补上另外 3 个头。
    # 为什么要补：同花顺会检查你是不是从它自己的登录页发来的请求。
    #   X-Requested-With  告诉服务器"我是网页里的 Ajax 请求"
    #   Referer           告诉服务器"我是从 /login 这个页面点过来的"
    #   Origin            同上，跨域请求才要
    # 抓包实测：少这几个头会被当成非法请求。
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://upass.10jqka.com.cn/login",
        "Origin": "https://upass.10jqka.com.cn",
    })
    return s


# ============================================================================
# 第一步：拿二维码  ← 你老代码里 createcode = s.get(...) 那两行
# ============================================================================
def create_qr(s):
    """返回 (qrid, 二维码地址)。"""
    # 注意 s.get 而不是 requests.get —— 用 Session 才会带上它记的 cookie
    resp = s.get("https://upass.10jqka.com.cn/scan/creatCode", timeout=15)

    # 先看原始返回。这一步很关键：不要猜字段名，打印出来看。
    print("creatCode 返回:", resp.text)

    data = resp.json()          # 把返回的 JSON 文本变成 Python 字典

    # 服务器说 errorCode != 0 就是失败了
    if data.get("errorCode") != 0:
        raise RuntimeError(f"取二维码失败: {data}")

    qrid = data["qrid"]         # 从字典里取出 qrid
    qr_url = "http://mobile.10jqka.com.cn/?source=PC&qrid=" + qrid
    return qrid, qr_url


# ============================================================================
# 第二步：轮询等扫码  ← 你老代码里那个 while True 循环
# ============================================================================
def poll_qr(s, qrid, wait_seconds=120):
    """每 4 秒问一次"扫了没"，扫好了返回账号密码。"""
    url = "https://upass.10jqka.com.cn/scan/getInfoNew"

    # ★★★ 这里是你老代码最大的问题 ★★★
    #
    # 你原来写的是：   data={"qrid": qrid}
    # 但服务器要的是这 5 个字段，少一个它就返回垃圾值：
    #     {"status": 0, "account": "1", "password": null, ...}
    # status=0 既不是"未扫码(1)"也不是"已确认(3)"，
    # 所以你那个 `if status == 3` 永远不成立 → 白等 120 秒。
    #
    # 我实测过：只发 qrid 得到 status=0；发全 5 个字段得到 status=1（正常的"等待扫码"）。
    body = {
        "qrid": qrid,
        "state": "1",
        "source": "pc_web",
        "page_source": "web_screen",
        "request_type": "login",
    }

    deadline = time.time() + wait_seconds  # 什么时候放弃

    while time.time() < deadline:
        resp = s.post(url, data=body, timeout=15)   # 还是 s.post，不是 requests.post
        data = resp.json()

        status = data.get("status")
        left = int(deadline - time.time())
        print(f"  原始返回: {data}   (剩余 {left}s)")

        # status 的含义（打印出来看多了就记住了）：
        #   1 = 还没扫
        #   2 = 扫了，手机还没点确认
        #   3 = 确认了，account/password 就在返回里
        if status == 3:
            return {
                "account": data["account"],
                "password": data["password"],
            }

        # status=1 且 expired=0 → 二维码本身过期了，别等了
        if status == 1 and data.get("expired") == 0:
            raise RuntimeError("二维码已过期，请重新运行")

        time.sleep(4)   # 你老代码里的 4 秒

    raise TimeoutError(f"{wait_seconds} 秒内没扫")


# ============================================================================
# 主流程：和你老代码的顺序完全一致
# ============================================================================
def main():
    # 老代码： s = requests.session()
    s = mk_session()

    # 老代码： createcode = s.get(creatCode, headers=HEADERS); qrid = createcode.json()["qrid"]
    qrid, qr_url = create_qr(s)
    print("qrid   =", qrid)
    print("二维码 =", qr_url)

    # 终端直接画二维码（画不出来就手动把上面那行 URL 贴到在线生成器）
    try:
        import ths_client  # noqa: F401  （只是为了让 vendor 的 qrcode 能被找到）
        from thspypc import qr_login

        print("\n" + qr_login.render_qr_ascii(qr_url))
    except Exception as e:
        print(f"（终端画二维码失败: {e}）")

    print("\n请用手机同花顺 App 扫码确认 ...")

    # 老代码： 那个 while True + time.sleep(4) + if status == 3
    cred = poll_qr(s, qrid, wait_seconds=120)

    print("\n✓ 扫到了！")
    print("  account  =", cred["account"])
    print("  password =", cred["password"][:8] + "..." + "（32位，打印一半就够）")

    print("\n【下一步的说明】")
    print("  到这里为止，你拿到的是「账号+密码」，还不是登录态。")
    print("  要用它去 auth.10jqka.com.cn 走三步鉴权（RSA公钥→登录→主验证），")
    print("  才能换到 passport 票据。那三步的代码在 thspypc 的 protocol.py 里，")
    print("  ths_client.py 已经帮你调好了，不用你自己写。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
