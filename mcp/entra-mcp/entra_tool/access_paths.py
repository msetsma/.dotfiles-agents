"""Walk the group-containment chain between a person and a group.

Graph reports effective membership (`transitiveMemberOf`) but never the route.
The route is a containment chain: the person is a member of a group, that
group is a member of another, and so on up to the target.

This finds it with a bidirectional breadth-first search: one frontier climbs
from the person (parent groups), the other descends from the target (child
groups), and each wave expands whichever frontier is smaller. A person is
often directly in 40+ groups while a target holds only a handful, so the
search usually meets after expanding the target side, a couple of waves
instead of fanning out across everything the person belongs to.

The Graph I/O is injected as batch fetchers so this stays pure and testable:
each takes a list of node ids and returns ``{node_id: [group, ...]}``, where a
group is any dict with an ``id`` and optional ``displayName``.
"""

from collections.abc import Callable, Sequence
from typing import Any


ParentsFetcher = Callable[[Sequence[str]], dict[str, list[dict[str, Any]]]]
ChildrenFetcher = Callable[[Sequence[str]], dict[str, list[dict[str, Any]]]]


def _chain(node: str, parents: dict[str, str | None], root: str) -> list[str]:
    """Follow predecessors from ``node`` back to ``root`` (inclusive)."""
    chain: list[str] = []
    current: str | None = node
    while current is not None:
        chain.append(current)
        if current == root:
            break
        current = parents.get(current)
    return chain


def _join_path(
    meeting: str,
    user_id: str,
    target_group_id: str,
    fwd_parent: dict[str, str | None],
    bwd_parent: dict[str, str | None],
) -> list[str]:
    upward = list(reversed(_chain(meeting, fwd_parent, user_id)))
    downward = _chain(meeting, bwd_parent, target_group_id)
    return upward + downward[1:]


def find_containment_path(
    user_id: str,
    target_group_id: str,
    fetch_parents: ParentsFetcher,
    fetch_children: ChildrenFetcher,
    *,
    max_nodes: int = 120,
) -> tuple[list[str], dict[str, str]] | None:
    """Return ``(path_ids, display_names)`` from user to target, or None.

    ``path_ids`` runs from the person to the target, each entry a member of the
    next. ``display_names`` maps every group encountered to its name. None means
    no path was found: either there is none, or ``max_nodes`` was reached
    before the frontiers met; the caller decides how to report that.
    """
    if user_id == target_group_id:
        return [user_id], {}

    names: dict[str, str] = {}
    fwd_parent: dict[str, str | None] = {user_id: None}
    bwd_parent: dict[str, str | None] = {target_group_id: None}
    fwd_frontier = [user_id]
    bwd_frontier = [target_group_id]
    expanded = 0

    while fwd_frontier and bwd_frontier and expanded < max_nodes:
        forward = len(fwd_frontier) <= len(bwd_frontier)
        frontier = fwd_frontier if forward else bwd_frontier
        seen = fwd_parent if forward else bwd_parent
        other = bwd_parent if forward else fwd_parent
        batch = (fetch_parents if forward else fetch_children)(frontier)

        next_frontier: list[str] = []
        meeting: str | None = None
        for node in frontier:
            expanded += 1
            for group in batch.get(node, []):
                group_id = group.get('id')
                if not group_id or group_id in seen:
                    continue
                names.setdefault(group_id, group.get('displayName') or '')
                seen[group_id] = node
                if group_id in other:
                    meeting = group_id
                    break
                next_frontier.append(group_id)
            if meeting:
                break

        if meeting:
            return (_join_path(meeting, user_id, target_group_id, fwd_parent, bwd_parent), names)
        if forward:
            fwd_frontier = next_frontier
        else:
            bwd_frontier = next_frontier

    return None
