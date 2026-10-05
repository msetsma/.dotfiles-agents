"""Structured filters over the cached directory rows.

Search answers "who matches this string", ranked best-first. These answer "who
is in this exact set", complete and unranked: a department, a title, account
state. Both read the same local cache, so a filter costs no Graph calls.
"""

import re
from collections.abc import Sequence
from typing import Any


def _normalize(value: Any) -> str:
    return ' '.join(str(value or '').lower().split())


def text_matches(value: Any, query: str) -> bool:
    """Case- and space-insensitive match, anchored to a word start.

    "Finance" matches "Finance", "Finance Operations", and "Global Finance",
    but a short filter cannot leak into an unrelated word: "IT" does not match
    "Digital", because the match has to begin at a word boundary. Blank queries
    match everything.
    """
    needle = _normalize(query)
    if not needle:
        return True
    return re.search(r'(?<![a-z0-9])' + re.escape(needle), _normalize(value)) is not None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == 'true'
    return bool(value)


def _flag_matches(record: dict[str, Any], field: str, expected: bool) -> bool:
    return _coerce_bool(record.get(field)) is expected


def sort_by_display_name(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable, human-ordered listing: search ranks by score, a list does not."""
    return sorted(records, key=lambda record: _normalize(record.get('displayName')))


def filter_users(
    records: Sequence[dict[str, Any]],
    *,
    department: str | None = None,
    job_title: str | None = None,
    company: str | None = None,
    office: str | None = None,
    user_type: str | None = None,
    enabled: bool | None = None,
    has_title: bool | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for record in records:
        if department and not text_matches(record.get('department'), department):
            continue
        if job_title and not text_matches(record.get('jobTitle'), job_title):
            continue
        if company and not text_matches(record.get('companyName'), company):
            continue
        if office and not text_matches(record.get('officeLocation'), office):
            continue
        if user_type and _normalize(record.get('userType')) != _normalize(user_type):
            continue
        if enabled is not None and not _flag_matches(record, 'accountEnabled', enabled):
            continue
        if has_title is not None and bool(_normalize(record.get('jobTitle'))) is not has_title:
            continue
        results.append(record)
    return results


def filter_groups(
    records: Sequence[dict[str, Any]],
    *,
    name: str | None = None,
    mail_enabled: bool | None = None,
    security_enabled: bool | None = None,
    group_type: str | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for record in records:
        if name and not text_matches(record.get('displayName'), name):
            continue
        if mail_enabled is not None and not _flag_matches(record, 'mailEnabled', mail_enabled):
            continue
        if security_enabled is not None and not _flag_matches(record, 'securityEnabled', security_enabled):
            continue
        types = record.get('groupTypes')
        if group_type and not text_matches(';'.join(types) if isinstance(types, list) else types, group_type):
            continue
        results.append(record)
    return results
