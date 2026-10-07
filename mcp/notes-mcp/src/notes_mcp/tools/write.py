"""Write tools: ``notes_create``, ``notes_update``, ``notes_append`` (section 10).

Every write runs through :func:`notes_mcp.withwrite.with_write`, which owns the
lock, the pre-write rebase, and the commit/push protocol. These functions do the
domain work: validate the target, render the note, and apply atomic writes
(temp file + ``os.replace``). The ``qmd`` mirror is re-synced on every write.

Each takes the shared ``ToolContext`` first and returns a plain dict. Contract
``NotesError``s become ``e.to_dict()``; anything else propagates.

The module-level ``frontmatter`` parameter of ``notes_update`` shadows the
frontmatter module, so it is imported as ``_frontmatter``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from notes_mcp import frontmatter as _frontmatter, layout
from notes_mcp.atomic import atomic_write
from notes_mcp.errors import (
    DUPLICATE_FILENAME,
    FIND_NOT_UNIQUE,
    INVALID_FRONTMATTER,
    NOT_FOUND,
    PATH_REJECTED,
    NotesError,
)
from notes_mcp.withwrite import with_write


if TYPE_CHECKING:
    from notes_mcp.context import ToolContext


_ALLOWED_TYPES = frozenset(
    {'note', 'project', 'decision', 'meeting', 'person', 'howto', 'reference', 'daily', 'weekly', 'watch', 'hub'}
)
_JOURNAL_TYPES = frozenset({'daily', 'weekly'})
# Vault Contract: type -> seed template in ``00 Meta/01 Templates``. ``note``,
# ``reference``, and ``hub`` deliberately have no template.
_TYPE_TEMPLATES = {
    'project': 'Project Hub',
    'decision': 'Decision',
    'meeting': 'Meeting',
    'person': 'Person',
    'howto': 'Howto',
    'watch': 'Watch',
    'weekly': 'Weekly',
    'daily': 'Daily',
}
# Never descend into these while hunting for a duplicate filename.
_PRUNE_DIRS = frozenset({'.git', '.obsidian', '.githooks'})


# --------------------------------------------------------------------------- #
# public tools
# --------------------------------------------------------------------------- #
def notes_create(
    ctx: ToolContext,
    category_id: str | None = None,
    title: str | None = None,
    note_type: str | None = None,
    body: str = '',
    tags: list[str] | None = None,
    related: list[str] | None = None,
    source: list[str] | None = None,
    status: str = 'active',
    **fields: object,
) -> dict[str, Any]:
    """Create a note inside a validated category and commit it.

    ``categoryId`` and ``type`` arrive camelCase from the MCP schema; the
    snake_case positional parameters mirror the contract's argument order, and
    the camelCase spellings are accepted as keyword aliases via ``fields``.
    """
    category_id = _alias(fields, 'categoryId', category_id)
    note_type = _alias(fields, 'type', note_type)
    try:
        category = ctx.index.validate(category_id)
        if note_type not in _ALLOWED_TYPES:
            raise _invalid_type(note_type)

        rel = _create_rel(ctx, category, title, note_type)
        target = ctx.guard.resolve(rel, for_write=True)
        if note_type in _JOURNAL_TYPES and target.is_file():
            return _update_journal(ctx, target, rel, body)

        text = _compose_new(ctx, category, note_type, title, body, tags, related, source, status)
        result = with_write(
            config=ctx.config,
            git=ctx.git,
            paths=[rel],
            tool='notes_create',
            summary=title or Path(rel).stem,
            fn=lambda: _create_payload(ctx, target, text, rel, note_type not in _JOURNAL_TYPES),
        )
    except NotesError as exc:
        return exc.to_dict()

    if result.get('ok') is not False:
        ctx.search.schedule_reindex()
    return result


def notes_update(
    ctx: ToolContext,
    path: str,
    body: str | None = None,
    edits: list[dict[str, str]] | None = None,
    frontmatter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update an existing note's body, apply find/replace edits, or merge frontmatter."""
    try:
        target = ctx.guard.resolve(path, for_write=True)
        rel = ctx.guard.relpath(target)
        if not target.is_file():
            raise _not_found(rel)

        fm, current = _frontmatter.parse(target.read_text(encoding='utf-8'))
        new_body = current if body is None else body
        if edits:
            new_body = _apply_edits(new_body, edits)
        if frontmatter:
            _merge_frontmatter(fm, frontmatter)

        fm['updated'] = _today()
        _frontmatter.sync_qmd_metadata(fm)
        _frontmatter.validate(fm, hub=fm.get('type') == 'hub')
        text = _frontmatter.serialize(fm, new_body)

        result = with_write(
            config=ctx.config,
            git=ctx.git,
            paths=[rel],
            tool='notes_update',
            summary=rel,
            fn=lambda: _write_payload(target, text, rel),
        )
    except NotesError as exc:
        return exc.to_dict()

    if result.get('ok') is not False:
        ctx.search.schedule_reindex()
    return result


def notes_append(
    ctx: ToolContext, path: str | None = None, daily: bool = False, heading: str | None = None, text: str = ''
) -> dict[str, Any]:
    """Append text to a note, optionally under a ``## heading``.

    ``daily=True`` targets today's daily note, seeding it from the Daily template
    when it does not exist yet.
    """
    try:
        if daily:
            rel = f'{layout.journal_dir(ctx, "daily")}/{_today()}.md'
        elif path:
            target = ctx.guard.resolve(path, for_write=True)
            rel = ctx.guard.relpath(target)
            if not target.is_file():
                raise _not_found(rel)
        else:
            raise _no_target()

        target = ctx.guard.resolve(rel, for_write=True)
        result = with_write(
            config=ctx.config,
            git=ctx.git,
            paths=[rel],
            tool='notes_append',
            summary=rel,
            fn=lambda: _append_payload(ctx, target, rel, heading, text, daily),
        )
    except NotesError as exc:
        return exc.to_dict()

    if result.get('ok') is not False:
        ctx.search.schedule_reindex()
    return result


# --------------------------------------------------------------------------- #
# create helpers
# --------------------------------------------------------------------------- #
def _create_rel(ctx: ToolContext, category: Any, title: str | None, note_type: str) -> str:
    if note_type == 'daily':
        return f'{layout.journal_dir(ctx, "daily")}/{_today()}.md'
    if note_type == 'weekly':
        return f'{layout.journal_dir(ctx, "weekly")}/{_week_stamp()}.md'
    safe = ctx.guard.sanitize_filename(title or '')
    if note_type == 'meeting':
        return f'{layout.journal_dir(ctx, "meetings")}/{_today()} {safe}.md'
    return f'{category.path}/{safe}.md'


def _compose_new(
    ctx: ToolContext,
    category: Any,
    note_type: str,
    title: str | None,
    body: str,
    tags: list[str] | None,
    related: list[str] | None,
    source: list[str] | None,
    status: str,
) -> str:
    today = _today()
    fm = _frontmatter.new_frontmatter(status=status, today=today, type=note_type)
    fm['tags'] = list(tags or [])
    fm['related'] = list(related or [])
    fm['source'] = list(source or [])
    if note_type == 'hub':
        fm['scope'] = category.scope
    _frontmatter.sync_qmd_metadata(fm)
    _frontmatter.validate(fm, hub=note_type == 'hub')

    content_body = _seeded_body(ctx, note_type, title, body, today)
    return _frontmatter.serialize(fm, content_body)


def _seeded_body(ctx: ToolContext, note_type: str, title: str | None, body: str, today: str) -> str:
    """The create body: a rendered type template (if any) plus the caller's body.

    Daily keeps its strict behavior (a missing Daily template raises). Every
    other templated type falls back to the caller's body when its template file
    is absent, so a partial vault still creates notes.
    """
    if note_type == 'daily':
        return _daily_body(ctx, title, body, today)
    template_name = _TYPE_TEMPLATES.get(note_type)
    if template_name is None:
        return body
    try:
        template = _read_template(ctx, template_name)
    except NotesError:
        return body
    _, template_body = _frontmatter.parse(template)
    rendered = _render_template(template_body, today=today, title=title or today)
    if body:
        rendered = f'{rendered.rstrip()}\n\n{body}'
    return rendered


def _daily_body(ctx: ToolContext, title: str | None, body: str, today: str) -> str:
    template = _read_template(ctx)
    _, template_body = _frontmatter.parse(template)
    rendered = _render_template(template_body, today=today, title=title or today)
    if body:
        rendered = f'{rendered.rstrip()}\n\n{body}'
    return rendered


def _basename_exists(ctx: ToolContext, filename: str) -> bool:
    """Vault-wide basename collision check, pruning git/VCS internals.

    ``os.walk`` with an in-place ``dirnames`` filter is far cheaper than
    ``rglob`` and never descends into ``.git`` (whose object store holds
    arbitrarily many files).
    """
    prune = _PRUNE_DIRS | {Path(layout.meta_dir(ctx)).name}
    for _dirpath, dirnames, filenames in os.walk(ctx.config.vault_path):
        dirnames[:] = [name for name in dirnames if name not in prune]
        if filename in filenames:
            return True
    return False


def _create_payload(ctx: ToolContext, target: Path, text: str, rel: str, check_duplicate: bool) -> dict[str, Any]:
    """Write a brand-new note, refusing a filename collision before writing.

    The check runs under the vault lock (the callback only runs after
    ``with_write`` acquired it), so two concurrent creates cannot both win.
    """
    if check_duplicate and _basename_exists(ctx, Path(rel).name):
        raise _duplicate(rel)
    return _write_payload(target, text, rel)


def _update_journal(ctx: ToolContext, target: Path, rel: str, body: str) -> dict[str, Any]:
    """Append to an existing daily/weekly note instead of raising DUPLICATE."""
    try:
        result = with_write(
            config=ctx.config,
            git=ctx.git,
            paths=[rel],
            tool='notes_update',
            summary=rel,
            fn=lambda: _journal_payload(target, rel, body),
        )
    except NotesError as exc:
        return exc.to_dict()
    if result.get('ok') is not False:
        ctx.search.schedule_reindex()
    return result


def _journal_payload(target: Path, rel: str, body: str) -> dict[str, Any]:
    fm, current = _frontmatter.parse(target.read_text(encoding='utf-8'))
    fm['updated'] = _today()
    _frontmatter.sync_qmd_metadata(fm)
    _frontmatter.validate(fm, hub=fm.get('type') == 'hub')
    atomic_write(target, _frontmatter.serialize(fm, _append_body(current, None, body)))
    return {'path': rel, 'needs_index_update': False}


# --------------------------------------------------------------------------- #
# update helpers
# --------------------------------------------------------------------------- #
def _apply_edits(body: str, edits: list[dict[str, str]]) -> str:
    for edit in edits:
        find = str(edit.get('find', ''))
        replace = str(edit.get('replace', ''))
        count = body.count(find)
        if count != 1:
            raise NotesError(
                FIND_NOT_UNIQUE,
                f'find string matches {count} times (expected exactly one)',
                'Use a longer find string that occurs exactly once.',
                find=find,
                count=count,
            )
        body = body.replace(find, replace)
    return body


def _merge_frontmatter(fm: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    for key, value in incoming.items():
        if key == 'qmd':
            continue
        fm[key] = value
    return fm


# --------------------------------------------------------------------------- #
# append helpers
# --------------------------------------------------------------------------- #
def _append_payload(
    ctx: ToolContext, target: Path, rel: str, heading: str | None, text: str, daily: bool
) -> dict[str, Any]:
    if daily and not target.is_file():
        raw = _render_template(_read_template(ctx), today=_today(), title=_today())
    else:
        raw = target.read_text(encoding='utf-8')

    fm, body = _frontmatter.parse(raw)
    body = _append_body(body, heading, text)
    fm['updated'] = _today()
    _frontmatter.sync_qmd_metadata(fm)
    atomic_write(target, _frontmatter.serialize(fm, body))
    return {'path': rel, 'needs_index_update': False}


def _append_body(body: str, heading: str | None, text: str) -> str:
    if not heading:
        return body if not text else f'{body.rstrip()}\n\n{text}\n'

    marker = f'## {heading}'
    lines = body.split('\n')
    start = next((index for index, line in enumerate(lines) if line.strip() == marker), None)
    if start is None:
        base = body.rstrip()
        prefix = f'{base}\n\n' if base else ''
        return f'{prefix}{marker}\n\n{text}\n'

    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith('## '):
            end = index
            break

    head = '\n'.join(lines[:end]).rstrip()
    tail = '\n'.join(lines[end:])
    if tail:
        return f'{head}\n\n{text}\n\n{tail}'
    return f'{head}\n\n{text}\n'


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
def _write_payload(target: Path, text: str, rel: str) -> dict[str, Any]:
    atomic_write(target, text)
    return {'path': rel, 'needs_index_update': False}


def _read_template(ctx: ToolContext, name: str = 'Daily') -> str:
    rel = layout.template_rel(ctx, name)
    template = ctx.config.vault_path / rel
    try:
        return template.read_text(encoding='utf-8')
    except OSError as exc:
        raise NotesError(
            NOT_FOUND, f'{name} template is missing: {rel}', f'Restore {rel} in the vault.', path=rel
        ) from exc


def _render_template(text: str, *, today: str, title: str) -> str:
    return text.replace('{{date:YYYY-MM-DD}}', today).replace('{{title}}', title)


def _alias(fields: dict[str, Any], key: str, current: str | None) -> str | None:
    if key in fields:
        value = fields.pop(key)
        return value if isinstance(value, str) else current
    return current


def _today() -> str:
    return datetime.now(tz=UTC).astimezone().date().isoformat()


def _week_stamp() -> str:
    iso = datetime.now(tz=UTC).astimezone().isocalendar()
    return f'{iso.year}-W{iso.week:02d}'


def _not_found(rel: str) -> NotesError:
    return NotesError(NOT_FOUND, f'note not found: {rel}', 'Check the path with notes_read or notes_list.', path=rel)


def _invalid_type(note_type: str | None) -> NotesError:
    return NotesError(
        INVALID_FRONTMATTER,
        f'unknown note type {note_type!r}',
        f'Use one of: {", ".join(sorted(_ALLOWED_TYPES))}.',
        type=note_type,
    )


def _duplicate(rel: str) -> NotesError:
    return NotesError(
        DUPLICATE_FILENAME,
        f'a note named {Path(rel).name!r} already exists',
        'Choose a different title or update the existing note.',
        path=rel,
    )


def _no_target() -> NotesError:
    return NotesError(PATH_REJECTED, 'notes_append needs a target', 'Pass path="..." or daily=True.')
