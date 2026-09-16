"""Prove the vendored thspypc runs unmodified with the Crypto shim."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHIM = os.path.join(HERE, "_ths_crypto_shim")
PKG = os.path.join(os.path.dirname(HERE), "vendor", "thspypc_src", "src")
sys.path.insert(0, SHIM)
sys.path.insert(0, PKG)

print("1) shim satisfies `import Crypto`")
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
print("   Crypto shim OK ->", RSA.__name__, "/", PKCS1_v1_5.__name__)

print("\n2) thspypc imports unmodified")
import thspypc
print("   thspypc", thspypc.__version__)

print("\n3) thspypc's own rsa_encrypt works via the shim")
pem, rsa_ver = thspypc.protocol.fetch_rsa_pubkey()
print("   rsa_version =", rsa_ver)
blob = thspypc.protocol.rsa_encrypt("13800000000", pem)
print("   ciphertext len =", len(blob), "(expect 172 for a 1024-bit key)")
assert len(blob) == 172

print("\n4) device fingerprint from thspypc itself")
print("   imei  =", thspypc.generate_imei())
print("   mac64 =", thspypc.generate_mac64())

print("\n5) self-stock / cookie-auth classes available")
from thspypc import BlockAuth, BlockManager
print("   BlockAuth   :", [n for n in dir(BlockAuth) if not n.startswith('_')])
print("   BlockManager:", [n for n in dir(BlockManager) if not n.startswith('_')][:9])

print("\n6) QR login entrypoints available")
from thspypc import qr_login_flow, load_credentials, save_credentials
print("   qr_login_flow, load_credentials, save_credentials OK")
print("\nALL CHECKS PASSED")
