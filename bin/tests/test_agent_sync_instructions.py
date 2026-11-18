"""Offline tests for the instructions overlay (bin/agentsync/instructions.py).

Covers the pure managed-block helpers and a real ``sync_instructions`` run into
a throwaway file, so the merge is proven without touching any client config.

Run with: uv run --with pytest pytest bin/tests/test_agent_sync_instructions.py
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agentsync import common
from agentsync.instructions import (
    INSTR_BEGIN,
    INSTR_END,
    apply_instruction_block,
    block_body,
    block_names,
    render_instruction_block,
    sync_instructions,
)
from jsonc import strip_jsonc


@pytest.fixture(autouse=True)
def _live_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common.CONFIG, 'dry_run', False)


@pytest.fixture
def target(tmp_path: Path) -> Path:
    return tmp_path / 'AGENTS.md'


def catalog(text: str = '# Delegation\n\nFan out.\n') -> dict:
    return {
        'opencode-delegation': {
            'name': 'opencode-delegation',
            'description': 'test',
            'clients': ['opencode'],
            'text': text,
        }
    }


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #
def test_render_empty_is_empty() -> None:
    assert render_instruction_block({}) == ''


def test_render_has_markers_names_and_body() -> None:
    block = render_instruction_block({'b': 'bee', 'a': 'aye'})
    assert block.startswith(INSTR_BEGIN)
    assert block.endswith(INSTR_END)
    assert '<!-- instructions: a, b -->' in block
    assert block_names(block) == ['a', 'b']
    assert block_body(block).strip() == 'aye\n\nbee'


def test_apply_appends_to_existing_text() -> None:
    doc = '# My own notes\n'
    out = apply_instruction_block(doc, 'BLOCK')
    assert out == '# My own notes\n\nBLOCK\n'


def test_apply_replaces_region_and_preserves_outside() -> None:
    block = render_instruction_block({'a': 'new'})
    doc = f'# Above\n\n{INSTR_BEGIN}\nold\n{INSTR_END}\n\n# Below\n'
    out = apply_instruction_block(doc, block)
    assert out.startswith('# Above\n\n')
    assert out.endswith('# Below\n')
    assert 'old' not in out
    assert 'new' in out


def test_apply_wraps_exact_unmanaged_copy() -> None:
    # Migration case: the file is the same text, just not yet marked.
    text = '# Delegation\n\nFan out.\n'
    block = render_instruction_block({'opencode-delegation': text})
    out = apply_instruction_block(text, block)
    assert out == block + '\n'
    assert out.count('Fan out.') == 1


def test_apply_removes_block_and_keeps_surroundings() -> None:
    doc = f'# Above\n\n{INSTR_BEGIN}\nbody\n{INSTR_END}\n\n# Below\n'
    out = apply_instruction_block(doc, '')
    assert out == '# Above\n\n# Below\n'


def test_apply_removes_block_from_empty_surroundings() -> None:
    doc = f'{INSTR_BEGIN}\nbody\n{INSTR_END}\n'
    assert apply_instruction_block(doc, '') == ''


# --------------------------------------------------------------------------- #
# sync_instructions end to end
# --------------------------------------------------------------------------- #
def test_sync_adds_block(target: Path) -> None:
    cli = {'path': str(target)}
    assert sync_instructions('opencode', cli, catalog(), []) is True
    text = target.read_text()
    assert INSTR_BEGIN in text
    assert 'Fan out.' in text


def test_sync_is_idempotent(target: Path) -> None:
    cli = {'path': str(target)}
    sync_instructions('opencode', cli, catalog(), [])
    before = target.read_text()
    assert sync_instructions('opencode', cli, catalog(), []) is False
    assert target.read_text() == before


def test_sync_reports_edit_as_changed(target: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli = {'path': str(target)}
    sync_instructions('opencode', cli, catalog(), [])
    capsys.readouterr()
    assert sync_instructions('opencode', cli, catalog('# Delegation\n\nFan out HARD.\n'), []) is True
    out = capsys.readouterr().out
    assert '~ opencode-delegation' in out


def test_sync_skips_other_clients(target: Path) -> None:
    cli = {'path': str(target)}
    # catalog entry targets opencode; asking as codex must be a no-op.
    assert sync_instructions('codex', cli, catalog(), []) is False
    assert not target.exists()


def test_sync_removes_block_when_no_instructions(target: Path) -> None:
    cli = {'path': str(target)}
    sync_instructions('opencode', cli, catalog(), [])
    assert sync_instructions('opencode', cli, {}, []) is True
    assert not target.exists()


def test_sync_honours_retired(target: Path) -> None:
    cli = {'path': str(target)}
    assert sync_instructions('opencode', cli, catalog(), ['opencode-delegation']) is False
    assert not target.exists()


# --------------------------------------------------------------------------- #
# strip_jsonc (refactored; guard the string/comment split)
# --------------------------------------------------------------------------- #
def test_strip_jsonc_removes_comments_outside_strings() -> None:
    text = '{\n  // line\n  "url": "https://x", /* block */\n  "n": 1\n}'
    out = strip_jsonc(text)
    assert '// line' not in out
    assert '/* block */' not in out
    assert '"https://x"' in out
    assert '"n": 1' in out


def test_strip_jsonc_keeps_comment_markers_inside_strings() -> None:
    text = '{"a": "a//b", "b": "c/*d*/e"}'
    assert strip_jsonc(text) == text


def test_strip_jsonc_respects_escaped_quote() -> None:
    text = '{"a": "x\\"//y"}'
    assert strip_jsonc(text) == text
