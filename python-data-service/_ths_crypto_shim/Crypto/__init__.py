"""
兼容层：用 cryptography 实现 PyCryptodome 的最小 RSA 接口子集。

背景
----
djj45/thspypc 的 `protocol.py::rsa_encrypt()` 里写的是：

    from Crypto.PublicKey import RSA
    from Crypto.Cipher import PKCS1_v1_5
    key = RSA.import_key(pubkey_pem)
    cipher = PKCS1_v1_5.new(key)
    encrypted = cipher.encrypt(plaintext.encode("gbk"))

即只用到 PyCryptodome 的三个入口：`RSA.import_key`、`PKCS1_v1_5.new`、`cipher.encrypt`。

本机 .venv 已装 cryptography 48.0.1，但没有 pycryptodome 且 pip 装不上
（沙箱 TLS 拦截导致 PyPI 不可达）。这个包提供上面的最小子集，
让 thspypc 不改一行源码即可运行：

    import sys
    sys.path.insert(0, "<this dir>")   # 让 `import Crypto` 命中本 shim
    import thspypc                     # 之后 thspypc 正常工作

⚠️ 仅当真的没有 pycryptodome 时才应该走这里 —— 有官方库就用官方库。
如果之后 pip 能用了，直接 `pip install pycryptodome` 并把这个目录从
sys.path 里拿掉即可，本 shim 会被自动遮蔽。
"""
