"""YAML frontmatter parsing, validation, and canonical construction.

A note is ``---\\n<yaml>---\\n<body>``. Parsing preserves key order and unknown
keys so a parse/serialize round-trip is semantically lossless. The ``qmd``
block is MCP-maintained and mirrored from the user-editable fields on every
write.
"""

from __future__ import annotations

import datetime
from typing import Any

import yaml

from .errors import INVALID_FRONTMATTER, NotesError


REQUIRED_KEYS = ('type', 'status', 'created', 'updated', 'tags', 'related', 'source')
HUB_REQUIRED_KEYS = (*REQUIRED_KEYS, 'scope')

_STRING_KEYS = ('type', 'status', 'created', 'updated')
_LIST_KEYS = ('tags', 'related', 'source')

_FENCE = '---'


def parse(text: str) -> tuple[dict[str, Any], str]:
    """Split ``text`` into ``(frontmatter, body)``.

    Missing or unparseable frontmatter raises ``INVALID_FRONTMATTER``.
    """
    lines = text.split('\n')
    if not lines or lines[0].strip() != _FENCE:
        raise _invalid('note has no YAML frontmatter')

    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == _FENCE:
            end = index
            break
    if end is None:
        raise _invalid('frontmatter is not closed with ---')

    block = '\n'.join(lines[1:end])
    body = '\n'.join(lines[end + 1 :])

    try:
        loaded = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise _invalid(f'frontmatter is not valid YAML: {exc}') from exc

    if not isinstance(loaded, dict):
        raise _invalid('frontmatter is not a YAML mapping')

    return normalize_scalars(loaded), body


def normalize_scalars(value: Any) -> Any:
    """Recursively coerce ``date``/``datetime`` scalars to ISO strings.

    ``yaml.safe_load`` turns unquoted ISO dates into ``datetime.date`` /
    ``datetime.datetime`` objects, which ``validate`` rejects and JSON cannot
    return. Nested dicts and lists (including the ``qmd`` block) are walked;
    every other value is returned untouched.

    ``datetime.datetime`` subclasses ``datetime.date``, so a single ``date``
    check covers both and ``.isoformat()`` keeps the time component when set.
    """
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: normalize_scalars(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_scalars(item) for item in value]
    return value


class _NoAliasDumper(yaml.SafeDumper):
    """Render repeated objects inline instead of YAML anchors and aliases.

    The ``qmd`` mirror shares values with the user-editable fields, so PyYAML
    would otherwise emit ``&id001`` / ``*id001``. That is valid YAML but noisy
    and confusing to hand-edit.
    """

    def ignore_aliases(self, data: Any) -> bool:
        return True


def serialize(fm: dict[str, Any], body: str) -> str:
    """Render ``fm`` and ``body`` back into note text."""
    block = yaml.dump(fm, Dumper=_NoAliasDumper, sort_keys=False, allow_unicode=True)
    return f'{_FENCE}\n{block}{_FENCE}\n{body}'


def validate(fm: dict[str, Any], *, hub: bool = False) -> None:
    """Check required keys and basic value types for a note's frontmatter."""
    required = HUB_REQUIRED_KEYS if hub else REQUIRED_KEYS
    missing = [key for key in required if key not in fm]
    if missing:
        raise _invalid(f'frontmatter is missing required keys: {", ".join(missing)}')

    for key in _STRING_KEYS:
        if not isinstance(fm[key], str):
            raise _invalid(f'frontmatter key {key!r} must be a string')
    for key in _LIST_KEYS:
        if not isinstance(fm[key], list):
            raise _invalid(f'frontmatter key {key!r} must be a list')
    if hub and not isinstance(fm['scope'], str):
        raise _invalid("frontmatter key 'scope' must be a string")


def sync_qmd_metadata(fm: dict[str, Any]) -> dict[str, Any]:
    """Mirror ``{type, status, tags}`` into ``fm['qmd']['metadata']``.

    Other keys inside the ``qmd`` block are preserved. Mutates and returns
    ``fm``.
    """
    qmd = fm.get('qmd')
    if not isinstance(qmd, dict):
        qmd = {}
    tags = fm.get('tags')
    qmd['metadata'] = {
        'type': fm.get('type'),
        'status': fm.get('status'),
        'tags': list(tags) if isinstance(tags, list) else tags,
    }
    fm['qmd'] = qmd
    return fm


def new_frontmatter(*, status: str, today: str, **fields: str) -> dict[str, Any]:
    """Build canonical frontmatter with keys in contract order.

    ``type`` arrives through ``**fields`` so the parameter name does not shadow
    the builtin ``type`` (ruff A002); the keyword call convention is unchanged.
    """
    if 'type' not in fields:
        raise _invalid("new_frontmatter requires a 'type' keyword argument")
    return {
        'type': fields['type'],
        'status': status,
        'created': today,
        'updated': today,
        'tags': [],
        'related': [],
        'source': [],
    }


def _invalid(message: str) -> NotesError:
    return NotesError(INVALID_FRONTMATTER, message, 'Provide valid YAML frontmatter with the required keys.')
