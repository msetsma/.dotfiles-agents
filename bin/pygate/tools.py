"""Locate and run the external programs the gate needs (git, vulture, ty)."""

import logging
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path


__all__ = ['TOOL_TIMEOUT', 'run_git', 'run_tool', 'tool_argv']

log = logging.getLogger(__name__)

# Per tool. Two tools plus ruff stay well inside the 90 s hook timeout.
TOOL_TIMEOUT = 25
GIT_TIMEOUT = 10


def tool_argv(name: str) -> list[str] | None:
    """The command for `name`: the binary on PATH, else `uvx`, else None."""
    found = shutil.which(name)
    if found:
        return [found]
    uvx = shutil.which('uvx')
    return [uvx, '--quiet', name] if uvx else None


def run_tool(argv: Sequence[str], cwd: Path) -> str | None:
    """Run a checker and return its stdout, or None (with a warning) if it could not run."""
    try:
        done = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=TOOL_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning('python_gate: %s failed to run: %s', argv[0], exc)
        return None
    return done.stdout


def run_git(cwd: Path, *args: str) -> str | None:
    """`git -C cwd <args>`: its stdout, line endings as git wrote them, on success; else None (a missing git also warns)."""
    git = shutil.which('git')
    if git is None:
        log.warning('python_gate: git not found; checks that need it are skipped')
        return None
    try:
        done = subprocess.run([git, '-C', str(cwd), *args], capture_output=True, timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning('python_gate: git failed in %s: %s', cwd, exc)
        return None
    # Decoded by hand: text mode would turn a committed CRLF into LF.
    return done.stdout.decode('utf-8', 'replace') if done.returncode == 0 else None
