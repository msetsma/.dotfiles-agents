"""Tests for the capture tools: notes_capture / notes_triage.

The fixture Index has no ``05 Inbox`` row and no Inbox template, so the capture
tests scaffold both into the agent clone and commit them, leaving the tree clean
for the write protocol. Triage never touches qmd: ``ctx.search`` is a stub.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from notes_mcp import frontmatter
from notes_mcp.git import Git
from notes_mcp.index import VaultIndex
from notes_mcp.paths import PathGuard
from notes_mcp.search import SearchHit
from notes_mcp.tools import capture


GIT = '/usr/bin/git'

INBOX_INDEX_ROW = '| 05 | Inbox | 05 Inbox | Quick capture. |\n'

INBOX_TEMPLATE = """---
type: note
status: draft
created: "{{date:YYYY-MM-DD}}"
updated: "{{date:YYYY-MM-DD}}"
tags: []
related: []
source: []
qmd:
  metadata:
    type: note
    status: draft
    tags: []
---

# {{title}}

## Capture

## Triage

- [ ] Move to the right category
"""

INBOX_HUB = """---
type: hub
status: active
created: "2026-01-05"
updated: "2026-01-05"
tags: []
related: []
source: []
scope: Quick capture.
qmd:
  metadata:
    type: hub
    status: active
    tags: []
---

# 05 Inbox

## Capture

## Triage
"""


class FakeSearch:
    """Records ``schedule_reindex`` calls; never touches qmd."""

    def __init__(self) -> None:
        self.calls = 0

    def schedule_reindex(self, **_kwargs: object) -> None:
        self.calls += 1


class StubSearch:
    """Returns canned hits and records the queries it saw."""

    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = list(hits)
        self.calls: list[str] = []

    def search(self, query, *, limit=10, rerank=True, **_loose):
        self.calls.append(query)
        return list(self.hits)

    def schedule_reindex(self, **_kwargs: object) -> None:
        return None


def make_ctx(vault) -> SimpleNamespace:
    config = vault.config()
    return SimpleNamespace(
        config=config,
        guard=PathGuard(config.vault_path),
        index=VaultIndex(config),
        git=Git(config),
        search=FakeSearch(),
    )


def _run(args: list[str], cwd) -> subprocess.CompletedProcess:
    run = subprocess.run
    return run(args, cwd=str(cwd), capture_output=True, text=True, check=True)


def _head(vault) -> str:
    return _run([GIT, 'rev-parse', 'HEAD'], vault.agent).stdout.strip()


def scaffold_inbox(vault) -> None:
    """Add the 05 Inbox row, hub, and template to the agent clone, then commit."""
    index = vault.agent / '00 Meta/00.00 Index.md'
    index.write_text(index.read_text(encoding='utf-8') + INBOX_INDEX_ROW, encoding='utf-8')

    (vault.agent / '05 Inbox').mkdir(exist_ok=True)
    (vault.agent / '05 Inbox/05 Inbox.md').write_text(INBOX_HUB, encoding='utf-8')
    (vault.agent / '00 Meta/01 Templates/Inbox.md').write_text(INBOX_TEMPLATE, encoding='utf-8')

    _run([GIT, 'add', '-A'], vault.agent)
    _run([GIT, 'commit', '-m', 'test: scaffold inbox'], vault.agent)


# --------------------------------------------------------------------------- #
# notes_capture
# --------------------------------------------------------------------------- #
def test_capture_creates_inbox_note(vault) -> None:
    scaffold_inbox(vault)
    ctx = make_ctx(vault)

    result = capture.notes_capture(ctx, '# Fix the thing\n\nDetails here.\n')

    assert result['ok'] is True, result
    assert result['category_id'] == '05'
    assert result['path'] == '05 Inbox/Fix the thing.md'
    assert result['pending_push'] is False
    assert result['needs_index_update'] is False

    text = (vault.agent / result['path']).read_text(encoding='utf-8')
    fm, body = frontmatter.parse(text)
    assert fm['type'] == 'note'
    assert fm['status'] == 'draft'
    assert fm['tags'] == []
    metadata = fm['qmd']['metadata']
    assert metadata['type'] == 'note'
    assert metadata['status'] == 'draft'
    assert metadata['tags'] == []

    assert 'Details here.' in body
    assert body.index('## Capture') < body.index('Details here.') < body.index('## Triage')
    assert '- [ ] Move to the right category' in body
    assert ctx.search.calls == 1


def test_capture_merges_tags_and_source(vault) -> None:
    scaffold_inbox(vault)
    ctx = make_ctx(vault)

    result = capture.notes_capture(ctx, 'A thought', title='Tagged', tags=['inbox'], source=['cli'])

    fm, _ = frontmatter.parse((vault.agent / result['path']).read_text(encoding='utf-8'))
    assert result['path'] == '05 Inbox/Tagged.md'
    assert fm['tags'] == ['inbox']
    assert fm['source'] == ['cli']


def test_capture_title_collision_gets_a_distinct_filename(vault) -> None:
    scaffold_inbox(vault)
    ctx = make_ctx(vault)

    first = capture.notes_capture(ctx, 'Same Title', title='Same Title')
    second = capture.notes_capture(ctx, 'Same Title', title='Same Title')

    assert first['path'] == '05 Inbox/Same Title.md'
    assert second['path'] != first['path']
    assert second['path'].startswith('05 Inbox/Same Title ')
    assert (vault.agent / first['path']).is_file()
    assert (vault.agent / second['path']).is_file()


def test_capture_falls_back_to_daily_when_inbox_absent(vault) -> None:
    ctx = make_ctx(vault)
    assert '05' not in {category.id for category in ctx.index.categories()}

    result = capture.notes_capture(ctx, 'quick thought')

    assert result['ok'] is True, result
    assert result['path'].startswith('40 Journal/41 Daily/')
    text = (vault.agent / result['path']).read_text(encoding='utf-8')
    assert 'quick thought' in text
    assert '## Inbox' in text


# --------------------------------------------------------------------------- #
# notes_triage
# --------------------------------------------------------------------------- #
def test_triage_suggests_category_and_excludes_hub(vault) -> None:
    scaffold_inbox(vault)
    ctx = make_ctx(vault)
    capture.notes_capture(ctx, '# Project idea\n\nsomething about platforms')

    hits = [
        SearchHit(path='10 Projects/11 Project A/11 Project A.md', title='Project A', score=2.0, snippet=None),
        SearchHit(path='05 Inbox/05 Inbox.md', title='Inbox', score=9.0, snippet=None),
        SearchHit(path='00 Meta/00.00 Index.md', title='Index', score=8.0, snippet=None),
        SearchHit(path='nowhere.md', title=None, score=7.0, snippet=None),
    ]
    stub = StubSearch(hits)
    ctx.search = stub
    before = _head(vault)

    result = capture.notes_triage(ctx)

    assert result['ok'] is True, result
    assert result['commit'] is None
    assert result['pending_push'] is False
    assert result['count'] == 1
    assert [item['path'] for item in result['suggestions']] == ['05 Inbox/Project idea.md']

    top = result['suggestions'][0]
    assert top['suggested_category_id'] == '11'
    assert top['suggested_category'] == 'Project A'
    assert top['score'] == 2.0
    assert [candidate['category_id'] for candidate in top['candidates']] == ['11']
    assert stub.calls
    assert 'Project idea' in stub.calls[0]

    assert _head(vault) == before


def test_triage_single_path_target(vault) -> None:
    scaffold_inbox(vault)
    ctx = make_ctx(vault)
    capture.notes_capture(ctx, '# Lone note\n\nbody')
    ctx.search = StubSearch(
        [
            SearchHit(
                path='20 Areas/21 Platform Engineering/21 Platform Engineering.md',
                title='Area',
                score=1.5,
                snippet=None,
            )
        ]
    )

    result = capture.notes_triage(ctx, path='05 Inbox/Lone note.md')

    assert result['count'] == 1
    assert result['suggestions'][0]['suggested_category_id'] == '21'


def test_triage_missing_path_returns_envelope(vault) -> None:
    scaffold_inbox(vault)
    ctx = make_ctx(vault)

    result = capture.notes_triage(ctx, path='05 Inbox/Missing.md')

    assert result['ok'] is False
    assert result['error']['code'] == 'NOT_FOUND'


def test_triage_without_inbox_returns_envelope(vault) -> None:
    ctx = make_ctx(vault)

    result = capture.notes_triage(ctx)

    assert result['ok'] is False
    assert result['error']['code'] == 'CATEGORY_NOT_FOUND'
