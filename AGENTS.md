# AGENTS.md

Instructions for anyone (human or agent) working in `~/.dotfiles-agents`. This directory
registers MCP servers and other resources with the local agent software. Custom
MCP servers each live in their own repo under
[github.com/msetsma](https://github.com/msetsma) and are cloned into `vendor/`.

## The one rule

**When you create or change a resource, update its catalog entry and the
Inventory table in [`README.md`](README.md).** The catalog is the single source of
truth for everything the agents load: MCP servers (custom *and*
third-party), skills, plugins, hooks, and instructions; the README table is the
human index. No catalog entry, no resource.

Then run `make sync` to push the catalog into every
agent. See [README.md](README.md) for the merge-vs-symlink model and
[`bin/agent_sync.py`](bin/agent_sync.py) for the engine. Don't hand-edit the agent
config files (`~/.claude.json`, `~/.config/opencode/opencode.jsonc`,
`~/.codex/config.toml`, the Claude Desktop config); edit the catalog instead.

Tag resources that shouldn't reach every machine (`tags = ["work"]`,
`["macos"]`); secondary machines filter them via `[profile]` in the untracked
`catalog/local.toml` (see README "Per-machine profiles"). No profile = sync
everything, which is the primary (Mac) behaviour.

Servers carry a `[package]` block; `make agent-outdated` reports newer
npm/uv/git versions and uncloned server repos, `make agent-update` applies them. Prefer `ref = "latest"`
(float) unless you need a specific version.

Also keep the server repo's own `README.md` (the deep docs) and
`pyproject.toml` `description` in sync: `README.md` at the root is the summary,
the server README is the detail.

## Repo conventions

- **One server = one repo** under github.com/msetsma, self-contained
  (`pyproject.toml`, `README.md`, `src/` or `server.py`, `tests/`). Its catalog
  entry names the repo, and `make agent-update` clones it into
  `vendor/<repo>/` (gitignored). Commit server changes in the server repo, not
  here.
- **Python + `uv`.** `requires-python = ">=3.14"`. Run via
  `uv run --directory <dir> ...`, so no global install is needed. (Exception:
  `entra-mcp` is uv-tool-installed because it also ships a daily-use CLI; see
  the launch commands below.)
- **stdio transport.** Agents launch the process; nothing listens on a port.
- **Read-only by default.** Annotate tools with
  `ToolAnnotations(read_only_hint=True, ...)` so clients can skip confirmation.
  Any write tool must be called out explicitly in the server README.
- **Verify before registering.** Every server needs a way to prove it works
  offline (`test_*.py` / `test_server.py`) before it's wired into an agent.

## Inventory (high level)

| Server | Repo | Purpose | Entry point |
|---|---|---|---|
| `sharepoint-mcp` | [msetsma/sharepoint-mcp](https://github.com/msetsma/sharepoint-mcp) | SharePoint: synced libraries read from disk (`sp_roots`/`sp_find`/`sp_read`) and lists via Graph (`sp_lists`/`sp_list_items`). No Full Disk Access; list tools need a signed-in `az`, read-only unless `SHAREPOINT_ENABLE_WRITES` is set. | `server.py` |
| `notes` | [msetsma/notes-mcp](https://github.com/msetsma/notes-mcp) | Read/write the personal Obsidian vault: qmd search, git-committed edits, inbox capture and triage, related-note discovery, digests, a vault lint. | `notes-mcp` (console script) |
| `teams-browser` | [msetsma/teams-mcp](https://github.com/msetsma/teams-mcp) | Teams meetings, transcripts, ad-hoc/1:1 calls, chats, channel messages, shared files via browser session tokens + local SQLite archive. | `teams-browser-mcp` (console script) |
| `entra-mcp` | [msetsma/entra-mcp](https://github.com/msetsma/entra-mcp) | Entra ID org structure + access analysis: people search, group membership, reporting trees, manager lookup, membership comparison. Azure CLI delegated session, no app registration. Also ships the `entra` CLI. | `entra-mcp` (console script, uv tool) |

Tool-level detail lives in each repo's `README.md`. Don't duplicate it here.

## Skills, plugins, hooks, instructions & packages

Alongside servers, the catalog owns five more kinds:

- **Skills**: `catalog/skills/<name>.toml` (definition) plus
  `catalog/skills/<name>/SKILL.md` (content), symlinked into each listed client's
  skills directory. Never hand-edit a client's skills dir; edit the catalog.
- **Plugins**: `catalog/plugins/<name>.toml` (definition) plus
  `catalog/plugins/<name>.ts` (content), symlinked into each listed client's
  plugins directory. This is how opencode hooks are delivered (opencode plugins,
  not a JSON event map).
- **Hooks**: `catalog/hooks/<name>.toml`, merged additively into each listed
  client's hook map (Claude Code, Codex). See
  [`catalog/hooks/example-hook.toml`](catalog/hooks/) for the shape.
- **Instructions**: `catalog/instructions/<name>.toml` (definition) plus
  `<name>.md` (content), merged into a marked block in each listed client's
  global instruction file (`~/.config/opencode/AGENTS.md`, `~/.claude/CLAUDE.md`,
  `~/.codex/AGENTS.md`). Only the marked block is rewritten; notes you write
  outside it survive. Instructions are deliberately per-client: each tool's
  vocabulary and agent names differ.

- **Packages** (pi only): `catalog/packages/<name>.toml` with a `source`
  (`npm:` / `git:` / local path) and optional resource filters, merged into
  `~/.pi/agent/settings.json` → `packages`. Add packages here, not with
  `pi install`; sync runs `pi update --extensions` to download them.

The `python-clean` gate is the worked example: a Claude Code hook plus an
opencode plugin over `bin/clean-python`.

`catalog/clients.toml` declares a `[<client>.<kind>]` block for each kind a
client supports; a kind with no block is skipped for that client.

## Claude Code mods

Mods (hooks-module plugins: panes, bands, status line, toasts, tool-call
guards) live outside the catalog, each in its own repo under github.com/msetsma
(the sidebar is [msetsma/claude-sidebar](https://github.com/msetsma/claude-sidebar)).
`mods/` holds only the `dotfiles-mods` marketplace, which lists each mod by
GitHub source, the same way third-party plugins come in. To add one, create the
repo, add a `{"source": "github", "repo": "msetsma/<repo>"}` entry to
`mods/.claude-plugin/marketplace.json`, run
`claude plugin marketplace update dotfiles-mods`, then
`claude plugin install <name>@dotfiles-mods`. To work on a mod, clone it and run
`claude --plugin-dir <clone>`. Details in [`mods/README.md`](mods/README.md).

## Registering a server with the local agents

> The snippets below are a fallback for a machine without this repo; prefer the
> catalog.

Configured clients: **opencode**, **Claude Code**, **Claude Desktop**, **Codex**
(MCP), plus **pi** (packages only so far).

Always use an **absolute path to `uv`** (`/opt/homebrew/bin/uv`) in GUI apps;
they don't inherit your shell `PATH`. Replace the command/args with the new
server's entry point.

Launch commands:

```sh
# sharepoint-mcp
/opt/homebrew/bin/uv run --directory /Users/msetsma/.dotfiles-agents/vendor/sharepoint-mcp server.py

# teams-browser
/opt/homebrew/bin/uv run --directory /Users/msetsma/.dotfiles-agents/vendor/teams-mcp --extra mcp teams-browser-mcp

# notes
/opt/homebrew/bin/uv run --directory /Users/msetsma/.dotfiles-agents/vendor/notes-mcp notes-mcp

# entra-mcp
/opt/homebrew/bin/uv run --directory /Users/msetsma/.dotfiles-agents/vendor/entra-mcp --extra mcp entra-mcp
```

Each path is a clone of the server's repo; `make agent-update` creates it.

`entra-mcp` also ships a daily-use `entra` CLI, so it's a uv tool install:

```sh
uv tool install --editable ~/.dotfiles-agents/vendor/entra-mcp --with fastmcp   # provides `entra` and `entra-mcp`
```

### opencode

Config: `~/.config/opencode/opencode.jsonc`, under `mcp.servers` (V2 does **not**
put server names directly under `mcp`). Use `disabled: true`, not `enabled`.

```sh
opencode mcp add sharepoint-mcp --global -- \
  /opt/homebrew/bin/uv run --directory /Users/msetsma/.dotfiles-agents/vendor/sharepoint-mcp server.py
opencode mcp list
```

```jsonc
"sharepoint-mcp": {
  "type": "local",
  "command": ["/opt/homebrew/bin/uv", "run", "--directory", "/Users/msetsma/.dotfiles-agents/vendor/sharepoint-mcp", "server.py"]
}
```

### Claude Code

User-scope servers land in `~/.claude.json`.

```sh
claude mcp add --scope user sharepoint-mcp -- \
  /opt/homebrew/bin/uv run --directory /Users/msetsma/.dotfiles-agents/vendor/sharepoint-mcp server.py
claude mcp list
```

### Claude Desktop

Config: `~/Library/Application Support/Claude/claude_desktop_config.json`.
Include `env.PATH`: the app launches without your shell environment.

```json
"mcpServers": {
  "sharepoint-mcp": {
    "command": "/opt/homebrew/bin/uv",
    "args": ["run", "--directory", "/Users/msetsma/.dotfiles-agents/vendor/sharepoint-mcp", "server.py"],
    "env": { "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" }
  }
}
```

### Codex

Config: `~/.codex/config.toml`.

```toml
[mcp_servers.sharepoint-mcp]
command = "/opt/homebrew/bin/uv"
args = ["run", "--directory", "/Users/msetsma/.dotfiles-agents/vendor/sharepoint-mcp", "server.py"]
```

### teams-browser has its own installer

```sh
uv run --directory ~/.dotfiles-agents/vendor/teams-mcp --extra mcp teams-browser mcp clients
uv run --directory ~/.dotfiles-agents/vendor/teams-mcp --extra mcp teams-browser mcp install claude-desktop
```

It supports `claude-desktop`, `claude-code`, `cursor`, `vscode`, `opencode`.
The `claude-code` / `vscode` / `opencode` targets write **project-local** files
in the current directory, so run it from the project you want, or use the manual
snippets above.

## Adding a new server: checklist

1. Create a repo `msetsma/<repo>` with `pyproject.toml`, `README.md`, entry point, tests.
2. Prove it offline (tests pass) in that repo.
3. Add `catalog/mcp/<name>.toml` with launch args pointing at
   `${AGENT_HOME}/vendor/<repo>` and a package block:
   `[package]`, `manager = "git"`, `repo = "msetsma/<repo>"`,
   `path = "${AGENT_HOME}/vendor/<repo>"`.
4. `make agent-update` to clone it into `vendor/<repo>/`.
5. `make agent-check`, then `make sync`, and confirm it connects
   (`opencode mcp list`, `claude mcp list`, or the client's MCP panel).
6. Add a row to the Inventory tables in `README.md` and above.
7. For anything with secrets or auth, document the one-time login step in the
   server's README.

## Path convention

Everything points at `~/.dotfiles-agents/...`.
