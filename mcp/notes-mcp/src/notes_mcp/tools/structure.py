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
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from notes_mcp import frontmatter
from notes_mcp.atomic import atomic_write
from notes_mcp.errors import DUPLICATE_FILENAME, NOT_FOUND, PATH_REJECTED, NotesError, notes_error
from notes_mcp.index import INDEX_REL_PATH
from notes_mcp.lock import FileLock
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

        paths = list(dict.fromkeys([old_rel, new_rel]))
        result = with_write(
            config=ctx.config,
            git=ctx.git,
            paths=paths,
            tool='notes_move',
            summary=new_rel,
            fn=lambda: _move_payload(ctx, old_rel, new_rel, category.path, paths),
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

        paths = list(dict.fromkeys([old_rel, new_rel]))
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


def notes_move_category(ctx: ToolContext, category_id: str, new_path: str) -> dict[str, Any]:
    """Relocate a whole category folder, updating the Index and its links.

    ``git mv`` moves the directory recursively; every path-qualified wikilink
    whose target lay under the old path is rewritten, and the Index ``Path``
    row is updated, all in one commit. Categories may not move into or out of
    ``00 Meta``, onto an existing directory, or onto their own path.
    """
    try:
        category = ctx.index.validate(category_id)
        old_dir = category.path
        new_dir = _normalize_new_dir(ctx, category.id, old_dir, new_path)
        old_files = _category_files(ctx, old_dir)
        new_files = [_relocate(rel, old_dir, new_dir) for rel in old_files]
        paths = list(dict.fromkeys([*old_files, *new_files, INDEX_REL_PATH]))

        result = with_write(
            config=ctx.config,
            git=ctx.git,
            paths=paths,
            tool='notes_move_category',
            summary=f'{old_dir} -> {new_dir}',
            fn=lambda: _move_category_payload(ctx, category_id, old_dir, new_dir),
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
def _move_payload(ctx: ToolContext, old_rel: str, new_rel: str, category_path: str, paths: list[str]) -> dict[str, Any]:
    ctx.git.run(['git', 'mv', old_rel, new_rel])
    changed = _rewrite_links(ctx, _path_patterns(old_rel, new_rel))
    _extend_paths(paths, changed)
    _bump_updated(ctx, new_rel)
    return {'path': new_rel, 'rewritten': len(changed), 'needs_index_update': category_path == _ARCHIVE_PATH}


def _rename_payload(
    ctx: ToolContext, old_rel: str, new_rel: str, old_title: str, new_title: str, paths: list[str]
) -> dict[str, Any]:
    if new_rel != old_rel:
        ctx.git.run(['git', 'mv', old_rel, new_rel])
    patterns = [(_link_pattern(old_title), f'[[{new_title}')]
    patterns += _path_patterns(old_rel, new_rel)
    changed = _rewrite_links(ctx, patterns)
    _extend_paths(paths, changed)
    _bump_updated(ctx, new_rel)
    return {'path': new_rel, 'rewritten': len(changed), 'needs_index_update': False}


def _move_category_payload(ctx: ToolContext, category_id: str, old_dir: str, new_dir: str) -> dict[str, Any]:
    ctx.git.run(['git', 'mv', old_dir, new_dir])
    changed = _rewrite_links(ctx, _prefix_patterns(old_dir, new_dir))
    ctx.index.apply_path(category_id, new_dir)
    return {'path': new_dir, 'index_path': INDEX_REL_PATH, 'rewritten': len(changed), 'needs_index_update': False}


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
    atomic_write(target, frontmatter.serialize(fm, body))


def _rewrite_links(ctx: ToolContext, patterns: list[tuple[re.Pattern[str], str]]) -> list[str]:
    """Rewrite matching wikilink targets vault-wide; return the changed rel paths."""
    changed: list[str] = []
    for rel, path in _writable_md(ctx):
        text = path.read_text(encoding='utf-8')
        rewritten, did_change = _rewrite_text(text, patterns)
        if not did_change:
            continue
        atomic_write(path, rewritten)
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


def _rewrite_text(text: str, patterns: list[tuple[re.Pattern[str], str]]) -> tuple[str, bool]:
    """Replace wikilink targets outside fenced code blocks; report whether any changed."""
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
        rewritten = line
        for pattern, replacement in patterns:
            rewritten = pattern.sub(lambda _match, _replacement=replacement: _replacement, rewritten)
        if rewritten != line:
            changed = True
        out.append(rewritten)
    return '\n'.join(out), changed


def _link_pattern(old_title: str) -> re.Pattern[str]:
    """Match ``[[<old title>`` only when followed by ``]``, ``|``, or ``#``."""
    return re.compile(r'\[\[' + re.escape(old_title) + r'(?=[\]|#])')


def _path_patterns(old_rel: str, new_rel: str) -> list[tuple[re.Pattern[str], str]]:
    """Anchored path-qualified link pairs for a moved or renamed note."""
    old_target = _strip_md(old_rel)
    new_target = _strip_md(new_rel)
    if old_target == new_target:
        return []
    return [(_link_pattern(old_target), f'[[{new_target}')]


def _prefix_patterns(old_dir: str, new_dir: str) -> list[tuple[re.Pattern[str], str]]:
    """Prefix replacement for every link whose target was under ``old_dir/``."""
    pattern = re.compile(r'\[\[' + re.escape(old_dir + '/'))
    return [(pattern, f'[[{new_dir}/')]


def _strip_md(rel: str) -> str:
    return rel.removesuffix('.md')


def _extend_paths(paths: list[str], extra: list[str]) -> None:
    for rel in extra:
        if rel not in paths:
            paths.append(rel)


def _normalize_new_dir(ctx: ToolContext, category_id: str, old_dir: str, new_path: str) -> str:
    """Normalise, prefix with the ``NN `` id, and validate a category destination."""
    normalized = re.sub(r'/+', '/', new_path.replace('\\', '/').strip('/'))
    if not normalized:
        raise _reject_path(new_path, 'the destination is empty')
    parent = PurePosixPath(normalized).parent
    base = PurePosixPath(normalized).name
    prefix = f'{category_id} '
    if not base.startswith(prefix):
        base = f'{prefix}{base}'
    new_dir = base if str(parent) == '.' else f'{parent}/{base}'
    if new_dir == old_dir:
        raise _reject_path(new_dir, 'the category is already at that path')
    if ctx.guard.is_meta(new_dir):
        raise _reject_path(new_dir, 'categories may not live under 00 Meta')
    if (ctx.config.vault_path / new_dir).is_dir():
        raise _reject_path(new_dir, 'a directory already exists there')
    return new_dir


def _category_files(ctx: ToolContext, old_dir: str) -> list[str]:
    """Every ``.md`` file currently under the category directory, vault-relative."""
    root = ctx.config.vault_path / old_dir
    return sorted(ctx.guard.relpath(path) for path in root.rglob('*.md'))


def _relocate(rel: str, old_dir: str, new_dir: str) -> str:
    return f'{new_dir}{rel[len(old_dir) :]}'


def _reject_path(rel: str, reason: str) -> NotesError:
    return NotesError(
        PATH_REJECTED,
        f'refusing to move the category to {rel!r} ({reason})',
        'Choose a vault-relative directory outside 00 Meta that does not already exist.',
        path=rel,
    )


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
