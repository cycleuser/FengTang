"""Error types and the standardized ToolResult container."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class MailPilotError(Exception):
    """Base exception for all MailPilot errors."""


class ConfigError(MailPilotError):
    """Raised for missing/invalid configuration."""


class AuthError(MailPilotError):
    """Raised when authentication against a mail server fails."""


class ConnectionError_(MailPilotError):
    """Raised when connecting/talking to a mail server fails."""


class MessageError(MailPilotError):
    """Raised for malformed messages or missing local messages."""


class ServerError(MailPilotError):
    """Raised for errors while running the built-in mail server."""


@dataclass
class ToolResult:
    """
    Standardized result container for all MailPilot operations.

    Attributes:
        success: Whether the operation succeeded.
        data: The result data (if successful).
        error: Error message (if failed).
        metadata: Additional context information.
    """

    success: bool
    data: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.success

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata,
        }
