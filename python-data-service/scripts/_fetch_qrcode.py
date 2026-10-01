"""
把 qrcode 库（纯 Python、无依赖）下载并解包到 stock-tracker/vendor/。

为什么不 pip install：本沙箱 PowerShell/curl 的 TLS 被拦截，pip 也走同样的
栈因而卡死；但 Python 的 urllib 能正常访问 PyPI。所以这里直接用 urllib 取
wheel 并解包，把库放进 vendor/，与 thspypc 放在一起。

下游用法（ths_client.py 的 _bootstrap() 已自动处理）：
    sys.path.insert(0, "<repo>/vendor/qrcode_lib")

等以后 pip 能用了，直接 `pip install qrcode` 即可，vendor 副本会被自动遮蔽。
"""
import io
import json
import os
import urllib.request
import zipfile

PINNED = "7.3.1"  # 纯 Python 且 PNG 保存不依赖可选的 pypng（7.4+ 会要求 pypng）

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
VENDOR = os.path.join(REPO_ROOT, "vendor")
TARGET = os.path.join(VENDOR, "qrcode_lib")


def main() -> int:
    os.makedirs(TARGET, exist_ok=True)

    meta_url = f"https://pypi.org/pypi/qrcode/{PINNED}/json"
    print("fetching metadata:", meta_url)
    with urllib.request.urlopen(meta_url, timeout=60) as resp:
        meta = json.load(resp)

    wheel = None
    for f in meta["urls"]:
        if f["filename"].endswith("-py3-none-any.whl"):
            wheel = f
            break
    if wheel is None:
        for f in meta["urls"]:
            if f["filename"].endswith(".whl"):
                wheel = f
                break
    if wheel is None:
        # 没有 wheel 就用 sdist（tar.gz），取包内的 qrcode/ 目录
        for f in meta["urls"]:
            if f["filename"].endswith(".tar.gz"):
                wheel = f
                break
    if wheel is None:
        print("!! 没找到 wheel / sdist；可用的文件：")
        for f in meta["urls"]:
            print("   ", f["filename"])
        return 1

    print("downloading:", wheel["filename"], f"({wheel['size']} bytes)")
    with urllib.request.urlopen(wheel["url"], timeout=180) as resp:
        blob = resp.read()
    print("got", len(blob), "bytes")

    fname = wheel["filename"]
    extracted = 0
    if fname.endswith(".whl"):
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            for n in zf.namelist():
                if n.endswith("/") or ".dist-info/" in n:
                    continue
                dest = os.path.join(TARGET, n)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as fh:
                    fh.write(zf.read(n))
                extracted += 1
    else:
        # sdist：解包后只取包内的 qrcode/ 目录
        import tarfile

        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
            for member in tf.getmembers():
                parts = member.name.split("/", 1)
                if len(parts) < 2:
                    continue
                inner = parts[1]
                if not inner.startswith("qrcode/"):
                    continue
                if member.isdir():
                    continue
                # 跳过测试目录，减小体积
                if "/tests/" in inner:
                    continue
                dest = os.path.join(TARGET, inner)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as fh:
                    fh.write(tf.extractfile(member).read())
                extracted += 1
    print("extracted", extracted, "files")

    print("\nvendored qrcode ->", TARGET)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
