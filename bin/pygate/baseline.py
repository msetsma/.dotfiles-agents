"""The committed version of a file: what the ratchet compares an edit against.

The baseline is the file's content at git HEAD. A file renamed or moved with
`git mv` (staged or not yet committed) takes the content of its old path, so a
rename alone introduces nothing. A file outside a repository, in a repository
with no commits, or not yet committed under any name has no baseline, so every
finding in it is new.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from pygate.tools import run_git


__all__ = ['Committed', 'changed_lines', 'committed']

# `@@ -a[,b] +c[,d] @@`: the new file's hunk starts at line c and spans d lines.
HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@')


@dataclass(frozen=True)
class Committed:
    """A file's content at HEAD; `renamed_from` is its old repository-relative path after a rename."""

    text: str
    renamed_from: str | None = None


def _rename_source(path: Path) -> str | None:
    """The HEAD path `path` was renamed from, per `git diff -M HEAD` (index and working tree)."""
    prefix = run_git(path.parent, 'rev-parse', '--show-prefix')
    if prefix is None:
        return None
    current = prefix.strip() + path.name
    status = run_git(path.parent, 'diff', '-M', '--name-status', '-z', '--diff-filter=R', 'HEAD')
    fields = (status or '').split('\0')
    # -z records: "R<score>\0old\0new\0".
    for index in range(0, len(fields) - 2, 3):
        if fields[index].startswith('R') and fields[index + 2] == current:
            return fields[index + 1]
    return None


def committed(path: Path) -> Committed | None:
    """`path` as committed at HEAD (under its old name after a rename), or None when HEAD has no version of it."""
    text = run_git(path.parent, 'show', f'HEAD:./{path.name}')
    if text is not None:
        return Committed(text)
    old = _rename_source(path)
    if old is None:
        return None
    text = run_git(path.parent, 'show', f'HEAD:{old}')
    return None if text is None else Committed(text, old)


def _hunk_lines(diff: str) -> Iterator[int]:
    for raw in diff.splitlines():
        match = HUNK.match(raw)
        if match is None:
            continue
        start, count = int(match['start']), int(match['count'] or 1)
        # A pure deletion (count 0) sits between line `start` and the next one.
        yield from range(start, start + count) if count else (start, start + 1)


def changed_lines(path: Path, base: Committed) -> frozenset[int] | None:
    """Lines of the working-tree file that differ from `base`; None when git cannot tell (count all)."""
    specs = [f':(literal){path.name}']
    if base.renamed_from is not None:
        specs.append(f':(top,literal){base.renamed_from}')
    diff = run_git(path.parent, 'diff', '--no-color', '--no-ext-diff', '-M', '-U0', 'HEAD', '--', *specs)
    return None if diff is None else frozenset(_hunk_lines(diff))
