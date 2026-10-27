"""录制-回放代理：挡在 Java 后端与真实 python-data 之间。

为什么需要它：压测行情类接口时，缓存未命中的请求会被 Java 转发到
python-data，再透传到 akshare/THS 这类免费上游——压测流量打上游，
轻则延迟数据失真，重则出口 IP 被上游封禁。本代理对每个
"路径+查询串" 只放行第一次请求到真实上游，响应落盘后无限回放，
上游从此只见涓涓细流；鉴权头（X-Internal-Token 等）原样转发，
所以 Java 侧无需任何代码改动，只是把 AKSHARE_API_BASE_URL 指过来。

用法（Python 3.8+，零第三方依赖）：
    python record_replay_proxy.py                  # 监听 :8001，上游 :8000
    set PORT=8002 && set UPSTREAM=http://localhost:8000 && python record_replay_proxy.py

想重录响应时：删除本文件旁边的 .cache 目录即可。

注意：不要用 curl 不带鉴权头去"预热"缓存——会录下 401 并污染该 URL。
预热交给 k6 基线脚本（5 并发，Java 会带正确的内部 token 来录）。
"""
import hashlib
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("UPSTREAM", "http://localhost:8000").rstrip("/")
PORT = int(os.environ.get("PORT", "8001"))
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)

FORWARD_HEADERS = ("Authorization", "X-Internal-Token", "Accept", "Accept-Language",
                   "Content-Type")

_locks_guard = threading.Lock()
_locks = {}  # 每个 URL 一把锁：同一 URL 的并发首请求只放一个去上游


def _key_lock(key):
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        key = hashlib.sha1(self.path.encode("utf-8")).hexdigest()
        meta_path = os.path.join(CACHE_DIR, key + ".meta")
        body_path = os.path.join(CACHE_DIR, key + ".body")
        with _key_lock(key):
            if os.path.exists(meta_path):
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
                with open(body_path, "rb") as f:
                    body = f.read()
                self._respond(meta["status"], meta["content_type"], body, cached=True)
                return
            try:
                req = urllib.request.Request(UPSTREAM + self.path, method="GET")
                for h in FORWARD_HEADERS:
                    if self.headers.get(h):
                        req.add_header(h, self.headers[h])
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = resp.read()
                    meta = {"status": resp.status,
                            "content_type": resp.headers.get("Content-Type",
                                                             "application/json")}
            except urllib.error.HTTPError as e:
                body = e.read()
                meta = {"status": e.code,
                        "content_type": e.headers.get("Content-Type",
                                                      "application/json")}
            except Exception as e:  # 上游不可达：明确 502，不静默吞掉
                self._respond(502, "application/json",
                              json.dumps({"error": "upstream %s unreachable: %s"
                                          % (UPSTREAM, e)}).encode())
                return
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f)
            with open(body_path, "wb") as f:
                f.write(body)
            print("[recorded] GET %s -> %s" % (self.path, meta["status"]), flush=True)
            self._respond(meta["status"], meta["content_type"], body, cached=False)

    def do_POST(self):
        # 写操作/实时计算不缓存，直接透传（本套压测脚本不会打到 POST）
        length = int(self.headers.get("Content-Length") or 0)
        payload = self.rfile.read(length) if length else None
        try:
            req = urllib.request.Request(UPSTREAM + self.path, data=payload,
                                         method="POST")
            for h in FORWARD_HEADERS:
                if self.headers.get(h):
                    req.add_header(h, self.headers[h])
            with urllib.request.urlopen(req, timeout=30) as resp:
                self._respond(resp.status,
                              resp.headers.get("Content-Type", "application/json"),
                              resp.read())
        except urllib.error.HTTPError as e:
            self._respond(e.code, e.headers.get("Content-Type", "application/json"),
                          e.read())
        except Exception as e:
            self._respond(502, "application/json",
                          json.dumps({"error": str(e)}).encode())

    def _respond(self, status, content_type, body, cached=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cached:
            self.send_header("X-Cache", "HIT")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # 回放路径每秒几百条日志毫无信息量，静默；录制时上面有显式打印
        pass


if __name__ == "__main__":
    print("record-replay proxy  :%d  ->  upstream %s" % (PORT, UPSTREAM))
    print("cache dir: %s" % CACHE_DIR)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
