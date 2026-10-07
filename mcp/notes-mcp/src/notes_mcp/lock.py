"""Cooperative cross-process file lock (CONTRACT section 6).

A single sentinel file guards the whole git write protocol. Acquisition uses
``O_CREAT | O_EXCL`` so exactly one process can hold it; contenders poll until
the holder releases or the lock goes stale. A stale lock is broken only when it
is older than ``stale`` *and* its recorded PID is dead (or the sentinel has no
parseable PID), and the break itself is atomic: contenders race an ``os.rename``
and only the winner of that rename proceeds, so two processes cannot both
"break" the same lock.
"""

from __future__ import annotations

import os
import tempfile
import time
from contextlib import suppress
from pathlib import Path

from notes_mcp.errors import CONFLICT, notes_error


POLL_INTERVAL = 0.2


class FileLock:
    """An exclusive lock backed by an ``O_EXCL`` sentinel file."""

    def __init__(self, path: Path, *, timeout: float = 30.0, stale: float = 120.0) -> None:
        self.path = Path(path)
        self.timeout = timeout
        self.stale = stale
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        """Whether this instance currently holds the lock."""
        return self._fd is not None

    def acquire(self) -> None:
        """Take the lock, breaking stale holders and contending until timeout."""
        if self.held:
            return
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if self._break_if_stale():
                    continue
                if time.monotonic() >= deadline:
                    raise notes_error(
                        CONFLICT,
                        f'Could not acquire lock {self.path} within {self.timeout:g}s',
                        'Another notes write is in progress; retry in a moment.',
                        lock=str(self.path),
                    ) from None
                time.sleep(POLL_INTERVAL)
                continue
            os.write(fd, f'{os.getpid()}\n'.encode())
            self._fd = fd
            return

    def _break_if_stale(self) -> bool:
        """Break an old lock whose owner is gone; return whether to retry now."""
        if not self.path.exists():
            return True
        if not self._is_stale(self.path):
            return False
        return self._break_atomically()

    def _is_stale(self, sentinel: Path) -> bool:
        """Whether ``sentinel`` is older than ``stale`` and its owner is gone."""
        try:
            mtime = sentinel.stat().st_mtime
        except FileNotFoundError:
            return False
        if time.time() - mtime <= self.stale:
            return False
        return self._owner_is_dead(sentinel)

    def _owner_is_dead(self, sentinel: Path) -> bool:
        """Whether the sentinel's PID is missing, unparseable, or not running."""
        try:
            raw = sentinel.read_text(encoding='utf-8')
        except OSError:
            return True
        tokens = raw.split()
        if not tokens or not tokens[0].isdigit():
            return True
        try:
            os.kill(int(tokens[0]), 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        return False

    def _break_atomically(self) -> bool:
        """Rename a stale sentinel away; only the rename winner deletes it.

        An ``os.rename`` moves *whatever* is at the path, so without care a
        contender whose staleness check predates another's break could rename
        away a lock that had just been recreated (leaving two holders). A
        sidecar ``O_EXCL`` guard serializes the break and re-checks staleness
        under it, so only a genuinely stale file is ever renamed; the rename
        itself then picks a single winner.
        """
        guard = Path(f'{self.path}.break')
        try:
            gfd = os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False  # another contender is breaking; wait and retry
        try:
            if not self._is_stale(self.path):
                return not self.path.exists()
            handle, name = tempfile.mkstemp(
                dir=str(self.path.parent), prefix=f'.{self.path.name}.break.', suffix='.tmp'
            )
            os.close(handle)
            temp = Path(name)
            try:
                self.path.rename(temp)
            except FileNotFoundError:
                # Someone else won the rename; just retry the exclusive create.
                with suppress(FileNotFoundError):
                    temp.unlink()
                return True
            with suppress(FileNotFoundError):
                temp.unlink()
            return True
        finally:
            os.close(gfd)
            with suppress(FileNotFoundError):
                guard.unlink()

    def release(self) -> None:
        """Release the lock. Idempotent: safe to call more than once."""
        if self._fd is None:
            return
        fd = self._fd
        self._fd = None
        try:
            os.close(fd)
        finally:
            with suppress(FileNotFoundError):
                self.path.unlink()

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
