#!/usr/bin/env python3
"""agent-sync - materialise the agent catalog into every client's config.

Single source of truth
----------------------
  catalog/mcp/*.toml   one file per MCP server (custom + third-party)
  catalog/skills/*.toml    one file per skill (content in catalog/skills/<name>/)
  catalog/plugins/*.toml   one file per plugin (content in catalog/plugins/<name>.ts)
  catalog/hooks/*.toml     one file per event hook
  catalog/instructions/*.toml  one file per instruction overlay (content in <name>.md)
  catalog/packages/*.toml  one file per pi package (npm / git / local source)
  catalog/clients.toml     where each agent keeps each kind, and how it spells it
  catalog/paths.toml       machine-specific command paths + variables
  catalog/retired.toml     resources to scrub from every client
  catalog/local.toml       untracked per-machine overrides + [profile] selection
                           (see bin/agent_profile.py)

Behaviour
---------
Each client declares one block per *kind* it supports. Three strategies:

* ``merge``     rewrite a subtree of a shared config in place, leaving every
                other key alone (MCP maps; hook event maps; pi package list).
* ``symlink``   link each catalog item into a client directory (skills, plugins).
* ``block``     swap a marked region inside a shared markdown file, leaving the
                rest of the file alone (instructions).

The engine lives in ``bin/agentsync/``, one module per resource kind (catalog
loading, mcp, skills, plugins, marketplace, hooks, instructions, packages) over
shared helpers in ``common``. This script is the entry point: it loads the
catalog and runs each client's syncers.

Usage
-----
  agent-sync                apply changes
  agent-sync --dry-run      semantic diff, write nothing
  agent-sync --client codex limit to one client
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


# Sibling modules, importable even when the script dir is not on sys.path (-P).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_profile import deep_merge, select_clients
from agentsync import common
from agentsync.catalog import load_catalogs, load_retired
from agentsync.common import CONFIG, build_context, die, load_local, load_toml, warn
from agentsync.hooks import sync_hooks
from agentsync.instructions import sync_instructions
from agentsync.mcp import MCP_SYNCERS, mcp_client_kind, render
from agentsync.packages import sync_packages
from agentsync.plugins import sync_plugins
from agentsync.skills import sync_skills


__all__ = ['main', 'sync_client', 'warn_unsupported']


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def sync_client(client: str, kinds: dict, catalogs: dict, retired: dict) -> bool:
    dirty = False
    if 'mcp' in kinds:
        cli = kinds['mcp']
        mcp = catalogs['mcp']
        desired = {n: render(s, cli, client) for n, s in mcp.items() if client in s['clients']}
        dirty |= MCP_SYNCERS[mcp_client_kind(cli)](client, cli, desired, retired['mcp'])
    if 'skills' in kinds:
        dirty |= sync_skills(client, kinds['skills'], catalogs['skills'], retired['skills'])
    if 'plugins' in kinds:
        dirty |= sync_plugins(client, kinds['plugins'], catalogs['plugins'], retired['plugins'])
    if 'hooks' in kinds:
        dirty |= sync_hooks(client, kinds['hooks'], catalogs['hooks'], retired['hook_commands'])
    if 'packages' in kinds:
        dirty |= sync_packages(client, kinds['packages'], catalogs['packages'], retired['packages'])
    if 'instructions' in kinds:
        dirty |= sync_instructions(client, kinds['instructions'], catalogs['instructions'], retired['instructions'])
    return dirty


def warn_unsupported(clients: dict, catalogs: dict, selected: list[str]) -> None:
    """Warn when a catalog item names a selected client lacking that kind's block.

    ``sync_client`` only syncs the kinds a client declares, so such an item would
    otherwise be skipped without a word.
    """
    for kind, items in catalogs.items():
        for name, item in sorted(items.items()):
            for client in item['clients']:
                if client in selected and kind not in clients.get(client, {}):
                    warn(f'{kind} {name!r} lists {client}, which has no [{client}.{kind}] block; skipped')


def main() -> int:
    ap = argparse.ArgumentParser(description='Sync the agent catalog into client configs.')
    ap.add_argument('--dry-run', action='store_true', help='show changes, write nothing')
    ap.add_argument('--client', action='append', help='limit to a client (repeatable)')
    args = ap.parse_args()
    CONFIG.dry_run = args.dry_run

    local = load_local()
    profile = local.get('profile', {})
    ctx = build_context(local)
    catalogs = load_catalogs(ctx, local, profile)
    clients = deep_merge(load_toml(common.CATALOG / 'clients.toml'), local.get('clients', {}))
    retired = load_retired(common.CATALOG / 'retired.toml', ctx)

    wanted = profile.get('clients')
    requested = args.client or (wanted if isinstance(wanted, list) else [])
    selected = args.client or select_clients(clients, profile)
    unknown = [c for c in requested if c not in clients]
    if unknown:
        die(f'unknown client(s): {", ".join(unknown)}')

    warn_unsupported(clients, catalogs, selected)
    dirty_any = False
    for client in selected:
        dirty_any |= sync_client(client, clients[client], catalogs, retired)
    print()
    if CONFIG.dry_run:
        print('dry-run complete - nothing written')
    else:
        print('done' if dirty_any else 'already in sync')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
