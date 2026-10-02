# MCP servers & agent configs

One repo for every MCP server this machine uses — the **custom** ones that live
here, and the **third-party** ones pulled in from `npx`, uv tools, and desktop
apps. Everything is declared once in [`catalog/`](catalog/) and pushed into each
agent by one command.

Modeled on [`~/.dotfiles`](../.dotfiles): **dotter** deploys files, **cargo-make**
runs tasks, per-host packages live in `.dotter/`.

---

## Layout

```
.mcp/
├── catalog/                 # single source of truth
│   ├── servers/*.toml       #   one file per MCP server
│   ├── clients.toml         #   where each agent keeps its MCP config
│   ├── paths.toml           #   machine-specific command paths + ${VARS}
│   └── retired.toml         #   servers to scrub from every client
├── bin/
│   ├── mcp-sync             #   wrapper (uv + tomlkit)
│   ├── mcp_sync.py          #   the sync engine
│   ├── mcp-update           #   update checker/applier
│   └── mcp_update.py
├── generated/               # rendered pure-MCP configs (gitignored)
├── .dotter/                 # dotter packages + pre-deploy hook
├── m365-local-mcp/          # custom servers (own projects)
├── teams-mcp/
├── databricks-mcp/          # uv project that pulls ai-dev-kit's MCP from git
└── entra-mcp/               # (its own git repo)
```

## How it works

```
catalog/servers/*.toml ─┐
catalog/clients.toml  ──┤  bin/mcp-sync
catalog/paths.toml    ──┤
catalog/retired.toml  ──┘
        │
        ├── merge  ──▶ ~/.claude.json                    Claude Code      (shared file)
        ├── merge  ──▶ .../Claude/claude_desktop_config.json   Claude Desktop (shared file)
        ├── merge  ──▶ ~/.config/opencode/opencode.jsonc opencode         (shared file)
        ├── merge  ──▶ ~/.codex/config.toml              Codex            (shared file)
        └── render ──▶ generated/vscode.json ──dotter──▶ VS Code mcp.json (pure file)
```

* **Shared files** (they also hold state and non-MCP settings) are *merged*:
  only the MCP block is rewritten; everything else is left alone. Codex keeps
  its per-tool tables (`[mcp_servers.databricks.tools.*]`), and the servers
  Codex ships itself (`node_repl`, `computer-use`) are never touched.
* **Pure files** (config that is nothing but MCP) are rendered to `generated/`
  and symlinked into place by dotter, so the repo owns them outright.
* `.dotter/pre_deploy.sh` runs `bin/mcp-sync`, so a single `dotter` run keeps
  every agent in sync.

## Clients

| Client | File | Format | Handling |
|---|---|---|---|
| opencode | `~/.config/opencode/opencode.jsonc` | JSONC | merge |
| Claude Code | `~/.claude.json` → `mcpServers` | JSON | merge |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` | JSON | merge |
| Codex | `~/.codex/config.toml` → `[mcp_servers]` | TOML | merge |
| VS Code | `~/Library/Application Support/Code/User/mcp.json` | JSON | dotter symlink |

## Inventory

Full table of what this repo manages. `custom` = source lives in this repo;
`external` = pulled from elsewhere (npx / uv tool / app).

| Server | Origin | Launch | Clients |
|---|---|---|---|
| `m365-local` | custom | `uv run --directory ~/.mcp/m365-local-mcp server.py` | opencode, Claude Code, Claude Desktop |
| `teams-browser` | custom | `uv run --directory ~/.mcp/teams-mcp teams-browser-mcp` | opencode, Claude Code, Claude Desktop |
| `entra-mcp` | custom | `entra-mcp` (uv tool) | opencode, Claude Code, Claude Desktop, VS Code, Codex |
| `obscura` | external | `~/.local/bin/obscura mcp --stealth` | opencode, Claude Code, Claude Desktop |
| `apple-mail` | external | `apple-mail-mcp` (uv tool) | opencode |
| `databricks` | external | `uv run --project ~/.mcp/databricks-mcp databricks-mcp` | opencode, Claude Code, Claude Desktop, Codex |
| `azure` | external | `npx @azure/mcp@3.0.0-beta.29 server start` | opencode, Claude Code, Claude Desktop |
| `azure-devops` | external | `npx @azure-devops/mcp ${ADO_ORG}` | opencode, Claude Code, Claude Desktop |
| `github` | external | remote `api.githubcopilot.com/mcp/` | opencode, Claude Code |
| `iMCP` | external | `/Applications/iMCP.app/…/imcp-server` | opencode, Claude Code, Claude Desktop, VS Code, Codex |
| `playwright` | external | `npx @playwright/mcp@latest` | opencode |
| `deepwiki` | external | remote `mcp.deepwiki.com/sse` | VS Code |
| `context7` | external | `npx @upstash/context7-mcp@latest` | VS Code |

> Claude Code also has a **project-scoped** `playwright` under
> `~/docs/Analytics.wiki` that this repo does not manage (project config, not
> user config).

## Secrets

**No secrets live here.** `github` references `${GITHUB_TOKEN}`; supply it in
your environment or the agent's own config. `paths.toml` holds only command
paths and non-secret env (`EMAIL_MCP_READ_ONLY`, `DATABRICKS_CONFIG_PROFILE`).

Machine-specific, non-secret values (like your Azure DevOps org) live in the
untracked `catalog/local.toml` — copy `catalog/local.toml.example` and fill it in.

> Two live tokens were found inline in the old configs while building this:
> a Notion token (`ntn_…`) and a GitHub token (`gho_…`). The Notion server was
> retired and the GitHub token replaced by the env reference — **rotate both**.

## Usage

```sh
cargo make mcp-check   # dry-run: show what would change
cargo make mcp-sync    # merge the catalog into every agent (no dotter)
cargo make sync        # full sync: dotter (runs mcp-sync) + deploy pure files
```

Or drive dotter directly:

```sh
dotter -v                # runs the pre-deploy sync, then deploys
dotter -v --force        # first run only: replace the real VS Code mcp.json
```

The very first deploy needs `--force` because VS Code's `mcp.json` already
exists as a real file; dotter will refuse to replace it otherwise.

## Keeping up to date

Versions are **not** baked into the launch args. Each external server has a
`[package]` block and the args use a `{{package}}` placeholder that the sync
engine resolves at render time:

```toml
[launch]
command = "npx"
args    = ["-y", "{{package}}", "server", "start"]

[package]
manager = "npm"
name    = "@azure/mcp"
ref     = "latest"     # a dist-tag = float; a concrete version = pinned
```

Third-party npm servers float at `@latest`, so they need no maintenance at all —
`npx` pulls the newest on every launch. One command reports and applies
everything else:

```sh
cargo make mcp-outdated   # report only
cargo make mcp-update     # apply available updates, then re-sync if a pin changed
```

| manager | check | apply |
|---|---|---|
| `npm` | `npm view <pkg> dist-tags.<ref>` | bump the `ref` if pinned; floats left alone |
| `uv-tool` | `uv tool list --outdated` | `uv tool upgrade <tool>` |
| `uv-project` | latest git tag of the project's repo | rewrite the pinned tag in `databricks-mcp/pyproject.toml` + re-lock |
| `git` | behind-count vs upstream, or latest tag | `git pull --ff-only`; tag-pinned / no-upstream are reported as manual |
| `source` | runs from a working tree | nothing — always current |
| `manual` / `remote` | – | a binary/app or a hosted endpoint |

Example report:

```
SERVER         MANAGER  STATUS
apple-mail     uv-tool  1.7.0 -> 1.8.2
azure          npm      floating @latest (now 3.0.0-beta.49)
databricks     uv-project  v0.2.0 (up to date)
m365-local     source   local source (always current)
```

> `databricks` is a special case: Databricks' ai-dev-kit MCP isn't published to
> PyPI (`databricks-tools-core` has no release), so it's run from git via the
> local [`databricks-mcp/`](databricks-mcp/) uv project. Bumping its tag never
> rewrites an agent config. `~/.ai-dev-kit` may still exist for its *skills*.

## Adding or changing a server

1. Add/edit `catalog/servers/<name>.toml` (set `clients = [...]`).
2. To retire one, add its name to `catalog/retired.toml`.
3. `cargo make mcp-check`, then `cargo make sync`.
4. Add a row to the Inventory table above. **That's the rule.**
