#!/usr/bin/env python3
"""agent-sync - materialise the agent catalog into every client's config.

Single source of truth
----------------------
  catalog/servers/*.toml   one file per MCP server (custom + third-party)
  catalog/skills/*.toml    one file per skill (content in catalog/skills/<name>/)
  catalog/hooks/*.toml     one file per event hook
  catalog/clients.toml     where each agent keeps each kind, and how it spells it
  catalog/paths.toml       machine-specific command paths + variables
  catalog/retired.toml     resources to scrub from every client

Behaviour
---------
Each client declares one block per *kind* it supports. Three strategies:

* ``merge``     rewrite a subtree of a shared config in place, leaving every
                other key alone (MCP maps; hook event maps).
* ``symlink``   link each catalog item into a client directory (skills).
* ``whole_file`` pure config rendered to ``generated/<client>.<ext>`` for dotter
                to symlink, so the repo owns the target outright (VS Code).

Codex keeps its per-tool tables (``[mcp_servers.databricks.tools.*]``); only the
managed keys change.

Usage
-----
  agent-sync                apply changes
  agent-sync --dry-run      semantic diff, write nothing
  agent-sync --client codex limit to one client
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py<3.11
    import tomli as tomllib  # type: ignore

REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO / 'catalog'
GENERATED = REPO / 'generated'

ENV_REF = re.compile(r'\$\{([A-Za-z0-9_]+)\}')
OPCODE_ENV_REF = re.compile(r'\{env:([A-Za-z0-9_]+)\}')


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def die(msg: str) -> None:
    print(f'agent-sync: error: {msg}', file=sys.stderr)
    raise SystemExit(1)


def load_toml(path: Path) -> dict:
    with open(path, 'rb') as fh:
        return tomllib.load(fh)


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments while respecting string literals.

    A naive regex would eat the `//` in `https://...`, so scan character by
    character and only strip comments outside of quoted strings.
    """
    out: list[str] = []
    i, n = 0, len(text)
    in_str = False
    esc = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == '/' and i + 1 < n and text[i + 1] == '/':
            while i < n and text[i] not in '\r\n':
                i += 1
            continue
        if ch == '/' and i + 1 < n and text[i + 1] == '*':
            i += 2
            while i + 1 < n and not (text[i] == '*' and text[i + 1] == '/'):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def expand(value, ctx: dict):
    """Expand ``~`` and ``${VAR}`` recursively through strings/lists/dicts."""
    if isinstance(value, str):
        value = os.path.expanduser(value)

        def repl(match: re.Match) -> str:
            name = match.group(1)
            # Known catalog vars (HOME, AGENT_HOME, ...) are expanded here.
            # Anything else is left intact as an environment reference for the
            # agent to resolve at launch (e.g. ${GITHUB_TOKEN}).
            return ctx.get(name, match.group(0))

        return ENV_REF.sub(repl, value)
    if isinstance(value, list):
        return [expand(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, ctx) for k, v in value.items()}
    return value


def opcodeify(value: str) -> str:
    """Opencode spells env references ``{env:VAR}`` instead of ``${VAR}``."""
    return ENV_REF.sub(lambda m: '{env:%s}' % m.group(1), value)


# --------------------------------------------------------------------------- #
# catalog loading
# --------------------------------------------------------------------------- #
def build_context() -> dict:
    paths = load_toml(CATALOG / 'paths.toml')
    ctx = {'HOME': os.path.expanduser('~')}
    ctx.update(expand(paths.get('vars', {}), ctx))
    ctx['commands'] = expand(paths.get('commands', {}), ctx)
    # Machine-local overrides (untracked). Keeps org-specific values out of the
    # published repo while still resolving locally.
    local = CATALOG / 'local.toml'
    if local.exists():
        data = load_toml(local)
        ctx.update(expand(data.get('vars', {}), ctx))
        ctx['commands'].update(expand(data.get('commands', {}), ctx))
    return ctx


def load_servers(ctx: dict) -> dict:
    servers: dict[str, dict] = {}
    for path in sorted((CATALOG / 'servers').glob('*.toml')):
        raw = load_toml(path)
        name = raw['name']
        kind = raw.get('kind', 'local')
        srv = {
            'name': name,
            'kind': kind,
            'origin': raw.get('origin', 'external'),
            'description': raw.get('description', ''),
            'clients': list(raw.get('clients', [])),
            'extra': raw.get('extra', {}),
            'package': raw.get('package', {}),
        }
        if kind == 'local':
            launch = raw['launch']
            cmd = launch['command']
            srv['command'] = ctx['commands'].get(cmd, cmd)
            srv['args'] = [expand(a, ctx) for a in launch.get('args', [])]
            srv['env'] = {k: expand(v, ctx) for k, v in raw.get('env', {}).items()}
            pkg = raw.get('package', {})
            if pkg.get('manager') == 'npm' and pkg.get('name'):
                ref = pkg.get('ref') or 'latest'
                token = f'{pkg["name"]}@{ref}'
                srv['args'] = [a.replace('{{package}}', token) for a in srv['args']]
        elif kind == 'remote':
            remote = raw['remote']
            srv['url'] = expand(remote['url'], ctx)
            srv['headers'] = {k: expand(v, ctx) for k, v in remote.get('headers', {}).items()}
        else:
            die(f'{path.name}: unknown kind {kind!r}')
        servers[name] = srv
    return servers


def load_skills(ctx: dict) -> dict:
    """Each ``catalog/skills/<name>.toml`` points at ``catalog/skills/<name>/``."""
    skills: dict[str, dict] = {}
    for path in sorted((CATALOG / 'skills').glob('*.toml')):
        raw = load_toml(path)
        name = raw['name']
        source = CATALOG / 'skills' / name
        if not (source / 'SKILL.md').is_file():
            die(f'{path.name}: missing {source.relative_to(REPO)}/SKILL.md')
        skills[name] = {
            'name': name,
            'description': raw.get('description', ''),
            'clients': list(raw.get('clients', [])),
            'dir': source,
        }
    return skills


def load_hooks(ctx: dict) -> dict:
    hooks: dict[str, dict] = {}
    for path in sorted((CATALOG / 'hooks').glob('*.toml')):
        raw = load_toml(path)
        name = raw['name']
        command = raw['command']
        hooks[name] = {
            'name': name,
            'description': raw.get('description', ''),
            'event': raw['event'],
            'matcher': raw.get('matcher', ''),
            'clients': list(raw.get('clients', [])),
            'command': expand(command['run'], ctx),
            'timeout': command.get('timeout'),
            'status_message': command.get('statusMessage'),
        }
    return hooks


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


def r_vscode(srv: dict, cli: dict) -> dict:
    if srv['kind'] == 'local':
        out = {'type': 'stdio', 'command': srv['command'], 'args': srv['args']}
        if srv['env']:
            out['env'] = srv['env']
        return out
    out = {'type': 'http', 'url': srv['url']}
    if srv['headers']:
        out['headers'] = srv['headers']
    return out


def r_codex(srv: dict, cli: dict) -> dict:
    if srv['kind'] == 'local':
        return {'command': srv['command'], 'args': srv['args'], 'env': dict(srv['env'])}
    return {'url': srv['url']}


RENDERERS = {
    'opencode': r_opencode,
    'claude-code': r_claude_code,
    'claude-desktop': r_claude_desktop,
    'vscode': r_vscode,
    'codex': r_codex,
}


def render(srv: dict, cli: dict, client: str) -> dict:
    out = RENDERERS[cli['style']](srv, cli)
    out.update(srv.get('extra', {}).get(client, {}))
    return out


# --------------------------------------------------------------------------- #
# per-client hook renderers
# --------------------------------------------------------------------------- #
def r_hook_claude_code(hook: dict) -> dict:
    cmd = {'type': 'command', 'command': hook['command']}
    if hook['timeout'] is not None:
        cmd['timeout'] = hook['timeout']
    if hook['status_message']:
        cmd['statusMessage'] = hook['status_message']
    return {'matcher': hook['matcher'], 'hooks': [cmd]}


def r_hook_codex(hook: dict) -> dict:
    cmd = {'type': 'command', 'command': hook['command']}
    if hook['timeout'] is not None:
        cmd['timeout'] = hook['timeout']
    return {'matcher': hook['matcher'], 'hooks': [cmd]}


HOOK_RENDERERS = {'claude-code': r_hook_claude_code, 'codex': r_hook_codex}


# --------------------------------------------------------------------------- #
# diff + apply
# --------------------------------------------------------------------------- #
def semantic_diff(existing: dict, desired: dict, retired: list[str]):
    added = [n for n in desired if n not in existing]
    removed = [n for n in existing if n in retired]
    changed = [n for n in desired if n in existing and existing[n] != desired[n]]
    return sorted(added), sorted(changed), sorted(removed)


def report(client: str, path: Path, added, changed, removed, kind: str = 'mcp') -> bool:
    dirty = bool(added or changed or removed)
    label = 'apply' if not DRY_RUN else 'dry-run'
    print(f'[{client}] {kind}: {path}  ({label})')
    if not dirty:
        print('  no changes')
        return False
    for n in added:
        print(f'  + {n}')
    for n in changed:
        print(f'  ~ {n}')
    for n in removed:
        print(f'  - {n}')
    return True


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + '\n')


def deep_get(doc: dict, keys: list[str]) -> dict:
    node = doc
    for key in keys:
        node = node.setdefault(key, {})
    return node


def sync_json_mcp(client: str, cli: dict, desired: dict, retired: list) -> bool:
    target = Path(os.path.expanduser(cli['path']))
    if target.exists():
        doc = json.loads(strip_jsonc(target.read_text()))
    else:
        doc = {}
    container = deep_get(doc, cli['container'])
    added, changed, removed = semantic_diff(container, desired, retired)
    dirty = report(client, target, added, changed, removed)
    if DRY_RUN or not dirty:
        return dirty
    for name in retired:
        container.pop(name, None)
    container.update(desired)
    write_json(target, doc)
    return dirty


def sync_whole_file_mcp(client: str, cli: dict, desired: dict, retired: list) -> bool:
    """Pure-MCP client: render to generated/ for dotter to symlink."""
    target = Path(os.path.expanduser(cli['path']))
    if target.exists():
        doc = json.loads(strip_jsonc(target.read_text()))
    else:
        doc = {}
    container = deep_get(doc, cli['container'])
    added, changed, removed = semantic_diff(container, desired, retired)
    dirty = report(client, target, added, changed, removed)
    print(f'    -> generated/{client}.json (dotter symlinks this to the target)')
    if DRY_RUN:
        return dirty
    for name in retired:
        container.pop(name, None)
    container.update(desired)
    write_json(GENERATED / f'{client}.json', doc)
    return dirty


def sync_codex_mcp(client: str, cli: dict, desired: dict, retired: list) -> bool:
    target = Path(os.path.expanduser(cli['path']))
    try:
        import tomlkit
    except ModuleNotFoundError:
        die('codex client needs tomlkit; run via bin/agent-sync (uv provides it)')
    doc = tomlkit.parse(target.read_text()) if target.exists() else tomlkit.document()
    table = doc.get('mcp_servers')
    if table is None:
        table = tomlkit.table()
        doc['mcp_servers'] = table

    existing = {}
    for name in table:
        entry = table[name]
        existing[name] = {
            'command': entry.get('command'),
            'args': list(entry.get('args', [])),
            'env': dict(entry.get('env', {})),
        }
    added, changed, removed = semantic_diff(existing, desired, retired)
    dirty = report(client, target, added, changed, removed)
    if DRY_RUN or not dirty:
        return dirty

    for name in retired:
        if name in table:
            del table[name]
    for name, srv in desired.items():
        if 'command' not in srv:
            continue  # remote servers are not managed in Codex here
        if name in table:
            entry = table[name]
        else:
            entry = tomlkit.table()
            table[name] = entry
        entry['command'] = srv['command']
        entry['args'] = srv['args']
        if srv['env']:
            env = tomlkit.table()
            for k, v in srv['env'].items():
                env[k] = v
            entry['env'] = env
        elif 'env' in entry:
            del entry['env']
    target.write_text(tomlkit.dumps(doc))
    return dirty


MCP_SYNCERS = {'json': sync_json_mcp, 'whole_file': sync_whole_file_mcp, 'codex': sync_codex_mcp}


def mcp_client_kind(cli: dict) -> str:
    if cli.get('whole_file'):
        return 'whole_file'
    if cli['format'] == 'toml':
        return 'codex'
    if cli['format'] in ('json', 'jsonc'):
        return 'json'
    die(f'unsupported format {cli["format"]!r}')


# --------------------------------------------------------------------------- #
# skills (symlink strategy)
# --------------------------------------------------------------------------- #
def sync_skills(client: str, cli: dict, skills: dict, retired: list) -> bool:
    base = Path(os.path.expanduser(cli['path']))
    manage_root = (CATALOG / 'skills').resolve()
    desired = {n: s for n, s in skills.items() if client in s['clients'] and n not in retired}

    # Only touch symlinks that point back into our catalog/skills/<name>/.
    existing: dict[str, str] = {}
    if base.is_dir():
        for entry in base.iterdir():
            if entry.is_symlink():
                resolved = os.path.realpath(entry)
                if resolved == str(manage_root) or resolved.startswith(str(manage_root) + os.sep):
                    existing[entry.name] = resolved

    added = sorted(n for n in desired if n not in existing)
    changed = sorted(n for n in desired if n in existing and existing[n] != str(desired[n]['dir'].resolve()))
    removed = sorted(n for n in existing if n not in desired)
    dirty = report(client, base, added, changed, removed, kind='skills')
    if DRY_RUN or not dirty:
        return dirty

    base.mkdir(parents=True, exist_ok=True)
    for name in removed + changed:
        (base / name).unlink()
    for name in added + changed:
        (base / name).symlink_to(desired[name]['dir'])
    return dirty


# --------------------------------------------------------------------------- #
# hooks (additive merge into a shared event map)
# --------------------------------------------------------------------------- #
def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True)


def sync_hooks(client: str, cli: dict, hooks: dict, retired_commands: list) -> bool:
    if cli['style'] not in HOOK_RENDERERS:
        print(f'[{client}] hooks: style {cli["style"]!r} not supported yet; skipping')
        return False
    renderer = HOOK_RENDERERS[cli['style']]

    target = Path(os.path.expanduser(cli['path']))
    desired: dict[str, list] = {}
    label: dict[str, str] = {}
    managed_keys: set[str] = set()
    for hook in hooks.values():
        if client not in hook['clients']:
            continue
        group = renderer(hook)
        key = _canon(group)
        desired.setdefault(hook['event'], []).append(group)
        managed_keys.add(key)
        label[key] = f'{hook["name"]} @ {hook["event"]}'

    if target.exists():
        doc = json.loads(strip_jsonc(target.read_text()))
    else:
        doc = {}
    container = deep_get(doc, cli['container'])

    # Existing events, minus any group the catalog owns (so re-sync and
    # retirement are idempotent) and minus explicitly retired commands.
    scrubbed: dict[str, list] = {}
    removed_labels: list[str] = []
    scrubbed_retired: set = set()
    for event, groups in container.items():
        kept = []
        for group in groups:
            key = _canon(group)
            cmds = [h.get('command', '') for h in group.get('hooks', [])]
            if key in managed_keys:
                continue
            if any(cmd in retired_commands for cmd in cmds):
                removed_labels.append(label.get(key, f'retired @ {event}'))
                scrubbed_retired.add((event, key))
                continue
            kept.append(group)
        if kept:
            scrubbed[event] = kept

    merged = {event: list(groups) for event, groups in scrubbed.items()}
    for event, groups in desired.items():
        merged.setdefault(event, []).extend(groups)

    old_keys = {(e, _canon(g)) for e, gs in container.items() for g in gs}
    new_keys = {(e, _canon(g)) for e, gs in merged.items() for g in gs}
    added_names = sorted(label.get(k[1], k[0]) for k in new_keys - old_keys)
    removed_names = sorted(removed_labels + [label.get(k[1], k[0]) for k in (old_keys - new_keys) - scrubbed_retired])
    changed = []
    dirty = report(client, target, added_names, changed, removed_names, kind='hooks')
    if DRY_RUN or not dirty:
        return dirty

    container.clear()
    container.update(merged)
    write_json(target, doc)
    return dirty


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
DRY_RUN = False


def sync_client(client: str, kinds: dict, catalogs: dict, retired: dict) -> bool:
    dirty = False
    if 'mcp' in kinds:
        cli = kinds['mcp']
        servers = catalogs['servers']
        desired = {n: render(s, cli, client) for n, s in servers.items() if client in s['clients']}
        dirty |= MCP_SYNCERS[mcp_client_kind(cli)](client, cli, desired, retired['servers'])
    if 'skills' in kinds:
        dirty |= sync_skills(client, kinds['skills'], catalogs['skills'], retired['skills'])
    if 'hooks' in kinds:
        dirty |= sync_hooks(client, kinds['hooks'], catalogs['hooks'], retired['hook_commands'])
    return dirty


def main() -> int:
    global DRY_RUN
    ap = argparse.ArgumentParser(description='Sync the agent catalog into client configs.')
    ap.add_argument('--dry-run', action='store_true', help='show changes, write nothing')
    ap.add_argument('--client', action='append', help='limit to a client (repeatable)')
    args = ap.parse_args()
    DRY_RUN = args.dry_run

    ctx = build_context()
    catalogs = {'servers': load_servers(ctx), 'skills': load_skills(ctx), 'hooks': load_hooks(ctx)}
    clients = load_toml(CATALOG / 'clients.toml')
    raw_retired = load_toml(CATALOG / 'retired.toml')
    retired = {
        'servers': raw_retired.get('servers', []),
        'skills': raw_retired.get('skills', []),
        'hook_commands': raw_retired.get('hook_commands', []),
    }

    selected = args.client or list(clients)
    unknown = [c for c in selected if c not in clients]
    if unknown:
        die(f'unknown client(s): {", ".join(unknown)}')

    dirty_any = False
    for client in selected:
        dirty_any |= sync_client(client, clients[client], catalogs, retired)
    print()
    if DRY_RUN:
        print('dry-run complete - nothing written')
    else:
        print('done' if dirty_any else 'already in sync')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
