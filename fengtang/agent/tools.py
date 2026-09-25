"""OpenAI function-calling tool definitions for agent integration.

TOOLS is the schema list; dispatch() routes a model's tool call to api.py.
Covers: read, send, search, mark, delete, move, fetch, accounts, folders.
"""

from __future__ import annotations

import json
from typing import Any

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "fengtang_send",
            "description": (
                "Send an email via the configured account's SMTP server. "
                "Attachments are [path, filename] pairs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Recipient addresses.",
                    },
                    "subject": {"type": "string", "description": "Subject line."},
                    "body": {"type": "string", "description": "Plain-text body."},
                    "account": {
                        "type": "string",
                        "description": "Account name (default account if omitted).",
                    },
                    "cc": {"type": "array", "items": {"type": "string"}},
                    "bcc": {"type": "array", "items": {"type": "string"}},
                    "html_body": {
                        "type": "string",
                        "description": "Optional HTML alternative body.",
                    },
                    "attachments": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "string"}},
                        "description": "Each item: [file_path, filename].",
                    },
                    "in_reply_to": {
                        "type": "string",
                        "description": "Message-ID being replied to.",
                    },
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fengtang_list",
            "description": "List locally stored messages (optionally one folder), newest first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "description": "Folder name, e.g. INBOX."},
                    "limit": {"type": "integer", "default": 50},
                    "offset": {"type": "integer", "default": 0},
                    "unread_only": {"type": "boolean", "default": False},
                    "flagged_only": {"type": "boolean", "default": False},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fengtang_read",
            "description": (
                "Read one local message by id: headers, body, attachment metadata. "
                "Optionally save attachments to a directory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "Local message id."},
                    "save_attachments_to": {"type": "string", "description": "Directory path."},
                },
                "required": ["message_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fengtang_search",
            "description": ("Search stored messages by free text (matches subject/from/to/body)."),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "folder": {"type": "string"},
                    "limit": {"type": "integer", "default": 50},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fengtang_mark",
            "description": (
                "Add/remove/replace flags on messages. "
                "Flags: seen, answered, flagged, deleted, draft."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message_ids": {"type": "array", "items": {"type": "integer"}},
                    "flags": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["seen", "answered", "flagged", "deleted", "draft"],
                        },
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["add", "remove", "replace"],
                        "default": "add",
                    },
                    "sync_imap": {"type": "boolean", "default": False},
                },
                "required": ["message_ids", "flags"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fengtang_delete",
            "description": "Delete messages from the local store by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["message_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fengtang_move",
            "description": "Move messages between local folders.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_ids": {"type": "array", "items": {"type": "integer"}},
                    "target_folder": {"type": "string"},
                },
                "required": ["message_ids", "target_folder"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fengtang_fetch",
            "description": (
                "Pull new messages from the remote server (IMAP or POP3) into the local store."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account": {"type": "string"},
                    "protocol": {
                        "type": "string",
                        "enum": ["auto", "imap", "pop3"],
                        "default": "auto",
                    },
                    "folder": {"type": "string", "default": "INBOX"},
                    "limit": {"type": "integer", "default": 50},
                    "only_unseen": {"type": "boolean", "default": False},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fengtang_folders",
            "description": "List local folders and (if reachable) IMAP server folders.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fengtang_account_add",
            "description": (
                "Add or replace a mail account. Use provider preset names like "
                "gmail/outlook/qq/163 to auto-fill servers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "email": {"type": "string"},
                    "password": {"type": "string"},
                    "auth": {
                        "type": "string",
                        "enum": ["auto", "plain", "login", "cram-md5", "xoauth2", "ntlm", "apop"],
                        "default": "auto",
                    },
                    "provider": {"type": "string"},
                },
                "required": ["name", "email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fengtang_account_list",
            "description": "List configured accounts (passwords masked).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fengtang_account_test",
            "description": "Test IMAP/POP3/SMTP connectivity and authentication.",
            "parameters": {
                "type": "object",
                "properties": {"account": {"type": "string"}},
            },
        },
    },
]

_DISPATCH = {
    "fengtang_send": "send_mail",
    "fengtang_list": "list_messages",
    "fengtang_read": "read_message",
    "fengtang_search": "search_messages",
    "fengtang_mark": "mark_messages",
    "fengtang_delete": "delete_messages",
    "fengtang_move": "move_messages",
    "fengtang_fetch": "fetch_messages",
    "fengtang_folders": "list_folders",
    "fengtang_account_add": "account_add",
    "fengtang_account_list": "account_list",
    "fengtang_account_test": "account_test",
}


def dispatch(name: str, arguments: dict[str, Any] | str) -> dict:
    """Dispatch a model tool call to the matching api function."""
    if isinstance(arguments, str):
        parsed: dict[str, Any] = json.loads(arguments)
    else:
        parsed = arguments
    args: dict[str, Any] = {str(k): v for k, v in parsed.items()}

    if name not in _DISPATCH:
        raise ValueError(f"Unknown tool: {name}. Available: {', '.join(sorted(_DISPATCH))}")

    # Normalize agent-facing parameter names to api signatures.
    if name in ("fengtang_send", "fengtang_fetch", "fengtang_account_test"):
        if "account" in args:
            args["account_name"] = args.pop("account")
    if name == "fengtang_account_add":
        args["set_default"] = True

    fn_name = _DISPATCH[name]
    import fengtang.api as api

    fn = getattr(api, fn_name)
    result: Any = fn(**args)
    out: dict = result.to_dict()
    return out


def tool_summary() -> list[dict[str, Any]]:
    """Compact one-line description per tool (for prompt building)."""
    out: list[dict[str, Any]] = []
    for tool in TOOLS:
        fn: dict[str, Any] = dict(tool["function"])
        params: dict[str, Any] = dict(fn.get("parameters", {}))
        required = list(params.get("required", []))
        out.append(
            {
                "name": str(fn["name"]),
                "description": str(fn.get("description", "")),
                "required": required,
            }
        )
    return out
