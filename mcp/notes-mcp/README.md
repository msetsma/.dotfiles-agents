# notes-mcp

MCP server for the personal Obsidian **Johnny-Decimal x PARA** vault at
`~/notes`. It exposes indexed read/search over the vault and git-backed
create/update/move/rename operations, so agents never touch the vault through
shell/filesystem tools directly.

- Vault: `/Users/msetsma/notes` (git `main`, remote `origin`)
- Search: [qmd](https://github.com/tobi/qmd) collection `notes`. Prefers the warm
  `qmd mcp --http` daemon (`QMD_DAEMON_URL`); falls back to the `qmd` CLI.
- Git: every write is committed as `agent:<AGENT_NAME>` and pushed, with
  autostash pull, retry, and conflict notes in `00 Meta/03 Conflicts/`. Dirty
  target paths are snapshotted under the human identity first, so an agent
  overwrite never silently loses unsaved edits.

## Tools (13)

Read: `notes_index`, `notes_search`, `notes_read`, `notes_list`, `notes_recent`,
`notes_status`.
Write: `notes_create`, `notes_update`, `notes_append`.
Structure: `notes_move`, `notes_rename`, `notes_move_category`, `notes_sync`.

## Deviations from the Part 2 spec

The vault on disk is Johnny-Decimal x PARA (not the spec's generic PARA), and the
repo's ruff gate forbids camelCase arguments and the `type` builtin. So:

- Tool parameters are published **snake_case**: `category_id`, `note_type`
  (`type` in the spec), `from_line`, `max_lines`, `target_category_id`,
  `new_title`. Behavior is unchanged.
- The MCP maintains the `qmd.metadata` frontmatter mirror (the spec omitted it).
- Success payloads may carry `pending_push`, `needs_index_update`, and
  `pending_sync` (a transient/offline sync was skipped) from the vault
  `AGENTS.md`.
- Additional error codes: `CONFIG_ERROR`, `NOT_FOUND`, `PENDING_SYNC`.

## Hardening notes

- **Search uses the daemon.** `notes_search` calls the qmd MCP daemon over HTTP
  (warm models) and only shells out to the CLI when the daemon is down/disabled,
  so hybrid search no longer reloads ~2 GB of models per call.
- **Pathspec commits.** Writes never `git add -A`; the commit is limited to the
  exact paths the tool touched, so files staged by other tools (e.g. Obsidian
  Git) are never swallowed.
- **Offline-tolerant pulls.** A transient (network) pull failure proceeds and is
  recorded as `pending_sync`; only a real rebase/merge conflict stops the write.
- **Human snapshot.** Before mutating, dirty target paths are committed under the
  human identity (`HUMAN_NAME`/`HUMAN_EMAIL`, else ambient git config).
- **Category restructure.** `notes_move_category` relocates a whole category
  folder, rewrites its row in `00 Meta/00.00 Index.md`, and updates
  path-qualified links in one commit.

## Env

| Var | Default | Purpose |
|---|---|---|
| `VAULT_PATH` | (required) | Vault root |
| `AGENT_NAME` | `claude` | Commit identity `agent:<name>` |
| `GIT_REMOTE` | `origin` | Remote to push to |
| `QMD_COLLECTION` | `notes` | qmd collection |
| `QMD_DAEMON_URL` | `http://localhost:8181/mcp` | qmd daemon MCP endpoint; empty/`0`/`false` disables |
| `QMD_TIMEOUT` | `60` | Seconds per qmd daemon/CLI call |
| `QMD_EMBED_ON_WRITE` | `0` | Refresh vectors (`qmd embed`) after a write's reindex (rely on the hourly job by default) |
| `HUMAN_NAME` / `HUMAN_EMAIL` | ambient git | Identity for pre-write snapshot commits |
| `QMD_EMBED_MODEL` / `QMD_RERANK_MODEL` / `QMD_GENERATE_MODEL` | qmd defaults | Optional `file://` model overrides |

## Run / test

```sh
uv run --directory ~/.dotfiles-agents/mcp/notes-mcp --extra dev pytest
uv run --directory ~/.dotfiles-agents/mcp/notes-mcp --extra dev pytest -m integration   # real vault + daemon
uv run --directory ~/.dotfiles-agents/mcp/notes-mcp --extra dev notes-mcp
```

See [`CONTRACT.md`](CONTRACT.md) for the frozen interfaces.
