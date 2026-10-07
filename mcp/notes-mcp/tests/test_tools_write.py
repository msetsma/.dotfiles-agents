"""Tests for the write tools: notes_create / notes_update / notes_append.

Real git and the M4 ``vault`` fixture; only the searcher is faked so the tools'
reindex scheduling can be observed without invoking qmd.

The fixture's seeded notes omit the closing ``---`` fence, so ``frontmatter.parse``
(correctly) rejects them. The update/append tests therefore create their target
through ``notes_create`` first, which emits canonical fenced frontmatter.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

from notes_mcp import frontmatter
from notes_mcp.git import Git
from notes_mcp.index import VaultIndex
from notes_mcp.paths import PathGuard
from notes_mcp.tools import write


GIT = '/usr/bin/git'


class FakeSearch:
    """Records ``schedule_reindex`` calls; never touches qmd."""

    def __init__(self) -> None:
        self.calls = 0

    def schedule_reindex(self, **_kwargs: object) -> None:
        self.calls += 1


def make_ctx(vault) -> SimpleNamespace:
    config = vault.config()
    return SimpleNamespace(
        config=config,
        guard=PathGuard(config.vault_path),
        index=VaultIndex(config),
        git=Git(config),
        search=FakeSearch(),
    )


def _today() -> str:
    return datetime.now(tz=UTC).astimezone().date().isoformat()


def _last_subject(vault) -> str:
    run = subprocess.run
    proc = run([GIT, 'log', '-1', '--format=%s'], cwd=str(vault.agent), capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _create_note(ctx, title: str, body: str) -> str:
    result = write.notes_create(ctx, '11', title, 'note', body)
    assert result.get('ok') is not False, result
    return result['path']


# --------------------------------------------------------------------------- #
# notes_create
# --------------------------------------------------------------------------- #
def test_create_writes_frontmatter_and_commits(vault) -> None:
    ctx = make_ctx(vault)
    rel = '10 Projects/11 Project A/Platform Notes.md'

    result = write.notes_create(ctx, '11', 'Platform Notes', 'note', '# Platform Notes\n\nHello.\n')

    assert result['path'] == rel
    assert result['pending_push'] is False
    assert result['needs_index_update'] is False

    text = (vault.agent / rel).read_text(encoding='utf-8')
    fm, body = frontmatter.parse(text)
    assert fm['type'] == 'note'
    assert fm['status'] == 'active'
    assert fm['created'] == _today()
    assert fm['updated'] == _today()
    assert fm['tags'] == []
    assert fm['qmd']['metadata'] == {'type': 'note', 'status': 'active', 'tags': []}
    assert 'Hello.' in body

    assert _last_subject(vault) == 'agent(notes_create): Platform Notes'
    assert ctx.search.calls == 1


def test_create_accepts_camelcase_aliases(vault) -> None:
    ctx = make_ctx(vault)

    result = write.notes_create(
        ctx, categoryId='11', title='Aliased Note', type='note', body='# Aliased Note\n', tags=['x']
    )

    assert result['path'] == '10 Projects/11 Project A/Aliased Note.md'
    fm, _ = frontmatter.parse((vault.agent / result['path']).read_text(encoding='utf-8'))
    assert fm['tags'] == ['x']


def test_create_unknown_category(vault) -> None:
    ctx = make_ctx(vault)

    result = write.notes_create(ctx, '99', 'Nope', 'note', 'body')

    assert result['ok'] is False
    assert result['error']['code'] == 'CATEGORY_NOT_FOUND'


def test_create_rejects_unknown_type(vault) -> None:
    ctx = make_ctx(vault)

    result = write.notes_create(ctx, '11', 'Weird', 'banana', 'body')

    assert result['ok'] is False
    assert result['error']['code'] == 'INVALID_FRONTMATTER'


def test_create_duplicate_filename(vault) -> None:
    ctx = make_ctx(vault)

    first = write.notes_create(ctx, '11', 'Platform Notes', 'note', 'body')
    assert first.get('ok') is not False

    second = write.notes_create(ctx, '11', 'Platform Notes', 'note', 'body')

    assert second['ok'] is False
    assert second['error']['code'] == 'DUPLICATE_FILENAME'
    assert second['error']['path'] == '10 Projects/11 Project A/Platform Notes.md'


# --------------------------------------------------------------------------- #
# notes_update
# --------------------------------------------------------------------------- #
def test_update_replaces_body_and_bumps_updated(vault) -> None:
    ctx = make_ctx(vault)
    rel = _create_note(ctx, 'Edit Target', '# Edit Target\n\nalpha beta gamma\n')

    result = write.notes_update(ctx, rel, body='# Edit Target\n\nUpdated body.\n')

    assert result['path'] == rel
    fm, body = frontmatter.parse((vault.agent / rel).read_text(encoding='utf-8'))
    assert 'Updated body.' in body
    assert fm['updated'] == _today()
    assert fm['type'] == 'note'
    assert _last_subject(vault) == f'agent(notes_update): {rel}'
    assert ctx.search.calls == 2


def test_update_edits_require_unique_match(vault) -> None:
    ctx = make_ctx(vault)
    rel = _create_note(ctx, 'Edit Target', '# Edit Target\n\nalpha beta gamma\n')

    ambiguous = write.notes_update(ctx, rel, edits=[{'find': 'o', 'replace': '0'}])

    assert ambiguous['ok'] is False
    assert ambiguous['error']['code'] == 'FIND_NOT_UNIQUE'
    assert ambiguous['error']['count'] != 1

    ok = write.notes_update(ctx, rel, edits=[{'find': 'beta', 'replace': 'BETA'}])

    assert ok.get('ok') is not False
    assert 'alpha BETA gamma' in (vault.agent / rel).read_text(encoding='utf-8')


def test_update_frontmatter_merge_reruns_qmd_mirror(vault) -> None:
    ctx = make_ctx(vault)
    rel = _create_note(ctx, 'Edit Target', '# Edit Target\n\nbody\n')

    result = write.notes_update(ctx, rel, frontmatter={'tags': ['renamed'], 'qmd': {'metadata': {'type': 'evil'}}})

    assert result.get('ok') is not False
    fm, _ = frontmatter.parse((vault.agent / rel).read_text(encoding='utf-8'))
    assert fm['tags'] == ['renamed']
    assert fm['qmd']['metadata'] == {'type': 'note', 'status': 'active', 'tags': ['renamed']}


def test_update_missing_note(vault) -> None:
    ctx = make_ctx(vault)

    result = write.notes_update(ctx, '30 Resources/33 Concepts/Missing.md', body='x')

    assert result['ok'] is False
    assert result['error']['code'] == 'NOT_FOUND'


# --------------------------------------------------------------------------- #
# notes_append
# --------------------------------------------------------------------------- #
def test_append_creates_daily_from_template(vault, monkeypatch) -> None:
    ctx = make_ctx(vault)
    monkeypatch.setattr(write, '_today', lambda: '2026-02-02')
    rel = '40 Journal/41 Daily/2026-02-02.md'
    assert not (vault.agent / rel).exists()

    result = write.notes_append(ctx, daily=True, heading='Log', text='- agent appended line')

    assert result['path'] == rel
    text = (vault.agent / rel).read_text(encoding='utf-8')
    assert '{{date' not in text
    assert '2026-02-02' in text
    assert '- agent appended line' in text
    fm, _ = frontmatter.parse(text)
    assert fm['type'] == 'daily'
    assert _last_subject(vault) == f'agent(notes_append): {rel}'
    assert ctx.search.calls == 1


def test_append_to_existing_note_under_heading(vault) -> None:
    ctx = make_ctx(vault)
    rel = _create_note(ctx, 'Append Target', '# Append Target\n\n## Notes\n\nfirst\n')

    result = write.notes_append(ctx, path=rel, heading='Notes', text='appended line')

    assert result['path'] == rel
    text = (vault.agent / rel).read_text(encoding='utf-8')
    assert '## Notes' in text
    assert text.index('first') < text.index('appended line')


def test_append_missing_note(vault) -> None:
    ctx = make_ctx(vault)

    result = write.notes_append(ctx, path='30 Resources/33 Concepts/Missing.md', text='x')

    assert result['ok'] is False
    assert result['error']['code'] == 'NOT_FOUND'


def test_append_without_target(vault) -> None:
    ctx = make_ctx(vault)

    result = write.notes_append(ctx, text='x')

    assert result['ok'] is False
    assert result['error']['code'] == 'PATH_REJECTED'


# --------------------------------------------------------------------------- #
# journal derivation, journal update, duplicate-in-lock (H5 / L3 / M4)
# --------------------------------------------------------------------------- #
def test_create_daily_appends_when_it_exists(vault, monkeypatch) -> None:
    ctx = make_ctx(vault)
    monkeypatch.setattr(write, '_today', lambda: '2026-01-05')
    rel = '40 Journal/41 Daily/2026-01-05.md'
    assert 'agent daily line' not in (vault.agent / rel).read_text(encoding='utf-8')

    result = write.notes_create(ctx, '41', None, 'daily', 'agent daily line')

    assert result.get('ok') is not False, result
    assert result['path'] == rel
    text = (vault.agent / rel).read_text(encoding='utf-8')
    assert 'agent daily line' in text
    fm, _ = frontmatter.parse(text)
    assert fm['type'] == 'daily'
    assert ctx.search.calls == 1


def test_journal_dir_follows_index_path(vault, monkeypatch) -> None:
    ctx = make_ctx(vault)
    monkeypatch.setattr(write, '_today', lambda: '2026-03-03')
    new_dir = '40 Journal/41 Daily Reworked'
    (vault.agent / new_dir).mkdir(parents=True, exist_ok=True)
    index = vault.agent / '00 Meta/00.00 Index.md'
    index.write_text(
        index.read_text(encoding='utf-8').replace(
            '| 41 | Daily | 40 Journal/41 Daily |', f'| 41 | Daily | {new_dir} |'
        ),
        encoding='utf-8',
    )

    result = write.notes_create(ctx, '41', None, 'daily', 'body')

    assert result.get('ok') is not False, result
    assert result['path'] == f'{new_dir}/2026-03-03.md'
    assert (vault.agent / result['path']).is_file()


def test_create_duplicate_creates_nothing(vault) -> None:
    ctx = make_ctx(vault)
    first = write.notes_create(ctx, '11', 'Dupe Target', 'note', 'one')
    assert first.get('ok') is not False, first
    target = vault.agent / first['path']
    content = target.read_text(encoding='utf-8')
    head = _last_subject(vault)

    second = write.notes_create(ctx, '11', 'Dupe Target', 'note', 'two')

    assert second['ok'] is False
    assert second['error']['code'] == 'DUPLICATE_FILENAME'
    assert target.read_text(encoding='utf-8') == content
    assert _last_subject(vault) == head


def test_duplicate_check_ignores_git_dir(vault) -> None:
    ctx = make_ctx(vault)
    (vault.agent / '.git' / 'Decoy.md').write_text('decoy\n', encoding='utf-8')

    result = write.notes_create(ctx, '11', 'Decoy', 'note', 'body')

    assert result.get('ok') is not False, result
    assert result['path'] == '10 Projects/11 Project A/Decoy.md'
