"""SSE 长连接保持测试：同时挂住 N 条 /api/v1/stocks/stream，测服务端承载能力。

为什么不用 k6：k6 不支持 SSE 长连接。而 SSE 的并发瓶颈是
"同时挂多少条连接"（每条占一个 Tomcat 线程 + 一份内存），与 QPS 是
两种并发模型——1.8G 内存的部署里，长连接数很可能先于 QPS 到顶。

用法（Java 已启动，且至少跑过一次 baseline.js 让 loadtest_0 账号存在）：
    python sse_hold.py -n 100 -t 300           # 100 条连接挂 5 分钟
    python sse_hold.py -n 200 -t 600           # 200 条挂 10 分钟
    python sse_hold.py -n 100 -t 300 --username loadtest_1

每 10 秒打印：存活连接数 / 累计事件数 / 连接失败 / 重连次数。
结束时打印 SUMMARY。判定：alive 稳定在 N 附近、connect_fail=0；
若服务端主动掐连接（reconnects 持续增长），去查 Java 日志。
"""
import argparse
import http.client
import json
import threading
import time
import urllib.parse
import urllib.request

stats_lock = threading.Lock()
stats = {"alive": 0, "events": 0, "connect_fail": 0, "reconnects": 0}


def login(base, username, password):
    req = urllib.request.Request(
        base + "/api/v1/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        token = json.loads(resp.read()).get("token")
    if not token:
        raise SystemExit("登录未返回 token：先跑一次 k6 baseline.js 创建测试账号")
    return token


class Holder(threading.Thread):
    def __init__(self, url, token, deadline):
        super().__init__(daemon=True)
        self.url = url
        self.token = token
        self.deadline = deadline

    def run(self):
        u = urllib.parse.urlsplit(self.url)
        path = u.path + ("?" + u.query if u.query else "")
        while time.time() < self.deadline:
            connected = False
            conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=30)
            try:
                conn.request("GET", path, headers={
                    "Authorization": "Bearer " + self.token,
                    "Accept": "text/event-stream",
                })
                resp = conn.getresponse()
                if resp.status != 200:
                    with stats_lock:
                        stats["connect_fail"] += 1
                    time.sleep(1)
                    continue
                connected = True
                with stats_lock:
                    stats["alive"] += 1
                while time.time() < self.deadline:
                    line = resp.readline()
                    if not line:
                        break  # 服务端断开
                    if line.startswith(b"data:"):
                        with stats_lock:
                            stats["events"] += 1
            except Exception:
                pass  # 30s 读超时/对端重置：都落到重连逻辑
            finally:
                if connected:
                    with stats_lock:
                        stats["alive"] -= 1
                try:
                    conn.close()
                except Exception:
                    pass
            if time.time() < self.deadline:
                with stats_lock:
                    stats["reconnects"] += 1
                time.sleep(1)


def main():
    ap = argparse.ArgumentParser(description="SSE 长连接保持测试")
    ap.add_argument("-n", type=int, default=100, help="同时挂的连接数（默认 100）")
    ap.add_argument("-t", type=int, default=300, help="持续秒数（默认 300）")
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--path", default="/api/v1/stocks/stream")
    ap.add_argument("--username", default="loadtest_0")
    ap.add_argument("--password", default="LoadTest#2026")
    args = ap.parse_args()

    token = login(args.base, args.username, args.password)
    deadline = time.time() + args.t
    print("SSE hold: %d 条连接，持续 %ds，目标 %s%s" % (args.n, args.t, args.base, args.path))
    print("Ctrl+C 可提前结束\n")

    for _ in range(args.n):
        Holder(args.base + args.path, token, deadline).start()
        time.sleep(0.02)  # 20ms/条错峰建连，避免建连本身形成冲击

    start = time.time()
    try:
        while time.time() < deadline:
            time.sleep(10)
            with stats_lock:
                s = dict(stats)
            print("[%4ds] alive=%-5d events=%-7d connect_fail=%-4d reconnects=%d"
                  % (int(time.time() - start), s["alive"], s["events"],
                     s["connect_fail"], s["reconnects"]), flush=True)
    except KeyboardInterrupt:
        print("\n提前结束")
    time.sleep(2)
    with stats_lock:
        print("SUMMARY:", dict(stats))


if __name__ == "__main__":
    main()
