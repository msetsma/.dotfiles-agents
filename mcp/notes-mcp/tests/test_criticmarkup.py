"""Tests for the CriticMarkup inline helpers."""

from __future__ import annotations

from notes_mcp import criticmarkup


def test_substitution() -> None:
    assert criticmarkup.substitution('old', 'new') == '{~~old~>new~~}'


def test_addition() -> None:
    assert criticmarkup.addition('text') == '{++text++}'


def test_deletion() -> None:
    assert criticmarkup.deletion('text') == '{--text--}'


def test_comment() -> None:
    assert criticmarkup.comment('text') == '{>>text<<}'


def test_has_markup_detects_each_opener() -> None:
    assert criticmarkup.has_markup('a {++b++} c')
    assert criticmarkup.has_markup('{~~a~>b~~}')
    assert criticmarkup.has_markup('{--x--}')
    assert criticmarkup.has_markup('{>>note<<}')


def test_has_markup_plain_text() -> None:
    assert not criticmarkup.has_markup('just plain text')
    assert not criticmarkup.has_markup('')
