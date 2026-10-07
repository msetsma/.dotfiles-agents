"""Offline tests for the per-machine profile layer (bin/agent_profile.py) and
its wiring into bin/agent_sync.py.

The first test pins the invariant that matters most: with no local.toml
profile, every catalog item and every client is selected, so a machine that
never opts in syncs exactly what it did before.

Run with: uv run --with pytest --with tomlkit pytest bin/tests/test_agent_sync_profile.py
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


BIN = Path(__file__).resolve().parents[1]


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, BIN / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def engine():
    module = load_module('agent_sync')
    module.CONFIG.dry_run = False
    return module


@pytest.fixture
def profile():
    return load_module('agent_profile')


CLIENTS = {
    'opencode': {'mcp': {'path': '~/.config/opencode/opencode.jsonc'}},
    'claude-code': {'mcp': {'path': '~/.claude.json'}, 'skills': {'path': '~/.claude/skills'}},
    'claude-desktop': {'mcp': {'path': '/mac/Claude/claude_desktop_config.json', 'inject_path': '/opt/homebrew/bin'}},
}


# --------------------------------------------------------------------------- #
# no profile == today's behaviour
# --------------------------------------------------------------------------- #
def test_no_profile_selects_whole_catalog(engine) -> None:
    ctx = engine.build_context()
    filtered = engine.load_catalogs(ctx, {}, {})
    unfiltered = {kind: loader(ctx) for kind, loader in engine.LOADERS.items()}
    assert filtered == unfiltered
    assert 'entra-mcp' in filtered['mcp']


def test_no_profile_selects_every_client(profile) -> None:
    assert profile.select_clients(CLIENTS, {}) == list(CLIENTS)


def test_empty_override_keeps_clients_identical(profile) -> None:
    assert profile.deep_merge(CLIENTS, {}) == CLIENTS


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
def test_is_selected(profile, item: dict, expected: bool) -> None:
    rules = {'exclude_tags': ['work', 'macos'], 'exclude': ['obscura']}
    assert profile.is_selected(item, rules) is expected


def test_profile_filters_real_catalog(engine) -> None:
    rules = {'exclude_tags': ['work', 'macos'], 'exclude': ['python-clean', 'python-clean-stop']}
    catalogs = engine.load_catalogs(engine.build_context(), {}, rules)
    assert not {'entra-mcp', 'teams-browser', 'sharepoint-mcp', 'apple-mail', 'iMCP'} & set(catalogs['mcp'])
    assert {'github', 'notes', 'playwright'} <= set(catalogs['mcp'])
    assert not {'python-clean', 'python-clean-stop'} & set(catalogs['hooks'])


def test_explicit_client_list_keeps_catalog_order(profile) -> None:
    rules = {'clients': ['claude-desktop', 'claude-code']}
    assert profile.select_clients(CLIENTS, rules) == ['claude-code', 'claude-desktop']


def test_auto_detects_clients_by_config_dir(profile) -> None:
    present = {Path('~/.claude.json').expanduser().parent, Path('/mac/Claude')}
    detected = profile.select_clients(CLIENTS, {'clients': 'auto'}, exists=lambda p: p in present)
    # ~/.claude.json's parent is $HOME, which opencode's ~/.config/opencode is not.
    assert detected == ['claude-code', 'claude-desktop']


# --------------------------------------------------------------------------- #
# overrides
# --------------------------------------------------------------------------- #
def test_client_override_deep_merges(profile) -> None:
    desktop = {'path': '~/AppData/Roaming/Claude/claude_desktop_config.json', 'inject_path': ''}
    local = {'claude-desktop': {'mcp': desktop}}
    merged = profile.deep_merge(CLIENTS, local)
    assert merged['claude-desktop']['mcp'] == desktop
    assert merged['claude-code'] == CLIENTS['claude-code']
    assert CLIENTS['claude-desktop']['mcp']['inject_path'] == '/opt/homebrew/bin'  # input untouched


def test_mcp_override_replaces_clients_and_merges_env(engine) -> None:
    local = {'mcp': {'playwright': {'clients': ['claude-code']}, 'notes': {'env': {'VAULT_PATH': 'C:/vault'}}}}
    mcp = engine.load_mcp(engine.build_context(local), local)
    assert mcp['playwright']['clients'] == ['claude-code']
    assert mcp['notes']['env'] == {'VAULT_PATH': 'C:/vault', 'QMD_COLLECTION': 'notes'}


@pytest.mark.parametrize(
    ('command', 'expected'), [('npx', ('npx', [])), (['cmd', '/c', 'npx'], ('cmd', ['/c', 'npx']))]
)
def test_split_command(profile, command, expected) -> None:
    assert profile.split_command(command) == expected


def test_list_command_prefixes_args(engine) -> None:
    local = {'commands': {'npx': ['cmd', '/c', 'npx']}}
    srv = engine.load_mcp(engine.build_context(local), local)['playwright']
    assert srv['command'] == 'cmd'
    assert srv['args'][:2] == ['/c', 'npx']
    assert srv['args'][2:] == ['-y', '@playwright/mcp@latest']


# --------------------------------------------------------------------------- #
# windows hygiene
# --------------------------------------------------------------------------- #
def test_skill_link_is_a_directory(engine, tmp_path: Path) -> None:
    source = tmp_path / 'src' / 'demo'
    source.mkdir(parents=True)
    (source / 'SKILL.md').write_text('# demo\n', encoding='utf-8')
    skills = {'demo': {'name': 'demo', 'clients': ['claude-code'], 'dir': source}}
    try:
        engine.sync_skills('claude-code', {'path': str(tmp_path / 'skills')}, skills, [])
    except OSError as exc:  # symlinks unavailable (Windows without Developer Mode)
        pytest.skip(f'symlinks unsupported: {exc}')
    assert (tmp_path / 'skills' / 'demo' / 'SKILL.md').read_text(encoding='utf-8') == '# demo\n'
