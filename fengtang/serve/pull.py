"""Inbound mail aggregation: pull messages from external mailboxes into the
built-in server's local store (fetchmail-style).

This is what lets a self-hosted FengTang server *receive* mail without a public
IP or its own DNS MX: an external account (IMAP or POP3) is polled, and each new
message is delivered into a local mailbox folder. Pure Python, reusing the
existing IMAP/POP3 clients.
"""

from __future__ import annotations

import logging
from typing import Any

from fengtang.core.config import Account
from fengtang.mail.store import Store

log = logging.getLogger("fengtang.server.pull")


def pull_account(
    account: Account,
    store: Store,
    folder: str = "INBOX",
    limit: int = 200,
    mark_seen: bool = True,
) -> int:
    """Fetch new messages from `account` into `store`; returns the number stored.

    Prefers IMAP when configured, falls back to POP3. De-duplication by
    Message-ID means repeated polls are safe.
    """
    protocol = "imap" if account.imap_host else ("pop3" if account.pop_host else "")
    if protocol == "imap":
        return _pull_imap(account, store, folder, limit, mark_seen)
    if protocol == "pop3":
        return _pull_pop3(account, store, folder, limit)
    log.warning("pull: account %s has no IMAP or POP3 server configured", account.name)
    return 0


def _pull_imap(account: Account, store: Store, folder: str, limit: int, mark_seen: bool) -> int:
    from fengtang.mail.imap_client import ImapClient

    stored = 0
    with ImapClient(account) as imap:
        for uid, raw in imap.fetch_all(folder, limit=limit, only_unseen=mark_seen):
            try:
                store.store(raw, folder=folder, uid=uid)
                stored += 1
                if mark_seen:
                    imap.store_flags(folder, uid, ["\\Seen"], mode="add")
            except Exception as exc:  # noqa: BLE001
                log.warning("pull: failed to store uid %s: %s", uid, exc)
    return stored


def _pull_pop3(account: Account, store: Store, folder: str, limit: int) -> int:
    from fengtang.mail.pop_client import PopClient

    stored = 0
    with PopClient(account) as pop:
        uidls = pop.uidl()
        for index, raw in pop.fetch_all(limit=limit):
            uid = uidls[index - 1] if index - 1 < len(uidls) else str(index)
            try:
                store.store(raw, folder=folder, uid=uid)
                stored += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("pull: failed to store #%s: %s", index, exc)
    return stored


def pull_all(accounts: list[Any], store: Store, **kwargs: Any) -> int:
    """Pull each account; returns the total number of messages stored."""
    return sum(pull_account(account, store, **kwargs) for account in accounts)
