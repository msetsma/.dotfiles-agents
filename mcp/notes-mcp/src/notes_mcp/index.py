"""Vault category index, parsed from ``00 Meta/00.00 Index.md``.

The index is the single source of truth for category IDs, names, paths, and
scopes. The parsed table is cached and reloaded only when the file's mtime
changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .errors import CATEGORY_NOT_FOUND, INDEX_UNAVAILABLE, NotesError


INDEX_REL_PATH = '00 Meta/00.00 Index.md'
_HEADERS = ('ID', 'Category', 'Path', 'Scope')


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
        for category in self.categories():
            if norm == category.path or norm.startswith(category.path + '/'):
                return category
        return None

    def validate(self, category_id: str) -> Category:
        category = self.get(category_id)
        if not (self.config.vault_path / category.path).is_dir():
            raise self._not_found(category_id, category.path)
        return category

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

    header_at = None
    for index, line in enumerate(lines):
        if _cells(line) == list(_HEADERS):
            header_at = index
            break
    if header_at is None:
        raise VaultIndex._unavailable(f'{INDEX_REL_PATH} has no ID/Category/Path/Scope table')

    categories: list[Category] = []
    for line in lines[header_at + 1 :]:
        cells = _cells(line)
        if not cells:
            continue
        if _is_separator(cells):
            continue
        if len(cells) < 4:
            continue
        categories.append(Category(id=cells[0], name=cells[1], path=cells[2], scope=cells[3]))
    if not categories:
        raise VaultIndex._unavailable(f'{INDEX_REL_PATH} table has no rows')
    return categories


def _cells(line: str) -> list[str]:
    if '|' not in line:
        return []
    stripped = line.strip().strip('|')
    return [cell.strip() for cell in stripped.split('|')]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(cell and set(cell) <= set('-: ') for cell in cells)
