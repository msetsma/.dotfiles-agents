"""GATE004: type errors from ty."""

import logging
import re
from pathlib import Path

from pygate.findings import Finding
from pygate.tools import run_tool, tool_argv


__all__ = ['check_types']

log = logging.getLogger(__name__)

TY_LINE = re.compile(r'^.+?:(?P<line>\d+):(?P<col>\d+): error\[(?P<rule>[\w-]+)\] (?P<message>.*)$')


def check_types(path: Path, package: Path) -> list[Finding]:
    """Errors only, run from the package directory so ty finds that package's `.venv`.

    Unresolved imports are ignored: the gate cannot know which environment the
    project installs into.
    """
    tool = tool_argv('ty')
    if tool is None:
        log.warning('python_gate: ty unavailable (no ty or uvx on PATH); type check skipped')
        return []
    argv = [*tool, 'check', '--output-format', 'concise', '--ignore', 'unresolved-import', str(path)]
    output = run_tool(argv, package)
    if output is None:
        return []
    findings = []
    for raw in output.splitlines():
        match = TY_LINE.match(raw.strip())
        if match:
            message = f'{match["rule"]}: {match["message"]}'
            findings.append(Finding(path, int(match['line']), int(match['col']), 'GATE004', message))
    return findings
