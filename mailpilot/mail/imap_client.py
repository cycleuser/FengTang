"""IMAP client for fetching, searching, and flag management (imaplib-based)."""

from __future__ import annotations

import base64
import imaplib
import ssl
from typing import Any

from mailpilot.core.auth import b64encode, cram_md5_response, xoauth2_string
from mailpilot.core.config import Account
from mailpilot.core.errors import AuthError, ConnectionError_, MessageError


def _connect(account: Account) -> Any:
    client: Any
    try:
        if account.imap_ssl:
            client = imaplib.IMAP4_SSL(
                account.imap_host,
                account.imap_port,
                ssl_context=ssl.create_default_context(),
            )
        else:
            client = imaplib.IMAP4(account.imap_host, account.imap_port)
            if account.imap_starttls:
                client.starttls(ssl.create_default_context())
        return client
    except (OSError, imaplib.IMAP4.error, ssl.SSLError) as exc:
        raise ConnectionError_(
            f"Cannot connect to IMAP {account.imap_host}:{account.imap_port}: {exc}"
        ) from exc


def _imap_login(client: imaplib.IMAP4, account: Account) -> None:
    auth = account.auth.lower()
    capabilities = {c.upper() for c in (client.capabilities or [])}

    def try_plain() -> bool:
        if "AUTH=PLAIN" not in capabilities:
            return False
        payload = b64encode(b"\x00" + account.email.encode() + b"\x00" + account.password.encode())
        try:
            typ, _ = client.authenticate("PLAIN", lambda x: payload.encode("ascii"))
        except imaplib.IMAP4.error:
            return False
        return typ == "OK"

    def try_login() -> bool:
        try:
            typ, _ = client.login(account.email, account.password)
        except imaplib.IMAP4.error:
            return False
        return typ == "OK"

    def try_cram_md5() -> bool:
        if "AUTH=CRAM-MD5" not in capabilities:
            return False
        try:
            typ, data = client.authenticate(
                "CRAM-MD5",
                lambda challenge: cram_md5_response(
                    account.email,
                    account.password,
                    base64.b64encode(challenge).decode("ascii")
                    if not challenge.endswith(b"=")
                    else challenge.decode("ascii"),
                ).encode("utf-8"),
            )
        except (imaplib.IMAP4.error, ValueError):
            return False
        return typ == "OK"

    def try_xoauth2() -> bool:
        if "AUTH=XOAUTH2" not in capabilities or not account.oauth2_token:
            return False
        try:
            typ, _ = client.authenticate(
                "XOAUTH2",
                lambda x: xoauth2_string(account.email, account.oauth2_token),
            )
        except imaplib.IMAP4.error:
            return False
        return typ == "OK"

    chain: tuple[Any, ...]
    if auth == "auto":
        chain = (try_xoauth2, try_cram_md5, try_plain, try_login)
    else:
        mapping = {
            "plain": (try_plain, try_login),
            "login": (try_login, try_plain),
            "cram-md5": (try_cram_md5,),
            "xoauth2": (try_xoauth2,),
        }
        chain = mapping.get(auth, (try_login, try_plain))  # type: ignore[assignment]
    for attempt in chain:
        if attempt():
            return
    raise AuthError(f"IMAP login failed for {account.email} on {account.imap_host}")


class ImapClient:
    """Context-managed IMAP session."""

    def __init__(self, account: Account) -> None:
        self.account = account
        self._client: imaplib.IMAP4 | None = None

    def __enter__(self) -> ImapClient:
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def connect(self) -> None:
        self._client = _connect(self.account)
        _imap_login(self._client, self.account)

    def close(self) -> None:
        if self._client:
            try:
                self._client.logout()
            except Exception:
                pass
            self._client = None

    @property
    def client(self) -> imaplib.IMAP4:
        if self._client is None:
            raise ConnectionError_("IMAP not connected")
        return self._client

    # ---------- folders ----------

    def list_folders(self) -> list[dict[str, Any]]:
        typ, data = self.client.list()
        if typ != "OK":
            return []
        folders = []
        for line in data or []:
            text = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)
            parts = text.split(' "/" ')
            name = parts[-1].strip('"') if parts else text
            flags = parts[0].strip("()\\").split() if parts else []
            folders.append(
                {
                    "name": name,
                    "flags": [f for f in flags if f and not f.startswith("/")],
                    "selectable": "\\Noselect" not in text,
                }
            )
        return folders

    def select(self, folder: str = "INBOX", readonly: bool = True) -> int:
        typ, data = self.client.select(folder, readonly=readonly)
        if typ != "OK":
            raise MessageError(f"Cannot select folder {folder!r}: {data}")
        return int(data[0]) if data and data[0] else 0

    # ---------- fetch ----------

    def fetch_all(
        self, folder: str = "INBOX", limit: int = 0, only_unseen: bool = False
    ) -> list[tuple[str, bytes]]:
        """Fetch up to `limit` (0=all) messages; returns [(uid, raw_bytes), ...]."""
        total = self.select(folder)
        if total == 0:
            return []
        criteria = "UNSEEN" if only_unseen else "ALL"
        typ, data = self.client.uid("SEARCH", None, criteria)  # type: ignore[arg-type]
        if typ != "OK" or not data or not data[0]:
            return []
        uids = data[0].split()
        if limit:
            uids = uids[-limit:]
        out: list[tuple[str, bytes]] = []
        for uid in uids:
            uid_str = uid.decode("ascii")
            typ, msg_data = self.client.uid("FETCH", uid_str, "(RFC822)")
            if typ != "OK" or not msg_data:
                continue
            raw = b""
            for item in msg_data:
                if isinstance(item, tuple):
                    raw = item[1]
                    break
            if raw:
                out.append((uid_str, raw))
        return out

    def fetch_flags(self, folder: str = "INBOX") -> dict[str, list[str]]:
        """Return uid -> IMAP flags list for the folder."""
        self.select(folder)
        typ, data = self.client.uid("SEARCH", None, "ALL")  # type: ignore[arg-type]
        if typ != "OK" or not data or not data[0]:
            return {}
        out: dict[str, list[str]] = {}
        for uid in data[0].split():
            typ, msg_data = self.client.uid("FETCH", uid.decode(), "(FLAGS)")
            flags: list[str] = []
            if typ == "OK" and msg_data:
                for item in msg_data:
                    text = item.decode("utf-8", "replace") if isinstance(item, bytes) else ""
                    if "FLAGS" in text:
                        idx = text.find("(")
                        end = text.find(")", idx)
                        raw_flags = text[idx + 1 : end]
                        flags = raw_flags.split() if raw_flags else []
                        break
            out[uid.decode()] = flags
        return out

    # ---------- search (server-side) ----------

    def search(self, folder: str, criteria: str) -> list[str]:
        """Run a raw IMAP SEARCH; returns list of sequence numbers."""
        self.select(folder)
        typ, data = self.client.search(None, criteria)
        if typ != "OK" or not data or not data[0]:
            return []
        seqs: list[str] = list(data[0].decode("ascii").split())
        return seqs

    # ---------- flags ----------

    def store_flags(self, folder: str, uid: str, flags: list[str], mode: str = "replace") -> bool:
        """mode: FLAGS (replace) | +FLAGS (add) | -FLAGS (remove)."""
        self.select(folder, readonly=False)
        op = {"replace": "FLAGS", "add": "+FLAGS", "remove": "-FLAGS"}[mode]
        flag_str = " ".join(flags)
        typ, _ = self.client.uid("STORE", uid, op, flag_str)
        return typ == "OK"

    def expunge(self, folder: str) -> None:
        self.select(folder, readonly=False)
        self.client.expunge()

    # ---------- send-side helpers ----------

    def append(self, folder: str, raw: bytes, flags: list[str] | None = None) -> bool:
        """Append a message to a folder (e.g. Sent)."""
        self.select(folder, readonly=False)
        typ, _ = self.client.append(
            folder,
            " ".join(flags) if flags else None,
            None,
            raw,
        )
        return typ == "OK"


def imap_search_criteria(query: str) -> str:
    """Translate a simple free-text query into an IMAP SEARCH program.

    Supports 'from:foo to:bar subject:baz since:2024-01-01 unseen' plus bare text.
    """
    import re

    tokens = re.findall(r'\S+:"[^"]+"|\S+', query)
    parts: list[str] = []
    bare: list[str] = []
    for token in tokens:
        key, _, value = token.partition(":")
        key_l = key.lower()
        value = value.strip('"')
        if key_l == "from":
            parts.append(f'FROM "{value}"')
        elif key_l == "to":
            parts.append(f'TO "{value}"')
        elif key_l == "subject":
            parts.append(f'SUBJECT "{value}"')
        elif key_l == "body":
            parts.append(f'BODY "{value}"')
        elif key_l == "since":
            parts.append(f"SINCE {value}")
        elif key_l == "before":
            parts.append(f"BEFORE {value}")
        elif key_l == "unseen":
            parts.append("UNSEEN")
        elif key_l == "flagged":
            parts.append("FLAGGED")
        elif key_l == "text":
            bare.append(value)
        else:
            bare.append(token)
    for word in bare:
        parts.append(f'TEXT "{word}"')
    return " ".join(parts) if parts else "ALL"
