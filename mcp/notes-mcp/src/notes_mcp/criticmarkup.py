"""CriticMarkup inline syntax helpers (Phase 3, W2).

Tiny, dependency-free builders for the four inline CriticMarkup constructs plus
a detector. Suggestion mode wraps proposed changes with these so a human can
accept or reject them in an editor; nothing here parses or strips markup.
"""

from __future__ import annotations


def substitution(old: str, new: str) -> str:
    """Render a substitution: ``{~~old~>new~~}``."""
    return f'{{~~{old}~>{new}~~}}'


def addition(text: str) -> str:
    """Render an addition: ``{++text++}``."""
    return f'{{++{text}++}}'


def deletion(text: str) -> str:
    """Render a deletion: ``{--text--}``."""
    return f'{{--{text}--}}'


def comment(text: str) -> str:
    """Render a comment: ``{>>text<<}``."""
    return f'{{>>{text}<<}}'


_MARKERS = ('{++', '{--', '{~~', '{>>')


def has_markup(text: str) -> bool:
    """Whether ``text`` contains any inline CriticMarkup opener."""
    return any(marker in text for marker in _MARKERS)
