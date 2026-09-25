"""Outbound mail delivery with no external tools.

Two paths, both pure Python:

- **Direct-to-MX** (`direct_deliver`): resolve the recipient domain's MX with our
  own DNS client (`fengtang.serve.dns`), then speak SMTP on port 25 to each MX in
  preference order. No authentication, no relay — the classic MTA path.
- **Smarthost relay** (`relay_deliver`): hand the message to an authenticated
  upstream SMTP server (any account already configured in FengTang). This is what
  Postfix calls `relayhost` and it is the reliable path on networks that block or
  greylist direct port-25 delivery.
"""

from __future__ import annotations

import email
import email.policy
import smtplib
import socket
from dataclasses import dataclass

from fengtang.core.config import Account
from fengtang.serve.dns import resolve_mx


@dataclass
class DeliveryResult:
    """Outcome of an outbound delivery attempt."""

    delivered: bool
    via: str = ""  # "direct" | "smarthost"
    detail: str = ""
    host: str = ""

    def to_dict(self) -> dict:
        return {
            "delivered": self.delivered,
            "via": self.via,
            "detail": self.detail,
            "host": self.host,
        }


def _crlf(raw: bytes) -> bytes:
    """Normalise line endings to CRLF for SMTP DATA."""
    return raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def default_helo_host() -> str:
    """A plausible FQDN to announce; many servers reject bare 'localhost'."""
    fqdn = socket.getfqdn()
    if "." in fqdn and not fqdn.startswith("localhost"):
        return fqdn
    hostname = socket.gethostname() or "localhost"
    return f"{hostname}.local"


def direct_deliver(
    raw: bytes,
    sender: str,
    recipients: list[str],
    helo_host: str = "",
    timeout: float = 30.0,
) -> DeliveryResult:
    """Deliver directly to the recipients' MX servers on port 25."""
    if not recipients:
        return DeliveryResult(False, "direct", "no recipients")
    helo = helo_host or default_helo_host()
    errors: list[str] = []
    # Group recipients by their domain so each domain's MX is tried once.
    domains: list[str] = []
    for rcpt in recipients:
        domain = rcpt.rsplit("@", 1)[-1].lower()
        if domain and domain not in domains:
            domains.append(domain)

    for domain in domains:
        try:
            mx_hosts = resolve_mx(domain)
        except OSError as exc:
            errors.append(f"{domain}: MX lookup failed ({exc})")
            continue
        domain_rcpts = [r for r in recipients if r.lower().endswith("@" + domain)]
        delivered_this_domain = False
        for mx in mx_hosts:
            try:
                server = smtplib.SMTP(mx, 25, timeout=timeout)
            except (OSError, smtplib.SMTPException) as exc:
                errors.append(f"{mx}: connect failed ({exc})")
                continue
            try:
                server.ehlo(helo)
                refused = server.sendmail(sender, domain_rcpts, _crlf(raw))
                if refused:
                    errors.append(f"{mx}: refused {refused}")
                else:
                    delivered_this_domain = True
                    break
            except smtplib.SMTPException as exc:
                errors.append(f"{mx}: {type(exc).__name__}: {exc}")
            finally:
                try:
                    server.quit()
                except Exception:
                    pass
        if not delivered_this_domain:
            # Report the first domain's failure but keep trying the others.
            pass

    if errors and any("connect failed" in e and "refused" not in e for e in errors):
        # If nothing succeeded and we only hit connection errors, surface them.
        pass
    # Consider it delivered if no error was recorded for any domain.
    if not errors:
        return DeliveryResult(True, "direct", "accepted by MX", host=domains[0] if domains else "")
    # If we got at least one successful domain but others failed, still report failure
    # detail so the operator sees the problem.
    return DeliveryResult(False, "direct", "; ".join(errors), host=domains[0] if domains else "")


def rewrite_from(raw: bytes, new_from: str, original_sender: str) -> bytes:
    """Rewrite the From header to `new_from`, preserving the original.

    Upstream relays (QQ, Gmail, Outlook) reject mail whose From domain the
    authenticated account does not own, so a smarthost relay must send as the
    authenticated identity.
    """
    try:
        message = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception:
        return raw
    original = message.get("From", original_sender)
    if message["From"] is not None:
        message.replace_header("From", new_from)
    else:
        message["From"] = new_from
    if "Reply-To" not in message:
        message["Reply-To"] = original
    message["X-Original-From"] = original
    return message.as_bytes(policy=email.policy.default)


def relay_deliver(
    account: Account,
    raw: bytes,
    sender: str,
    recipients: list[str],
    timeout: float = 30.0,
    rewrite_sender: bool = True,
) -> DeliveryResult:
    """Relay the message through an authenticated upstream SMTP account."""
    from fengtang.mail.smtp_client import _connect, _login

    envelope_sender = account.email or sender
    if rewrite_sender and account.email:
        raw = rewrite_from(raw, account.email, sender)
    try:
        server = _connect(account)
    except Exception as exc:
        return DeliveryResult(False, "smarthost", f"connect failed: {exc}")
    try:
        server.ehlo()
        _login(server, account)
        refused = server.sendmail(envelope_sender, recipients, _crlf(raw))
        if refused:
            return DeliveryResult(False, "smarthost", f"refused: {refused}", host=account.smtp_host)
        return DeliveryResult(True, "smarthost", "accepted", host=account.smtp_host)
    except Exception as exc:
        return DeliveryResult(
            False, "smarthost", f"{type(exc).__name__}: {exc}", host=account.smtp_host
        )
    finally:
        try:
            server.quit()
        except Exception:
            pass


def deliver(
    raw: bytes,
    sender: str,
    recipients: list[str],
    relay_account: Account | None = None,
    mode: str = "auto",
    helo_host: str = "",
    rewrite_sender: bool = True,
) -> DeliveryResult:
    """Deliver a message outbound.

    mode:
      - "direct":    only direct-to-MX
      - "smarthost": only relay through `relay_account`
      - "auto":      try direct-to-MX first, fall back to the smarthost
    """
    mode = (mode or "auto").lower()
    if mode == "smarthost":
        if relay_account is None:
            return DeliveryResult(False, "smarthost", "no relay account configured")
        return relay_deliver(relay_account, raw, sender, recipients, rewrite_sender=rewrite_sender)

    direct = direct_deliver(raw, sender, recipients, helo_host=helo_host)
    if direct.delivered or mode == "direct" or relay_account is None:
        return direct
    relay = relay_deliver(relay_account, raw, sender, recipients, rewrite_sender=rewrite_sender)
    if relay.delivered:
        relay.detail = f"direct failed ({direct.detail}); {relay.detail}"
    else:
        relay.detail = f"direct failed ({direct.detail}); relay failed ({relay.detail})"
    return relay
