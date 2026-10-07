"""Report generators: ``notes_weekly_digest`` and ``notes_stale_report`` (Phase 3).

``notes_weekly_digest`` drafts or refreshes the weekly review note for an ISO
week: it scans the note tree, groups the notes whose ``updated`` (or
``created``) date falls inside Monday..Sunday by category, renders a ``## Digest``
section, seeds a missing week from the ``Weekly`` template, and (when
``write=True``) commits through the shared write protocol.

``notes_stale_report`` lists notes not touched within ``days`` (a missing
``updated`` counts as stale). It is read-only by default; ``write=True`` appends
the report to today's daily note under ``## Reports``.

Each function takes the shared ``ToolContext`` first and returns a plain dict.
Contract ``NotesError``s become ``e.to_dict()``; anything else propagates.
"""

from __future__ import annotations

import datetime
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from notes_mcp import frontmatter as _frontmatter, layout
from notes_mcp.atomic import atomic_write
from notes_mcp.errors import PATH_REJECTED, NotesError, notes_error
from notes_mcp.withwrite import with_write


if TYPE_CHECKING:
    from notes_mcp.context import ToolContext


_WEEK_RE = re.compile(r'^(\d{4})-W(\d{2})$')
_SUNDAY = 7
_MAX_BULLETS = 200
_MAX_STALE = 200
_UNCATEGORISED = 'Uncategorised'
_STALE_SORT_UNKNOWN = 10**9
# Never descend into these while scanning for notes.
_PRUNE_DIRS = frozenset({'.git', '.obsidian', '.githooks'})


# --------------------------------------------------------------------------- #
# notes_weekly_digest
# --------------------------------------------------------------------------- #
def notes_weekly_digest(
    ctx: ToolContext, week: str | None = None, write: bool = True, **options: object
) -> dict[str, Any]:
    """Draft or refresh the weekly review note for ``week`` (default: this week)."""
    _reject_options(options)
    try:
        week = _resolve_week(week)
        monday, sunday = _week_range(week)
        rel = f'{layout.journal_dir(ctx, "weekly")}/{week}.md'
        notes = _notes_in_range(ctx, monday, sunday, rel)
        counts = _counts(notes)
        draft = _render_digest(ctx, week, monday, sunday, notes, rel)
    except NotesError as exc:
        return exc.to_dict()

    payload: dict[str, Any] = {
        'ok': True,
        'week': week,
        'range': [monday.isoformat(), sunday.isoformat()],
        'path': rel,
        'counts': counts,
    }
    if not write:
        return {
            **payload,
            'written': False,
            'draft': draft,
            'commit': None,
            'pending_push': False,
            'needs_index_update': False,
        }

    try:
        target = ctx.guard.resolve(rel, for_write=True)
        result = with_write(
            config=ctx.config,
            git=ctx.git,
            paths=[rel],
            tool='notes_weekly_digest',
            summary=week,
            fn=lambda: _digest_payload(target, draft, rel),
        )
    except NotesError as exc:
        return exc.to_dict()
    if result.get('ok') is False:
        return result

    ctx.search.schedule_reindex()
    return {**payload, 'written': True, **result}


# --------------------------------------------------------------------------- #
# notes_stale_report
# --------------------------------------------------------------------------- #
def notes_stale_report(ctx: ToolContext, days: int = 90, write: bool = False, **options: object) -> dict[str, Any]:
    """List notes whose ``updated`` date is missing or older than ``today - days``."""
    _reject_options(options)
    try:
        as_of = _today()
        stale = _stale_notes(ctx, as_of, days)
    except NotesError as exc:
        return exc.to_dict()

    payload: dict[str, Any] = {
        'ok': True,
        'days': days,
        'as_of': as_of.isoformat(),
        'count': len(stale),
        'stale': stale,
        'written': False,
        'path': None,
        'commit': None,
        'pending_push': False,
        'needs_index_update': False,
    }
    if not write:
        return payload

    result = _append_report(ctx, _report_text(as_of, days, stale))
    if result.get('ok') is False:
        return result

    payload['written'] = True
    payload['path'] = result.get('path')
    for key in ('commit', 'pending_push', 'needs_index_update'):
        if key in result:
            payload[key] = result[key]
    return payload


def _append_report(ctx: ToolContext, text: str) -> dict[str, Any]:
    """Append ``text`` to today's daily note under ``## Reports``."""
    from notes_mcp.tools.write import notes_append

    return notes_append(ctx, daily=True, heading='Reports', text=text)


# --------------------------------------------------------------------------- #
# weekly helpers
# --------------------------------------------------------------------------- #
def _resolve_week(week: str | None) -> str:
    if week is None:
        iso = datetime.datetime.now(tz=datetime.UTC).astimezone().isocalendar()
        return f'{iso.year}-W{iso.week:02d}'
    match = _WEEK_RE.match(week)
    if not match or not _valid_week(int(match.group(1)), int(match.group(2))):
        raise _bad_week(week)
    return week


def _valid_week(year: int, number: int) -> bool:
    try:
        datetime.date.fromisocalendar(year, number, 1)
    except ValueError:
        return False
    return True


def _week_range(week: str) -> tuple[datetime.date, datetime.date]:
    year, number = week.split('-W')
    return (
        datetime.date.fromisocalendar(int(year), int(number), 1),
        datetime.date.fromisocalendar(int(year), int(number), _SUNDAY),
    )


def _notes_in_range(ctx: ToolContext, monday: datetime.date, sunday: datetime.date, skip: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for rel, text in _markdown_files(ctx):
        if rel == skip:
            continue
        fm = _parse_or_none(text)
        date = _as_date(fm.get('updated') or fm.get('created')) if fm else None
        if date is None or not monday <= date <= sunday:
            continue
        category = ctx.index.by_path_prefix(rel)
        found.append(
            {
                'path': rel,
                'stem': Path(rel).stem,
                'date': date,
                'category': category.name if category else _UNCATEGORISED,
            }
        )
    return found


def _counts(notes: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for note in notes:
        counts[note['category']] = counts.get(note['category'], 0) + 1
    counts['total'] = len(notes)
    return counts


def _groups(notes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for note in notes:
        grouped.setdefault(note['category'], []).append(note)
    return {
        category: sorted(items, key=lambda item: (item['date'], item['stem']), reverse=True)
        for category, items in sorted(grouped.items())
    }


def _digest_text(week: str, monday: datetime.date, sunday: datetime.date, notes: list[dict[str, Any]]) -> str:
    lines = [f'**{week}** ({monday.isoformat()} to {sunday.isoformat()}) — {len(notes)} notes updated', '']
    remaining = _MAX_BULLETS
    for category, items in _groups(notes).items():
        lines.append(f'### {category} ({len(items)})')
        lines.append('')
        lines.extend(f'- [[{item["stem"]}]] — updated {item["date"].isoformat()}' for item in items[:remaining])
        remaining -= min(len(items), remaining)
        lines.append('')
        if remaining <= 0:
            break
    return '\n'.join(lines).rstrip() + '\n'


def _render_digest(
    ctx: ToolContext, week: str, monday: datetime.date, sunday: datetime.date, notes: list[dict[str, Any]], rel: str
) -> str:
    digest = _digest_text(week, monday, sunday, notes)
    today = _today().isoformat()
    target = ctx.config.vault_path / rel
    if target.is_file():
        fm, body = _frontmatter.parse(target.read_text(encoding='utf-8'))
    else:
        fm, body = _weekly_seed(ctx, week, today)
    body = _upsert_section(body, 'Digest', digest)
    fm['updated'] = today
    _frontmatter.sync_qmd_metadata(fm)
    _frontmatter.validate(fm, hub=fm.get('type') == 'hub')
    return _frontmatter.serialize(fm, body)


def _weekly_seed(ctx: ToolContext, week: str, today: str) -> tuple[dict[str, Any], str]:
    """Render the Weekly template, or a minimal body when it is missing/unreadable."""
    try:
        rel = layout.template_rel(ctx, 'Weekly')
        template = (ctx.config.vault_path / rel).read_text(encoding='utf-8')
        rendered = template.replace('{{date:YYYY-MM-DD}}', today).replace('{{title}}', week)
        return _frontmatter.parse(rendered)
    except OSError, NotesError:
        fm = _frontmatter.new_frontmatter(status='active', today=today, type='weekly')
        return fm, f'# {week}\n\n## Digest\n'


def _digest_payload(target: Path, text: str, rel: str) -> dict[str, Any]:
    atomic_write(target, text)
    return {'path': rel, 'needs_index_update': False}


def _upsert_section(body: str, heading: str, text: str) -> str:
    """Replace a ``## heading`` section's body in place, or append the section."""
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

    head = '\n'.join(lines[: start + 1]).rstrip()
    tail = '\n'.join(lines[end:]).rstrip()
    if tail:
        return f'{head}\n\n{text}\n\n{tail}\n'
    return f'{head}\n\n{text}\n'


# --------------------------------------------------------------------------- #
# stale helpers
# --------------------------------------------------------------------------- #
def _stale_notes(ctx: ToolContext, as_of: datetime.date, days: int) -> list[dict[str, Any]]:
    stale: list[dict[str, Any]] = []
    for rel, text in _markdown_files(ctx):
        fm = _parse_or_none(text)
        if fm is None:
            continue
        date = _as_date(fm.get('updated'))
        if date is None:
            stale.append({'path': rel, 'updated': None, 'days': None})
        elif (as_of - date).days > days:
            stale.append({'path': rel, 'updated': date.isoformat(), 'days': (as_of - date).days})
    stale.sort(key=_stale_order, reverse=True)
    return stale[:_MAX_STALE]


def _stale_order(item: dict[str, Any]) -> int:
    """Sort key: a missing ``updated`` (unknown age) ranks as the oldest."""
    days = item['days']
    return _STALE_SORT_UNKNOWN if days is None else days


def _report_text(as_of: datetime.date, days: int, stale: list[dict[str, Any]]) -> str:
    if not stale:
        return f'No notes older than {days} days as of {as_of.isoformat()}.\n'
    lines = [f'Stale notes as of {as_of.isoformat()} (older than {days} days): {len(stale)}', '']
    for item in stale:
        stem = Path(item['path']).stem
        if item['days'] is None:
            lines.append(f'- [[{stem}]] — no updated date')
        else:
            lines.append(f'- [[{stem}]] — updated {item["updated"]} ({item["days"]} days)')
    return '\n'.join(lines) + '\n'


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
def _markdown_files(ctx: ToolContext) -> list[tuple[str, str]]:
    """Every ``.md`` file outside the pruned internals and the Meta category."""
    vault = ctx.config.vault_path
    prune = _prune_dirs(ctx)
    files: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [name for name in dirnames if name not in prune]
        for name in sorted(filenames):
            if not name.endswith('.md'):
                continue
            path = Path(dirpath) / name
            try:
                text = path.read_text(encoding='utf-8')
            except OSError:
                continue
            files.append((path.relative_to(vault).as_posix(), text))
    return files


def _prune_dirs(ctx: ToolContext) -> set[str]:
    return set(_PRUNE_DIRS) | set(Path(layout.meta_dir(ctx)).parts)


def _parse_or_none(text: str) -> dict[str, Any] | None:
    try:
        fm, _body = _frontmatter.parse(text)
    except NotesError:
        return None
    return fm


def _as_date(value: object) -> datetime.date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _reject_options(options: dict[str, object]) -> None:
    if options:
        unknown = ', '.join(sorted(options))
        raise TypeError(f'unexpected keyword argument(s): {unknown}')


def _today() -> datetime.date:
    return datetime.datetime.now(tz=datetime.UTC).astimezone().date()


def _bad_week(week: str) -> NotesError:
    return notes_error(
        PATH_REJECTED, f'invalid ISO week: {week!r}', "Use the form 'YYYY-Www', for example '2026-W02'.", week=week
    )
