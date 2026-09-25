"""Pure-Python MD4 (needed for NTLM; dropped from stdlib hashlib long ago)."""

from __future__ import annotations

import struct


def _rot(x: int, n: int) -> int:
    x &= 0xFFFFFFFF
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def md4(data: bytes) -> bytes:
    """Return the 16-byte MD4 digest of `data` (RFC 1320)."""
    msg_len = len(data)
    padded = bytearray(data) + bytearray([0x80])
    while len(padded) % 64 != 56:
        padded.append(0)
    padded += struct.pack("<Q", msg_len * 8)

    a0, b0, c0, d0 = 0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476
    mask = 0xFFFFFFFF
    for start in range(0, len(padded), 64):
        x = struct.unpack("<16I", bytes(padded[start : start + 64]))
        a, b, c, d = a0, b0, c0, d0

        # Round 1 (F): a += F(b,c,d) + X[k]; rotate a by s; then shift roles.
        for k, s in zip(range(16), (3, 7, 11, 19) * 4, strict=False):
            f = (b & c) | ((~b & mask) & d)
            a = (a + f + x[k]) & mask
            a = _rot(a, s)
            a, b, c, d = d, a, b, c
        # Round 2 (G): word order 0,4,8,...; constant 0x5A827999.
        for k, s in zip(
            (0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15), (3, 5, 9, 13) * 4, strict=True
        ):
            g = (b & c) | (b & d) | (c & d)
            a = (a + g + x[k] + 0x5A827999) & mask
            a = _rot(a, s)
            a, b, c, d = d, a, b, c
        # Round 3 (H): word order 0,8,4,12,...; constant 0x6ED9EBA1.
        for k, s in zip(
            (0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15), (3, 9, 11, 15) * 4, strict=True
        ):
            h = b ^ c ^ d
            a = (a + h + x[k] + 0x6ED9EBA1) & mask
            a = _rot(a, s)
            a, b, c, d = d, a, b, c

        a0 = (a0 + a) & mask
        b0 = (b0 + b) & mask
        c0 = (c0 + c) & mask
        d0 = (d0 + d) & mask
    return struct.pack("<4I", a0, b0, c0, d0)
