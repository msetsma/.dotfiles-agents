"""Catalog loading: one loader per resource kind, ``local.toml`` overrides,
profile selection and ``retired.toml``."""

from __future__ import annotations

from pathlib import Path

from agent_profile import deep_merge, is_selected, split_command

from . import common
from .common import die, expand, load_toml


__all__ = [
    'LOADERS',
    'PACKAGE_FILTERS',
    'RETIRED_KINDS',
    'load_catalogs',
    'load_hooks',
    'load_instructions',
    'load_mcp',
    'load_packages',
    'load_plugins',
    'load_raw',
    'load_retired',
    'load_skills',
]


# --------------------------------------------------------------------------- #
# catalog loading
# --------------------------------------------------------------------------- #
def load_raw(path: Path, kind: str, local: dict) -> dict:
    """Catalog TOML with any ``local.toml [<kind>.<name>]`` override merged in."""
    raw = load_toml(path)
    override = local.get(kind, {}).get(raw['name'], {})
    return deep_merge(raw, override) if override else raw


def load_mcp(ctx: dict, local: dict) -> dict:
    mcp: dict[str, dict] = {}
    for path in sorted((common.CATALOG / 'mcp').glob('*.toml')):
        raw = load_raw(path, 'mcp', local)
        name = raw['name']
        kind = raw.get('kind', 'local')
        srv = {
            'name': name,
            'kind': kind,
            'origin': raw.get('origin', 'external'),
            'description': raw.get('description', ''),
            'clients': list(raw.get('clients', [])),
            'tags': list(raw.get('tags', [])),
            'extra': raw.get('extra', {}),
            'package': raw.get('package', {}),
        }
        if kind == 'local':
            launch = raw['launch']
            cmd = launch['command']
            srv['command'], prefix = split_command(ctx['commands'].get(cmd, cmd))
            srv['args'] = prefix + [expand(a, ctx) for a in launch.get('args', [])]
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


def load_skills(ctx: dict, local: dict) -> dict:
    """Each ``catalog/skills/<name>.toml`` points at ``catalog/skills/<name>/``."""
    skills: dict[str, dict] = {}
    for path in sorted((common.CATALOG / 'skills').glob('*.toml')):
        raw = load_raw(path, 'skills', local)
        name = raw['name']
        source = common.CATALOG / 'skills' / name
        if not (source / 'SKILL.md').is_file():
            die(f'{path.name}: missing {source.relative_to(common.REPO)}/SKILL.md')
        skills[name] = {
            'name': name,
            'description': raw.get('description', ''),
            'clients': list(raw.get('clients', [])),
            'tags': list(raw.get('tags', [])),
            'dir': source,
        }
    return skills


def _plugin_order(path: Path, raw: dict) -> int | None:
    """Optional ``order``: catalog plugins with one load first, lowest first."""
    order = raw.get('order')
    if order is not None and (not isinstance(order, int) or isinstance(order, bool)):
        die(f'{path.name}: order must be an integer, got {order!r}')
    return order


def load_plugins(ctx: dict, local: dict) -> dict:
    """Each ``catalog/plugins/<name>.toml`` is either a marketplace plugin (a
    ``[marketplace]`` block plus a ``plugin = "<name>@<marketplace>"`` id, for
    Claude Code) or points at ``catalog/plugins/<name>.ts`` (opencode)."""
    plugins: dict[str, dict] = {}
    for path in sorted((common.CATALOG / 'plugins').glob('*.toml')):
        raw = load_raw(path, 'plugins', local)
        name = raw['name']
        item = {
            'name': name,
            'description': raw.get('description', ''),
            'clients': list(raw.get('clients', [])),
            'tags': list(raw.get('tags', [])),
        }
        if 'marketplace' in raw:
            item['id'] = raw['plugin']
            item['marketplace'] = expand(raw['marketplace'], ctx)
            item['order'] = _plugin_order(path, raw)
            if 'options' in raw:
                item['options'] = dict(raw['options'])
        else:
            source = common.CATALOG / 'plugins' / f'{name}.ts'
            if not source.is_file():
                die(f'{path.name}: missing {source.relative_to(common.REPO)}')
            item['source'] = source
        plugins[name] = item
    return plugins


def load_hooks(ctx: dict, local: dict) -> dict:
    hooks: dict[str, dict] = {}
    for path in sorted((common.CATALOG / 'hooks').glob('*.toml')):
        raw = load_raw(path, 'hooks', local)
        name = raw['name']
        command = raw['command']
        hooks[name] = {
            'name': name,
            'description': raw.get('description', ''),
            'event': raw['event'],
            'matcher': raw.get('matcher', ''),
            'clients': list(raw.get('clients', [])),
            'tags': list(raw.get('tags', [])),
            'command': expand(command['run'], ctx),
            'timeout': command.get('timeout'),
            'status_message': command.get('statusMessage'),
        }
    return hooks


def load_instructions(ctx: dict, local: dict) -> dict:
    """Each ``catalog/instructions/<name>.toml`` points at ``<name>.md``."""
    instructions: dict[str, dict] = {}
    for path in sorted((common.CATALOG / 'instructions').glob('*.toml')):
        raw = load_raw(path, 'instructions', local)
        name = raw['name']
        source = common.CATALOG / 'instructions' / f'{name}.md'
        if not source.is_file():
            die(f'{path.name}: missing {source.relative_to(common.REPO)}')
        instructions[name] = {
            'name': name,
            'description': raw.get('description', ''),
            'clients': list(raw.get('clients', [])),
            'tags': list(raw.get('tags', [])),
            'text': source.read_text(encoding='utf-8'),
        }
    return instructions


PACKAGE_FILTERS = ('extensions', 'skills', 'prompts', 'themes')


def load_packages(ctx: dict, local: dict) -> dict:
    """Each ``catalog/packages/<name>.toml`` names one pi package ``source``,
    plus optional resource filters (see pi docs/packages.md)."""
    packages: dict[str, dict] = {}
    for path in sorted((common.CATALOG / 'packages').glob('*.toml')):
        raw = load_raw(path, 'packages', local)
        name = raw['name']
        packages[name] = {
            'name': name,
            'description': raw.get('description', ''),
            'clients': list(raw.get('clients', [])),
            'tags': list(raw.get('tags', [])),
            'source': expand(raw['source'], ctx),
            'filters': {k: list(raw[k]) for k in PACKAGE_FILTERS if k in raw},
        }
    return packages


LOADERS = {
    'mcp': load_mcp,
    'skills': load_skills,
    'plugins': load_plugins,
    'hooks': load_hooks,
    'instructions': load_instructions,
    'packages': load_packages,
}


def load_catalogs(ctx: dict, local: dict, profile: dict) -> dict:
    """Every kind, with local overrides applied and profile exclusions dropped."""
    return {
        kind: {name: item for name, item in loader(ctx, local).items() if is_selected(item, profile)}
        for kind, loader in LOADERS.items()
    }


RETIRED_KINDS = ('mcp', 'skills', 'plugins', 'hook_commands', 'instructions', 'packages')


def load_retired(path: Path, ctx: dict) -> dict[str, list[str]]:
    """``retired.toml`` per kind, with ``~`` and catalog ``${VARS}`` expanded."""
    raw = load_toml(path)
    return {kind: expand(raw.get(kind, []), ctx) for kind in RETIRED_KINDS}
