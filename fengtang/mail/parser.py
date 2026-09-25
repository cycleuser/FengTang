"""Message building, parsing, and rendering (pure stdlib `email` package)."""

from __future__ import annotations

import email
import email.header
import email.policy
import email.utils
import html
import quopri
import secrets
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from fengtang.core.errors import MessageError


@dataclass
class Attachment:
    """A decoded attachment ready for saving."""

    filename: str
    content_type: str
    size: int
    content: bytes = b""


@dataclass
class ParsedMessage:
    """A mail message parsed into plain data (agent-friendly)."""

    id: int = 0
    uid: str = ""
    folder: str = "INBOX"
    message_id: str = ""
    subject: str = ""
    from_: str = ""
    to: str = ""
    cc: str = ""
    reply_to: str = ""
    date: str = ""
    date_ts: float = 0.0
    flags: list[str] = field(default_factory=list)
    seen: bool = False
    answered: bool = False
    flagged: bool = False
    deleted: bool = False
    draft: bool = False
    size: int = 0
    text_body: str = ""
    html_body: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    in_reply_to: str = ""
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "uid": self.uid,
            "folder": self.folder,
            "message_id": self.message_id,
            "subject": self.subject,
            "from": self.from_,
            "to": self.to,
            "cc": self.cc,
            "reply_to": self.reply_to,
            "date": self.date,
            "date_ts": self.date_ts,
            "flags": self.flags,
            "seen": self.seen,
            "answered": self.answered,
            "flagged": self.flagged,
            "deleted": self.deleted,
            "draft": self.draft,
            "size": self.size,
            "text_body": self.text_body,
            "html_body": self.html_body,
            "attachments": self.attachments,
            "in_reply_to": self.in_reply_to,
        }


# ---------- building ----------


def build_message(
    from_addr: str,
    to_addrs: list[str],
    subject: str,
    body: str,
    cc_addrs: list[str] | None = None,
    bcc_addrs: list[str] | None = None,
    reply_to: str | None = None,
    in_reply_to: str | None = None,
    attachments: list[tuple[str, str]] | None = None,
    html_body: str | None = None,
) -> EmailMessage:
    """Build a fully-formed EmailMessage with optional alternative/attachment parts.

    attachments: list of (path, filename) pairs.
    """
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    if cc_addrs:
        msg["Cc"] = ", ".join(cc_addrs)
    if bcc_addrs:
        msg["Bcc"] = ", ".join(bcc_addrs)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(
        domain=from_addr.rsplit("@", 1)[-1],
        idstring=secrets.token_hex(8),
    )
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        refs = in_reply_to
        msg["References"] = refs
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    for path_str, filename in attachments or []:
        path = Path(path_str).expanduser()
        if not path.is_file():
            raise MessageError(f"Attachment not found: {path}")
        import mimetypes

        ctype, _encoding = mimetypes.guess_type(path.name)
        if ctype is None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        data = path.read_bytes()
        msg.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=filename or path.name,
        )
    return msg


# ---------- parsing ----------


def decode_header(value: str | None) -> str:
    """Decode RFC2047 headers robustly."""
    if not value:
        return ""
    try:
        parts = email.header.decode_header(value)
        out = []
        for text, charset in parts:
            if isinstance(text, bytes):
                out.append(text.decode(charset or "utf-8", errors="replace"))
            else:
                out.append(text)
        return "".join(out)
    except Exception:
        return value


def _part_to_text(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _walk_parts(
    msg: email.message.Message,
    attachments: list[dict[str, Any]],
    texts: dict[str, str],
    depth: int = 0,
) -> None:
    if depth > 20:
        return
    ctype = msg.get_content_type()
    disp = (msg.get_content_disposition() or "").lower()
    if msg.is_multipart():
        for sub in msg.get_payload():
            if isinstance(sub, email.message.Message):
                _walk_parts(sub, attachments, texts, depth + 1)
        return
    if disp == "attachment" or (disp == "inline" and msg.get_filename()):
        payload = msg.get_payload(decode=True) or b""
        attachments.append(
            {
                "filename": decode_header(msg.get_filename()) or f"attachment-{len(attachments)}",
                "content_type": ctype,
                "size": len(payload),
            }
        )
        return
    if ctype == "text/plain" and "text" not in texts:
        texts["text"] = _part_to_text(msg)
    elif ctype == "text/html":
        texts["html"] = texts.get("html") or _part_to_text(msg)


def parse_message(raw: bytes | str = b"") -> ParsedMessage:
    """Parse raw RFC822 bytes/str into a ParsedMessage."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8", errors="replace")
    if not raw:
        raise MessageError("Empty message payload")
    try:
        msg = email.message_from_bytes(raw)
    except Exception as exc:
        raise MessageError(f"Unparseable message: {exc}") from exc

    parsed = ParsedMessage()
    parsed.message_id = (msg.get("Message-ID") or "").strip()
    parsed.subject = decode_header(msg.get("Subject"))
    parsed.from_ = decode_header(msg.get("From"))
    parsed.to = decode_header(msg.get("To"))
    parsed.cc = decode_header(msg.get("Cc"))
    parsed.reply_to = decode_header(msg.get("Reply-To"))
    parsed.in_reply_to = (msg.get("In-Reply-To") or "").strip()
    date_hdr = msg.get("Date")
    parsed.date = date_hdr or ""
    if date_hdr:
        try:
            dt = email.utils.parsedate_to_datetime(date_hdr)
            parsed.date_ts = dt.timestamp() if dt else 0.0
        except (TypeError, ValueError):
            parsed.date_ts = 0.0
    parsed.size = len(raw)

    attachments: list[dict[str, Any]] = []
    texts: dict[str, str] = {}
    _walk_parts(msg, attachments, texts)
    parsed.text_body = texts.get("text", "")
    parsed.html_body = texts.get("html", "")
    parsed.attachments = attachments
    parsed.raw = raw.decode("utf-8", errors="replace")
    if not parsed.text_body and not parsed.html_body and not attachments:
        # Non-MIME singlepart message
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            parsed.text_body = payload.decode("utf-8", errors="replace")
    return parsed


# ---------- html fallback ----------
def html_to_text(html_text: str) -> str:
    """Very small HTML->text fallback when no plain part exists."""
    text = html.unescape(html_text)
    # Strip tags crudely but safely for display purposes.
    out: list[str] = []
    skip_depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "<":
            end = text.find(">", i)
            if end == -1:
                break
            tag = text[i + 1 : end].strip().lower()
            if tag.startswith(("script", "style")):
                skip_depth += 1
            elif tag.startswith(("/script", "/style")):
                skip_depth = max(0, skip_depth - 1)
            elif tag.startswith(("br", "p", "div", "tr", "li")) and skip_depth == 0:
                out.append("\n")
            i = end + 1
            continue
        if skip_depth == 0:
            out.append(ch)
        i += 1
    return "".join(out)


def save_attachments(parsed: ParsedMessage, target_dir: Path) -> list[Path]:
    """Re-decode attachment payloads from `raw` and write them to target_dir."""
    if isinstance(parsed.raw, str):
        raw = parsed.raw.encode("utf-8", errors="replace")
    else:
        raw = parsed.raw
    msg = email.message_from_bytes(raw)
    saved: list[Path] = []
    target_dir.mkdir(parents=True, exist_ok=True)
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disp = (part.get_content_disposition() or "").lower()
        if disp not in ("attachment", "inline"):
            continue
        filename = decode_header(part.get_filename()) or f"attachment-{len(saved)}"
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        dest = target_dir / filename
        counter = 1
        while dest.exists():
            stem, suffix = dest.stem, dest.suffix
            dest = target_dir / f"{stem}-{counter}{suffix}"
            counter += 1
        dest.write_bytes(payload)
        saved.append(dest)
    return saved


def now_rfc2822() -> str:
    return email.utils.formatdate(time.time(), localtime=True)


def quoted_printable_decode(data: bytes) -> bytes:
    return quopri.decodestring(data)
