"""FengTang(冯唐)- pure-Python command-line mail client with built-in server and agent API.

Named after the Han-dynasty official Feng Tang, who was sent bearing the
imperial tally (持节) to Yunzhong to deliver the emperor's edict of pardon
(赦书) and restore the governor Wei Shang. May every message be carried as
faithfully as that pardon, and arrive exactly as it must.
"""

__version__ = "0.1.1"
__author__ = "FengTang Contributors"
__license__ = "GPL-3.0-or-later"

APP_NAME = "FengTang"


def __getattr__(name: str):
    """Lazy import so CLI startup stays fast."""
    if name == "ToolResult":
        from fengtang.core.errors import ToolResult

        return ToolResult
    if name == "FengTangError":
        from fengtang.core.errors import FengTangError

        return FengTangError
    if name == "Config":
        from fengtang.core.config import Config

        return Config
    if name == "load_config":
        from fengtang.core.config import load_config

        return load_config
    if name == "save_config":
        from fengtang.core.config import save_config

        return save_config
    if name == "Account":
        from fengtang.core.config import Account

        return Account
    if name == "send_mail":
        from fengtang.api import send_mail

        return send_mail
    if name == "list_messages":
        from fengtang.api import list_messages

        return list_messages
    if name == "read_message":
        from fengtang.api import read_message

        return read_message
    if name == "search_messages":
        from fengtang.api import search_messages

        return search_messages
    if name == "mark_messages":
        from fengtang.api import mark_messages

        return mark_messages
    if name == "delete_messages":
        from fengtang.api import delete_messages

        return delete_messages
    if name == "fetch_messages":
        from fengtang.api import fetch_messages

        return fetch_messages
    raise AttributeError(f"module 'fengtang' has no attribute '{name}'")


__all__ = [
    "__version__",
    "__author__",
    "__license__",
    "APP_NAME",
    "ToolResult",
    "FengTangError",
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
