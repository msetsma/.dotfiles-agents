"""Offline tests for the pi packages merge in bin/agent_sync.py.

Run with: uv run --with pytest pytest bin/tests/test_agent_sync_packages.py
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


def pkg(source: str, **filters) -> dict:
    return {'name': source, 'clients': ['pi'], 'source': source, 'filters': filters}


@pytest.mark.parametrize(
    ('source', 'expected'),
    [
        ('npm:pi-foo', 'npm:pi-foo'),
        ('npm:pi-foo@1.2.3', 'npm:pi-foo'),
        ('npm:@scope/foo', 'npm:@scope/foo'),
        ('npm:@scope/foo@2', 'npm:@scope/foo'),
        ('git:github.com/u/r@v1', 'git:github.com/u/r'),
        ('git:git@github.com:u/r', 'git:git@github.com:u/r'),
        ('git:git@github.com:u/r@v1', 'git:git@github.com:u/r'),
        ('./local', './local'),
    ],
)
def test_package_id(engine, source: str, expected: str) -> None:
    assert engine.package_id(source) == expected


def test_merge_keeps_unmanaged_and_other_keys(engine, tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(json.dumps({'theme': 'dark', 'packages': ['npm:mine', 'npm:@s/a@1.0.0']}))
    catalog = {'a': pkg('npm:@s/a', skills=[]), 'b': pkg('npm:b')}
    assert engine.sync_packages('pi', {'path': str(target)}, catalog, [])
    doc = json.loads(target.read_text())
    assert doc['theme'] == 'dark'
    assert doc['packages'] == ['npm:mine', {'source': 'npm:@s/a', 'skills': []}, 'npm:b']
    # Second run is a no-op.
    assert not engine.sync_packages('pi', {'path': str(target)}, catalog, [])


def test_retired_removed_and_other_clients_ignored(engine, tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(json.dumps({'packages': ['npm:old@1', 'npm:keep']}))
    other = {'name': 'x', 'clients': ['opencode'], 'source': 'npm:x', 'filters': {}}
    assert engine.sync_packages('pi', {'path': str(target)}, {'x': other}, ['npm:old'])
    assert json.loads(target.read_text())['packages'] == ['npm:keep']
