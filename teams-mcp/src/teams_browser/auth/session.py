"""Persistent storage for the captured browser session (cookies + localStorage).

The session is the crown jewels: it contains bearer tokens for the user's Teams
account. It is encrypted at rest with a key held in the OS keychain where
available, falling back to a 0600 key file in the cache directory.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from ..config import APP_NAME, TEAMS_ORIGINS, Paths
from ..errors import AuthRequired

_KEYRING_SERVICE = APP_NAME
_KEYRING_USER = 'session-key'


def _load_or_create_key(paths: Paths) -> bytes:
    try:
        import keyring

        stored = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
        if stored:
            return stored.encode()
        key = Fernet.generate_key()
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, key.decode())
        return key
    except Exception:
        pass

    if paths.key_file.exists():
        return paths.key_file.read_bytes().strip()

    key = Fernet.generate_key()
    paths.key_file.write_bytes(key)
    try:
        os.chmod(paths.key_file, 0o600)
    except OSError:
        pass
    return key


def _fernet(paths: Paths) -> Fernet:
    return Fernet(_load_or_create_key(paths))


def encrypt(json_bytes: bytes, paths: Paths | None = None) -> bytes:
    paths = paths or Paths.default()
    paths.ensure()
    return _fernet(paths).encrypt(json_bytes)


def decrypt(blob: bytes, paths: Paths | None = None) -> bytes:
    paths = paths or Paths.default()
    return _fernet(paths).decrypt(blob)


# --------------------------------------------------------------------------- #
# Session state
# --------------------------------------------------------------------------- #

SessionState = dict[str, Any]


def save_session(state: SessionState, paths: Paths | None = None) -> None:
    paths = paths or Paths.default()
    paths.ensure()
    payload = json.dumps(state).encode('utf-8')
    paths.session_file.write_bytes(encrypt(payload, paths))
    try:
        os.chmod(paths.session_file, 0o600)
    except OSError:
        pass


def load_session(paths: Paths | None = None) -> SessionState | None:
    paths = paths or Paths.default()
    if not paths.session_file.exists():
        return None
    try:
        raw = decrypt(paths.session_file.read_bytes(), paths)
    except (InvalidToken, ValueError):
        raise AuthRequired(
            'Stored session could not be decrypted (key changed or file corrupt). Run `teams-browser login` again.'
        )
    return json.loads(raw)


def clear_session(paths: Paths | None = None) -> None:
    paths = paths or Paths.default()
    for f in (paths.session_file, paths.cache_file):
        if f.exists():
            f.unlink()


# --------------------------------------------------------------------------- #
# Accessors
# --------------------------------------------------------------------------- #


def get_teams_origin(state: SessionState) -> dict[str, Any] | None:
    """Pick the best Teams origin from the captured storage state.

    Preference order follows the known Teams hosts; when several are present we
    favour one that actually holds a Substrate token (new Teams stores search
    tokens on ``teams.cloud.microsoft``).
    """
    origins = state.get('origins') or []
    if not origins:
        return None

    def has_substrate(origin: dict[str, Any]) -> bool:
        for item in origin.get('localStorage') or []:
            name = item.get('name', '')
            if 'SubstrateSearch' in name:
                return True
            value = item.get('value', '')
            if isinstance(value, str) and 'SubstrateSearch' in value and value.startswith('{'):
                return True
        return False

    for origin in origins:
        if origin.get('origin') in TEAMS_ORIGINS and has_substrate(origin):
            return origin

    for known in TEAMS_ORIGINS:
        for origin in origins:
            if origin.get('origin') == known:
                return origin

    for origin in origins:
        url = origin.get('origin', '')
        if 'teams.microsoft' in url or 'teams.cloud' in url:
            return origin

    return None


def local_storage(state: SessionState) -> list[dict[str, str]]:
    origin = get_teams_origin(state)
    return list(origin.get('localStorage', [])) if origin else []


def cookies(state: SessionState) -> list[dict[str, Any]]:
    return list(state.get('cookies') or [])


def b64url_decode(data: str) -> bytes:
    """Decode a base64url segment, restoring padding."""
    padding = '=' * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)
