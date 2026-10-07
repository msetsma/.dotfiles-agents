"""Read-only tools for the notes MCP server (CONTRACT section 10).

Every function takes an explicit tool context first (duck-typed: ``config``,
``guard``, ``index``, ``search``, ``git``) and returns a plain dict. Success
payloads carry the result envelope tail; :class:`NotesError` failures are
converted with :meth:`NotesError.to_dict`. Unexpected exceptions propagate.

Note: the published MCP schema uses camelCase argument names. The clean-python
gate forbids camelCase arguments (N803) and the builtin ``type`` (A002), so the
Python signatures stay snake_case and ``notes_search`` accepts the ``type``
keyword as an alias for ``type_`` -- the same deviation the ``filter_`` adapter
in :mod:`notes_mcp.search` makes.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from notes_mcp.errors import NOT_FOUND, NotesError, notes_error
from notes_mcp.frontmatter import parse as parse_frontmatter
from notes_mcp.index import INDEX_REL_PATH
from notes_mcp.layout import resolve as resolve_layout


if TYPE_CHECKING:
    from notes_mcp.context import ToolContext


_DURATION = re.compile(r'^(\d+)([dhm])$')
_DURATION_UNITS = {'d': 'days', 'h': 'hours', 'm': 'minutes'}


# --------------------------------------------------------------------------- #
# envelope
# --------------------------------------------------------------------------- #
def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    """Merge ``payload`` with the success envelope tail."""
    return {'ok': True, **payload, 'commit': None, 'pending_push': False, 'needs_index_update': False}


# --------------------------------------------------------------------------- #
# tools
# --------------------------------------------------------------------------- #
def notes_index(ctx: ToolContext) -> dict:
    """List the categories from ``00 Meta/00.00 Index.md``."""
    try:
        categories = ctx.index.categories()
    except NotesError as exc:
        return exc.to_dict()
    return _ok(
        {
            'categories': [
                {'id': category.id, 'name': category.name, 'path': category.path, 'scope': category.scope}
                for category in categories
            ],
            'index_path': INDEX_REL_PATH,
        }
    )


@dataclass(frozen=True)
class _Criteria:
    """Post-filter criteria for :func:`notes_search`.

    ``prefix`` (from ``category_id``), ``area`` and ``domain`` match on the hit
    path; the rest read the hit's frontmatter. ``created_after``/``updated_after``
    are inclusive ``>=`` and ``created_before``/``updated_before`` inclusive
    ``<=``, compared on the date part of the note's ``created``/``updated``.
    """

    prefix: str | None = None
    area: str | None = None
    type_: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    domain: str | None = None
    created_after: str | None = None
    created_before: str | None = None
    updated_after: str | None = None
    updated_before: str | None = None

    @property
    def needs_frontmatter(self) -> bool:
        """True when any criterion can only be evaluated from the frontmatter."""
        return bool(self.tags) or any(
            value is not None
            for value in (
                self.type_,
                self.status,
                self.created_after,
                self.created_before,
                self.updated_after,
                self.updated_before,
            )
        )


def notes_search(
    ctx: ToolContext,
    query: str,
    *,
    area: str | None = None,
    category_id: str | None = None,
    type_: str | None = None,
    status: str | None = None,
    tags: list[str] | None = None,
    domain: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    updated_after: str | None = None,
    updated_before: str | None = None,
    limit: int = 10,
    rerank: bool = True,
    **options: object,
) -> dict:
    """Search the vault and post-filter hits by path/frontmatter.

    ``qmd --filter`` proved unreliable, so filtering happens here: ``category_id``,
    ``area`` and ``domain`` match on the hit path, ``type_``/``status``/``tags``
    parse the hit's frontmatter, and the four date bounds read the frontmatter's
    ``created``/``updated``. When any filter is set the search requests an
    oversampled pool (up to ``50``) before post-filtering, because qmd applies its
    own limit before we can filter. ``type`` is accepted as an alias for ``type_``.
    """
    if 'type' in options:
        type_ = options.pop('type')  # type: ignore[assignment]
    if options:
        unknown = ', '.join(sorted(options))
        raise TypeError(f'unexpected keyword argument(s): {unknown}')

    criteria = _Criteria(
        area=area,
        type_=type_,
        status=status,
        tags=tags,
        domain=domain,
        created_after=created_after,
        created_before=created_before,
        updated_after=updated_after,
        updated_before=updated_before,
    )
    filtering = any(value is not None for value in (area, category_id, domain)) or criteria.needs_frontmatter
    pool = min(max(limit * 5, limit), 50) if filtering else limit

    try:
        hits = ctx.search.search(query, limit=pool, rerank=rerank)
        prefix = ctx.index.get(category_id).path if category_id is not None else None
    except NotesError as exc:
        return exc.to_dict()
    criteria = replace(criteria, prefix=prefix)

    matched: list[dict[str, Any]] = []
    for hit in hits:
        if len(matched) >= limit:
            break
        if not _hit_matches(ctx, hit.path, criteria):
            continue
        matched.append({'path': hit.path, 'title': hit.title, 'score': hit.score, 'snippet': hit.snippet})

    return _ok({'query': query, 'count': len(matched), 'hits': matched})


def notes_read(ctx: ToolContext, path: str, from_line: int | None = None, max_lines: int | None = None) -> dict:
    """Return a note's frontmatter and body, optionally sliced by line."""
    try:
        resolved, frontmatter, body = _load_note(ctx, path)
    except NotesError as exc:
        return exc.to_dict()
    if from_line is not None or max_lines is not None:
        body = _slice_body(body, from_line, max_lines)
    return _ok({'path': ctx.guard.relpath(resolved), 'frontmatter': frontmatter, 'body': body})


def notes_list(ctx: ToolContext, path: str | None = None) -> dict:
    """List one level of directories and ``.md`` files under the vault or ``path``."""
    try:
        entries = _list_entries(ctx, path)
    except NotesError as exc:
        return exc.to_dict()
    return _ok({'entries': entries})


def notes_recent(ctx: ToolContext, since: str = '7d', author: str = 'any') -> dict:
    """List recently committed notes, optionally filtered to agent or human authors."""
    try:
        records = ctx.git.log_name_only(since=_since_expr(since), limit=200)
    except NotesError as exc:
        return exc.to_dict()

    notes: list[dict[str, Any]] = []
    for record in records:
        if not _author_matches(record.get('author', ''), author):
            continue
        for rel in record.get('paths', []):
            if not str(rel).endswith('.md'):
                continue
            notes.append({'path': rel, 'author': record['author'], 'date': record['date'], 'sha': record['sha']})
    return _ok({'since': since, 'author': author, 'notes': notes})


def notes_status(ctx: ToolContext) -> dict:
    """Report vault path, git branch/ahead/behind, last pull, and qmd health."""
    try:
        summary = ctx.git.status_summary()
    except NotesError as exc:
        return exc.to_dict()
    return _ok(
        {
            'vault_path': str(ctx.config.vault_path),
            **summary,
            'last_pull': summary.get('last_pull'),
            'qmd': ctx.search.health(),
            'layout': resolve_layout(ctx),
        }
    )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _load_note(ctx: ToolContext, path: str) -> tuple[Any, dict[str, Any], str]:
    """Resolve and parse a note; raise ``NOT_FOUND`` when it does not exist."""
    resolved = ctx.guard.resolve(path)
    if not resolved.is_file():
        raise notes_error(
            NOT_FOUND, f'note not found: {path}', 'Use a path from notes_list or notes_search.', path=path
        )
    frontmatter, body = parse_frontmatter(resolved.read_text(encoding='utf-8'))
    return resolved, frontmatter, body


def _slice_body(body: str, from_line: int | None, max_lines: int | None) -> str:
    """Slice ``body`` by 1-based line numbers; ``max_lines=None`` goes to the end."""
    lines = body.split('\n')
    start = max(from_line - 1, 0) if from_line is not None else 0
    end = start + max_lines if max_lines is not None else len(lines)
    return '\n'.join(lines[start:end])


def _hit_matches(ctx: ToolContext, path: str, criteria: _Criteria) -> bool:
    """True when a hit passes the path and frontmatter filters."""
    if not _path_matches(path, criteria):
        return False
    if not criteria.needs_frontmatter:
        return True
    frontmatter = _frontmatter(ctx, path)
    if frontmatter is None:
        return False
    return _frontmatter_matches(frontmatter, criteria)


def _path_matches(path: str, criteria: _Criteria) -> bool:
    """True when the hit path satisfies the prefix, area, and domain filters."""
    norm = path.replace('\\', '/')
    if criteria.prefix is not None and not _under(norm, _norm_dir(criteria.prefix)):
        return False
    if criteria.area is not None and not norm.startswith(_norm_dir(criteria.area)):
        return False
    return criteria.domain is None or criteria.domain.casefold() in norm.casefold()


def _frontmatter_matches(frontmatter: dict[str, Any], criteria: _Criteria) -> bool:
    """True when the hit frontmatter satisfies the type, status, tag, and date filters."""
    if criteria.type_ is not None and str(frontmatter.get('type')) != str(criteria.type_):
        return False
    if criteria.status is not None and str(frontmatter.get('status')) != str(criteria.status):
        return False
    if criteria.tags and not _has_any_tag(frontmatter, criteria.tags):
        return False
    return _dates_match(frontmatter, criteria)


def _has_any_tag(frontmatter: dict[str, Any], tags: list[str]) -> bool:
    """True when the note's ``tags`` list contains any of ``tags``."""
    note_tags = frontmatter.get('tags')
    note_tags = note_tags if isinstance(note_tags, list) else []
    return any(tag in note_tags for tag in tags)


def _dates_match(frontmatter: dict[str, Any], criteria: _Criteria) -> bool:
    """True when ``created`` and ``updated`` fall within their configured bounds."""
    created = _date_in_range(frontmatter.get('created'), criteria.created_after, criteria.created_before)
    updated = _date_in_range(frontmatter.get('updated'), criteria.updated_after, criteria.updated_before)
    return created and updated


def _date_in_range(value: object, after: str | None, before: str | None) -> bool:
    """True when ``value``'s date part is within inclusive ``[after, before]``.

    With no bound the value is unconstrained, so a missing date passes. When a
    bound is set a missing or unparseable date fails (the note is excluded).
    """
    low, high = _date_part(after), _date_part(before)
    if low is None and high is None:
        return True
    day = _date_part(value)
    if day is None:
        return False
    if low is not None and day < low:
        return False
    return high is None or day <= high


def _date_part(value: object) -> str | None:
    """Return a validated ``YYYY-MM-DD`` prefix, or ``None`` when absent/invalid."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()[:10]
    try:
        datetime.date.fromisoformat(candidate)
    except ValueError:
        return None
    return candidate


def _norm_dir(value: str) -> str:
    """Normalise a configured directory filter: posix separators, no outer slashes."""
    return value.replace('\\', '/').strip('/')


def _under(path: str, prefix: str) -> bool:
    """True when ``path`` is ``prefix`` itself or a descendant of it."""
    return path == prefix or path.startswith(prefix + '/')


def _frontmatter(ctx: ToolContext, path: str) -> dict[str, Any] | None:
    """Best-effort frontmatter read; ``None`` for missing/unreadable/unparseable hits."""
    try:
        resolved = ctx.guard.resolve(path)
    except NotesError:
        return None
    if not resolved.is_file():
        return None
    try:
        frontmatter, _ = parse_frontmatter(resolved.read_text(encoding='utf-8'))
    except NotesError, OSError:
        return None
    return frontmatter


def _list_entries(ctx: ToolContext, path: str | None) -> list[dict[str, Any]]:
    """Enumerate one level: all dirs plus ``.md`` files, dirs first, name-ascending."""
    root = ctx.config.vault_path if path is None else ctx.guard.resolve(path)
    if not root.is_dir():
        raise notes_error(
            NOT_FOUND,
            f'directory not found: {path}',
            'Pass an existing vault-relative directory or omit path for the vault root.',
            path=path,
        )

    entries: list[dict[str, Any]] = []
    for child in root.iterdir():
        if child.name.startswith('.'):
            continue
        is_dir = child.is_dir()
        if not is_dir and child.suffix != '.md':
            continue
        rel = ctx.guard.relpath(child)
        entries.append(
            {'name': child.name, 'path': rel, 'kind': 'dir' if is_dir else 'file', 'layer': _layer(rel, is_dir)}
        )
    entries.sort(key=lambda entry: (entry['kind'] != 'dir', entry['name']))
    return entries


def _layer(rel: str, is_dir: bool) -> str:
    """The entry's top-level folder name, or ``root`` for a file at the vault root."""
    parts = rel.split('/')
    if len(parts) > 1:
        return parts[0]
    return parts[0] if is_dir else 'root'


def _since_expr(since: str) -> str:
    """Translate ``7d``/``24h``/``30m`` into a git ``--since`` expression."""
    match = _DURATION.match(since.strip())
    if match is None:
        return since
    count, unit = match.groups()
    return f'{count} {_DURATION_UNITS[unit]} ago'


def _author_matches(author: str, mode: str) -> bool:
    """Filter by whether the commit author is an ``agent:`` identity."""
    is_agent = author.startswith('agent:')
    if mode == 'agent':
        return is_agent
    if mode == 'human':
        return not is_agent
    return True
