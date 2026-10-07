"""Tests for the read tools (CONTRACT section 10).

The M4 fixture's ``frontmatter()`` helper omits the closing ``---`` fence for its
seeded notes, so any note written through it fails ``frontmatter.parse``. These
tests therefore build a few valid notes inside the fixture vault themselves;
everything else (index, listing, git history, status) still exercises the
seeded fixture directly.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from notes_mcp.errors import INDEX_UNAVAILABLE, notes_error
from notes_mcp.git import Git
from notes_mcp.index import VaultIndex
from notes_mcp.paths import PathGuard
from notes_mcp.search import Searcher, SearchHit
from notes_mcp.tools import read


TAIL = {'commit': None, 'pending_push': False, 'needs_index_update': False}

NOTE_TEMPLATE = """---
type: {type_}
status: {status}
created: "2026-01-05"
updated: "2026-01-05"
tags: {tags}
related: []
source: []
qmd:
  metadata:
    type: {type_}
    status: {status}
    tags: {tags}
---
{body}"""

CONCEPT_NOTE = '30 Resources/33 Concepts/Readable.md'
MEETING_NOTE = '10 Projects/11 Project A/Agent Meeting.md'
AGENT_NOTE = '40 Journal/41 Daily/2026-01-06.md'


def assert_ok(result: dict[str, Any]) -> None:
    assert result['ok'] is True
    for key, value in TAIL.items():
        assert result[key] == value


def make_ctx(vault):
    config = vault.config()
    return types.SimpleNamespace(
        config=config,
        guard=PathGuard(config.vault_path),
        index=VaultIndex(config),
        search=Searcher(config),
        git=Git(config),
    )


def write_note(
    ctx, rel: str, *, type_: str, status: str = 'active', body: str = '# Note\n', tags: list[str] | None = None
) -> None:
    path = ctx.config.vault_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(NOTE_TEMPLATE.format(type_=type_, status=status, body=body, tags=tags or []), encoding='utf-8')


class FakeSearcher:
    """Deterministic stand-in for Searcher that records calls."""

    def __init__(self, hits=None, error=None, health=None):
        self.hits = list(hits or [])
        self.error = error
        self.health_result = (
            health
            if health is not None
            else {'available': False, 'version': None, 'collection': 'notes', 'indexed_at': None, 'models': {}}
        )
        self.calls: list[dict[str, Any]] = []

    def search(self, query, *, limit=10, rerank=True, **loose):
        self.calls.append({'query': query, 'limit': limit, 'rerank': rerank})
        if self.error is not None:
            raise self.error
        return list(self.hits)

    def health(self):
        return self.health_result


def _hits() -> list[SearchHit]:
    return [
        SearchHit(path=MEETING_NOTE, title='Meeting', score=1.0, snippet='a'),
        SearchHit(path=CONCEPT_NOTE, title='Readable', score=0.5, snippet='b'),
    ]


def _search_ctx(vault, hits=None, error=None):
    ctx = make_ctx(vault)
    ctx.search = FakeSearcher(hits=_hits() if hits is None else hits, error=error)
    return ctx


# --------------------------------------------------------------------------- #
# notes_index
# --------------------------------------------------------------------------- #
def test_index_lists_seeded_categories(vault):
    result = read.notes_index(make_ctx(vault))
    assert_ok(result)
    ids = {category['id'] for category in result['categories']}
    assert {'00', '11', '21'} <= ids
    assert result['index_path'] == '00 Meta/00.00 Index.md'
    project = next(category for category in result['categories'] if category['id'] == '11')
    assert project['path'] == '10 Projects/11 Project A'


# --------------------------------------------------------------------------- #
# notes_read
# --------------------------------------------------------------------------- #
def test_read_returns_frontmatter_and_body(vault):
    ctx = make_ctx(vault)
    write_note(ctx, CONCEPT_NOTE, type_='concept', body='# Readable\n\nA concept note.\n')

    result = read.notes_read(ctx, CONCEPT_NOTE)
    assert_ok(result)
    assert result['path'] == CONCEPT_NOTE
    assert result['frontmatter']['type'] == 'concept'
    assert result['frontmatter']['qmd']['metadata']['type'] == 'concept'
    assert result['body'].startswith('# Readable')
    assert 'A concept note.' in result['body']


def test_read_slices_body_lines(vault):
    ctx = make_ctx(vault)
    write_note(ctx, CONCEPT_NOTE, type_='concept', body='# Readable\n\nA concept note.\n')
    lines = read.notes_read(ctx, CONCEPT_NOTE)['body'].split('\n')

    single = read.notes_read(ctx, CONCEPT_NOTE, from_line=2, max_lines=1)
    assert single['body'] == lines[1]

    tail = read.notes_read(ctx, CONCEPT_NOTE, from_line=2)
    assert tail['body'] == '\n'.join(lines[1:])


def test_read_missing_returns_not_found(vault):
    result = read.notes_read(make_ctx(vault), '30 Resources/33 Concepts/Missing.md')
    assert result['ok'] is False
    assert result['error']['code'] == 'NOT_FOUND'


def test_read_rejects_protected_path(vault):
    result = read.notes_read(make_ctx(vault), '.git/config')
    assert result['ok'] is False
    assert result['error']['code'] == 'PATH_REJECTED'


# --------------------------------------------------------------------------- #
# notes_list
# --------------------------------------------------------------------------- #
def test_list_one_level(vault):
    ctx = make_ctx(vault)

    root = read.notes_list(ctx)
    assert_ok(root)
    by_name = {entry['name']: entry for entry in root['entries']}
    assert {'00 Meta', '10 Projects', '20 Areas', '30 Resources', '40 Journal'} <= set(by_name)
    assert by_name['10 Projects']['kind'] == 'dir'
    assert by_name['10 Projects']['layer'] == '10 Projects'
    assert '.git' not in by_name

    kinds = [entry['kind'] for entry in root['entries']]
    assert kinds == sorted(kinds, key=lambda kind: kind != 'dir')

    nested = read.notes_list(ctx, '30 Resources')
    assert_ok(nested)
    assert [entry['name'] for entry in nested['entries']] == ['33 Concepts']
    assert nested['entries'][0]['layer'] == '30 Resources'

    files = read.notes_list(ctx, '30 Resources/33 Concepts')
    assert_ok(files)
    assert [entry['name'] for entry in files['entries']] == ['Johnny Decimal.md']
    assert files['entries'][0]['kind'] == 'file'
    assert files['entries'][0]['layer'] == '30 Resources'


def test_list_missing_dir_returns_not_found(vault):
    result = read.notes_list(make_ctx(vault), '30 Resources/Nope')
    assert result['ok'] is False
    assert result['error']['code'] == 'NOT_FOUND'


# --------------------------------------------------------------------------- #
# notes_recent
# --------------------------------------------------------------------------- #
def test_recent_returns_agent_commits_and_filters(vault):
    ctx = make_ctx(vault)
    write_note(ctx, AGENT_NOTE, type_='daily', body='# 2026-01-06\n')
    ctx.git.add([AGENT_NOTE])
    sha = ctx.git.commit(tool='test', summary='add daily note', session='s', paths=[AGENT_NOTE])

    agent = read.notes_recent(ctx, since='7d', author='agent')
    assert_ok(agent)
    assert agent['since'] == '7d'
    assert any(entry['path'] == AGENT_NOTE and entry['sha'] == sha for entry in agent['notes'])
    assert all(entry['author'].startswith('agent:') for entry in agent['notes'])

    human = read.notes_recent(ctx, since='7d', author='human')
    assert_ok(human)
    assert all(not entry['author'].startswith('agent:') for entry in human['notes'])
    assert all(entry['path'] != AGENT_NOTE for entry in human['notes'])

    everyone = read.notes_recent(ctx, since='7d', author='any')
    assert {entry['path'] for entry in everyone['notes']} >= {AGENT_NOTE}


# --------------------------------------------------------------------------- #
# notes_status
# --------------------------------------------------------------------------- #
def test_status_includes_branch(vault):
    ctx = make_ctx(vault)
    ctx.search = FakeSearcher()
    result = read.notes_status(ctx)
    assert_ok(result)
    assert result['branch'] == 'main'
    assert result['vault_path'] == str(vault.agent)
    assert result['qmd'] == ctx.search.health_result


def test_status_includes_resolved_layout(vault):
    ctx = make_ctx(vault)
    ctx.search = FakeSearcher()

    result = read.notes_status(ctx)

    assert_ok(result)
    assert set(result['layout']) == {'daily', 'weekly', 'meetings', 'templates', 'attachments'}
    assert result['layout']['daily'] == '40 Journal/41 Daily'
    assert result['layout']['weekly'] == '40 Journal/42 Weekly'
    assert result['layout']['meetings'] == '40 Journal/43 Meetings'
    assert result['layout']['templates'] == '00 Meta/01 Templates'
    assert result['layout']['attachments'] == '00 Meta/02 Attachments'


# --------------------------------------------------------------------------- #
# notes_search
# --------------------------------------------------------------------------- #
def test_search_filters_by_category_id(vault):
    ctx = _search_ctx(vault)
    result = read.notes_search(ctx, 'notes', category_id='11')
    assert_ok(result)
    assert result['count'] == 1
    assert result['hits'][0]['path'] == MEETING_NOTE
    assert ctx.search.calls[0]['limit'] == 50
    assert ctx.search.calls[0]['rerank'] is True


def test_search_category_id_not_found(vault):
    ctx = _search_ctx(vault)
    result = read.notes_search(ctx, 'q', category_id='99')
    assert result['ok'] is False
    assert result['error']['code'] == 'CATEGORY_NOT_FOUND'


def test_search_filters_by_type_and_status(vault):
    ctx = _search_ctx(vault)
    write_note(ctx, CONCEPT_NOTE, type_='concept')
    write_note(ctx, MEETING_NOTE, type_='meeting', status='active')

    concept = read.notes_search(ctx, 'q', type_='concept')
    assert [hit['path'] for hit in concept['hits']] == [CONCEPT_NOTE]

    meeting = read.notes_search(ctx, 'q', type_='meeting', status='active')
    assert [hit['path'] for hit in meeting['hits']] == [MEETING_NOTE]

    missing = read.notes_search(ctx, 'q', type_='decision')
    assert missing['count'] == 0


def test_search_type_alias_and_area(vault):
    ctx = _search_ctx(vault)
    write_note(ctx, CONCEPT_NOTE, type_='concept')

    aliased = read.notes_search(ctx, 'q', type='concept')
    assert [hit['path'] for hit in aliased['hits']] == [CONCEPT_NOTE]

    area = read.notes_search(ctx, 'q', area='30 Resources')
    assert [hit['path'] for hit in area['hits']] == [CONCEPT_NOTE]


def test_search_forwards_limit_and_rerank(vault):
    ctx = _search_ctx(vault, hits=[])
    result = read.notes_search(ctx, 'q', limit=3, rerank=False)
    assert_ok(result)
    assert result['count'] == 0
    assert ctx.search.calls == [{'query': 'q', 'limit': 3, 'rerank': False}]


def test_search_error_returns_envelope(vault):
    ctx = _search_ctx(vault, error=notes_error(INDEX_UNAVAILABLE, 'no qmd', 'install qmd'))
    result = read.notes_search(ctx, 'q')
    assert result['ok'] is False
    assert result['error']['code'] == 'INDEX_UNAVAILABLE'


def test_search_rejects_unknown_kwargs(vault):
    ctx = _search_ctx(vault)
    with pytest.raises(TypeError, match='unexpected keyword'):
        read.notes_search(ctx, 'q', bogus=1)


def test_search_oversamples_then_truncates(vault):
    ctx = make_ctx(vault)
    paths = [f'30 Resources/33 Concepts/Note {index}.md' for index in range(5)]
    for rel in paths:
        write_note(ctx, rel, type_='concept')
    ctx.search = FakeSearcher(hits=[SearchHit(path=rel, title=rel, score=1.0, snippet=None) for rel in paths])

    result = read.notes_search(ctx, 'q', type_='concept', limit=2)
    assert_ok(result)
    assert ctx.search.calls[0]['limit'] == 10  # min(max(2 * 5, 2), 50)
    assert result['count'] == 2
    assert [hit['path'] for hit in result['hits']] == paths[:2]


def test_search_filters_by_tags_any_match(vault):
    ctx = _search_ctx(vault)
    write_note(ctx, CONCEPT_NOTE, type_='concept', tags=['ml', 'reading'])
    write_note(ctx, MEETING_NOTE, type_='meeting', tags=['work'])

    single = read.notes_search(ctx, 'q', tags=['ml'])
    assert [hit['path'] for hit in single['hits']] == [CONCEPT_NOTE]

    any_tag = read.notes_search(ctx, 'q', tags=['work', 'ml'])
    assert {hit['path'] for hit in any_tag['hits']} == {CONCEPT_NOTE, MEETING_NOTE}

    none = read.notes_search(ctx, 'q', tags=['missing'])
    assert none['count'] == 0
