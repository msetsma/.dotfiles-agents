"""Read-only vault lint: ``notes_lint`` (Phase 2).

Scans every markdown file in the vault (pruning git internals and the protected
``AGENTS.md``) and reports six kinds of problem, each a list capped at
:data:`_MAX_FINDINGS`:

- ``broken_links`` -- ``[[target]]`` links that resolve to no note (note tree
  only; links inside fenced code blocks are ignored).
- ``invalid_frontmatter`` -- files whose frontmatter fails to parse or validate
  (including ``00 Meta``; the raw Index file is not a note and is skipped).
- ``filename_collisions`` -- duplicate basenames in the note tree.
- ``missing_hubs`` -- Index categories (except ``00``) with no
  ``<dir basename>.md`` hub.
- ``index_drift`` -- Index rows whose Path is not a directory.
- ``secrets`` -- common secret shapes found in any note.

No commit is made. The function takes the shared :class:`ToolContext` and
returns a plain dict; a missing Index becomes the usual ``NotesError`` envelope.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from notes_mcp import frontmatter
from notes_mcp.errors import NotesError
from notes_mcp.index import INDEX_REL_PATH
from notes_mcp.layout import META_ID


if TYPE_CHECKING:
    from notes_mcp.context import ToolContext


_META_DIR = '00 Meta'
_PRUNE_DIRS = frozenset({'.git', '.obsidian', '.githooks'})
# Protected instruction file, not a note: prose wikilinks there are not vault links.
_NON_NOTE_FILES = frozenset({'agents.md'})
_MAX_FINDINGS = 100
_FENCES = ('```', '~~~')
_WIKILINK = re.compile(r'\[\[([^\[\]|#]+)')
_SECRET_PATTERNS = (
    ('aws_access_key', re.compile(r'AKIA[0-9A-Z]{16}')),
    ('github_token', re.compile(r'gh[pousr]_[A-Za-z0-9]{20,}')),
    ('private_key', re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
    ('openai_key', re.compile(r'sk-[A-Za-z0-9]{20,}')),
    ('assignment', re.compile(r'(?i)(api[_-]?key|token|secret)\s*[:=]\s*[\'"]?[A-Za-z0-9_\-]{16,}')),
)


def notes_lint(ctx: ToolContext) -> dict[str, Any]:
    """Report broken links, bad frontmatter, collisions, stale hubs, drift, secrets."""
    files = _load_files(ctx)
    note_files = [(rel, text) for rel, text in files if not _in_meta(rel)]
    note_paths = {rel for rel, _ in note_files}
    stems = {PurePosixPath(rel).stem for rel, _ in note_files}

    findings: dict[str, list[dict[str, Any]]] = {
        'broken_links': _broken_links(note_files, note_paths, stems),
        'invalid_frontmatter': _invalid_frontmatter(files),
        'filename_collisions': _filename_collisions(note_files),
        'missing_hubs': [],
        'index_drift': [],
        'secrets': _secrets(files),
    }
    try:
        categories = ctx.index.categories()
    except NotesError as exc:
        return exc.to_dict()

    findings['missing_hubs'] = _missing_hubs(ctx, categories)
    findings['index_drift'] = _index_drift(ctx, categories)
    return {
        'ok': True,
        'summary': {key: len(value) for key, value in findings.items()},
        'findings': findings,
        'commit': None,
        'pending_push': False,
        'needs_index_update': False,
    }


# --------------------------------------------------------------------------- #
# scanning
# --------------------------------------------------------------------------- #
def _load_files(ctx: ToolContext) -> list[tuple[str, str]]:
    vault = ctx.config.vault_path
    files: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [name for name in dirnames if name not in _PRUNE_DIRS]
        for name in sorted(filenames):
            if not name.endswith('.md') or name.casefold() in _NON_NOTE_FILES:
                continue
            path = Path(dirpath) / name
            try:
                text = path.read_text(encoding='utf-8')
            except OSError:
                continue
            files.append((path.relative_to(vault).as_posix(), text))
    return files


def _in_meta(rel: str) -> bool:
    return rel == _META_DIR or rel.startswith(_META_DIR + '/')


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #
def _broken_links(note_files: list[tuple[str, str]], note_paths: set[str], stems: set[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for rel, text in note_files:
        fenced = False
        for number, line in enumerate(text.split('\n'), start=1):
            if line.lstrip().startswith(_FENCES):
                fenced = not fenced
                continue
            if fenced:
                continue
            for match in _WIKILINK.finditer(line):
                target = match.group(1).strip()
                if target and not _resolves(target, note_paths, stems):
                    findings.append({'path': rel, 'target': target, 'line': number})
    return findings[:_MAX_FINDINGS]


def _resolves(target: str, note_paths: set[str], stems: set[str]) -> bool:
    if '/' in target:
        return target in note_paths or f'{target}.md' in note_paths
    return target in stems


def _invalid_frontmatter(files: list[tuple[str, str]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for rel, text in files:
        if rel == INDEX_REL_PATH:
            continue
        message = _frontmatter_error(text)
        if message is not None:
            findings.append({'path': rel, 'message': message})
    return findings[:_MAX_FINDINGS]


def _frontmatter_error(text: str) -> str | None:
    try:
        fm, _body = frontmatter.parse(text)
        frontmatter.validate(fm, hub=fm.get('type') == 'hub')
    except NotesError as exc:
        return exc.message
    return None


def _filename_collisions(note_files: list[tuple[str, str]]) -> list[dict[str, Any]]:
    by_name: dict[str, list[str]] = {}
    for rel, _text in note_files:
        by_name.setdefault(PurePosixPath(rel).name, []).append(rel)
    collisions = [{'name': name, 'paths': sorted(paths)} for name, paths in sorted(by_name.items()) if len(paths) > 1]
    return collisions[:_MAX_FINDINGS]


def _missing_hubs(ctx: ToolContext, categories: list[Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for category in categories:
        if category.id == META_ID:
            continue
        basename = PurePosixPath(category.path).name
        hub = f'{category.path.rstrip("/")}/{basename}.md'
        if not (ctx.config.vault_path / hub).is_file():
            findings.append({'category_id': category.id, 'path': category.path, 'hub': hub})
    return findings[:_MAX_FINDINGS]


def _index_drift(ctx: ToolContext, categories: list[Any]) -> list[dict[str, Any]]:
    findings = [
        {'category_id': category.id, 'path': category.path}
        for category in categories
        if not (ctx.config.vault_path / category.path).is_dir()
    ]
    return findings[:_MAX_FINDINGS]


def _secrets(files: list[tuple[str, str]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for rel, text in files:
        for number, line in enumerate(text.split('\n'), start=1):
            for label, pattern in _SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append({'path': rel, 'line': number, 'pattern': label})
    return findings[:_MAX_FINDINGS]
