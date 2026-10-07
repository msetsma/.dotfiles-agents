"""Tests for the sentinel-file lock (CONTRACT section 6)."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from notes_mcp.errors import CONFLICT, NotesError
from notes_mcp.lock import FileLock


def _age(path: Path, seconds: float) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


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


def test_stale_lock_without_parseable_pid_is_broken(tmp_path: Path) -> None:
    path = tmp_path / 'guard.lock'
    path.write_text('crashed writer')
    _age(path, 300.0)
    lock = FileLock(path, timeout=1.0, stale=120.0)
    lock.acquire()
    assert lock.held
    lock.release()
    assert not path.exists()


def test_stale_lock_with_dead_pid_is_broken(tmp_path: Path) -> None:
    path = tmp_path / 'guard.lock'
    path.write_text('999999\n')
    _age(path, 300.0)
    lock = FileLock(path, timeout=1.0, stale=120.0)
    lock.acquire()
    assert lock.held
    lock.release()
    assert not path.exists()


def test_live_pid_is_never_broken_by_age(tmp_path: Path) -> None:
    path = tmp_path / 'guard.lock'
    path.write_text(f'{os.getpid()}\n')
    _age(path, 300.0)
    contender = FileLock(path, timeout=0.3, stale=120.0)
    with pytest.raises(NotesError) as excinfo:
        contender.acquire()
    assert excinfo.value.code == CONFLICT
    assert path.exists()


def test_atomic_break_admits_exactly_one_contender(tmp_path: Path) -> None:
    path = tmp_path / 'guard.lock'
    path.write_text('999999\n')
    _age(path, 300.0)

    winners: list[FileLock] = []
    winners_guard = threading.Lock()
    barrier = threading.Barrier(8)

    def contender() -> None:
        lock = FileLock(path, timeout=0.8, stale=120.0)
        barrier.wait()
        try:
            lock.acquire()
        except NotesError:
            return
        with winners_guard:
            winners.append(lock)

    threads = [threading.Thread(target=contender) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(winners) == 1
    winners[0].release()
    assert not path.exists()
