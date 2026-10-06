"""Tests for the write protocol (CONTRACT section 9).

Real git, no network: the M4 ``vault`` fixture provides a bare remote plus human
and agent clones, so every protocol branch (happy path, autostash, push retry,
push exhaustion, rebase conflict) runs against genuine git.
"""

from __future__ import annotations

import re
import subprocess

from notes_mcp.errors import PUSH_FAILED_LOCAL_COMMITTED
from notes_mcp.git import Git
from notes_mcp.withwrite import withWrite


GIT = '/usr/bin/git'


def _run(args: list[str], cwd) -> subprocess.CompletedProcess:
    run = subprocess.run
    return run(args, cwd=str(cwd), capture_output=True, text=True, check=True)


def _human_commit(vault, rel: str, text: str) -> None:
    """Commit and push ``rel`` from the human clone (the concurrent-editor role)."""
    path = vault.human / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    _run([GIT, 'add', rel], vault.human)
    _run([GIT, 'commit', '-m', f'human: {rel}'], vault.human)
    _run([GIT, 'push', 'origin', 'main'], vault.human)


def _remote_files(vault) -> str:
    run = subprocess.run
    proc = run(
        [GIT, '--git-dir', str(vault.remote), 'ls-tree', '-r', '--name-only', 'main'],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def _last_author_and_subject(vault) -> tuple[str, str]:
    proc = _run([GIT, 'log', '-1', '--format=%an%x1f%s'], vault.agent)
    author, subject = proc.stdout.strip().split('\x1f')
    return author, subject


def test_withwrite_commits_as_agent_and_pushes(vault) -> None:
    config = vault.config()
    git = Git(config)

    def fn() -> dict:
        (config.vault_path / 'new-note.md').write_text('hello\n', encoding='utf-8')
        return {'path': 'new-note.md'}

    result = withWrite(config=config, git=git, paths=['new-note.md'], tool='notes_create', summary='New Note', fn=fn)

    assert re.fullmatch(r'[0-9a-f]{40}', result['commit'])
    assert result['pending_push'] is False
    assert result['needs_index_update'] is False
    assert result['path'] == 'new-note.md'

    author, subject = _last_author_and_subject(vault)
    assert author == 'agent:claude'
    assert subject == 'agent(notes_create): New Note'
    assert 'new-note.md' in _remote_files(vault)


def test_withwrite_autostash_keeps_dirty_edits(vault) -> None:
    config = vault.config()
    git = Git(config)
    dirty = config.vault_path / '30 Resources/33 Concepts/Johnny Decimal.md'
    dirty.write_text(dirty.read_text(encoding='utf-8') + '\nDIRTY EDIT\n', encoding='utf-8')

    _human_commit(vault, 'human-peer.md', 'peer\n')

    def fn() -> dict:
        (config.vault_path / 'clean-note.md').write_text('clean\n', encoding='utf-8')
        return {'path': 'clean-note.md'}

    result = withWrite(
        config=config, git=git, paths=['clean-note.md'], tool='notes_create', summary='Clean Note', fn=fn
    )

    assert result['pending_push'] is False
    assert 'DIRTY EDIT' in dirty.read_text(encoding='utf-8')
    files = _remote_files(vault)
    assert 'human-peer.md' in files
    assert 'clean-note.md' in files
    # The dirty edit was stashed, not committed.
    staged = _run([GIT, 'show', '--format=', '--name-only', 'HEAD'], vault.agent).stdout
    assert 'Johnny Decimal.md' not in staged


def test_withwrite_push_retry_after_remote_diverges(vault) -> None:
    config = vault.config()
    git = Git(config)

    def fn() -> dict:
        (config.vault_path / 'retry-note.md').write_text('retry\n', encoding='utf-8')
        _human_commit(vault, 'human-during.md', 'during\n')
        return {'path': 'retry-note.md'}

    result = withWrite(
        config=config, git=git, paths=['retry-note.md'], tool='notes_create', summary='Retry Note', fn=fn
    )

    assert result['pending_push'] is False
    assert result['commit']
    files = _remote_files(vault)
    assert 'human-during.md' in files
    assert 'retry-note.md' in files


def test_withwrite_unrecoverable_push_sets_pending(vault) -> None:
    config = vault.config()
    git = Git(config)
    rel = '30 Resources/33 Concepts/Johnny Decimal.md'

    def fn() -> dict:
        (config.vault_path / rel).write_text('# Johnny Decimal\n\nagent version\n', encoding='utf-8')
        _human_commit(vault, rel, '# Johnny Decimal\n\nhuman version\n')
        return {'path': rel}

    result = withWrite(config=config, git=git, paths=[rel], tool='notes_update', summary=rel, fn=fn)

    assert result.get('ok') is not False
    assert result['pending_push'] is True
    assert result['commit']
    assert result['push_error']['code'] == PUSH_FAILED_LOCAL_COMMITTED
    assert result['push_code'] == PUSH_FAILED_LOCAL_COMMITTED
    assert 'agent version' not in _remote_files(vault)


def test_withwrite_conflict_writes_note(vault) -> None:
    config = vault.config()
    git = Git(config)
    rel = '30 Resources/33 Concepts/Johnny Decimal.md'

    # Agent diverges locally first...
    (config.vault_path / rel).write_text('# Johnny Decimal\n\nagent local\n', encoding='utf-8')
    git.add([rel])
    git.commit(tool='notes_update', summary='local edit', session='sess')
    # ...then the remote moves on under the same file.
    _human_commit(vault, rel, '# Johnny Decimal\n\nhuman remote\n')

    called: list[bool] = []

    def fn() -> dict:
        called.append(True)
        return {'path': rel}

    result = withWrite(config=config, git=git, paths=[rel], tool='notes_update', summary=rel, fn=fn)

    assert result['ok'] is False
    assert result['error']['code'] == 'CONFLICT'
    assert result['error']['conflict_note'].startswith('00 Meta/03 Conflicts/')
    assert called == []
    notes = list((config.vault_path / '00 Meta/03 Conflicts').glob('*.md'))
    assert notes
    assert git.current_branch() == 'main'
