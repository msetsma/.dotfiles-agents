"""Path safety for the notes vault.

Every path that crosses the MCP boundary goes through :class:`PathGuard`. It
resolves symlinks, refuses anything outside the vault, and blocks the system
corners of the vault (git internals, Obsidian config, agent instructions) and
all non-markdown writes. Writes under ``00 Meta/`` are blocked except the
conflict staging area.

All forbidden-part checks are case-insensitive because APFS is case-insensitive:
``.GIT/`` and ``agents.md`` must not slip past their lowercase counterparts.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .errors import PATH_REJECTED, NotesError


_FORBIDDEN_PARTS = frozenset({'.git', '.obsidian', '.githooks'})
_FILENAME_BAD = set('/\\:*?"<>|')
_META_DIR = '00 Meta'
_META_CASEFOLD = _META_DIR.casefold()
_CONFLICTS_DIR = '00 Meta/03 Conflicts'
_CONFLICTS_CASEFOLD = _CONFLICTS_DIR.casefold()
_INDEX_FILENAME = '00.00 Index.md'


def _is_index(rel_posix: str) -> bool:
    """True for exactly ``00 Meta/00.00 Index.md`` (dir case-insensitive, name exact)."""
    directory, sep, filename = rel_posix.rpartition('/')
    return bool(sep) and directory.casefold() == _META_CASEFOLD and filename == _INDEX_FILENAME


class PathGuard:
    def __init__(self, vault_path: Path) -> None:
        self.vault = Path(os.path.realpath(vault_path))

    def resolve(self, rel: str, *, for_write: bool = False, allow_index: bool = False) -> Path:
        """Resolve ``rel`` against the vault, following symlinks.

        Raises :class:`NotesError` (``PATH_REJECTED``) when the result leaves
        the vault or hits a protected location. ``allow_index=True`` (used only
        by the category-move tool) additionally permits writing
        ``00 Meta/00.00 Index.md``; every other ``00 Meta/`` write stays blocked.
        """
        candidate = os.path.realpath(Path(self.vault) / rel)
        vault = str(self.vault)
        if candidate != vault and not candidate.startswith(vault + os.sep):
            raise self._reject(rel, 'path escapes the vault')

        rel_posix = os.path.relpath(candidate, vault).replace(os.sep, '/')
        parts = rel_posix.split('/')

        for part in parts:
            if part.casefold() in _FORBIDDEN_PARTS:
                raise self._reject(rel, f'{part!r} is off-limits')

        if parts[-1].casefold() == 'agents.md':
            raise self._reject(rel, 'AGENTS.md is off-limits')

        if for_write:
            if not rel_posix.endswith('.md'):
                raise self._reject(rel, 'only .md files may be written')
            lowered = rel_posix.casefold()
            under_meta = lowered.startswith(_META_CASEFOLD + '/')
            under_conflicts = lowered.startswith(_CONFLICTS_CASEFOLD + '/')
            index_write = allow_index and _is_index(rel_posix)
            if under_meta and not under_conflicts and not index_write:
                raise self._reject(rel, 'writes under 00 Meta/ are blocked')

        return Path(candidate)

    def relpath(self, path: Path) -> str:
        """Return ``path`` as a vault-relative POSIX string."""
        real = os.path.realpath(path)
        return os.path.relpath(real, str(self.vault)).replace(os.sep, '/')

    def sanitize_filename(self, name: str) -> str:
        """Strip separators/control punctuation and collapse whitespace.

        Raises :class:`NotesError` (``PATH_REJECTED``) when the cleaned name is
        empty, ``.``/``..``, or hidden (starts with ``.``).
        """
        cleaned = ''.join(ch for ch in name if ch not in _FILENAME_BAD)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        if not cleaned or cleaned in {'.', '..'} or cleaned.startswith('.'):
            raise self._reject(name, 'filename is empty, dot-only, or hidden')
        return cleaned

    def is_meta(self, rel: str) -> bool:
        norm = self._norm(rel)
        return norm == _META_DIR or norm.startswith(_META_DIR + '/')

    def is_conflicts(self, rel: str) -> bool:
        norm = self._norm(rel)
        return norm == _CONFLICTS_DIR or norm.startswith(_CONFLICTS_DIR + '/')

    @staticmethod
    def _norm(rel: str) -> str:
        return rel.replace(os.sep, '/').strip('/')

    @staticmethod
    def _reject(rel: str, reason: str) -> NotesError:
        return NotesError(
            PATH_REJECTED,
            f'path rejected: {rel!r} ({reason})',
            'Use a vault-relative markdown path outside protected directories.',
            path=rel,
        )
