"""GATE003: production code that nothing outside tests uses, found with vulture.

vulture scans the whole git project (so callers in sibling packages count) with
tests excluded, and only findings in the edited file are reported. Things a
framework or the interpreter calls are not dead, so these are dropped:

- functions, methods and classes with a decorator other than the plain ones in
  PLAIN_DECORATORS (a decorator such as `@mcp.tool` or `@field_validator`
  registers the function with whoever calls it);
- methods of a class whose base comes from another module (overrides such as
  `do_GET` or `JSONEncoder.default` are called by the base class);
- entry points named in pyproject.toml and pytest hooks (`pytest_*`);
- files read by convention (conf.py, settings, migrations, setup.py, ...).

Anything else a framework calls can be listed in a module that uses the names,
such as `vulture_whitelist.py` at the project root.
"""

import ast
import logging
import re
import tomllib
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from pygate.findings import Finding
from pygate.tools import run_tool, tool_argv


__all__ = ['Scope', 'check_dead_code', 'is_convention_file', 'is_test_file']

log = logging.getLogger(__name__)

# Paths that are not production code. vulture matches --exclude globs against
# absolute paths and `*` crosses directories, so each is anchored to the scan
# root (see _exclude_patterns): a parent directory named `tests` or `vendor`
# must not hide the whole project.
NOT_PRODUCTION = (
    'tests/*',
    'test/*',
    'test_*.py',
    '*_test.py',
    'conftest.py',
    '.venv/*',
    'venv/*',
    'vendor/*',
    'node_modules/*',
    'site-packages/*',
    'build/*',
    'dist/*',
    '.git/*',
)
CONVENTION_FILES = frozenset(
    {'conf.py', 'setup.py', 'manage.py', 'noxfile.py', 'fabfile.py', 'wsgi.py', 'asgi.py', 'gunicorn.conf.py'}
)
CONVENTION_DIRS = frozenset({'migrations', 'alembic'})
PLAIN_DECORATORS = frozenset(
    {
        'abstractmethod',
        'asynccontextmanager',
        'cache',
        'cached_property',
        'classmethod',
        'contextmanager',
        'dataclass',
        'final',
        'lru_cache',
        'overload',
        'override',
        'property',
        'singledispatch',
        'staticmethod',
        'total_ordering',
        'wraps',
    }
)
DEAD_KINDS = frozenset({'function', 'method', 'class', 'property'})
VULTURE_LINE = re.compile(r"^(?P<path>.+?):(?P<line>\d+): unused (?P<kind>\w+) '(?P<name>[^']+)'")
HINT = 'delete it, use it, or (if a framework calls it) list it in vulture_whitelist.py at the project root'


@dataclass(frozen=True)
class Scope:
    """Where to look: the edited file, its package (pyproject dir) and the project to scan."""

    path: Path
    package: Path
    scan_root: Path


def is_test_file(path: Path) -> bool:
    name = path.name
    in_test_dir = any(part in {'tests', 'test'} for part in path.parts[:-1])
    return in_test_dir or name.startswith('test_') or name.endswith('_test.py') or name == 'conftest.py'


def is_convention_file(path: Path) -> bool:
    name = path.name
    in_convention_dir = any(part in CONVENTION_DIRS for part in path.parts[:-1])
    return in_convention_dir or name in CONVENTION_FILES or name.startswith('settings')


def _targets(table: Mapping[str, object]) -> Iterator[str]:
    for value in table.values():
        if isinstance(value, str):
            yield value
        elif isinstance(value, Mapping):
            reference = value.get('callable') or value.get('reference')
            if isinstance(reference, str):
                yield reference


def _entry_point_names(package: Path) -> set[str]:
    """Callables named in pyproject.toml: scripts, entry points, poetry scripts."""
    try:
        data = tomllib.loads((package / 'pyproject.toml').read_text(encoding='utf-8'))
    except OSError, tomllib.TOMLDecodeError:
        return set()
    project = data.get('project', {})
    tables = [project.get('scripts', {}), project.get('gui-scripts', {}), *project.get('entry-points', {}).values()]
    tables.append(data.get('tool', {}).get('poetry', {}).get('scripts', {}))
    targets = [t for table in tables if isinstance(table, Mapping) for t in _targets(table)]
    return {t.rpartition(':')[2].split('.')[-1] for t in targets if ':' in t}


def _exclude_patterns(root: Path) -> list[str]:
    base = root.as_posix().rstrip('/')
    return [pattern for rel in NOT_PRODUCTION for pattern in (f'{base}/{rel}', f'{base}/*/{rel}')]


def _decorator_name(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute):
        return target.attr
    return target.id if isinstance(target, ast.Name) else ''


def _imported_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((alias.asname or alias.name).split('.')[0] for alias in node.names)
    return names


def _external_base(node: ast.ClassDef, imported: set[str]) -> bool:
    return any(isinstance(b, ast.Attribute) or (isinstance(b, ast.Name) and b.id in imported) for b in node.bases)


def _definition_lines(node: ast.stmt) -> set[int]:
    decorators = getattr(node, 'decorator_list', [])
    return {node.lineno, *(d.lineno for d in decorators)}


def _exempt_lines(tree: ast.Module) -> set[int]:
    """Lines of definitions that a framework, a base class or a decorator registration calls."""
    imported = _imported_names(tree)
    exempt: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if any(_decorator_name(d) not in PLAIN_DECORATORS for d in node.decorator_list):
            exempt |= _definition_lines(node)
        if isinstance(node, ast.ClassDef) and _external_base(node, imported):
            for item in node.body:
                exempt |= _definition_lines(item)
    return exempt


def _module_level_lines(tree: ast.Module) -> set[int]:
    return {node.lineno for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))}


def _vulture_argv(tool: list[str], scope: Scope) -> list[str]:
    root = scope.scan_root
    names = ['pytest_*', *sorted(_entry_point_names(scope.package))]
    excludes = ','.join(_exclude_patterns(root))
    return [*tool, str(root), '--min-confidence', '60', '--exclude', excludes, '--ignore-names', ','.join(names)]


def check_dead_code(scope: Scope, tree: ast.Module) -> list[Finding]:
    root = scope.scan_root
    relative = scope.path.relative_to(root) if scope.path.is_relative_to(root) else scope.path
    if is_test_file(relative) or is_convention_file(relative):
        return []
    tool = tool_argv('vulture')
    if tool is None:
        log.warning('python_gate: vulture unavailable (no vulture or uvx on PATH); dead-code check skipped')
        return []
    output = run_tool(_vulture_argv(tool, scope), root)
    if output is None:
        return []
    return list(_findings(output.splitlines(), scope, tree))


def _findings(lines: Iterable[str], scope: Scope, tree: ast.Module) -> Iterator[Finding]:
    exempt, module_lines = _exempt_lines(tree), _module_level_lines(tree)
    target = scope.path.resolve()
    for raw in lines:
        match = VULTURE_LINE.match(raw.strip())
        if not match or (scope.scan_root / match['path']).resolve() != target:
            continue
        line, kind = int(match['line']), match['kind']
        if line in exempt:
            continue
        if kind in DEAD_KINDS or (kind == 'variable' and line in module_lines):
            message = f"unused {kind} '{match['name']}': nothing outside tests uses it; {HINT}"
            yield Finding(scope.path, line, 1, 'GATE003', message)
