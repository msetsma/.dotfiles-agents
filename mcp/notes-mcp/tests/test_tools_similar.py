"""Tests for the similarity tools (CONTRACT section 10 / Phase 3).

Hermetic: ``FakeSearch`` stands in for the qmd-backed searcher and records each
query, so nothing touches the network, the daemon, or the real index. Notes are
written into the ``vault`` fixture directly (its seeded notes are exercised
only indirectly).
"""

from __future__ import annotations

import types
from typing import Any

from notes_mcp.errors import INDEX_UNAVAILABLE, notes_error
from notes_mcp.paths import PathGuard
from notes_mcp.search import SearchHit
from notes_mcp.tools import similar


TAIL = {'commit': None, 'pending_push': False, 'needs_index_update': False}

SOURCE = '30 Resources/33 Concepts/Source.md'
OTHER = '30 Resources/33 Concepts/Other.md'
THIRD = '30 Resources/33 Concepts/Third.md'
EXISTING = '30 Resources/33 Concepts/Existing.md'
QUALIFIED = '30 Resources/33 Concepts/Qualified.md'
FRESH = '30 Resources/33 Concepts/Fresh.md'

NOTE_TEMPLATE = """---
type: {type_}
status: active
created: "2026-01-05"
updated: "2026-01-05"
tags: []
related: []
source: []
qmd:
  metadata:
    type: {type_}
    status: active
    tags: []
---
{body}"""


class FakeSearch:
    """Deterministic stand-in for :class:`~notes_mcp.search.Searcher`."""

    def __init__(self, hits=None, error=None):
        self.hits = list(hits or [])
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def search(self, query, *, limit=10, rerank=True, **loose):
        self.calls.append({'query': query, 'limit': limit, 'rerank': rerank})
        if self.error is not None:
            raise self.error
        return list(self.hits)


def assert_ok(result: dict[str, Any]) -> None:
    assert result['ok'] is True
    for key, value in TAIL.items():
        assert result[key] == value


def make_ctx(vault, *, hits=None, error=None):
    config = vault.config()
    return types.SimpleNamespace(
        config=config, guard=PathGuard(config.vault_path), search=FakeSearch(hits=hits, error=error)
    )


def write_note(ctx, rel: str, body: str = '# Note\n', type_: str = 'concept') -> None:
    path = ctx.config.vault_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(NOTE_TEMPLATE.format(type_=type_, body=body), encoding='utf-8')


def hit(rel: str, score: float = 1.0) -> SearchHit:
    return SearchHit(path=rel, title=rel.split('/')[-1], score=score, snippet=None)


# --------------------------------------------------------------------------- #
# notes_similar
# --------------------------------------------------------------------------- #
def test_similar_drops_source_and_honours_limit(vault):
    ctx = make_ctx(vault, hits=[hit(SOURCE, 0.99), hit(OTHER, 0.8), hit(THIRD, 0.6)])
    write_note(ctx, SOURCE, body='# Source\nAlpha beta gamma.\n')

    result = similar.notes_similar(ctx, path=SOURCE, limit=2)
    assert_ok(result)
    assert [item['path'] for item in result['results']] == [OTHER, THIRD]
    assert result['count'] == 2
    assert result['query'].startswith('Source')
    assert 'Alpha beta gamma' in result['query']
    assert ctx.search.calls == [{'query': result['query'], 'limit': 4, 'rerank': True}]


def test_similar_text_mode_uses_text(vault):
    ctx = make_ctx(vault, hits=[hit(OTHER), hit(THIRD)])

    result = similar.notes_similar(ctx, text='delta epsilon', limit=1)
    assert_ok(result)
    assert result['query'] == 'delta epsilon'
    assert [item['path'] for item in result['results']] == [OTHER]
    assert ctx.search.calls[0]['limit'] == 2


def test_similar_requires_exactly_one_input(vault):
    ctx = make_ctx(vault)

    neither = similar.notes_similar(ctx)
    assert neither['ok'] is False
    assert neither['error']['code'] == 'PATH_REJECTED'

    both = similar.notes_similar(ctx, path=SOURCE, text='text')
    assert both['ok'] is False
    assert both['error']['code'] == 'PATH_REJECTED'
    assert ctx.search.calls == []


def test_similar_wikilinks_are_stripped_from_query(vault):
    ctx = make_ctx(vault)
    write_note(ctx, SOURCE, body='# Source\nSee [[Other|the other note]] and [link](http://x).\n')

    result = similar.notes_similar(ctx, path=SOURCE)
    assert_ok(result)
    assert 'the other note' in result['query']
    assert '[[Other' not in result['query']
    assert 'http://x' not in result['query']


# --------------------------------------------------------------------------- #
# notes_suggest_links
# --------------------------------------------------------------------------- #
def test_suggest_links_excludes_existing(vault):
    ctx = make_ctx(vault, hits=[hit(SOURCE, 0.99), hit(EXISTING, 0.9), hit(QUALIFIED, 0.8), hit(FRESH, 0.7)])
    write_note(ctx, SOURCE, body='# Source\nSee [[Existing]] and [[30 Resources/33 Concepts/Qualified]].\n')

    result = similar.notes_suggest_links(ctx, SOURCE, limit=5)
    assert_ok(result)
    assert result['path'] == SOURCE
    assert result['existing_links'] == 2
    assert [item['path'] for item in result['suggestions']] == [FRESH]
    assert result['count'] == 1
    assert ctx.search.calls[0]['limit'] == 15


def test_suggest_links_oversamples_and_caps(vault):
    ctx = make_ctx(vault)
    write_note(ctx, SOURCE)

    similar.notes_suggest_links(ctx, SOURCE, limit=20)
    assert ctx.search.calls[0]['limit'] == 30  # min(20 * 3, 30)


def test_suggest_links_returns_error_envelope(vault):
    ctx = make_ctx(vault, error=notes_error(INDEX_UNAVAILABLE, 'no qmd', 'install qmd'))
    write_note(ctx, SOURCE)

    result = similar.notes_suggest_links(ctx, SOURCE)
    assert result['ok'] is False
    assert result['error']['code'] == 'INDEX_UNAVAILABLE'
