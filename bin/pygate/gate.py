"""One gate run on one file, reporting only what the change introduced.

Phases:
  edit  ruff and the source checks (GATE001, GATE002, GATE005, GATE006): file
        local and fast, run after every write.
  full  edit plus vulture (GATE003) and ty (GATE004), which read the whole
        project; run by the Stop hook and at commit time.

The baseline is the file at git HEAD (pygate/baseline.py). With one, file-local
findings are ratcheted by fingerprint and project findings kept only on changed
lines (pygate/ratchet.py); without one every finding is new.

Auto-fix never rewrites legacy code: `ruff check --fix` runs only when the
baseline is absent or has no ruff findings, and `ruff format` only when it is
absent or already formatted. The commit-time format check (GATE007) follows the
same rule.
"""

import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pygate.baseline import Committed, changed_lines, committed
from pygate.dead_code import Scope, check_dead_code
from pygate.findings import Finding
from pygate.ratchet import introduced, on_lines
from pygate.ruff import Ruff, fix_file, format_file, is_formatted, lint
from pygate.source_checks import check_source
from pygate.type_check import check_types


__all__ = ['Phase', 'Request', 'run_gate']


class Phase(StrEnum):
    EDIT = 'edit'
    FULL = 'full'


@dataclass(frozen=True)
class Request:
    """What to check and how: the file's scope, its ruff, the phase, and whether to fix or check formatting."""

    scope: Scope
    ruff: Ruff
    phase: Phase
    fix: bool
    format_check: bool


@dataclass(frozen=True)
class Baseline:
    """The file at HEAD and ruff's findings on it."""

    source: Committed
    ruff: tuple[Finding, ...]

    @property
    def text(self) -> str:
        return self.source.text


def _baseline(ruff: Ruff, path: Path) -> Baseline | None:
    source = committed(path)
    return None if source is None else Baseline(source, tuple(lint(ruff, path, source.text)))


def _formatted(ruff: Ruff, path: Path, base: Baseline | None) -> bool:
    return base is None or is_formatted(ruff, path, base.text)


def _fix(ruff: Ruff, path: Path, base: Baseline | None) -> None:
    if base is None or not base.ruff:
        fix_file(ruff, path)
    if _formatted(ruff, path, base):
        format_file(ruff, path)


def _local(request: Request, text: str, base: Baseline | None) -> list[Finding]:
    path = request.scope.path
    current = [*lint(request.ruff, path, text), *check_source(path, text)]
    if base is None:
        return current
    return introduced(current, text, [*base.ruff, *check_source(path, base.text)], base.text)


def _format_findings(request: Request, text: str, base: Baseline | None) -> list[Finding]:
    ruff, path = request.ruff, request.scope.path
    if not request.format_check or not _formatted(ruff, path, base) or is_formatted(ruff, path, text):
        return []
    return [Finding(path, 1, 1, 'GATE007', f'not formatted; run: ruff format {path.name}')]


def _project(scope: Scope, text: str, base: Baseline | None) -> list[Finding]:
    try:
        tree = ast.parse(text, filename=str(scope.path))
    except SyntaxError:
        return []  # ruff reports the syntax error itself
    with ThreadPoolExecutor(max_workers=2) as pool:
        dead = pool.submit(check_dead_code, scope, tree)
        types = pool.submit(check_types, scope.path, scope.package)
        found = [*dead.result(), *types.result()]
    if base is None or not found:
        return found
    return on_lines(found, changed_lines(scope.path, base.source))


def run_gate(request: Request) -> list[Finding]:
    """The findings this change introduced, file-local ones first. Raises OSError if the file is unreadable."""
    path = request.scope.path
    path.read_text(encoding='utf-8', newline='')
    base = _baseline(request.ruff, path)
    if request.fix:
        _fix(request.ruff, path, base)
    # newline='' keeps CRLF, so the format check sees the file's real line endings.
    text = path.read_text(encoding='utf-8', newline='')
    project = _project(request.scope, text, base) if request.phase is Phase.FULL else []
    return [*_local(request, text, base), *_format_findings(request, text, base), *project]
