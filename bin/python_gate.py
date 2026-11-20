"""The Python gate on one file, run by bin/clean-python: ruff plus the checks ruff cannot express.

Usage:
  python_gate.py <file> <package-dir> <scan-root> --phase edit|full --ruff <ruff>
                 [--config <ruff-config>] [--fix] [--format-check]
  python_gate.py --session-changes <transcript> <cwd>

The first form writes one finding per line as `path:line:col: CODE message`:
ruff's rules, then

  GATE001  file longer than 800 lines
  GATE002  @dataclass without frozen=True; mark a deliberate one with
           `# gate: mutable: <why>` on the decorator or class line
  GATE003  dead code: vulture finds no use outside tests (pygate/dead_code.py)
  GATE004  type error reported by ty (unresolved imports ignored)
  GATE005  type-check suppression comment (`# type: ignore`, `# ty: ignore`)
  GATE006  broad except, or suppress(Exception), that does not re-raise
  GATE007  not formatted (only with --format-check)

Only findings the change introduced are reported: pre-existing ones in the
file's committed version (git HEAD) are not (pygate/gate.py, pygate/ratchet.py).
`--phase edit` runs ruff and the file-local checks; `--phase full` adds
vulture (GATE003) and ty (GATE004). `--fix` applies ruff's fixes and format
first, unless that would rewrite legacy code. Without `--config`, ruff runs
`--isolated`.

<package-dir> is the nearest pyproject.toml directory (ruff and ty run there);
<scan-root> is the git root vulture scans for callers.

The second form prints the Python files changed during a Claude Code session
(see pygate/session.py), one per line, for the Stop hook.

Exit status: 0 clean, 1 findings, 2 usage error, 3 the file could not be read,
4 ruff failed. A crash exits 1 with nothing on stdout; bin/clean-python treats
every outcome but findings as a skipped check, never a block. vulture and ty
come from PATH or `uvx`; a check whose tool is unavailable is skipped with a
warning on stderr.
"""

import argparse
import io
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from pygate.dead_code import Scope
from pygate.errors import GateError
from pygate.gate import Phase, Request, run_gate
from pygate.ruff import Ruff
from pygate.session import changed_python_files, session_start


__all__ = ['main']

log = logging.getLogger('python_gate')

EXIT_FINDINGS, EXIT_UNREADABLE, EXIT_TOOL_FAILED = 1, 3, 4


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='python_gate.py', description='The Python gate on one file.')
    parser.add_argument('file', type=Path)
    parser.add_argument('package', type=Path)
    parser.add_argument('scan_root', type=Path)
    parser.add_argument('--phase', type=Phase, choices=list(Phase), required=True)
    parser.add_argument('--ruff', required=True)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--fix', action='store_true')
    parser.add_argument('--format-check', action='store_true')
    return parser


def _request(args: argparse.Namespace) -> Request:
    scope = Scope(args.file.resolve(), args.package.resolve(), args.scan_root.resolve())
    ruff = Ruff(args.ruff, scope.package, args.config)
    return Request(scope, ruff, args.phase, fix=args.fix, format_check=args.format_check)


def _check(request: Request) -> int:
    try:
        findings = run_gate(request)
    except (OSError, UnicodeDecodeError) as exc:
        log.warning('python_gate: cannot read %s: %s', request.scope.path, exc)
        return EXIT_UNREADABLE
    except GateError as exc:
        log.warning('python_gate: %s', exc)
        return EXIT_TOOL_FAILED
    sys.stdout.write(''.join(f'{f.render()}\n' for f in findings))
    return EXIT_FINDINGS if findings else 0


def _session_changes(transcript: str, cwd: str) -> int:
    start = session_start(Path(transcript))
    if start is not None:
        sys.stdout.write(''.join(f'{p}\n' for p in changed_python_files(Path(cwd), start)))
    return 0


def main(argv: Sequence[str]) -> int:
    # Windows consoles default to cp1252; findings carry arbitrary paths. A
    # plain '\n' (no CRLF on Windows) keeps the report byte-identical across
    # platforms, which the waiver digests in bin/clean-python depend on.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding='utf-8', errors='replace', newline='\n')
    logging.basicConfig(format='%(message)s', level=logging.WARNING, stream=sys.stderr)
    if len(argv) == len(('flag', 'transcript', 'cwd')) and argv[0] == '--session-changes':
        return _session_changes(argv[1], argv[2])
    return _check(_request(_parser().parse_args(argv)))  # parse_args exits 2 on a usage error


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
