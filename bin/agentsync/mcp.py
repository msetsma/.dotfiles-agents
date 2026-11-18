"""MCP servers: per-client renderers and the JSON/JSONC and Codex TOML merges.

Codex keeps its per-tool tables (``[mcp_servers.databricks.tools.*]``); only the
managed keys change.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path
from types import ModuleType

from .common import (
    CONFIG,
    deep_get,
    die,
    opcodeify,
    read_json_config,
    refuse_commented,
    report,
    semantic_diff,
    write_json,
)


__all__ = ['MCP_SYNCERS', 'RENDERERS', 'mcp_client_kind', 'render', 'sync_codex_mcp', 'sync_json_mcp']


# --------------------------------------------------------------------------- #
# per-client mcp renderers
# --------------------------------------------------------------------------- #
def r_opencode(srv: dict, cli: dict) -> dict:
    if srv['kind'] == 'local':
        out = {'type': 'local', 'command': [srv['command'], *srv['args']]}
        if srv['env']:
            out['environment'] = {k: opcodeify(v) for k, v in srv['env'].items()}
        return out
    out = {'type': 'remote', 'url': srv['url']}
    headers = {k: opcodeify(v) for k, v in srv['headers'].items()}
    if headers:
        out['headers'] = headers
    return out


def r_claude_code(srv: dict, cli: dict) -> dict:
    if srv['kind'] == 'local':
        return {'type': 'stdio', 'command': srv['command'], 'args': srv['args'], 'env': dict(srv['env'])}
    out = {'type': 'http', 'url': srv['url']}
    if srv['headers']:
        out['headers'] = srv['headers']
    return out


def r_claude_desktop(srv: dict, cli: dict) -> dict:
    if srv['kind'] == 'local':
        out = {'command': srv['command'], 'args': srv['args']}
        env = dict(srv['env'])
        if cli.get('inject_path'):
            env.setdefault('PATH', cli['inject_path'])
        if env:
            out['env'] = env
        return out
    out = {'type': 'http', 'url': srv['url']}
    if srv['headers']:
        out['headers'] = srv['headers']
    return out


def r_codex(srv: dict, cli: dict) -> dict:
    if srv['kind'] == 'local':
        return {'command': srv['command'], 'args': srv['args'], 'env': dict(srv['env'])}
    return {'url': srv['url']}


RENDERERS = {'opencode': r_opencode, 'claude-code': r_claude_code, 'claude-desktop': r_claude_desktop, 'codex': r_codex}


def render(srv: dict, cli: dict, client: str) -> dict:
    out = RENDERERS[cli['style']](srv, cli)
    out.update(srv.get('extra', {}).get(client, {}))
    return out


# --------------------------------------------------------------------------- #
# mcp merge (JSON/JSONC maps, Codex TOML tables)
# --------------------------------------------------------------------------- #
def sync_json_mcp(client: str, cli: dict, desired: dict, retired: list) -> bool:
    target = Path(cli['path']).expanduser()
    doc, commented = read_json_config(target)
    container = deep_get(doc, cli['container'])
    added, changed, removed = semantic_diff(container, desired, retired)
    dirty = report(client, target, added, changed, removed)
    if not dirty or refuse_commented(target, commented, {n: desired[n] for n in added + changed}):
        return dirty
    if CONFIG.dry_run:
        return dirty
    for name in retired:
        container.pop(name, None)
    container.update(desired)
    write_json(target, doc)
    return dirty


CODEX_KEYS = ('command', 'args', 'env')
# Keys Codex writes into a server's table itself (per-tool approvals); sync
# never compares or removes them.
CODEX_SELF_KEYS = frozenset({'tools'})


def _plain(value: object) -> object:
    """A tomlkit item as plain Python, so it compares equal to rendered dicts."""
    return value.unwrap() if hasattr(value, 'unwrap') else value


def _codex_managed_keys(entry: MutableMapping, srv: dict) -> list[str]:
    """Keys of a catalog server's table that sync owns.

    The config records nothing about which keys sync wrote, so the catalog
    owns a catalog server's table entirely: every key except CODEX_SELF_KEYS.
    A key dropped from ``[extra.codex]`` then shows as drift and is removed.
    Non-catalog servers are never rewritten, so their hand-set keys survive.
    """
    present = [k for k in entry if k not in CODEX_SELF_KEYS]
    return list(dict.fromkeys([*srv, *present]))


def _read_codex_servers(table: MutableMapping, desired: dict) -> dict:
    """Each server's managed keys as plain values, comparable to ``desired``.

    A missing ``env`` reads as ``{}``, which is how an env-less server is
    written; any other missing key reads as None.
    """
    out = {}
    for name in table:
        entry = table[name]
        keys = _codex_managed_keys(entry, desired[name]) if name in desired else CODEX_KEYS
        out[name] = {k: _plain(entry.get(k, {} if k == 'env' else None)) for k in keys}
    return out


def _write_codex_entry(tomlkit: ModuleType, entry: MutableMapping, srv: dict) -> None:
    for key in [k for k in entry if k not in srv and k not in CODEX_SELF_KEYS]:
        del entry[key]
    for key, value in srv.items():
        if key == 'env' and not value:
            if 'env' in entry:
                del entry['env']
            continue
        if isinstance(value, dict):
            sub = tomlkit.table()
            for k, v in value.items():
                sub[k] = v
            entry[key] = sub
        else:
            entry[key] = value


def _write_codex_servers(tomlkit: ModuleType, table: MutableMapping, desired: dict, retired: list) -> None:
    for name in retired:
        if name in table:
            del table[name]
    for name, srv in desired.items():
        entry = table[name] if name in table else tomlkit.table()
        table[name] = entry
        _write_codex_entry(tomlkit, entry, srv)


def sync_codex_mcp(client: str, cli: dict, desired: dict, retired: list) -> bool:
    target = Path(cli['path']).expanduser()
    try:
        import tomlkit
    except ModuleNotFoundError:
        die('codex client needs tomlkit; run via bin/agent-sync (uv provides it)')
    doc = tomlkit.parse(target.read_text(encoding='utf-8')) if target.exists() else tomlkit.document()
    table = doc.get('mcp_servers')
    if table is None:
        table = tomlkit.table()
        doc['mcp_servers'] = table

    # Remote servers are not managed in Codex: drop them so dry-run converges.
    desired = {n: s for n, s in desired.items() if 'command' in s}
    existing = _read_codex_servers(table, desired)
    added, changed, removed = semantic_diff(existing, desired, retired)
    dirty = report(client, target, added, changed, removed)
    if CONFIG.dry_run or not dirty:
        return dirty

    _write_codex_servers(tomlkit, table, desired, retired)
    target.write_text(tomlkit.dumps(doc), encoding='utf-8')
    return dirty


MCP_SYNCERS = {'json': sync_json_mcp, 'codex': sync_codex_mcp}


# Config file format -> the syncer in MCP_SYNCERS that merges it.
MCP_CLIENT_KINDS = {'toml': 'codex', 'json': 'json', 'jsonc': 'json'}


def mcp_client_kind(cli: dict) -> str:
    kind = MCP_CLIENT_KINDS.get(cli['format'])
    if kind is None:
        die(f'unsupported format {cli["format"]!r}')
    return kind
