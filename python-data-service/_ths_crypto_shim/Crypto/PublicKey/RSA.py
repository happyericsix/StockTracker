"""`Crypto.PublicKey.RSA` 的最小兼容实现 —— **纯标准库，零第三方依赖**。

为什么不用 cryptography / pycryptodome：
    本机 pip 装不上包（沙箱 TLS 拦截导致 PyPI 不可达），
    而 thspypc 的 rsa_encrypt() 写死了 `from Crypto.PublicKey import RSA`。
    依赖任何一个"可能需要安装"的库，都会在别人的环境里炸掉
    （实测就出现过 `No module named 'cryptography'`）。

    所以这里用 Python 自带的 base64 + 大整数运算实现 RSA PKCS#1 v1.5 加密。
    不需要装任何东西，到哪儿都能跑。

只实现 thspypc 用到的那一个入口：``RSA.import_key(pem)``。
"""
import base64
import os
import re

__all__ = ["RsaKey", "import_key"]

# ---------------------------------------------------------------- ASN.1 DER
#
# PEM 里的公钥是一段 DER 编码的 ASN.1。RSA 公钥的结构是：
#
#   SubjectPublicKeyInfo ::= SEQUENCE {
#       algorithm  SEQUENCE { OID, NULL }      -- 固定 rsaEncryption
#       subjectPublicKey  BIT STRING {         -- 里面还套一层
#           RSAPublicKey ::= SEQUENCE { modulus INTEGER, exponent INTEGER }
#       }
#   }
#
# 所以"手写解析"就是按这个结构一层层剥开。
# 这里不做通用 ASN.1 解析（那要写很多代码），只按固定结构取值。


def _read_tlv(data: bytes, pos: int):
    """读一个 TLV（Type-Length-Value），返回 (tag, 内容, 下一个位置)。"""
    tag = data[pos]
    pos += 1
    length = data[pos]
    pos += 1
    if length & 0x80:
        # 长格式：低 7 位表示后面用几个字节存长度
        num_bytes = length & 0x7F
        length = int.from_bytes(data[pos:pos + num_bytes], "big")
        pos += num_bytes
    value = data[pos:pos + length]
    return tag, value, pos + length


def import_key(extern_key, passphrase=None):
    """从 PEM 公钥里取出 (n, e)。

    Args:
        extern_key: PEM 字符串（str）或 DER 字节
        passphrase: 用不到，仅为接口兼容保留

    Returns:
        RsaKey 对象，带 .n（模数）和 .e（指数）
    """
    if isinstance(extern_key, str):
        data = extern_key.encode()

    # 去掉 PEM 的 -----BEGIN/END----- 外壳和换行，得到纯 base64
    body = re.sub(rb"-----[^-]+-----", b"", data)
    der = base64.b64decode(re.sub(rb"\s", b"", body))

    # ---- 第 1 层：最外层 SEQUENCE ----
    tag, outer, _ = _read_tlv(der, 0)
    if tag != 0x30:
        raise ValueError(f"不是合法的 DER SEQUENCE（tag=0x{tag:02x}）")

    # ---- 第 2 层：跳过算法标识 SEQUENCE ----
    tag, _algorithm, pos = _read_tlv(outer, 0)
    if tag != 0x30:
        raise ValueError("算法标识不是 SEQUENCE")

    # ---- 第 3 层：BIT STRING（内容是 RSAPublicKey）----
    tag, bit_string, _ = _read_tlv(outer, pos)
    if tag != 0x03:
        raise ValueError("公钥不是 BIT STRING")
    # BIT STRING 第 1 个字节是"未使用位数"，标准值是 0，要跳过
    if bit_string[0] != 0:
        raise ValueError("BIT STRING 有未使用位，非标准 RSA 公钥")
    inner = bit_string[1:]

    # ---- 第 4 层：RSAPublicKey SEQUENCE ----
    tag, rsa_seq, _ = _read_tlv(inner, 0)
    if tag != 0x30:
        raise ValueError("RSAPublicKey 不是 SEQUENCE")

    # ---- 第 5 层：modulus 和 exponent 两个 INTEGER ----
    tag, n_bytes, pos = _read_tlv(rsa_seq, 0)
    if tag != 0x02:
        raise ValueError("modulus 不是 INTEGER")
    tag, e_bytes, _ = _read_tlv(rsa_seq, pos)
    if tag != 0x02:
        raise ValueError("exponent 不是 INTEGER")

    # INTEGER 是"有符号大端"：最高位为 1 时编码会补一个 0x00，要去掉
    if n_bytes[0] == 0:
        n_bytes = n_bytes[1:]
    if e_bytes[0] == 0:
        e_bytes = e_bytes[1:]

    n = int.from_bytes(n_bytes, "big")
    e = int.from_bytes(e_bytes, "big")
    return RsaKey(n, e)


# ---------------------------------------------------------------- RSA
class RsaKey:
    """RSA 公钥。只存 n（模数）和 e（指数），够做加密了。"""

    def __init__(self, n: int, e: int):
        self.n = n
        self.e = e

    @property
    def public_key(self):
        """兼容 cryptography 的写法（thspypc 没用到，留一层不亏）。"""
        return self

    @property
    def key_size(self) -> int:
        """模数位数，如 1024。"""
        return self.n.bit_length()

    def size_in_bytes(self) -> int:
        return (self.key_size + 7) // 8

    def encrypt(self, message: bytes) -> bytes:
        """RSA PKCS#1 v1.5 加密。

        打包格式（RFC 8017 的 EB）：
            0x00 || 0x02 || PS || 0x00 || M
        PS 是非零随机填充，长度让整块正好等于密钥字节数 k。
        """
        if isinstance(message, str):
            message = message.encode()

        k = self.size_in_bytes()
        ps_len = k - len(message) - 3
        if ps_len < 8:
            raise ValueError(f"明文太长：{len(message)} 字节，该密钥最多 {k - 11} 字节")

        # 随机填充，必须全部非零
        ps = bytearray()
        while len(ps) < ps_len:
            ps.extend(b for b in os.urandom(ps_len - len(ps)) if b != 0)
        ps = bytes(ps[:ps_len])

        eb = b"\x00\x02" + ps + b"\x00" + message

        # 核心就这一行：大整数模幂。Python 原生支持，不需要任何第三方库。
        m = int.from_bytes(eb, "big")
        c = pow(m, self.e, self.n)
        return c.to_bytes(k, "big")
