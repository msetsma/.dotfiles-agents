"""Tests for the structure tools: notes_move / notes_rename / notes_sync.

Real git and the M4 ``vault`` fixture; only the searcher is faked so reindex
scheduling can be observed without invoking qmd.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

from notes_mcp import frontmatter
from notes_mcp.git import Git
from notes_mcp.index import VaultIndex
from notes_mcp.paths import PathGuard
from notes_mcp.tools import structure, write


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


def _run(args: list[str], cwd) -> subprocess.CompletedProcess:
    run = subprocess.run
    return run(args, cwd=str(cwd), capture_output=True, text=True, check=True)


def _head(vault) -> str:
    return _run([GIT, 'rev-parse', 'HEAD'], vault.agent).stdout.strip()


def _last_author(vault) -> str:
    return _run([GIT, 'log', '-1', '--format=%an'], vault.agent).stdout.strip()


def _last_subject(vault) -> str:
    return _run([GIT, 'log', '-1', '--format=%s'], vault.agent).stdout.strip()


def _commits_since(vault, sha: str) -> int:
    count = _run([GIT, 'rev-list', '--count', f'{sha}..HEAD'], vault.agent).stdout.strip()
    return int(count)


def _human_commit(vault, rel: str, text: str) -> None:
    """Write, commit, and push ``rel`` from the human clone (concurrent editor)."""
    path = vault.human / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    _run([GIT, 'add', rel], vault.human)
    _run([GIT, 'commit', '-m', f'human: {rel}'], vault.human)
    _run([GIT, 'push', 'origin', 'main'], vault.human)


def _create(ctx, title: str, body: str) -> str:
    result = write.notes_create(ctx, '11', title, 'note', body)
    assert result.get('ok') is not False, result
    return result['path']


CONCEPT = '30 Resources/33 Concepts/Johnny Decimal.md'


# --------------------------------------------------------------------------- #
# notes_move
# --------------------------------------------------------------------------- #
def test_move_happy_path(vault) -> None:
    ctx = make_ctx(vault)
    new_rel = '10 Projects/11 Project A/Johnny Decimal.md'
    assert (vault.agent / CONCEPT).is_file()

    result = structure.notes_move(ctx, CONCEPT, '11')

    assert result['ok'] is True, result
    assert result['path'] == new_rel
    assert result['commit']
    assert result['pending_push'] is False
    assert result['needs_index_update'] is False
    assert not (vault.agent / CONCEPT).exists()
    assert (vault.agent / new_rel).is_file()
    fm, _ = frontmatter.parse((vault.agent / new_rel).read_text(encoding='utf-8'))
    assert fm['updated'] == _today()
    assert _last_author(vault) == 'agent:claude'
    assert _last_subject(vault) == f'agent(notes_move): {new_rel}'
    assert ctx.search.calls == 1


def test_move_into_meta_is_rejected(vault) -> None:
    ctx = make_ctx(vault)

    result = structure.notes_move(ctx, CONCEPT, '00')

    assert result['ok'] is False
    assert result['error']['code'] == 'PATH_REJECTED'
    assert (vault.agent / CONCEPT).is_file()


def test_move_unknown_category(vault) -> None:
    ctx = make_ctx(vault)

    result = structure.notes_move(ctx, CONCEPT, '99')

    assert result['ok'] is False
    assert result['error']['code'] == 'CATEGORY_NOT_FOUND'


def test_move_missing_note(vault) -> None:
    ctx = make_ctx(vault)

    result = structure.notes_move(ctx, '30 Resources/33 Concepts/Missing.md', '11')

    assert result['ok'] is False
    assert result['error']['code'] == 'NOT_FOUND'


def test_move_to_archive_flags_index_update(vault) -> None:
    ctx = make_ctx(vault)
    (vault.agent / '90 Archive').mkdir(parents=True, exist_ok=True)

    result = structure.notes_move(ctx, CONCEPT, '90')

    assert result['ok'] is True, result
    assert result['path'] == '90 Archive/Johnny Decimal.md'
    assert result['needs_index_update'] is True
    assert (vault.agent / '90 Archive/Johnny Decimal.md').is_file()


def test_move_duplicate_filename(vault) -> None:
    ctx = make_ctx(vault)
    # notes_create refuses a vault-wide duplicate basename, so plant the
    # colliding note directly at the move target.
    (vault.agent / '10 Projects/11 Project A/Johnny Decimal.md').write_text('# Duplicate\n', encoding='utf-8')

    result = structure.notes_move(ctx, CONCEPT, '11')

    assert result['ok'] is False
    assert result['error']['code'] == 'DUPLICATE_FILENAME'


def test_move_rewrites_path_qualified_links(vault) -> None:
    ctx = make_ctx(vault)
    referrer = _create(
        ctx, 'Path Referrer', '# Path Referrer\n\nSee [[10 Projects/11 Project A/Meeting Notes 2026-01-05]].\n'
    )
    before = _head(vault)

    result = structure.notes_move(ctx, '10 Projects/11 Project A/Meeting Notes 2026-01-05.md', '21')

    new_rel = '20 Areas/21 Platform Engineering/Meeting Notes 2026-01-05.md'
    assert result['ok'] is True, result
    assert result['path'] == new_rel
    assert result['rewritten'] == 1
    assert _commits_since(vault, before) == 1

    text = (vault.agent / referrer).read_text(encoding='utf-8')
    assert '[[20 Areas/21 Platform Engineering/Meeting Notes 2026-01-05]]' in text
    assert '[[10 Projects/11 Project A/Meeting Notes 2026-01-05' not in text


# --------------------------------------------------------------------------- #
# notes_rename
# --------------------------------------------------------------------------- #
def test_rename_rewrites_wikilinks_in_one_commit(vault) -> None:
    ctx = make_ctx(vault)
    target = _create(ctx, 'Old Title', '# Old Title\n\nbody\n')
    referrer = _create(
        ctx,
        'Referrer',
        '# Referrer\n\nSee [[Old Title]], [[Old Title|alias]], [[Old Title#Heading]],'
        ' and [[10 Projects/11 Project A/Old Title]].\n',
    )
    before = _head(vault)

    result = structure.notes_rename(ctx, target, 'New Title')

    new_rel = '10 Projects/11 Project A/New Title.md'
    assert result['ok'] is True, result
    assert result['path'] == new_rel
    assert result['rewritten'] == 1
    assert result['pending_push'] is False
    assert result['needs_index_update'] is False
    assert _commits_since(vault, before) == 1
    assert not (vault.agent / target).exists()
    assert (vault.agent / new_rel).is_file()

    text = (vault.agent / referrer).read_text(encoding='utf-8')
    assert '[[New Title]]' in text
    assert '[[New Title|alias]]' in text
    assert '[[New Title#Heading]]' in text
    assert '[[10 Projects/11 Project A/New Title]]' in text
    assert '[[Old Title' not in text


def test_rename_accepts_camelcase_alias(vault) -> None:
    ctx = make_ctx(vault)
    target = _create(ctx, 'Old Title', '# Old Title\n\nbody\n')

    result = structure.notes_rename(ctx, target, newTitle='Camel Title')

    assert result['ok'] is True, result
    assert result['path'] == '10 Projects/11 Project A/Camel Title.md'


def test_rename_duplicate_filename(vault) -> None:
    ctx = make_ctx(vault)
    target = _create(ctx, 'Old Title', '# Old Title\n\nbody\n')
    _create(ctx, 'New Title', '# New Title\n')

    result = structure.notes_rename(ctx, target, 'New Title')

    assert result['ok'] is False
    assert result['error']['code'] == 'DUPLICATE_FILENAME'


def test_rename_missing_note(vault) -> None:
    ctx = make_ctx(vault)

    result = structure.notes_rename(ctx, '30 Resources/33 Concepts/Missing.md', 'New')

    assert result['ok'] is False
    assert result['error']['code'] == 'NOT_FOUND'


# --------------------------------------------------------------------------- #
# notes_move_category
# --------------------------------------------------------------------------- #
CATEGORY = '10 Projects/11 Project A'


def _create_in(ctx, category_id: str, title: str, body: str) -> str:
    result = write.notes_create(ctx, category_id, title, 'note', body)
    assert result.get('ok') is not False, result
    return result['path']


def test_move_category_relocates_and_updates_index(vault) -> None:
    ctx = make_ctx(vault)
    referrer = _create_in(
        ctx,
        '33',
        'Category Referrer',
        '# Category Referrer\n\nSee [[10 Projects/11 Project A/Meeting Notes 2026-01-05]] '
        'and [[Meeting Notes 2026-01-05]].\n',
    )
    before = _head(vault)

    result = structure.notes_move_category(ctx, '11', '10 Projects/Renamed Project')

    new_dir = '10 Projects/11 Renamed Project'
    assert result['ok'] is True, result
    assert result['path'] == new_dir
    assert result['index_path'] == '00 Meta/00.00 Index.md'
    assert result['needs_index_update'] is False
    assert result['rewritten'] == 1
    assert _commits_since(vault, before) == 1

    assert not (vault.agent / CATEGORY).exists()
    assert (vault.agent / new_dir / 'Meeting Notes 2026-01-05.md').is_file()

    index_text = (vault.agent / '00 Meta/00.00 Index.md').read_text(encoding='utf-8')
    assert new_dir in index_text
    assert CATEGORY not in index_text
    assert ctx.index.get('11').path == new_dir

    text = (vault.agent / referrer).read_text(encoding='utf-8')
    assert '[[10 Projects/11 Renamed Project/Meeting Notes 2026-01-05]]' in text
    assert '[[Meeting Notes 2026-01-05]]' in text
    assert '[[10 Projects/11 Project A/' not in text


def test_move_category_renames_hub_and_updates_index_cells(vault) -> None:
    ctx = make_ctx(vault)
    hub = '10 Projects/11 Project A/11 Project A.md'
    hub_path = vault.agent / hub
    # Align the hub H1 with its folder basename so the rename must rewrite it.
    aligned = hub_path.read_text(encoding='utf-8').replace('# Project A\n', '# 11 Project A\n', 1)
    hub_path.write_text(aligned, encoding='utf-8')
    _run([GIT, 'add', '--', hub], vault.agent)
    _run([GIT, 'commit', '-m', 'test: align hub H1'], vault.agent)

    referrer = _create_in(
        ctx, '33', 'Title Referrer', '# Title Referrer\n\nSee [[11 Project A]] and [[11 Project A|alias]].\n'
    )
    before = _head(vault)

    result = structure.notes_move_category(ctx, '11', '10 Projects/Renamed Project', new_scope='Renamed scope.')

    new_dir = '10 Projects/11 Renamed Project'
    assert result['ok'] is True, result
    assert result['path'] == new_dir
    assert result['name'] == 'Renamed Project'
    assert result['hub'] == f'{new_dir}/11 Renamed Project.md'
    assert _commits_since(vault, before) == 1

    assert not (vault.agent / CATEGORY).exists()
    new_hub = vault.agent / new_dir / '11 Renamed Project.md'
    assert new_hub.is_file()
    fm, body = frontmatter.parse(new_hub.read_text(encoding='utf-8'))
    assert '# 11 Renamed Project' in body
    assert '# 11 Project A' not in body
    assert fm['updated'] == _today()
    assert fm['scope'] == 'Renamed scope.'

    index_text = (vault.agent / '00 Meta/00.00 Index.md').read_text(encoding='utf-8')
    assert '| 11 | Renamed Project | 10 Projects/11 Renamed Project | Renamed scope. |' in index_text

    text = (vault.agent / referrer).read_text(encoding='utf-8')
    assert '[[11 Renamed Project]]' in text
    assert '[[11 Renamed Project|alias]]' in text
    assert '[[11 Project A' not in text

    category = ctx.index.get('11')
    assert (category.name, category.path, category.scope) == ('Renamed Project', new_dir, 'Renamed scope.')


def test_move_category_accepts_camelcase_aliases(vault) -> None:
    ctx = make_ctx(vault)

    result = structure.notes_move_category(ctx, '11', '10 Projects/Renamed', newName='Custom', newScope='Custom scope.')

    assert result['ok'] is True, result
    assert result['name'] == 'Custom'
    assert ctx.index.get('11').name == 'Custom'
    assert ctx.index.get('11').scope == 'Custom scope.'


def test_move_category_rejects_meta_destination(vault) -> None:
    ctx = make_ctx(vault)

    result = structure.notes_move_category(ctx, '11', '00 Meta/11 Project A')

    assert result['ok'] is False
    assert result['error']['code'] == 'PATH_REJECTED'
    assert (vault.agent / CATEGORY).is_dir()


def test_move_category_rejects_existing_destination(vault) -> None:
    ctx = make_ctx(vault)
    (vault.agent / '10 Projects/11 Existing').mkdir(parents=True, exist_ok=True)

    result = structure.notes_move_category(ctx, '11', '10 Projects/11 Existing')

    assert result['ok'] is False
    assert result['error']['code'] == 'PATH_REJECTED'


def test_move_category_rejects_same_path(vault) -> None:
    ctx = make_ctx(vault)

    result = structure.notes_move_category(ctx, '11', CATEGORY)

    assert result['ok'] is False
    assert result['error']['code'] == 'PATH_REJECTED'


# --------------------------------------------------------------------------- #
# notes_sync
# --------------------------------------------------------------------------- #
def test_sync_clean_reports_branch_state(vault) -> None:
    ctx = make_ctx(vault)
    before = _head(vault)

    result = structure.notes_sync(ctx)

    assert result['ok'] is True
    assert result['branch'] == 'main'
    assert result['ahead'] == 0
    assert result['behind'] == 0
    assert result['conflicts'] is False
    assert result['commit'] is None
    assert result['pending_push'] is False
    assert result['needs_index_update'] is False
    assert _head(vault) == before
    assert ctx.search.calls == 0


def test_sync_pulls_a_human_commit(vault) -> None:
    ctx = make_ctx(vault)
    rel = '30 Resources/33 Concepts/Human Concept.md'
    _human_commit(vault, rel, '# Human Concept\n\nfrom the human clone\n')

    result = structure.notes_sync(ctx)

    assert result['ok'] is True, result
    assert (vault.agent / rel).is_file()
    assert result['behind'] == 0
    assert result['ahead'] == 0
    assert result['conflicts'] is False
