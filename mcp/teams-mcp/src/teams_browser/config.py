"""Runtime configuration and on-disk paths.

Everything is overridable via environment variables so the tool can run
standalone, from tests, or embedded in an MCP server.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


APP_NAME = 'teams-browser'

DEFAULT_TEAMS_BASE_URL = 'https://teams.microsoft.com'
DEFAULT_SUBSTRATE_BASE_URL = 'https://substrate.office.com'
DEFAULT_TEAMS_LOGIN_URL = 'https://teams.microsoft.com/v2/'

TEAMS_ORIGINS = (
    'https://teams.cloud.microsoft',
    'https://teams.microsoft.com',
    'https://teams.microsoft.us',
    'https://dod.teams.microsoft.us',
)

LOGIN_TIMEOUT_SECONDS = 300
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3


def _home() -> Path:
    return Path(os.environ.get('TEAMS_BROWSER_HOME', Path.home() / '.cache' / APP_NAME))


@dataclass(frozen=True)
class Paths:
    root: Path
    profile_dir: Path
    session_file: Path
    cache_file: Path
    key_file: Path
    db_file: Path

    @classmethod
    def default(cls) -> Paths:
        root = _home()
        return cls(
            root=root,
            profile_dir=root / 'profile',
            session_file=root / 'session.enc',
            cache_file=root / 'tokens.enc',
            key_file=root / 'secret.key',
            db_file=root / 'archive.db',
        )

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass


def teams_base_url() -> str:
    return os.environ.get('TEAMS_BROWSER_TEAMS_BASE_URL', DEFAULT_TEAMS_BASE_URL)


def substrate_base_url() -> str:
    return os.environ.get('TEAMS_BROWSER_SUBSTRATE_BASE_URL', DEFAULT_SUBSTRATE_BASE_URL)


def region_override() -> str | None:
    """Optional `<region>` or `<region>-<partition>` override for the calendar API."""
    return os.environ.get('TEAMS_BROWSER_REGION') or None


def login_timeout() -> int:
    return int(os.environ.get('TEAMS_BROWSER_LOGIN_TIMEOUT', LOGIN_TIMEOUT_SECONDS))


def request_timeout() -> int:
    return int(os.environ.get('TEAMS_BROWSER_REQUEST_TIMEOUT', REQUEST_TIMEOUT_SECONDS))
