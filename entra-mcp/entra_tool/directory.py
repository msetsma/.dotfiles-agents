from pathlib import Path
from typing import Any, Callable

from entra_tool.cache_freshness import cache_is_fresh
from entra_tool.cache_paths import (
    group_search_cache_meta_path,
    group_search_cache_path,
    search_cache_meta_path,
    search_cache_path,
)
from entra_tool.errors import AppError
from entra_tool.graph import graph_get, uri_encode
from entra_tool.graph_fields import (
    GROUP_MEMBER_FIELDS,
    GROUP_SEARCH_FIELDS,
    REPORT_USER_FIELDS,
    USER_DETAIL_FIELDS,
    USER_SEARCH_FIELDS,
    select_fields,
)
from entra_tool.rows import group_row, search_user_row, user_row
from entra_tool.search_cache import save_search_cache
from entra_tool.tsv import read_tsv


def collect_paged_rows(url: str, row_builder) -> list[list[Any]]:
    rows: list[list[Any]] = []
    while url:
        page = graph_get(url)
        rows.extend(row_builder(item) for item in page.get("value") or [])
        url = page.get("@odata.nextLink") or ""
    return rows


def collect_paged_json(url: str) -> list[dict[str, Any]]:
    """Follow @odata.nextLink and return the raw Graph objects unchanged.

    The row-based helpers below shape each object for the CLI's TSV tables.
    This variant keeps the full objects for structured consumers (the MCP
    server), so both front ends share one set of URLs and one paging loop.
    """
    items: list[dict[str, Any]] = []
    while url:
        page = graph_get(url)
        for item in page.get("value") or []:
            if isinstance(item, dict):
                items.append(item)
        url = page.get("@odata.nextLink") or ""
    return items


def users_search_url() -> str:
    return f"https://graph.microsoft.com/v1.0/users?$select={select_fields(USER_SEARCH_FIELDS)}&$top=999"


def groups_search_url() -> str:
    return f"https://graph.microsoft.com/v1.0/groups?$select={select_fields(GROUP_SEARCH_FIELDS)}&$top=999"


def user_groups_url(user_id: str, mode: str) -> str:
    relationship = "transitiveMemberOf" if mode == "transitive" else "memberOf"
    return (
        f"https://graph.microsoft.com/v1.0/users/{uri_encode(user_id)}/{relationship}"
        f"/microsoft.graph.group?$select={select_fields(GROUP_SEARCH_FIELDS)}&$top=999"
    )


def group_members_url(group_id: str, mode: str) -> str:
    relationship = "transitiveMembers" if mode == "transitive" else "members"
    return (
        f"https://graph.microsoft.com/v1.0/groups/{uri_encode(group_id)}/{relationship}"
        f"/microsoft.graph.user?$select={select_fields(GROUP_MEMBER_FIELDS)}&$top=999"
    )


def user_owned_groups_url(user_id: str) -> str:
    return (
        f"https://graph.microsoft.com/v1.0/users/{uri_encode(user_id)}"
        f"/ownedObjects/microsoft.graph.group?$select={select_fields(GROUP_SEARCH_FIELDS)}&$top=999"
    )


def user_detail_select_fields() -> str:
    return select_fields(USER_DETAIL_FIELDS)


def fetch_user_detail(selected_user_id: str) -> dict[str, Any]:
    encoded = uri_encode(selected_user_id)
    return graph_get(
        f"https://graph.microsoft.com/v1.0/users/{encoded}?$select={user_detail_select_fields()}"
    )


def _load_directory_rows(
    use_cache: bool,
    refresh_cache: bool,
    cache_file: Path,
    meta_file: Path,
    fetch_rows: Callable[[], list[list[Any]]],
) -> tuple[list[list[Any]], bool]:
    """Return directory rows, preferring a fresh cache.

    If Graph is unreachable, a stale cache is served rather than failing: the
    directory changes slowly, and yesterday's list beats no list at all. The
    fallback applies only to implicit refreshes — `entra cache refresh` asks
    for ``refresh_cache=True`` and must surface the failure.
    """
    if use_cache and not refresh_cache and cache_is_fresh(meta_file, cache_file):
        return read_tsv(cache_file), True

    try:
        rows = fetch_rows()
    except (AppError, OSError):
        if use_cache and not refresh_cache and cache_file.is_file():
            try:
                stale_rows = read_tsv(cache_file)
            except OSError:
                stale_rows = []
            if stale_rows:
                return stale_rows, True
        raise

    if use_cache:
        save_search_cache(rows, cache_file, meta_file)
    return rows, False


def load_user_search_rows(
    use_cache: bool, refresh_cache: bool
) -> tuple[list[list[Any]], bool]:
    return _load_directory_rows(
        use_cache,
        refresh_cache,
        search_cache_path(),
        search_cache_meta_path(),
        lambda: collect_paged_rows(users_search_url(), search_user_row),
    )


def load_group_search_rows(
    use_cache: bool, refresh_cache: bool
) -> tuple[list[list[Any]], bool]:
    return _load_directory_rows(
        use_cache,
        refresh_cache,
        group_search_cache_path(),
        group_search_cache_meta_path(),
        lambda: collect_paged_rows(groups_search_url(), group_row),
    )


def user_search_cache_is_fresh() -> bool:
    return cache_is_fresh(search_cache_meta_path(), search_cache_path())


def group_search_cache_is_fresh() -> bool:
    return cache_is_fresh(group_search_cache_meta_path(), group_search_cache_path())


def fetch_group_rows_for_user_id(user_id: str, mode: str) -> list[list[Any]]:
    return collect_paged_rows(user_groups_url(user_id, mode), group_row)


def fetch_user_rows_for_group_id(group_id: str, mode: str) -> list[list[Any]]:
    return collect_paged_rows(group_members_url(group_id, mode), user_row)


def fetch_user_groups_json(user_id: str, mode: str) -> list[dict[str, Any]]:
    return collect_paged_json(user_groups_url(user_id, mode))


def fetch_group_members_json(group_id: str, mode: str) -> list[dict[str, Any]]:
    return collect_paged_json(group_members_url(group_id, mode))


def fetch_user_owned_groups_json(user_id: str) -> list[dict[str, Any]]:
    return collect_paged_json(user_owned_groups_url(user_id))


def user_member_of_groups_url(user_id: str) -> str:
    return (
        f"https://graph.microsoft.com/v1.0/users/{uri_encode(user_id)}"
        f"/memberOf/microsoft.graph.group?$select=id,displayName&$top=999"
    )


def group_member_of_groups_url(group_id: str) -> str:
    return (
        f"https://graph.microsoft.com/v1.0/groups/{uri_encode(group_id)}"
        f"/memberOf/microsoft.graph.group?$select=id,displayName&$top=999"
    )


def group_child_groups_url(group_id: str, mode: str = "direct") -> str:
    relationship = "transitiveMembers" if mode == "transitive" else "members"
    return (
        f"https://graph.microsoft.com/v1.0/groups/{uri_encode(group_id)}"
        f"/{relationship}/microsoft.graph.group?$select=id,displayName&$top=999"
    )


def fetch_user_member_of_groups_json(user_id: str) -> list[dict[str, Any]]:
    return collect_paged_json(user_member_of_groups_url(user_id))


def fetch_group_member_of_groups_json(group_id: str) -> list[dict[str, Any]]:
    return collect_paged_json(group_member_of_groups_url(group_id))


def fetch_group_child_groups_json(
    group_id: str, mode: str = "direct"
) -> list[dict[str, Any]]:
    return collect_paged_json(group_child_groups_url(group_id, mode))


def report_user_select_fields() -> str:
    return select_fields(REPORT_USER_FIELDS)


def fetch_report_user_json(selected_user_id: str) -> dict[str, Any]:
    encoded = uri_encode(selected_user_id)
    return graph_get(
        f"https://graph.microsoft.com/v1.0/users/{encoded}?$select={report_user_select_fields()}"
    )


def fetch_direct_report_users(manager: dict[str, Any]) -> list[dict[str, Any]]:
    manager_id = manager.get("id") or ""
    if not manager_id:
        return []
    children: list[dict[str, Any]] = []
    url = f"https://graph.microsoft.com/v1.0/users/{uri_encode(manager_id)}/directReports/microsoft.graph.user?$select={report_user_select_fields()}&$top=999"
    while url:
        page = graph_get(url)
        for child in page.get("value") or []:
            if isinstance(child, dict):
                children.append(child)
        url = page.get("@odata.nextLink") or ""
    return children
