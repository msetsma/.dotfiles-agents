"""Structured errors for the notes MCP server.

Every tool returns a plain dict; failures use the envelope
``{"ok": False, "error": {"code", "message", "hint", **context}}``.
"""

from __future__ import annotations

from typing import Any


CONFLICT = 'CONFLICT'
CATEGORY_NOT_FOUND = 'CATEGORY_NOT_FOUND'
DUPLICATE_FILENAME = 'DUPLICATE_FILENAME'
FIND_NOT_UNIQUE = 'FIND_NOT_UNIQUE'
PUSH_FAILED_LOCAL_COMMITTED = 'PUSH_FAILED_LOCAL_COMMITTED'
PATH_REJECTED = 'PATH_REJECTED'
INVALID_FRONTMATTER = 'INVALID_FRONTMATTER'
INDEX_UNAVAILABLE = 'INDEX_UNAVAILABLE'
CONFIG_ERROR = 'CONFIG_ERROR'
NOT_FOUND = 'NOT_FOUND'
# Not a failure: the write proceeded but a transient sync failure (offline
# remote) left the vault ahead of/behind the remote. Annotates a success payload.
PENDING_SYNC = 'PENDING_SYNC'

ALL_CODES = (
    CONFLICT,
    CATEGORY_NOT_FOUND,
    DUPLICATE_FILENAME,
    FIND_NOT_UNIQUE,
    PUSH_FAILED_LOCAL_COMMITTED,
    PATH_REJECTED,
    INVALID_FRONTMATTER,
    INDEX_UNAVAILABLE,
    CONFIG_ERROR,
    NOT_FOUND,
    PENDING_SYNC,
)


class NotesError(Exception):
    """A tool-level failure carrying a code and a caller-actionable hint."""

    def __init__(self, code: str, message: str, hint: str = '', **context: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.context = context

    def to_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {'code': self.code, 'message': self.message, 'hint': self.hint}
        error.update(self.context)
        return {'ok': False, 'error': error}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f'NotesError({self.code!r}, {self.message!r})'


def notes_error(code: str, message: str, hint: str = '', **context: Any) -> NotesError:
    return NotesError(code, message, hint, **context)
