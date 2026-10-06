"""Tests for the git write protocol (CONTRACT section 7).

Real git, no network: a bare repo in ``tmp_path`` acts as the remote and two
clones (human, vault) drive fast-forward, non-fast-forward, and conflict paths.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from notes_mcp.errors import CONFLICT, NotesError
from notes_mcp.git import Git


def _spawn(args: list[str], cwd: Path, *, check: bool = True, env: dict[str, str] | None = None):
    run = subprocess.run
    return run(args, cwd=cwd, capture_output=True, text=True, check=check, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> SimpleNamespace:
    env = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0'}

    def run(args: list[str], cwd: Path, *, check: bool = True):
        return _spawn(args, cwd, check=check, env=env)

    remote = tmp_path / 'remote.git'
    run(['git', 'init', '--bare', '--initial-branch=main', remote.as_posix()], tmp_path)

    human = tmp_path / 'human'
    run(['git', 'clone', remote.as_posix(), human.as_posix()], tmp_path)
    run(['git', 'config', 'commit.gpgsign', 'false'], human)
    run(['git', 'config', 'user.name', 'Human'], human)
    run(['git', 'config', 'user.email', 'human@example.com'], human)
    (human / 'seed.md').write_text('seed\n')
    run(['git', 'add', 'seed.md'], human)
    run(['git', 'commit', '-m', 'seed'], human)
    run(['git', 'push', 'origin', 'main'], human)

    vault = tmp_path / 'vault'
    run(['git', 'clone', remote.as_posix(), vault.as_posix()], tmp_path)
    run(['git', 'config', 'commit.gpgsign', 'false'], vault)
    run(['git', 'config', 'user.name', 'Vault'], vault)
    run(['git', 'config', 'user.email', 'vault@example.com'], vault)

    config = SimpleNamespace(vault_path=vault, git_remote='origin', agent_name='tester', session='sess-1')
    return SimpleNamespace(remote=remote, human=human, vault=vault, config=config, run=run)


def _remote_files(repo: SimpleNamespace) -> str:
    return repo.run(
        ['git', '--git-dir', repo.remote.as_posix(), 'ls-tree', '-r', '--name-only', 'main'], repo.vault
    ).stdout


def test_commit_uses_agent_identity(repo: SimpleNamespace) -> None:
    git = Git(repo.config)
    (repo.vault / 'note.md').write_text('body\n')
    git.add(['note.md'])
    sha = git.commit(tool='notes_create', summary='add note', session='sess-9')

    assert re.fullmatch(r'[0-9a-f]{40}', sha)
    proc = repo.run(['git', 'log', '-1', '--format=%an%x1f%ae%x1f%s%x1f%b'], repo.vault)
    author, email, subject, body = proc.stdout.split('\x1f')
    assert author == 'agent:tester'
    assert email == 'agent+tester@noreply.local'
    assert subject == 'agent(notes_create): add note'
    assert 'Agent: tester' in body
    assert 'Session: sess-9' in body


def test_add_stages_only_named_paths(repo: SimpleNamespace) -> None:
    git = Git(repo.config)
    (repo.vault / 'a.md').write_text('a\n')
    (repo.vault / 'b.md').write_text('b\n')

    git.add(['a.md'])

    staged = repo.run(['git', 'diff', '--cached', '--name-only'], repo.vault).stdout.split()
    assert staged == ['a.md']
    porcelain = repo.run(['git', 'status', '--porcelain'], repo.vault).stdout
    assert 'A  a.md' in porcelain
    assert '?? b.md' in porcelain


def test_push_succeeds_to_bare_remote(repo: SimpleNamespace) -> None:
    git = Git(repo.config)
    (repo.vault / 'note.md').write_text('x\n')
    git.add(['note.md'])
    git.commit(tool='t', summary='s', session='sess')

    assert git.push() is True
    assert 'note.md' in _remote_files(repo)


def test_non_fast_forward_rebases_then_pushes(repo: SimpleNamespace) -> None:
    git = Git(repo.config)
    (repo.human / 'human.md').write_text('human\n')
    repo.run(['git', 'add', 'human.md'], repo.human)
    repo.run(['git', 'commit', '-m', 'human commit'], repo.human)
    repo.run(['git', 'push', 'origin', 'main'], repo.human)

    (repo.vault / 'vault.md').write_text('vault\n')
    git.add(['vault.md'])
    git.commit(tool='t', summary='vault commit', session='sess')

    assert git.push() is True
    files = _remote_files(repo)
    assert 'human.md' in files
    assert 'vault.md' in files


def test_push_conflict_aborts_and_returns_false(repo: SimpleNamespace) -> None:
    git = Git(repo.config)
    (repo.human / 'seed.md').write_text('human change\n')
    repo.run(['git', 'add', 'seed.md'], repo.human)
    repo.run(['git', 'commit', '-m', 'human edit'], repo.human)
    repo.run(['git', 'push', 'origin', 'main'], repo.human)

    (repo.vault / 'seed.md').write_text('vault change\n')
    git.add(['seed.md'])
    git.commit(tool='t', summary='vault edit', session='sess')

    assert git.push(retries=2) is False
    git_dir = repo.vault / '.git'
    assert not (git_dir / 'rebase-merge').exists()
    assert not (git_dir / 'rebase-apply').exists()
    assert git.current_branch() == 'main'


def test_wait_for_index_lock_times_out_then_clears(repo: SimpleNamespace) -> None:
    git = Git(repo.config)
    lock = repo.vault / '.git' / 'index.lock'
    lock.write_text('')

    with pytest.raises(NotesError) as excinfo:
        git.wait_for_index_lock(timeout=0.3)
    assert excinfo.value.code == CONFLICT

    lock.unlink()
    git.wait_for_index_lock(timeout=1.0)


def test_status_summary_and_log(repo: SimpleNamespace) -> None:
    git = Git(repo.config)
    assert git.current_branch() == 'main'

    (repo.vault / 'note.md').write_text('x\n')
    git.add(['note.md'])
    git.commit(tool='notes_create', summary='add note', session='sess')

    summary = git.status_summary()
    assert summary['branch'] == 'main'
    assert summary['ahead'] == 1
    assert summary['behind'] == 0
    assert 'last_pull' in summary

    entries = git.log_name_only(since='1 year ago', limit=10)
    assert entries
    assert entries[0]['author'] == 'agent:tester'
    assert 'note.md' in entries[0]['paths']


def test_write_conflict_note(repo: SimpleNamespace) -> None:
    git = Git(repo.config)
    rel = git.write_conflict_note(
        rel_path='10 Projects/Note.md',
        local_sha='a' * 40,
        remote_sha='b' * 40,
        diff_excerpt='<<<<<<< HEAD',
        intended='Keep the local wording.',
    )

    assert rel.startswith('00 Meta/03 Conflicts/')
    name = Path(rel).name
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}-\d{4} Note\.md', name)
    text = (repo.vault / rel).read_text(encoding='utf-8')
    assert 'a' * 40 in text
    assert 'b' * 40 in text
    assert 'Keep the local wording.' in text
