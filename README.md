# Agent configs: MCP servers, skills & hooks

One repo for everything this machine's coding agents load — MCP servers (the
**custom** ones that live here, and **third-party** ones pulled in from `npx`,
uv tools, and desktop apps), **skills**, and **hooks**. Every resource is
declared once in [`catalog/`](catalog/) and pushed into each agent by one
command.

Modeled on [`~/.dotfiles`](../.dotfiles): **dotter** deploys files, **cargo-make**
runs tasks, per-host packages live in `.dotter/`.

---

## Layout

```
.agentdots/
├── catalog/                 # single source of truth
│   ├── servers/*.toml       #   one file per MCP server
│   ├── skills/<name>.toml   #   one file per skill (content in <name>/)
│   ├── hooks/<name>.toml    #   one file per event hook
│   ├── clients.toml         #   where each agent keeps each kind
│   ├── paths.toml           #   machine-specific command paths + ${VARS}
│   └── retired.toml         #   resources to scrub from every client
├── bin/
│   ├── agent-sync           #   wrapper (uv + tomlkit)
│   ├── agent_sync.py        #   the sync engine
│   ├── agent-update         #   update checker/applier
│   └── agent_update.py
├── generated/               # rendered pure-config files (gitignored)
├── .dotter/                 # dotter packages + pre-deploy hook
└── mcp/                     # MCP server projects
    ├── m365-local-mcp/      #   custom (own project)
    ├── teams-mcp/           #   custom (own project)
    ├── databricks-mcp/      #   uv project pinning ai-dev-kit's MCP from git
    └── entra-mcp/           #   custom (own project; also ships the `entra` CLI)
```

## How it works

```
catalog/servers/*.toml ─┐
catalog/skills/       ──┤
catalog/hooks/*.toml  ──┤  bin/agent-sync
catalog/clients.toml  ──┤
catalog/paths.toml    ──┤
catalog/retired.toml  ──┘
        │
        ├── merge   ──▶ ~/.claude.json                    Claude Code     (mcp)
        ├── merge   ──▶ ~/.claude/settings.json           Claude Code     (hooks)
        ├── merge   ──▶ ~/.codex/config.toml              Codex           (mcp)
        ├── merge   ──▶ ~/.codex/hooks.json               Codex           (hooks)
        ├── merge   ──▶ .../Claude/claude_desktop_config.json  Claude Desktop (mcp)
        ├── merge   ──▶ ~/.config/opencode/opencode.jsonc opencode        (mcp)
        ├── symlink ──▶ ~/.claude/skills/                 Claude Code     (skills)
        ├── symlink ──▶ ~/.config/opencode/skills/        opencode        (skills)
        └── render  ──▶ generated/vscode.json ──dotter──▶ VS Code mcp.json (pure)
```

* A client declares one `[<client>.<kind>]` block per kind it supports; a kind
  with no block is skipped for that client.
* **Shared files** (they also hold state and non-resource settings) are
  *merged*: only the managed subtree is rewritten; everything else is left
  alone. Codex keeps its per-tool tables (`[mcp_servers.databricks.tools.*]`),
  and the servers Codex ships itself (`node_repl`, `computer-use`) are never
  touched.
* **Pure files** (config that is nothing but one kind) are rendered to
  `generated/` and symlinked into place by dotter, so the repo owns them.
* `.dotter/pre_deploy.sh` runs `bin/agent-sync`, so a single `dotter` run keeps
  every agent in sync.

## Clients

| Client | Kind | Location | Handling |
|---|---|---|---|
| opencode | mcp | `~/.config/opencode/opencode.jsonc` | merge |
| Claude Code | mcp | `~/.claude.json` → `mcpServers` | merge |
| Claude Code | skills | `~/.claude/skills/` | symlink |
| Claude Code | hooks | `~/.claude/settings.json` → `hooks` | merge |
| Claude Desktop | mcp | `~/Library/Application Support/Claude/claude_desktop_config.json` | merge |
| Codex | mcp | `~/.codex/config.toml` → `[mcp_servers]` | merge |
| Codex | hooks | `~/.codex/hooks.json` → `hooks` | merge |
| VS Code | mcp | `~/Library/Application Support/Code/User/mcp.json` | dotter symlink |

## Skills

A skill is a folder (`SKILL.md` + any assets) with a sibling TOML that says who
gets it:

```
catalog/skills/example-skill.toml     # clients = [...]
catalog/skills/example-skill/SKILL.md # content
```

`agent-sync` symlinks `catalog/skills/<name>/` into each listed client's skills
directory. It only touches symlinks that point back into this repo, so your own
skills are never disturbed. Drop a client from `clients`, or retire the skill in
`catalog/retired.toml`, and the link is cleaned up on the next sync.

Only Claude Code has a `[claude-code.skills]` block: opencode reads
`~/.claude/skills/` too (its documented Claude-compatible path), so it inherits
the same skills without a second copy. Add `[opencode.skills]` only if you want
skills in opencode's own directory.

> Removing a client's `[<client>.skills]` block stops managing that directory, so
> links already placed there are left behind — delete them by hand.

## Hooks

```
catalog/hooks/<name>.toml
```

Declares `event`, `matcher`, the shell `command`, and which `clients` receive it.
Claude Code and Codex use a near-identical hook shape, so one definition covers
both.

Hooks are **additive**: on each sync `agent-sync` removes the group it previously
wrote for a client and appends the current one, leaving any hook it doesn't
manage in the same file untouched. Retire a hook by adding its exact command to
`hook_commands` in `catalog/retired.toml`.

`catalog/hooks/example-hook.toml` ships inert (`clients = []`) — add a client to
enable it.

## Inventory

Full table of what this repo manages. `custom` = source lives in this repo;
`external` = pulled from elsewhere (npx / uv tool / app).

| Server | Origin | Launch | Clients |
|---|---|---|---|
| `m365-local` | custom | `uv run --directory ~/.agentdots/mcp/m365-local-mcp server.py` | opencode, Claude Code, Claude Desktop |
| `teams-browser` | custom | `uv run --directory ~/.agentdots/mcp/teams-mcp teams-browser-mcp` | opencode, Claude Code, Claude Desktop |
| `entra-mcp` | custom | `entra-mcp` (uv tool) | opencode, Claude Code, Claude Desktop, VS Code, Codex |
| `obscura` | external | `~/.local/bin/obscura mcp --stealth` | opencode, Claude Code, Claude Desktop |
| `apple-mail` | external | `apple-mail-mcp` (uv tool) | opencode |
| `databricks` | external | `uv run --project ~/.agentdots/mcp/databricks-mcp databricks-mcp` | opencode, Claude Code, Claude Desktop, Codex |
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
cargo make agent-check   # dry-run: show what would change
cargo make agent-sync    # merge the catalog into every agent (no dotter)
cargo make sync        # full sync: dotter (runs agent-sync) + deploy pure files
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
cargo make agent-outdated   # report only
cargo make agent-update     # apply available updates, then re-sync if a pin changed
```

| manager | check | apply |
|---|---|---|
| `npm` | `npm view <pkg> dist-tags.<ref>` | bump the `ref` if pinned; floats left alone |
| `uv-tool` | `uv tool list --outdated` | `uv tool upgrade <tool>` |
| `uv-project` | latest git tag of the project's repo | rewrite the pinned tag in `mcp/databricks-mcp/pyproject.toml` + re-lock |
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
> local [`databricks-mcp/`](mcp/databricks-mcp/) uv project. Bumping its tag never
> rewrites an agent config. `~/.ai-dev-kit` may still exist for its *skills*.

## Adding or changing a resource

1. **Server**: add/edit `catalog/servers/<name>.toml` (set `clients = [...]`).
2. **Skill**: add `catalog/skills/<name>.toml` + `catalog/skills/<name>/SKILL.md`.
3. **Hook**: add `catalog/hooks/<name>.toml`.
4. **Retire**: add the name (or, for hooks, the command) to `catalog/retired.toml`.
5. `cargo make agent-check`, then `cargo make sync`.
6. New server? Add a row to the Inventory table above. **That's the rule.**
