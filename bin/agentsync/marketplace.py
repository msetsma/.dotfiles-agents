"""Claude Code marketplace plugins: merge enabledPlugins, extraKnownMarketplaces
and pluginConfigs into settings.json, then reconcile with ``claude plugin``."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from .common import CONFIG, Diff, read_json_config, refuse_commented, report, write_json


__all__ = ['market_of', 'marketplace_arg', 'plugin_load_order', 'run_plugin_cli', 'sync_marketplace_plugins']

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# marketplace plugins (merge into Claude Code settings.json, then reconcile)
# --------------------------------------------------------------------------- #
def marketplace_arg(source: dict) -> str:
    """The argument ``claude plugin marketplace add`` takes for a source."""
    if source['source'] == 'github':
        return source['repo']
    if source['source'] == 'git':
        return source['url']
    return source['path']


def market_of(plugin_id: str) -> str:
    return plugin_id.partition('@')[2]


def run_plugin_cli(cli: dict, args: list[str]) -> None:
    command = cli['command']
    exe = shutil.which(command[0])
    if not exe:
        log.info('  %s not on PATH; run `%s`', command[0], ' '.join([*command, *args]))
        return
    subprocess.run([exe, *command[1:], *args], check=False)


def _options_drift(configs: dict, plugin_id: str, plugin: dict) -> bool:
    return configs.get(plugin_id, {}).get('options') != plugin.get('options')


def _want_config(current: dict, plugin: dict) -> dict:
    """A catalog plugin's pluginConfigs entry: ``options`` from the catalog (or
    none at all), every other key as the user left it."""
    rest = {k: v for k, v in current.items() if k != 'options'}
    return rest | {'options': plugin['options']} if 'options' in plugin else rest


def _merge_configs(configs: dict, desired: dict, removed: list) -> dict:
    merged = {}
    for plugin_id, current in configs.items():
        if plugin_id in removed:
            continue
        config = _want_config(current, desired[plugin_id]) if plugin_id in desired else current
        if config:
            merged[plugin_id] = config
    for plugin_id, plugin in desired.items():
        if plugin_id not in merged and 'options' in plugin:
            merged[plugin_id] = {'options': plugin['options']}
    return merged


def plugin_load_order(desired: dict, enabled: dict) -> list[str]:
    """The full enabledPlugins sequence: catalog and hand-installed ids.

    Claude Code nests mods in enabledPlugins order, first outermost. Catalog
    plugins with an integer ``order`` go at the very front, lowest first (ties
    by name). Every other id (unranked catalog plugins and hand-installed
    ones) keeps its existing position, and new unranked catalog plugins append
    at the end by name.
    """
    ranked = sorted((p for p in desired.values() if p.get('order') is not None), key=lambda p: (p['order'], p['name']))
    ranked_ids = [p['id'] for p in ranked]
    kept = [i for i in enabled if i not in ranked_ids]
    new = sorted((i for i in desired if i not in enabled and i not in ranked_ids), key=lambda i: desired[i]['name'])
    return ranked_ids + kept + new


def _order_drift(enabled: dict, desired: dict) -> bool:
    return list(enabled) != [i for i in plugin_load_order(desired, enabled) if i in enabled]


def _plugin_changes(enabled: dict, markets: dict, configs: dict, desired: dict, retired: list) -> tuple[dict, *Diff]:
    want_markets = {market_of(i): {'source': p['marketplace']} for i, p in desired.items()}
    added = sorted(i for i in desired if enabled.get(i) is not True)
    changed = sorted(
        i
        for i, p in desired.items()
        if i not in added and (markets.get(market_of(i)) != want_markets[market_of(i)] or _options_drift(configs, i, p))
    )
    removed = sorted(i for i in enabled if i.partition('@')[0] in retired and i not in desired)
    if _order_drift(enabled, desired):
        changed.append('enabledPlugins order')
    return want_markets, added, changed, removed


def _orphan_markets(enabled: dict, removed: list, want_markets: dict) -> list[str]:
    """Marketplaces only removed plugins used; nothing still enabled needs them."""
    still_used = {market_of(i) for i in enabled if i not in removed} | set(want_markets)
    return sorted({market_of(i) for i in removed} - still_used)


def sync_marketplace_plugins(client: str, cli: dict, plugins: dict, retired: list) -> bool:
    target = Path(cli['path']).expanduser()
    doc, commented = read_json_config(target)
    enabled = doc.get('enabledPlugins', {})
    markets = doc.get('extraKnownMarketplaces', {})
    configs = doc.get('pluginConfigs', {})
    desired = {p['id']: p for n, p in plugins.items() if client in p['clients'] and 'id' in p and n not in retired}
    want_markets, added, changed, removed = _plugin_changes(enabled, markets, configs, desired, retired)
    dirty = report(client, target, added, changed, removed, kind='plugins')
    if not dirty or refuse_commented(target, commented) or CONFIG.dry_run:
        return dirty

    orphans = _orphan_markets(enabled, removed, want_markets)
    # Ranked catalog plugins first; everything else keeps its slot, new ones last.
    # Catalog plugins are enabled; hand-installed ones keep their value.
    doc['enabledPlugins'] = {
        i: True if i in desired else enabled[i] for i in plugin_load_order(desired, enabled) if i not in removed
    }
    doc['extraKnownMarketplaces'] = {m: s for m, s in markets.items() if m not in orphans} | want_markets
    # A catalog plugin's `[options]` table owns pluginConfigs[id].options (dropped
    # when the table goes); other keys and non-catalog plugins survive.
    merged_configs = _merge_configs(configs, desired, removed)
    if merged_configs or 'pluginConfigs' in doc:
        doc['pluginConfigs'] = merged_configs
    write_json(target, doc)

    # The settings declare intent; the CLI fetches marketplaces and plugin caches.
    for plugin_id in removed:
        run_plugin_cli(cli, ['uninstall', plugin_id])
    for name in orphans:
        run_plugin_cli(cli, ['marketplace', 'remove', name])
    for plugin_id in added:
        if market_of(plugin_id) not in markets:
            run_plugin_cli(cli, ['marketplace', 'add', marketplace_arg(desired[plugin_id]['marketplace'])])
        run_plugin_cli(cli, ['install', plugin_id])
    return dirty
