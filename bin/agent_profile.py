"""agent_profile - per-machine selection layer for agent-sync.

Everything here is driven by optional tables in the untracked
``catalog/local.toml``. With none of them present every helper is the identity,
so a machine without a profile syncs exactly what the catalog lists.

  [profile]
  clients      = ["claude-code"]   # or "auto"; omitted = every client
  exclude_tags = ["work"]          # drop catalog items carrying these tags
  exclude      = ["obscura"]       # drop catalog items by name (any kind)

  [clients.<client>.<kind>]        # deep-merged over catalog/clients.toml
  [<kind>.<name>]                  # deep-merged over catalog/<kind>/<name>.toml
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path


__all__ = ['deep_merge', 'detect_clients', 'is_selected', 'select_clients', 'split_command']

AUTO = 'auto'


def deep_merge(base: Mapping, override: Mapping) -> dict:
    """Return a new dict: ``override`` merged into ``base``, tables recursively."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def is_selected(item: Mapping, profile: Mapping) -> bool:
    """False when the profile excludes ``item`` by name or by tag."""
    if item['name'] in profile.get('exclude', []):
        return False
    return not set(item.get('tags', [])) & set(profile.get('exclude_tags', []))


def _client_paths(kinds: Mapping) -> list[Path]:
    return [Path(block['path']).expanduser() for block in kinds.values() if 'path' in block]


def detect_clients(clients: Mapping, exists: Callable[[Path], bool] = Path.exists) -> list[str]:
    """Clients with at least one config path whose parent directory exists."""
    return [name for name, kinds in clients.items() if any(exists(p.parent) for p in _client_paths(kinds))]


def select_clients(clients: Mapping, profile: Mapping, exists: Callable[[Path], bool] = Path.exists) -> list[str]:
    """Clients this machine syncs: profile list, ``"auto"`` detection, or all."""
    wanted = profile.get('clients')
    if wanted is None:
        return list(clients)
    if wanted == AUTO:
        return detect_clients(clients, exists)
    return [name for name in clients if name in wanted]


def split_command(command: str | list[str]) -> tuple[str, list[str]]:
    """A command may be a list (``["cmd", "/c", "npx"]``): head + args prefix."""
    if isinstance(command, str):
        return command, []
    return command[0], list(command[1:])
