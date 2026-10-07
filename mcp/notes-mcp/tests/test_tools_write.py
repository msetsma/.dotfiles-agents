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
from notes_mcp.errors import INDEX_UNAVAILABLE, NotesError
from notes_mcp.git import Git
from notes_mcp.index import VaultIndex
from notes_mcp.paths import PathGuard
from notes_mcp.search import SearchHit
from notes_mcp.tools import write


GIT = '/usr/bin/git'

WATCH_TEMPLATE = """---
type: watch
status: active
created: "{{date:YYYY-MM-DD}}"
updated: "{{date:YYYY-MM-DD}}"
tags: []
related: []
source: []
qmd:
  metadata:
    type: watch
    status: active
    tags: []
---

# {{title}}

## Watching

## Signals
"""


class FakeSearch:
    """Records ``schedule_reindex`` calls; returns canned search hits."""

    def __init__(self, hits: list[SearchHit] | None = None) -> None:
        self.calls = 0
        self.hits = list(hits or [])
        self.queries: list[str] = []

    def schedule_reindex(self, **_kwargs: object) -> None:
        self.calls += 1

    def search(
        self, query: str, *, collection: str | None = None, limit: int = 10, rerank: bool = True
    ) -> list[SearchHit]:
        self.queries.append(query)
        return self.hits[:limit]


def make_ctx(vault, **env_overrides) -> SimpleNamespace:
    config = vault.config(**env_overrides)
    return SimpleNamespace(
        config=config,
        guard=PathGuard(config.vault_path),
        index=VaultIndex(config),
        git=Git(config),
        search=FakeSearch(),
    )


def _today() -> str:
    return datetime.now(tz=UTC).astimezone().date().isoformat()


def _epoch(value: str) -> int:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


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
    assert fm['qmd']['metadata'] == {
        'type': 'note',
        'status': 'active',
        'tags': [],
        'created_ts': _epoch(_today()),
        'updated_ts': _epoch(_today()),
    }
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
# template seeding by type
# --------------------------------------------------------------------------- #
def _write_watch_template(root) -> None:
    path = root / '00 Meta/01 Templates/Watch.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(WATCH_TEMPLATE, encoding='utf-8')


def test_create_watch_note_is_allowed(vault) -> None:
    ctx = make_ctx(vault)
    _write_watch_template(vault.agent)

    result = write.notes_create(ctx, '11', 'Vendor Watch', 'watch', 'Caller note.')

    assert result.get('ok') is not False, result
    rel = '10 Projects/11 Project A/Vendor Watch.md'
    assert result['path'] == rel
    fm, body = frontmatter.parse((vault.agent / rel).read_text(encoding='utf-8'))
    assert fm['type'] == 'watch'
    assert '# Vendor Watch' in body
    assert '## Watching' in body
    assert 'Caller note.' in body


def test_create_project_seeds_template_then_appends_body(vault) -> None:
    ctx = make_ctx(vault)

    result = write.notes_create(ctx, '11', 'My Project', 'project', 'Extra line.')

    assert result.get('ok') is not False, result
    fm, body = frontmatter.parse((vault.agent / result['path']).read_text(encoding='utf-8'))
    assert fm['type'] == 'project'
    assert '# My Project' in body
    assert '## Outcome' in body
    assert body.index('## Outcome') < body.index('Extra line.')


def test_create_decision_seeds_template(vault) -> None:
    ctx = make_ctx(vault)

    result = write.notes_create(ctx, '11', 'Pick One', 'decision', 'Rationale.')

    assert result.get('ok') is not False, result
    _, body = frontmatter.parse((vault.agent / result['path']).read_text(encoding='utf-8'))
    assert '## Decision' in body
    assert '## Consequences' in body
    assert 'Rationale.' in body


def test_create_missing_template_falls_back_to_body(vault) -> None:
    ctx = make_ctx(vault)
    (vault.agent / '00 Meta/01 Templates/Decision.md').unlink()

    result = write.notes_create(ctx, '11', 'No Template', 'decision', 'Only caller body.')

    assert result.get('ok') is not False, result
    _, body = frontmatter.parse((vault.agent / result['path']).read_text(encoding='utf-8'))
    assert body == 'Only caller body.'
    assert '## Decision' not in body


def test_create_untemplated_type_keeps_caller_body(vault) -> None:
    ctx = make_ctx(vault)

    result = write.notes_create(ctx, '11', 'Plain Note', 'note', 'Just this.')

    assert result.get('ok') is not False, result
    _, body = frontmatter.parse((vault.agent / result['path']).read_text(encoding='utf-8'))
    assert body == 'Just this.'


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
    assert fm['qmd']['metadata'] == {
        'type': 'note',
        'status': 'active',
        'tags': ['renamed'],
        'created_ts': _epoch(_today()),
        'updated_ts': _epoch(_today()),
    }


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


def test_daily_meeting_dirs_follow_index_paths(vault, monkeypatch) -> None:
    ctx = make_ctx(vault)
    daily_dir = '40 Journal/41 Daily Reworked'
    meetings_dir = '40 Journal/43 Meetings Reworked'
    (vault.agent / daily_dir).mkdir(parents=True, exist_ok=True)
    (vault.agent / meetings_dir).mkdir(parents=True, exist_ok=True)
    index = vault.agent / '00 Meta/00.00 Index.md'
    text = index.read_text(encoding='utf-8')
    text = text.replace('| 41 | Daily | 40 Journal/41 Daily |', f'| 41 | Daily | {daily_dir} |')
    text = text.replace('| 43 | Meetings | 40 Journal/43 Meetings |', f'| 43 | Meetings | {meetings_dir} |')
    index.write_text(text, encoding='utf-8')
    monkeypatch.setattr(write, '_today', lambda: '2026-05-05')

    daily = write.notes_create(ctx, '41', None, 'daily', 'body')
    meeting = write.notes_create(ctx, '43', 'Sync', 'meeting', 'body')

    assert daily.get('ok') is not False, daily
    assert daily['path'] == f'{daily_dir}/2026-05-05.md'
    assert meeting.get('ok') is not False, meeting
    assert meeting['path'] == f'{meetings_dir}/2026-05-05 Sync.md'
    assert (vault.agent / daily['path']).is_file()
    assert (vault.agent / meeting['path']).is_file()


def test_daily_and_meeting_dirs_respect_env_override(vault, monkeypatch) -> None:
    daily_dir = '40 Journal/41 Daily Override'
    meetings_dir = '40 Journal/43 Meetings Override'
    ctx = make_ctx(vault, NOTES_DAILY_DIR=daily_dir, NOTES_MEETINGS_DIR=meetings_dir)
    monkeypatch.setattr(write, '_today', lambda: '2026-04-04')
    # The Index category must still validate, even though the override wins.
    (vault.agent / '40 Journal/43 Meetings').mkdir(parents=True, exist_ok=True)

    daily = write.notes_create(ctx, '41', None, 'daily', 'body')
    meeting = write.notes_create(ctx, '43', 'Standup', 'meeting', 'body')

    assert daily.get('ok') is not False, daily
    assert daily['path'] == f'{daily_dir}/2026-04-04.md'
    assert meeting.get('ok') is not False, meeting
    assert meeting['path'] == f'{meetings_dir}/2026-04-04 Standup.md'
    assert (vault.agent / daily['path']).is_file()
    assert (vault.agent / meeting['path']).is_file()


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


# --------------------------------------------------------------------------- #
# suggestion mode (Phase 3, W2)
# --------------------------------------------------------------------------- #
def test_update_suggest_mode_via_env_wraps_edit(vault) -> None:
    ctx = make_ctx(vault, NOTES_WRITE_MODE='suggest')
    rel = _create_note(ctx, 'Edit Target', '# Edit Target\n\nalpha beta gamma\n')

    result = write.notes_update(ctx, rel, edits=[{'find': 'beta', 'replace': 'BETA'}])

    assert result.get('ok') is not False, result
    text = (vault.agent / rel).read_text(encoding='utf-8')
    assert '{~~beta~>BETA~~}' in text
    assert 'alpha beta gamma' not in text
    assert 'alpha BETA gamma' not in text


def test_update_suggest_mode_explicit_argument(vault) -> None:
    ctx = make_ctx(vault)
    rel = _create_note(ctx, 'Edit Target', '# Edit Target\n\nalpha beta gamma\n')

    result = write.notes_update(ctx, rel, edits=[{'find': 'beta', 'replace': 'BETA'}], mode='suggest')

    assert result.get('ok') is not False, result
    assert '{~~beta~>BETA~~}' in (vault.agent / rel).read_text(encoding='utf-8')


def test_update_suggest_mode_still_requires_unique_match(vault) -> None:
    ctx = make_ctx(vault)
    rel = _create_note(ctx, 'Edit Target', '# Edit Target\n\nalpha beta gamma\n')

    result = write.notes_update(ctx, rel, edits=[{'find': 'a', 'replace': 'A'}], mode='suggest')

    assert result['ok'] is False
    assert result['error']['code'] == 'FIND_NOT_UNIQUE'


def test_update_direct_mode_is_unchanged(vault) -> None:
    ctx = make_ctx(vault)
    rel = _create_note(ctx, 'Edit Target', '# Edit Target\n\nalpha beta gamma\n')

    result = write.notes_update(ctx, rel, edits=[{'find': 'beta', 'replace': 'BETA'}], mode='direct')

    assert result.get('ok') is not False, result
    text = (vault.agent / rel).read_text(encoding='utf-8')
    assert 'alpha BETA gamma' in text
    assert '{~~' not in text


def test_update_bogus_mode_falls_back_to_direct(vault) -> None:
    ctx = make_ctx(vault)
    rel = _create_note(ctx, 'Edit Target', '# Edit Target\n\nalpha beta gamma\n')

    result = write.notes_update(ctx, rel, edits=[{'find': 'beta', 'replace': 'BETA'}], mode='bogus')

    assert result.get('ok') is not False, result
    assert 'alpha BETA gamma' in (vault.agent / rel).read_text(encoding='utf-8')


def test_update_suggest_mode_body_applies_directly(vault) -> None:
    ctx = make_ctx(vault)
    rel = _create_note(ctx, 'Edit Target', '# Edit Target\n\nalpha beta gamma\n')

    result = write.notes_update(ctx, rel, body='# Edit Target\n\nReplaced.\n', mode='suggest')

    assert result.get('ok') is not False, result
    text = (vault.agent / rel).read_text(encoding='utf-8')
    assert 'Replaced.' in text
    assert '{~~' not in text


def test_append_suggest_mode_wraps_text(vault) -> None:
    ctx = make_ctx(vault)
    rel = _create_note(ctx, 'Append Target', '# Append Target\n\n## Notes\n\nfirst\n')

    result = write.notes_append(ctx, path=rel, heading='Notes', text='appended line', mode='suggest')

    assert result.get('ok') is not False, result
    text = (vault.agent / rel).read_text(encoding='utf-8')
    assert '{++appended line++}' in text
    assert text.index('first') < text.index('{++appended line++}')


def test_append_suggest_mode_via_env(vault) -> None:
    ctx = make_ctx(vault, NOTES_WRITE_MODE='suggest')
    rel = _create_note(ctx, 'Append Target', '# Append Target\n\n## Notes\n\nfirst\n')

    result = write.notes_append(ctx, path=rel, heading='Notes', text='a line')

    assert result.get('ok') is not False, result
    assert '{++a line++}' in (vault.agent / rel).read_text(encoding='utf-8')


def test_append_direct_mode_unchanged(vault) -> None:
    ctx = make_ctx(vault)
    rel = _create_note(ctx, 'Append Target', '# Append Target\n\n## Notes\n\nfirst\n')

    result = write.notes_append(ctx, path=rel, heading='Notes', text='plain line', mode='direct')

    assert result.get('ok') is not False, result
    text = (vault.agent / rel).read_text(encoding='utf-8')
    assert 'plain line' in text
    assert '{++' not in text


# --------------------------------------------------------------------------- #
# create returns best-effort 'similar' suggestions
# --------------------------------------------------------------------------- #
def test_create_attaches_similar_hits(vault) -> None:
    ctx = make_ctx(vault)
    own = '10 Projects/11 Project A/Platform Notes.md'
    ctx.search.hits = [
        SearchHit(own, 'Platform Notes', 0.99, None),
        SearchHit('20 Areas/21 Platform Engineering/21 Platform Engineering.md', 'Platform Engineering', 0.88, None),
        SearchHit('30 Resources/33 Concepts/Johnny Decimal.md', 'Johnny Decimal', 0.77, None),
        SearchHit('30 Resources/32 Tools/qmd.md', 'qmd', 0.66, None),
        SearchHit('90 Archive/Old.md', 'Old', 0.55, None),
    ]

    result = write.notes_create(ctx, '11', 'Platform Notes', 'note', 'body')

    assert result.get('ok') is not False, result
    assert result['path'] == own
    assert len(result['similar']) == 3
    assert all(item['path'] != own for item in result['similar'])
    assert result['similar'][0] == {
        'path': '20 Areas/21 Platform Engineering/21 Platform Engineering.md',
        'title': 'Platform Engineering',
        'score': 0.88,
    }
    assert ctx.search.queries == ['Platform Notes']


def test_create_similar_empty_without_search(vault) -> None:
    ctx = make_ctx(vault)
    ctx.search = SimpleNamespace(schedule_reindex=lambda **_kwargs: None)

    result = write.notes_create(ctx, '11', 'No Searcher', 'note', 'body')

    assert result.get('ok') is not False, result
    assert result['similar'] == []


def test_create_succeeds_when_similar_search_fails(vault, monkeypatch) -> None:
    ctx = make_ctx(vault)

    def _boom(*_args: object, **_kwargs: object) -> list[SearchHit]:
        raise NotesError(INDEX_UNAVAILABLE, 'qmd down', 'Retry later.')

    monkeypatch.setattr(ctx.search, 'search', _boom)

    result = write.notes_create(ctx, '11', 'Resilient Note', 'note', 'body')

    assert result.get('ok') is not False, result
    assert result['similar'] == []
