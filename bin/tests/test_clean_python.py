"""Offline tests for bin/clean-python.

Each test builds a throwaway project (its own ruff.toml, so behaviour does not
depend on the machine's global ruff config) and drives the wrapper exactly the
way a client would: a path argument, or a Claude Code PostToolUse JSON payload
on stdin.

Run with: uv run --with pytest pytest bin/tests/test_clean_python.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest


CLEAN_PYTHON = (Path(__file__).resolve().parents[1] / 'clean-python').resolve()

# A small, deterministic ruff config: F401 is deliberately unfixable so we can
# prove the "hand it back" path, and PLR1702 is enabled (preview, explicit) so
# we can prove the nesting gate.
RUFF_TOML = textwrap.dedent("""
    [lint]
    preview = true
    explicit-preview-rules = true
    select = ["F", "E", "W", "I", "C90", "PLR", "PLR1702"]
    ignore = ["PLR0915", "PLR0913", "E501"]
    fixable = ["ALL"]
    unfixable = ["F401"]

    [lint.mccabe]
    max-complexity = 10

    [lint.pylint]
    max-nested-blocks = 5

    [format]
    quote-style = "single"
""").lstrip()


@pytest.fixture
def project() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix='clean-python-') as tmp:
        root = Path(tmp)
        (root / 'ruff.toml').write_text(RUFF_TOML)
        yield root


def write(root: Path, name: str, content: str) -> Path:
    path = root / name
    path.write_text(textwrap.dedent(content).lstrip())
    return path


def run_path(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(CLEAN_PYTHON), *args], capture_output=True, text=True, check=False, env=env)


def run_stdin(payload: dict[str, object], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CLEAN_PYTHON)], input=json.dumps(payload), capture_output=True, text=True, check=False, env=env
    )


def run_stop(payload: dict[str, object], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CLEAN_PYTHON), '--stop-check'],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def tmp_env(project: Path) -> dict[str, str]:
    # Scoped state dir: the gate writes pending-<session> under $TMPDIR.
    return {**os.environ, 'TMPDIR': str(project)}


def pending_file(project: Path, session: str) -> Path:
    return project / 'clean-python' / f'pending-{session}'


def test_clean_file_passes(project: Path) -> None:
    path = write(project, 'clean.py', 'x = 1\n')
    result = run_path(str(path))
    assert result.returncode == 0, result.stderr


def test_non_python_is_a_noop(project: Path) -> None:
    path = write(project, 'notes.txt', 'import os\nnot python {{{')
    result = run_path(str(path))
    assert result.returncode == 0, result.stderr
    assert path.read_text() == 'import os\nnot python {{{'


def test_missing_file_is_a_noop(project: Path) -> None:
    result = run_path(str(project / 'absent.py'))
    assert result.returncode == 0, result.stderr


def test_fixable_is_rewritten_and_passes(project: Path) -> None:
    path = write(project, 'fmt.py', "x = {'a':1,'b':2}\n")
    before = path.read_text()
    result = run_path(str(path))
    assert result.returncode == 0, result.stderr
    assert path.read_text() != before
    assert "'a': 1" in path.read_text()


def test_unfixable_is_handed_back(project: Path) -> None:
    path = write(project, 'unused.py', 'import os\n')
    result = run_path(str(path))
    assert result.returncode == 2
    assert 'F401' in result.stderr


def test_suppression_is_ignored(project: Path) -> None:
    path = write(project, 'noqa.py', 'import os  # noqa: F401\n')
    result = run_path(str(path))
    assert result.returncode == 2
    assert 'F401' in result.stderr


def test_nesting_is_enforced(project: Path) -> None:
    path = write(
        project,
        'deep.py',
        """
        def deep(a, b, c, d, e, f):
            if a:
                if b:
                    if c:
                        if d:
                            if e:
                                if f:
                                    return 1
            return 0
        """,
    )
    result = run_path(str(path))
    assert result.returncode == 2
    assert 'PLR1702' in result.stderr


def test_claude_stdin_payload(project: Path) -> None:
    path = write(project, 'stdin_case.py', 'import os\n')
    result = run_stdin({'tool_input': {'file_path': str(path)}, 'tool_response': {}})
    assert result.returncode == 2
    assert 'F401' in result.stderr


def test_claude_stdin_payload_for_non_python_is_noop(project: Path) -> None:
    path = write(project, 'stdin_notes.txt', 'import os\n')
    result = run_stdin({'tool_input': {'file_path': str(path)}})
    assert result.returncode == 0, result.stderr


def test_tool_response_fallback(project: Path) -> None:
    path = write(project, 'fallback.py', 'import os\n')
    result = run_stdin({'tool_input': {}, 'tool_response': {'filePath': str(path)}})
    assert result.returncode == 2
    assert 'F401' in result.stderr


# --------------------------------------------------------------------------- #
# enforcement: pending state + Stop hook
# --------------------------------------------------------------------------- #
def test_posttooluse_records_pending_on_failure(project: Path) -> None:
    session = 'sess-record'
    path = write(project, 'unfixed.py', 'import os\n')
    result = run_stdin({'tool_input': {'file_path': str(path)}, 'session_id': session}, env=tmp_env(project))
    assert result.returncode == 2
    state = pending_file(project, session)
    assert state.exists()
    assert str(path) in state.read_text()


def test_posttooluse_records_nothing_when_fixable(project: Path) -> None:
    session = 'sess-ok'
    path = write(project, 'fixable2.py', "x = {'a':1}\n")
    result = run_stdin({'tool_input': {'file_path': str(path)}, 'session_id': session}, env=tmp_env(project))
    assert result.returncode == 0, result.stderr
    assert not pending_file(project, session).exists()


def test_posttooluse_without_session_records_nothing(project: Path) -> None:
    path = write(project, 'nosess.py', 'import os\n')
    result = run_stdin({'tool_input': {'file_path': str(path)}})
    assert result.returncode == 2
    assert not (project / 'clean-python').exists()


def test_stop_blocks_while_residue_remains(project: Path) -> None:
    session = 'sess-stop'
    path = write(project, 'broken.py', 'import os\n')
    run_stdin({'tool_input': {'file_path': str(path)}, 'session_id': session}, env=tmp_env(project))
    result = run_stop({'session_id': session}, env=tmp_env(project))
    assert result.returncode == 2
    assert 'F401' in result.stderr


def test_stop_releases_and_clears_after_fix(project: Path) -> None:
    session = 'sess-fix'
    path = write(project, 'broken2.py', 'import os\n')
    run_stdin({'tool_input': {'file_path': str(path)}, 'session_id': session}, env=tmp_env(project))
    path.write_text('x = 1\n')
    result = run_stop({'session_id': session}, env=tmp_env(project))
    assert result.returncode == 0, result.stderr
    assert not pending_file(project, session).exists()


def test_stop_gives_up_after_max_blocks(project: Path) -> None:
    session = 'sess-giveup'
    path = write(project, 'eternal.py', 'import os\n')
    run_stdin({'tool_input': {'file_path': str(path)}, 'session_id': session}, env=tmp_env(project))
    codes = [run_stop({'session_id': session}, env=tmp_env(project)).returncode for _ in range(5)]
    assert codes[:3] == [2, 2, 2]
    assert codes[-1] == 0
    assert not pending_file(project, session).exists()


def test_stop_without_session_is_noop(project: Path) -> None:
    result = run_stop({}, env=tmp_env(project))
    assert result.returncode == 0, result.stderr


def test_stop_drops_deleted_file(project: Path) -> None:
    session = 'sess-gone'
    path = write(project, 'gone.py', 'import os\n')
    run_stdin({'tool_input': {'file_path': str(path)}, 'session_id': session}, env=tmp_env(project))
    path.unlink()
    result = run_stop({'session_id': session}, env=tmp_env(project))
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------- #
# config resolution: project root first, global fallback, no leakage
# --------------------------------------------------------------------------- #
@pytest.fixture
def git_project() -> Iterator[Path]:
    """A throwaway git repo with no ruff config of its own."""
    with tempfile.TemporaryDirectory(prefix='clean-python-git-') as tmp:
        root = Path(tmp)
        git = shutil.which('git')
        assert git is not None
        subprocess.run([git, 'init', '-q'], cwd=root, check=True)
        yield root.resolve()


def config_for(path: Path, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        [str(CLEAN_PYTHON), '--config-for', str(path)], capture_output=True, text=True, check=False, env=env
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def isolated_env(root: Path) -> dict[str, str]:
    # No global ruff config: XDG_CONFIG_HOME points at an empty tree.
    return {**os.environ, 'XDG_CONFIG_HOME': str(root)}


def test_project_config_is_the_git_root(project: Path) -> None:
    path = write(project, 'a.py', 'x = 1\n')
    assert config_for(path) == str(project / 'ruff.toml')


def test_git_root_ruff_toml_is_used(git_project: Path) -> None:
    (git_project / 'ruff.toml').write_text(RUFF_TOML)
    path = write(git_project, 'a.py', 'x = 1\n')
    assert config_for(path) == str(git_project / 'ruff.toml')


def test_git_root_pyproject_with_tool_ruff_is_used(git_project: Path) -> None:
    (git_project / 'pyproject.toml').write_text('[project]\nname = "x"\n\n[tool.ruff]\nline-length = 88\n')
    path = write(git_project, 'a.py', 'x = 1\n')
    assert config_for(path) == str(git_project / 'pyproject.toml')


def test_pyproject_without_tool_ruff_falls_back_to_global(git_project: Path, tmp_path: Path) -> None:
    (git_project / 'pyproject.toml').write_text('[project]\nname = "x"\n')
    global_cfg = tmp_path / 'ruff' / 'ruff.toml'
    global_cfg.parent.mkdir(parents=True)
    global_cfg.write_text(RUFF_TOML)
    path = write(git_project, 'a.py', 'x = 1\n')
    assert config_for(path, env=isolated_env(tmp_path)) == str(global_cfg)


def test_nested_config_is_ignored(git_project: Path, tmp_path: Path) -> None:
    sub = git_project / 'pkg'
    sub.mkdir()
    (sub / 'ruff.toml').write_text(RUFF_TOML)
    path = write(sub, 'a.py', 'x = 1\n')
    # Only the git root is considered, so a config in a subdirectory is not used.
    assert config_for(path, env=isolated_env(tmp_path)) == ''


def test_no_config_anywhere_resolves_to_nothing(git_project: Path, tmp_path: Path) -> None:
    path = write(git_project, 'a.py', 'x = 1\n')
    assert config_for(path, env=isolated_env(tmp_path)) == ''


# --------------------------------------------------------------------------- #
# off-topic waivers
# --------------------------------------------------------------------------- #
def test_waive_suppresses_residue(project: Path) -> None:
    path = write(project, 'unused.py', 'import os\n')
    assert run_path(str(path)).returncode == 2
    assert run_path('--waive', str(path), env=tmp_env(project)).returncode == 0
    assert run_path(str(path), env=tmp_env(project)).returncode == 0


def test_waiver_matches_across_path_spellings(project: Path) -> None:
    path = write(project, 'unused.py', 'import os\n')
    # Waive by absolute path, then address the same file relatively: the waiver
    # must still match because the gate normalises the path before hashing.
    assert run_path('--waive', str(path), env=tmp_env(project)).returncode == 0
    result = subprocess.run(
        [str(CLEAN_PYTHON), 'unused.py'], cwd=project, capture_output=True, text=True, check=False, env=tmp_env(project)
    )
    assert result.returncode == 0, result.stderr


def test_waive_on_clean_file_is_a_noop(project: Path) -> None:
    path = write(project, 'clean.py', 'x = 1\n')
    assert run_path('--waive', str(path), env=tmp_env(project)).returncode == 0
    state = project / 'clean-python'
    assert not state.exists() or not list(state.glob('waived-*'))


def test_waiver_expires_when_findings_change(project: Path) -> None:
    path = write(project, 'unused.py', 'import os\n')
    run_path('--waive', str(path), env=tmp_env(project))
    assert run_path(str(path), env=tmp_env(project)).returncode == 0
    path.write_text('import sys\n')
    assert run_path(str(path), env=tmp_env(project)).returncode == 2


def test_waive_releases_stop(project: Path) -> None:
    session = 'sess-waive'
    path = write(project, 'broken.py', 'import os\n')
    run_stdin({'tool_input': {'file_path': str(path)}, 'session_id': session}, env=tmp_env(project))
    run_path('--waive', str(path), env=tmp_env(project))
    result = run_stop({'session_id': session}, env=tmp_env(project))
    assert result.returncode == 0, result.stderr
    assert not pending_file(project, session).exists()


def test_fixing_clears_stale_waiver(project: Path) -> None:
    path = write(project, 'unused.py', 'import os\n')
    run_path('--waive', str(path), env=tmp_env(project))
    path.write_text('x = 1\n')
    assert run_path(str(path), env=tmp_env(project)).returncode == 0
    state = project / 'clean-python'
    assert not list(state.glob('waived-*'))
