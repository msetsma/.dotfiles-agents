"""Vault layout resolution: journal, template, and attachment directories.

Paths resolve in precedence order: an explicit config env override wins, then
the matching Johnny-Decimal Index category, then a built-in fallback. This keeps
a personal or renumbered vault working without code changes, while the shipped
Index layout stays the default.

Every function takes the shared :class:`~notes_mcp.context.ToolContext` first
and returns a vault-relative POSIX string.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import NotesError


if TYPE_CHECKING:
    from .context import ToolContext


META_ID = '00'
DAILY_ID = '41'
WEEKLY_ID = '42'
MEETINGS_ID = '43'

_FALLBACK = {
    'daily': '40 Journal/41 Daily',
    'weekly': '40 Journal/42 Weekly',
    'meetings': '40 Journal/43 Meetings',
    'meta': '00 Meta',
}
_TEMPLATES_SUBDIR = '01 Templates'
_ATTACHMENTS_SUBDIR = '02 Attachments'
_JOURNAL = {
    'daily': (DAILY_ID, 'daily_dir'),
    'weekly': (WEEKLY_ID, 'weekly_dir'),
    'meetings': (MEETINGS_ID, 'meetings_dir'),
}


def journal_dir(ctx: ToolContext, kind: str) -> str:
    """Resolve a journal directory for ``kind`` in ``{daily, weekly, meetings}``."""
    try:
        category_id, field = _JOURNAL[kind]
    except KeyError:
        raise ValueError(f'unknown journal kind: {kind!r}') from None
    override = getattr(ctx.config, field)
    if override:
        return override
    return _from_index(ctx, category_id, _FALLBACK[kind])


def meta_dir(ctx: ToolContext) -> str:
    """Resolve the Meta category root (id 00), falling back on ``00 Meta``."""
    return _from_index(ctx, META_ID, _FALLBACK['meta'])


def templates_dir(ctx: ToolContext) -> str:
    """The templates directory: config override, else ``<meta>/01 Templates``."""
    return ctx.config.templates_dir or f'{meta_dir(ctx)}/{_TEMPLATES_SUBDIR}'


def attachments_dir(ctx: ToolContext) -> str:
    """The attachments directory: config override, else ``<meta>/02 Attachments``."""
    return ctx.config.attachments_dir or f'{meta_dir(ctx)}/{_ATTACHMENTS_SUBDIR}'


def template_rel(ctx: ToolContext, name: str) -> str:
    """Vault-relative path to a named template (``<templates>/<name>.md``)."""
    return f'{templates_dir(ctx)}/{name}.md'


def resolve(ctx: ToolContext) -> dict[str, str]:
    """Every resolved layout path, keyed by role."""
    return {
        'daily': journal_dir(ctx, 'daily'),
        'weekly': journal_dir(ctx, 'weekly'),
        'meetings': journal_dir(ctx, 'meetings'),
        'templates': templates_dir(ctx),
        'attachments': attachments_dir(ctx),
    }


def _from_index(ctx: ToolContext, category_id: str, fallback: str) -> str:
    """The Index category path, or ``fallback`` when it is absent/unavailable."""
    try:
        return ctx.index.get(category_id).path
    except NotesError:
        return fallback
