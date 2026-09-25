"""Unified API: every operation returns a ToolResult usable by humans and agents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fengtang.core.config import (
    Account,
    Config,
    load_config,
    save_config,
)
from fengtang.core.config import (
    add_account as config_add_account,
)
from fengtang.core.errors import FengTangError, ToolResult
from fengtang.mail.imap_client import ImapClient, imap_search_criteria
from fengtang.mail.parser import (
    build_message,
    html_to_text,
    parse_message,
    save_attachments,
)
from fengtang.mail.pop_client import PopClient
from fengtang.mail.smtp_client import smtp_send
from fengtang.mail.store import FLAG_MAP, Store

__version__ = "0.0.8"


def _meta() -> dict[str, Any]:
    return {"version": __version__}


def _as_path(config_path: str | None):
    if config_path is None:
        return None

    return Path(config_path).expanduser()


def _config(config_path: str | None = None) -> Config:
    if config_path is None:
        return load_config()

    return load_config(Path(config_path).expanduser())


def _store(config: Config) -> Store:
    return Store(config.db_path())


# ---------- account management ----------


def account_add(
    name: str,
    email: str,
    password: str = "",
    auth: str = "auto",
    provider: str = "",
    set_default: bool = False,
    config_path: str | None = None,
    **overrides: Any,
) -> ToolResult:
    """Add or replace an account."""
    try:
        config = _config(config_path)
        account = config_add_account(
            config,
            name=name,
            email=email,
            password=password,
            auth=auth,
            provider=provider,
            **overrides,
        )
        if set_default:
            config.default_account = name
        path = save_config(config, _as_path(config_path))
        return ToolResult(
            success=True,
            data=account.__dict__,
            metadata={
                **_meta(),
                "path": str(path),
            },
        )
    except FengTangError as exc:
        return ToolResult(success=False, error=str(exc), metadata=_meta())
    except Exception as exc:  # noqa: BLE001
        return ToolResult(success=False, error=f"{type(exc).__name__}: {exc}", metadata=_meta())


def account_list(config_path: str | None = None) -> ToolResult:
    try:
        config = _config(config_path)
        accounts = []
        for account in config.accounts:
            data = dict(account.__dict__)
            try:
                data["password"] = "***" if account.resolve_password() else ""
            except Exception:
                data["password"] = "***" if account.password else ""
            accounts.append(data)
        return ToolResult(
            success=True,
            data=accounts,
            metadata={
                **_meta(),
                "default": config.default_account,
            },
        )
    except FengTangError as exc:
        return ToolResult(success=False, error=str(exc), metadata=_meta())


def account_remove(name: str, config_path: str | None = None) -> ToolResult:
    try:
        config = _config(config_path)
        before = len(config.accounts)
        config.accounts = [a for a in config.accounts if a.name != name]
        if len(config.accounts) == before:
            return ToolResult(success=False, error=f"Account not found: {name}", metadata=_meta())
        if config.default_account == name:
            config.default_account = config.accounts[0].name if config.accounts else ""
        save_config(config, _as_path(config_path))
        return ToolResult(success=True, data={"removed": name}, metadata=_meta())
    except FengTangError as exc:
        return ToolResult(success=False, error=str(exc), metadata=_meta())


def account_test(name: str | None = None, config_path: str | None = None) -> ToolResult:
    """Probe IMAP/POP3/SMTP connectivity + auth for an account."""
    try:
        config = _config(config_path)
        account = config.get_account(name)
        result: dict[str, Any] = {"account": account.name}
        try:
            with ImapClient(account) as imap:
                folders = imap.list_folders()
                result["imap"] = {"ok": True, "folders": len(folders)}
        except FengTangError as exc:
            result["imap"] = {"ok": False, "error": str(exc)}
        if account.pop_host:
            try:
                from fengtang.mail.pop_client import PopClient

                with PopClient(account) as pop:
                    count, size = pop.stat()
                    result["pop3"] = {"ok": True, "messages": count, "size": size}
            except FengTangError as exc:
                result["pop3"] = {"ok": False, "error": str(exc)}
        if account.smtp_host:
            try:
                from fengtang.mail.smtp_client import _connect

                server = _connect(account)
                try:
                    server.ehlo()
                    _login_probe(server, account)
                    result["smtp"] = {"ok": True}
                finally:
                    try:
                        server.quit()
                    except Exception:
                        pass
            except FengTangError as exc:
                result["smtp"] = {"ok": False, "error": str(exc)}
        ok = any(v.get("ok") for k, v in result.items() if isinstance(v, dict))
        return ToolResult(success=ok, data=result, metadata=_meta())
    except FengTangError as exc:
        return ToolResult(success=False, error=str(exc), metadata=_meta())


def _login_probe(server: Any, account: Account) -> None:
    """Reuse smtp_client._login without circular import cost."""
    from fengtang.mail.smtp_client import _login

    _login(server, account)


# ---------- sending ----------


def send_mail(
    to: list[str],
    subject: str,
    body: str,
    account_name: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str | None = None,
    in_reply_to: str | None = None,
    attachments: list[list[str]] | None = None,
    html_body: str | None = None,
    save_to_sent: bool = True,
    config_path: str | None = None,
) -> ToolResult:
    """Send a message via the account's SMTP server.

    attachments: list of [path, filename] pairs.
    """
    try:
        config = _config(config_path)
        account = config.get_account(account_name)
        attach_pairs = [(pair[0], pair[1] if len(pair) > 1 else "") for pair in attachments or []]
        message = build_message(
            from_addr=account.email,
            to_addrs=to,
            subject=subject,
            body=body,
            cc_addrs=cc,
            bcc_addrs=bcc,
            reply_to=reply_to,
            in_reply_to=in_reply_to,
            attachments=attach_pairs,
            html_body=html_body,
        )
        refused, msg_id = smtp_send(account, message)
        sent_folder_id = None
        if save_to_sent and account.imap_host:
            try:
                with ImapClient(account) as imap:
                    imap.append("Sent", bytes(message))
            except FengTangError:
                pass  # best-effort
        store = _store(config)
        try:
            sent_folder = "Sent"
            sent_folder = sent_folder_name(config, account)
            sent_id = store.store(
                bytes(message),
                folder=sent_folder,
                extra_flags=["\\Seen"],
            )
            sent_folder_id = sent_id
        finally:
            store.close()
        data = {
            "message_id": msg_id,
            "refused": refused,
            "saved_to": sent_folder_id,
        }
        return ToolResult(success=True, data=data, metadata=_meta())
    except FengTangError as exc:
        return ToolResult(success=False, error=str(exc), metadata=_meta())
    except Exception as exc:  # noqa: BLE001
        return ToolResult(success=False, error=f"{type(exc).__name__}: {exc}", metadata=_meta())


def sent_folder_name(config: Config, account: Account) -> str:
    folder: str = account.extra.get("sent_folder", "Sent")
    return folder


# ---------- fetching ----------


def fetch_messages(
    account_name: str | None = None,
    protocol: str = "auto",
    folder: str = "INBOX",
    limit: int = 50,
    only_unseen: bool = False,
    mark_seen: bool = True,
    config_path: str | None = None,
) -> ToolResult:
    """Pull new messages from the server into the local store."""
    try:
        config = _config(config_path)
        account = config.get_account(account_name)
        store = _store(config)
        stored, errors = [], []
        try:
            proto = protocol.lower()
            if proto == "auto":
                proto = "imap" if account.imap_host else ("pop3" if account.pop_host else "none")
            if proto == "imap" and account.imap_host:
                with ImapClient(account) as imap:
                    for uid, raw in imap.fetch_all(folder, limit=limit, only_unseen=only_unseen):
                        try:
                            message_id = store.store(raw, folder=folder, uid=uid)
                            stored.append(message_id)
                            if mark_seen:
                                imap.store_flags(folder, uid, ["\\Seen"], mode="add")
                        except FengTangError as exc:
                            errors.append(f"uid {uid}: {exc}")
            elif proto == "pop3" and account.pop_host:
                with PopClient(account) as pop:
                    uidls = pop.uidl()
                    for index, raw in pop.fetch_all(limit=limit):
                        uid = uidls[index - 1] if index - 1 < len(uidls) else str(index)
                        try:
                            message_id = store.store(raw, folder=folder, uid=uid)
                            stored.append(message_id)
                        except FengTangError as exc:
                            errors.append(f"#{index}: {exc}")
            else:
                raise FengTangError(
                    f"No {proto.upper()} server configured for account {account.name!r}"
                )
        finally:
            store.close()
        return ToolResult(
            success=True,
            data={"fetched": len(stored), "ids": stored, "errors": errors},
            metadata={**_meta(), "protocol": proto, "account": account.name},
        )
    except FengTangError as exc:
        return ToolResult(success=False, error=str(exc), metadata=_meta())
    except Exception as exc:  # noqa: BLE001
        return ToolResult(success=False, error=f"{type(exc).__name__}: {exc}", metadata=_meta())


# ---------- reading / listing ----------


def list_messages(
    folder: str | None = None,
    limit: int = 50,
    offset: int = 0,
    unread_only: bool = False,
    flagged_only: bool = False,
    local_only: bool = True,
    account_name: str | None = None,
    config_path: str | None = None,
) -> ToolResult:
    try:
        config = _config(config_path)
        store = _store(config)
        try:
            if local_only:
                rows = store.list_messages(
                    folder=folder,
                    limit=limit,
                    offset=offset,
                    unread_only=unread_only,
                    flagged_only=flagged_only,
                )
            else:
                rows = store.list_messages(
                    folder=folder,
                    limit=limit,
                    offset=offset,
                    unread_only=unread_only,
                    flagged_only=flagged_only,
                )
        finally:
            store.close()
        return ToolResult(
            success=True,
            data=rows,
            metadata={
                **_meta(),
                "count": len(rows),
                "account": account_name or "",
            },
        )
    except FengTangError as exc:
        return ToolResult(success=False, error=str(exc), metadata=_meta())
    except Exception as exc:  # noqa: BLE001
        return ToolResult(success=False, error=f"{type(exc).__name__}: {exc}", metadata=_meta())


def read_message(
    message_id: int,
    save_attachments_to: str | None = None,
    config_path: str | None = None,
) -> ToolResult:
    try:
        config = _config(config_path)
        store = _store(config)
        try:
            data = store.get_message(message_id, with_body=True)
            raw = store.get_raw(message_id)
        finally:
            store.close()
        parsed = parse_message(raw)
        data["attachments"] = parsed.attachments
        if not data.get("text_body") and data.get("html_body"):
            data["text_body"] = html_to_text(data["html_body"])
        if save_attachments_to:
            from pathlib import Path

            saved = save_attachments(parsed, Path(save_attachments_to).expanduser())
            data["saved_attachments"] = [str(p) for p in saved]
        return ToolResult(success=True, data=data, metadata=_meta())
    except FengTangError as exc:
        return ToolResult(success=False, error=str(exc), metadata=_meta())


def search_messages(
    query: str,
    folder: str | None = None,
    limit: int = 50,
    offset: int = 0,
    server_side: bool = False,
    account_name: str | None = None,
    config_path: str | None = None,
) -> ToolResult:
    try:
        config = _config(config_path)
        if server_side:
            account = config.get_account(account_name)
            criteria = imap_search_criteria(query)
            with ImapClient(account) as imap:
                seqs = imap.search(folder or "INBOX", criteria)
            return ToolResult(
                success=True,
                data={"server_ids": seqs, "criteria": criteria},
                metadata={**_meta(), "server_side": True},
            )
        store = _store(config)
        try:
            rows = store.search(query, folder=folder, limit=limit, offset=offset)
        finally:
            store.close()
        return ToolResult(
            success=True,
            data=rows,
            metadata={
                **_meta(),
                "count": len(rows),
                "query": query,
            },
        )
    except FengTangError as exc:
        return ToolResult(success=False, error=str(exc), metadata=_meta())
    except Exception as exc:  # noqa: BLE001
        return ToolResult(success=False, error=f"{type(exc).__name__}: {exc}", metadata=_meta())


# ---------- marking / moving / deleting ----------


def mark_messages(
    message_ids: list[int],
    flags: list[str],
    mode: str = "add",
    sync_imap: bool = False,
    account_name: str | None = None,
    config_path: str | None = None,
) -> ToolResult:
    try:
        if mode not in ("add", "remove", "replace"):
            raise FengTangError(f"Invalid mode: {mode!r} (use add|remove|replace)")
        invalid = [f for f in flags if f not in FLAG_MAP]
        if invalid:
            raise FengTangError(f"Unknown flags: {invalid}; valid: {', '.join(FLAG_MAP)}")
        system_flags = [FLAG_MAP[f] for f in flags]
        config = _config(config_path)
        store = _store(config)
        try:
            for message_id in message_ids:
                store.set_flags(message_id, system_flags, mode=mode)
        finally:
            store.close()
        imap_synced = False
        if sync_imap:
            account = config.get_account(account_name)
            imap_synced = _sync_flags_imap(account, message_ids, store)
        return ToolResult(
            success=True,
            data={"marked": len(message_ids), "flags": flags, "mode": mode},
            metadata={**_meta(), "imap_synced": imap_synced},
        )
    except FengTangError as exc:
        return ToolResult(success=False, error=str(exc), metadata=_meta())
    except Exception as exc:  # noqa: BLE001
        return ToolResult(success=False, error=f"{type(exc).__name__}: {exc}", metadata=_meta())


def _sync_flags_imap(account: Account, message_ids: list[int], store: Store) -> bool:
    """Best-effort: push local flags for messages that carry a server uid."""
    try:
        with ImapClient(account) as imap:
            for message_id in message_ids:
                data = store.get_message(message_id, with_body=False)
                if not data.get("uid"):
                    continue
                flags = list(data.get("flags") or [])
                imap.store_flags(data["folder"], data["uid"], flags, mode="replace")
        return True
    except FengTangError:
        return False


def delete_messages(
    message_ids: list[int],
    purge_imap: bool = False,
    account_name: str | None = None,
    config_path: str | None = None,
) -> ToolResult:
    try:
        config = _config(config_path)
        store = _store(config)
        try:
            count = store.delete(message_ids)
        finally:
            store.close()
        return ToolResult(success=True, data={"deleted": count}, metadata=_meta())
    except FengTangError as exc:
        return ToolResult(success=False, error=str(exc), metadata=_meta())


def move_messages(
    message_ids: list[int],
    target_folder: str,
    account_name: str | None = None,
    config_path: str | None = None,
) -> ToolResult:
    try:
        config = _config(config_path)
        store = _store(config)
        try:
            count = store.move(message_ids, target_folder)
        finally:
            store.close()
        return ToolResult(
            success=True,
            data={"moved": count, "folder": target_folder},
            metadata=_meta(),
        )
    except FengTangError as exc:
        return ToolResult(success=False, error=str(exc), metadata=_meta())


def list_folders(account_name: str | None = None, config_path: str | None = None) -> ToolResult:
    try:
        config = _config(config_path)
        store = _store(config)
        try:
            local = store.folders()
        finally:
            store.close()
        server: list[dict[str, Any]] = []
        try:
            account = config.get_account(account_name)
            with ImapClient(account) as imap:
                server = imap.list_folders()
        except FengTangError:
            pass
        return ToolResult(success=True, data={"local": local, "server": server}, metadata=_meta())
    except FengTangError as exc:
        return ToolResult(success=False, error=str(exc), metadata=_meta())


def server_status(config_path: str | None = None) -> ToolResult:
    """Return local built-in server info (ports/users) if running config exists."""
    try:
        config = _config(config_path)
        data: dict[str, Any] = {
            "data_dir": str(config.data_path()),
            "accounts": [a.name for a in config.accounts],
        }
        return ToolResult(success=True, data=data, metadata=_meta())
    except FengTangError as exc:
        return ToolResult(success=False, error=str(exc), metadata=_meta())


def dumps(result: ToolResult, indent: int = 2) -> str:
    """Serialize a ToolResult as pretty JSON (used by --json CLI mode)."""
    out: str = json.dumps(result.to_dict(), indent=indent, ensure_ascii=False, default=str)
    return out
