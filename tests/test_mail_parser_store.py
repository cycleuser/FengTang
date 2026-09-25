"""Tests for MIME parsing/building and the SQLite store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fengtang.core.errors import MessageError
from fengtang.mail.parser import (
    build_message,
    decode_header,
    html_to_text,
    parse_message,
    save_attachments,
)
from fengtang.mail.store import Store

RAW_SIMPLE = (
    b"From: Alice <alice@example.com>\r\n"
    b"To: bob@example.com\r\n"
    b"Subject: Hello\r\n"
    b"Date: Mon, 24 Sep 2026 10:00:00 +0000\r\n"
    b"Message-ID: <abc@example.com>\r\n"
    b"\r\n"
    b"Plain body line.\r\n"
)

RAW_MIME_CJK = (
    "From: =?utf-8?B?5byg5LiJ?= <alice@example.com>\r\n"
    "To: bob@example.com\r\n"
    "Subject: =?utf-8?B?5L2g5aW9?=\r\n"
    "MIME-Version: 1.0\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "\r\n"
    "测试正文内容\r\n"
).encode()


def raw_multipart() -> bytes:
    import base64

    attachment = base64.b64encode(b"PDFDATA").decode()
    return (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: with-attachment\r\n"
        b"Message-ID: <multi@example.com>\r\n"
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="BOUND"\r\n'
        b"\r\n"
        b"--BOUND\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"see attached\r\n"
        b"--BOUND\r\n"
        b"Content-Type: application/pdf; name=doc.pdf\r\n"
        b"Content-Disposition: attachment; filename=doc.pdf\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n" + attachment.encode() + b"\r\n"
        b"--BOUND--\r\n"
    )


class TestBuildMessage:
    def test_basic_headers(self) -> None:
        msg = build_message("me@x.com", ["you@y.com"], "Hi", "Body")
        assert msg["From"] == "me@x.com"
        assert msg["To"] == "you@y.com"
        assert msg["Subject"] == "Hi"
        assert "Body" in msg.get_content()

    def test_attachment_missing_raises(self) -> None:
        with pytest.raises(MessageError):
            build_message(
                "me@x.com",
                ["you@y.com"],
                "s",
                "b",
                attachments=[("/nonexistent/file.bin", "")],
            )

    def test_attachment_content(self, tmp_path: Path) -> None:
        p = tmp_path / "notes.txt"
        p.write_text("attachment content", encoding="utf-8")
        msg = build_message(
            "me@x.com",
            ["you@y.com"],
            "s",
            "b",
            attachments=[(str(p), "renamed.txt")],
        )
        parts = list(msg.iter_attachments())
        assert len(parts) == 1
        assert parts[0].get_filename() == "renamed.txt"

    def test_html_alternative(self) -> None:
        msg = build_message("me@x.com", ["you@y.com"], "s", "plain", html_body="<b>bold</b>")
        assert msg.is_multipart()


class TestParseMessage:
    def test_simple(self) -> None:
        parsed = parse_message(RAW_SIMPLE)
        assert parsed.subject == "Hello"
        assert parsed.from_ == "Alice <alice@example.com>"
        assert parsed.text_body.strip() == "Plain body line."
        assert parsed.message_id == "<abc@example.com>"
        assert parsed.size == len(RAW_SIMPLE)

    def test_cjk_headers(self) -> None:
        parsed = parse_message(RAW_MIME_CJK)
        assert parsed.subject == "你好"
        assert "张" in parsed.from_  # RFC2047 display name decoded
        assert "测试正文" in parsed.text_body

    def test_multipart_attachment(self) -> None:
        parsed = parse_message(raw_multipart())
        assert len(parsed.attachments) == 1
        assert parsed.attachments[0]["filename"] == "doc.pdf"
        assert parsed.attachments[0]["size"] == len(b"PDFDATA")
        assert parsed.text_body.strip() == "see attached"

    def test_empty_raises(self) -> None:
        with pytest.raises(MessageError):
            parse_message(b"")

    def test_html_to_text(self) -> None:
        text = html_to_text("<p>Hello <b>world</b></p><script>bad()</script>")
        assert "Hello" in text and "world" in text
        assert "bad()" not in text

    def test_decode_header_plain(self) -> None:
        assert decode_header("plain") == "plain"
        assert decode_header(None) == ""


class TestStore:
    def test_store_and_get(self, tmp_path: Path) -> None:
        store = Store(tmp_path / "t.db")
        try:
            message_id = store.store(RAW_SIMPLE)
            assert message_id > 0
            data = store.get_message(message_id)
            assert data["subject"] == "Hello"
            assert data["seen"] is False
        finally:
            store.close()

    def test_dedupe_by_message_id(self, tmp_path: Path) -> None:
        store = Store(tmp_path / "t.db")
        try:
            first = store.store(RAW_SIMPLE)
            second = store.store(RAW_SIMPLE)
            assert first == second
            assert store.count() == 1
        finally:
            store.close()

    def test_flags_flow(self, tmp_path: Path) -> None:
        store = Store(tmp_path / "t.db")
        try:
            message_id = store.store(RAW_SIMPLE)
            store.set_flags(message_id, ["\\Seen"], mode="add")
            assert store.get_message(message_id)["seen"] is True
            store.set_flags(message_id, ["\\Seen"], mode="remove")
            assert store.get_message(message_id)["seen"] is False
        finally:
            store.close()

    def test_search(self, tmp_path: Path) -> None:
        store = Store(tmp_path / "t.db")
        try:
            store.store(RAW_SIMPLE)
            hits = store.search("Hello")
            assert len(hits) == 1
            assert store.search("nonexistent-xyz") == []
        finally:
            store.close()

    def test_delete_and_move(self, tmp_path: Path) -> None:
        store = Store(tmp_path / "t.db")
        try:
            message_id = store.store(RAW_SIMPLE)
            assert store.move([message_id], "Archive") == 1
            assert store.get_message(message_id)["folder"] == "Archive"
            assert store.delete([message_id]) == 1
            assert store.count() == 0
        finally:
            store.close()

    def test_folders_summary(self, tmp_path: Path) -> None:
        store = Store(tmp_path / "t.db")
        try:
            store.store(RAW_SIMPLE, folder="INBOX")
            folders = store.folders()
            assert folders[0]["name"] == "INBOX"
            assert folders[0]["total"] == 1
            assert folders[0]["unread"] == 1
        finally:
            store.close()

    def test_raw_roundtrip(self, tmp_path: Path) -> None:
        store = Store(tmp_path / "t.db")
        try:
            message_id = store.store(RAW_SIMPLE)
            assert store.get_raw(message_id) == RAW_SIMPLE
        finally:
            store.close()

    def test_attachment_saving(self, tmp_path: Path) -> None:
        parsed = parse_message(raw_multipart())
        target = tmp_path / "att"
        saved = save_attachments(parsed, target)
        assert len(saved) == 1
        assert saved[0].read_bytes() == b"PDFDATA"

    def test_corrupt_db_raises_cleanly(self, tmp_path: Path) -> None:
        db = tmp_path / "bad.db"
        db.write_bytes(b"this is not sqlite" * 100)
        with pytest.raises(sqlite3.DatabaseError):
            Store(db)
