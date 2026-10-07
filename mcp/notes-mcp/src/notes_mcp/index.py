"""Vault category index, parsed from ``00 Meta/00.00 Index.md``.

The index is the single source of truth for category IDs, names, paths, and
scopes. The parsed table is cached and reloaded only when the file's mtime
changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .atomic import atomic_write
from .config import Config
from .errors import CATEGORY_NOT_FOUND, INDEX_UNAVAILABLE, NotesError


INDEX_REL_PATH = '00 Meta/00.00 Index.md'
_HEADERS = ('ID', 'Category', 'Path', 'Scope')
_PATH_COLUMN = 2


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    path: str
    scope: str


class VaultIndex:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.index_path = config.vault_path / INDEX_REL_PATH
        self._cache: list[Category] | None = None
        self._mtime: float | None = None

    def categories(self, refresh: bool = False) -> list[Category]:
        try:
            mtime = self.index_path.stat().st_mtime
        except OSError as exc:
            raise self._unavailable(f'index file not found: {INDEX_REL_PATH}') from exc

        if self._cache is not None and not refresh and self._mtime == mtime:
            return self._cache

        try:
            text = self.index_path.read_text(encoding='utf-8')
            parsed = _parse_categories(text)
        except OSError as exc:
            raise self._unavailable(f'cannot read {INDEX_REL_PATH}: {exc}') from exc

        self._cache = parsed
        self._mtime = mtime
        return parsed

    def get(self, category_id: str) -> Category:
        for category in self.categories():
            if category.id == category_id:
                return category
        raise self._not_found(category_id)

    def by_path_prefix(self, rel: str) -> Category | None:
        norm = rel.replace('\\', '/').strip('/')
        best: Category | None = None
        best_len = -1
        for category in self.categories():
            if self._category_path(category) is None:
                continue
            path = category.path.strip().replace('\\', '/').strip('/')
            if not path:
                continue
            if (norm == path or norm.startswith(path + '/')) and len(path) > best_len:
                best = category
                best_len = len(path)
        return best

    def validate(self, category_id: str) -> Category:
        category = self.get(category_id)
        target = self._category_path(category)
        if target is None or not target.is_dir():
            raise self._not_found(category_id, category.path)
        return category

    def rewrite_path(self, category_id: str, new_path: str) -> str:
        """Return the Index text with ``category_id``'s Path cell replaced.

        Every other character, row, and the header/separator are preserved.
        """
        try:
            text = self.index_path.read_text(encoding='utf-8')
        except OSError as exc:
            raise self._unavailable(f'cannot read {INDEX_REL_PATH}: {exc}') from exc

        lines = text.splitlines(keepends=True)
        header_at = _find_header(lines)
        if header_at is None:
            raise self._unavailable(f'{INDEX_REL_PATH} has no ID/Category/Path/Scope table')

        for offset, line in enumerate(lines[header_at + 1 :]):
            cells = _cells(line.rstrip('\r\n'))
            if len(cells) < 4 or _is_separator(cells):
                continue
            if cells[0] == category_id:
                index = header_at + 1 + offset
                lines[index] = _replace_cell(line, _PATH_COLUMN, new_path)
                return ''.join(lines)
        raise self._not_found(category_id)

    def apply_path(self, category_id: str, new_path: str) -> None:
        """Rewrite the Index Path cell on disk and invalidate the parsed cache."""
        text = self.rewrite_path(category_id, new_path)
        atomic_write(self.index_path, text)
        self._cache = None
        self._mtime = None

    def _category_path(self, category: Category) -> Path | None:
        """Resolve a table path, or ``None`` if it escapes the vault."""
        raw = category.path.strip()
        if not raw:
            return None
        candidate = Path(raw)
        if candidate.is_absolute():
            return None
        vault_real = Path(os.path.realpath(self.config.vault_path))
        resolved = Path(os.path.realpath(vault_real / candidate))
        if resolved == vault_real or vault_real in resolved.parents:
            return resolved
        return None

    def _not_found(self, category_id: str, path: str | None = None) -> NotesError:
        context = {'categoryId': category_id}
        if path is not None:
            context['path'] = path
        return NotesError(
            CATEGORY_NOT_FOUND,
            f'category {category_id!r} is not available',
            'Call notes_index and use an ID from the index table.',
            **context,
        )

    @staticmethod
    def _unavailable(message: str) -> NotesError:
        return NotesError(
            INDEX_UNAVAILABLE, message, f'Ensure {INDEX_REL_PATH} exists and contains the ID/Category/Path/Scope table.'
        )


def _parse_categories(text: str) -> list[Category]:
    lines = text.splitlines()

    header_at = _find_header(lines)
    if header_at is None:
        raise VaultIndex._unavailable(f'{INDEX_REL_PATH} has no ID/Category/Path/Scope table')

    categories: list[Category] = []
    seen: set[str] = set()
    for line in lines[header_at + 1 :]:
        cells = _cells(line)
        if len(cells) < 4 or _is_separator(cells):
            continue
        category_id = cells[0]
        if not category_id:
            raise VaultIndex._unavailable(f'{INDEX_REL_PATH} contains a row with an empty ID')
        if category_id in seen:
            raise VaultIndex._unavailable(f'{INDEX_REL_PATH} lists duplicate ID {category_id!r}')
        seen.add(category_id)
        categories.append(Category(id=category_id, name=cells[1], path=cells[2], scope=cells[3]))
    if not categories:
        raise VaultIndex._unavailable(f'{INDEX_REL_PATH} table has no rows')
    return categories


def _find_header(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if _cells(line.rstrip('\r\n')) == list(_HEADERS):
            return index
    return None


def _scan_cells(line: str) -> list[tuple[int, int]]:
    """Character spans of each cell (between unescaped pipes, pipes excluded)."""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    escaped = False
    for index, char in enumerate(line):
        if char == '\\' and not escaped:
            if start is None:
                start = index
            escaped = True
        elif char == '|' and not escaped:
            if start is not None:
                spans.append((start, index))
                start = None
        else:
            if start is None:
                start = index
            escaped = False
    if start is not None:
        spans.append((start, len(line)))
    return spans


def _cells(line: str) -> list[str]:
    if '|' not in line:
        return []
    return [line[start:end].replace('\\|', '|').strip() for start, end in _scan_cells(line)]


def _replace_cell(line: str, column: int, value: str) -> str:
    spans = _scan_cells(line)
    if column >= len(spans):
        return line
    start, end = spans[column]
    segment = line[start:end]
    leading = segment[: len(segment) - len(segment.lstrip())]
    trailing = segment[len(segment.rstrip()) :]
    return line[:start] + leading + value + trailing + line[end:]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(cell and set(cell) <= set('-: ') for cell in cells)
