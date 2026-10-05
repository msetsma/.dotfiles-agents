"""Summarize a group for an access review.

Turns a group's members, its nested groups, and its owners into the counts and
short samples a reviewer actually wants: disabled accounts, guests, people with
no title, nested groups, and whether anyone owns the thing. Pure: the Graph
I/O happens before this, so it is trivial to test.
"""

from collections.abc import Sequence
from typing import Any


SAMPLE_LIMIT = 25


def _is_disabled(member: dict[str, Any]) -> bool:
    value = member.get('accountEnabled')
    if isinstance(value, str):
        return value.strip().lower() == 'false'
    return value is False


def _is_guest(member: dict[str, Any]) -> bool:
    return str(member.get('userType') or '').strip().lower() == 'guest'


def _has_title(member: dict[str, Any]) -> bool:
    return bool(str(member.get('jobTitle') or '').strip())


def _sample(members: Sequence[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return [
        {
            'id': member.get('id'),
            'displayName': member.get('displayName'),
            'userPrincipalName': member.get('userPrincipalName'),
        }
        for member in list(members)[:limit]
    ]


def summarize_group_audit(
    members: Sequence[dict[str, Any]],
    nested_groups: Sequence[dict[str, Any]],
    owners: Sequence[dict[str, Any]],
    *,
    sample_limit: int = SAMPLE_LIMIT,
) -> dict[str, Any]:
    disabled = [member for member in members if _is_disabled(member)]
    guests = [member for member in members if _is_guest(member)]
    untitled = [member for member in members if not _has_title(member)]
    empty = not members and not nested_groups

    findings: list[str] = []
    if empty:
        findings.append('Empty: no members and no nested groups.')
    if disabled:
        findings.append(f'{len(disabled)} disabled account(s).')
    if guests:
        findings.append(f'{len(guests)} guest(s).')
    if untitled:
        findings.append(f'{len(untitled)} member(s) with no job title.')
    if nested_groups:
        findings.append(f'{len(nested_groups)} nested group(s).')
    if not owners:
        findings.append('No owners: nobody can manage this group.')

    return {
        'total_users': len(members),
        'nested_group_count': len(nested_groups),
        'owner_count': len(owners),
        'disabled_count': len(disabled),
        'guest_count': len(guests),
        'untitled_count': len(untitled),
        'empty': empty,
        'findings': findings,
        'samples': {
            'disabled': _sample(disabled, sample_limit),
            'guests': _sample(guests, sample_limit),
            'untitled': _sample(untitled, sample_limit),
        },
        'nested_groups': list(nested_groups),
        'owners': list(owners),
    }
