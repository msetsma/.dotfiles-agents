"""Tests for the shared atomic writer (CONTRACT section 3)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from notes_mcp.atomic import atomic_write


def _temp_leftovers(directory: Path) -> list[Path]:
    return [path for path in directory.iterdir() if path.name.endswith('.tmp')]


def test_atomic_write_creates_parent_dirs_and_content(tmp_path: Path) -> None:
    target = tmp_path / 'nested' / 'deep' / 'note.md'
    atomic_write(target, 'hello\n')
    assert target.read_text(encoding='utf-8') == 'hello\n'


def test_atomic_write_leaves_no_temp_on_success(tmp_path: Path) -> None:
    target = tmp_path / 'note.md'
    atomic_write(target, 'body\n')
    assert target.read_text(encoding='utf-8') == 'body\n'
    assert _temp_leftovers(tmp_path) == []


def test_atomic_write_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / 'note.md'
    target.write_text('old\n', encoding='utf-8')
    atomic_write(target, 'new\n')
    assert target.read_text(encoding='utf-8') == 'new\n'


def test_failed_atomic_write_keeps_original_and_leaves_no_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / 'note.md'
    target.write_text('original\n', encoding='utf-8')

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError('replace failed')

    monkeypatch.setattr(os, 'replace', boom)
    with pytest.raises(OSError, match='replace failed'):
        atomic_write(target, 'replacement\n')

    assert target.read_text(encoding='utf-8') == 'original\n'
    assert _temp_leftovers(tmp_path) == []
