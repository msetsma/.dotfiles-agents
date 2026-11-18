"""Pi packages: merge the catalog's package sources into pi's settings.json."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from .common import CONFIG, read_json_config, refuse_commented, report, write_json


__all__ = ['package_id', 'render_package', 'sync_packages']

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# packages (merge into pi's settings.json package list)
# --------------------------------------------------------------------------- #
def package_id(source: str) -> str:
    """Pi's package identity: the source minus any ``@version`` / ``@ref``."""
    for prefix in ('npm:', 'git:'):
        if source.startswith(prefix):
            body = source[len(prefix) :]
            at = body.rfind('@')
            # npm scopes and git@host URLs keep their leading/host ``@``.
            if at > 0 and '/' not in body[at:] and ':' not in body[at:]:
                body = body[:at]
            return prefix + body
    return source


def render_package(pkg: dict) -> str | dict:
    return {'source': pkg['source'], **pkg['filters']} if pkg['filters'] else pkg['source']


def _entry_source(entry: str | dict) -> str:
    return entry['source'] if isinstance(entry, dict) else entry


def sync_packages(client: str, cli: dict, packages: dict, retired: list) -> bool:
    target = Path(cli['path']).expanduser()
    doc, commented = read_json_config(target)
    current = doc.get('packages', [])
    desired = {package_id(p['source']): render_package(p) for p in packages.values() if client in p['clients']}
    retired_ids = {package_id(r) for r in retired}

    existing = {package_id(_entry_source(e)): e for e in current}
    added = sorted(i for i in desired if i not in existing)
    changed = sorted(i for i in desired if i in existing and existing[i] != desired[i])
    removed = sorted(i for i in existing if i in retired_ids and i not in desired)
    dirty = report(client, target, added, changed, removed, kind='packages')
    if not dirty:
        return dirty
    if CONFIG.dry_run:
        refuse_commented(target, commented)
        return dirty

    # Keep hand-installed packages in place; replace managed ones, append new.
    merged = []
    for entry in current:
        pid = package_id(_entry_source(entry))
        if pid in retired_ids and pid not in desired:
            continue
        merged.append(desired.pop(pid, entry))
    merged.extend(desired[i] for i in sorted(desired))
    if refuse_commented(target, commented):
        return dirty
    doc['packages'] = merged
    write_json(target, doc)
    install = cli.get('install')
    if install and (added or changed):
        if shutil.which(install[0]):
            subprocess.run(install, check=False)
        else:
            log.info('  %s not on PATH; run `%s` to install', install[0], ' '.join(install))
    return dirty
