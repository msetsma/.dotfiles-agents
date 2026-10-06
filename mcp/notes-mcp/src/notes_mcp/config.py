"""Runtime configuration for the notes MCP server.

All values come from the environment so the same code runs for every agent.
``VAULT_PATH`` is the only required variable; the rest have sane defaults.
QMD model overrides are passed through verbatim to the ``qmd`` subprocess.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import CONFIG_ERROR, NotesError


@dataclass(frozen=True)
class Config:
    vault_path: Path
    agent_name: str = 'claude'
    git_remote: str = 'origin'
    qmd_collection: str = 'notes'
    qmd_embed_model: str | None = None
    qmd_rerank_model: str | None = None
    qmd_generate_model: str | None = None
    session: str = 'unknown'

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        """Build a :class:`Config` from ``env`` (defaults to ``os.environ``)."""
        env = os.environ if env is None else env

        raw = env.get('VAULT_PATH')
        if not raw:
            raise NotesError(
                CONFIG_ERROR, 'VAULT_PATH is not set', 'Set VAULT_PATH to the absolute path of the notes vault root.'
            )

        vault_path = Path(raw).expanduser()
        try:
            vault_path = vault_path.resolve()
        except OSError as exc:  # pragma: no cover - platform dependent
            raise NotesError(
                CONFIG_ERROR, f'VAULT_PATH cannot be resolved: {raw}', 'Point VAULT_PATH at an existing directory.'
            ) from exc

        if not vault_path.is_dir():
            raise NotesError(
                CONFIG_ERROR,
                f'VAULT_PATH is not a directory: {vault_path}',
                'Point VAULT_PATH at an existing directory.',
            )

        return cls(
            vault_path=vault_path,
            agent_name=env.get('AGENT_NAME') or 'claude',
            git_remote=env.get('GIT_REMOTE') or 'origin',
            qmd_collection=env.get('QMD_COLLECTION') or 'notes',
            qmd_embed_model=env.get('QMD_EMBED_MODEL') or None,
            qmd_rerank_model=env.get('QMD_RERANK_MODEL') or None,
            qmd_generate_model=env.get('QMD_GENERATE_MODEL') or None,
            session=env.get('AGENT_SESSION') or 'unknown',
        )
