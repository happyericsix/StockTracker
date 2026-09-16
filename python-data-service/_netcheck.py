import urllib.request

URLS = [
    "https://pypi.org/simple/",
    "https://pypi.tuna.tsinghua.edu.cn/simple/",
    "https://codeload.github.com/djj45/thspypc/tar.gz/refs/heads/main",
    "https://api.github.com/repos/djj45/thspypc",
]
for u in URLS:
    try:
        r = urllib.request.urlopen(u, timeout=20)
        cl = r.headers.get("Content-Length")
        print(u, "-> HTTP", r.status, "len=", cl)
    except Exception as e:
        print(u, "-> FAIL", type(e).__name__, str(e)[:100])
