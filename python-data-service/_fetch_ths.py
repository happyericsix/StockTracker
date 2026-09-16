"""Download the thspypc source tree into vendor/thspypc (one-off helper)."""
import io
import os
import sys
import tarfile
import urllib.request

DEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor"
)
URL = "https://codeload.github.com/djj45/thspypc/tar.gz/refs/heads/main"

os.makedirs(DEST, exist_ok=True)
print("downloading", URL)
raw = urllib.request.urlopen(URL, timeout=120).read()
print("got", len(raw), "bytes")

with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
    names = tf.getnames()
    print("archive members:", len(names))
    # strip the leading "<repo>-<ref>/" component
    for member in tf.getmembers():
        parts = member.name.split("/", 1)
        if len(parts) < 2 or not parts[1]:
            continue
        member.name = parts[1]
        target = os.path.join(DEST, "thspypc_src", member.name)
        if member.isdir():
            os.makedirs(target, exist_ok=True)
        elif member.isfile():
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(tf.extractfile(member).read())

print("extracted to", os.path.join(DEST, "thspypc_src"))
for root, dirs, files in os.walk(os.path.join(DEST, "thspypc_src")):
    dirs[:] = [d for d in dirs if d not in (".git",)]
    depth = root.count(os.sep) - os.path.join(DEST, "thspypc_src").count(os.sep)
    if depth <= 2:
        print("  " * depth + os.path.basename(root) + "/")
        for f in sorted(files)[:12]:
            print("  " * (depth + 1) + f)
sys.exit(0)
