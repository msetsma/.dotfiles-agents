# notes-mcp

MCP server for the personal Obsidian **Johnny-Decimal x PARA** vault at
`~/notes`. It exposes indexed read/search over the vault and git-backed
create/update/move/rename operations, so agents never touch the vault through
shell/filesystem tools directly.

- Vault: `/Users/msetsma/notes` (git `main`, remote `origin`)
- Search: [qmd](https://github.com/tobi/qmd) collection `notes` (BM25 + hybrid)
- Git: every write is committed as `agent:<AGENT_NAME>` and pushed, with
  autostash pull, retry, and conflict notes in `00 Meta/03 Conflicts/`

## Tools

Read: `notes_index`, `notes_search`, `notes_read`, `notes_list`, `notes_recent`,
`notes_status`.
Write: `notes_create`, `notes_update`, `notes_append`.
Structure: `notes_move`, `notes_rename`, `notes_sync`.

## Deviations from the Part 2 spec

The vault on disk is Johnny-Decimal x PARA (not the spec's generic PARA), and the
repo's ruff gate forbids camelCase arguments and the `type` builtin. So:

- Tool parameters are published **snake_case**: `category_id`, `note_type`
  (`type` in the spec), `from_line`, `max_lines`, `target_category_id`,
  `new_title`. Behavior is unchanged.
- The MCP maintains the `qmd.metadata` frontmatter mirror (the spec omitted it).
- Success payloads may carry `pending_push` and `needs_index_update` (from the
  vault `AGENTS.md`).
- `notes_sync` returns the qmd/lock/git status; `CONFIG_ERROR` and `NOT_FOUND`
  are additional error codes.

## Env

| Var | Default | Purpose |
|---|---|---|
| `VAULT_PATH` | (required) | Vault root |
| `AGENT_NAME` | `claude` | Commit identity `agent:<name>` |
| `GIT_REMOTE` | `origin` | Remote to push to |
| `QMD_COLLECTION` | `notes` | qmd collection |
| `QMD_EMBED_MODEL` / `QMD_RERANK_MODEL` / `QMD_GENERATE_MODEL` | qmd defaults | Optional `file://` model overrides |

## Run / test

```sh
uv run --directory ~/.dotfiles-agents/mcp/notes-mcp --extra dev pytest
uv run --directory ~/.dotfiles-agents/mcp/notes-mcp --extra dev notes-mcp
```

See [`CONTRACT.md`](CONTRACT.md) for the frozen interfaces.
