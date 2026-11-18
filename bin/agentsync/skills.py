"""Skills: symlink each catalog skill directory into a client's skills dir."""

from __future__ import annotations

import os
from pathlib import Path

from . import common
from .common import CONFIG, report


__all__ = ['sync_skills']


# --------------------------------------------------------------------------- #
# skills (symlink strategy)
# --------------------------------------------------------------------------- #
def sync_skills(client: str, cli: dict, skills: dict, retired: list) -> bool:
    base = Path(cli['path']).expanduser()
    manage_root = (common.CATALOG / 'skills').resolve()
    desired = {n: s for n, s in skills.items() if client in s['clients'] and n not in retired}

    # Only touch symlinks that point back into our catalog/skills/<name>/.
    existing: dict[str, str] = {}
    if base.is_dir():
        for entry in base.iterdir():
            if entry.is_symlink():
                resolved = os.path.realpath(entry)
                if resolved == str(manage_root) or resolved.startswith(str(manage_root) + os.sep):
                    existing[entry.name] = resolved

    added = sorted(n for n in desired if n not in existing)
    changed = sorted(n for n in desired if n in existing and existing[n] != str(desired[n]['dir'].resolve()))
    removed = sorted(n for n in existing if n not in desired)
    dirty = report(client, base, added, changed, removed, kind='skills')
    if CONFIG.dry_run or not dirty:
        return dirty

    base.mkdir(parents=True, exist_ok=True)
    for name in removed + changed:
        (base / name).unlink()
    for name in added + changed:
        (base / name).symlink_to(desired[name]['dir'], target_is_directory=True)
    return dirty
