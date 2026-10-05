# entra-mcp

Read-only Entra ID org-structure and access analysis, exposed as an `entra` CLI
and an `entra-mcp` MCP server. Both share one core package (`entra_tool`).

## Auth

Uses your existing `az login` session via `az rest`: no app registration, no
client secret, no certificate.

```sh
az login
```

## Run

```sh
uv tool install --editable ~/.dotfiles-agents/mcp/entra-mcp --with fastmcp
uv run --directory ~/.dotfiles-agents/mcp/entra-mcp --extra mcp entra-mcp
```

## MCP tools

`search_users`, `search_groups`, `list_users`, `list_groups`, `get_user`,
`get_user_groups`, `get_user_owned_groups`, `get_group`, `get_group_members`,
`group_audit`, `why_user_in_group`, `get_manager`, `get_reports`,
`compare_users`, `compare_groups`, `check_auth`. All read-only; identifiers
accept a UPN/email or an object id.

## CLI

```sh
entra user <upn>; entra group <name>; entra reports <upn>
entra compare-users <a> <b>; entra compare-groups <a> <b>
entra dept; entra cache status|refresh
```

Listings cache for 24 hours in `~/.cache/entra_membership/`; refresh with
`entra cache refresh`.
