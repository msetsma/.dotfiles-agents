"""Offline test for cloning missing git packages in bin/agent_update.py.

Run with: uv run --with pytest pytest bin/tests/test_agent_update_clone.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


AGENT_UPDATE = Path(__file__).resolve().parents[1] / 'agent_update.py'


def of[T](updates: tuple[object, ...], kind: type[T]) -> tuple[T, ...]:
    """The updates of one kind, in check order."""
    return tuple(u for u in updates if isinstance(u, kind))


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location('agent_update', AGENT_UPDATE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules['agent_update'] = module  # @dataclass resolves its module by name
    spec.loader.exec_module(module)
    return module


def test_missing_repo_path_is_planned_and_cloned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    up = load()
    calls = []
    monkeypatch.setattr(
        up, 'sh', lambda cmd, cwd=None: calls.append(cmd) or up.subprocess.CompletedProcess(cmd, 0, '', '')
    )
    dest = tmp_path / 'vendor' / 'notes-mcp'
    pkg = {'manager': 'git', 'repo': 'msetsma/notes-mcp', 'path': str(dest)}

    server = up.Server.from_catalog('notes', Path('notes.toml'), pkg, {})
    status = up.git_status(server, up.Check(fetch=False))
    plan = up.Plan(updates=status.updates)

    assert status.text.startswith('not cloned')
    assert of(plan.updates, up.Clone) == (up.Clone(server),)
    assert (server.name, server.repo, server.checkout) == ('notes', 'msetsma/notes-mcp', str(dest))
    assert calls == []  # reporting never touches the network

    resyncs = []
    monkeypatch.setattr(up, 'resync', lambda: resyncs.append(1) or True)
    assert up.apply_updates(plan) == []
    assert dest.parent.is_dir()
    assert ['git', 'clone', '--quiet', 'git@github.com:msetsma/notes-mcp.git', str(dest)] in calls
    assert resyncs == [1]  # a new clone is applied work, so configs are re-synced


def test_existing_path_is_not_cloned(tmp_path: Path) -> None:
    up = load()
    server = up.Server.from_catalog('x', Path('x.toml'), {'manager': 'git', 'repo': 'o/x', 'path': str(tmp_path)}, {})
    status = up.git_status(server, up.Check(fetch=False))
    assert of(status.updates, up.Clone) == ()
