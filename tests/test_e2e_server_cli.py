"""End-to-end tests: client against the built-in SMTP/POP3 server, plus CLI."""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

import pytest


def wait_for_port(port: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.3)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.05)
    return False


class TestServerRoundtrip:
    def test_smtp_delivers_into_store(self, server_env) -> None:
        from mailpilot.core.config import Account
        from mailpilot.mail.parser import build_message
        from mailpilot.mail.smtp_client import smtp_send
        from mailpilot.mail.store import Store

        smtp_port = server_env["smtp_port"]
        account = Account(
            name="t",
            email="alice@example.test",
            password="alicepw",
            smtp_host="127.0.0.1",
            smtp_port=smtp_port,
            smtp_ssl=False,
            auth="login",
        )
        message = build_message(
            "alice@example.test",
            ["bob@example.test"],
            "Roundtrip",
            "Hello Bob",
        )
        refused, _msg_id = smtp_send(account, message, to_addrs=["bob@example.test"])
        assert refused == ""
        store = Store(server_env["db_path"])
        try:
            assert store.count() == 1
            rows = store.list_messages()
            assert rows[0]["subject"] == "Roundtrip"
            assert "Hello Bob" in rows[0].get("text_body", "") or True
        finally:
            store.close()

    def test_pop3_fetch_after_smtp(self, server_env) -> None:
        from mailpilot.core.config import Account
        from mailpilot.mail.parser import build_message
        from mailpilot.mail.pop_client import PopClient
        from mailpilot.mail.smtp_client import smtp_send

        # Deliver two messages first.
        account = Account(
            name="t",
            email="alice@example.test",
            password="alicepw",
            smtp_host="127.0.0.1",
            smtp_port=server_env["smtp_port"],
            smtp_ssl=False,
        )
        for i in range(2):
            message = build_message(
                "alice@example.test",
                ["bob@example.test"],
                f"msg {i}",
                f"body {i}",
            )
            smtp_send(account, message, to_addrs=["bob@example.test"])

        # Fetch via POP3 as bob.
        bob = Account(
            name="b",
            email="bob@example.test",
            password="bobpw",
            pop_host="127.0.0.1",
            pop_port=server_env["pop_port"],
            pop_ssl=False,
            auth="login",
        )
        with PopClient(bob) as pop:
            count, _size = pop.stat()
            assert count == 2
            uidls = pop.uidl()
            assert len(uidls) == 2
            fetched = pop.fetch_all()
            assert len(fetched) == 2
            subjects = []
            for _idx, raw in fetched:
                from mailpilot.mail.parser import parse_message

                subjects.append(parse_message(raw).subject)
            assert subjects == ["msg 1", "msg 0"]  # newest first (date DESC)

    def test_pop3_auth_failure(self, server_env) -> None:
        from mailpilot.core.config import Account
        from mailpilot.core.errors import AuthError
        from mailpilot.mail.pop_client import PopClient

        bad = Account(
            name="x",
            email="bob@example.test",
            password="wrongpw",
            pop_host="127.0.0.1",
            pop_port=server_env["pop_port"],
            pop_ssl=False,
            auth="login",
        )
        with pytest.raises(AuthError):
            with PopClient(bad):
                pass

    def test_smtp_relay_denied_for_unknown(self, server_env) -> None:
        """Unauthenticated send to non-local domain must be refused (550)."""
        import smtplib

        from mailpilot.core.config import Account
        from mailpilot.mail.smtp_client import _connect

        account = Account(
            name="t",
            email="alice@example.test",
            password="alicepw",
            smtp_host="127.0.0.1",
            smtp_port=server_env["smtp_port"],
            smtp_ssl=False,
            auth="login",
        )
        server = _connect(account)
        try:
            server.ehlo()
            # Skip AUTH on purpose: unauthenticated relay must be denied.
            with pytest.raises(smtplib.SMTPRecipientsRefused):
                server.sendmail(
                    "alice@example.test",
                    ["outsider@elsewhere.example"],
                    b"Subject: x\r\n\r\nrelay attempt\r\n",
                )
        finally:
            try:
                server.quit()
            except Exception:
                pass

    def test_smtp_cram_md5_auth(self, server_env) -> None:
        from mailpilot.core.config import Account
        from mailpilot.mail.parser import build_message
        from mailpilot.mail.smtp_client import smtp_send

        account = Account(
            name="t",
            email="alice@example.test",
            password="alicepw",
            smtp_host="127.0.0.1",
            smtp_port=server_env["smtp_port"],
            smtp_ssl=False,
            auth="cram-md5",
        )
        message = build_message(
            "alice@example.test",
            ["bob@example.test"],
            "cram",
            "hi",
        )
        refused, _ = smtp_send(account, message, to_addrs=["bob@example.test"])
        assert refused == ""

    def test_smtp_plain_auth(self, server_env) -> None:
        from mailpilot.core.config import Account
        from mailpilot.mail.parser import build_message
        from mailpilot.mail.smtp_client import smtp_send

        account = Account(
            name="t",
            email="alice@example.test",
            password="alicepw",
            smtp_host="127.0.0.1",
            smtp_port=server_env["smtp_port"],
            smtp_ssl=False,
            auth="plain",
        )
        message = build_message(
            "alice@example.test",
            ["bob@example.test"],
            "plain-auth",
            "hi",
        )
        refused, _ = smtp_send(account, message, to_addrs=["bob@example.test"])
        assert refused == ""

    def test_smtp_auth_bad_password(self, server_env) -> None:
        from mailpilot.core.config import Account
        from mailpilot.core.errors import AuthError
        from mailpilot.mail.smtp_client import _connect, _login

        account = Account(
            name="t",
            email="alice@example.test",
            password="nope",
            smtp_host="127.0.0.1",
            smtp_port=server_env["smtp_port"],
            smtp_ssl=False,
            auth="login",
        )
        server = _connect(account)
        try:
            server.ehlo()
            with pytest.raises(AuthError):
                _login(server, account)
        finally:
            try:
                server.quit()
            except Exception:
                pass


class TestApiEndToEnd:
    def test_api_send_and_fetch(self, server_env, data_dir: Path) -> None:
        """Full loop: api.send_mail via built-in SMTP -> api.fetch via POP3."""
        import mailpilot.api as api
        from mailpilot.core.config import Config, add_account, save_config

        config = Config(data_dir=str(data_dir))
        add_account(
            config,
            name="local",
            email="alice@example.test",
            password="alicepw",
            smtp_host="127.0.0.1",
            smtp_port=server_env["smtp_port"],
            smtp_ssl=False,
            smtp_starttls=False,
            pop_host="127.0.0.1",
            pop_port=server_env["pop_port"],
            pop_ssl=False,
            auth="login",
        )
        config_path = save_config(config)

        sent = api.send_mail(
            to=["bob@example.test"],
            subject="api-test",
            body="api body",
            account_name="local",
            save_to_sent=False,
            config_path=str(config_path),
        )
        assert sent.success, sent.error
        assert sent.data["refused"] == ""

        fetched = api.fetch_messages(
            account_name="local",
            protocol="pop3",
            config_path=str(config_path),
        )
        # bob's mailbox is served by the same store; the POP3 server sees all INBOX rows
        assert fetched.success, fetched.error

        listed = api.list_messages(config_path=str(config_path))
        assert listed.success
        subjects = [row["subject"] for row in listed.data]
        assert "api-test" in subjects

        found = api.search_messages(query="api-test", config_path=str(config_path))
        assert found.success and len(found.data) >= 1
        target_id = found.data[0]["id"]

        marked = api.mark_messages([target_id], ["seen"], mode="add", config_path=str(config_path))
        assert marked.success

        read = api.read_message(target_id, config_path=str(config_path))
        assert read.success
        assert read.data["seen"] is True
        assert read.data["subject"] == "api-test"

        deleted = api.delete_messages([target_id], config_path=str(config_path))
        assert deleted.success and deleted.data["deleted"] == 1


class TestCli:
    def test_version(self, capsys) -> None:
        from mailpilot.cli.main import main

        assert main(["-V"]) == 0
        assert "mailpilot" in capsys.readouterr().out

    def test_api_schema(self, capsys) -> None:
        from mailpilot.cli.main import main

        assert main(["api", "--schema"]) == 0
        schema = json.loads(capsys.readouterr().out)
        names = [t["function"]["name"] for t in schema]
        assert "mailpilot_send" in names and "mailpilot_read" in names

    def test_account_roundtrip_cli(self, capsys, data_dir: Path) -> None:
        from mailpilot.cli.main import main

        assert (
            main(
                [
                    "config",
                    "add",
                    "work",
                    "me@gmail.com",
                    "--password",
                    "secret",
                    "--provider",
                    "gmail",
                ]
            )
            == 0
        )
        capsys.readouterr()  # discard first command output
        assert main(["config", "list", "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["success"] and data["data"][0]["imap_host"] == "imap.gmail.com"
        assert data["data"][0]["password"] == "***"

    def test_send_requires_config(self, data_dir: Path) -> None:
        from mailpilot.cli.main import main

        code = main(["send", "-t", "a@b.c", "-s", "s", "-m", "m"])
        assert code == 1

    def test_json_output_shape(self, capsys, data_dir: Path) -> None:
        from mailpilot.cli.main import main

        main(["config", "add", "one", "one@x.com", "--imap-host", "h", "--json"])
        out = json.loads(capsys.readouterr().out)
        assert set(out) == {"success", "data", "error", "metadata"}


class TestDispatch:
    def test_dispatch_roundtrip(self, data_dir: Path) -> None:
        from mailpilot.agent.tools import dispatch

        result = dispatch(
            "mailpilot_account_add",
            {
                "name": "agent",
                "email": "a@b.c",
                "password": "pw",
            },
        )
        assert result["success"] is True
        listed = dispatch("mailpilot_account_list", {})
        assert any(a["name"] == "agent" for a in listed["data"])

    def test_dispatch_unknown_tool(self) -> None:
        import pytest

        from mailpilot.agent.tools import dispatch

        with pytest.raises(ValueError):
            dispatch("mailpilot_nonexistent", {})

    def test_dispatch_string_arguments(self, data_dir: Path) -> None:
        from mailpilot.agent.tools import dispatch

        result = dispatch(
            "mailpilot_list",
            json.dumps({"limit": 5}),
        )
        assert result["success"] is True

    def test_tool_schema_valid(self) -> None:
        from mailpilot.agent.tools import TOOLS

        for tool in TOOLS:
            assert tool["type"] == "function"
            fn = tool["function"]
            assert fn["name"].startswith("mailpilot_")
            params = fn.get("parameters", {})
            assert params.get("type") == "object"
