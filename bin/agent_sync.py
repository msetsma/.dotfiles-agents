#!/usr/bin/env python3
"""agent-sync - materialise the agent catalog into every client's config.

Single source of truth
----------------------
  catalog/mcp/*.toml   one file per MCP server (custom + third-party)
  catalog/skills/*.toml    one file per skill (content in catalog/skills/<name>/)
  catalog/plugins/*.toml   one file per plugin (content in catalog/plugins/<name>.ts)
  catalog/hooks/*.toml     one file per event hook
  catalog/instructions/*.toml  one file per instruction overlay (content in <name>.md)
  catalog/clients.toml     where each agent keeps each kind, and how it spells it
  catalog/paths.toml       machine-specific command paths + variables
  catalog/retired.toml     resources to scrub from every client

Behaviour
---------
Each client declares one block per *kind* it supports. Three strategies:

* ``merge``     rewrite a subtree of a shared config in place, leaving every
                other key alone (MCP maps; hook event maps).
* ``symlink``   link each catalog item into a client directory (skills, plugins).
* ``block``     swap a marked region inside a shared markdown file, leaving the
                rest of the file alone (instructions).

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
from typing import NoReturn


try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py<3.11
    import tomli as tomllib  # type: ignore[import-not-found]

REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO / 'catalog'

ENV_REF = re.compile(r'\$\{([A-Za-z0-9_]+)\}')
OPCODE_ENV_REF = re.compile(r'\{env:([A-Za-z0-9_]+)\}')


class _Config:
    dry_run = False


CONFIG = _Config()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def die(msg: str) -> NoReturn:
    print(f'agent-sync: error: {msg}', file=sys.stderr)
    raise SystemExit(1)


def load_toml(path: Path) -> dict:
    with open(path, 'rb') as fh:
        return tomllib.load(fh)


def _consume_string(text: str, start: int) -> tuple[str, int]:
    """Return the quoted literal at ``start`` and the index just past it."""
    i = start + 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '\\':
            i += 2
            continue
        if ch == '"':
            return text[start : i + 1], i + 1
        i += 1
    return text[start:n], n


def _skip_comment(text: str, start: int) -> int:
    """Skip a ``//`` or ``/* */`` comment and return the index just past it."""
    n = len(text)
    if text[start + 1] == '/':
        i = start
        while i < n and text[i] not in '\r\n':
            i += 1
        return i
    i = start + 2
    while i + 1 < n and not (text[i] == '*' and text[i + 1] == '/'):
        i += 1
    return i + 2


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments while respecting string literals.

    A naive regex would eat the `//` in `https://...`, so scan character by
    character and only strip comments outside of quoted strings.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            literal, i = _consume_string(text, i)
            out.append(literal)
            continue
        if ch == '/' and i + 1 < n and text[i + 1] in '/*':
            i = _skip_comment(text, i)
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def expand(value, ctx: dict):
    """Expand ``~`` and ``${VAR}`` recursively through strings/lists/dicts."""
    if isinstance(value, str):
        if value.startswith('~'):
            value = str(Path(value).expanduser())

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
    return ENV_REF.sub(lambda match: f'{{env:{match.group(1)}}}', value)


# --------------------------------------------------------------------------- #
# catalog loading
# --------------------------------------------------------------------------- #
def build_context() -> dict:
    paths = load_toml(CATALOG / 'paths.toml')
    ctx = {'HOME': str(Path.home())}
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


def load_mcp(ctx: dict) -> dict:
    mcp: dict[str, dict] = {}
    for path in sorted((CATALOG / 'mcp').glob('*.toml')):
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
        mcp[name] = srv
    return mcp


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


def load_plugins(ctx: dict) -> dict:
    """Each ``catalog/plugins/<name>.toml`` points at ``catalog/plugins/<name>.ts``."""
    plugins: dict[str, dict] = {}
    for path in sorted((CATALOG / 'plugins').glob('*.toml')):
        raw = load_toml(path)
        name = raw['name']
        source = CATALOG / 'plugins' / f'{name}.ts'
        if not source.is_file():
            die(f'{path.name}: missing {source.relative_to(REPO)}')
        plugins[name] = {
            'name': name,
            'description': raw.get('description', ''),
            'clients': list(raw.get('clients', [])),
            'source': source,
        }
    return plugins


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


def load_instructions(ctx: dict) -> dict:
    """Each ``catalog/instructions/<name>.toml`` points at ``<name>.md``."""
    instructions: dict[str, dict] = {}
    for path in sorted((CATALOG / 'instructions').glob('*.toml')):
        raw = load_toml(path)
        name = raw['name']
        source = CATALOG / 'instructions' / f'{name}.md'
        if not source.is_file():
            die(f'{path.name}: missing {source.relative_to(REPO)}')
        instructions[name] = {
            'name': name,
            'description': raw.get('description', ''),
            'clients': list(raw.get('clients', [])),
            'text': source.read_text(),
        }
    return instructions


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
    label = 'apply' if not CONFIG.dry_run else 'dry-run'
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
    target = Path(cli['path']).expanduser()
    doc = json.loads(strip_jsonc(target.read_text())) if target.exists() else {}
    container = deep_get(doc, cli['container'])
    added, changed, removed = semantic_diff(container, desired, retired)
    dirty = report(client, target, added, changed, removed)
    if CONFIG.dry_run or not dirty:
        return dirty
    for name in retired:
        container.pop(name, None)
    container.update(desired)
    write_json(target, doc)
    return dirty


def _read_codex_servers(table) -> dict:
    return {
        name: {
            'command': table[name].get('command'),
            'args': list(table[name].get('args', [])),
            'env': dict(table[name].get('env', {})),
        }
        for name in table
    }


def _write_codex_servers(tomlkit, table, desired: dict, retired: list) -> None:
    for name in retired:
        if name in table:
            del table[name]
    for name, srv in desired.items():
        if 'command' not in srv:
            continue  # remote servers are not managed in Codex here
        entry = table[name] if name in table else tomlkit.table()
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


def sync_codex_mcp(client: str, cli: dict, desired: dict, retired: list) -> bool:
    target = Path(cli['path']).expanduser()
    try:
        import tomlkit
    except ModuleNotFoundError:
        die('codex client needs tomlkit; run via bin/agent-sync (uv provides it)')
    doc = tomlkit.parse(target.read_text()) if target.exists() else tomlkit.document()
    table = doc.get('mcp_servers')
    if table is None:
        table = tomlkit.table()
        doc['mcp_servers'] = table

    existing = _read_codex_servers(table)
    added, changed, removed = semantic_diff(existing, desired, retired)
    dirty = report(client, target, added, changed, removed)
    if CONFIG.dry_run or not dirty:
        return dirty

    _write_codex_servers(tomlkit, table, desired, retired)
    target.write_text(tomlkit.dumps(doc))
    return dirty


MCP_SYNCERS = {'json': sync_json_mcp, 'codex': sync_codex_mcp}


def mcp_client_kind(cli: dict) -> str:
    if cli['format'] == 'toml':
        return 'codex'
    if cli['format'] in ('json', 'jsonc'):
        return 'json'
    die(f'unsupported format {cli["format"]!r}')


# --------------------------------------------------------------------------- #
# skills (symlink strategy)
# --------------------------------------------------------------------------- #
def sync_skills(client: str, cli: dict, skills: dict, retired: list) -> bool:
    base = Path(cli['path']).expanduser()
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
    if CONFIG.dry_run or not dirty:
        return dirty

    base.mkdir(parents=True, exist_ok=True)
    for name in removed + changed:
        (base / name).unlink()
    for name in added + changed:
        (base / name).symlink_to(desired[name]['dir'])
    return dirty


def sync_plugins(client: str, cli: dict, plugins: dict, retired: list) -> bool:
    base = Path(cli['path']).expanduser()
    manage_root = (CATALOG / 'plugins').resolve()
    desired = {f'{n}.ts': p for n, p in plugins.items() if client in p['clients'] and n not in retired}

    # Only touch symlinks that point back into our catalog/plugins/.
    existing: dict[str, str] = {}
    if base.is_dir():
        for entry in base.iterdir():
            if entry.is_symlink():
                resolved = os.path.realpath(entry)
                if resolved == str(manage_root) or resolved.startswith(str(manage_root) + os.sep):
                    existing[entry.name] = resolved

    added = sorted(n for n in desired if n not in existing)
    changed = sorted(n for n in desired if n in existing and existing[n] != str(desired[n]['source'].resolve()))
    removed = sorted(n for n in existing if n not in desired)
    dirty = report(client, base, added, changed, removed, kind='plugins')
    if CONFIG.dry_run or not dirty:
        return dirty

    base.mkdir(parents=True, exist_ok=True)
    for name in removed + changed:
        (base / name).unlink()
    for name in added + changed:
        (base / name).symlink_to(desired[name]['source'])
    return dirty


# --------------------------------------------------------------------------- #
# hooks (additive merge into a shared event map)
# --------------------------------------------------------------------------- #
def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True)


def _collect_hook_groups(client: str, hooks: dict, renderer) -> tuple[dict, dict[str, str], set[str]]:
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
    return desired, label, managed_keys


def _keep_hook_group(
    group: dict,
    event: str,
    managed_keys: set,
    retired_commands: list,
    label: dict,
    removed_labels: list,
    scrubbed_retired: set,
) -> bool:
    key = _canon(group)
    if key in managed_keys:
        return False
    for entry in group.get('hooks', []):
        if entry.get('command', '') in retired_commands:
            removed_labels.append(label.get(key, f'retired @ {event}'))
            scrubbed_retired.add((event, key))
            return False
    return True


def _scrub_hooks(container: dict, managed_keys: set, retired_commands: list, label: dict):
    scrubbed: dict[str, list] = {}
    removed_labels: list[str] = []
    scrubbed_retired: set = set()
    for event, groups in container.items():
        kept = [
            group
            for group in groups
            if _keep_hook_group(group, event, managed_keys, retired_commands, label, removed_labels, scrubbed_retired)
        ]
        if kept:
            scrubbed[event] = kept
    return scrubbed, removed_labels, scrubbed_retired


def _hook_diff(container: dict, merged: dict, scrubbed_retired: set, removed_labels: list, label: dict):
    old_keys = {(event, _canon(group)) for event, groups in container.items() for group in groups}
    new_keys = {(event, _canon(group)) for event, groups in merged.items() for group in groups}
    added = sorted(label.get(key[1], key[0]) for key in new_keys - old_keys)
    dropped = (old_keys - new_keys) - scrubbed_retired
    removed = sorted(removed_labels + [label.get(key[1], key[0]) for key in dropped])
    return added, removed


def sync_hooks(client: str, cli: dict, hooks: dict, retired_commands: list) -> bool:
    if cli['style'] not in HOOK_RENDERERS:
        print(f'[{client}] hooks: style {cli["style"]!r} not supported yet; skipping')
        return False
    renderer = HOOK_RENDERERS[cli['style']]
    target = Path(cli['path']).expanduser()
    desired, label, managed_keys = _collect_hook_groups(client, hooks, renderer)

    doc = json.loads(strip_jsonc(target.read_text())) if target.exists() else {}
    container = deep_get(doc, cli['container'])

    # Existing events, minus any group the catalog owns (so re-sync and
    # retirement are idempotent) and minus explicitly retired commands.
    scrubbed, removed_labels, scrubbed_retired = _scrub_hooks(container, managed_keys, retired_commands, label)

    merged = {event: list(groups) for event, groups in scrubbed.items()}
    for event, groups in desired.items():
        merged.setdefault(event, []).extend(groups)

    added_names, removed_names = _hook_diff(container, merged, scrubbed_retired, removed_labels, label)
    dirty = report(client, target, added_names, [], removed_names, kind='hooks')
    if CONFIG.dry_run or not dirty:
        return dirty

    container.clear()
    container.update(merged)
    write_json(target, doc)
    return dirty


# --------------------------------------------------------------------------- #
# instructions (managed markdown block in a shared file)
# --------------------------------------------------------------------------- #
# Clients read a single global instruction file (opencode: AGENTS.md, Claude
# Code: CLAUDE.md, Codex: AGENTS.md). There is no subtree to key into, so the
# catalog owns one delimited region and everything outside it is left alone.
INSTR_BEGIN = '<!-- dotfiles-agents:begin (managed by agent-sync; edit catalog/instructions/ instead) -->'
INSTR_END = '<!-- dotfiles-agents:end -->'
INSTR_REGION = re.compile(re.escape(INSTR_BEGIN) + r'.*?' + re.escape(INSTR_END), re.DOTALL)
INSTR_NAMES = re.compile(r'<!-- instructions: (.*?) -->', re.DOTALL)


def render_instruction_block(items: dict[str, str]) -> str:
    names = sorted(items)
    if not names:
        return ''
    body = '\n\n'.join(items[name].strip() for name in names)
    return f'{INSTR_BEGIN}\n<!-- instructions: {", ".join(names)} -->\n{body}\n{INSTR_END}'


def block_names(region: str) -> list[str]:
    match = INSTR_NAMES.search(region)
    if not match:
        return []
    return [name.strip() for name in match.group(1).split(',') if name.strip()]


def block_body(region: str) -> str:
    match = re.search(r'<!-- instructions: .*? -->\n(.*?)\n?' + re.escape(INSTR_END), region, re.DOTALL)
    return match.group(1) if match else ''


def _final_newline(text: str) -> str:
    """Trim trailing blank lines and end with exactly one newline (or ``''``)."""
    stripped = text.rstrip('\n')
    return f'{stripped}\n' if stripped else ''


def apply_instruction_block(document: str, block: str) -> str:
    """Swap the managed region for ``block``, preserving everything else.

    A file that is an exact unmanaged copy of the block content (the state just
    before the catalog first adopts it) is wrapped in markers instead of gaining
    a second copy.
    """
    match = INSTR_REGION.search(document)
    if match:
        head = document[: match.start()].rstrip('\n')
        tail = document[match.end() :].lstrip('\n')
        parts = [part for part in (head, block, tail) if part]
        return _final_newline('\n\n'.join(parts))
    if not block:
        return document
    if document.strip() and document.strip() == block_body(block).strip():
        return _final_newline(block)
    body = document.rstrip('\n')
    return _final_newline(f'{body}\n\n{block}') if body else _final_newline(block)


def _load_instruction_block(target: Path) -> tuple[str, str | None, list[str], str]:
    existing = target.read_text() if target.exists() else ''
    match = INSTR_REGION.search(existing)
    region = match.group(0) if match else None
    return existing, region, block_names(region) if region else [], block_body(region) if region else ''


def sync_instructions(client: str, cli: dict, instructions: dict, retired: list) -> bool:
    target = Path(cli['path']).expanduser()
    desired = {
        name: item['text'] for name, item in instructions.items() if client in item['clients'] and name not in retired
    }
    existing, region, old_names, old_body = _load_instruction_block(target)
    new_block = render_instruction_block(desired)
    new_body = '\n\n'.join(desired[name].strip() for name in sorted(desired))

    added = sorted(set(desired) - set(old_names))
    removed = sorted(set(old_names) - set(desired))
    common = sorted(set(desired) & set(old_names))
    changed = common if old_body != new_body else []
    dirty = bool(added or removed or changed) or bool(new_block) != bool(region)
    report(client, target, added, changed, removed, kind='instructions')
    if not dirty or CONFIG.dry_run:
        return dirty

    updated = apply_instruction_block(existing, new_block)
    if updated:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(updated)
    elif target.exists():
        target.unlink()
    return dirty


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def sync_client(client: str, kinds: dict, catalogs: dict, retired: dict) -> bool:
    dirty = False
    if 'mcp' in kinds:
        cli = kinds['mcp']
        mcp = catalogs['mcp']
        desired = {n: render(s, cli, client) for n, s in mcp.items() if client in s['clients']}
        dirty |= MCP_SYNCERS[mcp_client_kind(cli)](client, cli, desired, retired['mcp'])
    if 'skills' in kinds:
        dirty |= sync_skills(client, kinds['skills'], catalogs['skills'], retired['skills'])
    if 'plugins' in kinds:
        dirty |= sync_plugins(client, kinds['plugins'], catalogs['plugins'], retired['plugins'])
    if 'hooks' in kinds:
        dirty |= sync_hooks(client, kinds['hooks'], catalogs['hooks'], retired['hook_commands'])
    if 'instructions' in kinds:
        dirty |= sync_instructions(client, kinds['instructions'], catalogs['instructions'], retired['instructions'])
    return dirty


def main() -> int:
    ap = argparse.ArgumentParser(description='Sync the agent catalog into client configs.')
    ap.add_argument('--dry-run', action='store_true', help='show changes, write nothing')
    ap.add_argument('--client', action='append', help='limit to a client (repeatable)')
    args = ap.parse_args()
    CONFIG.dry_run = args.dry_run

    ctx = build_context()
    catalogs = {
        'mcp': load_mcp(ctx),
        'skills': load_skills(ctx),
        'plugins': load_plugins(ctx),
        'hooks': load_hooks(ctx),
        'instructions': load_instructions(ctx),
    }
    clients = load_toml(CATALOG / 'clients.toml')
    raw_retired = load_toml(CATALOG / 'retired.toml')
    retired = {
        'mcp': raw_retired.get('mcp', []),
        'skills': raw_retired.get('skills', []),
        'plugins': raw_retired.get('plugins', []),
        'hook_commands': raw_retired.get('hook_commands', []),
        'instructions': raw_retired.get('instructions', []),
    }

    selected = args.client or list(clients)
    unknown = [c for c in selected if c not in clients]
    if unknown:
        die(f'unknown client(s): {", ".join(unknown)}')

    dirty_any = False
    for client in selected:
        dirty_any |= sync_client(client, clients[client], catalogs, retired)
    print()
    if CONFIG.dry_run:
        print('dry-run complete - nothing written')
    else:
        print('done' if dirty_any else 'already in sync')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
