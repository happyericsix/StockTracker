"""`Crypto.Cipher.PKCS1_v1_5` 的最小兼容实现 —— **纯标准库，零第三方依赖**。

对应 PyCryptodome 的用法::

    cipher = PKCS1_v1_5.new(key)       # key 来自 Crypto.PublicKey.RSA.import_key
    encrypted = cipher.encrypt(plain)  # bytes -> bytes

PyCryptodome 的 PKCS1_v1_5 同时支持加密和解密（按 key 类型自动判断），
这里只需要加密路径 —— 加密逻辑就在 Crypto/PublicKey/RSA.py 里。
"""
__all__ = ["PKCS1_v1_5", "new"]


class PKCS1_v1_5:
    """PKCS#1 v1.5 加密器。"""

    def __init__(self, key):
        # key 可能是我们自己的 RsaKey，也可能是别的东西；只要能 encrypt 就行
        self._key = getattr(key, "public_key", key)
        if not hasattr(self._key, "encrypt"):
            raise TypeError(
                f"PKCS1_v1_5.new() 需要 RSA 公钥对象，收到 {type(key).__name__}"
            )

    def encrypt(self, plaintext: bytes) -> bytes:
        """RSA PKCS#1 v1.5 加密。"""
        if isinstance(plaintext, str):
            plaintext = plaintext.encode()
        return self._key.encrypt(plaintext)


def new(key) -> PKCS1_v1_5:
    """PyCryptodome 风格的构造入口。"""
    return PKCS1_v1_5(key)
