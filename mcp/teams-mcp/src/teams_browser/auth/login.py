"""Playwright-driven capture of the Teams web session.

The browser is used only for authentication. We never scrape the DOM: once the
Teams app has finished loading we snapshot ``storage_state`` (cookies +
localStorage) which contains everything needed to talk to the internal APIs.

A persistent on-disk profile lives in ``~/.cache/teams-browser/profile`` so
that subsequent runs usually only need a silent, headless refresh.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable
from typing import Any

from ..config import DEFAULT_TEAMS_LOGIN_URL, Paths, login_timeout
from ..errors import AuthRequiredError
from .session import SessionState, save_session
from .tokens import decode_jwt_payload, extract_region_config, extract_tokens, find_token, is_jwt


# Marker substrings used to decide "we have enough tokens to proceed".
_READY_MARKERS = ('substratesearch', 'substrate.office.com', 'api.spaces.skype.com')

# In-page probe for the spaces token (key names for MSAL/tmp.auth entries,
# values for classic MSAL JSON). Cheaper than snapshotting the whole session
# with ``storage_state()`` on every poll, which also churns the page while the
# user is trying to sign in.
_MARKER_JS = (
    '() => {'
    " let s = '';"
    ' for (let i = 0; i < localStorage.length; i++) {'
    '  const k = localStorage.key(i);'
    '  s += k + localStorage.getItem(k);'
    ' }'
    " return s.toLowerCase().includes('api.spaces.skype.com');"
    '}'
)


def _spaces_token_marker_present(context: Any) -> bool:
    for page in context.pages:
        with contextlib.suppress(Exception):  # page mid-navigation or on another origin
            if page.evaluate(_MARKER_JS):
                return True
    return False


def _capture(context: Any) -> SessionState:
    return context.storage_state()


def _has_required_tokens(state: SessionState) -> bool:
    return find_token(state, ('api.spaces.skype.com',)) is not None


def _ready(state: SessionState) -> bool:
    tokens = extract_tokens(state)
    return tokens.substrate is not None and tokens.spaces is not None


def interactive_login(
    *,
    headed: bool = True,
    paths: Paths | None = None,
    timeout: int | None = None,
    on_wait: Callable[[str], None] | None = None,
    settle_seconds: float = 6.0,
) -> SessionState:
    """Open Teams, wait for sign-in, and return the captured session state.

    ``settle_seconds`` is how long we keep polling after the first token appears
    so that the secondary tokens (Substrate, CSA, ...) have a chance to land.
    """
    paths = paths or Paths.default()
    paths.ensure()
    timeout = timeout or login_timeout()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise AuthRequiredError(
            'Playwright is not installed. Run `uv sync` and `uv run playwright install chromium`.'
        ) from exc

    notify = on_wait or (lambda _msg: None)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(paths.profile_dir),
            headless=not headed,
            args=['--no-first-run', '--no-default-browser-check'],
            viewport={'width': 1280, 'height': 900},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(DEFAULT_TEAMS_LOGIN_URL, wait_until='domcontentloaded')
            state = _wait_for_login(
                context, deadline=time.time() + timeout, settle_seconds=settle_seconds, notify=notify
            )
        finally:
            context.close()

    if state is None:
        raise AuthRequiredError(
            f'Timed out after {timeout}s waiting for Teams sign-in. '
            'Complete the login in the opened browser window and try again.'
        )
    save_session(state, paths)
    return state


def _wait_for_login(
    context: Any, *, deadline: float, settle_seconds: float, notify: Callable[[str], None]
) -> SessionState | None:
    """Poll until the required tokens land, give the rest a moment, or time out."""
    first_seen: float | None = None
    announced_wait = False
    while time.time() < deadline:
        state = _capture(context) if _spaces_token_marker_present(context) else None
        if state is not None and _has_required_tokens(state):
            if first_seen is None:
                first_seen = time.time()
                notify('Signed in - waiting for secondary tokens...')
            if _ready(state) or time.time() - first_seen >= settle_seconds:
                return state
        elif not announced_wait:
            announced_wait = True
            notify('Waiting for you to complete sign-in in the browser...')
        time.sleep(2.0)

    state = _capture(context)
    return state if _has_required_tokens(state) else None


def refresh_session_headless(
    *, paths: Paths | None = None, timeout: int = 60, settle_seconds: float = 3.0
) -> SessionState:
    """Re-open Teams headlessly to silently renew tokens from the saved profile."""
    return interactive_login(headed=False, paths=paths, timeout=timeout, settle_seconds=settle_seconds)


# --------------------------------------------------------------------------- #
# Reconnaissance
# --------------------------------------------------------------------------- #


def recon_dump(state: SessionState) -> dict[str, Any]:
    """Summarise what the captured session actually contains (secrets redacted)."""
    keys, token_targets = _recon_origins(state.get('origins') or [])
    tokens = extract_tokens(state)
    return {
        'origins': keys,
        'token_targets': token_targets,
        'cookie_names': sorted({c.get('name', '') for c in state.get('cookies') or []}),
        'extracted': {
            'substrate': tokens.substrate is not None,
            'spaces': tokens.spaces is not None,
            'csa': tokens.csa is not None,
            'graph': tokens.graph is not None,
            'skypetoken': tokens.skype_token is not None,
            'authtoken': tokens.auth_token is not None,
        },
        'region': _region_summary(extract_region_config(state)),
    }


def _recon_origins(origins: list[dict[str, Any]]) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    keys: dict[str, list[str]] = {}
    token_targets: list[dict[str, Any]] = []
    for origin in origins:
        names = []
        for item in origin.get('localStorage') or []:
            name = item.get('name', '')
            names.append(name)
            token_targets += _recon_targets(name, item.get('value', ''))
        keys[origin.get('origin', '?')] = sorted(names)
    return keys, token_targets


def _recon_targets(name: str, value: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if value.startswith('{'):
        try:
            entry = json.loads(value)
        except Exception:
            entry = {}
        target = entry.get('target')
        if target and is_jwt(entry.get('secret')):
            payload = decode_jwt_payload(entry['secret']) or {}
            found.append(
                {
                    'key': name,
                    'target': target,
                    'aud': payload.get('aud'),
                    'upn': payload.get('upn') or payload.get('preferred_username'),
                    'exp': payload.get('exp'),
                }
            )
    if '.Token.' in name:
        found.append({'key': name, 'resource': name.split('.Token.', 1)[1], 'tmp_auth': True})
    return found


def _region_summary(region: Any) -> dict[str, Any] | None:
    if not region:
        return None
    return {
        'region': region.region,
        'partition': region.partition,
        'has_partition': region.has_partition,
        'teams_base_url': region.teams_base_url,
        'middle_tier_url': region.middle_tier_url,
    }
