"""Checks ruff cannot express, run by bin/clean-python after ruff on each file.

Usage:
  python_gate.py <file> <package-dir> <scan-root>
  python_gate.py --session-changes <transcript> <cwd>

The first form writes one finding per line as `path:line:col: CODE message`:

  GATE001  file longer than 800 lines
  GATE002  @dataclass without frozen=True; mark a deliberate one with
           `# gate: mutable: <why>` on the decorator or class line
  GATE003  dead code: vulture finds no use outside tests (pygate/dead_code.py)
  GATE004  type error reported by ty (unresolved imports ignored)
  GATE005  type-check suppression comment (`# type: ignore`, `# ty: ignore`)
  GATE006  broad except, or suppress(Exception), that does not re-raise

<package-dir> is the nearest pyproject.toml directory (ty runs there and reads
its entry points); <scan-root> is the git root vulture scans for callers.

The second form prints the Python files changed during a Claude Code session
(see pygate/session.py), one per line, for the Stop hook.

Exit status: 0 clean, 1 findings, 2 usage error, 3 the file could not be read.
A crash exits 1 with nothing on stdout, which bin/clean-python reports as a
skipped check, never as findings. vulture and ty come from PATH or `uvx`; a
check whose tool is unavailable is skipped with a warning on stderr.
"""

import ast
import io
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from pygate.dead_code import Scope, check_dead_code
from pygate.findings import Finding
from pygate.session import changed_python_files, session_start
from pygate.source_checks import check_dataclasses, check_length, check_suppressions, check_swallowed_errors
from pygate.type_check import check_types


__all__ = ['main', 'run_checks']

log = logging.getLogger('python_gate')

USAGE = 'usage: python_gate.py <file> <package-dir> <scan-root> | --session-changes <transcript> <cwd>'
EXIT_FINDINGS, EXIT_USAGE, EXIT_UNREADABLE = 1, 2, 3


def run_checks(scope: Scope) -> list[Finding]:
    path = scope.path
    source = path.read_text(encoding='utf-8')
    found = [*check_length(path, source), *check_suppressions(path, source)]
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return found  # ruff reports the syntax error itself
    return [
        *found,
        *check_dataclasses(path, tree, source.splitlines()),
        *check_swallowed_errors(path, tree),
        *check_dead_code(scope, tree),
        *check_types(path, scope.package),
    ]


def _session_changes(transcript: str, cwd: str) -> int:
    start = session_start(Path(transcript))
    if start is not None:
        sys.stdout.write(''.join(f'{p}\n' for p in changed_python_files(Path(cwd), start)))
    return 0


def _check(file: str, package: str, scan_root: str) -> int:
    scope = Scope(Path(file).resolve(), Path(package).resolve(), Path(scan_root).resolve())
    try:
        findings = run_checks(scope)
    except (OSError, UnicodeDecodeError) as exc:
        log.warning('python_gate: cannot read %s: %s', file, exc)
        return EXIT_UNREADABLE
    sys.stdout.write(''.join(f'{f.render()}\n' for f in findings))
    return EXIT_FINDINGS if findings else 0


def main(argv: Sequence[str]) -> int:
    # Windows consoles default to cp1252; findings carry arbitrary paths.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding='utf-8', errors='replace')
    logging.basicConfig(format='%(message)s', level=logging.WARNING, stream=sys.stderr)
    if len(argv) == len(('flag', 'transcript', 'cwd')) and argv[0] == '--session-changes':
        return _session_changes(argv[1], argv[2])
    if len(argv) != len(('file', 'package', 'scan_root')) or argv[0].startswith('-'):
        log.error(USAGE)
        return EXIT_USAGE
    return _check(*argv)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
