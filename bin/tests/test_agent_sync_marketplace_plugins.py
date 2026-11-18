"""Offline tests for the Claude Code marketplace-plugin merge (bin/agentsync/marketplace.py).

Run with: uv run --with pytest pytest bin/tests/test_agent_sync_marketplace_plugins.py
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from agentsync import common, marketplace
from agentsync.catalog import load_plugins
from agentsync.marketplace import marketplace_arg
from agentsync.plugins import sync_plugins


@pytest.fixture(autouse=True)
def _live_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common.CONFIG, 'dry_run', False)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list:
    """Record CLI reconcile calls instead of running `claude plugin`."""
    recorded: list = []
    monkeypatch.setattr(marketplace, 'run_plugin_cli', lambda cli, args: recorded.append(args))
    return recorded


def plugin(name: str, market: str, repo: str, clients: Sequence[str] = ('claude-code',)) -> dict:
    return {
        'name': name,
        'clients': list(clients),
        'id': f'{name}@{market}',
        'marketplace': {'source': 'github', 'repo': repo},
    }


def cli(target: Path) -> dict:
    return {'path': str(target), 'style': 'marketplace', 'command': ['claude', 'plugin']}


def test_marketplace_arg() -> None:
    assert marketplace_arg({'source': 'github', 'repo': 'u/r'}) == 'u/r'
    assert marketplace_arg({'source': 'git', 'url': 'https://x/r.git'}) == 'https://x/r.git'
    assert marketplace_arg({'source': 'directory', 'path': '/m'}) == '/m'


def test_merge_enables_and_registers_keeping_other_keys(calls: list, tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(json.dumps({'model': 'opus', 'enabledPlugins': {'mine@hand': True}}))
    catalog = {'caveman': plugin('caveman', 'caveman', 'JuliusBrussee/caveman')}
    assert sync_plugins('claude-code', cli(target), catalog, [])
    doc = json.loads(target.read_text())
    assert doc['model'] == 'opus'
    assert doc['enabledPlugins'] == {'mine@hand': True, 'caveman@caveman': True}
    assert doc['extraKnownMarketplaces'] == {
        'caveman': {'source': {'source': 'github', 'repo': 'JuliusBrussee/caveman'}}
    }
    assert calls == [['marketplace', 'add', 'JuliusBrussee/caveman'], ['install', 'caveman@caveman']]
    # Second run is a no-op.
    calls.clear()
    assert not sync_plugins('claude-code', cli(target), catalog, [])
    assert calls == []


def test_other_clients_and_ts_plugins_ignored(calls: list, tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    ts = {'name': 'python-clean', 'clients': ['opencode'], 'source': Path('x.ts')}
    other = plugin('x', 'm', 'u/x', clients=('opencode',))
    assert not sync_plugins('claude-code', cli(target), {'python-clean': ts, 'x': other}, [])
    assert calls == []


def test_retired_plugin_uninstalled_and_orphan_marketplace_pruned(calls: list, tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(
        json.dumps(
            {
                'enabledPlugins': {
                    'context-mode@context-mode': True,
                    'context-mode@old-name': True,
                    'keep@shared': True,
                },
                'extraKnownMarketplaces': {
                    'context-mode': {'source': {'source': 'github', 'repo': 'u/cm'}},
                    'old-name': {'source': {'source': 'github', 'repo': 'u/cm'}},
                    'shared': {'source': {'source': 'github', 'repo': 'u/s'}},
                },
            }
        )
    )
    assert sync_plugins('claude-code', cli(target), {}, ['context-mode'])
    doc = json.loads(target.read_text())
    assert doc['enabledPlugins'] == {'keep@shared': True}
    assert doc['extraKnownMarketplaces'] == {'shared': {'source': {'source': 'github', 'repo': 'u/s'}}}
    assert ['uninstall', 'context-mode@context-mode'] in calls
    assert ['marketplace', 'remove', 'context-mode'] in calls
    assert ['marketplace', 'remove', 'old-name'] in calls


def test_dry_run_writes_nothing(calls: list, tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text('{}')
    common.CONFIG.dry_run = True
    catalog = {'caveman': plugin('caveman', 'caveman', 'JuliusBrussee/caveman')}
    assert sync_plugins('claude-code', cli(target), catalog, [])
    assert target.read_text() == '{}'
    assert calls == []


def test_load_plugins_reads_marketplace_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / 'plugins').mkdir()
    (tmp_path / 'plugins' / 'caveman.toml').write_text(
        'name = "caveman"\nclients = ["claude-code"]\nplugin = "caveman@caveman"\n'
        '[marketplace]\nsource = "github"\nrepo = "JuliusBrussee/caveman"\n'
    )
    monkeypatch.setattr(common, 'CATALOG', tmp_path)
    loaded = load_plugins({}, {})['caveman']
    assert loaded['id'] == 'caveman@caveman'
    assert loaded['marketplace'] == {'source': 'github', 'repo': 'JuliusBrussee/caveman'}


def test_options_merge_into_plugin_configs(calls: list, tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(
        json.dumps(
            {
                'enabledPlugins': {'p@m': True},
                'extraKnownMarketplaces': {'m': {'source': {'source': 'github', 'repo': 'u/p'}}},
                'pluginConfigs': {'p@m': {'options': {'theme': 'old'}}, 'mine@hand': {'options': {'a': 1}}},
            }
        )
    )
    catalog = {'p': plugin('p', 'm', 'u/p') | {'options': {'theme': 'mocha', 'clock': '12h'}}}
    assert sync_plugins('claude-code', cli(target), catalog, [])
    doc = json.loads(target.read_text())
    assert doc['pluginConfigs'] == {
        'p@m': {'options': {'theme': 'mocha', 'clock': '12h'}},
        'mine@hand': {'options': {'a': 1}},
    }
    assert calls == []
    assert not sync_plugins('claude-code', cli(target), catalog, [])


def test_retired_plugin_drops_its_config(calls: list, tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(
        json.dumps({'enabledPlugins': {'old@m': True}, 'pluginConfigs': {'old@m': {'options': {'x': 1}}}})
    )
    assert sync_plugins('claude-code', cli(target), {}, ['old'])
    assert 'old@m' not in json.loads(target.read_text())['pluginConfigs']


def test_dropped_options_are_removed_from_plugin_configs(calls: list, tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(
        json.dumps(
            {
                'enabledPlugins': {'p@m': True, 'mine@hand': True},
                'extraKnownMarketplaces': {'m': {'source': {'source': 'github', 'repo': 'u/p'}}},
                'pluginConfigs': {
                    'p@m': {'options': {'theme': 'old'}, 'other': 1},
                    'q@m': {'options': {'x': 1}},
                    'mine@hand': {'options': {'a': 1}},
                },
            }
        )
    )
    catalog = {'p': plugin('p', 'm', 'u/p'), 'q': plugin('q', 'm', 'u/p')}
    assert sync_plugins('claude-code', cli(target), catalog, [])
    assert json.loads(target.read_text())['pluginConfigs'] == {'p@m': {'other': 1}, 'mine@hand': {'options': {'a': 1}}}
    assert not sync_plugins('claude-code', cli(target), catalog, [])


def test_load_plugins_reads_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / 'plugins').mkdir()
    base = 'clients = ["claude-code"]\n[marketplace]\nsource = "directory"\npath = "/m"\n'
    (tmp_path / 'plugins' / 'a.toml').write_text(f'name = "a"\nplugin = "a@m"\norder = 5\n{base}')
    (tmp_path / 'plugins' / 'b.toml').write_text(f'name = "b"\nplugin = "b@m"\n{base}')
    monkeypatch.setattr(common, 'CATALOG', tmp_path)
    loaded = load_plugins({}, {})
    assert loaded['a']['order'] == 5
    assert loaded['b']['order'] is None


def test_fresh_sync_orders_catalog_plugins_by_order_then_name(calls: list, tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(json.dumps({'enabledPlugins': {'mine@hand': True}}))
    catalog = {
        'sidebar': plugin('sidebar', 'mods', 'u/m'),
        'caveman': plugin('caveman', 'caveman', 'u/c'),
        'style': plugin('style', 'mods', 'u/m') | {'order': 0},
    }
    assert sync_plugins('claude-code', cli(target), catalog, [])
    order = list(json.loads(target.read_text())['enabledPlugins'])
    assert order == ['style@mods', 'mine@hand', 'caveman@caveman', 'sidebar@mods']


def test_hand_installed_plugin_keeps_its_position(calls: list, tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(
        json.dumps(
            {
                'enabledPlugins': {'style@mods': True, 'a@mods': True, 'mine@hand': False, 'b@mods': True},
                'extraKnownMarketplaces': {'mods': {'source': {'source': 'github', 'repo': 'u/m'}}},
                'pluginConfigs': {'a@mods': {'options': {'x': 1}}},
            }
        )
    )
    catalog = {
        'style': plugin('style', 'mods', 'u/m') | {'order': 0},
        'a': plugin('a', 'mods', 'u/m') | {'options': {'x': 2}},
        'b': plugin('b', 'mods', 'u/m'),
    }
    assert sync_plugins('claude-code', cli(target), catalog, [])
    enabled = json.loads(target.read_text())['enabledPlugins']
    assert enabled == {'style@mods': True, 'a@mods': True, 'mine@hand': False, 'b@mods': True}
    assert list(enabled) == ['style@mods', 'a@mods', 'mine@hand', 'b@mods']
    assert not sync_plugins('claude-code', cli(target), catalog, [])


def test_ranked_plugin_moves_to_front_past_hand_installed(calls: list, tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(
        json.dumps(
            {
                'enabledPlugins': {'mine@hand': True, 'a@mods': True, 'style@mods': True, 'old@mods': True},
                'extraKnownMarketplaces': {'mods': {'source': {'source': 'github', 'repo': 'u/m'}}},
            }
        )
    )
    catalog = {
        'style': plugin('style', 'mods', 'u/m') | {'order': 0},
        'a': plugin('a', 'mods', 'u/m'),
        'new': plugin('new', 'mods', 'u/m'),
    }
    assert sync_plugins('claude-code', cli(target), catalog, ['old'])
    order = list(json.loads(target.read_text())['enabledPlugins'])
    assert order == ['style@mods', 'mine@hand', 'a@mods', 'new@mods']
    calls.clear()
    assert not sync_plugins('claude-code', cli(target), catalog, ['old'])


def test_out_of_order_catalog_plugins_are_reordered(calls: list, tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(
        json.dumps(
            {
                'enabledPlugins': {'sidebar@mods': True, 'style@mods': True},
                'extraKnownMarketplaces': {'mods': {'source': {'source': 'github', 'repo': 'u/m'}}},
            }
        )
    )
    catalog = {'sidebar': plugin('sidebar', 'mods', 'u/m'), 'style': plugin('style', 'mods', 'u/m') | {'order': 0}}
    assert sync_plugins('claude-code', cli(target), catalog, [])
    assert list(json.loads(target.read_text())['enabledPlugins']) == ['style@mods', 'sidebar@mods']
    assert calls == []
    assert not sync_plugins('claude-code', cli(target), catalog, [])


def test_unordered_catalog_plugins_keep_their_existing_order(calls: list, tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(
        json.dumps(
            {
                'enabledPlugins': {'style@mods': True, 'sidebar@mods': True},
                'extraKnownMarketplaces': {'mods': {'source': {'source': 'github', 'repo': 'u/m'}}},
            }
        )
    )
    catalog = {'sidebar': plugin('sidebar', 'mods', 'u/m'), 'style': plugin('style', 'mods', 'u/m')}
    assert not sync_plugins('claude-code', cli(target), catalog, [])


def test_bad_order_dies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / 'plugins').mkdir()
    (tmp_path / 'plugins' / 'a.toml').write_text(
        'name = "a"\nplugin = "a@m"\norder = "first"\nclients = []\n[marketplace]\nsource = "directory"\npath = "/m"\n'
    )
    monkeypatch.setattr(common, 'CATALOG', tmp_path)
    with pytest.raises(SystemExit):
        load_plugins({}, {})


def test_real_catalog_loads_style_before_sidebar(calls: list, tmp_path: Path) -> None:
    """mods/README.md: style must sit above other mods to frame their bands."""
    catalog = load_plugins({'AGENT_HOME': str(tmp_path)}, {})
    mods = {n: p for n, p in catalog.items() if n in ('style', 'sidebar')}
    target = tmp_path / 'settings.json'
    target.write_text('{}')
    sync_plugins('claude-code', cli(target), mods, [])
    order = list(json.loads(target.read_text())['enabledPlugins'])
    assert order.index('style@msetsma-mods') < order.index('sidebar@msetsma-mods')
