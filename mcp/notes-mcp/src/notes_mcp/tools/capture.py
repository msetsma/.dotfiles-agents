"""Capture tools: ``notes_capture`` and ``notes_triage``.

``notes_capture`` creates one inbox note per captured item: it renders the Inbox
template (``type: note``, ``status: draft``), inserts the text under the
template's ``## Capture`` heading, keeps ``## Triage``, and commits through the
shared write protocol. When the vault has no ``05`` (Inbox) category it falls
back to appending to today's daily under ``## Inbox``.

``notes_triage`` is read-only: for every inbox note it asks the search index for
related notes, maps them back to categories, and returns a ranked suggestion.
No commit is made, so the caller can act on it later.

Each function takes the shared ``ToolContext`` first and returns a plain dict.
Contract ``NotesError``s become ``e.to_dict()``; anything else propagates.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from notes_mcp import frontmatter, layout
from notes_mcp.atomic import atomic_write
from notes_mcp.errors import NOT_FOUND, NotesError, notes_error
from notes_mcp.withwrite import with_write


if TYPE_CHECKING:
    from notes_mcp.context import ToolContext


INBOX_ID = '05'
_MAX_TRIAGE = 50
_MAX_SNIPPET = 600
_TITLE_LIMIT = 60
# Never descend into these while hunting for a duplicate basename. The Inbox
# template lives under ``00 Meta`` but the target never does, so pruning it is
# safe and keeps the walk cheap.
_PRUNE_DIRS = frozenset({'.git', '.obsidian', '.githooks', '00 Meta'})
_SKIP_CATEGORIES = frozenset({'00', INBOX_ID})


# --------------------------------------------------------------------------- #
# notes_capture
# --------------------------------------------------------------------------- #
def notes_capture(
    ctx: ToolContext,
    text: str,
    title: str | None = None,
    tags: list[str] | None = None,
    source: list[str] | None = None,
    **options: object,
) -> dict[str, Any]:
    """Create one inbox note from ``text`` (or append to the daily as a fallback)."""
    _reject_options(options)
    try:
        category = ctx.index.get(INBOX_ID)
    except NotesError:
        from notes_mcp.tools.write import notes_append

        return notes_append(ctx, daily=True, heading='Inbox', text=text)

    try:
        return _capture(ctx, category.path, text, title, tags, source)
    except NotesError as exc:
        return exc.to_dict()


def _capture(
    ctx: ToolContext, category_path: str, text: str, title: str | None, tags: list[str] | None, source: list[str] | None
) -> dict[str, Any]:
    today = _today()
    safe_title = _derive_title(ctx, text, title)
    rel = _unique_rel(ctx, category_path, safe_title)
    target = ctx.guard.resolve(rel, for_write=True)
    rendered = _render_note(_read_template(ctx), text, safe_title, today, tags, source)

    result = with_write(
        config=ctx.config,
        git=ctx.git,
        paths=[rel],
        tool='notes_capture',
        summary=safe_title,
        fn=lambda: _capture_payload(target, rendered, rel),
    )
    if result.get('ok') is not False:
        ctx.search.schedule_reindex()
    return result


def _capture_payload(target: Path, text: str, rel: str) -> dict[str, Any]:
    atomic_write(target, text)
    return {'path': rel, 'category_id': INBOX_ID, 'needs_index_update': False}


def _render_note(
    template: str, text: str, title: str, today: str, tags: list[str] | None, source: list[str] | None
) -> str:
    rendered = template.replace('{{date:YYYY-MM-DD}}', today).replace('{{title}}', title)
    fm, body = frontmatter.parse(rendered)
    body = _insert_under_capture(body, text)
    fm['type'] = fm.get('type') or 'note'
    fm['status'] = fm.get('status') or 'draft'
    fm.setdefault('tags', [])
    fm.setdefault('related', [])
    fm.setdefault('source', [])
    fm['created'] = today
    fm['updated'] = today
    if tags:
        fm['tags'] = list(tags)
    if source:
        fm['source'] = list(source)
    frontmatter.sync_qmd_metadata(fm)
    frontmatter.validate(fm, hub=fm.get('type') == 'hub')
    return frontmatter.serialize(fm, body)


def _insert_under_capture(body: str, text: str) -> str:
    """Put ``text`` under ``## Capture``, keeping every following section."""
    lines = body.split('\n')
    start = next((index for index, line in enumerate(lines) if line.strip() == '## Capture'), None)
    if start is None:
        base = body.rstrip()
        prefix = f'{base}\n\n' if base else ''
        return f'{prefix}## Capture\n\n{text}\n'

    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith('## '):
            end = index
            break
    head = lines[: start + 1]
    tail = lines[end:]
    return '\n'.join([*head, '', text, '', *tail])


def _derive_title(ctx: ToolContext, text: str, title: str | None) -> str:
    raw = title or _first_line(text)[:_TITLE_LIMIT].strip()
    if not raw:
        raw = f'capture {_now().strftime("%Y-%m-%d %H%M%S")}'
    return ctx.guard.sanitize_filename(raw)


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip('#-*').strip()
        if stripped:
            return stripped
    return ''


def _unique_rel(ctx: ToolContext, category_path: str, slug: str) -> str:
    if not _basename_exists(ctx, f'{slug}.md'):
        return f'{category_path}/{slug}.md'

    stamp = _now().strftime('%H%M%S')
    candidate = f'{slug} {stamp}'
    counter = 2
    while _basename_exists(ctx, f'{candidate}.md'):
        candidate = f'{slug} {stamp}-{counter}'
        counter += 1
    return f'{category_path}/{candidate}.md'


def _basename_exists(ctx: ToolContext, filename: str) -> bool:
    """Vault-wide basename collision check, pruning git internals and Meta."""
    for _dirpath, dirnames, filenames in os.walk(ctx.config.vault_path):
        dirnames[:] = [name for name in dirnames if name not in _PRUNE_DIRS]
        if filename in filenames:
            return True
    return False


def _read_template(ctx: ToolContext) -> str:
    rel = layout.template_rel(ctx, 'Inbox')
    template = ctx.config.vault_path / rel
    try:
        return template.read_text(encoding='utf-8')
    except OSError as exc:
        raise notes_error(
            NOT_FOUND, f'inbox template is missing: {rel}', f'Restore {rel} in the vault.', path=rel
        ) from exc


# --------------------------------------------------------------------------- #
# notes_triage
# --------------------------------------------------------------------------- #
def notes_triage(
    ctx: ToolContext, path: str | None = None, limit: int | None = None, **options: object
) -> dict[str, Any]:
    """Suggest a category for each inbox note (read-only; no commit)."""
    _reject_options(options)
    try:
        targets = _triage_targets(ctx, path, limit)
        suggestions = [_suggest(ctx, rel) for rel in targets]
    except NotesError as exc:
        return exc.to_dict()
    return {
        'ok': True,
        'count': len(suggestions),
        'suggestions': suggestions,
        'commit': None,
        'pending_push': False,
        'needs_index_update': False,
    }


def _triage_targets(ctx: ToolContext, path: str | None, limit: int | None) -> list[str]:
    cap = _MAX_TRIAGE if not limit or limit > _MAX_TRIAGE else max(limit, 1)
    if path is not None:
        resolved = ctx.guard.resolve(path)
        if not resolved.is_file():
            raise notes_error(
                NOT_FOUND, f'note not found: {path}', 'Use a path from notes_list or notes_search.', path=path
            )
        return [ctx.guard.relpath(resolved)]

    directory = ctx.index.get(INBOX_ID)
    root = ctx.config.vault_path / directory.path
    if not root.is_dir():
        return []

    hub = f'{directory.path}/{Path(directory.path).name}.md'
    targets: list[str] = []
    for file in sorted(root.rglob('*.md')):
        rel = ctx.guard.relpath(file)
        if rel == hub:
            continue
        targets.append(rel)
        if len(targets) >= cap:
            break
    return targets


def _suggest(ctx: ToolContext, rel: str) -> dict[str, Any]:
    title, body = _note_text(ctx, rel)
    query = f'{title}\n{body[:_MAX_SNIPPET]}'.strip()
    hits = ctx.search.search(query, limit=8, rerank=True)
    candidates = sorted(_aggregate(ctx, hits).values(), key=lambda item: item['score'], reverse=True)
    top = candidates[0] if candidates else None
    return {
        'path': rel,
        'suggested_category_id': top['category_id'] if top else None,
        'suggested_category': top['category'] if top else None,
        'score': top['score'] if top else 0.0,
        'candidates': candidates,
    }


def _note_text(ctx: ToolContext, rel: str) -> tuple[str, str]:
    raw = ctx.guard.resolve(rel).read_text(encoding='utf-8')
    try:
        _frontmatter, body = frontmatter.parse(raw)
    except NotesError:
        body = raw
    return Path(rel).stem, body


def _aggregate(ctx: ToolContext, hits: list[Any]) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    for hit in hits:
        category = ctx.index.by_path_prefix(_hit_path(hit))
        if category is None or category.id in _SKIP_CATEGORIES:
            continue
        entry = totals.setdefault(category.id, {'category_id': category.id, 'category': category.name, 'score': 0.0})
        entry['score'] += _hit_score(hit)
    return totals


def _hit_path(hit: Any) -> str:
    value = hit.get('path') if isinstance(hit, dict) else getattr(hit, 'path', '')
    return str(value or '')


def _hit_score(hit: Any) -> float:
    value = hit.get('score') if isinstance(hit, dict) else getattr(hit, 'score', None)
    return float(value) if value is not None else 0.0


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
def _reject_options(options: dict[str, object]) -> None:
    if options:
        unknown = ', '.join(sorted(options))
        raise TypeError(f'unexpected keyword argument(s): {unknown}')


def _now() -> datetime:
    return datetime.now(tz=UTC).astimezone()


def _today() -> str:
    return _now().date().isoformat()
