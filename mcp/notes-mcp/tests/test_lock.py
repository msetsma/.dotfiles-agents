"""Tests for the sentinel-file lock (CONTRACT section 6)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from notes_mcp.errors import CONFLICT, NotesError
from notes_mcp.lock import FileLock


def test_context_manager_creates_and_removes(tmp_path: Path) -> None:
    path = tmp_path / 'guard.lock'
    lock = FileLock(path)
    with lock:
        assert path.exists()
        assert lock.held
    assert not path.exists()
    assert not lock.held


def test_release_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / 'guard.lock'
    lock = FileLock(path)
    lock.acquire()
    lock.release()
    lock.release()
    assert not path.exists()


def test_second_acquirer_times_out(tmp_path: Path) -> None:
    path = tmp_path / 'guard.lock'
    held = FileLock(path)
    held.acquire()
    try:
        contender = FileLock(path, timeout=0.3)
        with pytest.raises(NotesError) as excinfo:
            contender.acquire()
    finally:
        held.release()
    assert excinfo.value.code == CONFLICT


def test_lock_can_be_reacquired_after_release(tmp_path: Path) -> None:
    path = tmp_path / 'guard.lock'
    first = FileLock(path)
    first.acquire()
    first.release()
    with FileLock(path, timeout=1.0) as second:
        assert second.held
    assert not path.exists()


def test_fresh_foreign_lock_is_respected(tmp_path: Path) -> None:
    path = tmp_path / 'guard.lock'
    path.write_text('held by someone else')
    contender = FileLock(path, timeout=0.3, stale=120.0)
    with pytest.raises(NotesError):
        contender.acquire()
    assert path.exists()


def test_stale_lock_is_broken(tmp_path: Path) -> None:
    path = tmp_path / 'guard.lock'
    path.write_text('crashed writer')
    old = time.time() - 300.0
    os.utime(path, (old, old))
    lock = FileLock(path, timeout=1.0, stale=120.0)
    lock.acquire()
    assert lock.held
    lock.release()
    assert not path.exists()
