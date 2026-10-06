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

import re
from typing import TYPE_CHECKING, Any

from notes_mcp.errors import NOT_FOUND, NotesError, notes_error
from notes_mcp.frontmatter import parse as parse_frontmatter
from notes_mcp.index import INDEX_REL_PATH


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


def notes_search(
    ctx: ToolContext,
    query: str,
    area: str | None = None,
    category_id: str | None = None,
    type_: str | None = None,
    status: str | None = None,
    limit: int = 10,
    rerank: bool = True,
    **options: object,
) -> dict:
    """Search the vault and post-filter hits by path/frontmatter.

    ``qmd --filter`` proved unreliable, so filtering happens here: ``category_id``
    and ``area`` match on the hit path, ``type_``/``status`` parse the hit's
    frontmatter. ``type`` is accepted as an alias for ``type_``.
    """
    if 'type' in options:
        type_ = options.pop('type')  # type: ignore[assignment]
    if options:
        unknown = ', '.join(sorted(options))
        raise TypeError(f'unexpected keyword argument(s): {unknown}')

    try:
        hits = ctx.search.search(query, limit=limit, rerank=rerank)
        prefix = ctx.index.get(category_id).path if category_id is not None else None
    except NotesError as exc:
        return exc.to_dict()

    matched: list[dict[str, Any]] = []
    for hit in hits:
        if prefix is not None and not hit.path.startswith(prefix):
            continue
        if area is not None and not hit.path.startswith(area):
            continue
        if (type_ is not None or status is not None) and not _matches_frontmatter(ctx, hit.path, type_, status):
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


def _matches_frontmatter(ctx: ToolContext, path: str, type_: str | None, status: str | None) -> bool:
    """True when the hit file's frontmatter matches the requested type/status."""
    frontmatter = _frontmatter(ctx, path)
    if frontmatter is None:
        return False
    if type_ is not None and str(frontmatter.get('type')) != str(type_):
        return False
    return status is None or str(frontmatter.get('status')) == str(status)


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
