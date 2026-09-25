"""Tests for the self-contained MTA: DNS resolver, outbound delivery, inbound pull."""

from __future__ import annotations

import email
from pathlib import Path

from fengtang.core.config import Account
from fengtang.mail.parser import build_message
from fengtang.mail.store import Store
from fengtang.serve import dns
from fengtang.serve import outbound as ob
from fengtang.serve.pull import pull_account


class TestDnsResolver:
    def test_encode_name(self) -> None:
        assert dns._encode_name("a.bc") == b"\x01a\x02bc\x00"

    def test_decode_roundtrip(self) -> None:
        packet = dns._encode_name("mail.example.com") + b"\x00\x01\x00\x01"
        name, offset = dns._decode_name(packet, 0)
        assert name == "mail.example.com"
        assert offset == len(packet) - 4

    def test_decode_compression_pointer(self) -> None:
        # "example.com" at offset 0, then a name "mail" + pointer to offset 0
        packet = dns._encode_name("example.com") + b"\x04mail\xc0\x00"
        name, _ = dns._decode_name(packet, len(dns._encode_name("example.com")))
        assert name == "mail.example.com"

    def test_resolve_mx_fallback_on_dns_error(self, monkeypatch) -> None:
        def boom(*_args, **_kwargs):
            raise OSError("no dns")

        monkeypatch.setattr(dns, "_raw_query", boom)
        assert dns.resolve_mx("example.invalid") == ["example.invalid"]

    def test_resolve_mx_parses_answer(self, monkeypatch) -> None:
        # Build a synthetic response: question + two MX answers (pref 20 then 10).
        name = dns._encode_name("example.com")
        question = name + b"\x00\x0f\x00\x01"
        answer_head = b"\xc0\x0c"  # pointer to the question name
        exch_a = dns._encode_name("mx-b.example.com")
        exch_b = dns._encode_name("mx-a.example.com")
        rr_a = (
            answer_head
            + b"\x00\x0f\x00\x01\x00\x00\x00\x3c"
            + len(b"\x00\x14" + exch_a).to_bytes(2, "big")
            + b"\x00\x14"
            + exch_a
        )
        rr_b = (
            answer_head
            + b"\x00\x0f\x00\x01\x00\x00\x00\x3c"
            + len(b"\x00\x0a" + exch_b).to_bytes(2, "big")
            + b"\x00\x0a"
            + exch_b
        )
        header = b"\x12\x34\x81\x80\x00\x01\x00\x02\x00\x00\x00\x00"
        packet = header + question + rr_a + rr_b

        monkeypatch.setattr(dns, "_raw_query", lambda *a, **k: packet)
        assert dns.resolve_mx("example.com") == ["mx-a.example.com", "mx-b.example.com"]

    def test_system_nameservers_nonempty(self) -> None:
        assert dns.system_nameservers()


class TestOutboundHelpers:
    def test_crlf_normalisation(self) -> None:
        assert ob._crlf(b"a\nb\r\nc") == b"a\r\nb\r\nc"

    def test_default_helo_host_is_fqdnish(self) -> None:
        assert "." in ob.default_helo_host()

    def test_rewrite_from_preserves_original(self) -> None:
        msg = build_message("orig@fengtang.local", ["x@y.z"], "s", "b")
        rewritten = ob.rewrite_from(bytes(msg), "relay@qq.com", "orig@fengtang.local")
        parsed = email.message_from_bytes(rewritten)
        assert parsed["From"] == "relay@qq.com"
        assert parsed["Reply-To"] == "orig@fengtang.local"
        assert parsed["X-Original-From"] == "orig@fengtang.local"

    def test_deliver_smarthost_without_account(self) -> None:
        result = ob.deliver(b"raw", "a@b.c", ["d@e.f"], mode="smarthost")
        assert result.delivered is False
        assert "no relay account" in result.detail


class TestOutboundRelayToBuiltin:
    def test_relay_via_local_smarthost(self, server_env) -> None:
        """Relay through the built-in SMTP server (acts as an authenticated smarthost)."""
        account = Account(
            name="relay",
            email="alice@example.test",
            password="alicepw",
            smtp_host="127.0.0.1",
            smtp_port=server_env["smtp_port"],
            smtp_ssl=False,
            auth="login",
        )
        msg = build_message("someone@fengtang.local", ["bob@example.test"], "relayed", "hi")
        result = ob.relay_deliver(
            account, bytes(msg), "someone@fengtang.local", ["bob@example.test"]
        )
        assert result.delivered, result.detail
        store = Store(server_env["db_path"])
        try:
            subjects = [row["subject"] for row in store.list_messages()]
            assert "relayed" in subjects
        finally:
            store.close()

    def test_deliver_auto_falls_back_to_smarthost(self, server_env, monkeypatch) -> None:
        """Direct-MX is stubbed to fail; delivery must fall back to the smarthost."""
        monkeypatch.setattr(
            ob, "direct_deliver", lambda *a, **k: ob.DeliveryResult(False, "direct", "stub fail")
        )
        account = Account(
            name="relay",
            email="alice@example.test",
            password="alicepw",
            smtp_host="127.0.0.1",
            smtp_port=server_env["smtp_port"],
            smtp_ssl=False,
            auth="login",
        )
        msg = build_message("someone@fengtang.local", ["bob@example.test"], "fallback", "hi")
        result = ob.deliver(
            bytes(msg),
            "someone@fengtang.local",
            ["bob@example.test"],
            relay_account=account,
            mode="auto",
        )
        assert result.delivered and result.via == "smarthost"

    def test_outbound_failure_reported(self, server_env) -> None:
        account = Account(
            name="relay",
            email="alice@example.test",
            password="wrongpw",
            smtp_host="127.0.0.1",
            smtp_port=server_env["smtp_port"],
            smtp_ssl=False,
            auth="login",
        )
        result = ob.relay_deliver(
            account, b"Subject: x\r\n\r\nbody\r\n", "a@b.c", ["bob@example.test"]
        )
        assert result.delivered is False


class TestInboundPull:
    def test_pull_pop3_into_store(self, server_env, tmp_path: Path) -> None:
        """Deliver a message locally, then pull it back via POP3 into a store."""
        from fengtang.core.config import Account
        from fengtang.mail.smtp_client import smtp_send

        sender = Account(
            name="s",
            email="alice@example.test",
            password="alicepw",
            smtp_host="127.0.0.1",
            smtp_port=server_env["smtp_port"],
            smtp_ssl=False,
        )
        msg = build_message("alice@example.test", ["bob@example.test"], "pull-me", "body")
        smtp_send(sender, msg, to_addrs=["bob@example.test"])

        # Pull bob's mailbox via POP3 into a fresh store.
        bob = Account(
            name="b",
            email="bob@example.test",
            password="bobpw",
            pop_host="127.0.0.1",
            pop_port=server_env["pop_port"],
            pop_ssl=False,
        )
        target = Store(tmp_path / "pulled.db")
        try:
            count = pull_account(bob, target)
            assert count >= 1
            subjects = [row["subject"] for row in target.list_messages()]
            assert "pull-me" in subjects
        finally:
            target.close()

    def test_pull_no_server_returns_zero(self, tmp_path: Path) -> None:
        acct = Account(name="none", email="a@b.c")
        store = Store(tmp_path / "empty.db")
        try:
            assert pull_account(acct, store) == 0
        finally:
            store.close()


class TestServerPullLoop:
    def test_server_pull_accounts(self, server_env, tmp_path: Path, monkeypatch) -> None:
        """MailServer's pull wiring stores external mail without a public IP."""
        import asyncio

        from fengtang.core.config import Account
        from fengtang.mail.smtp_client import smtp_send
        from fengtang.serve.server import MailServer

        # Seed bob's mailbox with a message.
        sender = Account(
            name="s",
            email="alice@example.test",
            password="alicepw",
            smtp_host="127.0.0.1",
            smtp_port=server_env["smtp_port"],
            smtp_ssl=False,
        )
        msg = build_message("alice@example.test", ["bob@example.test"], "loop-pull", "b")
        smtp_send(sender, msg, to_addrs=["bob@example.test"])

        bob = Account(
            name="b",
            email="bob@example.test",
            password="bobpw",
            pop_host="127.0.0.1",
            pop_port=server_env["pop_port"],
            pop_ssl=False,
        )
        db = tmp_path / "server2.db"
        server = MailServer(
            db,
            smtp_host="127.0.0.1",
            smtp_port=0,
            pop_host="127.0.0.1",
            pop_port=0,
            domain="example.test",
            pull_accounts=[bob],
        )
        try:
            count = asyncio.run(server._pull_once())
            assert count >= 1
            subjects = [row["subject"] for row in server.store.list_messages()]
            assert "loop-pull" in subjects
        finally:
            server.store.close()
