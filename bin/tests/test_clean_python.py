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

# The gate is a bash script; run it through bash so Windows (which cannot exec
# a shebang script directly) behaves like macOS and Linux.
BASH = shutil.which('bash')
pytestmark = pytest.mark.skipif(BASH is None, reason='clean-python needs bash on PATH')

# Only tests that reach a ruff run need ruff; the resolution probes and the
# early-exit paths run on bash alone.
needs_ruff = pytest.mark.skipif(shutil.which('ruff') is None, reason='this test needs ruff on PATH')


def test_script_uses_lf_line_endings() -> None:
    # A CR after the shebang breaks the gate on macOS and Linux (`bash\r`).
    assert b'\r' not in CLEAN_PYTHON.read_bytes()


def gate(*args: str) -> list[str]:
    return [str(BASH), str(CLEAN_PYTHON), *args]


def native(path: str) -> str:
    # Git Bash on Windows prints POSIX paths (/tmp/...); map them back so they
    # compare equal to Python's native paths. A no-op where cygpath is absent.
    cygpath = shutil.which('cygpath')
    if not path or cygpath is None:
        return path
    return subprocess.run([cygpath, '-w', path], capture_output=True, text=True, check=True).stdout.strip()


# Ruff's preview mode reports rule names instead of codes; accept either.
RULE_NAMES = {'F401': 'unused-import', 'PLR1702': 'too-many-nested-blocks'}


def reports(stderr: str, code: str) -> bool:
    return code in stderr or RULE_NAMES[code] in stderr


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
    return subprocess.run(gate(*args), capture_output=True, text=True, check=False, env=env)


def run_stdin(payload: dict[str, object], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(gate(), input=json.dumps(payload), capture_output=True, text=True, check=False, env=env)


def run_stop(payload: dict[str, object], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        gate('--stop-check'), input=json.dumps(payload), capture_output=True, text=True, check=False, env=env
    )


def tmp_env(project: Path) -> dict[str, str]:
    # Scoped state dir: the gate writes pending-<session> under $TMPDIR.
    return {**os.environ, 'TMPDIR': str(project)}


def pending_file(project: Path, session: str) -> Path:
    return project / 'clean-python' / f'pending-{session}'


@needs_ruff
def test_clean_file_passes(project: Path) -> None:
    path = write(project, 'clean.py', 'print(1)\n')
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


@needs_ruff
def test_fixable_is_rewritten_and_passes(project: Path) -> None:
    path = write(project, 'fmt.py', "print({'a':1,'b':2})\n")
    before = path.read_text()
    result = run_path(str(path))
    assert result.returncode == 0, result.stderr
    assert path.read_text() != before
    assert "'a': 1" in path.read_text()


@needs_ruff
def test_unfixable_is_handed_back(project: Path) -> None:
    path = write(project, 'unused.py', 'import os\n')
    result = run_path(str(path))
    assert result.returncode == 2
    assert reports(result.stderr, 'F401')


@needs_ruff
def test_suppression_is_ignored(project: Path) -> None:
    path = write(project, 'noqa.py', 'import os  # noqa: F401\n')
    result = run_path(str(path))
    assert result.returncode == 2
    assert reports(result.stderr, 'F401')


@needs_ruff
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
    assert reports(result.stderr, 'PLR1702')


@needs_ruff
def test_claude_stdin_payload(project: Path) -> None:
    path = write(project, 'stdin_case.py', 'import os\n')
    result = run_stdin({'tool_input': {'file_path': str(path)}, 'tool_response': {}})
    assert result.returncode == 2
    assert reports(result.stderr, 'F401')


def test_claude_stdin_payload_for_non_python_is_noop(project: Path) -> None:
    path = write(project, 'stdin_notes.txt', 'import os\n')
    result = run_stdin({'tool_input': {'file_path': str(path)}})
    assert result.returncode == 0, result.stderr


@needs_ruff
def test_tool_response_fallback(project: Path) -> None:
    path = write(project, 'fallback.py', 'import os\n')
    result = run_stdin({'tool_input': {}, 'tool_response': {'filePath': str(path)}})
    assert result.returncode == 2
    assert reports(result.stderr, 'F401')


# --------------------------------------------------------------------------- #
# enforcement: pending state + Stop hook
# --------------------------------------------------------------------------- #
@needs_ruff
def test_posttooluse_records_pending_on_failure(project: Path) -> None:
    session = 'sess-record'
    path = write(project, 'unfixed.py', 'import os\n')
    result = run_stdin({'tool_input': {'file_path': str(path)}, 'session_id': session}, env=tmp_env(project))
    assert result.returncode == 2
    state = pending_file(project, session)
    assert state.exists()
    # The gate records the physical path (pwd -P), e.g. /private/var on macOS.
    recorded = [Path(native(line.split('\t', 1)[1])).resolve() for line in state.read_text().splitlines()]
    assert path.resolve() in recorded


@needs_ruff
def test_posttooluse_records_a_clean_file_for_the_stop_check(project: Path) -> None:
    # The edit phase skips vulture and ty; Stop runs them, then drops the clean file.
    session = 'sess-ok'
    path = write(project, 'fixable2.py', "print({'a':1})\n")
    result = run_stdin({'tool_input': {'file_path': str(path)}, 'session_id': session}, env=tmp_env(project))
    assert result.returncode == 0, result.stderr
    assert pending_file(project, session).exists()
    assert run_stop({'session_id': session}, env=tmp_env(project)).returncode == 0
    assert not pending_file(project, session).exists()


@needs_ruff
def test_posttooluse_without_session_records_nothing(project: Path) -> None:
    path = write(project, 'nosess.py', 'import os\n')
    result = run_stdin({'tool_input': {'file_path': str(path)}})
    assert result.returncode == 2
    assert not (project / 'clean-python').exists()


@needs_ruff
def test_stop_blocks_while_residue_remains(project: Path) -> None:
    session = 'sess-stop'
    path = write(project, 'broken.py', 'import os\n')
    run_stdin({'tool_input': {'file_path': str(path)}, 'session_id': session}, env=tmp_env(project))
    result = run_stop({'session_id': session}, env=tmp_env(project))
    assert result.returncode == 2
    assert reports(result.stderr, 'F401')


@needs_ruff
def test_stop_releases_and_clears_after_fix(project: Path) -> None:
    session = 'sess-fix'
    path = write(project, 'broken2.py', 'import os\n')
    run_stdin({'tool_input': {'file_path': str(path)}, 'session_id': session}, env=tmp_env(project))
    path.write_text('print(1)\n')
    result = run_stop({'session_id': session}, env=tmp_env(project))
    assert result.returncode == 0, result.stderr
    assert not pending_file(project, session).exists()


@needs_ruff
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


@needs_ruff
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
    result = subprocess.run(gate('--config-for', str(path)), capture_output=True, text=True, check=False, env=env)
    assert result.returncode == 0, result.stderr
    return native(result.stdout.strip())


def ruff_for(path: Path) -> str:
    result = subprocess.run(gate('--ruff-for', str(path)), capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return native(result.stdout.strip())


def isolated_env(root: Path) -> dict[str, str]:
    # No global ruff config: XDG_CONFIG_HOME and APPDATA point at an empty tree.
    return {**os.environ, 'XDG_CONFIG_HOME': str(root), 'APPDATA': str(root)}


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


@needs_ruff
def test_ruff_runs_from_the_package_dir(git_project: Path, tmp_path: Path) -> None:
    # A user-level config resolves `src` against the cwd, so the gate must run
    # from pkg/ (nearest pyproject.toml) to see src/mypkg as first-party.
    global_cfg = tmp_path / 'ruff' / 'ruff.toml'
    global_cfg.parent.mkdir(parents=True)
    global_cfg.write_text('src = ["src", "."]\n\n[lint]\nselect = ["I"]\n')
    pkg = git_project / 'pkg'
    (pkg / 'src' / 'mypkg').mkdir(parents=True)
    (pkg / 'src' / 'mypkg' / '__init__.py').write_text('')
    (pkg / 'pyproject.toml').write_text('[project]\nname = "mypkg"\n')
    (pkg / 'tests').mkdir()
    source = 'import os\n\nimport pytest\n\nimport mypkg\n\nprint(os, pytest, mypkg)\n'
    path = write(pkg / 'tests', 'test_a.py', source)
    result = run_path(str(path), env={**isolated_env(tmp_path), 'TMPDIR': str(tmp_path)})
    assert result.returncode == 0, result.stderr
    assert path.read_text() == source


@pytest.mark.parametrize('venv_ruff', ['bin/ruff', 'Scripts/ruff.exe'])
def test_project_venv_ruff_is_preferred(git_project: Path, venv_ruff: str) -> None:
    # POSIX venvs put ruff in .venv/bin, Windows venvs in .venv/Scripts.
    ruff = git_project / '.venv' / venv_ruff
    ruff.parent.mkdir(parents=True)
    ruff.write_text('#!/bin/sh\n')
    ruff.chmod(0o755)
    path = write(git_project, 'a.py', 'x = 1\n')
    assert ruff_for(path) == str(ruff)


def test_no_config_anywhere_resolves_to_nothing(git_project: Path, tmp_path: Path) -> None:
    path = write(git_project, 'a.py', 'x = 1\n')
    assert config_for(path, env=isolated_env(tmp_path)) == ''


# --------------------------------------------------------------------------- #
# off-topic waivers
# --------------------------------------------------------------------------- #
@needs_ruff
def test_waive_suppresses_residue(project: Path) -> None:
    path = write(project, 'unused.py', 'import os\n')
    assert run_path(str(path)).returncode == 2
    assert run_path('--waive', str(path), env=tmp_env(project)).returncode == 0
    assert run_path(str(path), env=tmp_env(project)).returncode == 0


@needs_ruff
def test_waiver_matches_a_report_of_several_lines(project: Path) -> None:
    # The edit share of the waiver (the full report minus ty) must hash the same
    # bytes as the edit report (no CR left in a multi-line report on Windows).
    path = write(project, 'two.py', "import os\nimport sys\n\nx: int = 'one'\nprint(x)\n")
    assert run_path(str(path), env=tmp_env(project)).returncode == 2
    assert run_path('--waive', str(path), env=tmp_env(project)).returncode == 0
    assert run_path(str(path), env=tmp_env(project)).returncode == 0


@needs_ruff
def test_waiver_matches_across_path_spellings(project: Path) -> None:
    path = write(project, 'unused.py', 'import os\n')
    # Waive by absolute path, then address the same file relatively: the waiver
    # must still match because the gate normalises the path before hashing.
    assert run_path('--waive', str(path), env=tmp_env(project)).returncode == 0
    result = subprocess.run(
        gate('unused.py'), cwd=project, capture_output=True, text=True, check=False, env=tmp_env(project)
    )
    assert result.returncode == 0, result.stderr


@needs_ruff
def test_waive_on_clean_file_is_a_noop(project: Path) -> None:
    path = write(project, 'clean.py', 'print(1)\n')
    assert run_path('--waive', str(path), env=tmp_env(project)).returncode == 0
    state = project / 'clean-python'
    assert not state.exists() or not list(state.glob('waived-*'))


@needs_ruff
def test_waiver_expires_when_findings_change(project: Path) -> None:
    path = write(project, 'unused.py', 'import os\n')
    run_path('--waive', str(path), env=tmp_env(project))
    assert run_path(str(path), env=tmp_env(project)).returncode == 0
    path.write_text('import sys\n')
    assert run_path(str(path), env=tmp_env(project)).returncode == 2


@needs_ruff
def test_waive_releases_stop(project: Path) -> None:
    session = 'sess-waive'
    path = write(project, 'broken.py', 'import os\n')
    run_stdin({'tool_input': {'file_path': str(path)}, 'session_id': session}, env=tmp_env(project))
    run_path('--waive', str(path), env=tmp_env(project))
    result = run_stop({'session_id': session}, env=tmp_env(project))
    assert result.returncode == 0, result.stderr
    assert not pending_file(project, session).exists()


@needs_ruff
def test_fixing_clears_stale_waiver(project: Path) -> None:
    path = write(project, 'unused.py', 'import os\n')
    run_path('--waive', str(path), env=tmp_env(project))
    path.write_text('print(1)\n')
    assert run_path(str(path), env=tmp_env(project)).returncode == 0
    state = project / 'clean-python'
    assert not list(state.glob('waived-*'))


# --------------------------------------------------------------------------- #
# no working python: the gate stays non-blocking but says it is skipped
# --------------------------------------------------------------------------- #
def broken_python_env(project: Path) -> dict[str, str]:
    # Shadow uv, python3 and python with stubs that fail, like the Windows Store stub.
    stubs = project / 'stub-bin'
    stubs.mkdir()
    for name in ('uv', 'python3', 'python'):
        stub = stubs / name
        stub.write_text('#!/bin/sh\nexit 1\n', newline='\n')
        stub.chmod(0o755)
    return {**tmp_env(project), 'PATH': f'{stubs}{os.pathsep}{os.environ["PATH"]}'}


def test_posttooluse_without_python_warns_and_passes(project: Path) -> None:
    path = write(project, 'nopy.py', 'import os\n')
    result = run_stdin({'tool_input': {'file_path': str(path)}, 'session_id': 's'}, env=broken_python_env(project))
    assert result.returncode == 0, result.stderr
    assert result.stderr.strip() == 'clean-python: no working python found; skipping the gate'


def test_stop_without_python_warns_and_passes(project: Path) -> None:
    result = run_stop({'session_id': 's'}, env=broken_python_env(project))
    assert result.returncode == 0, result.stderr
    assert result.stderr.strip() == 'clean-python: no working python found; skipping the gate'
