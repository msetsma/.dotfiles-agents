"""Offline tests for githooks/: the Python gate at commit time, plus forwarding
to each repository's own .git/hooks.

Each test makes a throwaway repository whose local core.hooksPath points at
githooks/, which is what `make githooks` sets globally.

Run with: uv run --with pytest pytest bin/tests/test_githooks.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


HOOKS = Path(__file__).resolve().parents[2] / 'githooks'
GIT = shutil.which('git')
pytestmark = pytest.mark.skipif(
    GIT is None or shutil.which('bash') is None or shutil.which('ruff') is None,
    reason='needs git, bash and ruff on PATH',
)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    assert GIT is not None
    env = {**os.environ, 'TMPDIR': str(repo)}
    return subprocess.run([GIT, *args], cwd=repo, capture_output=True, text=True, check=False, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, 'init', '-q')
    git(tmp_path, 'config', 'user.email', 'test@example.com')
    git(tmp_path, 'config', 'user.name', 'test')
    git(tmp_path, 'config', 'core.hooksPath', HOOKS.as_posix())
    (tmp_path / 'ruff.toml').write_text('[lint]\nselect = ["F"]\n', encoding='utf-8')
    return tmp_path


def commit(repo: Path, name: str, text: str) -> subprocess.CompletedProcess[str]:
    (repo / name).write_text(text, encoding='utf-8')
    git(repo, 'add', name)
    return git(repo, 'commit', '-q', '-m', f'add {name}')


def test_every_hook_uses_lf_line_endings() -> None:
    for hook in HOOKS.iterdir():
        assert b'\r' not in hook.read_bytes(), hook.name


def test_clean_python_commits(repo: Path) -> None:
    result = commit(repo, 'a.py', 'print("ok")\n')
    assert result.returncode == 0, result.stderr


def test_failing_python_is_refused(repo: Path) -> None:
    result = commit(repo, 'a.py', 'import os\n')
    assert result.returncode != 0
    assert 'commit refused' in result.stderr


def test_non_python_commits(repo: Path) -> None:
    result = commit(repo, 'notes.txt', 'import os\n')
    assert result.returncode == 0, result.stderr


def test_repo_hooks_still_run(repo: Path) -> None:
    marker = repo / 'ran.txt'
    own = repo / '.git' / 'hooks' / 'post-commit'
    own.write_text(f'#!/bin/sh\necho ran > "{marker.as_posix()}"\n', encoding='utf-8', newline='\n')
    own.chmod(0o755)
    result = commit(repo, 'notes.txt', 'x\n')
    assert result.returncode == 0, result.stderr
    assert marker.exists()


def test_repo_pre_commit_runs_after_the_gate(repo: Path) -> None:
    own = repo / '.git' / 'hooks' / 'pre-commit'
    own.write_text('#!/bin/sh\necho repo-hook-refused >&2\nexit 1\n', encoding='utf-8', newline='\n')
    own.chmod(0o755)
    result = commit(repo, 'a.py', 'print("ok")\n')
    assert result.returncode != 0
    assert 'repo-hook-refused' in result.stderr
