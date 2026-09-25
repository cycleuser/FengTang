"""Authentication method helpers shared by SMTP/IMAP/POP3 clients.

Covers all mainstream mechanisms:
- SASL PLAIN and LOGIN (password)
- CRAM-MD5 (challenge-response, used by IMAP/POP3/SMTP)
- XOAUTH2 / OAUTHBEARER (OAuth2 access tokens, Gmail/Outlook)
- NTLM (type-1/type-3 handshake, Outlook/Exchange)
- APOP (POP3 timestamped MD5 digest)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct

from fengtang.core._des import DesKey

SASL_MECHANISMS = ("PLAIN", "LOGIN", "CRAM-MD5", "XOAUTH2", "OAUTHBEARER", "NTLM")


def b64decode(value: str) -> bytes:
    """Decode server base64 challenge, tolerating surrounding whitespace."""
    return base64.b64decode(value.strip().encode("ascii"))


def b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


# ---------- SASL PLAIN ----------


def sasl_plain(authcid: str, password: str, authzid: str = "") -> bytes:
    """Build a SASL PLAIN response: authzid NUL authcid NUL password."""
    return b"\x00".join(
        [
            authzid.encode("utf-8"),
            authcid.encode("utf-8"),
            password.encode("utf-8"),
        ]
    )


# ---------- SASL LOGIN ----------


def sasl_login_user(username: str) -> bytes:
    return username.encode("utf-8")


def sasl_login_password(password: str) -> bytes:
    return password.encode("utf-8")


# ---------- CRAM-MD5 ----------


def cram_md5_response(username: str, password: str, challenge_b64: str) -> str:
    """Answer a CRAM-MD5 challenge: 'user <hexdigest-of-hmac(challenge)>'."""
    challenge = b64decode(challenge_b64)
    digest = hmac.new(password.encode("utf-8"), challenge, hashlib.md5).hexdigest()
    return f"{username} {digest}"


# ---------- XOAUTH2 / OAUTHBEARER ----------


def xoauth2_string(username: str, token: str) -> bytes:
    """SASL XOAUTH2 initial client response."""
    return f"user={username}\x01auth=Bearer {token}\x01\x01".encode()


def oauthbearer_string(token: str) -> bytes:
    """SASL OAUTHBEARER initial client response (RFC 7628)."""
    return f"n,a=,\x01auth=Bearer {token}\x01\x01".encode()


# ---------- NTLM (type-1/type-3 subset: NTLMv1 response) ----------

_NTLM_SIGNATURE = b"NTLMSSP\x00"

_NEGOTIATE_FLAGS = 0x00000201  # NTLMSSP_NEGOTIATE_UNICODE | REQUEST_TARGET | NEGOTIATE_NTLM
_NTLMSSP_TYPE1 = 1
_NTLMSSP_TYPE2 = 2
_NTLMSSP_TYPE3 = 3


def _pack_field(buffer: bytes, offset: int) -> bytes:
    """8-byte SECURITY_BUFFER: len, max-len, offset(u32)."""
    return struct.pack("<HHI", len(buffer), len(buffer), offset)


def ntlm_type1_message(domain: str = "", workstation: str = "") -> str:
    """Build an NTLMSSP type-1 (negotiate) message, base64-encoded."""
    domain_b = domain.upper().encode("utf-16-le")
    workstation_b = workstation.upper().encode("utf-16-le")
    header = (
        _NTLM_SIGNATURE + struct.pack("<I", _NTLMSSP_TYPE1) + struct.pack("<I", _NEGOTIATE_FLAGS)
    )
    msg = (
        header
        + _pack_field(domain_b, 32)
        + _pack_field(workstation_b, 32 + len(domain_b))
        + domain_b
        + workstation_b
    )
    return b64encode(msg)


def _parse_ntlm_type2(message_b64: str) -> tuple[bytes, bytes]:
    """Extract the server challenge and target info from a type-2 message."""
    raw = b64decode(message_b64)
    if raw[:8] != _NTLM_SIGNATURE or len(raw) < 32:
        raise ValueError("Malformed NTLM type-2 message")
    msg_type = struct.unpack("<I", raw[8:12])[0]
    if msg_type != _NTLMSSP_TYPE2:
        raise ValueError(f"Expected NTLM type-2, got type {msg_type}")
    challenge = raw[24:32]
    target_info = b""
    if len(raw) >= 44:
        ti_len, _ti_maxlen, ti_offset, _ = struct.unpack("<HHII", raw[32:44])
        if ti_len and len(raw) >= ti_offset + ti_len:
            target_info = raw[ti_offset : ti_offset + ti_len]
    return challenge, target_info


def _ntlmv1_response(password: str, challenge: bytes) -> bytes:
    """Classic NTLMv1 NT-response: 24 bytes = 3x DES(challenge, 7-byte key chunk)."""
    # NT hash = MD4(UTF-16LE(password)); pad to 21 bytes and split into 7-byte keys.
    from fengtang.core._md4 import md4

    ntlm_hash = md4(password.encode("utf-16-le"))
    padded = ntlm_hash.ljust(21, b"\x00")
    parts = []
    for i in range(3):
        chunk = padded[(i * 7) : ((i + 1) * 7)]
        parts.append(DesKey(chunk).encrypt(challenge))
    return b"".join(parts)


def ntlm_type3_message(
    username: str,
    password: str,
    type2_b64: str,
    domain: str = "",
    workstation: str = "",
) -> str:
    """Build an NTLMSSP type-3 (authenticate) message with NTLMv1 responses."""
    challenge, _target_info = _parse_ntlm_type2(type2_b64)
    user_b = username.encode("utf-16-le")
    domain_b = domain.upper().encode("utf-16-le")
    ws_b = workstation.upper().encode("utf-16-le")
    lm_resp = _lm_response(password, challenge)
    nt_resp = _ntlmv1_response(password, challenge)

    base = 64
    user_off = base + len(domain_b)
    ws_off = user_off + len(user_b)
    lm_off = ws_off + len(ws_b)
    nt_off = lm_off + len(lm_resp)
    header = (
        _NTLM_SIGNATURE
        + struct.pack("<I", _NTLMSSP_TYPE3)
        + _pack_field(domain_b, base)
        + _pack_field(user_b, user_off)
        + _pack_field(ws_b, ws_off)
        + _pack_field(lm_resp, lm_off)
        + _pack_field(nt_resp, nt_off)
        + _pack_field(b"", nt_off + len(nt_resp))  # session key: empty
        + struct.pack("<I", 0x8201)  # flags: unicode + ntlm + ...
    )
    msg = header + domain_b + user_b + ws_b + lm_resp + nt_resp
    return b64encode(msg)


def _lm_response(password: str, challenge: bytes) -> bytes:
    """LM response: DES(challenge, LM-hash split) — 7-byte keys padded to 8."""
    pw = password.upper()[:14].encode("ascii").ljust(14, b"\x00")
    from fengtang.core._des import DesKey

    part1 = DesKey(pw[:7]).encrypt(challenge)
    part2 = DesKey(pw[7:14]).encrypt(challenge)
    return part1 + part2


def ntlm_available() -> bool:
    return True


# ---------- APOP (POP3) ----------


def apop_response(password: str, banner_timestamp: str) -> str:
    """APOP digest = MD5(timestamp + shared-secret) (RFC 1939 Appendix E)."""
    return hashlib.md5(banner_timestamp.encode("utf-8") + password.encode("utf-8")).hexdigest()


def extract_apop_timestamp(banner: str) -> str | None:
    """Find '<...>' APOP timestamp in a POP3 greeting; returns it or None."""
    import re

    match = re.search(r"<[^>\s]+>", banner)
    return match.group(0) if match else None


def random_challenge(nbytes: int = 16) -> str:
    """Server-side helper: random base64 challenge for CRAM-MD5."""
    return b64encode(secrets.token_bytes(nbytes))
