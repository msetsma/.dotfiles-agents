"""Locate and run the external checkers (vulture, ty)."""

import logging
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path


__all__ = ['TOOL_TIMEOUT', 'run_tool', 'tool_argv']

log = logging.getLogger(__name__)

# Per tool. Two tools plus ruff stay well inside the 90 s hook timeout.
TOOL_TIMEOUT = 25


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
