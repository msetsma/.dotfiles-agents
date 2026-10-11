"""Offline test for cloning missing git packages in bin/agent_update.py.

Run with: uv run --with pytest pytest bin/tests/test_agent_update_clone.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


AGENT_UPDATE = Path(__file__).resolve().parents[1] / 'agent_update.py'


def load():
    spec = importlib.util.spec_from_file_location('agent_update', AGENT_UPDATE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules['agent_update'] = module  # @dataclass resolves its module by name
    spec.loader.exec_module(module)
    return module


def test_missing_repo_path_is_planned_and_cloned(tmp_path, monkeypatch):
    up = load()
    calls = []
    monkeypatch.setattr(
        up, 'sh', lambda cmd, cwd=None: calls.append(cmd) or up.subprocess.CompletedProcess(cmd, 0, '', '')
    )
    dest = tmp_path / 'vendor' / 'notes-mcp'
    pkg = {'manager': 'git', 'repo': 'msetsma/notes-mcp', 'path': str(dest)}

    plan = up.Plan()
    status = up.git_status('notes', pkg, {}, do_fetch=False, plan=plan)

    assert status.startswith('not cloned')
    assert plan.git_clones == [('notes', 'msetsma/notes-mcp', str(dest))]
    assert calls == []  # reporting never touches the network

    up.apply_updates(plan)
    assert dest.parent.is_dir()
    assert ['git', 'clone', '--quiet', 'git@github.com:msetsma/notes-mcp.git', str(dest)] in calls


def test_existing_path_is_not_cloned(tmp_path):
    up = load()
    plan = up.Plan()
    up.git_status('x', {'manager': 'git', 'repo': 'o/x', 'path': str(tmp_path)}, {}, do_fetch=False, plan=plan)
    assert plan.git_clones == []
