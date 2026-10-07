"""Tests for the digest tools: notes_weekly_digest / notes_stale_report.

Real git and the offline ``vault`` fixture; only the searcher is faked. The
fixture note tree is cleared first so grouping counts and staleness are
deterministic, then reseeded with notes that carry known ``updated`` dates.
"""

from __future__ import annotations

import datetime
import subprocess
from pathlib import Path
from types import SimpleNamespace

from notes_mcp import frontmatter
from notes_mcp.git import Git
from notes_mcp.index import VaultIndex
from notes_mcp.paths import PathGuard
from notes_mcp.tools import digest, write
from tests.fixtures.vault_fixture import note


GIT = '/usr/bin/git'
WEEK = '2026-W02'
WEEK_REL = f'40 Journal/42 Weekly/{WEEK}.md'

WEEKLY_TEMPLATE = """---
type: weekly
status: active
created: "{{date:YYYY-MM-DD}}"
updated: "{{date:YYYY-MM-DD}}"
tags: []
related: []
source: []
qmd:
  metadata:
    type: weekly
    status: active
    tags: []
---

# {{title}}

## Highlights

## Progress

## Blockers

## Next week
"""

# Required keys minus ``updated``: parse succeeds, the tool treats it as stale.
NO_UPDATED = """---
type: note
status: active
created: "2025-06-01"
tags: []
related: []
source: []
---

# No Updated
"""

# rel -> (updated date, stem); Old is deliberately outside 2026-W02.
DIGEST_NOTES = {
    '10 Projects/11 Project A/Alpha.md': ('2026-01-06', 'Alpha'),
    '10 Projects/11 Project A/Beta.md': ('2026-01-07', 'Beta'),
    '30 Resources/33 Concepts/Gamma.md': ('2026-01-08', 'Gamma'),
    '60 Loose/Delta.md': ('2026-01-09', 'Delta'),
    '10 Projects/11 Project A/Old.md': ('2026-01-20', 'Old'),
}


class FakeSearch:
    """Records ``schedule_reindex`` calls; never touches qmd."""

    def __init__(self) -> None:
        self.calls = 0

    def schedule_reindex(self, **_kwargs: object) -> None:
        self.calls += 1


def make_ctx(vault, **env_overrides) -> SimpleNamespace:
    config = vault.config(**env_overrides)
    return SimpleNamespace(
        config=config,
        guard=PathGuard(config.vault_path),
        index=VaultIndex(config),
        git=Git(config),
        search=FakeSearch(),
    )


def _today_iso() -> str:
    return datetime.datetime.now(tz=datetime.UTC).astimezone().date().isoformat()


def _last_subject(vault) -> str:
    proc = subprocess.run(
        [GIT, 'log', '-1', '--format=%s'], cwd=str(vault.agent), capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _clear_notes(root: Path) -> None:
    for path in sorted(root.rglob('*.md')):
        if path.relative_to(root).as_posix().startswith('00 Meta/'):
            continue
        path.unlink()


def _seed(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _commit(root: Path, message: str = 'seed digest notes') -> None:
    subprocess.run([GIT, 'add', '-A'], cwd=str(root), check=True)
    subprocess.run([GIT, 'commit', '-m', message], cwd=str(root), check=True)


def _note(updated: str, stem: str) -> str:
    return note(type_='note', updated=updated, body=f'# {stem}\n')


def _seed_digest(vault) -> None:
    _clear_notes(vault.agent)
    for rel, (updated, stem) in DIGEST_NOTES.items():
        _seed(vault.agent, rel, _note(updated, stem))
    _commit(vault.agent)


# --------------------------------------------------------------------------- #
# notes_weekly_digest
# --------------------------------------------------------------------------- #
def test_weekly_digest_draft_groups_and_writes_nothing(vault) -> None:
    ctx = make_ctx(vault)
    _seed_digest(vault)

    result = digest.notes_weekly_digest(ctx, week=WEEK, write=False)

    assert result['ok'] is True
    assert result['week'] == WEEK
    assert result['range'] == ['2026-01-05', '2026-01-11']
    assert result['path'] == WEEK_REL
    assert result['written'] is False
    assert result['counts'] == {'Project A': 2, 'Concepts': 1, 'Uncategorised': 1, 'total': 4}

    draft = result['draft']
    assert '**2026-W02** (2026-01-05 to 2026-01-11) — 4 notes updated' in draft
    assert '### Project A (2)' in draft
    assert '### Concepts (1)' in draft
    assert '### Uncategorised (1)' in draft
    assert '- [[Alpha]] — updated 2026-01-06' in draft
    assert '- [[Gamma]] — updated 2026-01-08' in draft
    assert '[[Old]]' not in draft
    assert '## Digest' in draft

    assert not (vault.agent / WEEK_REL).exists()
    assert ctx.search.calls == 0


def test_weekly_digest_defaults_to_current_week(vault) -> None:
    ctx = make_ctx(vault)
    iso = datetime.datetime.now(tz=datetime.UTC).astimezone().isocalendar()

    result = digest.notes_weekly_digest(ctx, write=False)

    assert result['week'] == f'{iso.year}-W{iso.week:02d}'


def test_weekly_digest_invalid_week_is_rejected(vault) -> None:
    ctx = make_ctx(vault)

    for bad in ('nope', '2026-W99'):
        result = digest.notes_weekly_digest(ctx, week=bad, write=False)

        assert result['ok'] is False
        assert result['error']['code'] == 'PATH_REJECTED'


def test_weekly_digest_write_creates_note_with_minimal_fallback(vault) -> None:
    ctx = make_ctx(vault)
    _seed_digest(vault)
    assert not (vault.agent / '00 Meta/01 Templates/Weekly.md').exists()

    result = digest.notes_weekly_digest(ctx, week=WEEK, write=True)

    assert result['ok'] is True
    assert result['written'] is True
    target = vault.agent / WEEK_REL
    fm, body = frontmatter.parse(target.read_text(encoding='utf-8'))
    assert fm['type'] == 'weekly'
    assert fm['updated'] == _today_iso()
    assert body.startswith('# 2026-W02')
    assert '## Digest' in body
    assert '### Project A (2)' in body
    assert _last_subject(vault) == 'agent(notes_weekly_digest): 2026-W02'
    assert ctx.search.calls == 1


def test_weekly_digest_write_seeds_from_template(vault) -> None:
    ctx = make_ctx(vault)
    _seed_digest(vault)
    _seed(vault.agent, '00 Meta/01 Templates/Weekly.md', WEEKLY_TEMPLATE)
    _commit(vault.agent)

    result = digest.notes_weekly_digest(ctx, week=WEEK, write=True)

    assert result.get('ok') is not False, result
    _, body = frontmatter.parse((vault.agent / WEEK_REL).read_text(encoding='utf-8'))
    assert '# 2026-W02' in body
    assert '## Highlights' in body
    assert '## Digest' in body
    assert '{{title}}' not in body


def test_weekly_digest_replaces_existing_digest_section(vault) -> None:
    ctx = make_ctx(vault)
    _seed_digest(vault)
    existing = note(
        type_='weekly',
        updated='2026-01-05',
        body='# 2026-W02\n\n## Digest\n\n- [[Stale bullet]]\n\n## Highlights\n\nKeep me.\n',
    )
    _seed(vault.agent, WEEK_REL, existing)
    _commit(vault.agent)

    result = digest.notes_weekly_digest(ctx, week=WEEK, write=True)

    assert result.get('ok') is not False, result
    _, body = frontmatter.parse((vault.agent / WEEK_REL).read_text(encoding='utf-8'))
    assert '[[Stale bullet]]' not in body
    assert body.count('## Digest') == 1
    assert '### Project A (2)' in body
    assert 'Keep me.' in body


# --------------------------------------------------------------------------- #
# notes_stale_report
# --------------------------------------------------------------------------- #
def test_stale_report_lists_old_but_not_fresh(vault, monkeypatch) -> None:
    ctx = make_ctx(vault)
    monkeypatch.setattr(digest, '_today', lambda: datetime.date(2026, 3, 1))
    _clear_notes(vault.agent)
    _seed(vault.agent, '30 Resources/33 Concepts/Old.md', _note('2025-01-01', 'Old'))
    _seed(vault.agent, '30 Resources/33 Concepts/Fresh.md', _note('2026-02-15', 'Fresh'))
    _commit(vault.agent)

    result = digest.notes_stale_report(ctx, days=90, write=False)

    assert result['ok'] is True
    assert result['as_of'] == '2026-03-01'
    assert result['days'] == 90
    assert result['written'] is False
    assert result['path'] is None
    assert result['count'] == 1
    assert result['stale'][0]['path'] == '30 Resources/33 Concepts/Old.md'
    assert result['stale'][0]['updated'] == '2025-01-01'
    assert result['stale'][0]['days'] == 424
    assert ctx.search.calls == 0


def test_stale_report_missing_updated_counts_as_stale(vault, monkeypatch) -> None:
    ctx = make_ctx(vault)
    monkeypatch.setattr(digest, '_today', lambda: datetime.date(2026, 3, 1))
    _clear_notes(vault.agent)
    _seed(vault.agent, '30 Resources/33 Concepts/NoDate.md', NO_UPDATED)
    _commit(vault.agent)

    result = digest.notes_stale_report(ctx, days=90, write=False)

    assert result['count'] == 1
    assert result['stale'][0] == {'path': '30 Resources/33 Concepts/NoDate.md', 'updated': None, 'days': None}


def test_stale_report_orders_oldest_first(vault, monkeypatch) -> None:
    ctx = make_ctx(vault)
    monkeypatch.setattr(digest, '_today', lambda: datetime.date(2026, 3, 1))
    _clear_notes(vault.agent)
    _seed(vault.agent, '30 Resources/33 Concepts/Younger.md', _note('2025-06-01', 'Younger'))
    _seed(vault.agent, '30 Resources/33 Concepts/Older.md', _note('2025-01-01', 'Older'))
    _commit(vault.agent)

    result = digest.notes_stale_report(ctx, days=90, write=False)

    assert [entry['path'] for entry in result['stale']] == [
        '30 Resources/33 Concepts/Older.md',
        '30 Resources/33 Concepts/Younger.md',
    ]


def test_stale_report_write_appends_to_daily(vault, monkeypatch) -> None:
    ctx = make_ctx(vault)
    monkeypatch.setattr(digest, '_today', lambda: datetime.date(2026, 3, 1))
    monkeypatch.setattr(write, '_today', lambda: '2026-03-01')
    _clear_notes(vault.agent)
    _seed(vault.agent, '30 Resources/33 Concepts/Old.md', _note('2025-01-01', 'Old'))
    _commit(vault.agent)

    result = digest.notes_stale_report(ctx, days=90, write=True)

    assert result['ok'] is True
    assert result['written'] is True
    assert result['path'] == '40 Journal/41 Daily/2026-03-01.md'
    text = (vault.agent / result['path']).read_text(encoding='utf-8')
    assert '## Reports' in text
    assert '- [[Old]] — updated 2025-01-01 (424 days)' in text
    assert _last_subject(vault) == 'agent(notes_append): 40 Journal/41 Daily/2026-03-01.md'
    assert ctx.search.calls == 1
