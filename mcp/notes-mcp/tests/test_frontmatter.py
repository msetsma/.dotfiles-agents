"""Tests for notes_mcp.frontmatter."""

from __future__ import annotations

import pytest

from notes_mcp.errors import INVALID_FRONTMATTER, NotesError
from notes_mcp.frontmatter import (
    HUB_REQUIRED_KEYS,
    REQUIRED_KEYS,
    new_frontmatter,
    normalize_scalars,
    parse,
    serialize,
    sync_qmd_metadata,
    validate,
)


TODAY = '2026-10-06'


def make_fm():
    return new_frontmatter(type='note', status='active', today=TODAY)


def make_unquoted_text(*, scope: bool = False) -> str:
    """A hand-edited note whose dates are unquoted (human-typed YAML scalars)."""
    lines = [
        '---',
        'type: note',
        'status: active',
        'created: 2026-10-06',
        'updated: 2026-10-06T12:00:00',
        'tags: []',
        'related: []',
        'source: []',
    ]
    if scope:
        lines.append('scope: Ongoing platform ownership.')
    lines.extend(['---', 'body', ''])
    return '\n'.join(lines)


def test_new_frontmatter_canonical_order():
    fm = make_fm()

    assert list(fm) == list(REQUIRED_KEYS)
    assert fm['created'] == fm['updated'] == TODAY
    assert fm['tags'] == []
    assert fm['related'] == []
    assert fm['source'] == []


def test_round_trip_preserves_unknown_key_order_and_qmd():
    fm = make_fm()
    fm['custom'] = 'keep-me'
    fm['qmd'] = {'collection': 'notes', 'metadata': {'stale': True}}
    sync_qmd_metadata(fm)

    text = serialize(fm, 'Body line\n')
    parsed, body = parse(text)

    assert parsed == fm
    assert list(parsed) == list(fm)
    assert parsed['custom'] == 'keep-me'
    assert parsed['qmd']['metadata'] == {'type': 'note', 'status': 'active', 'tags': []}
    assert parsed['qmd']['collection'] == 'notes'
    assert body == 'Body line\n'


def test_serialize_wraps_in_fences():
    text = serialize(make_fm(), 'body')

    assert text.startswith('---\n')
    assert '\n---\n' in text


def test_sync_qmd_metadata_tracks_current_values():
    fm = make_fm()
    fm['status'] = 'archived'
    fm['tags'] = ['a', 'b']

    sync_qmd_metadata(fm)

    assert fm['qmd']['metadata'] == {'type': 'note', 'status': 'archived', 'tags': ['a', 'b']}


def test_serialize_emits_no_yaml_anchors():
    fm = make_fm()
    fm['tags'] = ['a', 'b']
    sync_qmd_metadata(fm)

    text = serialize(fm, 'Body\n')

    assert '&' not in text
    assert '*id' not in text
    parsed, _ = parse(text)
    assert parsed['tags'] == ['a', 'b']
    assert parsed['qmd']['metadata']['tags'] == ['a', 'b']


def test_sync_qmd_metadata_does_not_alias_tags():
    fm = make_fm()
    fm['tags'] = ['a']

    sync_qmd_metadata(fm)
    fm['tags'].append('b')

    assert fm['qmd']['metadata']['tags'] == ['a']


def test_parse_without_frontmatter_is_invalid():
    with pytest.raises(NotesError) as excinfo:
        parse('Just a plain body.\n')

    assert excinfo.value.code == INVALID_FRONTMATTER


def test_parse_unclosed_frontmatter_is_invalid():
    with pytest.raises(NotesError) as excinfo:
        parse('---\ntype: note\n')

    assert excinfo.value.code == INVALID_FRONTMATTER


def test_parse_bad_yaml_is_invalid():
    with pytest.raises(NotesError) as excinfo:
        parse('---\nfoo: [unclosed\n---\nbody\n')

    assert excinfo.value.code == INVALID_FRONTMATTER


def test_parse_non_mapping_is_invalid():
    with pytest.raises(NotesError) as excinfo:
        parse('---\n- a\n- b\n---\nbody\n')

    assert excinfo.value.code == INVALID_FRONTMATTER


def test_validate_accepts_canonical():
    validate(make_fm())


def test_validate_missing_key_is_invalid():
    fm = make_fm()
    del fm['tags']

    with pytest.raises(NotesError) as excinfo:
        validate(fm)

    assert excinfo.value.code == INVALID_FRONTMATTER


def test_validate_wrong_type_is_invalid():
    fm = make_fm()
    fm['tags'] = 'not-a-list'

    with pytest.raises(NotesError) as excinfo:
        validate(fm)

    assert excinfo.value.code == INVALID_FRONTMATTER


def test_validate_hub_requires_scope():
    fm = make_fm()

    with pytest.raises(NotesError) as excinfo:
        validate(fm, hub=True)
    assert excinfo.value.code == INVALID_FRONTMATTER

    fm['scope'] = 'Ongoing platform ownership.'
    validate(fm, hub=True)
    assert 'scope' in HUB_REQUIRED_KEYS


def test_parse_coerces_unquoted_dates_to_iso_strings():
    fm, body = parse(make_unquoted_text())

    assert fm['created'] == '2026-10-06'
    assert fm['updated'] == '2026-10-06T12:00:00'
    assert isinstance(fm['created'], str)
    assert isinstance(fm['updated'], str)
    assert body == 'body\n'


def test_parse_coerces_nested_dates_in_qmd_and_lists():
    text = make_unquoted_text().replace(
        'source: []\n', 'source: []\nqmd:\n  metadata:\n    indexed: 2026-10-06\n  history:\n    - 2026-01-02\n'
    )
    fm, _ = parse(text)

    assert fm['qmd']['metadata']['indexed'] == '2026-10-06'
    assert isinstance(fm['qmd']['metadata']['indexed'], str)
    assert fm['qmd']['history'] == ['2026-01-02']
    assert isinstance(fm['qmd']['history'][0], str)


def test_normalize_scalars_leaves_non_dates_untouched():
    value = {
        'count': 3,
        'ratio': 0.5,
        'flag': True,
        'nothing': None,
        'quoted': '2026-10-06',
        'nested': ['plain', 1, False],
    }

    assert normalize_scalars(value) == value
    assert normalize_scalars(value)['count'] == 3
    assert normalize_scalars(value)['flag'] is True


def test_validate_accepts_note_parsed_from_unquoted_dates():
    fm, _ = parse(make_unquoted_text(scope=True))

    validate(fm, hub=True)


def test_serialize_quotes_coerced_dates_and_round_trips():
    fm, _ = parse(make_unquoted_text())

    text = serialize(fm, 'body\n')

    assert "created: '2026-10-06'" in text
    assert "updated: '2026-10-06T12:00:00'" in text
    reparsed, body = parse(text)
    assert reparsed == fm
    assert reparsed['created'] == '2026-10-06'
    assert reparsed['updated'] == '2026-10-06T12:00:00'
    assert body == 'body\n'
