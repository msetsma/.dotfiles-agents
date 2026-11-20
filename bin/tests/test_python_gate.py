"""Tests for the bin/python_gate.py CLI and its wiring into bin/clean-python.

The clean-python tests need bash, git, ruff and uvx (for vulture and ty).

Run with: uv run --with pytest pytest bin/tests/test_python_gate.py
"""

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


BIN = Path(__file__).resolve().parents[1]
GATE = BIN / 'python_gate.py'
CLEAN_PYTHON = BIN / 'clean-python'
BASH = shutil.which('bash')
GIT = shutil.which('git')
HAS_TOOLS = shutil.which('uvx') is not None or (shutil.which('vulture') and shutil.which('ty'))
needs_gate = pytest.mark.skipif(
    BASH is None or GIT is None or shutil.which('ruff') is None or not HAS_TOOLS,
    reason='needs bash, git, ruff and uvx on PATH',
)

RUFF_TOML = '[lint]\nselect = ["F"]\n'
MUTABLE = 'from dataclasses import dataclass\n\n\n@dataclass\nclass A:\n    x: int\n\n\nprint(A)\n'


def write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding='utf-8')
    return path


def run_gate(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE), *args], capture_output=True, text=True, encoding='utf-8', check=False
    )


def check_file(path: Path, root: Path, phase: str = 'full') -> subprocess.CompletedProcess[str]:
    return run_gate(str(path), str(root), str(root), '--phase', phase, '--ruff', shutil.which('ruff') or 'ruff')


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    'args', [(), ('a.py', 'pkg'), ('--bogus', 'a', 'b'), ('a.py', 'pkg', 'root'), ('a', 'b', 'c', '--phase', 'x')]
)
def test_usage_error(args: tuple[str, ...]) -> None:
    assert run_gate(*args).returncode == 2


def test_unreadable_file_exits_3(tmp_path: Path) -> None:
    result = check_file(tmp_path / 'missing.py', tmp_path)
    assert result.returncode == 3
    assert result.stdout == ''


@needs_gate
def test_reports_findings_and_exits_1(tmp_path: Path) -> None:
    path = write(tmp_path, 'core.py', MUTABLE)
    result = check_file(path, tmp_path)
    assert result.returncode == 1
    assert 'GATE002' in result.stdout


@needs_gate
def test_non_ascii_paths_are_reported(tmp_path: Path) -> None:
    path = write(tmp_path / 'données 日本', 'core.py', MUTABLE)
    result = check_file(path, tmp_path)
    assert result.returncode == 1
    assert 'données 日本' in result.stdout


def test_session_changes_without_a_transcript_prints_nothing(tmp_path: Path) -> None:
    result = run_gate('--session-changes', str(tmp_path / 'none.jsonl'), str(tmp_path))
    assert result.returncode == 0
    assert result.stdout == ''


# --------------------------------------------------------------------------- #
# clean-python integration
# --------------------------------------------------------------------------- #
@pytest.fixture
def repo(tmp_path: Path) -> Path:
    assert GIT is not None
    subprocess.run([GIT, 'init', '-q'], cwd=tmp_path, check=True)
    (tmp_path / 'ruff.toml').write_text(RUFF_TOML, encoding='utf-8')
    return tmp_path


def run_clean(*args: str, cwd: Path, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    env = {**os.environ, 'TMPDIR': str(cwd)}
    return subprocess.run(
        [BASH, str(CLEAN_PYTHON), *args],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=False,
        env=env,
    )


def git(repo: Path, *args: str) -> None:
    assert GIT is not None
    subprocess.run([GIT, *args], cwd=repo, check=True, capture_output=True)


@needs_gate
def test_gate_findings_block_the_edit(repo: Path) -> None:
    path = write(repo, 'a.py', MUTABLE)
    result = run_clean(str(path), cwd=repo)
    assert result.returncode == 2
    assert 'GATE002' in result.stderr


@needs_gate
def test_gate_in_a_directory_with_brackets_and_spaces(repo: Path) -> None:
    path = write(repo / 'br [x]', 'a.py', MUTABLE)
    result = run_clean(str(path), cwd=repo)
    assert result.returncode == 2
    assert 'GATE002' in result.stderr


@needs_gate
def test_gate_crash_is_a_skipped_check_not_a_block(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    # A copy of bin/ whose python_gate.py crashes: clean-python must warn and pass.
    fake_bin = tmp_path_factory.mktemp('bin')
    shutil.copy(CLEAN_PYTHON, fake_bin / 'clean-python')
    (fake_bin / 'python_gate.py').write_text('raise RuntimeError("boom")\n', encoding='utf-8')
    path = write(repo, 'a.py', 'print(1)\n')
    assert BASH is not None
    result = subprocess.run(
        [BASH, str(fake_bin / 'clean-python'), str(path)], cwd=repo, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert 'checks skipped' in result.stderr


@needs_gate
def test_stop_checks_python_written_through_the_shell(repo: Path) -> None:
    # Never passed through PostToolUse: only the session scan can catch it.
    log = write(repo, 'session.jsonl', json.dumps({'timestamp': '2000-01-01T00:00:00Z'}) + '\n')
    write(repo, 'shell_written.py', 'import os\n')
    payload = json.dumps({'session_id': 'shell-sess', 'transcript_path': str(log), 'cwd': str(repo)})
    result = run_clean('--stop-check', cwd=repo, stdin=payload)
    assert result.returncode == 2
    assert 'shell_written.py' in result.stderr
    # Once it is fixed the stop passes, and a clean file is not re-checked.
    write(repo, 'shell_written.py', 'print(1)\n')
    assert run_clean('--stop-check', cwd=repo, stdin=payload).returncode == 0
    assert run_clean('--stop-check', cwd=repo, stdin=payload).returncode == 0


@needs_gate
def test_staged_passes_clean_files(repo: Path) -> None:
    write(repo, 'a.py', 'print("ok")\n')
    git(repo, 'add', 'a.py')
    result = run_clean('--staged', cwd=repo)
    assert result.returncode == 0, result.stderr


@needs_gate
def test_staged_refuses_findings_and_unformatted_code(repo: Path) -> None:
    write(repo, 'bad.py', 'import os\n')
    write(repo, 'ugly.py', "print( 'ok' )\n")
    write(repo, 'unstaged.py', 'import sys\n')
    git(repo, 'add', 'bad.py', 'ugly.py')
    result = run_clean('--staged', cwd=repo)
    assert result.returncode == 1
    assert 'F401' in result.stderr or 'unused-import' in result.stderr
    assert 'GATE007 not formatted; run: ruff format ugly.py' in result.stderr
    assert 'unstaged.py' not in result.stderr
    # Check only: the staged files are left as they were.
    assert (repo / 'bad.py').read_text(encoding='utf-8') == 'import os\n'


@needs_gate
def test_staged_refuses_a_file_with_unstaged_changes(repo: Path) -> None:
    path = write(repo, 'a.py', 'import os\n')
    git(repo, 'add', 'a.py')
    path.write_text('print("ok")\n', encoding='utf-8')
    result = run_clean('--staged', cwd=repo)
    assert result.returncode == 1
    assert 'a.py: has unstaged changes' in result.stderr
