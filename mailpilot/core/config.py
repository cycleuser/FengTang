"""Configuration management: accounts, profiles, persistence as JSON."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from mailpilot.core.errors import ConfigError

# Well-known provider presets. Empty auth means "auto-detect".
PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "gmail": {
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "imap_ssl": True,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        "smtp_ssl": True,
        "pop_host": "pop.gmail.com",
        "pop_port": 995,
        "pop_ssl": True,
        "auth": "xoauth2",
    },
    "outlook": {
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "imap_ssl": True,
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "smtp_ssl": False,
        "smtp_starttls": True,
        "pop_host": "outlook.office365.com",
        "pop_port": 995,
        "pop_ssl": True,
        "auth": "xoauth2",
    },
    "yahoo": {
        "imap_host": "imap.mail.yahoo.com",
        "imap_port": 993,
        "imap_ssl": True,
        "smtp_host": "smtp.mail.yahoo.com",
        "smtp_port": 465,
        "smtp_ssl": True,
        "pop_host": "pop.mail.yahoo.com",
        "pop_port": 995,
        "pop_ssl": True,
        "auth": "plain",
    },
    "icloud": {
        "imap_host": "imap.mail.me.com",
        "imap_port": 993,
        "imap_ssl": True,
        "smtp_host": "smtp.mail.me.com",
        "smtp_port": 587,
        "smtp_ssl": False,
        "smtp_starttls": True,
        "pop_host": "pop.mail.me.com",
        "pop_port": 995,
        "pop_ssl": True,
        "auth": "plain",
    },
    "qq": {
        "imap_host": "imap.qq.com",
        "imap_port": 993,
        "imap_ssl": True,
        "smtp_host": "smtp.qq.com",
        "smtp_port": 465,
        "smtp_ssl": True,
        "pop_host": "pop.qq.com",
        "pop_port": 995,
        "pop_ssl": True,
        "auth": "login",
    },
    "163": {
        "imap_host": "imap.163.com",
        "imap_port": 993,
        "imap_ssl": True,
        "smtp_host": "smtp.163.com",
        "smtp_port": 465,
        "smtp_ssl": True,
        "pop_host": "pop.163.com",
        "pop_port": 995,
        "pop_ssl": True,
        "auth": "plain",
    },
    "126": {
        "imap_host": "imap.126.com",
        "imap_port": 993,
        "imap_ssl": True,
        "smtp_host": "smtp.126.com",
        "smtp_port": 465,
        "smtp_ssl": True,
        "pop_host": "pop.126.com",
        "pop_port": 995,
        "pop_ssl": True,
        "auth": "plain",
    },
    "sina": {
        "imap_host": "imap.sina.com",
        "imap_port": 993,
        "imap_ssl": True,
        "smtp_host": "smtp.sina.com",
        "smtp_port": 465,
        "smtp_ssl": True,
        "pop_host": "pop.sina.com",
        "pop_port": 995,
        "pop_ssl": True,
        "auth": "login",
    },
    "aliyun": {
        "imap_host": "imap.aliyun.com",
        "imap_port": 993,
        "imap_ssl": True,
        "smtp_host": "smtp.aliyun.com",
        "smtp_port": 465,
        "smtp_ssl": True,
        "pop_host": "pop3.aliyun.com",
        "pop_port": 995,
        "pop_ssl": True,
        "auth": "login",
    },
    "zoho": {
        "imap_host": "imap.zoho.com",
        "imap_port": 993,
        "imap_ssl": True,
        "smtp_host": "smtp.zoho.com",
        "smtp_port": 465,
        "smtp_ssl": True,
        "pop_host": "pop.zoho.com",
        "pop_port": 995,
        "pop_ssl": True,
        "auth": "plain",
    },
    "outlook-pop": {
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "imap_ssl": True,
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "smtp_ssl": False,
        "smtp_starttls": True,
        "pop_host": "pop-mail.outlook.com",
        "pop_port": 995,
        "pop_ssl": True,
        "auth": "plain",
    },
}

VALID_AUTH_METHODS = {"plain", "login", "cram-md5", "xoauth2", "ntlm", "apop", "auto"}


@dataclass
class Account:
    """One mail account (used for both remote servers and the built-in server)."""

    name: str
    email: str
    password: str = ""
    auth: str = "auto"  # plain|login|cram-md5|xoauth2|ntlm|apop|auto
    imap_host: str = ""
    imap_port: int = 993
    imap_ssl: bool = True
    imap_starttls: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_ssl: bool = True
    smtp_starttls: bool = False
    pop_host: str = ""
    pop_port: int = 995
    pop_ssl: bool = True
    pop_starttls: bool = False
    default: bool = False
    oauth2_token: str = ""  # access token cache (refresh handled by caller)
    extra: dict[str, Any] = field(default_factory=dict)

    def apply_preset(self, provider: str) -> None:
        """Fill host/port fields from a well-known provider preset."""
        preset = PROVIDER_PRESETS.get(provider.lower())
        if preset is None:
            raise ConfigError(
                f"Unknown provider preset: {provider!r}. "
                f"Available: {', '.join(sorted(PROVIDER_PRESETS))}"
            )
        for key, value in preset.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def validate(self) -> None:
        """Basic sanity checks."""
        if not self.name:
            raise ConfigError("Account name must not be empty")
        if not self.email or "@" not in self.email:
            raise ConfigError(f"Account {self.name!r} has invalid email: {self.email!r}")
        if self.auth not in VALID_AUTH_METHODS:
            raise ConfigError(
                f"Account {self.name!r}: unknown auth method {self.auth!r}. "
                f"Valid: {', '.join(sorted(VALID_AUTH_METHODS))}"
            )


@dataclass
class Config:
    """Top-level MailPilot configuration."""

    accounts: list[Account] = field(default_factory=list)
    data_dir: str = ""  # resolved lazily by data_path()
    default_account: str = ""

    # ---- paths ----

    def _resolve_data_dir(self) -> Path:
        if self.data_dir:
            return Path(self.data_dir).expanduser()
        env = os.environ.get("MAILPILOT_DATA_DIR")
        if env:
            return Path(env).expanduser()
        return Path.home() / ".mailpilot"

    def data_path(self) -> Path:
        """The data directory (created on demand)."""
        path = self._resolve_data_dir()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def db_path(self) -> Path:
        return self.data_path() / "mailpilot.db"

    def config_path(self) -> Path:
        return self.data_path() / "config.json"

    # ---- account helpers ----

    def get_account(self, name: str | None = None) -> Account:
        """Return the named account, the default account, or the only account."""
        if self.accounts and name is None:
            if self.default_account:
                for acct in self.accounts:
                    if acct.name == self.default_account:
                        return acct
            if len(self.accounts) == 1:
                return self.accounts[0]
            names = ", ".join(a.name for a in self.accounts)
            raise ConfigError(f"Multiple accounts configured ({names}); specify --account")
        for acct in self.accounts:
            if acct.name == name:
                return acct
        raise ConfigError(f"Account not found: {name!r}")


def _write_config_atomic(path: Path, payload: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    # Restrict permissions early: config contains credentials.
    try:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    tmp.replace(path)


def save_config(config: Config, path: Path | None = None) -> Path:
    """Persist config as JSON; returns the path written."""
    target = path or config.config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "accounts": [asdict(a) for a in config.accounts],
            "data_dir": config.data_dir,
            "default_account": config.default_account,
        },
        indent=2,
        ensure_ascii=False,
    )
    _write_config_atomic(target, payload)
    return target


def load_config(path: Path | None = None) -> Config:
    """Load config from disk; missing file yields an empty Config."""
    if path is None:
        probe = Config()
        target = probe.config_path()
    else:
        target = path
    if not target.exists():
        return Config()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {target}: {exc}") from exc
    accounts = [Account(**item) for item in raw.get("accounts", [])]
    return Config(
        accounts=accounts,
        data_dir=raw.get("data_dir", ""),
        default_account=raw.get("default_account", ""),
    )


def add_account(
    config: Config,
    name: str,
    email: str,
    password: str = "",
    auth: str = "auto",
    provider: str = "",
    **overrides: Any,
) -> Account:
    """Create (or replace) an account, optionally from a provider preset."""
    account = Account(name=name, email=email, password=password, auth=auth)
    if provider:
        account.apply_preset(provider)
    for key, value in overrides.items():
        if value in (None, ""):
            continue
        if hasattr(account, key):
            setattr(account, key, value)
    account.validate()
    config.accounts = [a for a in config.accounts if a.name != name]
    config.accounts.append(account)
    if not config.default_account or not config.default_account:
        config.default_account = name
    return account


def setup_logging(debug: bool = False, verbose: bool = False) -> None:
    """Configure root logging for CLI use."""
    import logging

    if debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    else:
        level = logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Quiet the very chatty stdlib protocol loggers unless debugging.
    if not debug:
        for name in ("imaplib", "smtplib", "poplib", "asyncio"):
            logging.getLogger(name).setLevel(logging.WARNING)
