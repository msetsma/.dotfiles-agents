"""Offline tests for the pi packages merge (bin/agentsync/packages.py).

Run with: uv run --with pytest pytest bin/tests/test_agent_sync_packages.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agentsync import common
from agentsync.packages import package_id, sync_packages


@pytest.fixture(autouse=True)
def _live_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common.CONFIG, 'dry_run', False)


def pkg(source: str, **filters: object) -> dict:
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
def test_package_id(source: str, expected: str) -> None:
    assert package_id(source) == expected


def test_merge_keeps_unmanaged_and_other_keys(tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(json.dumps({'theme': 'dark', 'packages': ['npm:mine', 'npm:@s/a@1.0.0']}))
    catalog = {'a': pkg('npm:@s/a', skills=[]), 'b': pkg('npm:b')}
    assert sync_packages('pi', {'path': str(target)}, catalog, [])
    doc = json.loads(target.read_text())
    assert doc['theme'] == 'dark'
    assert doc['packages'] == ['npm:mine', {'source': 'npm:@s/a', 'skills': []}, 'npm:b']
    # Second run is a no-op.
    assert not sync_packages('pi', {'path': str(target)}, catalog, [])


def test_retired_removed_and_other_clients_ignored(tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(json.dumps({'packages': ['npm:old@1', 'npm:keep']}))
    other = {'name': 'x', 'clients': ['opencode'], 'source': 'npm:x', 'filters': {}}
    assert sync_packages('pi', {'path': str(target)}, {'x': other}, ['npm:old'])
    assert json.loads(target.read_text())['packages'] == ['npm:keep']
