# teams-browser

Read-only access to Microsoft Teams meetings, transcripts, calls, chats, channel
messages, and shared files, using the tokens your browser session already holds.
No Graph app registration, no admin consent, no write scopes.

## Run

```sh
uv sync --extra mcp
uv run playwright install chromium
uv run teams-browser login    # one-time interactive sign-in
uv run teams-browser doctor   # verify session and endpoints
uv run teams-browser-mcp      # stdio MCP server
```

## CLI

`login`, `doctor`, `meetings`, `transcript`, `calls`, `chats`, `messages`,
`files`, `attachments`, `sync`, `search`, `digest`, and `mcp install <client>`
to register the server with a client. `--json` emits `{ok, schema_version,
data}`.

## MCP tools

`list_meetings`, `get_meeting`, `get_transcript`, `get_transcript_analytics`,
`list_calls`, `get_meeting_attachments`, `list_chats`, `get_chat_messages`,
`search_archive`, `get_archive_digest`, `sync_archive`, `save_transcript`,
`session_status`, `start_login`. All are read-only except the two archive
writers.

`sync` mirrors meetings, transcripts, chats, and files into a local SQLite
archive at `~/.cache/teams-browser/archive.db`; `search` and `digest` then run
offline. Session tokens refresh automatically and expire about hourly; re-run
`login` if a refresh is blocked.
