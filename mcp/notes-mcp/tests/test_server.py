"""Server registry and tool-wrapper tests (no network, no qmd)."""

from __future__ import annotations

import asyncio

from notes_mcp.context import build_context
from notes_mcp.server import READ_TOOLS, build_server, build_tools


EXPECTED_TOOLS = {
    'notes_index',
    'notes_search',
    'notes_read',
    'notes_list',
    'notes_recent',
    'notes_status',
    'notes_create',
    'notes_update',
    'notes_append',
    'notes_move',
    'notes_rename',
    'notes_sync',
}


def test_registers_all_twelve_tools(vault):
    mcp = build_server(vault.config())
    assert {tool.name for tool in asyncio.run(mcp.list_tools())} == EXPECTED_TOOLS


def test_read_tools_are_read_only(vault):
    mcp = build_server(vault.config())
    for tool in asyncio.run(mcp.list_tools()):
        assert tool.annotations is not None
        if tool.name in READ_TOOLS:
            assert tool.annotations.read_only_hint is True
        else:
            assert tool.annotations.read_only_hint is False
            assert tool.annotations.destructive_hint is False


def test_index_wrapper(vault):
    ctx = build_context(vault.config())
    out = build_tools(ctx)['notes_index']()
    assert out['ok'] is True
    assert {'11', '21'} <= {category['id'] for category in out['categories']}


def test_read_and_list_wrappers(vault):
    ctx = build_context(vault.config())
    tools = build_tools(ctx)

    listed = tools['notes_list']()
    assert listed['ok'] is True

    note = tools['notes_read']('10 Projects/11 Project A/11 Project A.md')
    assert note['ok'] is True
    assert note['frontmatter']['type'] == 'hub'


def test_missing_note_returns_envelope(vault):
    ctx = build_context(vault.config())
    out = build_tools(ctx)['notes_read']('nope.md')
    assert out['ok'] is False
    assert out['error']['code'] == 'NOT_FOUND'
