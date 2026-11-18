"""Offline tests for the config-writing paths of agent-sync (bin/agentsync/, bin/jsonc.py).

Every test writes into ``tmp_path``; no live client config is touched.

Run with: uv run --with pytest --with tomlkit pytest bin/tests/test_agent_sync_engine.py
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonc
import pytest
from agent_sync import warn_unsupported
from agentsync import common
from agentsync.catalog import load_retired
from agentsync.common import build_context, expand, load_toml, read_json_config
from agentsync.hooks import r_hook_claude_code, sync_hooks
from agentsync.mcp import sync_codex_mcp, sync_json_mcp


@pytest.fixture(autouse=True)
def _live_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common.CONFIG, 'dry_run', False)


# --------------------------------------------------------------------------- #
# expand
# --------------------------------------------------------------------------- #
def test_expand_tilde_uses_forward_slashes() -> None:
    out = expand('~/.dotfiles-agents/bin/clean-python', {})
    assert '\\' not in out
    assert out.endswith('/.dotfiles-agents/bin/clean-python')
    assert not out.startswith('~')


def test_expand_recurses_and_keeps_unknown_vars() -> None:
    ctx = {'AGENT_HOME': '/a'}
    assert expand({'k': ['${AGENT_HOME}/x', '${GITHUB_TOKEN}']}, ctx) == {'k': ['/a/x', '${GITHUB_TOKEN}']}
    assert expand(3, ctx) == 3


def test_build_context_home_and_agent_home_use_forward_slashes() -> None:
    ctx = build_context({})
    assert '\\' not in ctx['HOME']
    assert '\\' not in ctx['AGENT_HOME']


# --------------------------------------------------------------------------- #
# jsonc
# --------------------------------------------------------------------------- #
def test_loads_jsonc_accepts_comments_and_trailing_commas() -> None:
    text = '{\n  // c\n  "a": [1, 2,],\n  "u": "https://x,}",\n  /* b */ "b": {"c": 1,},\n}\n'
    assert jsonc.loads_jsonc(text) == {'a': [1, 2], 'u': 'https://x,}', 'b': {'c': 1}}


def test_has_comments() -> None:
    assert jsonc.has_comments('{"a": 1} // x')
    assert jsonc.has_comments('{/* x */}')
    assert not jsonc.has_comments('{"u": "https://x/*y*/"}')


def test_strip_trailing_commas_is_linear_on_large_input() -> None:
    import time

    items = ',\n'.join(f'{{"k{i}": [1, 2, 3], "s": "v,]"}}' for i in range(45000))
    text = f'[\n{items},\n]'
    assert len(text) > 1_500_000
    start = time.perf_counter()
    parsed = jsonc.loads_jsonc(text)
    assert time.perf_counter() - start < 2
    assert len(parsed) == 45000


# --------------------------------------------------------------------------- #
# MCP JSON merge and retire
# --------------------------------------------------------------------------- #
def mcp_cli(target: Path) -> dict:
    return {'path': str(target), 'format': 'json', 'container': ['mcpServers'], 'style': 'claude-code'}


def test_json_mcp_merge_and_retire(tmp_path: Path) -> None:
    target = tmp_path / 'claude.json'
    target.write_text(json.dumps({'theme': 'dark', 'mcpServers': {'mine': {'command': 'x'}, 'old': {'command': 'o'}}}))
    desired = {'new': {'type': 'stdio', 'command': 'n', 'args': [], 'env': {}}}
    assert sync_json_mcp('claude-code', mcp_cli(target), desired, ['old'])
    doc = json.loads(target.read_text())
    assert doc['theme'] == 'dark'
    assert doc['mcpServers'] == {'mine': {'command': 'x'}, 'new': desired['new']}
    assert not sync_json_mcp('claude-code', mcp_cli(target), desired, ['old'])


def test_json_mcp_bad_json_dies_with_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / 'broken.json'
    target.write_text('{"mcpServers": ')
    with pytest.raises(SystemExit):
        sync_json_mcp('claude-code', mcp_cli(target), {}, [])
    assert str(target) in capsys.readouterr().err


def test_jsonc_mcp_trailing_commas_parse_and_comments_are_not_destroyed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / 'opencode.jsonc'
    original = '{\n  // keep me\n  "mcp": {"servers": {"a": {"type": "local", "command": ["a"]},},},\n}\n'
    target.write_text(original)
    cli = {'path': str(target), 'format': 'jsonc', 'container': ['mcp', 'servers'], 'style': 'opencode'}
    desired = {'b': {'type': 'local', 'command': ['b']}}
    assert sync_json_mcp('opencode', cli, desired, [])
    assert target.read_text() == original
    err = capsys.readouterr().err
    assert 'comments' in err
    assert str(target) in err


def test_jsonc_dry_run_warns_that_a_real_sync_will_not_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / 'opencode.jsonc'
    original = '{\n  // keep me\n  "mcp": {"servers": {}}\n}\n'
    target.write_text(original)
    cli = {'path': str(target), 'format': 'jsonc', 'container': ['mcp', 'servers'], 'style': 'opencode'}
    common.CONFIG.dry_run = True
    assert sync_json_mcp('opencode', cli, {'b': {'type': 'local', 'command': ['b']}}, [])
    assert target.read_text() == original
    err = capsys.readouterr().err
    assert 'comments' in err
    assert str(target) in err


def test_unreadable_config_dies_with_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / 'settings.json'
    target.write_bytes(b'{"a": "\xff"}')
    with pytest.raises(SystemExit):
        read_json_config(target)
    assert str(target) in capsys.readouterr().err


def test_jsonc_mcp_without_comments_is_written(tmp_path: Path) -> None:
    target = tmp_path / 'opencode.jsonc'
    target.write_text('{"mcp": {"servers": {"a": {"type": "local", "command": ["a"]},}}}')
    cli = {'path': str(target), 'format': 'jsonc', 'container': ['mcp', 'servers'], 'style': 'opencode'}
    assert sync_json_mcp('opencode', cli, {'b': {'type': 'local', 'command': ['b']}}, [])
    assert set(json.loads(target.read_text())['mcp']['servers']) == {'a', 'b'}


# --------------------------------------------------------------------------- #
# hooks
# --------------------------------------------------------------------------- #
def hook(name: str, command: str, event: str = 'PostToolUse', timeout: int | None = 30, matcher: str = 'Write') -> dict:
    return {
        'name': name,
        'event': event,
        'matcher': matcher,
        'clients': ['claude-code'],
        'command': command,
        'timeout': timeout,
        'status_message': 'msg',
    }


HOOK_CLI = {'format': 'json', 'container': ['hooks'], 'style': 'claude-code'}
USER_GROUP = {'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'echo user'}]}


def hook_cli(target: Path) -> dict:
    return HOOK_CLI | {'path': str(target)}


def groups(target: Path, event: str) -> list:
    return json.loads(target.read_text())['hooks'][event]


def test_hooks_preserve_user_hooks_and_are_idempotent(tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(json.dumps({'model': 'x', 'hooks': {'PostToolUse': [USER_GROUP]}}))
    catalog = {'h': hook('h', '/r/bin/clean')}
    assert sync_hooks('claude-code', hook_cli(target), catalog, [])
    assert groups(target, 'PostToolUse')[0] == USER_GROUP
    assert len(groups(target, 'PostToolUse')) == 2
    assert json.loads(target.read_text())['model'] == 'x'
    assert not sync_hooks('claude-code', hook_cli(target), catalog, [])


def test_hooks_edited_fields_replace_instead_of_duplicating(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(json.dumps({'hooks': {'PostToolUse': [USER_GROUP]}}))
    sync_hooks('claude-code', hook_cli(target), {'h': hook('h', '/r/bin/clean')}, [])
    edited = {'h': hook('h', '/r/bin/clean', timeout=60, matcher='Edit')}
    capsys.readouterr()
    assert sync_hooks('claude-code', hook_cli(target), edited, [])
    assert '~ h @ PostToolUse' in capsys.readouterr().out
    managed = [g for g in groups(target, 'PostToolUse') if g != USER_GROUP]
    assert len(managed) == 1
    assert managed[0]['matcher'] == 'Edit'
    assert managed[0]['hooks'][0]['timeout'] == 60
    assert not sync_hooks('claude-code', hook_cli(target), edited, [])


def test_hooks_collapse_existing_duplicates(tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    stale = r_hook_claude_code(hook('h', '/r/bin/clean', timeout=10))
    target.write_text(json.dumps({'hooks': {'PostToolUse': [stale, USER_GROUP, stale]}}))
    catalog = {'h': hook('h', '/r/bin/clean')}
    assert sync_hooks('claude-code', hook_cli(target), catalog, [])
    assert groups(target, 'PostToolUse') == [USER_GROUP, r_hook_claude_code(catalog['h'])]


def test_hooks_moved_event_drops_old_group(tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    sync_hooks('claude-code', hook_cli(target), {'h': hook('h', '/r/bin/clean')}, [])
    assert sync_hooks('claude-code', hook_cli(target), {'h': hook('h', '/r/bin/clean', event='Stop')}, [])
    doc = json.loads(target.read_text())['hooks']
    assert 'PostToolUse' not in doc
    assert len(doc['Stop']) == 1


def test_hooks_user_group_sharing_a_catalog_command_is_kept(tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    mixed = {
        'matcher': '',
        'hooks': [{'type': 'command', 'command': '/r/bin/clean'}, {'type': 'command', 'command': 'echo me'}],
    }
    target.write_text(json.dumps({'hooks': {'PostToolUse': [mixed]}}))
    sync_hooks('claude-code', hook_cli(target), {'h': hook('h', '/r/bin/clean')}, [])
    assert groups(target, 'PostToolUse')[0] == mixed


def test_hooks_retired_command_is_scrubbed(tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    old = {'matcher': '', 'hooks': [{'type': 'command', 'command': '/old/clean'}]}
    target.write_text(json.dumps({'hooks': {'PostToolUse': [old, USER_GROUP]}}))
    assert sync_hooks('claude-code', hook_cli(target), {}, ['/old/clean'])
    assert groups(target, 'PostToolUse') == [USER_GROUP]


def test_hooks_user_group_matching_a_hook_for_another_client_is_kept(tmp_path: Path) -> None:
    target = tmp_path / 'settings.json'
    mine = {'matcher': 'Edit', 'hooks': [{'type': 'command', 'command': '/r/bin/clean'}]}
    target.write_text(json.dumps({'hooks': {'PostToolUse': [mine]}}))
    catalog = {'h': hook('h', '/r/bin/clean') | {'clients': ['codex']}}
    assert not sync_hooks('claude-code', hook_cli(target), catalog, [])
    assert groups(target, 'PostToolUse') == [mine]


def test_hooks_delisted_client_drops_the_group_sync_wrote(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / 'settings.json'
    target.write_text(json.dumps({'hooks': {'PostToolUse': [USER_GROUP]}}))
    sync_hooks('claude-code', hook_cli(target), {'h': hook('h', '/r/bin/clean')}, [])
    delisted = {'h': hook('h', '/r/bin/clean') | {'clients': ['codex']}}
    capsys.readouterr()
    assert sync_hooks('claude-code', hook_cli(target), delisted, [])
    assert '- h @ PostToolUse' in capsys.readouterr().out
    assert groups(target, 'PostToolUse') == [USER_GROUP]
    assert not sync_hooks('claude-code', hook_cli(target), delisted, [])


def test_hooks_same_event_and_command_in_two_hooks_dies(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / 'settings.json'
    catalog = {'a': hook('a', '/r/bin/clean', matcher='Write'), 'b': hook('b', '/r/bin/clean', matcher='Bash')}
    with pytest.raises(SystemExit):
        sync_hooks('claude-code', hook_cli(target), catalog, [])
    err = capsys.readouterr().err
    assert "'a'" in err
    assert "'b'" in err
    assert not target.exists()


# --------------------------------------------------------------------------- #
# client kind coverage (M3)
# --------------------------------------------------------------------------- #
def test_clients_toml_wires_instructions_for_claude_code_and_codex() -> None:
    clients = load_toml(common.CATALOG / 'clients.toml')
    assert clients['claude-code']['instructions']['path'] == '~/.claude/CLAUDE.md'
    assert clients['codex']['instructions']['path'] == '~/.codex/AGENTS.md'


def test_warns_when_item_lists_client_without_kind(capsys: pytest.CaptureFixture[str]) -> None:
    clients = {'opencode': {'mcp': {}}, 'codex': {'mcp': {}, 'skills': {}}}
    catalogs = {'skills': {'s': {'name': 's', 'clients': ['opencode', 'codex']}}, 'mcp': {}}
    warn_unsupported(clients, catalogs, ['opencode', 'codex'])
    err = capsys.readouterr().err
    assert "skills 's' lists opencode" in err
    assert 'codex' not in err


def test_no_warning_for_unselected_clients(capsys: pytest.CaptureFixture[str]) -> None:
    clients = {'opencode': {'mcp': {}}, 'codex': {'mcp': {}}}
    catalogs = {'skills': {'s': {'name': 's', 'clients': ['opencode']}}}
    warn_unsupported(clients, catalogs, ['codex'])
    assert capsys.readouterr().err == ''


# --------------------------------------------------------------------------- #
# codex mcp (L2)
# --------------------------------------------------------------------------- #
def test_codex_converges_with_remote_and_extra_keys(tmp_path: Path) -> None:
    pytest.importorskip('tomlkit')
    target = tmp_path / 'config.toml'
    target.write_text('[mcp_servers.keep]\ncommand = "k"\n\n[mcp_servers.keep.tools.x]\napproval = "auto"\n')
    cli = {'path': str(target), 'format': 'toml', 'container': ['mcp_servers'], 'style': 'codex'}
    desired = {
        'local': {'command': 'c', 'args': ['a'], 'env': {}, 'startup_timeout_sec': 30},
        'remote': {'url': 'https://example.invalid/mcp'},
    }
    assert sync_codex_mcp('codex', cli, desired, [])
    text = target.read_text()
    assert 'startup_timeout_sec = 30' in text
    assert 'approval = "auto"' in text
    assert not sync_codex_mcp('codex', cli, desired, [])


def test_codex_removed_extra_key_is_drift_and_removed(tmp_path: Path) -> None:
    pytest.importorskip('tomlkit')
    target = tmp_path / 'config.toml'
    target.write_text(
        '[mcp_servers.hand]\ncommand = "h"\nstartup_timeout_sec = 5\n\n'
        '[mcp_servers.local]\ncommand = "c"\nargs = ["a"]\nstartup_timeout_sec = 30\n\n'
        '[mcp_servers.local.tools.x]\napproval = "auto"\n'
    )
    cli = {'path': str(target), 'format': 'toml', 'container': ['mcp_servers'], 'style': 'codex'}
    desired = {'local': {'command': 'c', 'args': ['a'], 'env': {}}}
    assert sync_codex_mcp('codex', cli, desired, [])
    text = target.read_text()
    assert 'startup_timeout_sec = 30' not in text
    assert 'startup_timeout_sec = 5' in text
    assert 'approval = "auto"' in text
    assert not sync_codex_mcp('codex', cli, desired, [])


def test_load_retired_expands_vars_and_defaults_missing_kinds(tmp_path: Path) -> None:
    path = tmp_path / 'retired.toml'
    path.write_text(
        'packages = ["${AGENT_HOME}/packages/pi-quiet", "npm:x"]\nhook_commands = ["${AGENT_HOME}/bin/c ${OTHER}"]\n'
    )
    retired = load_retired(path, {'AGENT_HOME': '/a'})
    assert retired['packages'] == ['/a/packages/pi-quiet', 'npm:x']
    assert retired['hook_commands'] == ['/a/bin/c ${OTHER}']
    assert retired['mcp'] == []
    assert retired['instructions'] == []
