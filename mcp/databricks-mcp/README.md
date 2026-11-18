# databricks-mcp

A self-contained uv project that runs Databricks' [ai-dev-kit] MCP server from
its git source, so the `databricks` entry in the catalog needs no prebuilt venv
and no upstream installer.

[ai-dev-kit]: https://github.com/databricks-solutions/ai-dev-kit

## Why

ai-dev-kit's own `install.sh` makes a shallow, single-tag clone with no branch
or upstream, which only the installer can update. This project instead pulls
the two monorepo packages (`databricks-mcp-server`, `databricks-tools-core`;
the latter has no PyPI release) from **one pinned git tag**, which
`make agent-update` bumps:

```toml
dependencies = [
  "databricks-mcp-server @ git+https://github.com/databricks-solutions/ai-dev-kit.git@v0.2.0#subdirectory=databricks-mcp-server",
  "databricks-tools-core @ git+https://github.com/databricks-solutions/ai-dev-kit.git@v0.2.0#subdirectory=databricks-tools-core",
]
```

`uv.lock` records the exact commit, and `mcp_databricks_launcher.py` provides the
`databricks-mcp` console script.

## Run

```sh
uv run --project ~/.dotfiles-agents/mcp/databricks-mcp databricks-mcp
```

## Update

```sh
make agent-outdated   # shows: databricks  uv-project  v0.2.0 -> v0.2.x
make agent-update     # rewrites the tag above, re-locks, then re-syncs every client
```

Because the catalog launches via `--project` (the tag lives here, not in the
agent configs), a version bump leaves the agent configs unchanged; the re-sync
after `make agent-update` finds nothing to rewrite for this server.
