"""Shared state and helpers: repo paths, run config, ``${VAR}`` expansion, the
per-machine context, JSON/JSONC config I/O and the diff report every syncer prints."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import NoReturn


try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py<3.11
    import tomli as tomllib  # type: ignore[import-not-found]

from jsonc import has_comments, loads_jsonc


# (added, changed, removed) names, each sorted: what a sync would do.
type Diff = tuple[list[str], list[str], list[str]]


__all__ = [
    'CATALOG',
    'CONFIG',
    'REPO',
    'build_context',
    'deep_get',
    'die',
    'expand',
    'load_local',
    'load_toml',
    'opcodeify',
    'read_json_config',
    'refuse_commented',
    'report',
    'semantic_diff',
    'warn',
    'write_json',
]


REPO = Path(__file__).resolve().parent.parent.parent
CATALOG = REPO / 'catalog'


ENV_REF = re.compile(r'\$\{([A-Za-z0-9_]+)\}')


class _Config:
    dry_run = False


CONFIG = _Config()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def die(msg: str) -> NoReturn:
    print(f'agent-sync: error: {msg}', file=sys.stderr)
    raise SystemExit(1)


def warn(msg: str) -> None:
    print(f'agent-sync: warning: {msg}', file=sys.stderr)


def load_toml(path: Path) -> dict:
    with open(path, 'rb') as fh:
        return tomllib.load(fh)


def expand[T](value: T, ctx: dict) -> T:
    """Expand ``~`` and ``${VAR}`` recursively through strings/lists/dicts."""
    if isinstance(value, str):
        if value.startswith('~'):
            # Forward slashes even on Windows: these land in bash hook commands.
            value = Path(value).expanduser().as_posix()

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
# local overrides + context
# --------------------------------------------------------------------------- #
def load_local() -> dict:
    """Machine-local overrides (untracked). Keeps org-specific values and the
    per-machine profile out of the published repo; absent means no overrides."""
    local = CATALOG / 'local.toml'
    return load_toml(local) if local.exists() else {}


def build_context(local: dict) -> dict:
    paths = load_toml(CATALOG / 'paths.toml')
    ctx = {'HOME': Path.home().as_posix()}
    ctx.update(expand(paths.get('vars', {}), ctx))
    ctx['commands'] = expand(paths.get('commands', {}), ctx)
    ctx.update(expand(local.get('vars', {}), ctx))
    ctx['commands'].update(expand(local.get('commands', {}), ctx))
    return ctx


# --------------------------------------------------------------------------- #
# diff + apply
# --------------------------------------------------------------------------- #
def semantic_diff(existing: dict, desired: dict, retired: list[str]) -> Diff:
    added = [n for n in desired if n not in existing]
    removed = [n for n in existing if n in retired]
    changed = [n for n in desired if n in existing and existing[n] != desired[n]]
    return sorted(added), sorted(changed), sorted(removed)


def report(
    client: str, path: Path, added: list[str], changed: list[str], removed: list[str], kind: str = 'mcp'
) -> bool:
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
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def read_json_config(target: Path) -> tuple[dict, bool]:
    """Parse a JSON/JSONC client config; ``(doc, has_comments)``.

    A missing file is an empty document. Unparseable files stop the sync with
    the path rather than a traceback.
    """
    if not target.exists():
        return {}, False
    try:
        text = target.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        die(f'{target}: cannot read: {exc}')
    try:
        doc = loads_jsonc(text)
    except json.JSONDecodeError as exc:
        die(f'{target}: not valid JSON/JSONC: {exc}')
    if not isinstance(doc, dict):
        die(f'{target}: expected a JSON object at the top level')
    return doc, has_comments(text)


def refuse_commented(target: Path, commented: bool, pending: dict | None = None) -> bool:
    """True (and warn) when writing ``target`` would drop its comments.

    Rewriting serialises plain JSON, so a commented file is left untouched and
    the user is told what to apply by hand.
    """
    if not commented:
        return False
    if CONFIG.dry_run:
        warn(f'{target} has comments that a rewrite would destroy; a real sync will not write it.')
        return True
    warn(f'{target} has comments that a rewrite would destroy; not writing it. Apply the changes above by hand.')
    for name, value in (pending or {}).items():
        print(f'  {json.dumps(name)}: {json.dumps(value, ensure_ascii=False)}', file=sys.stderr)
    return True


def deep_get(doc: dict, keys: list[str]) -> dict:
    node = doc
    for key in keys:
        node = node.setdefault(key, {})
    return node
