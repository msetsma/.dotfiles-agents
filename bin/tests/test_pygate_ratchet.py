"""Tests for the ratchet: only findings a change introduces are reported.

Unit tests cover pygate/ratchet.py; the rest drive bin/python_gate.py and
bin/clean-python on a git repository whose HEAD holds the legacy version of a
file. Those need bash, git, ruff and uvx (for vulture and ty).

Run with: uv run --with pytest pytest bin/tests/test_pygate_ratchet.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from pygate.baseline import Committed, changed_lines, committed
from pygate.findings import Finding
from pygate.ratchet import introduced, on_lines
from pygate.source_checks import MAX_LINES


BIN = Path(__file__).resolve().parents[1]
GATE = BIN / 'python_gate.py'
CLEAN_PYTHON = BIN / 'clean-python'
BASH, GIT, RUFF = shutil.which('bash'), shutil.which('git'), shutil.which('ruff')
HAS_TOOLS = shutil.which('uvx') is not None or (shutil.which('vulture') and shutil.which('ty'))
needs_git = pytest.mark.skipif(GIT is None, reason='needs git on PATH')
needs_gate = pytest.mark.skipif(
    BASH is None or GIT is None or RUFF is None or not HAS_TOOLS, reason='needs bash, git, ruff and uvx on PATH'
)

# F401 is unfixable so a pre-existing one stays put; F541 (f-string without
# placeholders) is fixable, which shows whether auto-fix ran.
RUFF_TOML = '[lint]\nselect = ["F"]\nunfixable = ["F401"]\n'
PATH = Path('a.py')


def finding(line: int, code: str = 'F401', message: str = 'unused') -> Finding:
    return Finding(PATH, line, 1, code, message)


# --------------------------------------------------------------------------- #
# pygate/ratchet.py
# --------------------------------------------------------------------------- #
def test_moved_finding_is_not_new() -> None:
    baseline = [finding(1)]
    current = [finding(3)]
    assert introduced(current, 'x = 1\n\nimport os\n', baseline, 'import os\n') == []


def test_same_text_with_a_new_message_is_new() -> None:
    assert introduced([finding(1, message='other')], 'import os\n', [finding(1)], 'import os\n') != []


def test_a_second_copy_of_a_finding_is_new() -> None:
    current = [finding(1), finding(2)]
    assert introduced(current, 'import os\nimport os\n', [finding(1)], 'import os\n') == [finding(2)]


def test_file_level_finding_matches_by_code() -> None:
    long_finding = finding(MAX_LINES + 1, 'GATE001', 'File has 900 lines')
    old = finding(MAX_LINES + 1, 'GATE001', 'File has 850 lines')
    assert introduced([long_finding], 'x\n' * 900, [old], 'y\n' * 850) == []


def test_line_reference_in_a_message_is_not_part_of_the_fingerprint() -> None:
    old = finding(8, 'F811', 'Redefinition of unused `f` from line 4')
    new = finding(10, 'F811', 'Redefinition of unused `f` from line 6')
    assert introduced([new], '\n' * 9 + 'def f(): ...\n', [old], '\n' * 7 + 'def f(): ...\n') == []


def test_growing_a_metric_already_over_its_limit_is_not_new() -> None:
    old = finding(1, 'PLR0915', 'Too many statements (56 > 50)')
    new = finding(1, 'PLR0915', 'Too many statements (57 > 50)')
    assert introduced([new], 'def big():\n', [old], 'def big():\n') == []


def test_on_lines_keeps_changed_lines_only() -> None:
    found = [finding(1), finding(5)]
    assert on_lines(found, frozenset({5})) == [finding(5)]
    assert on_lines(found, None) == found


# --------------------------------------------------------------------------- #
# pygate/baseline.py
# --------------------------------------------------------------------------- #
def git(repo: Path, *args: str) -> None:
    assert GIT is not None
    subprocess.run([GIT, *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    # Spaces, brackets and non-ASCII in the path: git and bash must both cope.
    root = tmp_path / 'r [x] é'
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.email', 'gate@example.invalid')
    git(root, 'config', 'user.name', 'gate')
    git(root, 'config', 'core.autocrlf', 'false')
    # A fixture repo: no global hooks (this gate's own pre-commit) and no signing.
    git(root, 'config', 'core.hooksPath', '.git/hooks')
    git(root, 'config', 'commit.gpgsign', 'false')
    (root / 'ruff.toml').write_text(RUFF_TOML, encoding='utf-8')
    return root


def write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding='utf-8', newline='\n')
    return path


def commit(repo: Path, name: str, text: str) -> Path:
    path = write(repo, name, text)
    git(repo, 'add', '--', name)
    git(repo, 'commit', '-q', '-m', 'legacy')
    return path


@needs_git
def test_baseline_is_the_committed_text(repo: Path) -> None:
    path = commit(repo, 'br [x]/a.py', 'x = 1\ny = 2\n')
    path.write_text('x = 1\nz = 3\ny = 2\n', encoding='utf-8', newline='\n')
    base = committed(path)
    assert base == Committed('x = 1\ny = 2\n')
    assert changed_lines(path, base) == frozenset({2})


@needs_git
def test_no_baseline_for_untracked_or_outside_git(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    commit(repo, 'a.py', 'x = 1\n')
    assert committed(write(repo, 'new.py', 'x = 1\n')) is None
    assert committed(write(tmp_path_factory.mktemp('nogit'), 'a.py', 'x = 1\n')) is None


@needs_git
def test_deletion_marks_the_neighbouring_lines(repo: Path) -> None:
    path = commit(repo, 'a.py', 'a = 1\nb = 2\nc = 3\n')
    path.write_text('a = 1\nc = 3\n', encoding='utf-8', newline='\n')
    assert changed_lines(path, Committed('a = 1\nb = 2\nc = 3\n')) == frozenset({1, 2})


@needs_git
def test_renamed_file_takes_its_old_paths_baseline(repo: Path) -> None:
    commit(repo, 'old.py', 'a = 1\nb = 2\n')
    (repo / 'sub é [x]').mkdir()
    git(repo, 'mv', 'old.py', 'sub é [x]/new.py')
    path = repo / 'sub é [x]' / 'new.py'
    path.write_text('a = 1\nb = 2\nc = 3\n', encoding='utf-8', newline='\n')
    base = committed(path)
    assert base == Committed('a = 1\nb = 2\n', 'old.py')
    assert changed_lines(path, base) == frozenset({3})


# --------------------------------------------------------------------------- #
# bin/python_gate.py on a committed file
# --------------------------------------------------------------------------- #
def gate(path: Path, repo: Path, phase: str, *flags: str) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(GATE), str(path), str(repo), str(repo), '--phase', phase, '--ruff', str(RUFF)]
    argv += ['--config', str(repo / 'ruff.toml'), *flags]
    return subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', check=False)


FINDING = re.compile(r'^.+?:\d+:\d+: (?P<code>\S+) ')


def codes(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [match['code'] for line in result.stdout.splitlines() if (match := FINDING.match(line))]


LEGACY = 'import os\nimport sys\n\nprint(sys.argv)\n'


@needs_gate
def test_pre_existing_finding_is_not_reported_after_an_unrelated_edit(repo: Path) -> None:
    path = commit(repo, 'a.py', LEGACY)
    path.write_text(LEGACY + 'print(sys.path)\n', encoding='utf-8', newline='\n')
    result = gate(path, repo, 'edit', '--fix')
    assert result.returncode == 0, result.stdout + result.stderr


@needs_gate
def test_new_finding_is_reported(repo: Path) -> None:
    path = commit(repo, 'a.py', LEGACY)
    path.write_text('import json\n' + LEGACY, encoding='utf-8', newline='\n')
    result = gate(path, repo, 'edit')
    assert result.returncode == 1
    assert '`json` imported but unused' in result.stdout
    assert '`os` imported but unused' not in result.stdout


@needs_gate
def test_finding_caused_on_an_unchanged_line_is_reported(repo: Path) -> None:
    # Removing the only use makes the untouched import unused.
    path = commit(repo, 'a.py', 'import os\n\nprint(os.sep)\n')
    path.write_text('import os\n\nprint(1)\n', encoding='utf-8', newline='\n')
    result = gate(path, repo, 'edit')
    assert result.returncode == 1
    assert codes(result) == ['F401']


@needs_gate
def test_legacy_long_file_is_not_reported_but_crossing_the_limit_is(repo: Path) -> None:
    long_file = commit(repo, 'long.py', 'x = 1\n' * (MAX_LINES + 10))
    long_file.write_text('x = 1\n' * (MAX_LINES + 12), encoding='utf-8', newline='\n')
    assert gate(long_file, repo, 'edit').returncode == 0
    short_file = commit(repo, 'short.py', 'x = 1\n' * (MAX_LINES - 1))
    short_file.write_text('x = 1\n' * (MAX_LINES + 2), encoding='utf-8', newline='\n')
    assert codes(gate(short_file, repo, 'edit')) == ['GATE001']


# A ty error (GATE004) and dead code (GATE003) in the committed version.
PROJECT_LEGACY = """
    def legacy_dead() -> None:
        pass


    count: int = 'one'
    print(count)
"""
PROJECT_NEW = """

    def new_dead() -> None:
        pass


    total: int = 'two'
    print(total)
"""


@needs_gate
def test_project_findings_count_only_on_changed_lines(repo: Path) -> None:
    path = commit(repo, 'core.py', PROJECT_LEGACY)
    legacy = path.read_text(encoding='utf-8')
    path.write_text(legacy + 'print(count)\n', encoding='utf-8', newline='\n')
    assert gate(path, repo, 'full').returncode == 0
    path.write_text(legacy + textwrap.dedent(PROJECT_NEW), encoding='utf-8', newline='\n')
    result = gate(path, repo, 'full')
    assert sorted(codes(result)) == ['GATE003', 'GATE004'], result.stdout
    assert 'new_dead' in result.stdout
    assert 'legacy_dead' not in result.stdout


@needs_gate
def test_edit_phase_skips_vulture_and_ty(repo: Path) -> None:
    path = write(repo, 'core.py', PROJECT_LEGACY)
    assert gate(path, repo, 'edit').returncode == 0
    assert sorted(codes(gate(path, repo, 'full'))) == ['GATE003', 'GATE004']


@needs_gate
def test_legacy_messy_file_is_neither_fixed_nor_formatted(repo: Path) -> None:
    messy = "import os\nx=f'a'\n"
    path = commit(repo, 'a.py', messy)
    path.write_text(messy + 'y=1\n', encoding='utf-8', newline='\n')
    gate(path, repo, 'edit', '--fix')
    assert path.read_text(encoding='utf-8') == messy + 'y=1\n'


@needs_gate
def test_clean_baseline_is_still_fixed_and_formatted(repo: Path) -> None:
    path = commit(repo, 'a.py', 'x = "a"\n')
    path.write_text('x = "a"\ny=f"b"\n', encoding='utf-8', newline='\n')
    result = gate(path, repo, 'edit', '--fix')
    assert result.returncode == 0, result.stdout
    assert path.read_text(encoding='utf-8') == 'x = "a"\ny = "b"\n'


@needs_gate
def test_staged_format_check_skips_a_legacy_unformatted_file(repo: Path) -> None:
    path = commit(repo, 'a.py', 'x=1\n')
    path.write_text('x=1\ny=2\n', encoding='utf-8', newline='\n')
    assert gate(path, repo, 'edit', '--format-check').returncode == 0
    clean = commit(repo, 'b.py', 'x = 1\n')
    clean.write_text('x = 1\ny=2\n', encoding='utf-8', newline='\n')
    assert codes(gate(clean, repo, 'edit', '--format-check')) == ['GATE007']


@needs_gate
def test_shifted_legacy_redefinition_is_not_reported(repo: Path) -> None:
    legacy = 'def f() -> None:\n    pass\n\n\ndef f() -> None:\n    pass\n'
    path = commit(repo, 'a.py', legacy)
    path.write_text('"""Doc."""\n\n' + legacy, encoding='utf-8', newline='\n')
    result = gate(path, repo, 'edit')
    assert result.returncode == 0, result.stdout


@needs_gate
def test_rename_alone_introduces_nothing_and_rewrites_nothing(repo: Path) -> None:
    commit(repo, 'a.py', "import os\nx=f'a'\n")
    git(repo, 'mv', 'a.py', 'b.py')
    path = repo / 'b.py'
    result = gate(path, repo, 'edit', '--fix')
    assert result.returncode == 0, result.stdout + result.stderr
    assert path.read_text(encoding='utf-8') == "import os\nx=f'a'\n"


@needs_gate
@pytest.mark.parametrize('flags', [('--fix',), ('--format-check',)])
def test_syntax_error_is_reported_not_skipped(repo: Path, flags: tuple[str, ...]) -> None:
    path = commit(repo, 'a.py', 'x = 1\n')
    path.write_text('x = 1\ndef broken(:\n', encoding='utf-8', newline='\n')
    result = gate(path, repo, 'edit', *flags)
    assert result.returncode == 1, result.stderr
    assert path.read_text(encoding='utf-8') == 'x = 1\ndef broken(:\n'


@needs_gate
def test_legacy_file_that_does_not_parse_still_gets_checked(repo: Path) -> None:
    path = commit(repo, 'a.py', 'def broken(:\n')
    path.write_text('def broken(:\n\n\nx = 1\n', encoding='utf-8', newline='\n')
    result = gate(path, repo, 'edit', '--fix')
    assert result.returncode in {0, 1}, result.stderr
    assert 'failed' not in result.stderr


@needs_gate
def test_format_check_sees_the_files_own_line_endings(repo: Path) -> None:
    (repo / 'ruff.toml').write_text(RUFF_TOML + '[format]\nline-ending = "lf"\n', encoding='utf-8')
    path = write(repo, 'ok.py', 'import os\n\nprint(os.sep)\n')
    result = gate(path, repo, 'edit', '--format-check')
    assert result.returncode == 0, result.stdout + result.stderr


# --------------------------------------------------------------------------- #
# bin/clean-python: Stop and commit run vulture and ty
# --------------------------------------------------------------------------- #
def clean_python(*args: str, cwd: Path, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    env = {**os.environ, 'TMPDIR': str(cwd)}
    argv = [BASH, str(CLEAN_PYTHON), *args]
    return subprocess.run(
        argv, cwd=cwd, input=stdin, capture_output=True, text=True, encoding='utf-8', check=False, env=env
    )


@needs_gate
def test_edit_passes_but_stop_and_commit_run_ty_and_vulture(repo: Path) -> None:
    path = write(repo, 'core.py', PROJECT_LEGACY)
    assert clean_python(str(path), cwd=repo).returncode == 0
    log = write(repo, 'session.jsonl', json.dumps({'timestamp': '2000-01-01T00:00:00Z'}) + '\n')
    payload = json.dumps({'session_id': 'phase-sess', 'transcript_path': str(log), 'cwd': str(repo)})
    stop = clean_python('--stop-check', cwd=repo, stdin=payload)
    assert stop.returncode == 2
    assert 'GATE003' in stop.stderr
    assert 'GATE004' in stop.stderr
    git(repo, 'add', 'core.py')
    staged = clean_python('--staged', cwd=repo)
    assert staged.returncode == 1
    assert 'GATE003' in staged.stderr
    assert 'GATE004' in staged.stderr


@needs_gate
def test_edit_to_a_legacy_file_passes_through_clean_python(repo: Path) -> None:
    path = commit(repo, 'a.py', LEGACY)
    payload = json.dumps({'tool_input': {'file_path': str(path)}, 'session_id': 'legacy-sess'})
    path.write_text(LEGACY + 'print(sys.path)\n', encoding='utf-8', newline='\n')
    result = clean_python(cwd=repo, stdin=payload)
    assert result.returncode == 0, result.stderr
    assert path.read_text(encoding='utf-8') == LEGACY + 'print(sys.path)\n'


@needs_gate
def test_staged_syntax_error_refuses_the_commit(repo: Path) -> None:
    path = commit(repo, 'c.py', 'x = 1\n')
    path.write_text('x = 1\ndef broken(:\n', encoding='utf-8', newline='\n')
    git(repo, 'add', 'c.py')
    staged = clean_python('--staged', cwd=repo)
    assert staged.returncode == 1, staged.stderr
    assert 'commit refused' in staged.stderr


@needs_gate
def test_stop_runs_ty_and_vulture_on_a_clean_edit_outside_its_repo(
    repo: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    other = tmp_path_factory.mktemp('outside')
    (other / 'ruff.toml').write_text(RUFF_TOML, encoding='utf-8')
    path = write(other, 'core.py', PROJECT_LEGACY)
    edit = json.dumps({'tool_input': {'file_path': str(path)}, 'session_id': 'out-sess'})
    assert clean_python(cwd=repo, stdin=edit).returncode == 0
    log = write(repo, 'session.jsonl', json.dumps({'timestamp': '2000-01-01T00:00:00Z'}) + '\n')
    payload = json.dumps({'session_id': 'out-sess', 'transcript_path': str(log), 'cwd': str(repo)})
    stop = clean_python('--stop-check', cwd=repo, stdin=payload)
    assert stop.returncode == 2
    assert 'GATE003' in stop.stderr
    assert 'GATE004' in stop.stderr
