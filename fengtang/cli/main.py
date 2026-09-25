"""FengTang command-line interface.

Global flags: -V/--version, --debug, --verbose, --json, -q/--quiet, -a/--account.
Subcommands: config, send, fetch, list, read, search, mark, delete, move,
folders, serve, api.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

PROG = "fengtang"

GLOBAL_FLAGS: list[tuple[list[str], dict[str, Any]]] = [
    (["-V", "--version"], {"action": "store_true", "help": "show version and exit"}),
    (["--debug"], {"action": "store_true", "help": "debug logging (protocol traces)"}),
    (["--verbose", "-v"], {"action": "store_true", "help": "verbose output"}),
    (["--json"], {"action": "store_true", "help": "machine-readable JSON output"}),
    (["-q", "--quiet"], {"action": "store_true", "help": "only errors; exit codes only"}),
]


def _add_globals(parser: argparse.ArgumentParser) -> None:
    for flags, kwargs in GLOBAL_FLAGS:
        parser.add_argument(*flags, **kwargs)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="FengTang(冯唐)- pure-Python CLI mail client (SMTP/IMAP/POP3 + built-in server).\n"
        "名字取自古人云中传书的信使:愿每封邮件如当年家书,忠实送达。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  fengtang config add work me@example.com --provider gmail\n"
            "  fengtang send -t bob@example.com -s 'Hi' -m 'Hello there'\n"
            "  fengtang fetch --limit 20\n"
            "  fengtang list --unread\n"
            "  fengtang search 'from:alice quarterly'\n"
            "  fengtang read 42\n"
            "  fengtang mark 42 --flags seen,flagged\n"
            "  fengtang serve --smtp-port 2525 --pop-port 1110 --user me@localhost:secret\n"
        ),
    )
    _add_globals(parser)
    sub = parser.add_subparsers(dest="command")

    def sub_parser(*args, **kwargs):
        p = sub.add_parser(*args, **kwargs)
        _add_globals(p)
        return p

    # ---- config ----
    p_config = sub_parser("config", help="manage accounts")
    csub = p_config.add_subparsers(dest="config_command")

    def csub_parser(*args, **kwargs):
        p = csub.add_parser(*args, **kwargs)
        _add_globals(p)
        return p

    p_add = csub_parser("add", help="add or replace an account")
    p_add.add_argument("name")
    p_add.add_argument("email")
    p_add.add_argument(
        "--password", default=None, help="password / authorization code (omit to be prompted)"
    )
    p_add.add_argument(
        "--auth",
        default="auto",
        choices=["auto", "plain", "login", "cram-md5", "xoauth2", "ntlm", "apop"],
    )
    p_add.add_argument(
        "--provider",
        default="",
        help="preset: gmail, outlook, qq, 163, 126, yahoo, icloud, zoho, aliyun, sina",
    )
    (
        p_add.add_argument("--imap-host", default=""),
        p_add.add_argument("--imap-port", type=int, default=0),
    )
    (
        p_add.add_argument("--smtp-host", default=""),
        p_add.add_argument("--smtp-port", type=int, default=0),
    )
    (
        p_add.add_argument("--pop-host", default=""),
        p_add.add_argument("--pop-port", type=int, default=0),
    )
    p_add.add_argument("--no-imap-ssl", action="store_true")
    p_add.add_argument("--no-smtp-ssl", action="store_true")
    p_add.add_argument("--smtp-starttls", action="store_true")
    p_add.add_argument("--set-default", action="store_true")
    csub_parser("list", help="list accounts")
    p_rm = csub_parser("remove", help="remove an account")
    p_setextra = csub_parser("set-extra", help="set a per-account extra field (e.g. client_id)")
    p_setextra.add_argument("name")
    p_setextra.add_argument("key")
    p_setextra.add_argument("value")
    p_rm.add_argument("name")
    p_login = csub_parser("login", help="interactive OAuth2 browser login (Gmail/Outlook)")
    p_login.add_argument("email", nargs="?", default="", help="email to log in")
    p_login.add_argument(
        "-a", "--account", default=None, help="existing account name to (re)authorize"
    )
    p_login.add_argument("--provider", default="gmail", choices=["gmail", "outlook"])
    p_login.add_argument(
        "--client-id", default="", help="OAuth client_id (or set extra.client_id in config)"
    )
    p_login.add_argument(
        "--no-browser", action="store_true", help="print the URL instead of opening the browser"
    )
    p_login.add_argument("--timeout", type=int, default=300)
    p_login.add_argument(
        "--port", type=int, default=None, help="fixed loopback redirect port (Outlook: register it)"
    )

    p_test = csub_parser("test", help="probe IMAP/POP3/SMTP auth")
    p_test.add_argument("-a", "--account", default=None)
    p_test.add_argument("--oauth2-token", default="", help="OAuth2 access token for XOAUTH2")

    # ---- send ----
    p_send = sub_parser("send", help="send an email")
    p_send.add_argument("-t", "--to", required=True, action="append")
    p_send.add_argument("-s", "--subject", required=True)
    p_send.add_argument("-m", "--message", default="", help="plain-text body ('-' = stdin)")
    p_send.add_argument("--html", default="", help="HTML body file ('-' = stdin)")
    p_send.add_argument("--body-file", default="", help="read body from file")
    p_send.add_argument("-c", "--cc", action="append")
    p_send.add_argument("--bcc", action="append")
    p_send.add_argument("--reply-to", default="")
    p_send.add_argument("--in-reply-to", default="", help="Message-ID being replied to")
    p_send.add_argument(
        "--attach",
        action="append",
        default=[],
        help="file to attach (repeatable); 'path:name' renames",
    )
    p_send.add_argument("-a", "--account", default=None)
    p_send.add_argument("--oauth2-token", default="")
    p_send.add_argument("--no-save-sent", action="store_true")

    # ---- fetch ----
    p_fetch = sub_parser("fetch", help="pull new mail into the local store")
    p_fetch.add_argument("-a", "--account", default=None)
    p_fetch.add_argument("--protocol", default="auto", choices=["auto", "imap", "pop3"])
    p_fetch.add_argument("-f", "--folder", default="INBOX")
    p_fetch.add_argument("-n", "--limit", type=int, default=50)
    p_fetch.add_argument("--unseen-only", action="store_true")
    p_fetch.add_argument("--no-mark-seen", action="store_true")

    # ---- list ----
    p_list_m = sub_parser("list", help="list stored messages")
    p_list_m.add_argument("-f", "--folder", default=None)
    p_list_m.add_argument("-n", "--limit", type=int, default=50)
    p_list_m.add_argument("--offset", type=int, default=0)
    p_list_m.add_argument("--unread", action="store_true")
    p_list_m.add_argument("--flagged", action="store_true")

    # ---- read ----
    p_read = sub_parser("read", help="read one message")
    p_read.add_argument("id", type=int)
    p_read.add_argument("--save-attachments", default="", metavar="DIR")
    p_read.add_argument("--raw", action="store_true", help="print full RFC822 source")

    # ---- search ----
    p_search = sub_parser("search", help="search stored messages")
    p_search.add_argument("query")
    p_search.add_argument("-f", "--folder", default=None)
    p_search.add_argument("-n", "--limit", type=int, default=50)
    p_search.add_argument(
        "--server",
        action="store_true",
        help="run IMAP SEARCH server-side (from:/to:/subject:/since:)",
    )

    # ---- mark ----
    p_mark = sub_parser("mark", help="add/remove/replace flags")
    p_mark.add_argument("ids", nargs="+", type=int)
    p_mark.add_argument(
        "--flags", required=True, help="comma list: seen,answered,flagged,deleted,draft"
    )
    p_mark.add_argument("--mode", default="add", choices=["add", "remove", "replace"])
    p_mark.add_argument("--sync-imap", action="store_true")
    p_mark.add_argument("-a", "--account", default=None)

    # ---- delete / move ----
    p_del = sub_parser("delete", help="delete stored messages")
    p_del.add_argument("ids", nargs="+", type=int)
    p_move = sub_parser("move", help="move messages to another local folder")
    p_move.add_argument("ids", nargs="+", type=int)
    p_move.add_argument("folder")

    # ---- folders ----
    p_folders = sub_parser("folders", help="list local/server folders")
    p_folders.add_argument("-a", "--account", default=None)

    # ---- serve ----
    p_serve = sub_parser("serve", help="run the built-in SMTP+POP3 server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--smtp-port", type=int, default=2525)
    p_serve.add_argument("--pop-port", type=int, default=1110)
    p_serve.add_argument("--domain", default="localhost")
    p_serve.add_argument(
        "--user",
        action="append",
        default=[],
        metavar="EMAIL:PASSWORD",
        help="auth-protected mailbox (repeatable)",
    )
    p_serve.add_argument("--db", default="", help="custom db path")

    # ---- setup wizard ----
    p_setup = sub_parser("setup-gmail", help="guided Gmail OAuth setup (client_id + login)")
    p_setup.add_argument("email", help="Gmail address to authorize")
    p_setup.add_argument(
        "--client-id", default="", help="client_id if you already have one (skips guidance)"
    )
    p_setup.add_argument("--no-browser", action="store_true")
    p_setup.add_argument("--timeout", type=int, default=300)

    # ---- api ----
    p_api = sub_parser("api", help="print agent TOOLS JSON schema + tool list")
    p_api.add_argument("--schema", action="store_true", help="full OpenAI tools JSON")

    return parser


# ------------------------------------------------------------------ output


def _emit(args: argparse.Namespace, payload: dict, quiet_text: str = "") -> int:
    """Print result payload respecting --json/--quiet; return exit code."""
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    elif payload.get("success"):
        if not getattr(args, "quiet", False):
            print(quiet_text or json.dumps(payload.get("data"), ensure_ascii=False, default=str))
    else:
        print(f"error: {payload.get('error')}", file=sys.stderr)
        return 1
    return 0 if payload.get("success") else 1


def _tool_result_payload(result) -> dict:
    return result.to_dict() if hasattr(result, "to_dict") else dict(result)


# ------------------------------------------------------------------ commands


def cmd_setup_gmail(args: argparse.Namespace) -> int:
    """Gmail OAuth login using the built-in Thunderbird public client_id."""
    from fengtang.core.config import load_config, save_config

    config = load_config()
    name = args.email.split("@")[0].replace(".", "-")
    account = None
    for acct in config.accounts:
        if acct.email == args.email:
            account = acct
            break
    if account is None:
        from fengtang.core.config import add_account

        account = add_account(config, name=name, email=args.email, provider="gmail")
        save_config(config)

    return cmd_config(
        argparse.Namespace(
            config_command="login",
            email="",
            account=account.name,
            provider="gmail",
            client_id=args.client_id,
            no_browser=args.no_browser,
            timeout=args.timeout,
            port=None,
            json=getattr(args, "json", False),
            quiet=getattr(args, "quiet", False),
        )
    )


def cmd_oauth_login(args: argparse.Namespace) -> int:
    """Interactive browser OAuth2 login; persists tokens into the account."""
    from fengtang.core.config import load_config, save_config
    from fengtang.mail.oauth import interactive_login

    if not args.email and not args.account:
        print("error: provide an email or -a ACCOUNT", file=sys.stderr)
        return 1

    config = load_config()
    if args.account:
        account = config.get_account(args.account)
        email = account.email
    else:
        name = args.email.split("@")[0].replace(".", "-")
        account = config_add_or_get(config, name=name, email=args.email, provider=args.provider)
        email = args.email
    provider = (account.extra or {}).get("oauth_provider") or args.provider
    client_id = (account.extra or {}).get("client_id") or args.client_id
    # Thunderbird built-in public client_id used when unset (see oauth.py).

    try:
        tokens = interactive_login(
            email=email,
            provider=provider,
            client_id=client_id,
            open_browser=not args.no_browser,
            timeout=args.timeout,
            localhost_port=args.port,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    account.oauth2_token = tokens["access_token"]
    account.extra = dict(account.extra or {})
    account.extra["refresh_token"] = tokens["refresh_token"]
    account.extra["client_id"] = client_id
    account.extra["oauth_provider"] = provider
    account.extra["token_obtained_at"] = tokens["obtained_at"]
    account.extra["token_expires_in"] = tokens["expires_in"]
    account.auth = "xoauth2"
    save_config(config)
    if not getattr(args, "json", False):
        print(
            f"login OK for {email}; tokens saved to config.json "
            f"(refresh token: {'yes' if tokens['refresh_token'] else 'NO'})"
        )
    return 0


def config_add_or_get(config, name: str, email: str, provider: str):
    """Add the account if missing, else return the existing one."""
    from fengtang.core.config import add_account

    for acct in config.accounts:
        if acct.email == email:
            return acct
    return add_account(config, name=name, email=email, provider=provider)


def cmd_config(args: argparse.Namespace) -> int:
    import fengtang.api as api

    if args.config_command == "add":
        password = args.password
        if password is None:
            import getpass

            hint = (
                "app password/授权码"
                if args.provider.lower()
                in (
                    "gmail",
                    "outlook",
                    "qq",
                    "163",
                    "126",
                    "yahoo",
                    "icloud",
                    "zoho",
                    "aliyun",
                    "sina",
                )
                else "password"
            )
            try:
                password = getpass.getpass(f"Password ({hint}) for {args.email}: ")
            except (EOFError, KeyboardInterrupt):
                print("\naborted", file=sys.stderr)
                return 130
            except OSError:
                # Non-interactive context (no tty): don't block; save empty and warn.
                print(
                    "warning: no tty available; account saved without password. "
                    "Re-run with --password to set it.",
                    file=sys.stderr,
                )
                password = ""
        overrides = {}
        for key in ("imap_host", "imap_port", "smtp_host", "smtp_port", "pop_host", "pop_port"):
            value = getattr(args, key, None)
            if value:
                overrides[key] = value
        if args.no_imap_ssl:
            overrides["imap_ssl"] = False
        if args.no_smtp_ssl:
            overrides["smtp_ssl"] = False
        if args.smtp_starttls:
            overrides["smtp_starttls"] = True
        result = api.account_add(
            name=args.name,
            email=args.email,
            password=password,
            auth=args.auth,
            provider=args.provider,
            set_default=args.set_default,
            **overrides,
        )
        return _emit(args, _tool_result_payload(result), f"account saved: {args.name}")
    if args.config_command == "remove":
        result = api.account_remove(args.name)
        return _emit(args, _tool_result_payload(result), f"removed: {args.name}")
    if args.config_command == "set-extra":
        from fengtang.core.config import load_config, save_config

        config = load_config()
        account = config.get_account(args.name)
        account.extra = dict(account.extra or {})
        account.extra[args.key] = args.value
        save_config(config)
        if not getattr(args, "json", False):
            print(f"{args.name}.extra.{args.key} = {args.value}")
        return 0
    if args.config_command == "login":
        return cmd_oauth_login(args)
    if args.config_command == "test":
        result = api.account_test(args.account)
        data = result.data if result.success else {}
        lines = [f"account: {data.get('account', args.account or '?')}"]
        for proto in ("imap", "pop3", "smtp"):
            info = data.get(proto) or {}
            status = "OK" if info.get("ok") else f"FAIL ({info.get('error', 'not configured')})"
            lines.append(f"  {proto.upper()}: {status}")
        text = "\n".join(lines)
        return _emit(args, _tool_result_payload(result), text)
    result = api.account_list()
    accounts = result.data or []
    lines = [f"{a['name']}: {a['email']} (auth={a['auth']})" for a in accounts] or ["(none)"]
    return _emit(args, _tool_result_payload(result), "\n".join(lines))


def cmd_send(args: argparse.Namespace) -> int:
    import fengtang.api as api

    body = args.message
    if args.body_file:
        from pathlib import Path

        body = Path(args.body_file).expanduser().read_text(encoding="utf-8")
    if body == "-":
        body = sys.stdin.read()
    html_body = None
    if args.html:
        if args.html == "-":
            html_body = sys.stdin.read()
        else:
            from pathlib import Path

            html_body = Path(args.html).expanduser().read_text(encoding="utf-8")
    attachments = []
    for item in args.attach or []:
        path, _, name = item.partition(":")
        attachments.append([path, name or ""])
    result = api.send_mail(
        to=args.to,
        subject=args.subject,
        body=body,
        account_name=args.account,
        cc=args.cc,
        bcc=args.bcc,
        reply_to=args.reply_to or None,
        in_reply_to=args.in_reply_to or None,
        attachments=attachments,
        html_body=html_body,
        save_to_sent=not args.no_save_sent,
    )
    data = result.data if result.success else {}
    text = f"sent (message-id: {data.get('message_id', '?')})" + (
        f"; refused: {data['refused']}" if data.get("refused") else ""
    )
    return _emit(args, _tool_result_payload(result), text)


def cmd_fetch(args: argparse.Namespace) -> int:
    import fengtang.api as api

    result = api.fetch_messages(
        account_name=args.account,
        protocol=args.protocol,
        folder=args.folder,
        limit=args.limit,
        only_unseen=args.unseen_only,
        mark_seen=not args.no_mark_seen,
    )
    data = result.data if result.success else {}
    text = f"fetched {data.get('fetched', 0)} new message(s) into local store"
    if data.get("errors"):
        text += f"; errors: {data['errors']}"
    return _emit(args, _tool_result_payload(result), text)


def cmd_list(args: argparse.Namespace) -> int:
    import fengtang.api as api

    result = api.list_messages(
        folder=args.folder,
        limit=args.limit,
        offset=args.offset,
        unread_only=args.unread,
        flagged_only=args.flagged,
    )
    rows = result.data if result.success else []
    lines = []
    for row in rows:
        seen = "" if row.get("seen") else "*"
        lines.append(
            f"{row['id']:>6}  {row.get('date', '')[:22]:<22}  "
            f"{row.get('from', '')[:28]:<28}  {seen}{row.get('subject', '')[:60]}"
        )
    if not lines:
        lines = ["(no messages)"]
    return _emit(args, _tool_result_payload(result), "\n".join(lines))


def cmd_read(args: argparse.Namespace) -> int:
    import fengtang.api as api

    result = api.read_message(args.id, save_attachments_to=args.save_attachments or None)
    if not result.success:
        return _emit(args, _tool_result_payload(result))
    if args.json:
        return _emit(args, _tool_result_payload(result))
    data = result.data
    if args.raw:
        print(data.get("raw", ""))
        return 0
    header_lines = [
        f"From:    {data.get('from', '')}",
        f"To:      {data.get('to', '')}",
    ]
    if data.get("cc"):
        header_lines.append(f"Cc:     {data['cc']}")
    header_lines += [
        f"Date:    {data.get('date', '')}",
        f"Subject: {data.get('subject', '')}",
    ]
    if data.get("attachments"):
        names = ", ".join(a.get("filename", "?") for a in data["attachments"])
        header_lines.append(f"Attach:  {names}")
    print("\n".join(header_lines))
    print("-" * 60)
    body = data.get("text_body") or ""
    print(body if body else "(empty body)")
    if args.save_attachments:
        saved = data.get("saved_attachments") or []
        print("-" * 60)
        print(f"saved {len(saved)} attachment(s) to {args.save_attachments}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    import fengtang.api as api

    result = api.search_messages(
        query=args.query,
        folder=args.folder,
        limit=args.limit,
        server_side=args.server,
    )
    rows = result.data if result.success and isinstance(result.data, list) else []
    lines = [
        f"{row['id']:>6}  {row.get('date', '')[:22]:<22}  {row.get('from', '')[:28]:<28}  "
        f"{row.get('subject', '')[:60]}"
        for row in rows
    ] or ["(no matches)"]
    return _emit(args, _tool_result_payload(result), "\n".join(lines))


def cmd_mark(args: argparse.Namespace) -> int:
    import fengtang.api as api

    flags = [f.strip() for f in args.flags.split(",") if f.strip()]
    result = api.mark_messages(
        message_ids=args.ids,
        flags=flags,
        mode=args.mode,
        sync_imap=args.sync_imap,
        account_name=args.account,
    )
    data = result.data if result.success else {}
    return _emit(args, _tool_result_payload(result), f"marked {data.get('marked', 0)} message(s)")


def cmd_delete(args: argparse.Namespace) -> int:
    import fengtang.api as api

    result = api.delete_messages(message_ids=args.ids)
    data = result.data if result.success else {}
    return _emit(args, _tool_result_payload(result), f"deleted {data.get('deleted', 0)} message(s)")


def cmd_move(args: argparse.Namespace) -> int:
    import fengtang.api as api

    result = api.move_messages(message_ids=args.ids, target_folder=args.folder)
    data = result.data if result.success else {}
    return _emit(
        args,
        _tool_result_payload(result),
        f"moved {data.get('moved', 0)} message(s) to {args.folder}",
    )


def cmd_folders(args: argparse.Namespace) -> int:
    import fengtang.api as api

    result = api.list_folders(account_name=args.account)
    data = result.data if result.success else {}
    lines = ["local folders:"]
    for folder in data.get("local") or []:
        lines.append(f"  {folder['name']:<20} total={folder['total']} unread={folder['unread']}")
    if data.get("server"):
        lines.append("server folders:")
        for folder in data["server"]:
            lines.append(f"  {folder['name']}")
    return _emit(args, _tool_result_payload(result), "\n".join(lines))


def cmd_serve(args: argparse.Namespace) -> int:
    from pathlib import Path

    from fengtang.core.config import Config
    from fengtang.serve.server import serve_blocking

    users: dict[str, str] = {}
    for item in args.user or []:
        email_addr, sep, password = item.partition(":")
        if not sep or not password:
            print("error: --user expects EMAIL:PASSWORD", file=sys.stderr)
            return 1
        users[email_addr.strip().lower()] = password
    db_path = Path(args.db).expanduser() if args.db else Config().data_path() / "fengtang.db"
    if args.json:
        info = {
            "smtp": [args.host, args.smtp_port],
            "pop3": [args.host, args.pop_port],
            "domain": args.domain,
            "users": sorted(users),
            "db": str(db_path),
        }
        print(json.dumps(info, indent=2))
    try:
        serve_blocking(
            db_path,
            smtp_port=args.smtp_port,
            pop_port=args.pop_port,
            host=args.host,
            domain=args.domain,
            users=users or None,
        )
    except KeyboardInterrupt:
        pass
    return 0


def cmd_api(args: argparse.Namespace) -> int:
    from fengtang.agent.tools import TOOLS, tool_summary

    if args.schema:
        print(json.dumps(TOOLS, indent=2, ensure_ascii=False))
        return 0
    print(json.dumps(tool_summary(), indent=2, ensure_ascii=False))
    return 0


# ------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)

    if getattr(args, "version", False):
        from fengtang import __version__

        print(f"fengtang {__version__}")
        return 0

    if getattr(args, "debug", False):
        import imaplib
        import logging

        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
        imaplib.Debug = 4  # type: ignore[attr-defined]
    else:
        from fengtang.core.config import setup_logging

        setup_logging(debug=False, verbose=getattr(args, "verbose", False))

    handlers = {
        "setup-gmail": cmd_setup_gmail,
        "config": cmd_config,
        "send": cmd_send,
        "fetch": cmd_fetch,
        "list": cmd_list,
        "read": cmd_read,
        "search": cmd_search,
        "mark": cmd_mark,
        "delete": cmd_delete,
        "move": cmd_move,
        "folders": cmd_folders,
        "serve": cmd_serve,
        "api": cmd_api,
    }
    if args.command in handlers:
        try:
            return handlers[args.command](args)
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
            return 130
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
