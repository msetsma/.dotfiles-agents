"""Plugins: symlink each catalog ``<name>.ts`` into a client's plugins dir, or
hand a marketplace-style client to ``marketplace.sync_marketplace_plugins``."""

from __future__ import annotations

import os
from pathlib import Path

from . import common
from .common import CONFIG, report
from .marketplace import sync_marketplace_plugins


__all__ = ['sync_plugins']


# --------------------------------------------------------------------------- #
# plugins (symlink strategy)
# --------------------------------------------------------------------------- #
def sync_plugins(client: str, cli: dict, plugins: dict, retired: list) -> bool:
    if cli.get('style') == 'marketplace':
        return sync_marketplace_plugins(client, cli, plugins, retired)
    base = Path(cli['path']).expanduser()
    manage_root = (common.CATALOG / 'plugins').resolve()
    desired = {
        f'{n}.ts': p for n, p in plugins.items() if client in p['clients'] and 'source' in p and n not in retired
    }

    # Only touch symlinks that point back into our catalog/plugins/.
    existing: dict[str, str] = {}
    if base.is_dir():
        for entry in base.iterdir():
            if entry.is_symlink():
                resolved = os.path.realpath(entry)
                if resolved == str(manage_root) or resolved.startswith(str(manage_root) + os.sep):
                    existing[entry.name] = resolved

    added = sorted(n for n in desired if n not in existing)
    changed = sorted(n for n in desired if n in existing and existing[n] != str(desired[n]['source'].resolve()))
    removed = sorted(n for n in existing if n not in desired)
    dirty = report(client, base, added, changed, removed, kind='plugins')
    if CONFIG.dry_run or not dirty:
        return dirty

    base.mkdir(parents=True, exist_ok=True)
    for name in removed + changed:
        (base / name).unlink()
    for name in added + changed:
        (base / name).symlink_to(desired[name]['source'])
    return dirty
