# Agent configs: MCP servers, skills, plugins, hooks & instructions

[![License: Apache-2.0](https://img.shields.io/github/license/msetsma/.dotfiles-agents?style=flat-square)](https://github.com/msetsma/.dotfiles-agents/blob/main/LICENSE) [![Last commit](https://img.shields.io/github/last-commit/msetsma/.dotfiles-agents?style=flat-square)](https://github.com/msetsma/.dotfiles-agents/commits/main)

One repo for everything this machine's coding agents load: MCP servers (custom
ones that live here, plus third-party ones from `npx`, uv tools, and desktop
apps), skills, plugins, hooks, and per-tool instructions. Every resource is
declared once in [`catalog/`](catalog/) and pushed into each agent by one
command.

Modeled on [`~/.dotfiles`](../.dotfiles): **cargo-make** runs the sync and update
tasks; the catalog is the source of truth.

---

## Layout

```
.dotfiles-agents/
├── catalog/                # single source of truth — one file per resource
│   ├── mcp/*.toml          # MCP servers (custom + third-party)
│   ├── skills/<name>.toml  # skills (content in <name>/SKILL.md)
│   ├── plugins/<name>.toml # opencode plugins (content in <name>.ts)
│   ├── hooks/<name>.toml   # Claude Code / Codex event hooks
│   ├── instructions/*.toml # per-tool prompts (content in <name>.md)
│   ├── clients.toml        # where each agent keeps each kind
│   ├── paths.toml          # machine paths + ${VARS}
│   └── retired.toml        # resources to scrub from every client
├── bin/                    # agent-sync, agent-update, clean-python
└── mcp/                    # MCP server projects
    ├── m365-local-mcp/     # custom
    ├── teams-mcp/          # custom
    ├── databricks-mcp/     # pins ai-dev-kit's MCP from git
    └── entra-mcp/          # custom; also ships the `entra` CLI
```

## How it works

```mermaid
flowchart LR
  C[catalog/] --> S[bin/agent-sync]
  S -- merge --> M[MCP + hooks in shared client configs]
  S -- symlink --> P[skills/ and plugins/ dirs]
  S -- block --> I[global AGENTS.md]
```

`catalog/` is the single source of truth. `bin/agent-sync` renders it into each
client with one of three strategies:

| Strategy    | Used for           | Behaviour                                                          |
|-------------|--------------------|--------------------------------------------------------------------|
| **merge**   | MCP servers, hooks | rewrite only the managed subtree; leave the rest of the file alone |
| **symlink** | skills, plugins    | link one item per file/dir into the client's directory             |
| **block**   | instructions       | replace one marked region in the client's global file              |

A client declares one `[<client>.<kind>]` block per kind it supports; a kind with
no block is skipped.

## Clients

| Client         | Kind         | Location                                                          | Handling |
|----------------|--------------|-------------------------------------------------------------------|----------|
| opencode       | mcp          | `~/.config/opencode/opencode.jsonc`                               | merge    |
| opencode       | plugins      | `~/.config/opencode/plugins/`                                     | symlink  |
| opencode       | instructions | `~/.config/opencode/AGENTS.md`                                    | block    |
| Claude Code    | mcp          | `~/.claude.json` → `mcpServers`                                   | merge    |
| Claude Code    | skills       | `~/.claude/skills/`                                               | symlink  |
| Claude Code    | hooks        | `~/.claude/settings.json` → `hooks`                               | merge    |
| Claude Desktop | mcp          | `~/Library/Application Support/Claude/claude_desktop_config.json` | merge    |
| Codex          | mcp          | `~/.codex/config.toml` → `[mcp_servers]`                          | merge    |
| Codex          | hooks        | `~/.codex/hooks.json` → `hooks`                                   | merge    |

Notes:

- Shared files also hold non-resource settings; only the managed subtree is touched.
- Codex's own per-tool tables and built-in servers (`node_repl`, `computer-use`) are never touched.
- Skills link once, for Claude Code; opencode reads `~/.claude/skills/` (its Claude-compatible path) and inherits them. Add `[opencode.skills]` for a separate copy.
- Removing a `[<client>.skills]` block stops managing that directory; existing links are left behind.

## Resource kinds

```
catalog/skills/<name>.toml       # + <name>/SKILL.md
catalog/plugins/<name>.toml      # + <name>.ts
catalog/hooks/<name>.toml        # event + matcher + command
catalog/instructions/<name>.toml # + <name>.md
```

- **Skills / plugins** — symlinked per item; `agent-sync` only touches links that point back into this repo. opencode auto-discovers direct `.ts` files, so plugins need no `opencode.jsonc` entry.
- **Hooks** — merged additively: the previously managed group is replaced, unmanaged hooks in the same file are left alone. Retire one by adding its exact command to `hook_commands` in `catalog/retired.toml`.
- **Instructions** — per-client (tool vocabularies differ), wrapped in a `<!-- dotfiles-agents:begin/end -->` block; text outside the markers survives.

`catalog/skills/github-glowup/` is a real skill (audits a GitHub repo for
quality-of-life improvements); `catalog/skills/example-skill/` is the placeholder
that proves the pipeline, and `catalog/hooks/example-hook.toml` ships inert
(`clients = []`) as a hook template.

## Inventory

Full table of what this repo manages. `custom` = source lives in this repo;
`external` = pulled from elsewhere (npx / uv tool / app).

> [!NOTE]
> Claude Code also has a **project-scoped** `playwright` under
> `~/docs/Analytics.wiki` that this repo does not manage (project config, not
> user config).

<details>
<summary>Show the table</summary>

| Server          | Origin   | Launch                                                                  | Clients                                      |
|-----------------|----------|-------------------------------------------------------------------------|----------------------------------------------|
| `m365-local`    | custom   | `uv run --directory ~/.dotfiles-agents/mcp/m365-local-mcp server.py`    | opencode, Claude Code, Claude Desktop        |
| `teams-browser` | custom   | `uv run --directory ~/.dotfiles-agents/mcp/teams-mcp teams-browser-mcp` | opencode, Claude Code, Claude Desktop        |
| `entra-mcp`     | custom   | `entra-mcp` (uv tool)                                                   | opencode, Claude Code, Claude Desktop, Codex |
| `obscura`       | external | `~/.local/bin/obscura mcp --stealth`                                    | opencode, Claude Code, Claude Desktop        |
| `apple-mail`    | external | `apple-mail-mcp` (uv tool)                                              | opencode                                     |
| `databricks`    | external | `uv run --project ~/.dotfiles-agents/mcp/databricks-mcp databricks-mcp` | opencode, Claude Code, Claude Desktop, Codex |
| `azure`         | external | `npx @azure/mcp@3.0.0-beta.29 server start`                             | opencode, Claude Code, Claude Desktop        |
| `azure-devops`  | external | `npx @azure-devops/mcp ${ADO_ORG}`                                      | opencode, Claude Code, Claude Desktop        |
| `github`        | external | remote `api.githubcopilot.com/mcp/`                                     | opencode, Claude Code                        |
| `iMCP`          | external | `/Applications/iMCP.app/…/imcp-server`                                  | opencode, Claude Code, Claude Desktop, Codex |
| `playwright`    | external | `npx @playwright/mcp@latest`                                            | opencode                                     |

</details>

## Secrets

**No secrets live here.** `github` references `${GITHUB_TOKEN}` — supply it in
your environment or the agent's config. `paths.toml` holds command paths and
non-secret env only. Machine-specific, non-secret values go in the untracked
`catalog/local.toml` (copy `catalog/local.toml.example`).

> [!WARNING]
> Two live tokens were found inline in the old configs while building this — a
> Notion token (`ntn_…`) and a GitHub token (`gho_…`). The Notion server was
> retired and the GitHub token replaced by the env reference. **Rotate both.**

## Usage

```sh
cargo make agent-check   # dry-run: show what would change
cargo make sync          # render + merge the catalog into every agent
```

## Keeping up to date

External servers carry a `[package]` block; launch args use a `{{package}}`
placeholder resolved at render time, so npm servers float at `@latest` and need
no maintenance.

```sh
cargo make agent-outdated   # report only
cargo make agent-update     # apply updates, then re-sync if a pin changed
```

| manager             | how it updates                                                    |
|---------------------|-------------------------------------------------------------------|
| `npm`               | bump the pinned `ref`; floating tags left alone                   |
| `uv-tool`           | `uv tool upgrade <tool>`                                          |
| `uv-project`        | bump the pinned git tag + re-lock                                 |
| `git`               | `git pull --ff-only`; tag-pinned / no-upstream reported as manual |
| `source`            | always current (runs from a working tree)                         |
| `manual` / `remote` | a binary/app or hosted endpoint — nothing to do                   |

`databricks` runs from the local [`mcp/databricks-mcp/`](mcp/databricks-mcp/) uv
project (ai-dev-kit's MCP isn't on PyPI); bumping its pin never rewrites an agent
config.

## Adding or changing a resource

1. **Server** — add/edit `catalog/mcp/<name>.toml` (set `clients = [...]`).
2. **Skill** — add `catalog/skills/<name>.toml` + `catalog/skills/<name>/SKILL.md`.
3. **Hook** — add `catalog/hooks/<name>.toml`.
4. **Retire** — add the name (or, for hooks, the command) to `catalog/retired.toml`.
5. `cargo make agent-check`, then `cargo make sync`.
6. New server? Add a row to the Inventory table above. **That's the rule.**
