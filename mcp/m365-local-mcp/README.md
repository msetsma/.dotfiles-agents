# m365-local-mcp

Read-only MCP server for local Microsoft 365 data on macOS: Outlook meetings,
Mail search, and OneDrive-synced SharePoint files. Needs no Microsoft Graph
consent and no Full Disk Access.

## Tools

| Tool | What it does |
|---|---|
| `meetings_list` | Events in a date window |
| `calendars_list` | Calendar names and event counts |
| `mail_folders` | Mail accounts and their mailboxes |
| `mail_search` | Search by `subject`, `sender`, or `body` |
| `mail_get` | Headers and body for one message |
| `sp_roots` | Synced SharePoint library roots |
| `sp_find` | Filename search — downloads nothing |
| `sp_read` | Text of one file — downloads it if needed |

All tools are read-only; nothing sends mail, creates events, or writes files.

## Run

```sh
uv run --directory ~/.dotfiles-agents/mcp/m365-local-mcp server.py
uv run --directory ~/.dotfiles-agents/mcp/m365-local-mcp test_server.py  # offline self-check
```

Requires Mail.app and Calendar.app (AppleScript) and at least one synced
SharePoint library. `M365_MAIL_ACCOUNT` overrides the Mail.app account name.
