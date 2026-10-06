"""Path safety for the notes vault.

Every path that crosses the MCP boundary goes through :class:`PathGuard`. It
resolves symlinks, refuses anything outside the vault, and blocks the system
corners of the vault (git internals, Obsidian config, agent instructions) and
all non-markdown writes. Writes under ``00 Meta/`` are blocked except the
conflict staging area.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .errors import PATH_REJECTED, NotesError


_FORBIDDEN_PARTS = frozenset({'.git', '.obsidian', '.githooks'})
_FILENAME_BAD = set('/\\:*?"<>|')
_META_DIR = '00 Meta'
_CONFLICTS_DIR = '00 Meta/03 Conflicts'


class PathGuard:
    def __init__(self, vault_path: Path) -> None:
        self.vault = Path(os.path.realpath(vault_path))

    def resolve(self, rel: str, *, for_write: bool = False) -> Path:
        """Resolve ``rel`` against the vault, following symlinks.

        Raises :class:`NotesError` (``PATH_REJECTED``) when the result leaves
        the vault or hits a protected location.
        """
        candidate = os.path.realpath(Path(self.vault) / rel)
        vault = str(self.vault)
        if candidate != vault and not candidate.startswith(vault + os.sep):
            raise self._reject(rel, 'path escapes the vault')

        rel_posix = os.path.relpath(candidate, vault).replace(os.sep, '/')
        parts = rel_posix.split('/')

        for part in parts:
            if part in _FORBIDDEN_PARTS:
                raise self._reject(rel, f'{part!r} is off-limits')

        if parts[-1] == 'AGENTS.md':
            raise self._reject(rel, 'AGENTS.md is off-limits')

        if for_write:
            if not rel_posix.endswith('.md'):
                raise self._reject(rel, 'only .md files may be written')
            if rel_posix.startswith(_META_DIR + '/') and not rel_posix.startswith(_CONFLICTS_DIR + '/'):
                raise self._reject(rel, 'writes under 00 Meta/ are blocked')

        return Path(candidate)

    def relpath(self, path: Path) -> str:
        """Return ``path`` as a vault-relative POSIX string."""
        real = os.path.realpath(path)
        return os.path.relpath(real, str(self.vault)).replace(os.sep, '/')

    def sanitize_filename(self, name: str) -> str:
        """Strip separators/control punctuation and collapse whitespace."""
        cleaned = ''.join(ch for ch in name if ch not in _FILENAME_BAD)
        return re.sub(r'\s+', ' ', cleaned).strip()

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
