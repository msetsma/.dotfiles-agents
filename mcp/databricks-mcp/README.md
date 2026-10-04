# databricks-mcp

A self-contained uv project that runs Databricks' [ai-dev-kit] MCP server from
its git source, so the `databricks` entry in the catalog needs no prebuilt venv
and no upstream installer.

[ai-dev-kit]: https://github.com/databricks-solutions/ai-dev-kit

## Why

The old setup used a shallow, single-tag clone at `~/.ai-dev-kit` plus its own
`.venv`, created by ai-dev-kit's `install.sh`. That clone had no branch or
upstream, so updates had to be done by rerunning the installer. Here the two
monorepo packages (`databricks-mcp-server`, `databricks-tools-core`;
the latter has no PyPI release) are pulled from **one pinned git tag**:

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
cargo make agent-outdated   # shows: databricks  uv-project  v0.2.0 -> v0.2.x
cargo make agent-update     # rewrites the tag above, re-locks, re-syncs
```

Because the catalog launches via `--project` (the tag lives here, not in the
agent configs), a version bump never rewrites any agent config.

> `~/.ai-dev-kit` may still be installed for its **skills**; it's just no longer
> the source of the `databricks` MCP server.
