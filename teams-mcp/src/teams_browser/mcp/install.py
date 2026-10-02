"""Register the MCP server with common MCP clients.

Each client keeps its config in a different file with a different schema, so we
normalise that here. ``--print`` shows the snippet without touching disk.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

SERVER_NAME = 'teams-browser'

# Map of client id -> human label.
CLIENTS = ('claude-desktop', 'claude-code', 'cursor', 'vscode', 'opencode')


@dataclass
class ClientTarget:
    label: str
    path: Path
    merge: Callable[[dict[str, Any], tuple[str, list[str]]], dict[str, Any]]


def server_command() -> tuple[str, list[str]]:
    """Resolve an absolute command that launches the stdio server.

    Prefers the installed console script (works from any directory); falls back
    to ``uv run --directory <project> teams-browser-mcp``.
    """
    exe = shutil.which('teams-browser-mcp')
    if exe:
        return exe, []
    project = Path(__file__).resolve().parents[3]
    return 'uv', ['run', '--directory', str(project), 'teams-browser-mcp']


def _home() -> Path:
    return Path.home()


def _client_targets() -> dict[str, ClientTarget]:
    appdata = os.environ.get('APPDATA')
    if sys.platform == 'darwin':
        claude_desktop = _home() / 'Library/Application Support/Claude/claude_desktop_config.json'
    elif appdata:
        claude_desktop = Path(appdata) / 'Claude/claude_desktop_config.json'
    else:
        claude_desktop = _home() / '.config/Claude/claude_desktop_config.json'

    return {
        'claude-desktop': ClientTarget('Claude Desktop', claude_desktop, _merge_command_form),
        'claude-code': ClientTarget('Claude Code (.mcp.json)', Path.cwd() / '.mcp.json', _merge_command_form),
        'cursor': ClientTarget('Cursor', _home() / '.cursor/mcp.json', _merge_command_form),
        'vscode': ClientTarget('VS Code (.vscode/mcp.json)', Path.cwd() / '.vscode/mcp.json', _merge_vscode),
        'opencode': ClientTarget('opencode (opencode.json)', Path.cwd() / 'opencode.json', _merge_opencode),
    }


# --------------------------------------------------------------------------- #
# Per-client merge strategies
# --------------------------------------------------------------------------- #


def _merge_command_form(config: dict[str, Any], cmd: tuple[str, list[str]]) -> dict[str, Any]:
    """claude-desktop / claude-code / cursor: {mcpServers: {name: {command, args}}}."""
    servers = config.setdefault('mcpServers', {})
    servers[SERVER_NAME] = {'command': cmd[0], 'args': cmd[1]}
    return config


def _merge_vscode(config: dict[str, Any], cmd: tuple[str, list[str]]) -> dict[str, Any]:
    servers = config.setdefault('servers', {})
    servers[SERVER_NAME] = {'type': 'stdio', 'command': cmd[0], 'args': cmd[1]}
    return config


def _merge_opencode(config: dict[str, Any], cmd: tuple[str, list[str]]) -> dict[str, Any]:
    servers = config.setdefault('mcp', {})
    servers[SERVER_NAME] = {'type': 'local', 'command': [cmd[0], *cmd[1]], 'enabled': True}
    return config


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def snippet(client: str) -> dict[str, Any]:
    """Return just the fragment a client needs (without an existing file)."""
    target = _resolve(client)
    return target.merge({}, server_command())


def describe_clients() -> list[dict[str, str]]:
    """List supported clients, labels and config paths."""
    return [
        {'client': name, 'label': target.label, 'path': str(target.path)} for name, target in _client_targets().items()
    ]


def install(client: str, *, path: Path | None = None, print_only: bool = False) -> dict[str, Any]:
    target = _resolve(client)
    config_path = path or target.path
    cmd = server_command()

    if print_only:
        return {'client': client, 'path': str(config_path), 'config': snippet(client), 'written': False}

    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f'{config_path} exists but is not valid JSON ({exc}). Fix it or pass --path to a different file.'
            ) from exc

    merged = target.merge(existing, cmd)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(merged, indent=2) + '\n', encoding='utf-8')
    return {'client': client, 'path': str(config_path), 'config': merged, 'written': True}


def _resolve(client: str) -> ClientTarget:
    targets = _client_targets()
    if client not in targets:
        raise ValueError(f"Unknown client '{client}'. Choose one of: {', '.join(CLIENTS)}.")
    return targets[client]
