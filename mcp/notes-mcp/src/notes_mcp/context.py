"""Shared per-process tool context.

Every tool takes a :class:`ToolContext` first arg. The server builds one from
env/config at startup; tests build one directly (see CONTRACT.md section 10).
"""

from __future__ import annotations

from dataclasses import dataclass

from notes_mcp.config import Config
from notes_mcp.git import Git
from notes_mcp.index import VaultIndex
from notes_mcp.paths import PathGuard
from notes_mcp.search import Searcher


@dataclass
class ToolContext:
    config: Config
    guard: PathGuard
    index: VaultIndex
    search: Searcher
    git: Git


def build_context(config: Config | None = None) -> ToolContext:
    """Build the shared context, reading the environment when no config is given."""
    config = config or Config.from_env()
    return ToolContext(
        config=config,
        guard=PathGuard(config.vault_path),
        index=VaultIndex(config),
        search=Searcher(config),
        git=Git(config),
    )
