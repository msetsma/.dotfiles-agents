"""Run ruff for one file: lint a text, auto-fix and format the file, check formatting.

bin/clean-python resolves the binary and the config; ruff runs from the file's
package directory (cwd) so a user-level config resolves `src` the same way a
plain `ruff check` there would. Without a config, `--isolated` keeps a stray
config in a parent directory from leaking in. Lint always passes
`--ignore-noqa`: a suppression is not a fix.

Texts go through stdin with `--stdin-filename`, so the committed baseline is
linted with exactly the settings of the working-tree file. stdin is sent as
UTF-8 bytes: text mode would turn every newline into CRLF on Windows and break
a `line-ending = "lf"` format check.

`ruff format` cannot format a file that does not parse (exit 2). That is not a
gate failure: the file counts as not formatted, nothing is rewritten, and the
lint step reports the syntax error as a finding.
"""

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pygate.errors import GateError
from pygate.findings import Finding
from pygate.tools import TOOL_TIMEOUT


__all__ = ['Ruff', 'RuffError', 'fix_file', 'format_file', 'is_formatted', 'lint']

# ruff exits 0 (clean) or 1 (findings / would reformat); 2 means it failed.
RUFF_FAILED = 2


class RuffError(GateError):
    """ruff could not run, failed, or printed output the gate cannot parse."""


@dataclass(frozen=True)
class Ruff:
    """How to run ruff for one file."""

    binary: str
    cwd: Path
    config: Path | None


def _relative(ruff: Ruff, path: Path) -> str:
    return str(path.relative_to(ruff.cwd)) if path.is_relative_to(ruff.cwd) else str(path)


@dataclass(frozen=True)
class Outcome:
    """A finished ruff run: its exit status and decoded output."""

    returncode: int
    stdout: str
    stderr: str


def _run(ruff: Ruff, args: Sequence[str], stdin: str | None = None, *, may_fail: bool = False) -> Outcome:
    """Run ruff; raise RuffError when it cannot run, or when it fails (exit 2) unless `may_fail`."""
    config = ['--config', str(ruff.config)] if ruff.config else ['--isolated']
    try:
        done = subprocess.run(
            [ruff.binary, *args, *config],
            cwd=ruff.cwd,
            input=None if stdin is None else stdin.encode('utf-8'),
            capture_output=True,
            timeout=TOOL_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuffError(f'ruff failed to run: {exc}') from exc
    outcome = Outcome(done.returncode, done.stdout.decode('utf-8', 'replace'), done.stderr.decode('utf-8', 'replace'))
    if outcome.returncode >= RUFF_FAILED and not may_fail:
        raise RuffError(f'ruff {args[0]} failed: {outcome.stderr.strip()}')
    return outcome


def _finding(path: Path, item: object) -> Finding:
    if not isinstance(item, dict):
        raise RuffError(f'unexpected ruff output: {item!r}')
    location = item.get('location')
    code = item.get('code') or item.get('name')
    message = item.get('message')
    if not (isinstance(location, dict) and isinstance(code, str) and isinstance(message, str)):
        raise RuffError(f'unexpected ruff output: {item!r}')
    return Finding(path, int(location.get('row', 1)), int(location.get('column', 1)), code, message)


def lint(ruff: Ruff, path: Path, text: str) -> list[Finding]:
    """ruff's findings for `text` as the content of `path`, suppressions ignored."""
    args = ['check', '--ignore-noqa', '--output-format', 'json', '--stdin-filename', _relative(ruff, path), '-']
    done = _run(ruff, args, text)
    try:
        items = json.loads(done.stdout or '[]')
    except json.JSONDecodeError as exc:
        raise RuffError(f'unexpected ruff output: {done.stdout[:200]!r}') from exc
    if not isinstance(items, list):
        raise RuffError(f'unexpected ruff output: {items!r}')
    return [_finding(path, item) for item in items]


def is_formatted(ruff: Ruff, path: Path, text: str) -> bool:
    """True when `ruff format` would leave `text` (as `path`) unchanged; False when it cannot parse it."""
    args = ['format', '--check', '--quiet', '--stdin-filename', _relative(ruff, path), '-']
    done = _run(ruff, args, text, may_fail=True)
    return done.returncode == 0


def fix_file(ruff: Ruff, path: Path) -> None:
    """Apply ruff's auto-fixes to the file in place."""
    _run(ruff, ['check', '--fix', '--quiet', _relative(ruff, path)])


def format_file(ruff: Ruff, path: Path) -> None:
    """Format the file in place; a file that does not parse is left as it is."""
    _run(ruff, ['format', '--quiet', _relative(ruff, path)], may_fail=True)
