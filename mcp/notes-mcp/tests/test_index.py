"""Tests for notes_mcp.index.VaultIndex."""

from __future__ import annotations

import pytest

from notes_mcp.config import Config
from notes_mcp.errors import CATEGORY_NOT_FOUND, INDEX_UNAVAILABLE, NotesError
from notes_mcp.index import Category, VaultIndex
from tests.fixtures.vault_fixture import INDEX_MD


@pytest.fixture
def index(vault) -> VaultIndex:
    return VaultIndex(vault.config())


def _write_index(vault, text: str) -> VaultIndex:
    (vault.agent / '00 Meta' / '00.00 Index.md').write_text(text, encoding='utf-8')
    return VaultIndex(vault.config())


def _table(*rows: str) -> str:
    header = '# Index\n| ID | Category | Path | Scope |\n|----|----|----|----|\n'
    return header + ''.join(row + '\n' for row in rows)


def test_categories_parses_table(index):
    categories = index.categories()

    assert len(categories) == 13
    assert all(isinstance(category, Category) for category in categories)
    assert categories[1] == Category(
        id='11', name='Project A', path='10 Projects/11 Project A', scope='Placeholder project — rename or delete.'
    )


def test_get_returns_category(index):
    assert index.get('11').name == 'Project A'


def test_get_missing_raises(index):
    with pytest.raises(NotesError) as excinfo:
        index.get('99')

    assert excinfo.value.code == CATEGORY_NOT_FOUND


def test_by_path_prefix_matches_descendants(index):
    assert index.by_path_prefix('10 Projects/11 Project A/sub/note.md').id == '11'
    assert index.by_path_prefix('10 Projects/11 Project A').id == '11'
    assert index.by_path_prefix('not/a/category/note.md') is None


def test_by_path_prefix_prefers_longest_match(vault):
    index = _write_index(
        vault, _table('| 10 | Projects | 10 Projects | top |', '| 11 | Project A | 10 Projects/11 Project A | sub |')
    )

    assert index.by_path_prefix('10 Projects/11 Project A/note.md').id == '11'
    assert index.by_path_prefix('10 Projects/other/note.md').id == '10'


def test_validate_existing_folder(index):
    assert index.validate('11').id == '11'


def test_validate_missing_folder_raises(index):
    # Category 12 is in the table but its folder is absent from the vault.
    with pytest.raises(NotesError) as excinfo:
        index.validate('12')

    assert excinfo.value.code == CATEGORY_NOT_FOUND


@pytest.mark.parametrize('bad_path', ['/etc', '../escape', '10 Projects/../../escape'])
def test_validate_rejects_path_outside_vault(vault, bad_path):
    index = _write_index(vault, _table(f'| 11 | Evil | {bad_path} | outside |'))

    with pytest.raises(NotesError) as excinfo:
        index.validate('11')

    assert excinfo.value.code == CATEGORY_NOT_FOUND


def test_by_path_prefix_skips_escaping_path(vault):
    index = _write_index(vault, _table('| 11 | Evil | ../escape | outside |'))

    assert index.by_path_prefix('../escape/note.md') is None


def test_duplicate_ids_are_unavailable(vault):
    index = _write_index(
        vault, _table('| 11 | A | 10 Projects/11 Project A | one |', '| 11 | B | 10 Projects/11 Project A | two |')
    )

    with pytest.raises(NotesError) as excinfo:
        index.categories()

    assert excinfo.value.code == INDEX_UNAVAILABLE
    assert '11' in excinfo.value.message


def test_empty_id_is_unavailable(vault):
    index = _write_index(vault, _table('|  | A | 10 Projects/11 Project A | one |'))

    with pytest.raises(NotesError) as excinfo:
        index.categories()

    assert excinfo.value.code == INDEX_UNAVAILABLE


def test_escaped_pipe_cell_parses(vault):
    index = _write_index(vault, _table('| 11 | A \\| B | 10 Projects/11 Project A | Rename or delete \\| later. |'))

    category = index.get('11')
    assert category.name == 'A | B'
    assert category.scope == 'Rename or delete | later.'


def test_link_alias_pipe_cell_parses(vault):
    index = _write_index(vault, _table('| 11 | [[Project A\\|hub]] | 10 Projects/11 Project A | one |'))

    assert index.get('11').name == '[[Project A|hub]]'


def test_refresh_picks_up_changes(index):
    assert len(index.categories()) == 13

    meta = index.config.vault_path / '00 Meta' / '00.00 Index.md'
    meta.write_text(INDEX_MD + '| 91 | Extra | 90 Archive | Later. |\n', encoding='utf-8')

    assert len(index.categories(refresh=True)) == 14


def test_absent_index_is_unavailable(tmp_path):
    empty = tmp_path / 'empty'
    empty.mkdir()
    index = VaultIndex(Config(vault_path=empty))

    with pytest.raises(NotesError) as excinfo:
        index.categories()

    assert excinfo.value.code == INDEX_UNAVAILABLE


def test_unparseable_index_is_unavailable(vault):
    index = _write_index(vault, '# Index\nno table here\n')

    with pytest.raises(NotesError) as excinfo:
        index.categories()

    assert excinfo.value.code == INDEX_UNAVAILABLE


def test_rewrite_path_swaps_only_path_cell(index):
    new_path = '10 Projects/11 Renamed'
    result = index.rewrite_path('11', new_path)

    old_row = '| 11 | Project A | 10 Projects/11 Project A | Placeholder project — rename or delete. |'
    new_row = '| 11 | Project A | 10 Projects/11 Renamed | Placeholder project — rename or delete. |'
    assert result == INDEX_MD.replace(old_row, new_row)


def test_rewrite_path_preserves_other_rows(index):
    before = INDEX_MD.splitlines()
    after = index.rewrite_path('11', '10 Projects/11 Renamed').splitlines()

    assert len(before) == len(after)
    changed = 0
    for old_line, new_line in zip(before, after, strict=True):
        if '| 11 |' in old_line:
            assert new_line != old_line
            changed += 1
        else:
            assert new_line == old_line
    assert changed == 1


def test_rewrite_path_missing_raises(index):
    with pytest.raises(NotesError) as excinfo:
        index.rewrite_path('99', 'anywhere')

    assert excinfo.value.code == CATEGORY_NOT_FOUND


def test_apply_path_writes_and_refreshes_cache(index):
    assert index.get('11').path == '10 Projects/11 Project A'

    index.apply_path('11', '10 Projects/11 Renamed')

    on_disk = (index.config.vault_path / '00 Meta' / '00.00 Index.md').read_text(encoding='utf-8')
    assert '10 Projects/11 Renamed' in on_disk
    assert index.get('11').path == '10 Projects/11 Renamed'
    assert index.categories(refresh=True)[1].path == '10 Projects/11 Renamed'
