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
    'notes_move_category',
}


def test_registers_all_thirteen_tools(vault):
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


def test_move_category_is_a_write_tool(vault):
    mcp = build_server(vault.config())
    tool = next(t for t in asyncio.run(mcp.list_tools()) if t.name == 'notes_move_category')
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False


def test_move_category_wrapper_accepts_new_name_and_scope(vault):
    ctx = build_context(vault.config())
    tools = build_tools(ctx)

    out = tools['notes_move_category']('11', '10 Projects/Renamed', new_name='Renamed', new_scope='New scope.')

    assert out['ok'] is True, out
    assert out['path'] == '10 Projects/11 Renamed'
    assert out['name'] == 'Renamed'
    assert out['hub'] == '10 Projects/11 Renamed/11 Renamed.md'


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
