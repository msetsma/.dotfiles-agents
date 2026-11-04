"""Validate watch/brand/movement frontmatter against ``schema.toml``, or print the Watch template.

Usage:
    python check_frontmatter.py check <watch|brand|movement> <frontmatter.json | ->
    python check_frontmatter.py template watch

``check`` prints one problem per line and exits 1 when any exist, else prints
``ok``. MCP-owned keys (type, status, dates, tags, related, source, qmd) are
ignored. ``template`` prints the frontmatter block for ``Watch.md`` so the
template and the schema cannot drift.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any


__all__ = ['SchemaError', 'check', 'load_schema', 'main', 'render_template']

SCHEMA_PATH = Path(__file__).resolve().parent.parent / 'schema.toml'
MCP_OWNED = frozenset({'type', 'status', 'created', 'updated', 'tags', 'related', 'source', 'qmd'})
_LINK = re.compile(r'^\[\[[^\[\]|]+(\|[^\[\]]+)?\]\]$')
_MONTH = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')
_DATE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$')
_URL = re.compile(r'^https?://\S+$')


class SchemaError(Exception):
    """The schema file is missing, malformed, or names an unknown kind."""


def load_schema(path: Path = SCHEMA_PATH) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding='utf-8'))['kinds']
    except (OSError, tomllib.TOMLDecodeError, KeyError) as exc:
        raise SchemaError(f'cannot load schema {path}: {exc}') from exc


def _kind(schema: Mapping[str, Any], kind: str) -> Mapping[str, Any]:
    if kind not in schema:
        raise SchemaError(f'unknown kind {kind!r}; expected one of {", ".join(sorted(schema))}')
    return schema[kind]


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _pattern(regex: re.Pattern[str]) -> Callable[[Any], bool]:
    return lambda value: isinstance(value, str) and bool(regex.match(value))


_SCALAR_CHECKS: dict[str, tuple[Callable[[Any], bool], str]] = {
    'str': (lambda value: isinstance(value, str) and bool(value.strip()), 'a non-empty string'),
    'int': (_is_int, 'an integer'),
    'number': (_is_number, 'a number (no units)'),
    'bool': (lambda value: isinstance(value, bool), 'true or false'),
    'link': (_pattern(_LINK), 'a wikilink like "[[Name]]"'),
    'month': (_pattern(_MONTH), 'YYYY-MM'),
    'date': (_pattern(_DATE), 'YYYY-MM-DD'),
    'url': (_pattern(_URL), 'an http(s) URL'),
}


def _check_value(key: str, value: Any, spec: Mapping[str, Any]) -> list[str]:
    if value is None:
        return []
    kind = spec['type']
    allowed = spec.get('values', [])
    if kind == 'enum':
        return [] if value in allowed else [f'{key}: {value!r} is not one of {", ".join(map(str, allowed))}']
    if kind == 'list':
        if not isinstance(value, list):
            return [f'{key}: must be a list']
        return [f'{key}: {item!r} is not one of {", ".join(allowed)}' for item in value if item not in allowed]
    test, expected = _SCALAR_CHECKS[kind]
    if not test(value):
        return [f'{key}: {value!r} must be {expected}']
    low, high = spec.get('min'), spec.get('max')
    if (low is not None and value < low) or (high is not None and value > high):
        return [f'{key}: {value} must be between {low} and {high}']
    return []


def check(kind: str, frontmatter: Mapping[str, Any], schema: Mapping[str, Any]) -> list[str]:
    """Every problem with ``frontmatter`` for ``kind``; empty when it conforms."""
    fields = _kind(schema, kind)['fields']
    problems = [
        f'{key}: unknown key (not in schema.toml)' for key in frontmatter if key not in fields and key not in MCP_OWNED
    ]
    for key, value in frontmatter.items():
        if key in fields:
            problems.extend(_check_value(key, value, fields[key]))
    if kind == 'watch' and frontmatter.get('bracelet_links') is not None and frontmatter.get('strap_type') != 'bracelet':
        problems.append('bracelet_links: set only when strap_type is bracelet')
    return problems


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, bool):
        return ' true' if value else ' false'
    return f' {value}'


def render_template(kind: str, schema: Mapping[str, Any]) -> str:
    """The template frontmatter: MCP-owned keys, then the schema keys in order (defaults or null)."""
    spec = _kind(schema, kind)
    defaults = spec.get('defaults', {})
    lines = ['---', f'type: {kind}', 'status: active', 'created: "{{date:YYYY-MM-DD}}"', 'updated: "{{date:YYYY-MM-DD}}"']
    lines += [f'{key}:{_yaml_scalar(defaults.get(key))}' for key in spec['order']]
    lines += ['tags: []', 'related: []', 'source: []', 'qmd:', '  metadata:', f'    type: {kind}', '    status: active']
    lines += ['    tags: []', '---']
    return '\n'.join(lines) + '\n'


def _read_json(arg: str) -> Mapping[str, Any]:
    text = sys.stdin.read() if arg == '-' else Path(arg).read_text(encoding='utf-8')
    data = json.loads(text)
    if not isinstance(data, dict):
        raise SchemaError('frontmatter JSON must be an object')
    return data


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        schema = load_schema()
        if len(args) == 2 and args[0] == 'template':
            sys.stdout.write(render_template(args[1], schema))
            return 0
        if len(args) == 3 and args[0] == 'check':
            problems = check(args[1], _read_json(args[2]), schema)
            sys.stdout.write('\n'.join(problems) + '\n' if problems else 'ok\n')
            return 1 if problems else 0
    except (SchemaError, OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f'error: {exc}\n')
        return 2
    sys.stderr.write(__doc__ or '')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
