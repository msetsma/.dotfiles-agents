"""Event hooks: per-client renderers and the additive merge into a shared
event map (Claude Code, Codex)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from .common import CONFIG, Diff, deep_get, die, read_json_config, refuse_commented, report, write_json


__all__ = ['HOOK_RENDERERS', 'r_hook_claude_code', 'r_hook_codex', 'sync_hooks']

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# per-client hook renderers
# --------------------------------------------------------------------------- #
def r_hook_claude_code(hook: dict) -> dict:
    cmd = {'type': 'command', 'command': hook['command']}
    if hook['timeout'] is not None:
        cmd['timeout'] = hook['timeout']
    if hook['status_message']:
        cmd['statusMessage'] = hook['status_message']
    return {'matcher': hook['matcher'], 'hooks': [cmd]}


def r_hook_codex(hook: dict) -> dict:
    cmd = {'type': 'command', 'command': hook['command']}
    if hook['timeout'] is not None:
        cmd['timeout'] = hook['timeout']
    return {'matcher': hook['matcher'], 'hooks': [cmd]}


HOOK_RENDERERS = {'claude-code': r_hook_claude_code, 'codex': r_hook_codex}


# --------------------------------------------------------------------------- #
# hooks (additive merge into a shared event map)
# --------------------------------------------------------------------------- #
# A catalog hook is identified by (event, command), not by its full rendered
# JSON, so editing its matcher / timeout / statusMessage replaces the group the
# catalog wrote instead of leaving it behind as an "unmanaged" duplicate. A
# group is only claimed when its single command is the command of a catalog
# hook that lists this client, or it is exactly the group sync renders here
# for a hook that does not list this client (written before the hook was
# de-listed, so sync removes it). User groups (other commands, a catalog
# command bundled with others, or a hand-written group for a hook that targets
# other clients) are never touched. Changing a hook's command string makes the old one unrecognisable:
# list the old string under `hook_commands` in catalog/retired.toml.
HookKey = tuple[str, str]


def _group_commands(group: dict) -> list[str]:
    return [entry.get('command', '') for entry in group.get('hooks', [])]


def _owned_command(event: str, group: dict, owned: set[str], delisted: dict) -> str | None:
    """The catalog command a group belongs to in this client, or None.

    ``owned`` holds the commands of hooks that list this client. ``delisted``
    maps ``(event, command)`` of hooks that do not list it to the groups sync
    would render for them here; an exact match is a group sync wrote before
    the hook was de-listed, so it is claimed (and then removed). Anything else
    is a user group.
    """
    commands = _group_commands(group)
    if len(commands) != 1:
        return None
    command = commands[0]
    if command in owned or group in delisted.get((event, command), []):
        return command
    return None


def _delisted_groups(client: str, hooks: dict, renderer: Callable[[dict], dict]) -> dict[HookKey, list[dict]]:
    """``(event, command) -> rendered groups`` for hooks that skip this client."""
    out: dict[HookKey, list[dict]] = {}
    for hook in hooks.values():
        if client not in hook['clients']:
            out.setdefault((hook['event'], hook['command']), []).append(renderer(hook))
    return out


def _collect_hook_groups(client: str, hooks: dict, renderer: Callable[[dict], dict]) -> dict[HookKey, dict]:
    """``(event, command) -> rendered group`` for this client's catalog hooks.

    Two hooks with the same key would overwrite each other, so that dies.
    """
    groups: dict[HookKey, dict] = {}
    owners: dict[HookKey, str] = {}
    for hook in hooks.values():
        if client not in hook['clients']:
            continue
        key = (hook['event'], hook['command'])
        if key in owners:
            die(
                f'[{client}] hooks {owners[key]!r} and {hook["name"]!r} share event {key[0]!r} and command '
                f'{key[1]!r}; merge them into one hook (or give one a distinct command)'
            )
        owners[key] = hook['name']
        groups[key] = renderer(hook)
    return groups


def _split_hooks(
    container: dict, owned: set[str], delisted: dict, retired_commands: list
) -> tuple[dict[str, list], dict[HookKey, list], list[str]]:
    """Existing groups as (user groups by event, catalog groups by key, retired labels)."""
    kept: dict[str, list] = {}
    managed: dict[HookKey, list] = {}
    retired_labels: list[str] = []
    for event, groups in container.items():
        for group in groups:
            command = _owned_command(event, group, owned, delisted)
            if command is not None:
                managed.setdefault((event, command), []).append(group)
            elif any(c in retired_commands for c in _group_commands(group)):
                retired_labels.append(f'retired @ {event}')
            else:
                kept.setdefault(event, []).append(group)
    return kept, managed, retired_labels


def _hook_diff(desired: dict, managed: dict, retired_labels: list, names: dict) -> Diff:
    def label(key: HookKey) -> str:
        return f'{names.get(key, key[1])} @ {key[0]}'

    added = sorted(label(k) for k in desired if k not in managed)
    changed = sorted(label(k) for k in desired if k in managed and managed[k] != [desired[k]])
    removed = sorted([label(k) for k in managed if k not in desired] + retired_labels)
    return added, changed, removed


def sync_hooks(client: str, cli: dict, hooks: dict, retired_commands: list) -> bool:
    if cli['style'] not in HOOK_RENDERERS:
        log.info('[%s] hooks: style %r not supported yet; skipping', client, cli['style'])
        return False
    target = Path(cli['path']).expanduser()
    renderer = HOOK_RENDERERS[cli['style']]
    desired = _collect_hook_groups(client, hooks, renderer)
    names = {(h['event'], h['command']): h['name'] for h in hooks.values()}
    owned = {h['command'] for h in hooks.values() if client in h['clients']}
    delisted = _delisted_groups(client, hooks, renderer)

    doc, commented = read_json_config(target)
    container = deep_get(doc, cli['container'])
    kept, managed, retired_labels = _split_hooks(container, owned, delisted, retired_commands)
    added, changed, removed = _hook_diff(desired, managed, retired_labels, names)
    dirty = report(client, target, added, changed, removed, kind='hooks')
    if not dirty or refuse_commented(target, commented) or CONFIG.dry_run:
        return dirty

    # User groups keep their order; catalog groups follow them.
    merged = {event: list(groups) for event, groups in kept.items()}
    for (event, _command), group in desired.items():
        merged.setdefault(event, []).append(group)
    container.clear()
    container.update(merged)
    write_json(target, doc)
    return dirty
