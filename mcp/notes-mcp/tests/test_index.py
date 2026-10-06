"""Tests for notes_mcp.index.VaultIndex."""

from __future__ import annotations

import pytest

from notes_mcp.config import Config
from notes_mcp.errors import CATEGORY_NOT_FOUND, INDEX_UNAVAILABLE, NotesError
from notes_mcp.index import Category, VaultIndex


INDEX_TEXT = """# Index
| ID | Category | Path | Scope |
|----|----------|------|-------|
| 00 | Meta | 00 Meta | System: index, templates, attachments, conflicts, views. |
| 11 | Project A | 10 Projects/11 Project A | Placeholder project — rename or delete. |
| 12 | Project B | 10 Projects/12 Project B | Placeholder project — rename or delete. |
| 21 | Platform Engineering | 20 Areas/21 Platform Engineering | Ongoing platform ownership. |
| 22 | Team & People Mgmt | 20 Areas/22 Team & People Mgmt | Ongoing team and people responsibilities. |
| 31 | How-tos | 30 Resources/31 How-tos | Step-by-step procedures. |
| 32 | Tools | 30 Resources/32 Tools | Tool reference and setup notes. |
| 33 | Concepts | 30 Resources/33 Concepts | Concepts, definitions, mental models. |
| 34 | People | 30 Resources/34 People | People reference notes. |
| 41 | Daily | 40 Journal/41 Daily | One note per day, named YYYY-MM-DD. |
| 42 | Weekly | 40 Journal/42 Weekly | Weekly reviews, named YYYY-Www. |
| 43 | Meetings | 40 Journal/43 Meetings | Meeting notes, named YYYY-MM-DD <Title>. |
| 90 | Archive | 90 Archive | Finished or retired material. |
"""


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / 'vault'
    meta = root / '00 Meta'
    meta.mkdir(parents=True)
    (meta / '00.00 Index.md').write_text(INDEX_TEXT, encoding='utf-8')
    (root / '10 Projects' / '11 Project A').mkdir(parents=True)
    return root


@pytest.fixture
def index(vault) -> VaultIndex:
    return VaultIndex(Config(vault_path=vault))


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


def test_validate_existing_folder(index):
    assert index.validate('11').id == '11'


def test_validate_missing_folder_raises(index):
    # Category 12 is in the table but its folder is absent from the vault.
    with pytest.raises(NotesError) as excinfo:
        index.validate('12')

    assert excinfo.value.code == CATEGORY_NOT_FOUND


def test_refresh_picks_up_changes(index):
    assert len(index.categories()) == 13

    meta = index.config.vault_path / '00 Meta' / '00.00 Index.md'
    meta.write_text(INDEX_TEXT + '| 91 | Extra | 90 Archive | Later. |\n', encoding='utf-8')

    assert len(index.categories(refresh=True)) == 14


def test_absent_index_is_unavailable(tmp_path):
    empty = tmp_path / 'empty'
    empty.mkdir()
    index = VaultIndex(Config(vault_path=empty))

    with pytest.raises(NotesError) as excinfo:
        index.categories()

    assert excinfo.value.code == INDEX_UNAVAILABLE


def test_unparseable_index_is_unavailable(vault):
    meta = vault / '00 Meta' / '00.00 Index.md'
    meta.write_text('# Index\nno table here\n', encoding='utf-8')
    index = VaultIndex(Config(vault_path=vault))

    with pytest.raises(NotesError) as excinfo:
        index.categories()

    assert excinfo.value.code == INDEX_UNAVAILABLE
