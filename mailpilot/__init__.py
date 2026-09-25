"""MailPilot - pure-Python command-line mail client with built-in server and agent API."""

__version__ = "0.0.4"
__author__ = "MailPilot Contributors"
__license__ = "GPL-3.0-or-later"

APP_NAME = "MailPilot"


def __getattr__(name: str):
    """Lazy import so CLI startup stays fast."""
    if name == "ToolResult":
        from mailpilot.core.errors import ToolResult

        return ToolResult
    if name == "MailPilotError":
        from mailpilot.core.errors import MailPilotError

        return MailPilotError
    if name == "Config":
        from mailpilot.core.config import Config

        return Config
    if name == "load_config":
        from mailpilot.core.config import load_config

        return load_config
    if name == "save_config":
        from mailpilot.core.config import save_config

        return save_config
    if name == "Account":
        from mailpilot.core.config import Account

        return Account
    if name == "send_mail":
        from mailpilot.api import send_mail

        return send_mail
    if name == "list_messages":
        from mailpilot.api import list_messages

        return list_messages
    if name == "read_message":
        from mailpilot.api import read_message

        return read_message
    if name == "search_messages":
        from mailpilot.api import search_messages

        return search_messages
    if name == "mark_messages":
        from mailpilot.api import mark_messages

        return mark_messages
    if name == "delete_messages":
        from mailpilot.api import delete_messages

        return delete_messages
    if name == "fetch_messages":
        from mailpilot.api import fetch_messages

        return fetch_messages
    raise AttributeError(f"module 'mailpilot' has no attribute '{name}'")


__all__ = [
    "__version__",
    "__author__",
    "__license__",
    "APP_NAME",
    "ToolResult",
    "MailPilotError",
    "Config",
    "Account",
    "load_config",
    "save_config",
    "send_mail",
    "list_messages",
    "read_message",
    "search_messages",
    "mark_messages",
    "delete_messages",
    "fetch_messages",
]
