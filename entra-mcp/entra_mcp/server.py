"""Model Context Protocol server (stdio) exposing the Entra org/access core.

Everything here is read-only: each tool issues GETs against Microsoft Graph
through the `az` CLI's delegated session. There is no app registration and no
client secret, so the server can see exactly what the signed-in user can see -
nothing more.

This is the same core the `entra` CLI uses. One deliberate difference: the CLI
hides disabled and title-less people from its terminal tables, because a table
is noisy. The MCP returns them, since JSON has no such constraint and an agent
should be able to decide for itself. `accountEnabled` and `jobTitle` are always
present so it can filter.

Run with `entra-mcp`, or `uv run --directory <dir> --extra mcp entra-mcp`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

from entra_tool.access_paths import find_containment_path
from entra_tool.directory import (
    fetch_group_child_groups_json,
    fetch_group_member_of_groups_json,
    fetch_group_members_json,
    fetch_report_user_json,
    fetch_user_detail,
    fetch_user_groups_json,
    fetch_user_member_of_groups_json,
    fetch_user_owned_groups_json,
    group_search_cache_is_fresh,
    load_group_search_rows,
    load_user_search_rows,
    user_search_cache_is_fresh,
)
from entra_tool.directory_filters import filter_groups, filter_users, sort_by_display_name
from entra_tool.errors import AppError, GraphError
from entra_tool.graph import az_command, graph_get, odata_string_literal, uri_encode
from entra_tool.graph_fields import GROUP_SEARCH_FIELDS, USER_RESOLVE_FIELDS, select_fields
from entra_tool.group_audit import summarize_group_audit
from entra_tool.report_rows import report_has_title, report_is_enabled, report_sort_key
from entra_tool.report_tree import collect_report_tree_structured
from entra_tool.text import is_guid, is_short_hex_id_prefix, looks_like_bad_guid

from entra_mcp.matching import search_records

GRAPH = 'https://graph.microsoft.com/v1.0'

# Must stay in sync with the row builders in entra_tool/rows.py; the tests
# round-trip a row through these to catch drift.
USER_SEARCH_ROW_FIELDS = [
    'displayName',
    'userPrincipalName',
    'mail',
    'id',
    'accountEnabled',
    'userType',
    'jobTitle',
    'department',
    'companyName',
    'officeLocation',
]
GROUP_SEARCH_ROW_FIELDS = [
    'displayName',
    'mail',
    'id',
    'mailEnabled',
    'securityEnabled',
    'groupTypes',
    'createdDateTime',
]

# Field weights for search. The display name dominates so a name match always
# outranks a match buried in a department or office string.
USER_SEARCH_WEIGHTS = (
    ('displayName', 1.0),
    ('userPrincipalName', 0.85),
    ('mail', 0.85),
    ('jobTitle', 0.6),
    ('department', 0.6),
    ('companyName', 0.5),
    ('officeLocation', 0.5),
)
GROUP_SEARCH_WEIGHTS = (('displayName', 1.0), ('mail', 0.85))

GROUP_WORKERS = 6
MAX_PEOPLE_FOR_GROUPS = 100
# Ceiling on group nodes visited while tracing a containment path. A person is
# often directly in 40+ groups, so the two-sided search needs room; past this
# the tool reports that it gave up rather than guessing.
MAX_PATH_NODES = 120

# Cached TSV rows store these as strings; restore real booleans on the way out.
BOOLEAN_FIELDS = {'mailEnabled', 'securityEnabled', 'accountEnabled'}


class EntraError(Exception):
    """A problem the caller can act on (bad identifier, ambiguous name, ...)."""


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def _resolve_user(identifier: str) -> dict[str, Any]:
    """Resolve a UPN/email/object id to a user object."""
    try:
        obj = graph_get(f'{GRAPH}/users/{uri_encode(identifier)}?$select={select_fields(USER_RESOLVE_FIELDS)}')
    except GraphError as exc:
        raise EntraError(
            f'User not found: {identifier}. Use a full UPN/email address or Entra object id.'
            + _suggest_users(identifier)
        ) from exc
    if not obj.get('id'):
        raise EntraError(f'User not found: {identifier}' + _suggest_users(identifier))
    return obj


def _suggest_users(query: str) -> str:
    """Close cached matches to offer when an exact lookup misses.

    Turns a dead end ("User not found") into something actionable when the
    spelling was the problem. An email/UPN is reduced to its local part first:
    "Setmsa@example.com" only resembles a display name once the domain (and its
    extra length) is out of the way.
    """
    term = query.split('@', 1)[0] if '@' in query else query
    try:
        rows, _ = load_user_search_rows(use_cache=True, refresh_cache=False)
    except (AppError, OSError):
        return ''
    candidates = _search(rows, USER_SEARCH_ROW_FIELDS, USER_SEARCH_WEIGHTS, term, 3, 'normal')
    if not candidates:
        return ''
    listed = '; '.join(f'{c["displayName"]} <{c.get("userPrincipalName") or c.get("id")}>' for c in candidates)
    return f' Closest cached matches: {listed}.'


def _suggest_groups(query: str) -> str:
    """Close cached matches to offer when an exact group name misses."""
    try:
        rows, _ = load_group_search_rows(use_cache=True, refresh_cache=False)
    except (AppError, OSError):
        return ''
    candidates = _search(rows, GROUP_SEARCH_ROW_FIELDS, GROUP_SEARCH_WEIGHTS, query, 3, 'normal')
    if not candidates:
        return ''
    listed = '; '.join(f'{c["displayName"]} <{c["id"]}>' for c in candidates)
    return f' Closest cached matches: {listed}.'


def _resolve_group(identifier: str) -> dict[str, Any]:
    """Resolve a display name or object id to a group object.

    Unlike the CLI's resolver this never exits the process: an ambiguous name
    comes back as a normal tool error listing the candidates.
    """
    if is_guid(identifier):
        try:
            obj = graph_get(f'{GRAPH}/groups/{uri_encode(identifier)}?$select={select_fields(GROUP_SEARCH_FIELDS)}')
        except GraphError as exc:
            raise EntraError(
                f'Group not found: {identifier}. Use a full group object id or exact display name.'
            ) from exc
    elif looks_like_bad_guid(identifier):
        raise EntraError(f'This looks like a group id with extra characters: {identifier}')
    elif is_short_hex_id_prefix(identifier):
        raise EntraError('Short group id prefixes are not supported. Use the full group id.')
    else:
        filter_value = uri_encode(f'displayName eq {odata_string_literal(identifier)}')
        try:
            result = graph_get(
                f'{GRAPH}/groups?$filter={filter_value}&$select={select_fields(GROUP_SEARCH_FIELDS)}&$top=50'
            )
        except GraphError as exc:
            raise EntraError(f'Group lookup failed for: {identifier}') from exc
        matches = [m for m in (result.get('value') or []) if isinstance(m, dict)]
        if not matches:
            raise EntraError(
                f'No group found with displayName exactly equal to: {identifier}' + _suggest_groups(identifier)
            )
        if len(matches) > 1:
            candidates = '; '.join(f'{m.get("displayName")} <{m.get("id")}>' for m in matches)
            raise EntraError(
                f'Multiple groups match "{identifier}". Re-run with the exact group id. Candidates: {candidates}'
            )
        obj = matches[0]

    if not obj.get('id'):
        raise EntraError(f'Group not found: {identifier}')
    return obj


# --------------------------------------------------------------------------- #
# Search over the cached directory lists
# --------------------------------------------------------------------------- #


def _coerce_bool(value: Any) -> Any:
    """The search cache is TSV, so booleans come back as the strings
    "true"/"false". Restore real booleans so the JSON is honest."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == 'true':
            return True
        if lowered == 'false':
            return False
    return value


def _strip_odata(obj: dict[str, Any]) -> dict[str, Any]:
    """Drop Graph's @odata.* metadata so tool output stays readable."""
    return {key: value for key, value in obj.items() if not key.startswith('@odata.')}


def _group_ref(group: dict[str, Any]) -> dict[str, Any]:
    """The identity fields a caller needs to act on a group."""
    return {'id': group.get('id'), 'displayName': group.get('displayName'), 'mail': group.get('mail')}


def _row_to_record(row: list[Any], fields: list[str]) -> dict[str, Any]:
    padded = list(row) + [''] * (len(fields) - len(row))
    record = dict(zip(fields, padded))
    if 'groupTypes' in record:
        raw = record['groupTypes']
        record['groupTypes'] = [part for part in str(raw).split(';') if part]
    for field in BOOLEAN_FIELDS & record.keys():
        record[field] = _coerce_bool(record[field])
    return record


def _search(
    rows: list[list[Any]],
    fields: list[str],
    weights: tuple[tuple[str, float], ...],
    query: str,
    limit: int,
    strictness: str,
) -> list[dict[str, Any]]:
    records = [_row_to_record(row, fields) for row in rows]
    return search_records(records, weights, query, limit, strictness)


def _listing_response(
    matches: list[dict[str, Any]], limit: int, *, from_cache: bool, stale: bool, total: int
) -> dict[str, Any]:
    """Shape a filtered listing.

    ``count`` is the full number of matches; ``returned`` is how many fit under
    ``limit``. Reporting both lets a caller size a set it cannot fetch whole.
    """
    capped = max(1, limit)
    return {
        'from_cache': from_cache,
        'stale': stale,
        'total': total,
        'count': len(matches),
        'returned': min(len(matches), capped),
        'matches': matches[:capped],
    }


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #


def search_users(
    query: str, limit: int = 25, strictness: Literal['strict', 'normal', 'loose'] = 'strict'
) -> dict[str, Any]:
    """Find people by name, email, department, or job title.

    Searches the locally cached directory listing (refreshed daily), so it is
    fast and does not hit Graph. Use the returned `userPrincipalName` or `id`
    with the other tools.

    Matching is fuzzy, so you do not need the exact spelling: partial words
    ("Xuli" finds "Xueli"), words in any order ("kevin tian"), and small
    misspellings including transposed letters ("Setmsa" finds "Setsma") all
    match. If a first attempt returns nothing, try fewer letters rather than
    guessing the spelling.

    By default only results scoring within 10% of the best match are returned,
    which is usually just the person you meant. Loosen `strictness` when the
    answer is missing or you want to browse alternatives.

    Args:
        query: Free text matched against display name, UPN, email, department,
            and job title.
        limit: Maximum number of people to return (default 25).
        strictness: How close to the best match a result must be. "strict"
            (default) returns only near-best matches; "normal" widens to about
            the top 60%; "loose" returns every fuzzy match, which can be
            thousands (still capped by `limit`).
    """
    rows, from_cache = load_user_search_rows(use_cache=True, refresh_cache=False)
    return {
        'from_cache': from_cache,
        'stale': from_cache and not user_search_cache_is_fresh(),
        'count': len(rows),
        'matches': _search(rows, USER_SEARCH_ROW_FIELDS, USER_SEARCH_WEIGHTS, query, limit, strictness),
    }


def search_groups(
    query: str, limit: int = 25, strictness: Literal['strict', 'normal', 'loose'] = 'strict'
) -> dict[str, Any]:
    """Find groups by display name or mail address.

    Searches the locally cached group listing (refreshed daily). Use the
    returned `id` with the other group tools; display names are not always
    unique.

    Matching is fuzzy — partial words, out-of-order words, and small
    misspellings all match, so an exact display name is not required.

    By default only results scoring within 10% of the best match are returned.
    Loosen `strictness` when the answer is missing or you want to browse.

    Args:
        query: Free text matched against group display name and mail address.
        limit: Maximum number of groups to return (default 25).
        strictness: How close to the best match a result must be. "strict"
            (default) returns only near-best matches; "normal" widens to about
            the top 60%; "loose" returns every fuzzy match, which can be
            thousands (still capped by `limit`).
    """
    rows, from_cache = load_group_search_rows(use_cache=True, refresh_cache=False)
    return {
        'from_cache': from_cache,
        'stale': from_cache and not group_search_cache_is_fresh(),
        'count': len(rows),
        'matches': _search(rows, GROUP_SEARCH_ROW_FIELDS, GROUP_SEARCH_WEIGHTS, query, limit, strictness),
    }


def list_users(
    department: str | None = None,
    job_title: str | None = None,
    company: str | None = None,
    office: str | None = None,
    user_type: Literal['Member', 'Guest'] | None = None,
    enabled: bool | None = None,
    has_title: bool | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List people by attribute — complete and unranked, unlike a search.

    Answers "who is in department X", "every contractor", "all disabled
    accounts", "everyone in this office". It reads the local cache, so it costs
    no Graph calls. Filters combine with AND; text filters match whole words,
    so "IT" does not match "Digital".

    ``count`` reports the true number of matches even when ``limit`` truncates
    ``matches``, so you can size a set you cannot fetch whole.

    Args:
        department: Match the department (word-boundary, case-insensitive).
        job_title: Match the job title.
        company: Match the company name.
        office: Match the office location.
        user_type: "Member" or "Guest" (exact).
        enabled: true for enabled accounts, false for disabled ones.
        has_title: true for people with a job title, false for those without.
        limit: Maximum matches to return (default 50).
    """
    if not any([department, job_title, company, office, user_type, enabled is not None, has_title is not None]):
        raise EntraError('list_users needs at least one filter; use search_users for free text.')
    rows, from_cache = load_user_search_rows(use_cache=True, refresh_cache=False)
    records = [_row_to_record(row, USER_SEARCH_ROW_FIELDS) for row in rows]
    matches = sort_by_display_name(
        filter_users(
            records,
            department=department,
            job_title=job_title,
            company=company,
            office=office,
            user_type=user_type,
            enabled=enabled,
            has_title=has_title,
        )
    )
    return _listing_response(
        matches, limit, from_cache=from_cache, stale=from_cache and not user_search_cache_is_fresh(), total=len(rows)
    )


def list_groups(
    name: str | None = None,
    mail_enabled: bool | None = None,
    security_enabled: bool | None = None,
    group_type: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List groups by attribute — complete and unranked, unlike a search.

    Answers "all mail-enabled groups", "every security group", "all Microsoft
    365 groups". It reads the local cache, so it costs no Graph calls. Filters
    combine with AND.

    ``count`` reports the true number of matches even when ``limit`` truncates
    ``matches``.

    Args:
        name: Match the display name (word-boundary, case-insensitive).
        mail_enabled: true/false on mail-enabled.
        security_enabled: true/false on security-enabled.
        group_type: Match the group types, e.g. "Unified".
        limit: Maximum matches to return (default 50).
    """
    if not any([name, mail_enabled is not None, security_enabled is not None, group_type]):
        raise EntraError('list_groups needs at least one filter; use search_groups for free text.')
    rows, from_cache = load_group_search_rows(use_cache=True, refresh_cache=False)
    records = [_row_to_record(row, GROUP_SEARCH_ROW_FIELDS) for row in rows]
    matches = sort_by_display_name(
        filter_groups(
            records, name=name, mail_enabled=mail_enabled, security_enabled=security_enabled, group_type=group_type
        )
    )
    return _listing_response(
        matches, limit, from_cache=from_cache, stale=from_cache and not group_search_cache_is_fresh(), total=len(rows)
    )


def get_user(user: str) -> dict[str, Any]:
    """Get one person's full profile.

    Returns title, department, company, office, employee id/type, phone
    numbers, account status, user type, proxy addresses and creation date.

    Args:
        user: Full UPN/email address or Entra object id.
    """
    resolved = _resolve_user(user)
    return _strip_odata(fetch_user_detail(resolved['id']))


def get_user_groups(user: str, mode: str = 'transitive') -> dict[str, Any]:
    """List the groups a person belongs to.

    Args:
        user: Full UPN/email address or Entra object id.
        mode: "transitive" (default) includes nested membership - the groups
            that actually grant access. "direct" returns only groups the
            person is assigned to directly.
    """
    resolved = _resolve_user(user)
    groups = fetch_user_groups_json(resolved['id'], mode)
    return {
        'user': {
            'id': resolved.get('id'),
            'displayName': resolved.get('displayName'),
            'userPrincipalName': resolved.get('userPrincipalName'),
        },
        'mode': mode,
        'count': len(groups),
        'groups': groups,
    }


def get_user_owned_groups(user: str) -> dict[str, Any]:
    """List the groups a person owns.

    Owners can add and remove members, so ownership is often a route to access.
    This is the mirror of `get_group`, which lists a group's owners.

    Args:
        user: Full UPN/email address or Entra object id.
    """
    resolved = _resolve_user(user)
    try:
        groups = fetch_user_owned_groups_json(resolved['id'])
    except GraphError:
        groups = []
    return {
        'user': {
            'id': resolved.get('id'),
            'displayName': resolved.get('displayName'),
            'userPrincipalName': resolved.get('userPrincipalName'),
        },
        'count': len(groups),
        'groups': groups,
    }


def why_user_in_group(user: str, group: str) -> dict[str, Any]:
    """Trace how a person gets access to a group through nested membership.

    Graph can say whether someone is effectively a member, but not the route.
    This walks it and returns the chain, for example
    `Ada -> IT Team -> IT Department -> All Employees`. A direct member gives a
    two-node chain.

    The walk is bounded (about 120 groups) because each step is a Graph call.
    If the path is deeper than that, `has_access` is still answered from a
    transitive lookup, `path` is null, and `note` says the search gave up.

    Args:
        user: Full UPN/email address or Entra object id.
        group: Exact group display name or Entra object id.
    """
    resolved = _resolve_user(user)
    target = _resolve_group(group)
    user_ref = {
        'id': resolved.get('id'),
        'displayName': resolved.get('displayName'),
        'userPrincipalName': resolved.get('userPrincipalName'),
    }
    group_ref = _group_ref(target)
    user_id = resolved['id']
    target_id = target['id']

    # One transitive lookup settles membership definitively, so a non-member is
    # answered without walking the containment graph at all.
    members = fetch_user_groups_json(user_id, 'transitive')
    if not any(member.get('id') == target_id for member in members):
        return {
            'user': user_ref,
            'group': group_ref,
            'has_access': False,
            'path': None,
            'note': 'Not a member of this group, directly or transitively.',
        }

    def fetch_parent(node_id: str) -> list[dict[str, Any]]:
        if node_id == user_id:
            return fetch_user_member_of_groups_json(node_id)
        return fetch_group_member_of_groups_json(node_id)

    def fetch_parents(node_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        with ThreadPoolExecutor(max_workers=GROUP_WORKERS) as executor:
            results = executor.map(fetch_parent, node_ids)
        return dict(zip(node_ids, results))

    def fetch_children(node_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        with ThreadPoolExecutor(max_workers=GROUP_WORKERS) as executor:
            results = executor.map(fetch_group_child_groups_json, node_ids)
        return dict(zip(node_ids, results))

    found = find_containment_path(user_id, target_id, fetch_parents, fetch_children)

    if found is None:
        return {
            'user': user_ref,
            'group': group_ref,
            'has_access': True,
            'path': None,
            'note': (
                f'A member, but no nesting path was found within {MAX_PATH_NODES} '
                'groups; the chain may be deeper or broader.'
            ),
        }

    path_ids, names = found
    display = dict(names)
    display[user_id] = resolved.get('displayName') or ''
    display[target_id] = target.get('displayName') or ''
    path = [
        {'kind': 'user' if node_id == user_id else 'group', 'id': node_id, 'displayName': display.get(node_id) or ''}
        for node_id in path_ids
    ]
    return {
        'user': user_ref,
        'group': group_ref,
        'has_access': True,
        'path': path,
        'hops': len(path_ids) - 1,
        'note': (
            'Direct membership.' if len(path_ids) == 2 else f'Nested through {len(path_ids) - 2} intermediate group(s).'
        ),
    }


def _fetch_group_owners(group_id: str) -> list[dict[str, Any]]:
    try:
        page = graph_get(
            f'{GRAPH}/groups/{uri_encode(group_id)}/owners?$select=id,displayName,userPrincipalName,mail&$top=20'
        )
    except GraphError:
        return []
    return [owner for owner in (page.get('value') or []) if isinstance(owner, dict)]


def get_group(group: str) -> dict[str, Any]:
    """Get one group's details and owners.

    Args:
        group: Exact group display name or Entra object id. If a display name
            matches more than one group, the error lists the candidates - retry
            with an id.
    """
    resolved = _resolve_group(group)
    return {'group': _strip_odata(resolved), 'owners': _fetch_group_owners(resolved['id'])}


def get_group_members(group: str, mode: str = 'transitive', include_groups: bool = False) -> dict[str, Any]:
    """List the people in a group.

    Members are people; nested groups are not returned unless you ask, so a
    count can look lighter than the group's real reach. Pass `include_groups`
    to see them.

    Args:
        group: Exact group display name or Entra object id.
        mode: "transitive" (default) expands nested groups to the people who
            actually get access. "direct" returns only direct members.
        include_groups: Also return the nested groups — direct ones under
            "direct", every nested group under "transitive".
    """
    resolved = _resolve_group(group)
    members = fetch_group_members_json(resolved['id'], mode)
    result = {'group': _group_ref(resolved), 'mode': mode, 'count': len(members), 'members': members}
    if include_groups:
        nested = fetch_group_child_groups_json(resolved['id'], mode)
        result['nested_group_count'] = len(nested)
        result['nested_groups'] = nested
    return result


def group_audit(group: str, mode: str = 'transitive') -> dict[str, Any]:
    """Review a group: disabled accounts, guests, untitled members, nesting, owners.

    Costs a few Graph calls (members, nested groups, owners) and returns counts
    plus a capped sample of each problem category, so it is cheap to run before
    deciding whether to dig further.

    Args:
        group: Exact group display name or Entra object id.
        mode: "transitive" (default) audits everyone who effectively gets access
            through nesting. "direct" audits only direct assignments.
    """
    resolved = _resolve_group(group)
    members = fetch_group_members_json(resolved['id'], mode)
    nested = fetch_group_child_groups_json(resolved['id'], mode)
    owners = _fetch_group_owners(resolved['id'])
    return {'group': _group_ref(resolved), 'mode': mode, **summarize_group_audit(members, nested, owners)}


def get_manager(user: str) -> dict[str, Any]:
    """Find who a person reports to.

    Args:
        user: Full UPN/email address or Entra object id.
    """
    resolved = _resolve_user(user)
    try:
        manager = graph_get(
            f'{GRAPH}/users/{uri_encode(resolved["id"])}/manager'
            f'?$select=id,displayName,userPrincipalName,mail,jobTitle,department'
        )
    except GraphError:
        return {
            'user': {'id': resolved.get('id'), 'displayName': resolved.get('displayName')},
            'manager': None,
            'note': 'No manager is set for this person.',
        }
    return {
        'user': {'id': resolved.get('id'), 'displayName': resolved.get('displayName')},
        'manager': _strip_odata(manager),
    }


def _tree_node(
    user_id: str, users: dict[str, dict[str, Any]], children_by_manager: dict[str, list[dict[str, Any]]], seen: set[str]
) -> dict[str, Any] | None:
    if user_id in seen:
        return None
    user = users.get(user_id)
    if not user:
        return None
    seen.add(user_id)
    node = {
        'id': user.get('id') or '',
        'displayName': user.get('displayName') or '',
        'userPrincipalName': user.get('userPrincipalName') or '',
        'mail': user.get('mail') or '',
        'jobTitle': user.get('jobTitle') or '',
        'department': user.get('department') or '',
        'accountEnabled': user.get('accountEnabled'),
        'managerId': user.get('managerId') or '',
        'managerDisplayName': user.get('managerDisplayName') or '',
        'directReports': [],
    }
    for child in sorted(children_by_manager.get(user_id, []), key=report_sort_key):
        child_node = _tree_node(child.get('id') or '', users, children_by_manager, seen)
        if child_node:
            node['directReports'].append(child_node)
    return node


def _groups_by_user(user_ids: list[str]) -> dict[str, list[str] | None]:
    def one(user_id: str) -> tuple[str, list[str] | None]:
        try:
            groups = fetch_user_groups_json(user_id, 'transitive')
        except GraphError:
            return user_id, None
        return user_id, [g.get('displayName') or '' for g in groups]

    with ThreadPoolExecutor(max_workers=GROUP_WORKERS) as executor:
        return dict(executor.map(one, user_ids))


def get_reports(user: str, mode: str = 'transitive', include_groups: bool = False) -> dict[str, Any]:
    """Walk the reporting tree under a person.

    Returns a nested tree rooted at the given person. Unlike the CLI, no one
    is hidden - check `accountEnabled` and `jobTitle` if you want the same
    filtering the terminal tables apply.

    Args:
        user: Full UPN/email address or Entra object id.
        mode: "transitive" (default) walks every level below. "direct" returns
            only immediate reports.
        include_groups: Also attach each person's transitive group names. Costs
            one Graph call per person, so it is refused above 100 people -
            narrow the tree first.
    """
    resolved = _resolve_user(user)
    try:
        root_json = fetch_report_user_json(resolved['id'])
    except GraphError as exc:
        raise EntraError(f'User detail lookup failed for reports root: {resolved.get("userPrincipalName")}') from exc

    users, children_by_manager = collect_report_tree_structured(resolved['id'], root_json, mode, True, False)
    tree = _tree_node(resolved['id'], users, children_by_manager, set())

    hidden_disabled = sum(1 for u in users.values() if not report_is_enabled(u))
    hidden_missing_title = sum(1 for u in users.values() if report_is_enabled(u) and not report_has_title(u))

    people = [u for u in users.values() if u.get('id')]
    levels = [int(u.get('level') or 0) for u in people]

    if include_groups:
        if len(people) > MAX_PEOPLE_FOR_GROUPS:
            raise EntraError(
                f'include_groups needs one Graph call per person and this tree has '
                f'{len(people)} (limit {MAX_PEOPLE_FOR_GROUPS}). Narrow the tree with '
                f'mode="direct" or drop include_groups.'
            )
        groups_by_user = _groups_by_user([u['id'] for u in people])
        for person in people:
            person['groups'] = groups_by_user.get(person['id'])

    return {
        'root': {
            'id': resolved.get('id'),
            'displayName': resolved.get('displayName'),
            'userPrincipalName': resolved.get('userPrincipalName'),
        },
        'mode': mode,
        'total_people': len(people),
        'max_depth': max(levels, default=0),
        'hidden': {
            'disabled': hidden_disabled,
            'missing_title': hidden_missing_title,
            'note': 'The CLI hides these; this response includes them.',
        },
        'tree': tree,
        'people': people,
    }


def _split_by_id(
    left: list[dict[str, Any]], right: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    left_by_id = {item['id']: item for item in left if item.get('id')}
    right_by_id = {item['id']: item for item in right if item.get('id')}
    shared = [left_by_id[i] for i in left_by_id.keys() & right_by_id.keys()]
    left_only = [left_by_id[i] for i in left_by_id.keys() - right_by_id.keys()]
    right_only = [right_by_id[i] for i in right_by_id.keys() - left_by_id.keys()]
    key = lambda item: str(item.get('displayName') or '').lower()  # noqa: E731
    return (sorted(shared, key=key), sorted(left_only, key=key), sorted(right_only, key=key))


def compare_users(first_user: str, second_user: str, mode: str = 'transitive') -> dict[str, Any]:
    """Compare two people's group membership.

    Shows groups they share and groups only one of them has - useful for
    spotting why two people in the same role have different access.

    Args:
        first_user: Full UPN/email address or Entra object id.
        second_user: Full UPN/email address or Entra object id.
        mode: "transitive" (default) or "direct".
    """
    first = _resolve_user(first_user)
    second = _resolve_user(second_user)
    first_groups = fetch_user_groups_json(first['id'], mode)
    second_groups = fetch_user_groups_json(second['id'], mode)
    shared, first_only, second_only = _split_by_id(first_groups, second_groups)

    def label(obj: dict[str, Any]) -> str:
        return f'{obj.get("displayName")} <{obj.get("userPrincipalName") or obj.get("mail") or obj.get("id")}>'

    return {
        'mode': mode,
        'first': {'label': label(first), 'count': len(first_groups)},
        'second': {'label': label(second), 'count': len(second_groups)},
        'shared': shared,
        'first_only': first_only,
        'second_only': second_only,
    }


def compare_groups(first_group: str, second_group: str, mode: str = 'transitive') -> dict[str, Any]:
    """Compare two groups' membership.

    Shows members they share and members only one has - useful for spotting
    duplicate groups, drift, and whether one group could replace another.

    Args:
        first_group: Exact group display name or Entra object id.
        second_group: Exact group display name or Entra object id.
        mode: "transitive" (default) or "direct".
    """
    first = _resolve_group(first_group)
    second = _resolve_group(second_group)
    first_members = fetch_group_members_json(first['id'], mode)
    second_members = fetch_group_members_json(second['id'], mode)
    shared, first_only, second_only = _split_by_id(first_members, second_members)

    def label(obj: dict[str, Any]) -> str:
        return f'{obj.get("displayName")} <{obj.get("id")}>'

    return {
        'mode': mode,
        'first': {'label': label(first), 'count': len(first_members)},
        'second': {'label': label(second), 'count': len(second_members)},
        'shared': shared,
        'first_only': first_only,
        'second_only': second_only,
    }


def check_auth() -> dict[str, Any]:
    """Check whether the delegated `az` session is usable.

    Call this first if any other tool reports a Graph error. It reports which
    Azure CLI binary was found and who it is signed in as. If it is not
    authenticated, run `az login` in a terminal.
    """
    az = az_command()
    try:
        result = subprocess.run(
            [az, 'account', 'show', '--query', '{user:user.name,tenant:tenantId}', '-o', 'json'],
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return {'ok': False, 'az_path': az, 'error': 'Azure CLI not found. Install it or set ENTRA_AZ_PATH.'}
    if result.returncode != 0:
        return {
            'ok': False,
            'az_path': az,
            'error': (result.stderr or result.stdout).strip(),
            'fix': 'Run `az login` in a terminal, then retry.',
        }
    try:
        account = json.loads(result.stdout or '{}')
    except json.JSONDecodeError:
        account = {}
    return {'ok': True, 'az_path': az, 'account': account}


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #

READ_ONLY_TOOLS = (
    search_users,
    search_groups,
    list_users,
    list_groups,
    get_user,
    get_user_groups,
    get_user_owned_groups,
    get_group,
    get_group_members,
    group_audit,
    get_manager,
    get_reports,
    why_user_in_group,
    compare_users,
    compare_groups,
    check_auth,
)


def build_server():
    try:
        from fastmcp import FastMCP
        from mcp.types import ToolAnnotations
    except ImportError as exc:  # pragma: no cover
        sys.stderr.write('fastmcp is not installed. Install the extra: `uv sync --extra mcp`.\n')
        raise SystemExit(2) from exc

    read = ToolAnnotations(read_only_hint=True, open_world_hint=True)
    mcp = FastMCP('entra')
    for fn in READ_ONLY_TOOLS:
        mcp.tool(annotations=read)(fn)
    return mcp


def main() -> None:
    build_server().run()


__all__ = [
    'EntraError',
    'build_server',
    'check_auth',
    'compare_groups',
    'compare_users',
    'get_group',
    'get_group_members',
    'get_manager',
    'get_reports',
    'get_user',
    'get_user_groups',
    'get_user_owned_groups',
    'group_audit',
    'list_groups',
    'list_users',
    'main',
    'search_groups',
    'search_users',
    'why_user_in_group',
]
