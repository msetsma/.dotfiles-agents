"""Tests for bin/pygate/session.py: Python files changed during a session."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from pygate.session import changed_python_files, session_start


GIT = shutil.which('git')
needs_git = pytest.mark.skipif(GIT is None, reason='needs git on PATH')


def transcript(tmp_path: Path, *records: object) -> Path:
    path = tmp_path / 'session.jsonl'
    path.write_text(''.join(f'{json.dumps(r)}\n' for r in records), encoding='utf-8')
    return path


def test_session_start_is_the_first_timestamp(tmp_path: Path) -> None:
    path = transcript(
        tmp_path,
        {'type': 'summary'},
        {'timestamp': '2026-11-18T12:00:00.000Z'},
        {'timestamp': '2026-11-18T13:00:00.000Z'},
    )
    assert session_start(path) == pytest.approx(1795003200.0)


def test_session_start_without_a_transcript_is_none(tmp_path: Path) -> None:
    assert session_start(tmp_path / 'missing.jsonl') is None
    assert session_start(transcript(tmp_path, {'type': 'summary'})) is None


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    assert GIT is not None
    subprocess.run([GIT, 'init', '-q'], cwd=tmp_path, check=True)
    return tmp_path


def touch(path: Path, mtime: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('x = 1\n', encoding='utf-8')
    os.utime(path, (mtime, mtime))
    return path


@needs_git
def test_changed_files_are_dirty_python_files_newer_than_the_start(repo: Path) -> None:
    start = time.time() - 100
    new = touch(repo / 'pkg' / 'new.py', start + 10)
    touch(repo / 'old.py', start - 10)
    touch(repo / 'notes.txt', start + 10)
    stub = touch(repo / 'types.pyi', start + 10)
    found = {p.resolve() for p in changed_python_files(repo / 'pkg', start)}
    assert found == {new.resolve(), stub.resolve()}


@needs_git
def test_committed_unchanged_files_are_not_changed(repo: Path) -> None:
    assert GIT is not None
    start = time.time() - 100
    touch(repo / 'a.py', start + 10)
    subprocess.run([GIT, 'add', 'a.py'], cwd=repo, check=True)
    subprocess.run(
        [
            GIT,
            '-c',
            'user.email=t@example.com',
            '-c',
            'user.name=t',
            '-c',
            'core.hooksPath=/dev/null',
            'commit',
            '-qm',
            'a',
        ],
        cwd=repo,
        check=True,
    )
    assert changed_python_files(repo, start) == []


def test_outside_git_nothing_changed(tmp_path: Path) -> None:
    touch(tmp_path / 'a.py', time.time())
    assert changed_python_files(tmp_path, 0) == []
