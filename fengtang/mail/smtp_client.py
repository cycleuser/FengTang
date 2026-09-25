"""SMTP client supporting all mainstream auth methods over SSL/STARTTLS."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from fengtang.core.auth import (
    b64encode,
    cram_md5_response,
    ntlm_type1_message,
    ntlm_type3_message,
    oauthbearer_string,
    sasl_login_password,
    sasl_login_user,
    sasl_plain,
    xoauth2_string,
)
from fengtang.core.config import Account
from fengtang.core.errors import AuthError, ConnectionError_


def _connect(account: Account) -> Any:
    server: Any
    try:
        if account.smtp_ssl:
            server = smtplib.SMTP_SSL(
                account.smtp_host,
                account.smtp_port,
                context=ssl.create_default_context(),
                timeout=30,
            )
        else:
            server = smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=30)
            if account.smtp_starttls:
                server.starttls(context=ssl.create_default_context())
        return server
    except (OSError, smtplib.SMTPException) as exc:
        raise ConnectionError_(
            f"Cannot connect to {account.smtp_host}:{account.smtp_port}: {exc}"
        ) from exc


def _login(server: smtplib.SMTP, account: Account) -> None:
    auth = account.auth.lower()
    email_addr = account.email
    password = account.resolve_password()

    supported = set()
    esmtp_features = server.esmtp_features
    for feature in esmtp_features:
        if feature.lower().startswith("auth"):
            supported = {m.upper() for m in esmtp_features[feature].split()}
    # Some servers report features oddly; fall back to advertised 'auth' key.
    if not supported and "auth" in esmtp_features:
        supported = {m.upper() for m in esmtp_features["auth"].split()}

    def try_plain() -> bool:
        if "PLAIN" not in supported:
            return False
        encoded = b64encode(sasl_plain(email_addr, password))
        try:
            code, _resp = server.docmd("AUTH", f"PLAIN {encoded}")
        except smtplib.SMTPException:
            return False
        if code == 235:
            return True
        return False

    def try_login() -> bool:
        if "LOGIN" not in supported:
            return False
        try:
            code, _ = server.docmd("AUTH", "LOGIN")
            if code == 334:
                code, _ = server.docmd(b64encode(sasl_login_user(email_addr)))
            if code == 334:
                code, _ = server.docmd(b64encode(sasl_login_password(password)))
        except smtplib.SMTPException:
            return False
        return code == 235

    def try_cram_md5() -> bool:
        if "CRAM-MD5" not in supported:
            return False
        try:
            code, resp = server.docmd("AUTH", "CRAM-MD5")
            if code != 334:
                return False
            answer = cram_md5_response(email_addr, password, resp.decode("ascii", "replace"))
            code, _ = server.docmd(b64encode(answer.encode("utf-8")))
        except smtplib.SMTPException:
            return False
        return code == 235

    def try_xoauth2() -> bool:
        mech = (
            "XOAUTH2"
            if "XOAUTH2" in supported
            else ("OAUTHBEARER" if "OAUTHBEARER" in supported else "")
        )
        token = account.oauth2_token
        if not mech or not token:
            return False
        if mech == "XOAUTH2":
            payload = b64encode(xoauth2_string(email_addr, token))
        else:
            payload = b64encode(oauthbearer_string(token))
        try:
            code, _ = server.docmd("AUTH", f"{mech} {payload}")
        except smtplib.SMTPException:
            return False
        return code == 235

    def try_ntlm() -> bool:
        if "NTLM" not in supported:
            return False
        try:
            code, _ = server.docmd("AUTH", f"NTLM {ntlm_type1_message()}")
            if code != 334:
                return False
            type2 = _.decode("ascii", "replace")
            type3 = ntlm_type3_message(email_addr, password, type2)
            code, _ = server.docmd(type3)
        except smtplib.SMTPException:
            return False
        return code == 235

    tried: list[str] = []
    if auth == "auto":
        chain = [try_plain, try_login, try_cram_md5, try_xoauth2, try_ntlm]
    else:
        mapping = {
            "plain": [try_plain, try_login],
            "login": [try_login, try_plain],
            "cram-md5": [try_cram_md5],
            "xoauth2": [try_xoauth2],
            "ntlm": [try_ntlm],
        }
        chain = mapping.get(auth, [try_plain, try_login])
    for attempt in chain:
        name = attempt.__name__.replace("try_", "").upper()
        tried.append(name)
        if attempt():
            return
    raise AuthError(f"All auth methods failed on {account.smtp_host} (tried: {', '.join(tried)})")


def smtp_send(
    account: Account,
    message: EmailMessage,
    to_addrs: list[str] | None = None,
) -> tuple[str, str]:
    """Send a message; returns (refused_summary, message_id)."""
    server = _connect(account)
    try:
        server.ehlo()
        _login(server, account)
        recipients = to_addrs or []
        if not recipients:
            for header in ("To", "Cc", "Bcc"):
                value = message.get(header)
                if value:
                    recipients.extend(
                        addr.strip() for addr in str(value).split(",") if addr.strip()
                    )
        msg_id = message.get("Message-ID", "")
        refused = server.send_message(message, from_addr=account.email, to_addrs=recipients)
        summary = ", ".join(f"{addr}: {reason}" for addr, reason in refused.items()) or ""
        return summary, str(msg_id)
    finally:
        try:
            server.quit()
        except Exception:
            pass
