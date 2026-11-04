"""Tests for the vocabulary checker: ``uv run --no-project --with pytest pytest``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from check_frontmatter import check, load_schema, main, render_template


SCHEMA = load_schema()

GOOD_WATCH = {
    'type': 'watch',
    'tags': [],
    'brand': '[[Tudor]]',
    'model': 'Black Bay 58',
    'reference': 'M7939A1A0NU-0002',
    'style': 'diver',
    'case_diameter_mm': 39,
    'thickness_mm': 11.7,
    'crystal': 'sapphire',
    'crystal_domed': True,
    'complications': [],
    'strap_type': 'bracelet',
    'bracelet_links': 3,
    'microadjust': True,
    'retail_date': '2026-11',
    'market_source': ['ebay-asking', 'chrono24-asking'],
    'rating': None,
    'verified': False,
}


def test_conforming_watch_passes() -> None:
    assert check('watch', GOOD_WATCH, SCHEMA) == []


@pytest.mark.parametrize(
    ('key', 'value'),
    [
        ('crystal', 'Sapphire'),
        ('case_diameter_mm', '39mm'),
        ('microadjust', 'yes'),
        ('brand', 'Tudor'),
        ('retail_date', '2026-11-04'),
        ('complications', ['Date']),
        ('style', 'dive'),
        ('rating', 11),
        ('verified', 0),
        ('release_year', 2026.5),
    ],
)
def test_nonstandard_values_are_flagged(key: str, value: object) -> None:
    problems = check('watch', {**GOOD_WATCH, key: value}, SCHEMA)
    assert len(problems) == 1
    assert problems[0].startswith(f'{key}:')


def test_unknown_and_retired_keys_are_flagged() -> None:
    problems = check('watch', {**GOOD_WATCH, 'ownership': 'wishlist', 'bracelet': 'rivet'}, SCHEMA)
    assert sorted(problems) == sorted(
        ['ownership: unknown key (not in schema.toml)', 'bracelet: unknown key (not in schema.toml)']
    )


def test_bracelet_links_needs_bracelet() -> None:
    assert check('watch', {**GOOD_WATCH, 'strap_type': 'rubber'}, SCHEMA) == [
        'bracelet_links: set only when strap_type is bracelet'
    ]


def test_movement_and_brand_kinds() -> None:
    movement = {'kind': 'movement', 'maker': '[[Omega]]', 'calibre': '3861', 'certification': ['metas', 'iso-3159']}
    assert check('movement', movement, SCHEMA) == []
    assert check('brand', {'kind': 'brand', 'country': 'Switzerland'}, SCHEMA) == [
        f"country: 'Switzerland' is not one of {', '.join(SCHEMA['brand']['fields']['country']['values'])}"
    ]


def test_template_lists_every_schema_key_once() -> None:
    text = render_template('watch', SCHEMA)
    keys = [line.split(':')[0] for line in text.splitlines() if line and not line.startswith((' ', '-'))]
    assert set(SCHEMA['watch']['order']) <= set(keys)
    assert len(keys) == len(set(keys))
    assert 'verified: false' in text
    assert set(SCHEMA['watch']['order']) == set(SCHEMA['watch']['fields'])


def test_cli_check_and_template(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / 'fm.json'
    source.write_text(json.dumps({**GOOD_WATCH, 'crystal': 'glass'}), encoding='utf-8')
    assert main(['check', 'watch', str(source)]) == 1
    assert 'crystal' in capsys.readouterr().out
    assert main(['template', 'watch']) == 0
    assert main(['check', 'nope', str(source)]) == 2
