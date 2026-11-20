"""Python files changed during a Claude Code session, for the Stop hook.

The PostToolUse hook only sees Write/Edit/MultiEdit. Files changed through a
shell command (heredoc, sed, a formatter) never reach it, so the Stop hook also
checks every Python file in the session's git repository that is dirty and was
modified after the session started. The start time is the first timestamp in
the session transcript.
"""

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from pygate.tools import run_git


__all__ = ['changed_python_files', 'session_start']

PYTHON_SUFFIXES = frozenset({'.py', '.pyi'})


def _timestamps(lines: Iterator[str]) -> Iterator[float]:
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        stamp = record.get('timestamp') if isinstance(record, dict) else None
        if isinstance(stamp, str):
            try:
                yield datetime.fromisoformat(stamp).timestamp()
            except ValueError:
                continue


def session_start(transcript: Path) -> float | None:
    """Epoch seconds of the transcript's first timestamped record, or None."""
    try:
        with transcript.open(encoding='utf-8', errors='replace') as handle:
            return next(_timestamps(handle), None)
    except OSError:
        return None


def _dirty_paths(porcelain: str) -> Iterator[str]:
    # `status --porcelain -z`: "XY path\0", plus "origpath\0" after a rename.
    entries = iter(porcelain.split('\0'))
    for entry in entries:
        if len(entry) < len('XY p'):
            continue
        if entry[0] in 'RC':
            next(entries, None)
        yield entry[3:]


def changed_python_files(cwd: Path, since: float) -> list[Path]:
    """Dirty Python files in cwd's git repository modified at or after `since`."""
    top = run_git(cwd, 'rev-parse', '--show-toplevel')
    status = run_git(cwd, 'status', '--porcelain', '-z', '--untracked-files=all')
    if top is None or status is None:
        return []
    root = Path(top.strip())
    candidates = (root / rel for rel in _dirty_paths(status))
    return [
        path
        for path in candidates
        if path.suffix in PYTHON_SUFFIXES and path.is_file() and path.stat().st_mtime >= since
    ]
