"""MCP server entry point: registers the 16 ``notes_*`` tools over stdio.

The vault is the single source of truth for behavior (see the vault ``AGENTS.md``);
``CONTRACT.md`` fixes the interfaces. Tool parameters are published snake_case
(``category_id``, ``note_type``, ``from_line``, ``max_lines``,
``target_category_id``, ``new_title``); the spec's camelCase spellings map onto
them. This is a deliberate deviation: the repo's ruff gate forbids camelCase
arguments and the ``type`` builtin.

stdout is the MCP protocol channel, so diagnostics go to stderr only.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from notes_mcp import __version__
from notes_mcp.config import Config
from notes_mcp.context import ToolContext, build_context
from notes_mcp.errors import NotesError
from notes_mcp.tools import (
    capture as capture_tools,
    lint as lint_tools,
    read as read_tools,
    structure as structure_tools,
    write as write_tools,
)


READ_TOOLS = frozenset(
    {
        'notes_index',
        'notes_search',
        'notes_read',
        'notes_list',
        'notes_recent',
        'notes_status',
        'notes_triage',
        'notes_lint',
    }
)

INSTRUCTIONS = (
    'Read and write notes in the personal Obsidian vault (Johnny-Decimal x PARA). '
    'Call notes_index to load allowed categories; never invent a category or area. '
    'Call notes_search before creating a note; update an existing note instead of '
    "duplicating. Read a category's hub note to confirm scope. Every note needs valid "
    'frontmatter (the server maintains the qmd.metadata mirror). Never create or '
    'rename categories, edit 00 Meta/00.00 Index.md, delete notes, or touch '
    'AGENTS.md/.obsidian/.git/.githooks. If a tool returns a CONFLICT error, stop and '
    'report it. If a tool returns pending_push, work may continue; notes_sync retries.'
)

Tool = Callable[..., dict[str, Any]]


def _call(fn: Callable[..., dict[str, Any]], ctx: ToolContext, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Invoke a tool, converting NotesError into the failure envelope."""
    try:
        return fn(ctx, *args, **kwargs)
    except NotesError as exc:
        return exc.to_dict()


def _read_tools(ctx: ToolContext) -> dict[str, Tool]:
    def notes_index() -> dict[str, Any]:
        """List the vault's categories from 00 Meta/00.00 Index.md."""
        return _call(read_tools.notes_index, ctx)

    def notes_search(
        query: str,
        area: str | None = None,
        category_id: str | None = None,
        note_type: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        domain: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        updated_after: str | None = None,
        updated_before: str | None = None,
        limit: int = 10,
        rerank: bool = True,
    ) -> dict[str, Any]:
        """Search the vault, filtered by area/category/type/status/tags/domain/dates."""
        return _call(
            read_tools.notes_search,
            ctx,
            query,
            area=area,
            category_id=category_id,
            type_=note_type,
            status=status,
            tags=tags,
            domain=domain,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            updated_before=updated_before,
            limit=limit,
            rerank=rerank,
        )

    def notes_read(path: str, from_line: int | None = None, max_lines: int | None = None) -> dict[str, Any]:
        """Read a note: parsed frontmatter plus body (optionally a line range)."""
        return _call(read_tools.notes_read, ctx, path, from_line, max_lines)

    def notes_list(path: str | None = None) -> dict[str, Any]:
        """List one level of folders and files under the vault (or a path)."""
        return _call(read_tools.notes_list, ctx, path)

    def notes_recent(since: str = '7d', author: str = 'any') -> dict[str, Any]:
        """Recently changed notes from git history, filtered by author."""
        return _call(read_tools.notes_recent, ctx, since, author)

    def notes_status() -> dict[str, Any]:
        """Vault path, branch, ahead/behind, last pull, and qmd index health."""
        return _call(read_tools.notes_status, ctx)

    def notes_triage(path: str | None = None, limit: int | None = None) -> dict[str, Any]:
        """Suggest a category for each inbox note (read-only; no commit)."""
        return _call(capture_tools.notes_triage, ctx, path, limit)

    def notes_lint() -> dict[str, Any]:
        """Report broken links, bad frontmatter, collisions, hubs, drift, secrets."""
        return _call(lint_tools.notes_lint, ctx)

    return {
        'notes_index': notes_index,
        'notes_search': notes_search,
        'notes_read': notes_read,
        'notes_list': notes_list,
        'notes_recent': notes_recent,
        'notes_status': notes_status,
        'notes_triage': notes_triage,
        'notes_lint': notes_lint,
    }


def _write_tools(ctx: ToolContext) -> dict[str, Tool]:
    def notes_create(
        category_id: str,
        title: str,
        note_type: str,
        body: str,
        tags: list[str] | None = None,
        related: list[str] | None = None,
        source: list[str] | None = None,
        status: str = 'active',
    ) -> dict[str, Any]:
        """Create a note in a validated category and commit it."""
        return _call(write_tools.notes_create, ctx, category_id, title, note_type, body, tags, related, source, status)

    def notes_update(
        path: str,
        body: str | None = None,
        edits: list[dict[str, str]] | None = None,
        frontmatter: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update a note's body, apply find/replace edits, or merge frontmatter."""
        return _call(write_tools.notes_update, ctx, path, body, edits, frontmatter)

    def notes_append(
        path: str | None = None, daily: bool = False, heading: str | None = None, text: str = ''
    ) -> dict[str, Any]:
        """Append text to a note (or today's daily), optionally under a heading."""
        return _call(write_tools.notes_append, ctx, path, daily, heading, text)

    def notes_capture(text: str, title: str | None = None, tags: list[str] | None = None) -> dict[str, Any]:
        """Create one inbox note (or append to the daily when there is no Inbox)."""
        return _call(capture_tools.notes_capture, ctx, text, title, tags)

    return {
        'notes_create': notes_create,
        'notes_update': notes_update,
        'notes_append': notes_append,
        'notes_capture': notes_capture,
    }


def _structure_tools(ctx: ToolContext) -> dict[str, Tool]:
    def notes_move(path: str, target_category_id: str) -> dict[str, Any]:
        """Move a note to another category (git mv), filename unchanged."""
        return _call(structure_tools.notes_move, ctx, path, target_category_id)

    def notes_rename(path: str, new_title: str) -> dict[str, Any]:
        """Rename a note and rewrite [[wikilinks]] vault-wide in one commit."""
        return _call(structure_tools.notes_rename, ctx, path, new_title)

    def notes_sync() -> dict[str, Any]:
        """Pull the vault (autostash) and report status; no commit."""
        return _call(structure_tools.notes_sync, ctx)

    def notes_move_category(
        category_id: str, new_path: str, new_name: str | None = None, new_scope: str | None = None
    ) -> dict[str, Any]:
        """Move a whole category folder, updating the Index, hub, and its links."""
        return _call(structure_tools.notes_move_category, ctx, category_id, new_path, new_name, new_scope)

    return {
        'notes_move': notes_move,
        'notes_rename': notes_rename,
        'notes_sync': notes_sync,
        'notes_move_category': notes_move_category,
    }


def build_tools(ctx: ToolContext) -> dict[str, Tool]:
    """Create the 16 tool callables bound to ``ctx`` (also used by tests)."""
    return {**_read_tools(ctx), **_write_tools(ctx), **_structure_tools(ctx)}


def build_server(config: Config | None = None) -> Any:
    """Build the MCPServer with all 16 tools registered."""
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations

    read_ann = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
    write_ann = ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
    )

    mcp = MCPServer(name='notes', version=__version__, instructions=INSTRUCTIONS)
    for name, fn in build_tools(build_context(config)).items():
        mcp.tool(name=name, annotations=read_ann if name in READ_TOOLS else write_ann)(fn)
    return mcp


def main() -> None:
    try:
        server = build_server()
    except NotesError as exc:
        sys.stderr.write(f'notes-mcp: {exc.code}: {exc.message} ({exc.hint})\n')
        raise SystemExit(2) from exc
    server.run()


if __name__ == '__main__':
    main()
