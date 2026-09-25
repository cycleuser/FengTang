"""Local SQLite message store: folders, messages, flags, full-text-ish search."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from fengtang.core.errors import MessageError
from fengtang.mail.parser import parse_message

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    folder      TEXT NOT NULL DEFAULT 'INBOX',
    message_id  TEXT NOT NULL DEFAULT '',
    uid         TEXT NOT NULL DEFAULT '',
    subject     TEXT NOT NULL DEFAULT '',
    from_addr   TEXT NOT NULL DEFAULT '',
    to_addr     TEXT NOT NULL DEFAULT '',
    cc_addr     TEXT NOT NULL DEFAULT '',
    reply_to    TEXT NOT NULL DEFAULT '',
    date_str    TEXT NOT NULL DEFAULT '',
    date_ts     REAL NOT NULL DEFAULT 0,
    flags       TEXT NOT NULL DEFAULT '[]',
    size        INTEGER NOT NULL DEFAULT 0,
    text_body   TEXT NOT NULL DEFAULT '',
    html_body   TEXT NOT NULL DEFAULT '',
    in_reply_to TEXT NOT NULL DEFAULT '',
    raw         BLOB NOT NULL,
    fetched_at  REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_folder ON messages(folder);
CREATE INDEX IF NOT EXISTS idx_messages_message_id ON messages(message_id);
CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(date_ts);
"""

FLAG_MAP = {
    "seen": "\\Seen",
    "answered": "\\Answered",
    "flagged": "\\Flagged",
    "deleted": "\\Deleted",
    "draft": "\\Draft",
}

SYSTEM_FLAG_TO_KEY = {v: k for k, v in FLAG_MAP.items()}


def flags_to_system(flags: Iterable[str]) -> list[str]:
    out = []
    for f in flags:
        out.append(FLAG_MAP.get(f, f) if not f.startswith("\\") else f)
    return out


class Store:
    """Thread-safe wrapper around a SQLite message database."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- write ----------

    def store(
        self, raw: bytes, folder: str = "INBOX", uid: str = "", extra_flags: list[str] | None = None
    ) -> int:
        """Store a raw message; de-duplicates by (folder, message_id)."""
        parsed = parse_message(raw)
        with self._lock:
            if parsed.message_id:
                row = self._conn.execute(
                    "SELECT id FROM messages WHERE folder=? AND message_id=? AND message_id!=''",
                    (folder, parsed.message_id),
                ).fetchone()
                if row:
                    return int(row["id"])
            flags = list(dict.fromkeys(parsed.flags + (extra_flags or [])))
            cursor = self._conn.execute(
                """INSERT INTO messages
                   (folder, message_id, uid, subject, from_addr, to_addr, cc_addr,
                    reply_to, date_str, date_ts, flags, size, text_body, html_body,
                    in_reply_to, raw, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    folder,
                    parsed.message_id,
                    uid,
                    parsed.subject,
                    parsed.from_,
                    parsed.to,
                    parsed.cc,
                    parsed.reply_to,
                    parsed.date,
                    parsed.date_ts,
                    json.dumps(flags),
                    parsed.size,
                    parsed.text_body,
                    parsed.html_body,
                    parsed.in_reply_to,
                    sqlite3.Binary(raw),
                    time.time(),
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid or 0)

    def set_flags(self, message_id: int, flags: list[str], mode: str = "replace") -> bool:
        """mode: replace | add | remove."""
        with self._lock:
            row = self._conn.execute(
                "SELECT flags FROM messages WHERE id=?", (message_id,)
            ).fetchone()
            if row is None:
                raise MessageError(f"Message id {message_id} not found in local store")
            current: list[str] = json.loads(row["flags"])
            if mode == "replace":
                new_flags = list(dict.fromkeys(flags))
            elif mode == "add":
                new_flags = list(dict.fromkeys(current + flags))
            else:
                new_flags = [f for f in current if f not in flags]
            self._conn.execute(
                "UPDATE messages SET flags=? WHERE id=?", (json.dumps(new_flags), message_id)
            )
            self._conn.commit()
            return True

    def delete(self, message_ids: list[int]) -> int:
        with self._lock:
            placeholders = ",".join("?" * len(message_ids))
            cursor = self._conn.execute(
                f"DELETE FROM messages WHERE id IN ({placeholders})", message_ids
            )
            self._conn.commit()
            return int(cursor.rowcount)

    def move(self, message_ids: list[int], target_folder: str) -> int:
        with self._lock:
            placeholders = ",".join("?" * len(message_ids))
            cursor = self._conn.execute(
                f"UPDATE messages SET folder=? WHERE id IN ({placeholders})",
                [target_folder, *(str(i) for i in message_ids)],
            )
            self._conn.commit()
            return int(cursor.rowcount)

    # ---------- read ----------

    def list_messages(
        self,
        folder: str | None = None,
        limit: int = 50,
        offset: int = 0,
        unread_only: bool = False,
        flagged_only: bool = False,
        with_body: bool = False,
    ) -> list[dict[str, Any]]:
        query = [
            "SELECT id, folder, message_id, uid, subject, from_addr, to_addr, cc_addr,"
            " date_str, date_ts, flags, size, in_reply_to"
        ]
        if with_body:
            query.append(", text_body, html_body")
        query.append(" FROM messages")
        clauses: list[str] = []
        params: list[Any] = []
        if folder:
            clauses.append("folder=?")
            params.append(folder)
        if unread_only:
            clauses.append("flags NOT LIKE '%\\\\Seen%'")
        if flagged_only:
            clauses.append("flags LIKE '%\\\\Flagged%'")
        if clauses:
            query.append(" WHERE " + " AND ".join(clauses))
        query.append(" ORDER BY date_ts DESC, id DESC LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        rows = self._conn.execute("".join(query), params).fetchall()
        return [self._row_to_dict(row, with_body=with_body) for row in rows]

    def get_message(self, message_id: int, with_body: bool = True) -> dict[str, Any]:
        columns = (
            "id, folder, message_id, uid, subject, from_addr, to_addr, cc_addr,"
            " reply_to, date_str, date_ts, flags, size, in_reply_to, raw"
        )
        if with_body:
            columns += ", text_body, html_body"
        row = self._conn.execute(
            f"SELECT {columns} FROM messages WHERE id=?", (message_id,)
        ).fetchone()
        if row is None:
            raise MessageError(f"Message id {message_id} not found in local store")
        return self._row_to_dict(row, with_body=with_body, include_raw=True)

    def get_raw(self, message_id: int) -> bytes:
        row = self._conn.execute("SELECT raw FROM messages WHERE id=?", (message_id,)).fetchone()
        if row is None:
            raise MessageError(f"Message id {message_id} not found in local store")
        return bytes(row["raw"])

    def search(
        self,
        query: str,
        folder: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Substring search across subject/from/to/body (SQLite LIKE, case-insensitive)."""
        pattern = f"%{query}%"
        sql = """SELECT id, folder, message_id, uid, subject, from_addr, to_addr,
                        date_str, date_ts, flags, size
                 FROM messages
                 WHERE (subject LIKE ? OR from_addr LIKE ? OR to_addr LIKE ?
                        OR text_body LIKE ? OR raw LIKE ?)"""
        params: list[Any] = [pattern] * 5
        if folder:
            sql += " AND folder=?"
            params.append(folder)
        sql += " ORDER BY date_ts DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def folders(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT folder, COUNT(*) AS total,
                      SUM(CASE WHEN flags NOT LIKE '%\\\\Seen%' THEN 1 ELSE 0 END) AS unread
               FROM messages GROUP BY folder ORDER BY folder"""
        ).fetchall()
        return [
            {"name": r["folder"], "total": r["total"], "unread": r["unread"] or 0} for r in rows
        ]

    def count(self, folder: str | None = None) -> int:
        if folder:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE folder=?", (folder,)
            ).fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()
        return int(row["n"])

    # ---------- helpers ----------

    @staticmethod
    def _row_to_dict(
        row: sqlite3.Row, with_body: bool = False, include_raw: bool = False
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": row["id"],
            "folder": row["folder"],
            "message_id": row["message_id"],
            "uid": row["uid"],
            "subject": row["subject"],
            "from": row["from_addr"],
            "to": row["to_addr"],
            "cc": row["cc_addr"] if "cc_addr" in row.keys() else "",
            "date": row["date_str"],
            "date_ts": row["date_ts"],
            "flags": json.loads(row["flags"]),
            "seen": "\\Seen" in json.loads(row["flags"]),
            "flagged": "\\Flagged" in json.loads(row["flags"]),
            "size": row["size"],
        }
        if "reply_to" in row.keys():
            data["reply_to"] = row["reply_to"]
        if "in_reply_to" in row.keys():
            data["in_reply_to"] = row["in_reply_to"]
        if with_body and "text_body" in row.keys():
            data["text_body"] = row["text_body"]
            data["html_body"] = row["html_body"]
        if include_raw:
            raw = row["raw"]
            data["raw"] = bytes(raw).decode("utf-8", errors="replace") if raw else ""
        return data
