"""POP3 client (fetch-focused) supporting USER/PASS, APOP, AUTH PLAIN/CRAM-MD5/XOAUTH2."""

from __future__ import annotations

import poplib
import ssl
from typing import Any

from mailpilot.core.auth import (
    apop_response,
    b64encode,
    cram_md5_response,
    extract_apop_timestamp,
    sasl_plain,
    xoauth2_string,
)
from mailpilot.core.config import Account
from mailpilot.core.errors import AuthError, ConnectionError_


def _connect(account: Account) -> tuple[Any, str]:
    """Connect and return (client, greeting)."""
    client: Any
    try:
        if account.pop_ssl:
            client = poplib.POP3_SSL(
                account.pop_host,
                account.pop_port,
                context=ssl.create_default_context(),
                timeout=30,
            )
        else:
            client = poplib.POP3(account.pop_host, account.pop_port, timeout=30)
            if account.pop_starttls:
                client.stls()
        greeting = client.welcome.decode("utf-8", "replace")
        return client, greeting
    except (OSError, poplib.error_proto, ssl.SSLError) as exc:
        raise ConnectionError_(
            f"Cannot connect to POP3 {account.pop_host}:{account.pop_port}: {exc}"
        ) from exc


def _pop_login(client: poplib.POP3, account: Account, greeting: str) -> None:
    auth = account.auth.lower()
    capabilities: list[str] = []
    try:
        caps_dict = client.capa() or {}
        for key, values in caps_dict.items():
            capabilities.append(key.upper())
            capabilities.extend(str(v).upper() for v in values)
    except poplib.error_proto:
        pass
    caps_upper = {c.upper() for c in capabilities}

    def try_apop() -> bool:
        timestamp = extract_apop_timestamp(greeting)
        if not timestamp:
            return False
        try:
            client.apop(account.email, apop_response(account.password, timestamp))
        except poplib.error_proto:
            return False
        return True

    def try_user_pass() -> bool:
        try:
            client.user(account.email)
            client.pass_(account.password)
        except poplib.error_proto:
            return False
        return True

    def try_cram_md5() -> bool:
        if "CRAM-MD5" not in caps_upper:
            return False
        try:
            typ, challenge_lines = client._shortcmd("AUTH CRAM-MD5")  # type: ignore[attr-defined]  # noqa: SLF001
            challenge = challenge_lines[0].decode("ascii", "replace") if challenge_lines else ""
            answer = cram_md5_response(account.email, account.password, challenge)
            client._shortcmd(b64encode(answer.encode("utf-8")))  # type: ignore[attr-defined]  # noqa: SLF001
        except (poplib.error_proto, Exception):
            return False
        return True

    def try_plain() -> bool:
        if "AUTH-PLAIN" not in caps_upper and "SASL-PLAIN" not in caps_upper:
            return False
        try:
            payload = b64encode(sasl_plain(account.email, account.password))
            client._shortcmd(f"AUTH PLAIN {payload}")  # type: ignore[attr-defined]  # noqa: SLF001
        except poplib.error_proto:
            return False
        return True

    def try_xoauth2() -> bool:
        if "AUTH-XOAUTH2" not in caps_upper or not account.oauth2_token:
            return False
        try:
            payload = b64encode(xoauth2_string(account.email, account.oauth2_token))
            client._shortcmd(f"AUTH XOAUTH2 {payload}")  # type: ignore[attr-defined]  # noqa: SLF001
        except poplib.error_proto:
            return False
        return True

    chain: tuple[Any, ...]
    if auth == "auto":
        chain = (try_apop, try_cram_md5, try_plain, try_xoauth2, try_user_pass)
    else:
        mapping = {
            "apop": (try_apop, try_user_pass),
            "plain": (try_plain, try_user_pass),
            "login": (try_user_pass,),
            "cram-md5": (try_cram_md5,),
            "xoauth2": (try_xoauth2,),
        }
        chain = mapping.get(auth, (try_user_pass, try_apop))
    for attempt in chain:
        if attempt():
            return
    raise AuthError(f"POP3 login failed for {account.email} on {account.pop_host}")


class PopClient:
    """Context-managed POP3 session."""

    def __init__(self, account: Account) -> None:
        self.account = account
        self._client: poplib.POP3 | None = None

    def __enter__(self) -> PopClient:
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def connect(self) -> None:
        client, greeting = _connect(self.account)
        _pop_login(client, self.account, greeting)
        self._client = client

    def close(self) -> None:
        if self._client:
            try:
                self._client.quit()
            except Exception:
                pass
            self._client = None

    @property
    def client(self) -> poplib.POP3:
        if self._client is None:
            raise ConnectionError_("POP3 not connected")
        return self._client

    def stat(self) -> tuple[int, int]:
        """(message_count, mailbox_size)."""
        return self.client.stat()

    def list_messages(self) -> list[tuple[str, int]]:
        typ, data, _octets = self.client.list()
        if not str(typ, "ascii", "replace").startswith("+OK"):
            return []
        out: list[tuple[str, int]] = []
        for line in data or []:
            text = line.decode("ascii", "replace")
            parts = text.split()
            if len(parts) >= 2:
                out.append((parts[0], int(parts[1])))
        return out

    def fetch(self, index: int) -> bytes:
        typ, lines, _octets = self.client.retr(index)
        if not str(typ, "ascii", "replace").startswith("+OK"):
            raise ConnectionError_(f"POP3 RETR {index} failed")
        return b"\r\n".join(lines)

    def fetch_all(self, limit: int = 0) -> list[tuple[int, bytes]]:
        total, _size = self.stat()
        indexes = range(1, total + 1)
        if limit:
            indexes = range(max(1, total - limit + 1), total + 1)
        return [(i, self.fetch(i)) for i in indexes]

    def uidl(self) -> list[str]:
        typ, data, _octets = self.client.uidl()
        if not str(typ, "ascii", "replace").startswith("+OK"):
            return []
        out: list[str] = []
        for line in data or []:
            text = line.decode("ascii", "replace")
            parts = text.split()
            if len(parts) >= 2:
                out.append(parts[1])
        return out

    def delete(self, index: int) -> None:
        self.client.dele(index)

    def noop(self) -> None:
        self.client.noop()
