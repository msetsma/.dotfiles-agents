"""Checks that need only the file itself: length, dataclasses, suppressions, swallowed errors."""

import ast
import io
import re
import tokenize
from collections.abc import Iterable, Sequence
from pathlib import Path

from pygate.findings import Finding


__all__ = [
    'MAX_LINES',
    'MUTABLE_MARKER',
    'check_dataclasses',
    'check_length',
    'check_source',
    'check_suppressions',
    'check_swallowed_errors',
]

MAX_LINES = 800
MUTABLE_MARKER = '# gate: mutable'
SUPPRESSION = re.compile(r'#\s*(type|ty|pyright|mypy)\s*:\s*ignore\b')
BROAD_ERRORS = frozenset({'Exception', 'BaseException'})


def check_length(path: Path, source: str) -> list[Finding]:
    count = len(source.splitlines())
    if count <= MAX_LINES:
        return []
    return [Finding(path, MAX_LINES + 1, 1, 'GATE001', f'File has {count} lines (max {MAX_LINES}); split it')]


def _name(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute):
        return target.attr
    return target.id if isinstance(target, ast.Name) else ''


def _is_frozen(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    return any(
        kw.arg == 'frozen' and isinstance(kw.value, ast.Constant) and kw.value.value is True for kw in node.keywords
    )


def _marked_mutable(lines: Sequence[str], *linenos: int) -> bool:
    return any(MUTABLE_MARKER in lines[n - 1] for n in linenos if 0 < n <= len(lines))


def check_dataclasses(path: Path, tree: ast.Module, lines: Sequence[str]) -> list[Finding]:
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for deco in node.decorator_list:
            if _name(deco) != 'dataclass' or _is_frozen(deco):
                continue
            if _marked_mutable(lines, deco.lineno, node.lineno):
                continue
            message = f'@dataclass `{node.name}` is mutable; use frozen=True or mark it `{MUTABLE_MARKER}: <why>`'
            findings.append(Finding(path, deco.lineno, deco.col_offset + 1, 'GATE002', message))
    return findings


def check_suppressions(path: Path, source: str) -> list[Finding]:
    """`# type: ignore` and friends silence the type check the same way `# noqa` would silence ruff."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenError, SyntaxError:
        return []
    message = 'type-check suppression comment; fix the type error instead'
    return [
        Finding(path, tok.start[0], tok.start[1] + 1, 'GATE005', message)
        for tok in tokens
        if tok.type == tokenize.COMMENT and SUPPRESSION.search(tok.string)
    ]


def _is_broad(node: ast.expr | None) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Tuple):
        return any(_is_broad(elt) for elt in node.elts)
    return _name(node) in BROAD_ERRORS


def _reraises(handler: ast.ExceptHandler) -> bool:
    return any(isinstance(node, ast.Raise) for stmt in handler.body for node in ast.walk(stmt))


def _swallowing_handlers(tree: ast.Module) -> Iterable[ast.ExceptHandler]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and _is_broad(node.type) and not _reraises(node):
            yield node


def _broad_suppress(tree: ast.Module) -> Iterable[ast.Call]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _name(node) == 'suppress' and any(_is_broad(arg) for arg in node.args):
            yield node


def check_swallowed_errors(path: Path, tree: ast.Module) -> list[Finding]:
    """A broad except (or suppress(Exception)) that does not re-raise hides every failure."""
    handlers = [
        Finding(
            path,
            h.lineno,
            h.col_offset + 1,
            'GATE006',
            'broad except swallows the error; catch the specific exception or re-raise',
        )
        for h in _swallowing_handlers(tree)
    ]
    suppressors = [
        Finding(
            path,
            c.lineno,
            c.col_offset + 1,
            'GATE006',
            'suppress(Exception) swallows every error; name the specific exception',
        )
        for c in _broad_suppress(tree)
    ]
    return handlers + suppressors


def check_source(path: Path, source: str) -> list[Finding]:
    """Every check above on `source` as the content of `path`."""
    found = [*check_length(path, source), *check_suppressions(path, source)]
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return found  # ruff reports the syntax error itself
    return [*found, *check_dataclasses(path, tree, source.splitlines()), *check_swallowed_errors(path, tree)]
