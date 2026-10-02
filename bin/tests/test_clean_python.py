"""Offline tests for bin/clean-python.

Each test builds a throwaway project (its own ruff.toml, so behaviour does not
depend on the machine's global ruff config) and drives the wrapper exactly the
way a client would: a path argument, or a Claude Code PostToolUse JSON payload
on stdin.

Run with: uv run --with pytest pytest bin/tests/test_clean_python.py
"""

from __future__ import annotations

import json
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


def run_path(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(CLEAN_PYTHON), *args], capture_output=True, text=True, check=False)


def run_stdin(payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(CLEAN_PYTHON)], input=json.dumps(payload), capture_output=True, text=True, check=False)


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
