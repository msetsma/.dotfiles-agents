"""jsonc - strip // and /* */ comments from JSONC without touching strings."""

from __future__ import annotations


__all__ = ['strip_jsonc']


def _consume_string(text: str, start: int) -> tuple[str, int]:
    """Return the quoted literal at ``start`` and the index just past it."""
    i = start + 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '\\':
            i += 2
            continue
        if ch == '"':
            return text[start : i + 1], i + 1
        i += 1
    return text[start:n], n


def _skip_comment(text: str, start: int) -> int:
    """Skip a ``//`` or ``/* */`` comment and return the index just past it."""
    n = len(text)
    if text[start + 1] == '/':
        i = start
        while i < n and text[i] not in '\r\n':
            i += 1
        return i
    i = start + 2
    while i + 1 < n and not (text[i] == '*' and text[i + 1] == '/'):
        i += 1
    return i + 2


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments while respecting string literals.

    A naive regex would eat the `//` in `https://...`, so scan character by
    character and only strip comments outside of quoted strings.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            literal, i = _consume_string(text, i)
            out.append(literal)
            continue
        if ch == '/' and i + 1 < n and text[i + 1] in '/*':
            i = _skip_comment(text, i)
            continue
        out.append(ch)
        i += 1
    return ''.join(out)
