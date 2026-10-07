"""Runtime configuration for the notes MCP server.

All values come from the environment so the same code runs for every agent.
``VAULT_PATH`` is the only required variable; the rest have sane defaults. QMD
model overrides pass through to the ``qmd`` child env verbatim; the daemon,
timeout, and embed knobs steer the search adapter (:mod:`notes_mcp.search`).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import CONFIG_ERROR, NotesError


DEFAULT_DAEMON_URL = 'http://localhost:8181/mcp'
DEFAULT_QMD_TIMEOUT = 60.0
_DISABLED = frozenset({'', '0', 'false', 'no', 'none', 'off'})


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
    qmd_daemon_url: str | None = DEFAULT_DAEMON_URL
    qmd_timeout: float = DEFAULT_QMD_TIMEOUT
    qmd_embed_on_write: bool = True
    human_name: str | None = None
    human_email: str | None = None

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
            qmd_daemon_url=_optional_url(env.get('QMD_DAEMON_URL')),
            qmd_timeout=_as_float(env.get('QMD_TIMEOUT'), DEFAULT_QMD_TIMEOUT),
            qmd_embed_on_write=_as_bool(env.get('QMD_EMBED_ON_WRITE'), default=True),
            human_name=env.get('HUMAN_NAME') or None,
            human_email=env.get('HUMAN_EMAIL') or None,
        )


def _optional_url(value: str | None) -> str | None:
    """``None`` (unset) -> the default URL; a disabled marker -> ``None`` (CLI only)."""
    if value is None:
        return DEFAULT_DAEMON_URL
    stripped = value.strip()
    return None if stripped.lower() in _DISABLED else stripped


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in _DISABLED


def _as_float(value: str | None, default: float) -> float:
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default
