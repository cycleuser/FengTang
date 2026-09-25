"""Pure-Python built-in mail servers (asyncio SMTP + POP3) with zero system deps.

The SMTP server accepts local-domain delivery and stores messages in the same
SQLite store the client reads from. Optional AUTH (PLAIN/LOGIN/CRAM-MD5) can be
required; when no credentials are configured the server runs open-relay-free
(only local recipients accepted).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import secrets
import socket
import time
from pathlib import Path
from typing import Any

from fengtang.core.auth import extract_apop_timestamp, random_challenge
from fengtang.core.errors import ServerError
from fengtang.mail.store import Store

log = logging.getLogger("fengtang.server")

_CRLF = b"\r\n"


class MailboxServerState:
    """Shared state: local domains, users, and the backing store."""

    def __init__(
        self,
        store: Store,
        local_domains: list[str],
        users: dict[str, str] | None = None,
        require_auth: bool = False,
    ) -> None:
        self.store = store
        self.local_domains = [d.lower() for d in local_domains or []]
        self.users: dict[str, str] = users or {}  # username(email) -> password
        self.require_auth = require_auth
        self._sent_log: list[dict[str, Any]] = []

    def is_local_recipient(self, address: str) -> bool:
        addr = address.strip().lower()
        domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
        return domain in self.local_domains or addr in {u.lower() for u in self.users}

    def record_delivery(self, sender: str, recipient: str, raw: bytes) -> int:
        flags = ["\\Seen"]
        return self.store.store(raw, folder="INBOX", extra_flags=flags)

    def log_sent(self, sender: str, recipient: str, raw: bytes) -> None:
        self._sent_log.append(
            {
                "sender": sender,
                "recipient": recipient,
                "size": len(raw),
                "ts": time.time(),
            }
        )

    @property
    def sent_log(self) -> list[dict[str, Any]]:
        return list(self._sent_log)


class _LineReader:
    """Buffered CRLF line reader over an asyncio StreamReader."""

    def __init__(self, reader: asyncio.StreamReader) -> None:
        self._reader = reader

    async def read_line(self, limit: int = 1_048_576) -> bytes:
        line = await asyncio.wait_for(self._reader.readline(), timeout=120)
        if not line:
            raise ConnectionResetError("client closed connection")
        if len(line) > limit:
            raise ServerError("line too long")
        return line.rstrip(b"\r\n")


class _AuthSession:
    """Tracks AUTH state for one session."""

    def __init__(self, state: MailboxServerState) -> None:
        self.state = state
        self.user: str | None = None
        self.awaiting: str | None = None  # mechanism awaiting continuation
        self.challenge: str = ""

    @property
    def authenticated(self) -> bool:
        return self.user is not None

    def check_creds(self, user: str, password: str) -> bool:
        return self.state.users.get(user.lower(), self.state.users.get(user)) == password


# =====================================================================
# SMTP server (RFC 5321 subset: MAIL/RCPT/DATA/RSET/NOOP/QUIT/AUTH)
# =====================================================================


class SmtpSession:
    """One SMTP client session."""

    GREETING = "220 {host} FengTang SMTP ready"

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        state: MailboxServerState,
        hostname: str,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.state = state
        self.hostname = hostname
        self.lines = _LineReader(reader)
        self.auth = _AuthSession(state)
        self.sender = ""
        self.recipients: list[str] = []
        self.in_data = False
        self._data_lines: list[bytes] = []

    async def send(self, text: str) -> None:
        self.writer.write(text.encode("utf-8") + _CRLF)
        await self.writer.drain()

    async def run(self) -> None:
        await self.send(self.GREETING.format(host=self.hostname))
        while True:
            try:
                line = await self.lines.read_line()
            except (asyncio.TimeoutError, ConnectionResetError):
                break
            if self.auth.awaiting:
                done = await self._handle_auth_line(line)
                if done:
                    break
                continue
            if self.in_data:
                done = await self._handle_data_line(line)
                if done:
                    break
                continue
            text = line.decode("utf-8", "replace")
            try:
                if not await self._handle_command(text):
                    break
            except ServerError as exc:
                await self.send(f"451 {exc}")
        try:
            self.writer.close()
        except Exception:
            pass

    async def _handle_command(self, text: str) -> bool:
        verb, _, rest = text.partition(" ")
        verb_u = verb.upper()
        if verb_u == "QUIT":
            await self.send("221 Bye")
            return False
        if verb_u == "EHLO":
            features = [
                f"250-{self.hostname}",
                "250-8BITMIME",
                "250-SIZE 26214400",
                "250-PIPELINING",
            ]
            if self.state.users:
                features.append("250-AUTH PLAIN LOGIN CRAM-MD5")
            features.append("250 HELP")
            for feature in features:
                await self.send(feature)
            return True
        if verb_u in ("HELO",):
            await self.send(f"250 {self.hostname}")
            return True
        if verb_u == "NOOP":
            await self.send("250 OK")
            return True
        if verb_u == "RSET":
            self.sender = ""
            self.recipients = []
            await self.send("250 OK")
            return True
        if verb_u == "MAIL":
            if self.auth.awaiting:
                await self.send("503 bad sequence (finish AUTH first)")
                return True
            if self.sender:
                await self.send("503 nested MAIL")
                return True
            addr = self._extract_addr(rest)
            if addr is None:
                await self.send("501 syntax")
                return True
            self.sender = addr
            await self.send("250 OK")
            return True
        if verb_u == "RCPT":
            if not self.sender:
                await self.send("503 need MAIL first")
                return True
            addr = self._extract_addr(rest)
            if addr is None:
                await self.send("501 syntax")
                return True
            if not self.auth.authenticated and not self.state.is_local_recipient(addr):
                await self.send("550 relay denied")
                return True
            self.recipients.append(addr)
            await self.send("250 OK")
            return True
        if verb_u == "DATA":
            if not self.recipients:
                await self.send("503 need RCPT first")
                return True
            await self.send("354 End data with <CR><LF>.<CR><LF>")
            self.in_data = True
            return True
        if verb_u == "AUTH":
            return await self._handle_auth(rest)
        if verb_u == "STARTTLS":
            await self.send("454 TLS not available")
            return True
        await self.send("502 command not implemented")
        return True

    @staticmethod
    def _extract_addr(rest: str) -> str | None:
        import re

        match = re.search(r"<([^>]*)>", rest)
        if match:
            return match.group(1).strip().lower() or None
        parts = rest.split(":")
        addr = parts[-1].strip() if parts else ""
        return addr or None

    async def _handle_auth(self, rest: str) -> bool:
        if self.auth.authenticated:
            await self.send("503 already authenticated")
            return True
        mech, _, initial = rest.partition(" ")
        mech_u = mech.upper()
        if mech_u == "PLAIN":
            payload = initial or ""
            if not payload:
                self.auth.awaiting = "PLAIN"
                await self.send("334 ")
                return True
            return await self._finish_plain(payload)
        if mech_u == "LOGIN":
            self.auth.awaiting = "LOGIN"
            self.auth.challenge = base64.b64encode(b"Username:").decode("ascii")
            await self.send(f"334 {self.auth.challenge}")
            return True
        if mech_u == "CRAM-MD5":
            self.auth.awaiting = "CRAM-MD5"
            self.auth.challenge = random_challenge()
            await self.send(f"334 {self.auth.challenge}")
            return True
        await self.send("504 unsupported mechanism")
        return True

    async def _finish_plain(self, payload_b64: str) -> bool:
        try:
            raw = base64.b64decode(payload_b64.strip())
            _authzid, authcid, password = raw.split(b"\x00", 2)
        except Exception:
            await self.send("535 malformed")
            return True
        user = authcid.decode("utf-8", "replace")
        if self.state.users.get(user.lower(), self.state.users.get(user)) == password.decode(
            "utf-8", "replace"
        ):
            self.auth.user = user
            self.auth.awaiting = None
            await self.send("235 authenticated")
        else:
            await self.send("535 authentication failed")
        return True

    async def _handle_data_line(self, line: bytes) -> bool:
        if line == b".":
            raw = _CRLF.join(self._data_lines)
            self._data_lines = []
            self.in_data = False
            for recipient in self.recipients:
                self.state.record_delivery(self.sender, recipient, raw)
                self.state.log_sent(self.sender, recipient, raw)
            self.sender = ""
            self.recipients = []
            await self.send("250 OK message accepted")
            return False
        if line.startswith(b"."):
            line = line[1:]
        self._data_lines.append(line)
        return False

    async def _handle_auth_line(self, line: bytes) -> bool:
        """Continuation lines after a 334 during AUTH."""
        text = line.decode("utf-8", "replace").strip()
        if text == "*":
            self.auth.awaiting = None
            await self.send("501 cancelled")
            return False
        if self.auth.awaiting == "PLAIN":
            return await self._finish_plain(text)
        if self.auth.awaiting == "LOGIN":
            try:
                user = base64.b64decode(text.strip()).decode("utf-8", "replace")
            except Exception:
                await self.send("535 malformed")
                return True
            self.auth.user = user  # staged only; not authenticated yet
            self.auth.awaiting = "LOGIN-PASS"
            self.auth.challenge = base64.b64encode(b"Password:").decode("ascii")
            await self.send(f"334 {self.auth.challenge}")
            return False
        if self.auth.awaiting == "LOGIN-PASS":
            try:
                password = base64.b64decode(text.strip()).decode("utf-8", "replace")
            except Exception:
                await self.send("535 malformed")
                return True
            staged_user = self.auth.user or ""
            self.auth.user = None
            self.auth.awaiting = None
            if (
                self.state.users.get(staged_user.lower(), self.state.users.get(staged_user, ""))
                == password
            ):
                self.auth.user = staged_user
                await self.send("235 authenticated")
            else:
                await self.send("535 authentication failed")
            return False
        if self.auth.awaiting == "CRAM-MD5":
            try:
                decoded = base64.b64decode(text.strip()).decode("utf-8", "replace")
                user, digest = decoded.rsplit(" ", 1)
            except Exception:
                await self.send("535 malformed")
                return True
            stored = self.state.users.get(user.lower(), self.state.users.get(user, ""))
            challenge_raw = base64.b64decode(self.auth.challenge.strip())
            expected = hmac.new(stored.encode("utf-8"), challenge_raw, hashlib.md5).hexdigest()
            self.auth.awaiting = None
            if hmac.compare_digest(digest, expected):
                self.auth.user = user
                await self.send("235 authenticated")
            else:
                await self.send("535 authentication failed")
            return False
        await self.send("502 unexpected")
        return False


# Patch: route DATA-mode AUTH continuation lines through _handle_auth_line.


async def handle_smtp_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: MailboxServerState,
    hostname: str,
) -> None:
    session = SmtpSession(reader, writer, state, hostname)
    try:
        await session.run()
    except Exception:
        log.exception("SMTP session error")
        try:
            writer.close()
        except Exception:
            pass


# =====================================================================
# POP3 server (USER/PASS/APOP, LIST/RETR/DELE/STAT/UIDL/NOOP/RSET/QUIT)
# =====================================================================


class PopSession:
    """One POP3 client session."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        state: MailboxServerState,
        hostname: str,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.state = state
        self.hostname = hostname
        self.lines = _LineReader(reader)
        self.greeting = (
            f"+OK FengTang POP3 ready <{secrets.token_hex(8)}.{int(time.time())}@{hostname}>"
        )
        self.user: str | None = None
        self.authenticated = False
        self.maildrop: list[tuple[str, bytes]] = []
        self.deleted: list[int] = []
        self._auth_crud: dict[str, Any] = {}

    async def send(self, text: str) -> None:
        self.writer.write(text.encode("utf-8") + _CRLF)
        await self.writer.drain()

    async def send_raw(self, data: bytes) -> None:
        self.writer.write(data + _CRLF)
        await self.writer.drain()

    async def run(self) -> None:
        await self.send(self.greeting)
        while True:
            try:
                line = await self.lines.read_line()
            except (asyncio.TimeoutError, ConnectionResetError):
                break
            text = line.decode("utf-8", "replace")
            try:
                if not await self._handle_command(text):
                    break
            except ServerError as exc:
                await self.send(f"-ERR {exc}")
        try:
            self.writer.close()
        except Exception:
            pass

    def _load_maildrop(self) -> None:
        rows = self.state.store.list_messages(folder="INBOX", limit=10_000)
        self.maildrop = []
        for row in rows:
            raw = self.state.store.get_raw(row["id"])
            self.maildrop.append((row.get("uid") or str(row["id"]), raw))
        self.deleted = []

    async def _handle_command(self, text: str) -> bool:
        verb, _, rest = text.partition(" ")
        verb_u = verb.upper()
        if verb_u == "QUIT":
            await self.send("+OK Bye")
            return False
        if verb_u == "CAPA":
            await self.send("+OK capability list follows")
            for cap in ("USER", "TOP", "UIDL", "PIPELINING", "IMPLEMENTATION FengTang"):
                await self.send(cap)
            await self.send(".")
            return True
        if verb_u == "NOOP":
            await self.send("+OK")
            return True
        if verb_u == "USER":
            self.user = rest.strip()
            await self.send("+OK user accepted")
            return True
        if verb_u == "PASS":
            if not self.user:
                await self.send("-ERR USER first")
                return True
            stored = self.state.users.get(self.user.lower(), self.state.users.get(self.user, ""))
            if stored is not None and hmac.compare_digest(stored, rest):
                self.authenticated = True
                self._load_maildrop()
                await self.send(f"+OK maildrop has {len(self.maildrop)} messages")
            else:
                await self.send("-ERR authentication failed")
            return True
        if verb_u == "APOP":
            parts = rest.split()
            if len(parts) != 2:
                await self.send("-ERR syntax")
                return True
            user, digest = parts
            stored = self.state.users.get(user.lower(), self.state.users.get(user, ""))
            if stored is None:
                await self.send("-ERR no such user")
                return True
            ts = extract_apop_timestamp(self.greeting) or ""
            expected = hashlib.md5(ts.encode() + stored.encode("utf-8")).hexdigest()
            if hmac.compare_digest(digest, expected):
                self.user = user
                self.authenticated = True
                self._load_maildrop()
                await self.send(f"+OK maildrop has {len(self.maildrop)} messages")
            else:
                await self.send("-ERR authentication failed")
            return True
        if not self.authenticated:
            await self.send("-ERR authenticate first")
            return True
        if verb_u == "STAT":
            count = len(self.maildrop) - len(self.deleted)
            size = sum(
                len(raw) for i, (_u, raw) in enumerate(self.maildrop, 1) if i not in self.deleted
            )
            await self.send(f"+OK {count} {size}")
            return True
        if verb_u == "LIST":
            if rest.strip():
                idx = int(rest)
                if 1 <= idx <= len(self.maildrop) and idx not in self.deleted:
                    await self.send(f"+OK {idx} {len(self.maildrop[idx - 1][1])}")
                else:
                    await self.send("-ERR no such message")
                return True
            await self.send(f"+OK {len(self.maildrop) - len(self.deleted)} messages")
            for i, (_u, raw) in enumerate(self.maildrop, 1):
                if i not in self.deleted:
                    await self.send(f"{i} {len(raw)}")
            await self.send(".")
            return True
        if verb_u == "UIDL":
            if rest.strip():
                idx = int(rest)
                if 1 <= idx <= len(self.maildrop) and idx not in self.deleted:
                    await self.send(f"+OK {idx} {self.maildrop[idx - 1][0]}")
                else:
                    await self.send("-ERR no such message")
                return True
            await self.send("+OK unique-id listing follows")
            for i, (uid, _raw) in enumerate(self.maildrop, 1):
                if i not in self.deleted:
                    await self.send(f"{i} {uid}")
            await self.send(".")
            return True
        if verb_u == "RETR":
            idx = int(rest)
            if not (1 <= idx <= len(self.maildrop)) or idx in self.deleted:
                await self.send("-ERR no such message")
                return True
            raw = self.maildrop[idx - 1][1]
            await self.send(f"+OK {len(raw)} octets")
            # dot-stuffing on send
            payload = _CRLF.join(
                (b"." + chunk if chunk.startswith(b".") else chunk) for chunk in raw.split(b"\r\n")
            )
            await self.send_raw(payload)
            await self.send(".")
            return True
        if verb_u == "TOP":
            parts = rest.split()
            idx, n_lines = int(parts[0]), int(parts[1])
            if not (1 <= idx <= len(self.maildrop)) or idx in self.deleted:
                await self.send("-ERR no such message")
                return True
            raw = self.maildrop[idx - 1][1]
            head, _, body = raw.partition(b"\r\n\r\n")
            body_lines = body.split(b"\r\n")[:n_lines]
            chunk = head + _CRLF + _CRLF + _CRLF.join(body_lines)
            await self.send(f"+OK top of message {idx}")
            await self.send_raw(chunk)
            await self.send(".")
            return True
        if verb_u == "DELE":
            idx = int(rest)
            if 1 <= idx <= len(self.maildrop) and idx not in self.deleted:
                self.deleted.append(idx)
                await self.send(f"+OK message {idx} deleted")
            else:
                await self.send("-ERR no such message")
            return True
        if verb_u == "RSET":
            self.deleted = []
            await self.send("+OK")
            return True
        await self.send("-ERR unknown command")
        return True


async def handle_pop_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: MailboxServerState,
    hostname: str,
) -> None:
    session = PopSession(reader, writer, state, hostname)
    try:
        await session.run()
    except Exception:
        log.exception("POP3 session error")
        try:
            writer.close()
        except Exception:
            pass


# =====================================================================
# Server lifecycle
# =====================================================================


class MailServer:
    """Runs both SMTP and POP3 listeners; returns when stop() is awaited/called."""

    def __init__(
        self,
        db_path: Path,
        smtp_host: str = "127.0.0.1",
        smtp_port: int = 2525,
        pop_host: str = "127.0.0.1",
        pop_port: int = 1110,
        domain: str = "localhost",
        users: dict[str, str] | None = None,
        require_auth: bool = False,
    ) -> None:
        self.smtp_host, self.smtp_port = smtp_host, smtp_port
        self.pop_host, self.pop_port = pop_host, pop_port
        self.domain = domain
        self.store = Store(db_path)
        self.state = MailboxServerState(
            self.store,
            [domain, smtp_host],
            users=users,
            require_auth=require_auth,
        )
        self._smtp_server: asyncio.AbstractServer | None = None
        self._pop_server: asyncio.AbstractServer | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._smtp_server = await asyncio.start_server(
            lambda r, w: handle_smtp_client(r, w, self.state, self.domain),
            self.smtp_host,
            self.smtp_port,
        )
        self._pop_server = await asyncio.start_server(
            lambda r, w: handle_pop_client(r, w, self.state, self.domain),
            self.pop_host,
            self.pop_port,
        )

    async def serve_forever(self) -> None:
        if self._smtp_server is None or self._pop_server is None:
            await self.start()
        assert self._smtp_server is not None and self._pop_server is not None
        async with self._smtp_server, self._pop_server:
            await asyncio.gather(
                self._smtp_server.serve_forever(),
                self._pop_server.serve_forever(),
            )

    def stop(self) -> None:
        for server in (self._smtp_server, self._pop_server):
            if server is not None:
                server.close()

    @property
    def addresses(self) -> dict[str, tuple[str, int]]:
        return {
            "smtp": (self.smtp_host, self.smtp_port),
            "pop3": (self.pop_host, self.pop_port),
        }


def serve_blocking(
    db_path: Path,
    smtp_port: int = 2525,
    pop_port: int = 1110,
    host: str = "127.0.0.1",
    domain: str = "localhost",
    users: dict[str, str] | None = None,
) -> None:
    """Blocking entry point used by `fengtang serve`."""
    try:
        asyncio.run(
            MailServer(
                db_path,
                smtp_host=host,
                smtp_port=smtp_port,
                pop_host=host,
                pop_port=pop_port,
                domain=domain,
                users=users,
            ).serve_forever()
        )
    except KeyboardInterrupt:
        pass


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) != 0
