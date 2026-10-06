"""Structural tools: ``notes_move``, ``notes_rename``, ``notes_sync`` (section 10).

Move and rename run through :func:`notes_mcp.withwrite.with_write` so the lock,
pre-write rebase, commit, and push protocol is shared with the write tools. The
domain work -- ``git mv``, the frontmatter ``updated`` bump, and the vault-wide
wikilink rewrite -- happens inside the callback, under the vault lock.

``notes_sync`` is the one structural tool that does not commit: it takes the lock,
pulls with rebase + autostash, and reports the resulting branch state.

Each takes the shared ``ToolContext`` first and returns a plain dict. Contract
``NotesError``s become ``e.to_dict()``; anything else propagates. The published
MCP schema is camelCase, so the camelCase spellings are accepted as keyword
aliases and the Python names stay snake_case (ruff N803).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from notes_mcp import frontmatter
from notes_mcp.errors import DUPLICATE_FILENAME, NOT_FOUND, PATH_REJECTED, NotesError, notes_error
from notes_mcp.lock import FileLock
from notes_mcp.tools.write import _atomic_write
from notes_mcp.withwrite import with_write


if TYPE_CHECKING:
    from notes_mcp.context import ToolContext


_ARCHIVE_PATH = '90 Archive'
_META_PATH = '00 Meta'
_LOCK_REL_PARTS = ('.git', 'notes-mcp.lock')
_FENCE_MARKERS = ('```', '~~~')


# --------------------------------------------------------------------------- #
# public tools
# --------------------------------------------------------------------------- #
def notes_move(ctx: ToolContext, path: str, target_category_id: str | None = None, **options: object) -> dict[str, Any]:
    """Move a note into another category, preserving its filename.

    ``targetCategoryId`` is accepted as a camelCase alias for the snake_case
    ``target_category_id``. ``git mv`` keeps history; the moved note's
    ``updated`` date is bumped. Moving into or out of ``00 Meta`` is refused.
    """
    target_category_id = _alias(options, 'targetCategoryId', target_category_id)
    try:
        source = _resolve_note(ctx, path)
        old_rel = ctx.guard.relpath(source)
        category = ctx.index.validate(target_category_id)
        new_rel = f'{category.path}/{source.name}'
        if ctx.guard.is_meta(old_rel) or ctx.guard.is_meta(new_rel):
            raise _reject(old_rel, new_rel)
        if (ctx.config.vault_path / new_rel).exists():
            raise _duplicate(new_rel)

        result = with_write(
            config=ctx.config,
            git=ctx.git,
            paths=[new_rel],
            tool='notes_move',
            summary=new_rel,
            fn=lambda: _move_payload(ctx, old_rel, new_rel, category.path),
        )
    except NotesError as exc:
        return exc.to_dict()

    if result.get('ok') is not False:
        result.setdefault('ok', True)
        ctx.search.schedule_reindex()
    return result


def notes_rename(ctx: ToolContext, path: str, new_title: str | None = None, **options: object) -> dict[str, Any]:
    """Rename a note in place and rewrite its wikilinks across the vault.

    ``newTitle`` is accepted as a camelCase alias for the snake_case
    ``new_title``. ``[[Old]]``, ``[[Old|alias]]``, and ``[[Old#heading]]`` become
    the new title in every writable ``.md`` file, committed together with the
    rename. Links inside fenced code blocks are left untouched; links inside
    inline code spans are not distinguished (documented limitation).
    """
    new_title = _alias(options, 'newTitle', new_title)
    try:
        source = _resolve_note(ctx, path)
        old_rel = ctx.guard.relpath(source)
        if not new_title:
            raise notes_error(
                PATH_REJECTED, 'notes_rename needs a new title', f'Pass newTitle to rename {old_rel}.', path=old_rel
            )

        old_title = Path(old_rel).stem
        new_stem = ctx.guard.sanitize_filename(new_title)
        parent = Path(old_rel).parent.as_posix()
        new_rel = f'{parent}/{new_stem}.md' if parent != '.' else f'{new_stem}.md'
        if new_rel != old_rel and (ctx.config.vault_path / new_rel).exists():
            raise _duplicate(new_rel)

        paths = [new_rel]
        result = with_write(
            config=ctx.config,
            git=ctx.git,
            paths=paths,
            tool='notes_rename',
            summary=new_rel,
            fn=lambda: _rename_payload(ctx, old_rel, new_rel, old_title, new_stem, paths),
        )
    except NotesError as exc:
        return exc.to_dict()

    if result.get('ok') is not False:
        result.setdefault('ok', True)
        ctx.search.schedule_reindex()
    return result


def notes_sync(ctx: ToolContext) -> dict[str, Any]:
    """Pull the remote into the vault: lock, wait for the index, rebase, autostash.

    No commit is made. A pull/rebase conflict aborts the rebase and returns the
    ``CONFLICT`` error envelope.
    """
    lock = FileLock(Path(ctx.config.vault_path).joinpath(*_LOCK_REL_PARTS))
    try:
        lock.acquire()
    except NotesError as exc:
        return exc.to_dict()

    try:
        ctx.git.wait_for_index_lock()
        ctx.git.pull_rebase_autostash()
    except NotesError as exc:
        ctx.git.abort_rebase()
        return exc.to_dict()
    finally:
        lock.release()

    return {
        'ok': True,
        'branch': ctx.git.current_branch(),
        **ctx.git.status_summary(),
        'conflicts': False,
        'commit': None,
        'pending_push': False,
        'needs_index_update': False,
    }


# --------------------------------------------------------------------------- #
# payload builders (run inside with_write, under the lock)
# --------------------------------------------------------------------------- #
def _move_payload(ctx: ToolContext, old_rel: str, new_rel: str, category_path: str) -> dict[str, Any]:
    ctx.git.run(['git', 'mv', old_rel, new_rel])
    _bump_updated(ctx, new_rel)
    return {'path': new_rel, 'needs_index_update': category_path == _ARCHIVE_PATH}


def _rename_payload(
    ctx: ToolContext, old_rel: str, new_rel: str, old_title: str, new_title: str, paths: list[str]
) -> dict[str, Any]:
    if new_rel != old_rel:
        ctx.git.run(['git', 'mv', old_rel, new_rel])
    changed = _rewrite_links(ctx, old_title, new_title)
    paths.extend(rel for rel in changed if rel not in paths)
    _bump_updated(ctx, new_rel)
    return {'path': new_rel, 'rewritten': len(changed), 'needs_index_update': False}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _resolve_note(ctx: ToolContext, path: str) -> Path:
    resolved = ctx.guard.resolve(path, for_write=True)
    if not resolved.is_file():
        raise notes_error(
            NOT_FOUND, f'note not found: {path}', 'Use a path from notes_list or notes_search.', path=path
        )
    return resolved


def _bump_updated(ctx: ToolContext, rel: str) -> None:
    """Set ``updated`` to today (re-syncing the qmd mirror) and write atomically."""
    target = ctx.guard.resolve(rel, for_write=True)
    fm, body = frontmatter.parse(target.read_text(encoding='utf-8'))
    fm['updated'] = _today()
    frontmatter.sync_qmd_metadata(fm)
    _atomic_write(target, frontmatter.serialize(fm, body))


def _rewrite_links(ctx: ToolContext, old_title: str, new_title: str) -> list[str]:
    """Rewrite ``[[old_title...]]`` links vault-wide; return the changed rel paths."""
    changed: list[str] = []
    for rel, path in _writable_md(ctx):
        text = path.read_text(encoding='utf-8')
        rewritten, did_change = _rewrite_text(text, old_title, new_title)
        if not did_change:
            continue
        _atomic_write(path, rewritten)
        changed.append(rel)
    return changed


def _writable_md(ctx: ToolContext) -> list[tuple[str, Path]]:
    """Every ``.md`` file the guard permits writing (skips ``00 Meta`` and git dirs)."""
    writable: list[tuple[str, Path]] = []
    for path in sorted(ctx.config.vault_path.rglob('*.md')):
        rel = ctx.guard.relpath(path)
        try:
            ctx.guard.resolve(rel, for_write=True)
        except NotesError:
            continue
        writable.append((rel, path))
    return writable


def _rewrite_text(text: str, old_title: str, new_title: str) -> tuple[str, bool]:
    """Replace wikilink targets outside fenced code blocks; report whether any changed."""
    pattern = _link_pattern(old_title)

    def replace(_match: re.Match[str]) -> str:
        return f'[[{new_title}'

    lines = text.split('\n')
    fenced = False
    changed = False
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(_FENCE_MARKERS):
            fenced = not fenced
            out.append(line)
            continue
        if fenced:
            out.append(line)
            continue
        rewritten = pattern.sub(replace, line)
        if rewritten != line:
            changed = True
        out.append(rewritten)
    return '\n'.join(out), changed


def _link_pattern(old_title: str) -> re.Pattern[str]:
    """Match ``[[<old title>`` only when followed by ``]``, ``|``, or ``#``."""
    return re.compile(r'\[\[' + re.escape(old_title) + r'(?=[\]|#])')


def _alias(options: dict[str, object], key: str, current: str | None) -> str | None:
    if key in options:
        value = options.pop(key)
        return value if isinstance(value, str) else current
    return current


def _today() -> str:
    return datetime.now(tz=UTC).astimezone().date().isoformat()


def _duplicate(rel: str) -> NotesError:
    return NotesError(
        DUPLICATE_FILENAME,
        f'a note named {Path(rel).name!r} already exists',
        'Choose a different title or remove the existing note.',
        path=rel,
    )


def _reject(old_rel: str, new_rel: str) -> NotesError:
    return NotesError(
        PATH_REJECTED,
        f'refusing to move {old_rel!r} to or from {new_rel!r}',
        f'Notes cannot be moved into or out of {_META_PATH!r}.',
        path=new_rel,
    )
