"""Cooperative cross-process file lock (CONTRACT section 6).

A single sentinel file guards the whole git write protocol. Acquisition uses
``O_CREAT | O_EXCL`` so exactly one process can hold it; contenders poll until
the holder releases or the lock goes stale. Stale locks (older than ``stale``
seconds) are broken so a crashed writer cannot wedge the vault forever.
"""

from __future__ import annotations

import os
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
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return True
        if time.time() - mtime <= self.stale:
            return False
        with suppress(FileNotFoundError):
            self.path.unlink()
        return True

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
