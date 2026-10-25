"""Offline tests for the Claude Code marketplace-plugin merge in bin/agent_sync.py.

Run with: uv run --with pytest pytest bin/tests/test_agent_sync_marketplace_plugins.py
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


AGENT_SYNC = (Path(__file__).resolve().parents[1] / 'agent_sync.py').resolve()


@pytest.fixture
def engine():
    spec = importlib.util.spec_from_file_location('agent_sync', AGENT_SYNC)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.CONFIG.dry_run = False
    return module


@pytest.fixture
def calls(engine, monkeypatch) -> list:
    """Record CLI reconcile calls instead of running `claude plugin`."""
    recorded: list = []
    monkeypatch.setattr(engine, 'run_plugin_cli', lambda cli, args: recorded.append(args))
    return recorded


def plugin(name: str, market: str, repo: str, clients=('claude-code',)) -> dict:
    return {
        'name': name,
        'clients': list(clients),
        'id': f'{name}@{market}',
        'marketplace': {'source': 'github', 'repo': repo},
    }


def cli(target: Path) -> dict:
    return {'path': str(target), 'style': 'marketplace', 'command': ['claude', 'plugin']}


def test_marketplace_arg(engine) -> None:
    assert engine.marketplace_arg({'source': 'github', 'repo': 'u/r'}) == 'u/r'
    assert engine.marketplace_arg({'source': 'git', 'url': 'https://x/r.git'}) == 'https://x/r.git'
    assert engine.marketplace_arg({'source': 'directory', 'path': '/m'}) == '/m'


def test_merge_enables_and_registers_keeping_other_keys(engine, calls, tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(json.dumps({'model': 'opus', 'enabledPlugins': {'mine@hand': True}}))
    catalog = {'caveman': plugin('caveman', 'caveman', 'JuliusBrussee/caveman')}
    assert engine.sync_plugins('claude-code', cli(target), catalog, [])
    doc = json.loads(target.read_text())
    assert doc['model'] == 'opus'
    assert doc['enabledPlugins'] == {'mine@hand': True, 'caveman@caveman': True}
    assert doc['extraKnownMarketplaces'] == {
        'caveman': {'source': {'source': 'github', 'repo': 'JuliusBrussee/caveman'}}
    }
    assert calls == [['marketplace', 'add', 'JuliusBrussee/caveman'], ['install', 'caveman@caveman']]
    # Second run is a no-op.
    calls.clear()
    assert not engine.sync_plugins('claude-code', cli(target), catalog, [])
    assert calls == []


def test_other_clients_and_ts_plugins_ignored(engine, calls, tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    ts = {'name': 'python-clean', 'clients': ['opencode'], 'source': Path('x.ts')}
    other = plugin('x', 'm', 'u/x', clients=('opencode',))
    assert not engine.sync_plugins('claude-code', cli(target), {'python-clean': ts, 'x': other}, [])
    assert calls == []


def test_retired_plugin_uninstalled_and_orphan_marketplace_pruned(engine, calls, tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(
        json.dumps({
            'enabledPlugins': {'context-mode@context-mode': True, 'context-mode@old-name': True, 'keep@shared': True},
            'extraKnownMarketplaces': {
                'context-mode': {'source': {'source': 'github', 'repo': 'u/cm'}},
                'old-name': {'source': {'source': 'github', 'repo': 'u/cm'}},
                'shared': {'source': {'source': 'github', 'repo': 'u/s'}},
            },
        })
    )
    assert engine.sync_plugins('claude-code', cli(target), {}, ['context-mode'])
    doc = json.loads(target.read_text())
    assert doc['enabledPlugins'] == {'keep@shared': True}
    assert doc['extraKnownMarketplaces'] == {'shared': {'source': {'source': 'github', 'repo': 'u/s'}}}
    assert ['uninstall', 'context-mode@context-mode'] in calls
    assert ['marketplace', 'remove', 'context-mode'] in calls
    assert ['marketplace', 'remove', 'old-name'] in calls


def test_dry_run_writes_nothing(engine, calls, tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text('{}')
    engine.CONFIG.dry_run = True
    catalog = {'caveman': plugin('caveman', 'caveman', 'JuliusBrussee/caveman')}
    assert engine.sync_plugins('claude-code', cli(target), catalog, [])
    assert target.read_text() == '{}'
    assert calls == []


def test_load_plugins_reads_marketplace_entries(engine, tmp_path: Path, monkeypatch) -> None:
    (tmp_path / 'plugins').mkdir()
    (tmp_path / 'plugins' / 'caveman.toml').write_text(
        'name = "caveman"\nclients = ["claude-code"]\nplugin = "caveman@caveman"\n'
        '[marketplace]\nsource = "github"\nrepo = "JuliusBrussee/caveman"\n'
    )
    monkeypatch.setattr(engine, 'CATALOG', tmp_path)
    loaded = engine.load_plugins({})['caveman']
    assert loaded['id'] == 'caveman@caveman'
    assert loaded['marketplace'] == {'source': 'github', 'repo': 'JuliusBrussee/caveman'}
