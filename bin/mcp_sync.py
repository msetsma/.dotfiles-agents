#!/usr/bin/env python3
"""mcp-sync - materialise the MCP catalog into every agent's config.

Single source of truth
----------------------
  catalog/servers/*.toml   one file per MCP server (custom + third-party)
  catalog/clients.toml     where each agent keeps its MCP config
  catalog/paths.toml       machine-specific command paths + variables
  catalog/retired.toml     servers to scrub from every client

Behaviour
---------
* Clients flagged ``whole_file = true`` (pure-MCP configs, e.g. VS Code) are
  rendered to ``generated/<client>.<ext>``. Dotter then symlinks that file to
  the real target, so the repo owns it outright.
* Every other client is *merged* in place: only the MCP subtree is rewritten.
  Everything else in the file is preserved. Codex keeps its per-tool tables
  (e.g. ``[mcp_servers.databricks.tools.*]``); only command/args/env change.

Usage
-----
  mcp-sync                apply changes
  mcp-sync --dry-run      semantic diff, write nothing
  mcp-sync --client codex limit to one client
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
CATALOG = REPO / "catalog"
GENERATED = REPO / "generated"

ENV_REF = re.compile(r"\$\{([A-Za-z0-9_]+)\}")
OPCODE_ENV_REF = re.compile(r"\{env:([A-Za-z0-9_]+)\}")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def die(msg: str) -> None:
    print(f"mcp-sync: error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def load_toml(path: Path) -> dict:
    with open(path, "rb") as fh:
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
            elif ch == "\\":
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
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def expand(value, ctx: dict):
    """Expand ``~`` and ``${VAR}`` recursively through strings/lists/dicts."""
    if isinstance(value, str):
        value = os.path.expanduser(value)

        def repl(match: re.Match) -> str:
            name = match.group(1)
            # Known catalog vars (HOME, MCP_HOME, ...) are expanded here.
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
    """opencode spells env references ``{env:VAR}`` instead of ``${VAR}``."""
    return ENV_REF.sub(lambda m: "{env:%s}" % m.group(1), value)


# --------------------------------------------------------------------------- #
# catalog loading
# --------------------------------------------------------------------------- #
def build_context() -> dict:
    paths = load_toml(CATALOG / "paths.toml")
    ctx = {"HOME": os.path.expanduser("~")}
    ctx.update(expand(paths.get("vars", {}), ctx))
    ctx["commands"] = expand(paths.get("commands", {}), ctx)
    # Machine-local overrides (untracked). Keeps org-specific values out of the
    # published repo while still resolving locally.
    local = CATALOG / "local.toml"
    if local.exists():
        data = load_toml(local)
        ctx.update(expand(data.get("vars", {}), ctx))
        ctx["commands"].update(expand(data.get("commands", {}), ctx))
    return ctx


def load_servers(ctx: dict) -> dict:
    servers: dict[str, dict] = {}
    for path in sorted((CATALOG / "servers").glob("*.toml")):
        raw = load_toml(path)
        name = raw["name"]
        kind = raw.get("kind", "local")
        srv = {
            "name": name,
            "kind": kind,
            "origin": raw.get("origin", "external"),
            "description": raw.get("description", ""),
            "clients": list(raw.get("clients", [])),
            "extra": raw.get("extra", {}),
            "package": raw.get("package", {}),
        }
        if kind == "local":
            launch = raw["launch"]
            cmd = launch["command"]
            srv["command"] = ctx["commands"].get(cmd, cmd)
            srv["args"] = [expand(a, ctx) for a in launch.get("args", [])]
            srv["env"] = {k: expand(v, ctx) for k, v in raw.get("env", {}).items()}
            pkg = raw.get("package", {})
            if pkg.get("manager") == "npm" and pkg.get("name"):
                ref = pkg.get("ref") or "latest"
                token = f'{pkg["name"]}@{ref}'
                srv["args"] = [a.replace("{{package}}", token) for a in srv["args"]]
        elif kind == "remote":
            remote = raw["remote"]
            srv["url"] = expand(remote["url"], ctx)
            srv["headers"] = {
                k: expand(v, ctx) for k, v in remote.get("headers", {}).items()
            }
        else:
            die(f"{path.name}: unknown kind {kind!r}")
        servers[name] = srv
    return servers


# --------------------------------------------------------------------------- #
# per-client renderers
# --------------------------------------------------------------------------- #
def r_opencode(srv: dict, cli: dict) -> dict:
    if srv["kind"] == "local":
        out = {"type": "local", "command": [srv["command"], *srv["args"]]}
        if srv["env"]:
            out["environment"] = {k: opcodeify(v) for k, v in srv["env"].items()}
        return out
    out = {"type": "remote", "url": srv["url"]}
    headers = {k: opcodeify(v) for k, v in srv["headers"].items()}
    if headers:
        out["headers"] = headers
    return out


def r_claude_code(srv: dict, cli: dict) -> dict:
    if srv["kind"] == "local":
        return {
            "type": "stdio",
            "command": srv["command"],
            "args": srv["args"],
            "env": dict(srv["env"]),
        }
    out = {"type": "http", "url": srv["url"]}
    if srv["headers"]:
        out["headers"] = srv["headers"]
    return out


def r_claude_desktop(srv: dict, cli: dict) -> dict:
    if srv["kind"] == "local":
        out = {"command": srv["command"], "args": srv["args"]}
        env = dict(srv["env"])
        if cli.get("inject_path"):
            env.setdefault("PATH", cli["inject_path"])
        if env:
            out["env"] = env
        return out
    out = {"type": "http", "url": srv["url"]}
    if srv["headers"]:
        out["headers"] = srv["headers"]
    return out


def r_vscode(srv: dict, cli: dict) -> dict:
    if srv["kind"] == "local":
        out = {"type": "stdio", "command": srv["command"], "args": srv["args"]}
        if srv["env"]:
            out["env"] = srv["env"]
        return out
    out = {"type": "http", "url": srv["url"]}
    if srv["headers"]:
        out["headers"] = srv["headers"]
    return out


def r_codex(srv: dict, cli: dict) -> dict:
    if srv["kind"] == "local":
        return {"command": srv["command"], "args": srv["args"], "env": dict(srv["env"])}
    return {"url": srv["url"]}


RENDERERS = {
    "opencode": r_opencode,
    "claude-code": r_claude_code,
    "claude-desktop": r_claude_desktop,
    "vscode": r_vscode,
    "codex": r_codex,
}
CLIENTS = {}  # populated in main()


def render(srv: dict, cli: dict, client: str) -> dict:
    out = RENDERERS[cli["style"]](srv, cli)
    out.update(srv.get("extra", {}).get(client, {}))
    return out


# --------------------------------------------------------------------------- #
# diff + apply
# --------------------------------------------------------------------------- #
def semantic_diff(existing: dict, desired: dict, retired: list[str]):
    added = [n for n in desired if n not in existing]
    removed = [n for n in existing if n in retired]
    changed = [n for n in desired if n in existing and existing[n] != desired[n]]
    return sorted(added), sorted(changed), sorted(removed)


def report(client: str, path: Path, added, changed, removed) -> bool:
    dirty = bool(added or changed or removed)
    label = "apply" if not DRY_RUN else "dry-run"
    print(f"[{client}] {path}  ({label})")
    if not dirty:
        print("  no changes")
        return False
    for n in added:
        print(f"  + {n}")
    for n in changed:
        print(f"  ~ {n}")
    for n in removed:
        print(f"  - {n}")
    return True


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def deep_get(doc: dict, keys: list[str]) -> dict:
    node = doc
    for key in keys:
        node = node.setdefault(key, {})
    return node


def sync_json_client(client: str, cli: dict, desired: dict, retired: list) -> bool:
    target = Path(os.path.expanduser(cli["path"]))
    if target.exists():
        doc = json.loads(strip_jsonc(target.read_text()))
    else:
        doc = {}
    container = deep_get(doc, cli["container"])
    added, changed, removed = semantic_diff(container, desired, retired)
    dirty = report(client, target, added, changed, removed)
    if DRY_RUN or not dirty:
        return dirty
    for name in retired:
        container.pop(name, None)
    container.update(desired)
    write_json(target, doc)
    return dirty


def sync_whole_file_client(client: str, cli: dict, desired: dict, retired: list) -> bool:
    """Pure-MCP client: render to generated/ for dotter to symlink."""
    target = Path(os.path.expanduser(cli["path"]))
    if target.exists():
        doc = json.loads(strip_jsonc(target.read_text()))
    else:
        doc = {}
    container = deep_get(doc, cli["container"])
    added, changed, removed = semantic_diff(container, desired, retired)
    dirty = report(client, target, added, changed, removed)
    print(f"    -> generated/{client}.json (dotter symlinks this to the target)")
    if DRY_RUN:
        return dirty
    for name in retired:
        container.pop(name, None)
    container.update(desired)
    write_json(GENERATED / f"{client}.json", doc)
    return dirty


def sync_codex(client: str, cli: dict, desired: dict, retired: list) -> bool:
    target = Path(os.path.expanduser(cli["path"]))
    try:
        import tomlkit
    except ModuleNotFoundError:
        die("codex client needs tomlkit; run via bin/mcp-sync (uv provides it)")
    doc = tomlkit.parse(target.read_text()) if target.exists() else tomlkit.document()
    table = doc.get("mcp_servers")
    if table is None:
        table = tomlkit.table()
        doc["mcp_servers"] = table

    existing = {}
    for name in table:
        entry = table[name]
        existing[name] = {
            "command": entry.get("command"),
            "args": list(entry.get("args", [])),
            "env": dict(entry.get("env", {})),
        }
    added, changed, removed = semantic_diff(existing, desired, retired)
    dirty = report(client, target, added, changed, removed)
    if DRY_RUN or not dirty:
        return dirty

    for name in retired:
        if name in table:
            del table[name]
    for name, srv in desired.items():
        if "command" not in srv:
            continue  # remote servers are not managed in Codex here
        if name in table:
            entry = table[name]
        else:
            entry = tomlkit.table()
            table[name] = entry
        entry["command"] = srv["command"]
        entry["args"] = srv["args"]
        if srv["env"]:
            env = tomlkit.table()
            for k, v in srv["env"].items():
                env[k] = v
            entry["env"] = env
        elif "env" in entry:
            del entry["env"]
    target.write_text(tomlkit.dumps(doc))
    return dirty


SYNCERS = {
    "json": sync_json_client,
    "whole_file": sync_whole_file_client,
    "codex": sync_codex,
}


def client_kind(cli: dict) -> str:
    if cli.get("whole_file"):
        return "whole_file"
    if cli["format"] == "toml":
        return "codex"
    if cli["format"] in ("json", "jsonc"):
        return "json"
    die(f"unsupported format {cli['format']!r}")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
DRY_RUN = False


def main() -> int:
    global DRY_RUN
    ap = argparse.ArgumentParser(description="Sync the MCP catalog into agent configs.")
    ap.add_argument("--dry-run", action="store_true", help="show changes, write nothing")
    ap.add_argument("--client", action="append", help="limit to a client (repeatable)")
    args = ap.parse_args()
    DRY_RUN = args.dry_run

    ctx = build_context()
    servers = load_servers(ctx)
    CLIENTS.update(load_toml(CATALOG / "clients.toml"))
    retired = load_toml(CATALOG / "retired.toml").get("servers", [])

    selected = args.client or list(CLIENTS)
    unknown = [c for c in selected if c not in CLIENTS]
    if unknown:
        die(f"unknown client(s): {', '.join(unknown)}")

    dirty_any = False
    for client in selected:
        cli = CLIENTS[client]
        desired = {
            name: render(srv, cli, client)
            for name, srv in servers.items()
            if client in srv["clients"]
        }
        dirty_any |= SYNCERS[client_kind(cli)](client, cli, desired, retired)
    print()
    if DRY_RUN:
        print("dry-run complete - nothing written")
    else:
        print("done" if dirty_any else "already in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
