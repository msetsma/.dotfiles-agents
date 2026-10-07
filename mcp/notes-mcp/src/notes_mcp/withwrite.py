"""The write protocol: lock, sync, mutate, commit, push (CONTRACT section 9).

``withWrite`` is the single funnel every mutating tool goes through. It holds
the vault lock, rebases onto the remote before the caller's writes run, snapshots
any pre-existing human edits to the target paths, commits exactly the paths the
tool names (never ``git add -A``), and pushes. Failures never escape as
exceptions: push exhaustion becomes ``pending_push`` with code
``PUSH_FAILED_LOCAL_COMMITTED`` on a success payload, a transient (offline) pull
annotates the success payload with ``PENDING_SYNC``, and a rebase conflict
becomes a ``CONFLICT`` error dict after a conflict note is written under
``00 Meta/03 Conflicts/`` (committed only when the tree is clean).

The contract signature is extended in exactly one place: ``lock`` is optional so
callers without a pre-built lock still get the standard ``.git`` sentinel.

Ruff (pep8-naming N802) forbids a camelCase ``def`` name, so the implementation
is ``with_write`` and the contract's public ``withWrite`` is exposed through a
module ``__getattr__`` (PEP 562). Both ``from notes_mcp.withwrite import
withWrite`` and ``withwrite.withWrite`` resolve to the same function.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from notes_mcp.errors import CONFLICT, PENDING_SYNC, PUSH_FAILED_LOCAL_COMMITTED, NotesError
from notes_mcp.lock import FileLock


if TYPE_CHECKING:
    from notes_mcp.config import Config
    from notes_mcp.git import Git, PullResult

_LOCK_REL_PARTS = ('.git', 'notes-mcp.lock')
_PUSH_CODE = PUSH_FAILED_LOCAL_COMMITTED
_REBASE_MARKERS = ('rebase-merge', 'rebase-apply', 'MERGE_HEAD')


def with_write(
    *,
    config: Config,
    git: Git,
    lock: FileLock | None = None,
    paths: list[str],
    tool: str,
    summary: str,
    fn: Callable[[], dict],
) -> dict[str, Any]:
    """Run ``fn`` inside the full git write protocol (CONTRACT section 9).

    Returns ``fn()``'s payload merged with ``commit``, ``pending_push``, and
    ``needs_index_update``. A rebase conflict returns the ``CONFLICT`` error
    envelope instead; it never raises.
    """
    if lock is None:
        lock = FileLock(Path(config.vault_path).joinpath(*_LOCK_REL_PARTS))
    lock.acquire()
    try:
        git.wait_for_index_lock()
        result = git.pull_rebase_autostash()
        if result.conflict:
            return _conflict_result(git, config, paths, summary, result)

        sync_error = None
        if result.transient:
            sync_error = {
                'code': PENDING_SYNC,
                'message': result.detail,
                'hint': 'Remote unreachable; the write was applied and committed locally.',
            }

        # Preserve pre-existing human edits to the target paths before the agent
        # mutates them, so the agent commit contains only the agent's change.
        git.snapshot_dirty(paths, tool=tool, summary=summary)

        payload = fn()
        if not isinstance(payload, dict):  # defensive: fn should return a dict
            payload = {'value': payload}

        # Never stage a path that no longer exists: a rename already staged the
        # deletion side (``git mv``), and ``git add`` on a missing path errors.
        add_paths = [path for path in paths if (config.vault_path / path).exists()]
        git.add(add_paths)

        payload['commit'] = git.commit(tool=tool, summary=summary, session=config.session, paths=paths)
        payload['pending_push'] = not git.push()
        payload['needs_index_update'] = bool(payload.get('needs_index_update', False))
        if sync_error is not None:
            payload['pending_sync'] = True
            payload['sync_code'] = PENDING_SYNC
            payload['sync_error'] = sync_error
        payload.setdefault('ok', True)
        if payload['pending_push']:
            _record_push_failure(payload, config)
        return payload
    finally:
        lock.release()


def _record_push_failure(payload: dict[str, Any], config: Config) -> None:
    """Annotate a success payload whose commit could not be pushed."""
    payload['push_code'] = _PUSH_CODE
    payload['push_error'] = {
        'code': _PUSH_CODE,
        'message': f'committed locally but push to {config.git_remote} failed',
        'hint': 'The commit is safe locally; run notes_sync or retry the write later.',
    }


def _conflict_result(git: Git, config: Config, paths: list[str], summary: str, result: PullResult) -> dict[str, Any]:
    """Turn a pull/rebase conflict into a CONFLICT envelope plus a conflict note."""
    git.abort_rebase()

    rel_target = paths[0] if paths else 'vault'
    note_rel = git.write_conflict_note(
        rel_path=rel_target,
        local_sha=result.local_sha,
        remote_sha=result.remote_sha,
        diff_excerpt=result.detail,
        intended=summary,
    )

    # The note is only committed when the tree is already clean; otherwise it
    # stays on disk for the human to review alongside the conflicted state.
    if _tree_is_clean(git):
        git.add([note_rel])
        git.commit(
            tool='notes_conflict', summary=f'conflict note for {rel_target}', session=config.session, paths=[note_rel]
        )
        git.push()

    error = NotesError(
        CONFLICT,
        f'could not rebase onto {config.git_remote}: {result.detail}',
        'A conflict note was written under 00 Meta/03 Conflicts; resolve it, then retry.',
        local_sha=result.local_sha,
        remote_sha=result.remote_sha,
    ).to_dict()
    error['error']['conflict_note'] = note_rel
    return error


def _tree_is_clean(git: Git) -> bool:
    """Whether no rebase/merge is in progress and no paths are unmerged."""
    git_dir = Path(git.cwd) / '.git'
    if any((git_dir / marker).exists() for marker in _REBASE_MARKERS):
        return False
    proc = git.run(['git', 'diff', '--name-only', '--diff-filter=U'], check=False)
    return not proc.stdout.strip()


def __getattr__(name: str) -> object:
    if name == 'withWrite':
        return with_write
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


__all__ = ['with_write']
