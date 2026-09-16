"""PyCryptodome `Crypto.Cipher` 的最小兼容实现（基于 cryptography）。"""
from . import PKCS1_v1_5  # noqa: F401

__all__ = ["PKCS1_v1_5"]
