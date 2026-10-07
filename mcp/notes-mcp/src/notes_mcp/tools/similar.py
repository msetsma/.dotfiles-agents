"""Read-only similarity tools (Phase 3): ``notes_similar`` and ``notes_suggest_links``.

Both answer "what else is like this?" without touching git: they build a query
from an existing note (or raw text), ask the qmd-backed searcher for neighbours,
and return ranked candidates. Nothing is committed, so the caller can decide
what to act on later.

Each function takes the shared ``ToolContext`` first and returns a plain dict.
Contract ``NotesError``s become ``e.to_dict()``; anything else propagates.

Registering these in ``server.py`` is another agent's job; this module only
supplies the implementations.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from notes_mcp.errors import NOT_FOUND, PATH_REJECTED, NotesError, notes_error
from notes_mcp.frontmatter import parse as parse_frontmatter


if TYPE_CHECKING:
    from notes_mcp.context import ToolContext


# Body excerpt fed to the search index; enough signal without dragging the
# whole note (and its YAML) through the query.
_EXCERPT = 800
# Hard ceiling on the suggestion candidate pool, so a huge ``limit`` stays
# bounded.
_SUGGEST_CAP = 30

# Lightweight markdown/wikilink stripping. The wikilink group 2 is the ``|alias``
# display text; group 1 is the target.
_WIKILINK = re.compile(r'\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]')
_MD_LINK = re.compile(r'\[([^\]]+)\]\([^)]*\)')
_MD_MARKUP = re.compile(r'[#*_`>~\[\]]+')
# Already-present link targets, as frozen in the Phase 3 contract.
_LINK_TARGET = re.compile(r'\[\[([^\[\]|#]+)')


# --------------------------------------------------------------------------- #
# envelope
# --------------------------------------------------------------------------- #
def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    """Merge ``payload`` with the success envelope tail (mirrors ``read.py``)."""
    return {'ok': True, **payload, 'commit': None, 'pending_push': False, 'needs_index_update': False}


def _hit_dict(hit: Any) -> dict[str, Any]:
    """Project a search hit onto the ``{path, title, score}`` shape."""
    return {'path': hit.path, 'title': hit.title, 'score': hit.score}


# --------------------------------------------------------------------------- #
# notes_similar
# --------------------------------------------------------------------------- #
def notes_similar(
    ctx: ToolContext, path: str | None = None, text: str | None = None, limit: int = 5, **options: object
) -> dict[str, Any]:
    """Return the notes most similar to a note (``path``) or raw ``text``.

    Exactly one of ``path``/``text`` is required. The query is the note's title
    plus a stripped excerpt of its body, or the raw text truncated to the same
    length. The source note and ``00 Meta`` system files are excluded.
    """
    _reject_options(options)
    try:
        query, source = _similar_query(ctx, path, text)
        hits = ctx.search.search(query, limit=_pool(limit, factor=2), rerank=True)
    except NotesError as exc:
        return exc.to_dict()
    results = _collect(hits, exclude=source, limit=max(limit, 1))
    return _ok({'query': query, 'count': len(results), 'results': results})


# --------------------------------------------------------------------------- #
# notes_suggest_links
# --------------------------------------------------------------------------- #
def notes_suggest_links(ctx: ToolContext, path: str, limit: int = 10, **options: object) -> dict[str, Any]:
    """Suggest ``[[wikilinks]]`` to add to an existing note.

    Candidates come from the search index; the note itself, anything it
    already links to (by stem, vault-relative path, or path without ``.md``),
    and ``00 Meta`` system files are excluded.
    """
    _reject_options(options)
    try:
        query, rel = _note_query(ctx, path)
        existing = _existing_links(ctx, rel)
        hits = ctx.search.search(query, limit=_pool(limit, factor=3), rerank=True)
    except NotesError as exc:
        return exc.to_dict()
    suggestions = _filter_candidates(hits, rel, existing, max(limit, 1))
    return _ok({'path': rel, 'count': len(suggestions), 'suggestions': suggestions, 'existing_links': len(existing)})


# --------------------------------------------------------------------------- #
# query construction
# --------------------------------------------------------------------------- #
def _similar_query(ctx: ToolContext, path: str | None, text: str | None) -> tuple[str, str | None]:
    """Return ``(query, source_path)``; ``source_path`` is ``None`` for text mode."""
    if path is None and text is None:
        raise notes_error(
            PATH_REJECTED,
            'notes_similar needs exactly one of path or text',
            'Pass path=<vault note> to compare against a note, or text=<raw text>.',
        )
    if path is not None and text is not None:
        raise notes_error(
            PATH_REJECTED,
            'notes_similar accepts path or text, not both',
            'Pass exactly one of path=<vault note> or text=<raw text>.',
        )
    if text is not None:
        return _clean(text[:_EXCERPT]), None
    return _note_query(ctx, path)


def _note_query(ctx: ToolContext, path: str) -> tuple[str, str]:
    """Read a note and return ``(query, vault-relative path)``."""
    resolved = ctx.guard.resolve(path)
    if not resolved.is_file():
        raise notes_error(
            NOT_FOUND, f'note not found: {path}', 'Use a path from notes_list or notes_search.', path=path
        )
    _frontmatter, body = parse_frontmatter(resolved.read_text(encoding='utf-8'))
    rel = ctx.guard.relpath(resolved)
    return _clean(f'{Path(rel).stem}\n{body[:_EXCERPT]}'), rel


def _clean(text: str) -> str:
    """Strip markdown/wikilink syntax and collapse whitespace."""
    unlinked = _WIKILINK.sub(lambda match: match.group(2) or match.group(1), text)
    unlinked = _MD_LINK.sub(lambda match: match.group(1), unlinked)
    return ' '.join(_MD_MARKUP.sub(' ', unlinked).split())


# --------------------------------------------------------------------------- #
# ranking helpers
# --------------------------------------------------------------------------- #
def _pool(limit: int, *, factor: int) -> int:
    """Oversample the search pool, capped at :data:`_SUGGEST_CAP`."""
    return min(max(limit, 1) * factor, _SUGGEST_CAP)


def _collect(hits: list[Any], *, exclude: str | None, limit: int) -> list[dict[str, Any]]:
    """Project hits to ``{path, title, score}``, dropping ``exclude`` and truncating."""
    results: list[dict[str, Any]] = []
    for hit in hits:
        if _is_system(hit.path):
            continue
        if exclude is not None and _same_path(hit.path, exclude):
            continue
        results.append(_hit_dict(hit))
        if len(results) >= limit:
            break
    return results


def _filter_candidates(hits: list[Any], rel: str, existing: set[str], limit: int) -> list[dict[str, Any]]:
    """Drop the note itself and already-linked hits, then truncate to ``limit``."""
    linked = set(existing)
    suggestions: list[dict[str, Any]] = []
    for hit in hits:
        if _is_system(hit.path) or _same_path(hit.path, rel) or _link_keys(hit.path) & linked:
            continue
        suggestions.append(_hit_dict(hit))
        if len(suggestions) >= limit:
            break
    return suggestions


def _existing_links(ctx: ToolContext, rel: str) -> set[str]:
    """Return the normalised targets of every ``[[wikilink]]`` in the note."""
    raw = ctx.guard.resolve(rel).read_text(encoding='utf-8')
    return {_norm(target) for target in _LINK_TARGET.findall(raw)}


def _link_keys(path: str) -> set[str]:
    """Normalised keys a candidate path can be linked by: its path and its stem."""
    text = path.replace('\\', '/')
    return {_norm(text), _norm(Path(text).stem)}


def _norm(value: str) -> str:
    """Normalise a link target or path: posix, case-folded, without a ``.md`` suffix."""
    text = value.strip().replace('\\', '/').casefold()
    return text.removesuffix('.md')


def _same_path(left: str, right: str) -> bool:
    """Case-folded comparison of two vault-relative paths."""
    return left.replace('\\', '/').casefold() == right.replace('\\', '/').casefold()


def _is_system(path: str) -> bool:
    """True for ``00 Meta`` hits: system files are not link/similar candidates."""
    norm = path.replace('\\', '/').lstrip('/')
    return norm == '00 Meta' or norm.startswith('00 Meta/')


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
def _reject_options(options: dict[str, object]) -> None:
    """Raise ``TypeError`` on any unexpected keyword argument."""
    if options:
        unknown = ', '.join(sorted(options))
        raise TypeError(f'unexpected keyword argument(s): {unknown}')
