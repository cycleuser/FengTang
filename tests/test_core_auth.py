"""Tests for auth primitives (PLAIN/LOGIN/CRAM-MD5/XOAUTH2/NTLM/APOP)."""

from __future__ import annotations

import base64
import hashlib
import hmac

from fengtang.core._md4 import md4
from fengtang.core.auth import (
    apop_response,
    cram_md5_response,
    extract_apop_timestamp,
    ntlm_type1_message,
    ntlm_type3_message,
    oauthbearer_string,
    sasl_plain,
    xoauth2_string,
)


class TestMd4:
    """MD4 verified against `openssl dgst -provider legacy -md4`."""

    def test_vectors(self) -> None:
        vectors = [
            (b"", "31d6cfe0d16ae931b73c59d7e0c089c0"),
            (b"a", "bde52cb31de33e46245e05fbdbd6fb24"),
            (b"abc", "a448017aaf21d8525fc10ae87aa6729d"),
            (b"message digest", "d9130a8164549fe818874806e1c7014b"),
            (b"abcdefghijklmnopqrstuvwxyz", "d79e1c308aa5bbcdeea8ed63df412da9"),
            (
                b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
                "043f8582f241db351ce627e153e7f0e4",
            ),
            (
                b"12345678901234567890123456789012345678901234567890123456789012345678901234567890",
                "e33b4ddc9c38f2199c3e7b164fcc0536",
            ),
        ]
        for data, expected in vectors:
            assert md4(data).hex() == expected, f"MD4({data[:12]!r})"

    def test_ntlm_hash(self) -> None:
        assert md4("password".encode("utf-16-le")).hex() == "8846f7eaee8fb117ad06bdd830b7586c"


class TestSaslPlain:
    def test_format(self) -> None:
        raw = sasl_plain("user@example.com", "secret")
        assert raw == b"\x00user@example.com\x00secret"

    def test_with_authzid(self) -> None:
        raw = sasl_plain("u", "p", authzid="z")
        assert raw == b"z\x00u\x00p"


class TestCramMd5:
    def test_known_vector(self) -> None:
        challenge = base64.b64encode(b"<1234.5678@host>").decode()
        digest = hmac.new(b"tim", b"<1234.5678@host>", hashlib.md5).hexdigest()
        expected = f"tim {digest}"
        assert cram_md5_response("tim", "tim", challenge) == expected


class TestXoauth2:
    def test_format(self) -> None:
        payload = xoauth2_string("user@gmail.com", "token123")
        assert payload == b"user=user@gmail.com\x01auth=Bearer token123\x01\x01"

    def test_oauthbearer(self) -> None:
        payload = oauthbearer_string("tok")
        assert b"Bearer tok" in payload


class TestApop:
    def test_extract_timestamp(self) -> None:
        banner = "+OK POP3 ready <1896.697170952@dbc.mtview.ca.us>"
        assert extract_apop_timestamp(banner) == "<1896.697170952@dbc.mtview.ca.us>"

    def test_no_timestamp(self) -> None:
        assert extract_apop_timestamp("+OK plain banner") is None

    def test_response_matches_md5(self) -> None:
        ts = "<1896.697170952@dbc.mtview.ca.us>"
        expected = hashlib.md5(ts.encode() + b"tanstaaf").hexdigest()
        assert apop_response("tanstaaf", ts) == expected


class TestNtlm:
    def test_type1_signature(self) -> None:
        msg = base64.b64decode(ntlm_type1_message())
        assert msg[:8] == b"NTLMSSP\x00"
        assert int.from_bytes(msg[8:12], "little") == 1

    def test_type3_roundtrip(self) -> None:
        # Simulate a type-2 challenge message.
        challenge = b"\x01\x02\x03\x04\x05\x06\x07\x08"
        type2 = base64.b64encode(
            b"NTLMSSP\x00" + (2).to_bytes(4, "little") + b"\x00" * 12 + challenge + b"\x00" * 16
        ).decode()
        type3 = ntlm_type3_message("user", "password", type2)
        raw = base64.b64decode(type3)
        assert raw[:8] == b"NTLMSSP\x00"
        assert int.from_bytes(raw[8:12], "little") == 3

        # Layout: sig(8)+type(4), then 6 SECURITY_BUFFERS (8B each) at offset 12,
        # then flags. field3=LM response (16B), field4=NT response (24B).
        def field_len(index: int) -> int:
            off = 12 + 8 * index
            return int.from_bytes(raw[off : off + 2], "little")

        assert field_len(3) == 16 and field_len(4) == 24

    def test_malformed_type2_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            ntlm_type3_message("u", "p", base64.b64encode(b"junkjunkjunkjunkjunk").decode())
