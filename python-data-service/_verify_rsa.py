# -*- coding: utf-8 -*-
"""
_verify_rsa.py — 验证纯 Python RSA 实现正确性（一次性自检脚本）

只靠"不报错"不能证明加密是对的，必须做一次真正的往返：
    用自己生成的密钥对加密 → 用私钥解密 → 看原文是否一致。

这个文件是独立自检，不参与业务流程，验证完可以删。
"""
import base64
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ths_crypto_shim"))

from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# ------------------------------------------------------------------ 生成密钥对
def gen_rsa_keypair(bits=1024, e=65537):
    """生成 RSA 密钥对（教学用，不追求素性检验的性能）。"""
    def is_probable_prime(n, k=20):
        if n < 2:
            return False
        for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
            if n % p == 0:
                return n == p
        d, r = n - 1, 0
        while d % 2 == 0:
            d //= 2
            r += 1
        for _ in range(k):
            a = int.from_bytes(os.urandom(8), "big") % (n - 3) + 2
            x = pow(a, d, n)
            if x in (1, n - 1):
                continue
            for _ in range(r - 1):
                x = pow(x, 2, n)
                if x == n - 1:
                    break
            else:
                return False
        return True

    def gen_prime(nbits):
        while True:
            cand = int.from_bytes(os.urandom(nbits // 8), "big")
            cand |= (1 << (nbits - 1)) | 1     # 置最高位和最低位
            if is_probable_prime(cand):
                return cand

    while True:
        p = gen_prime(bits // 2)
        q = gen_prime(bits // 2)
        if p == q:
            continue
        n = p * q
        if n.bit_length() != bits:
            continue
        phi = (p - 1) * (q - 1)
        if phi % e == 0:
            continue
        d = pow(e, -1, phi)      # Python 3.8+ 内置模逆
        return n, e, d


def der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def der_int(v: int) -> bytes:
    b = v.to_bytes((v.bit_length() + 7) // 8 or 1, "big")
    if b[0] & 0x80:
        b = b"\x00" + b
    return b"\x02" + der_len(len(b)) + b


def to_pem_public_key(n: int, e: int) -> str:
    """把 (n, e) 编成 SubjectPublicKeyInfo PEM（结构对就行，测试用）。"""
    rsa_pub = der_int(n) + der_int(e)
    rsa_seq = b"\x30" + der_len(len(rsa_pub)) + rsa_pub
    bit_string = b"\x03" + der_len(len(rsa_seq) + 1) + b"\x00" + rsa_seq
    # rsaEncryption OID 1.2.840.113549.1.1.1 + NULL
    oid = bytes.fromhex("06092a864886f70d010101") + b"\x05\x00"
    alg = b"\x30" + der_len(len(oid)) + oid
    spki = b"\x30" + der_len(len(alg) + len(bit_string)) + alg + bit_string
    b64 = base64.b64encode(spki).decode()
    lines = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    return "-----BEGIN PUBLIC KEY-----\n" + "\n".join(lines) + "\n-----END PUBLIC KEY-----\n"


def rsa_decrypt_pkcs1v15(c: int, d: int, n: int) -> bytes:
    """用私钥解密并剥掉 PKCS#1 v1.5 填充。"""
    k = (n.bit_length() + 7) // 8
    m = pow(c, d, n)
    eb = m.to_bytes(k, "big")
    assert eb[0] == 0x00 and eb[1] == 0x02, "填充头不对"
    sep = eb.index(b"\x00", 2)          # 找分隔符
    return eb[sep + 1:]


def main() -> int:
    print("=" * 58)
    print("纯 Python RSA 自检（往返验证）")
    print("=" * 58)

    print("\n[1] 生成一对 1024 位测试密钥 ...")
    n, e, d = gen_rsa_keypair(1024)
    print(f"    n 位数 = {n.bit_length()}   e = {e}")

    pem = to_pem_public_key(n, e)

    print("\n[2] 用我们的 shim 解析这个 PEM ...")
    key = RSA.import_key(pem)
    print(f"   解析出 n 位数 = {key.key_size}   e = {key.e}")
    assert key.n == n, "解析出的模数不对！"
    assert key.e == e, "解析出的指数不对！"
    print("   ✓ 解析结果与原始密钥一致")

    print("\n[3] 公钥加密 ...")
    cipher = PKCS1_v1_5.new(key)

    for plaintext in ["13800000000", "mx_7mi3abcd", "a" * 117]:   # 117 = 1024/8 - 11
        blob = cipher.encrypt(plaintext.encode("gbk"))
        assert len(blob) == 128, f"密文长度应为 128，实际 {len(blob)}"
        b64 = base64.b64encode(blob).decode()
        assert len(b64) == 172, f"base64 后应为 172 字符，实际 {len(b64)}"

    print("   ✓ 明文 -> 128 字节密文 -> 172 字符 base64（与真实接口一致）")

    print("\n[4] 用私钥解密，看能不能还原（这一步才证明加密是对的）...")
    for plaintext in ["13800000000", "mx_7mi3abcd", "a" * 117]:
        blob = cipher.encrypt(plaintext.encode("gbk"))
        c = int.from_bytes(blob, "big")
        recovered = rsa_decrypt_pkcs1v15(c, d, n).decode("gbk")
        status = "✓" if recovered == plaintext else "✗"
        shown = plaintext if len(plaintext) <= 20 else plaintext[:17] + "..."
        print(f"     {status} {shown!r} -> 还原 {recovered[:20]!r}")
        assert recovered == plaintext, "往返不一致，加密实现有 bug！"

    print("\n[5] 用服务器真实公钥试一次 ...")
    import ths_client as tc
    real_pem, ver = tc.thspypc.protocol.fetch_rsa_pubkey()
    real_key = RSA.import_key(real_pem)
    print(f"    服务器 rsa_version = {ver}")
    print(f"    真实公钥 n 位数 = {real_key.key_size}   e = {real_key.e}")
    real_blob = PKCS1_v1_5.new(real_key).encrypt("13800000000".encode("gbk"))
    print(f"    加密成功，base64 长度 = {len(base64.b64encode(real_blob).decode())}")

    print("\n" + "=" * 58)
    print("全部通过 ✅ 纯 Python RSA 实现正确，且不依赖任何第三方库")
    print("=" * 58)
    return 0


if __name__ == "__main__":
    sys.exit(main())
