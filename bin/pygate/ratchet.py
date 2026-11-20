"""Keep only the findings a change introduced, so legacy code does not block an edit.

File-local findings (ruff and the source checks) are matched against the same
checks run on the baseline by fingerprint: the code, the message and the
stripped text of the line the finding points at. Fingerprints form a multiset,
so a finding is new once the file has more of its fingerprint than the
baseline did. A file-level finding (FILE_LEVEL) fingerprints by code alone: a
legacy file already over the length limit is not re-reported, but crossing the
limit is.

Messages are normalized before fingerprinting: a line number inside a message
("Redefinition of unused `f` from line 4") becomes `line N`, so an edit above it
does not make the legacy finding look new, and a metric's count ("Too many
statements (57 > 50)") is dropped, so growing a function that was already over
a limit is not reported; only crossing the limit is.

Project findings (vulture, ty) depend on other files, so they are kept only on
lines the change touched.
"""

import re
from collections import Counter
from collections.abc import Sequence

from pygate.findings import Finding


__all__ = ['FILE_LEVEL', 'introduced', 'on_lines']

FILE_LEVEL = frozenset({'GATE001'})

type Fingerprint = tuple[str, str, str]

LINE_REFERENCE = re.compile(r'\bline \d+\b')
METRIC_COUNT = re.compile(r'\s*\(\d+ > \d+\)')


def _normalized(message: str) -> str:
    return METRIC_COUNT.sub('', LINE_REFERENCE.sub('line N', message))


def _fingerprint(finding: Finding, lines: Sequence[str]) -> Fingerprint:
    if finding.code in FILE_LEVEL:
        return (finding.code, '', '')
    text = lines[finding.line - 1].strip() if 0 < finding.line <= len(lines) else ''
    return (finding.code, _normalized(finding.message), text)


def introduced(
    current: Sequence[Finding], current_text: str, baseline: Sequence[Finding], baseline_text: str
) -> list[Finding]:
    """The findings of `current` beyond the count of their fingerprint in `baseline`."""
    current_lines, baseline_lines = current_text.splitlines(), baseline_text.splitlines()
    allowance = Counter(_fingerprint(f, baseline_lines) for f in baseline)
    seen: Counter[Fingerprint] = Counter()
    new = []
    for finding in current:
        key = _fingerprint(finding, current_lines)
        seen[key] += 1
        if seen[key] > allowance[key]:
            new.append(finding)
    return new


def on_lines(findings: Sequence[Finding], lines: frozenset[int] | None) -> list[Finding]:
    """The findings on one of `lines`; None counts every line."""
    if lines is None:
        return list(findings)
    return [f for f in findings if f.line in lines]
