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
