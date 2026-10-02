import json
import os
import shutil
import subprocess
from typing import Any
from urllib.parse import quote

from entra_tool.errors import GraphError


AZ_FALLBACK_PATHS = ('/opt/homebrew/bin/az', '/usr/local/bin/az', '/usr/bin/az')


def az_command() -> str:
    """Locate the Azure CLI.

    GUI-launched MCP clients (Claude Desktop, VS Code) do not inherit the
    shell PATH, so `shutil.which` can come up empty there. Fall back to the
    usual absolute locations before giving up.
    """
    override = os.environ.get('ENTRA_AZ_PATH')
    if override:
        return override
    found = shutil.which('az')
    if found:
        return found
    for candidate in AZ_FALLBACK_PATHS:
        if os.path.exists(candidate):
            return candidate
    return 'az'


def uri_encode(value: str) -> str:
    return quote(value, safe='')


def odata_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def graph_get(url: str) -> dict[str, Any]:
    result = subprocess.run(
        [az_command(), 'rest', '--only-show-errors', '--method', 'get', '--url', url, '-o', 'json'],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise GraphError(detail or f'az rest failed for {url}')
    try:
        return json.loads(result.stdout or '{}')
    except json.JSONDecodeError as exc:
        raise GraphError(f'az rest returned invalid JSON for {url}: {exc}') from exc


def graph_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
    result = subprocess.run(
        [
            az_command(),
            'rest',
            '--only-show-errors',
            '--method',
            'post',
            '--url',
            url,
            '--headers',
            'Content-Type=application/json',
            '--body',
            json.dumps(body),
            '-o',
            'json',
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise GraphError(detail or f'az rest failed for {url}')
    try:
        return json.loads(result.stdout or '{}')
    except json.JSONDecodeError as exc:
        raise GraphError(f'az rest returned invalid JSON for {url}: {exc}') from exc
