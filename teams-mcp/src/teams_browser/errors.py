"""Structured error hierarchy.

Every failure mode the CLI/MCP needs to react to has a distinct class so the
caller can decide whether to retry, prompt for login, or give up.
"""

from __future__ import annotations


class TeamsBrowserError(Exception):
    """Base class for all errors raised by teams_browser."""


class AuthRequired(TeamsBrowserError):
    """No usable session exists; an interactive login is required."""


class TokenExpired(TeamsBrowserError):
    """A session exists but its tokens are no longer valid.

    Usually recoverable by a silent refresh; if the refresh token is dead, this
    escalates to :class:`AuthRequired`.
    """


class ApiError(TeamsBrowserError):
    """An upstream Teams/Substrate API call failed."""

    def __init__(self, status: int, message: str, *, body: str | None = None):
        super().__init__(f'HTTP {status}: {message}')
        self.status = status
        self.body = body


class RateLimited(ApiError):
    """Upstream returned 429; ``retry_after`` carries the wait, if provided."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(429, message)
        self.retry_after = retry_after


class ResourceNotFound(TeamsBrowserError):
    """The requested meeting/transcript does not exist (or is not visible)."""


class TranscriptUnavailable(ResourceNotFound):
    """The meeting exists but has no transcript (recording off, not processed)."""


class ConfigError(TeamsBrowserError):
    """The session is present but missing a required piece of configuration."""
