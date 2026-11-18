"""Instructions: a managed markdown block inside each client's global
instruction file."""

from __future__ import annotations

import re
from pathlib import Path

from .common import CONFIG, report


__all__ = [
    'INSTR_BEGIN',
    'INSTR_END',
    'apply_instruction_block',
    'block_body',
    'block_names',
    'render_instruction_block',
    'sync_instructions',
]


# --------------------------------------------------------------------------- #
# instructions (managed markdown block in a shared file)
# --------------------------------------------------------------------------- #
# Clients read a single global instruction file (opencode: AGENTS.md, Claude
# Code: CLAUDE.md, Codex: AGENTS.md). There is no subtree to key into, so the
# catalog owns one delimited region and everything outside it is left alone.
INSTR_BEGIN = '<!-- dotfiles-agents:begin (managed by agent-sync; edit catalog/instructions/ instead) -->'
INSTR_END = '<!-- dotfiles-agents:end -->'
INSTR_REGION = re.compile(re.escape(INSTR_BEGIN) + r'.*?' + re.escape(INSTR_END), re.DOTALL)
INSTR_NAMES = re.compile(r'<!-- instructions: (.*?) -->', re.DOTALL)


def render_instruction_block(items: dict[str, str]) -> str:
    names = sorted(items)
    if not names:
        return ''
    body = '\n\n'.join(items[name].strip() for name in names)
    return f'{INSTR_BEGIN}\n<!-- instructions: {", ".join(names)} -->\n{body}\n{INSTR_END}'


def block_names(region: str) -> list[str]:
    match = INSTR_NAMES.search(region)
    if not match:
        return []
    return [name.strip() for name in match.group(1).split(',') if name.strip()]


def block_body(region: str) -> str:
    match = re.search(r'<!-- instructions: .*? -->\n(.*?)\n?' + re.escape(INSTR_END), region, re.DOTALL)
    return match.group(1) if match else ''


def _final_newline(text: str) -> str:
    """Trim trailing blank lines and end with exactly one newline (or ``''``)."""
    stripped = text.rstrip('\n')
    return f'{stripped}\n' if stripped else ''


def apply_instruction_block(document: str, block: str) -> str:
    """Swap the managed region for ``block``, preserving everything else.

    A file that is an exact unmanaged copy of the block content (the state just
    before the catalog first adopts it) is wrapped in markers instead of gaining
    a second copy.
    """
    match = INSTR_REGION.search(document)
    if match:
        head = document[: match.start()].rstrip('\n')
        tail = document[match.end() :].lstrip('\n')
        parts = [part for part in (head, block, tail) if part]
        return _final_newline('\n\n'.join(parts))
    if not block:
        return document
    if document.strip() and document.strip() == block_body(block).strip():
        return _final_newline(block)
    body = document.rstrip('\n')
    return _final_newline(f'{body}\n\n{block}') if body else _final_newline(block)


def _load_instruction_block(target: Path) -> tuple[str, str | None, list[str], str]:
    existing = target.read_text(encoding='utf-8') if target.exists() else ''
    match = INSTR_REGION.search(existing)
    region = match.group(0) if match else None
    return existing, region, block_names(region) if region else [], block_body(region) if region else ''


def sync_instructions(client: str, cli: dict, instructions: dict, retired: list) -> bool:
    target = Path(cli['path']).expanduser()
    desired = {
        name: item['text'] for name, item in instructions.items() if client in item['clients'] and name not in retired
    }
    existing, region, old_names, old_body = _load_instruction_block(target)
    new_block = render_instruction_block(desired)
    new_body = '\n\n'.join(desired[name].strip() for name in sorted(desired))

    added = sorted(set(desired) - set(old_names))
    removed = sorted(set(old_names) - set(desired))
    common = sorted(set(desired) & set(old_names))
    changed = common if old_body != new_body else []
    dirty = bool(added or removed or changed) or bool(new_block) != bool(region)
    report(client, target, added, changed, removed, kind='instructions')
    if not dirty or CONFIG.dry_run:
        return dirty

    updated = apply_instruction_block(existing, new_block)
    if updated:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(updated, encoding='utf-8')
    elif target.exists():
        target.unlink()
    return dirty
