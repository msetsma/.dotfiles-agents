# sharepoint-mcp

Read-only-by-default MCP server for SharePoint on macOS: OneDrive-synced
SharePoint libraries read from disk, and SharePoint lists via Microsoft Graph.
The file tools need no Microsoft Graph consent and no Full Disk Access; the list
tools (`sp_lists`, `sp_list_items`) use the Azure CLI's delegated Graph token
instead, so they require a signed-in `az`. List writes exist too, but are opt-in
(see below).

## Tools

| Tool | What it does |
|---|---|
| `sp_roots` | Synced SharePoint library roots |
| `sp_find` | Filename search; downloads nothing |
| `sp_read` | Text of one file; downloads it if needed |
| `sp_lists` | Lists and libraries on a SharePoint site (Graph) |
| `sp_list_items` | Rows with column values from a list (Graph) |

`sp_lists` and `sp_list_items` borrow the `az` CLI's delegated Graph token, so
run `az login` once before using them (no app registration needed).

## Writes (opt-in)

List writes are off unless the server is launched with `SHAREPOINT_ENABLE_WRITES`
set to a truthy value (`1`, `true`, `yes`, `on`). Without it, the three write
tools are not registered and the server is read-only.

```sh
SHAREPOINT_ENABLE_WRITES=1 uv run --directory ~/.dotfiles-agents/mcp/sharepoint-mcp server.py
```

| Tool | What it does |
|---|---|
| `sp_list_item_create` | Create an item from a `fields` dict |
| `sp_list_item_update` | Change only the given fields on an item |
| `sp_list_item_delete` | Delete an item (moves it to the site recycle bin) |

`fields` is a dict of column values, e.g. `{"project_status": "In Progress"}`.
These wire out with `read_only_hint=False`; delete also sets
`destructive_hint=True` so clients prompt.

## Run

```sh
uv run --directory ~/.dotfiles-agents/mcp/sharepoint-mcp server.py
uv run --directory ~/.dotfiles-agents/mcp/sharepoint-mcp test_server.py  # offline self-check
```

Requires at least one synced SharePoint library.
