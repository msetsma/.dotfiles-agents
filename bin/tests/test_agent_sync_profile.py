"""Offline tests for the per-machine profile layer (bin/agent_profile.py) and
its wiring into bin/agent_sync.py.

The first test pins the invariant that matters most: with no local.toml
profile, every catalog item and every client is selected, so a machine that
never opts in syncs exactly what it did before.

Run with: uv run --with pytest --with tomlkit pytest bin/tests/test_agent_sync_profile.py
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_profile import deep_merge, is_selected, select_clients, split_command
from agentsync import common
from agentsync.catalog import LOADERS, load_catalogs, load_mcp
from agentsync.common import build_context, load_local
from agentsync.skills import sync_skills


@pytest.fixture(autouse=True)
def _live_run(monkeypatch) -> None:
    monkeypatch.setattr(common.CONFIG, 'dry_run', False)


CLIENTS = {
    'opencode': {'mcp': {'path': '~/.config/opencode/opencode.jsonc'}},
    'claude-code': {'mcp': {'path': '~/.claude.json'}, 'skills': {'path': '~/.claude/skills'}},
    'claude-desktop': {'mcp': {'path': '/mac/Claude/claude_desktop_config.json', 'inject_path': '/opt/homebrew/bin'}},
}


# --------------------------------------------------------------------------- #
# no profile == today's behaviour
# --------------------------------------------------------------------------- #
def test_no_profile_selects_whole_catalog() -> None:
    ctx = build_context(load_local())
    filtered = load_catalogs(ctx, {}, {})
    unfiltered = {kind: loader(ctx, {}) for kind, loader in LOADERS.items()}
    assert filtered == unfiltered
    assert 'entra-mcp' in filtered['mcp']


def test_no_profile_selects_every_client() -> None:
    assert select_clients(CLIENTS, {}) == list(CLIENTS)


def test_empty_override_keeps_clients_identical() -> None:
    assert deep_merge(CLIENTS, {}) == CLIENTS


# --------------------------------------------------------------------------- #
# selection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ('item', 'expected'),
    [
        ({'name': 'entra-mcp', 'tags': ['work']}, False),
        ({'name': 'sharepoint-mcp', 'tags': ['work', 'macos']}, False),
        ({'name': 'obscura', 'tags': []}, False),
        ({'name': 'github', 'tags': []}, True),
        ({'name': 'notes'}, True),
    ],
)
def test_is_selected(item: dict, expected: bool) -> None:
    rules = {'exclude_tags': ['work', 'macos'], 'exclude': ['obscura']}
    assert is_selected(item, rules) is expected


def test_profile_filters_real_catalog() -> None:
    rules = {'exclude_tags': ['work', 'macos'], 'exclude': ['python-clean', 'python-clean-stop']}
    catalogs = load_catalogs(build_context(load_local()), {}, rules)
    assert not {'entra-mcp', 'teams-browser', 'sharepoint-mcp', 'apple-mail', 'iMCP'} & set(catalogs['mcp'])
    assert {'github', 'notes', 'playwright'} <= set(catalogs['mcp'])
    assert not {'python-clean', 'python-clean-stop'} & set(catalogs['hooks'])


def test_explicit_client_list_keeps_catalog_order() -> None:
    rules = {'clients': ['claude-desktop', 'claude-code']}
    assert select_clients(CLIENTS, rules) == ['claude-code', 'claude-desktop']


def test_auto_detects_clients_by_config_dir() -> None:
    present = {Path('~/.claude.json').expanduser().parent, Path('/mac/Claude')}
    detected = select_clients(CLIENTS, {'clients': 'auto'}, exists=lambda p: p in present)
    # ~/.claude.json's parent is $HOME, which opencode's ~/.config/opencode is not.
    assert detected == ['claude-code', 'claude-desktop']


# --------------------------------------------------------------------------- #
# overrides
# --------------------------------------------------------------------------- #
def test_client_override_deep_merges() -> None:
    desktop = {'path': '~/AppData/Roaming/Claude/claude_desktop_config.json', 'inject_path': ''}
    local = {'claude-desktop': {'mcp': desktop}}
    merged = deep_merge(CLIENTS, local)
    assert merged['claude-desktop']['mcp'] == desktop
    assert merged['claude-code'] == CLIENTS['claude-code']
    assert CLIENTS['claude-desktop']['mcp']['inject_path'] == '/opt/homebrew/bin'  # input untouched


def test_mcp_override_replaces_clients_and_merges_env() -> None:
    local = {'mcp': {'playwright': {'clients': ['claude-code']}, 'notes': {'env': {'VAULT_PATH': 'C:/vault'}}}}
    base = load_mcp(build_context(load_local()), {})
    mcp = load_mcp(build_context(local), local)
    assert mcp['playwright']['clients'] == ['claude-code']
    # The override replaces the keys it sets and inherits the catalog's other
    # env vars. Derive the expectation from the catalog so the test tracks
    # changes instead of pinning today's full env set.
    assert mcp['notes']['env'] == {**base['notes']['env'], 'VAULT_PATH': 'C:/vault'}
    assert base['notes']['env']['VAULT_PATH'] != 'C:/vault'


@pytest.mark.parametrize(
    ('command', 'expected'), [('npx', ('npx', [])), (['cmd', '/c', 'npx'], ('cmd', ['/c', 'npx']))]
)
def test_split_command(command, expected) -> None:
    assert split_command(command) == expected


def test_list_command_prefixes_args() -> None:
    local = {'commands': {'npx': ['cmd', '/c', 'npx']}}
    srv = load_mcp(build_context(local), local)['playwright']
    assert srv['command'] == 'cmd'
    assert srv['args'][:2] == ['/c', 'npx']
    assert srv['args'][2:] == ['-y', '@playwright/mcp@latest', '--headless']


# --------------------------------------------------------------------------- #
# windows hygiene
# --------------------------------------------------------------------------- #
def test_skill_link_is_a_directory(tmp_path: Path) -> None:
    source = tmp_path / 'src' / 'demo'
    source.mkdir(parents=True)
    (source / 'SKILL.md').write_text('# demo\n', encoding='utf-8')
    skills = {'demo': {'name': 'demo', 'clients': ['claude-code'], 'dir': source}}
    try:
        sync_skills('claude-code', {'path': str(tmp_path / 'skills')}, skills, [])
    except OSError as exc:  # symlinks unavailable (Windows without Developer Mode)
        pytest.skip(f'symlinks unsupported: {exc}')
    assert (tmp_path / 'skills' / 'demo' / 'SKILL.md').read_text(encoding='utf-8') == '# demo\n'
