"""Tests for notes_mcp.paths.PathGuard."""

from __future__ import annotations

import pytest

from notes_mcp.errors import PATH_REJECTED, NotesError
from notes_mcp.paths import PathGuard


@pytest.fixture
def guard(tmp_path) -> PathGuard:
    vault = tmp_path / 'vault'
    vault.mkdir()
    return PathGuard(vault)


def test_resolve_relative_read(guard):
    resolved = guard.resolve('10 Projects/11 Project A/note.md')

    assert resolved == guard.vault / '10 Projects/11 Project A/note.md'


def test_resolve_traversal_rejected(guard):
    with pytest.raises(NotesError) as excinfo:
        guard.resolve('../outside.md')

    assert excinfo.value.code == PATH_REJECTED


def test_resolve_absolute_escape_rejected(guard):
    with pytest.raises(NotesError) as excinfo:
        guard.resolve('/etc/passwd')

    assert excinfo.value.code == PATH_REJECTED


def test_resolve_symlink_escape_rejected(guard, tmp_path):
    target = tmp_path / 'outside'
    target.mkdir()
    (target / 'secret.md').write_text('secret', encoding='utf-8')
    (guard.vault / 'link.md').symlink_to(target / 'secret.md')

    with pytest.raises(NotesError) as excinfo:
        guard.resolve('link.md')

    assert excinfo.value.code == PATH_REJECTED


@pytest.mark.parametrize('protected', ['.git/config', '.obsidian/app.json', '.githooks/pre-commit'])
def test_resolve_protected_dirs_rejected(guard, protected):
    with pytest.raises(NotesError) as excinfo:
        guard.resolve(protected)

    assert excinfo.value.code == PATH_REJECTED


def test_resolve_agents_md_rejected(guard):
    with pytest.raises(NotesError) as excinfo:
        guard.resolve('AGENTS.md')

    assert excinfo.value.code == PATH_REJECTED


def test_non_markdown_read_allowed_but_write_rejected(guard):
    assert guard.resolve('assets/diagram.png') == guard.vault / 'assets/diagram.png'

    with pytest.raises(NotesError) as excinfo:
        guard.resolve('assets/diagram.png', for_write=True)

    assert excinfo.value.code == PATH_REJECTED


def test_meta_write_rejected_except_conflicts(guard):
    with pytest.raises(NotesError) as excinfo:
        guard.resolve('00 Meta/05 Templates/note.md', for_write=True)
    assert excinfo.value.code == PATH_REJECTED

    allowed = guard.resolve('00 Meta/03 Conflicts/2026-10-06-1200 note.md', for_write=True)
    assert allowed == guard.vault / '00 Meta/03 Conflicts/2026-10-06-1200 note.md'


def test_meta_read_allowed(guard):
    resolved = guard.resolve('00 Meta/00.00 Index.md')
    assert resolved == guard.vault / '00 Meta/00.00 Index.md'


def test_relpath(guard):
    path = guard.vault / '20 Areas' / '21 Platform Engineering' / 'note.md'
    assert guard.relpath(path) == '20 Areas/21 Platform Engineering/note.md'


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('a/b:c*d?e"f<g>h|i.md', 'abcdefghi.md'),
        ('  spacing   out  ', 'spacing out'),
        ('keep-dashes_and.dots.md', 'keep-dashes_and.dots.md'),
    ],
)
def test_sanitize_filename(guard, raw, expected):
    assert guard.sanitize_filename(raw) == expected


def test_is_meta_and_is_conflicts(guard):
    assert guard.is_meta('00 Meta/00.00 Index.md') is True
    assert guard.is_meta('10 Projects/note.md') is False
    assert guard.is_conflicts('00 Meta/03 Conflicts/x.md') is True
    assert guard.is_conflicts('00 Meta/00.00 Index.md') is False
