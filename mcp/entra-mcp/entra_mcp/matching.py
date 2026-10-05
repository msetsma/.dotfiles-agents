"""Fuzzy matching for directory search.

The CLI leans on `fzf` for this: you type part of a name, `fzf` filters the
list live, and you pick. An MCP client can't do that interactively, so the
matching has to happen server-side and be good enough that the agent finds the
right person from a partial or misremembered spelling.

Three tiers, best score wins:

1. **Substring** - the token appears in the field. Prefix beats mid-word.
2. **Subsequence** - the token's characters appear *in order*, gaps allowed.
   This is what `fzf` does, and it is what makes "Xuli" find "Xueli".
3. **Typo tolerance** - character-multiset similarity, which catches
   transpositions ("Setmsa" -> "Setsma") and single wrong letters
   ("Zueli" -> "Xueli"). Cheap: no dynamic programming, and transpositions
   score 1.0 because the letters are identical, just reordered.

Multi-token queries AND together, so "kevin tian" matches "Tian, Xueli (Kevin)"
even though those words are far apart in the string.

Everything runs over the in-memory directory cache, so there is no extra Graph
traffic.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from typing import Any


PREFIX_SCORE = 1.0
WORD_START_SCORE = 0.96
SUBSTRING_SCORE = 0.90
SUBSEQUENCE_CEILING = 0.85
TYPO_CEILING = 0.55

TYPO_MIN_TOKEN = 4
TYPO_MIN_RATIO = 0.6
TYPO_MAX_LENGTH_DELTA = 2
TYPO_PREFILTER_RATIO = 0.4
STRICTNESS_FLOORS = {'strict': 0.90, 'normal': 0.60, 'loose': 0.0}
DEFAULT_STRICTNESS = 'strict'


def normalize(value: str) -> str:
    return value.casefold()


def tokenize(query: str) -> list[str]:
    return [token for token in normalize(query).split() if token]


def _at_word_start(text: str, index: int) -> bool:
    return index == 0 or not text[index - 1].isalnum()


def subsequence_score(token: str, text: str) -> float | None:
    """Score ``token`` as an in-order subsequence of ``text``, or None.

    Weighted by three things, because "characters appear in order" is far too
    generous on its own:

    - **density** - how tightly the matches are packed. "xuli" inside
      "xu e li" is a strong match; the same letters scattered across two
      different words are not.
    - **contiguity** - adjacent runs of matched characters.
    - **word starts** - matches that begin a word.

    Without the density term a scattered match ties with a tight one, which is
    exactly how "Xuli" used to lose to "BingXuan".
    """
    if token[0] not in text:
        return None
    position = 0
    first = -1
    last = -1
    previous = -2
    contiguous = 0
    word_starts = 0
    for index, char in enumerate(text):
        if char != token[position]:
            continue
        if first < 0:
            first = index
        if index == previous + 1:
            contiguous += 1
        if _at_word_start(text, index):
            word_starts += 1
        previous = index
        last = index
        position += 1
        if position == len(token):
            break
    if position < len(token):
        return None
    length = len(token)
    density = length / (last - first + 1)
    quality = (0.5 * density) + (0.3 * contiguous / length) + (0.2 * word_starts / length)
    return quality * SUBSEQUENCE_CEILING


def token_score(token: str, text: str) -> float | None:
    """Best tier available for this token in this field."""
    index = text.find(token)
    if index == 0:
        return PREFIX_SCORE
    if index > 0:
        return WORD_START_SCORE if _at_word_start(text, index) else SUBSTRING_SCORE
    return subsequence_score(token, text)


def multiset_ratio(token: str, word: str) -> float:
    """Shared-letter ratio in O(n), no dynamic programming.

    Transpositions and single-letter substitutions score high; unrelated words
    score near zero.
    """
    if not word:
        return 0.0
    counts: dict[str, int] = {}
    for char in word:
        counts[char] = counts.get(char, 0) + 1
    shared = 0
    for char in token:
        remaining = counts.get(char, 0)
        if remaining:
            counts[char] = remaining - 1
            shared += 1
    return shared / max(len(token), len(word))


def typo_word_score(token: str, text: str) -> float | None:
    """Closest word in ``text`` to a misspelling of ``token``, or None.

    Two stages: a cheap shared-letter gate, then a real sequence comparison.
    The gate alone is not enough: it cannot tell an anagram from a match, so
    "Setmsa" scored identically against "Setsma" and "MASSET". Comparing
    sequences is what separates a transposition from unrelated letters.
    """
    if len(token) < TYPO_MIN_TOKEN:
        return None
    best: float | None = None
    for word in text.split():
        trimmed = word.strip(',.()[]-')
        if not trimmed or abs(len(trimmed) - len(token)) > TYPO_MAX_LENGTH_DELTA:
            continue
        if multiset_ratio(token, trimmed) < TYPO_PREFILTER_RATIO:
            continue
        ratio = difflib.SequenceMatcher(None, token, trimmed).ratio()
        if ratio >= TYPO_MIN_RATIO and (best is None or ratio > best):
            best = ratio
    return None if best is None else best * TYPO_CEILING


def _prepare(record: dict[str, Any], weights: Sequence[tuple[str, float]]):
    fields = []
    for name, weight in weights:
        value = record.get(name)
        if value:
            fields.append((normalize(str(value)), weight))
    return fields


def fuzzy_score(tokens: Sequence[str], fields) -> float | None:
    """Every token must match some field. Returns the mean best score."""
    if not tokens or not fields:
        return None
    total = 0.0
    for token in tokens:
        best = 0.0
        for text, weight in fields:
            score = token_score(token, text)
            if score is not None and score * weight > best:
                best = score * weight
        if best <= 0.0:
            return None
        total += best
    return total / len(tokens)


def typo_score(tokens: Sequence[str], primary: str) -> float | None:
    """Typo pass, against the primary field only.

    Restricted to names on purpose: it is where misspellings happen, and it
    keeps the second pass cheap. Skipped unless every token is long enough to
    fuzzy-match meaningfully.
    """
    if not tokens or not primary:
        return None
    total = 0.0
    for token in tokens:
        score = typo_word_score(token, primary)
        if score is None:
            return None
        total += score
    return total / len(tokens)


def search_records(
    records: Sequence[dict[str, Any]],
    weights: Sequence[tuple[str, float]],
    query: str,
    limit: int,
    strictness: str = DEFAULT_STRICTNESS,
) -> list[dict[str, Any]]:
    """Rank records against a free-text query, best first.

    Two passes, then a cut. The fuzzy pass scores substring and subsequence
    matches; the typo pass adds misspelling tolerance, *upgrading* a record's
    score rather than only filling gaps. A record can match both ways:
    "Setsma, Mitchell" fuzzy-matches "Setmsa" by scattering letters (0.25)
    while also being a near-perfect typo match (0.46). Treating typos as a
    fallback for unmatched records would leave it on the weaker score and let
    an anagram like "Samsa" win.

    The typo pass always runs. It is cheaper than the fuzzy pass (~75ms vs
    ~115ms over 42k names) and keeping near-misses out is the floor's job, not
    its own, so `loose` can surface a typo even when a strong but wrong match
    already exists.

    ``strictness`` sets the floor as a fraction of the best score found, which
    keeps it meaningful whether the best match is exact (1.0) or merely the
    least-bad option (0.46).
    """
    tokens = tokenize(query)
    if not tokens:
        return []

    primary_name = weights[0][0] if weights else ''
    best: dict[int, tuple[float, dict[str, Any]]] = {}

    for index, record in enumerate(records):
        score = fuzzy_score(tokens, _prepare(record, weights))
        if score is not None:
            best[index] = (score, record)

    for index, record in enumerate(records):
        primary = normalize(str(record.get(primary_name) or ''))
        score = typo_score(tokens, primary)
        if score is None:
            continue
        current = best.get(index)
        if current is None or score > current[0]:
            best[index] = (score, record)

    if not best:
        return []

    floor = STRICTNESS_FLOORS.get(strictness, STRICTNESS_FLOORS[DEFAULT_STRICTNESS])
    ranked = sorted(best.items(), key=lambda item: (-item[1][0], item[0]))
    threshold = ranked[0][1][0] * floor

    results: list[dict[str, Any]] = []
    for _, (score, record) in ranked:
        if score < threshold:
            break
        results.append(record)
        if len(results) >= max(1, limit):
            break
    return results
