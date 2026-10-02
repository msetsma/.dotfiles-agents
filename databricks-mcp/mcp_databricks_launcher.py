"""Console entry point for the Databricks ai-dev-kit MCP server.

ai-dev-kit's server lives as two packages inside its monorepo and exposes no
console script, so upstream runs it via ``databricks-mcp-server/run_server.py``.
This thin launcher does the same thing from the installed packages, giving us a
stable command name (``databricks-mcp``) that agents can point at.
"""


def main() -> None:
    from databricks_mcp_server.server import mcp

    mcp.run(transport='stdio')


if __name__ == '__main__':
    main()
