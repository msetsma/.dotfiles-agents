"""Offline tests for the instructions overlay in bin/agent_sync.py.

Covers the pure managed-block helpers and a real ``sync_instructions`` run into
a throwaway file, so the merge is proven without touching any client config.

Run with: uv run --with pytest pytest bin/tests/test_agent_sync_instructions.py
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


AGENT_SYNC = (Path(__file__).resolve().parents[1] / 'agent_sync.py').resolve()


def load_engine():
    spec = importlib.util.spec_from_file_location('agent_sync', AGENT_SYNC)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def engine():
    module = load_engine()
    module.CONFIG.dry_run = False
    return module


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
def test_render_empty_is_empty(engine) -> None:
    assert engine.render_instruction_block({}) == ''


def test_render_has_markers_names_and_body(engine) -> None:
    block = engine.render_instruction_block({'b': 'bee', 'a': 'aye'})
    assert block.startswith(engine.INSTR_BEGIN)
    assert block.endswith(engine.INSTR_END)
    assert '<!-- instructions: a, b -->' in block
    assert engine.block_names(block) == ['a', 'b']
    assert engine.block_body(block).strip() == 'aye\n\nbee'


def test_apply_appends_to_existing_text(engine) -> None:
    doc = '# My own notes\n'
    out = engine.apply_instruction_block(doc, 'BLOCK')
    assert out == '# My own notes\n\nBLOCK\n'


def test_apply_replaces_region_and_preserves_outside(engine) -> None:
    block = engine.render_instruction_block({'a': 'new'})
    doc = f'# Above\n\n{engine.INSTR_BEGIN}\nold\n{engine.INSTR_END}\n\n# Below\n'
    out = engine.apply_instruction_block(doc, block)
    assert out.startswith('# Above\n\n')
    assert out.endswith('# Below\n')
    assert 'old' not in out
    assert 'new' in out


def test_apply_wraps_exact_unmanaged_copy(engine) -> None:
    # Migration case: the file is the same text, just not yet marked.
    text = '# Delegation\n\nFan out.\n'
    block = engine.render_instruction_block({'opencode-delegation': text})
    out = engine.apply_instruction_block(text, block)
    assert out == block + '\n'
    assert out.count('Fan out.') == 1


def test_apply_removes_block_and_keeps_surroundings(engine) -> None:
    doc = f'# Above\n\n{engine.INSTR_BEGIN}\nbody\n{engine.INSTR_END}\n\n# Below\n'
    out = engine.apply_instruction_block(doc, '')
    assert out == '# Above\n\n# Below\n'


def test_apply_removes_block_from_empty_surroundings(engine) -> None:
    doc = f'{engine.INSTR_BEGIN}\nbody\n{engine.INSTR_END}\n'
    assert engine.apply_instruction_block(doc, '') == ''


# --------------------------------------------------------------------------- #
# sync_instructions end to end
# --------------------------------------------------------------------------- #
def test_sync_adds_block(engine, target: Path) -> None:
    cli = {'path': str(target)}
    assert engine.sync_instructions('opencode', cli, catalog(), []) is True
    text = target.read_text()
    assert engine.INSTR_BEGIN in text
    assert 'Fan out.' in text


def test_sync_is_idempotent(engine, target: Path) -> None:
    cli = {'path': str(target)}
    engine.sync_instructions('opencode', cli, catalog(), [])
    before = target.read_text()
    assert engine.sync_instructions('opencode', cli, catalog(), []) is False
    assert target.read_text() == before


def test_sync_reports_edit_as_changed(engine, target: Path, capsys) -> None:
    cli = {'path': str(target)}
    engine.sync_instructions('opencode', cli, catalog(), [])
    capsys.readouterr()
    assert engine.sync_instructions('opencode', cli, catalog('# Delegation\n\nFan out HARD.\n'), []) is True
    out = capsys.readouterr().out
    assert '~ opencode-delegation' in out


def test_sync_skips_other_clients(engine, target: Path) -> None:
    cli = {'path': str(target)}
    # catalog entry targets opencode; asking as codex must be a no-op.
    assert engine.sync_instructions('codex', cli, catalog(), []) is False
    assert not target.exists()


def test_sync_removes_block_when_no_instructions(engine, target: Path) -> None:
    cli = {'path': str(target)}
    engine.sync_instructions('opencode', cli, catalog(), [])
    assert engine.sync_instructions('opencode', cli, {}, []) is True
    assert not target.exists()


def test_sync_honours_retired(engine, target: Path) -> None:
    cli = {'path': str(target)}
    assert engine.sync_instructions('opencode', cli, catalog(), ['opencode-delegation']) is False
    assert not target.exists()


# --------------------------------------------------------------------------- #
# strip_jsonc (refactored; guard the string/comment split)
# --------------------------------------------------------------------------- #
def test_strip_jsonc_removes_comments_outside_strings(engine) -> None:
    text = '{\n  // line\n  "url": "https://x", /* block */\n  "n": 1\n}'
    out = engine.strip_jsonc(text)
    assert '// line' not in out
    assert '/* block */' not in out
    assert '"https://x"' in out
    assert '"n": 1' in out


def test_strip_jsonc_keeps_comment_markers_inside_strings(engine) -> None:
    text = '{"a": "a//b", "b": "c/*d*/e"}'
    assert engine.strip_jsonc(text) == text


def test_strip_jsonc_respects_escaped_quote(engine) -> None:
    text = '{"a": "x\\"//y"}'
    assert engine.strip_jsonc(text) == text
