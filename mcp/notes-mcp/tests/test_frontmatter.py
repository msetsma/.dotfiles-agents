"""Tests for notes_mcp.frontmatter."""

from __future__ import annotations

import pytest

from notes_mcp.errors import INVALID_FRONTMATTER, NotesError
from notes_mcp.frontmatter import (
    HUB_REQUIRED_KEYS,
    REQUIRED_KEYS,
    new_frontmatter,
    parse,
    serialize,
    sync_qmd_metadata,
    validate,
)


TODAY = '2026-10-06'


def make_fm():
    return new_frontmatter(type='note', status='active', today=TODAY)


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
