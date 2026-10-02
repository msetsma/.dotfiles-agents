"""Browserless token refresh via the Entra ID refresh-token grant.

Classic Teams sessions carry a ``RefreshToken`` entry in the MSAL cache. We can
exchange it directly for fresh access tokens (Teams is a public SPA client, so
no secret is needed) and then re-mint the messaging cookies via ``authsvc``.
That turns a ~8s browser round-trip into ~100ms.

If the session only holds new-Teams ``tmp.auth`` encrypted tokens (no classic
refresh token) this returns ``False`` and the caller falls back to the browser.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import quote

import httpx

from ..config import request_timeout
from .session import Paths, SessionState, get_teams_origin, save_session

_TOKEN_ENDPOINT = 'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'
_AUTHSVC_ENDPOINT = 'https://authsvc.teams.microsoft.com/v1.0/authz'

# resource marker -> requested scope
_SCOPES = [
    ('substrate.office.com', 'https://substrate.office.com/.default offline_access'),
    ('api.spaces.skype.com', 'https://api.spaces.skype.com/.default offline_access'),
    ('chatsvcagg.teams.microsoft.com', 'https://chatsvcagg.teams.microsoft.com/.default offline_access'),
]

_SKYPE_TOKEN_DOMAINS = ('.asyncgw.teams.microsoft.com', '.asm.skype.com')
_AUTH_TOKEN_DOMAINS = ('teams.cloud.microsoft', 'teams.microsoft.com')


def refresh_via_http(state: SessionState, paths: Paths | None = None) -> bool:
    origin = get_teams_origin(state)
    if not origin or not origin.get('localStorage'):
        return False

    cache = _extract_msal_cache(origin['localStorage'])
    if not cache:
        return False

    current_refresh = cache['refresh_token']
    spaces_token: str | None = None
    spaces_expires_in: int | None = None
    refreshed = 0

    with httpx.Client(timeout=request_timeout()) as client:
        for resource, scope in _SCOPES:
            result = _refresh_access_token(client, cache['tenant_id'], cache['client_id'], current_refresh, scope)
            if not result:
                continue
            _update_access_token(origin['localStorage'], resource, result, cache)
            refreshed += 1
            new_refresh = result.get('refresh_token')
            if new_refresh and new_refresh != current_refresh:
                current_refresh = new_refresh
            if resource == 'api.spaces.skype.com':
                spaces_token = result['access_token']
                spaces_expires_in = result.get('expires_in', 3600)

        if refreshed == 0:
            return False

        _update_refresh_token(origin['localStorage'], cache['refresh_key'], current_refresh)

        if spaces_token:
            skype = _exchange_skype_token(client, spaces_token)
            if skype:
                _set_skype_cookie(state, skype, 86400)
                _set_auth_cookie(state, spaces_token, spaces_expires_in or 3600)

    save_session(state, paths)
    return True


def _extract_msal_cache(local_storage: list[dict[str, str]]) -> dict[str, Any] | None:
    refresh_token = None
    refresh_key = None
    client_id = None
    tenant_id = None

    for item in local_storage:
        try:
            entry = json.loads(item.get('value', ''))
        except Exception:
            continue
        if entry.get('credentialType') == 'RefreshToken' and entry.get('secret'):
            refresh_token = entry['secret']
            refresh_key = item['name']
            client_id = entry.get('clientId')
        if entry.get('credentialType') == 'AccessToken' and entry.get('realm') and not tenant_id:
            tenant_id = entry['realm']

    if not (refresh_token and refresh_key and client_id and tenant_id):
        return None
    return {
        'refresh_token': refresh_token,
        'refresh_key': refresh_key,
        'client_id': client_id,
        'tenant_id': tenant_id,
        'home_account_id': entry_home_account(local_storage) or '',
        'environment': 'login.microsoftonline.com',
    }


def entry_home_account(local_storage: list[dict[str, str]]) -> str | None:
    for item in local_storage:
        try:
            entry = json.loads(item.get('value', ''))
        except Exception:
            continue
        if entry.get('homeAccountId'):
            return entry['homeAccountId']
    return None


def _refresh_access_token(
    client: httpx.Client, tenant_id: str, client_id: str, refresh_token: str, scope: str
) -> dict[str, Any] | None:
    url = _TOKEN_ENDPOINT.format(tenant=tenant_id)
    try:
        response = client.post(
            url,
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                # Required: Teams' client id is registered as an SPA, so Entra
                # rejects refresh grants without a matching Origin header.
                'Origin': 'https://teams.microsoft.com',
            },
            content=(
                f'grant_type=refresh_token&client_id={quote(client_id)}'
                f'&refresh_token={quote(refresh_token)}&scope={quote(scope)}'
            ),
        )
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _exchange_skype_token(client: httpx.Client, spaces_token: str) -> str | None:
    try:
        response = client.post(
            _AUTHSVC_ENDPOINT,
            headers={'Authorization': f'Bearer {spaces_token}', 'Content-Type': 'application/json'},
            content='{}',
        )
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    try:
        return (response.json().get('tokens') or {}).get('skypeToken')
    except ValueError:
        return None


def _update_access_token(
    local_storage: list[dict[str, str]], resource: str, token_response: dict[str, Any], cache: dict[str, Any]
) -> None:
    now = int(time.time())
    expires_on = str(now + int(token_response.get('expires_in', 3600)))
    extended = str(now + int(token_response.get('ext_expires_in', token_response.get('expires_in', 3600))))

    for item in local_storage:
        try:
            entry = json.loads(item.get('value', ''))
        except Exception:
            continue
        if entry.get('credentialType') != 'AccessToken':
            continue
        target = entry.get('target', '')
        if resource not in target:
            continue
        entry.update(
            secret=token_response['access_token'], expiresOn=expires_on, extendedExpiresOn=extended, cachedAt=str(now)
        )
        item['value'] = json.dumps(entry)
        return

    entry = {
        'credentialType': 'AccessToken',
        'homeAccountId': cache.get('home_account_id', ''),
        'environment': cache.get('environment', 'login.microsoftonline.com'),
        'clientId': cache['client_id'],
        'realm': cache['tenant_id'],
        'target': token_response.get('scope', resource),
        'tokenType': token_response.get('token_type', 'Bearer'),
        'secret': token_response['access_token'],
        'expiresOn': expires_on,
        'extendedExpiresOn': extended,
        'cachedAt': str(now),
    }
    key = (
        f'{entry["homeAccountId"]}-{entry["environment"]}-accesstoken-'
        f'{entry["clientId"]}-{entry["realm"]}-{entry["target"].lower()}'
    )
    local_storage.append({'name': key, 'value': json.dumps(entry)})


def _update_refresh_token(local_storage: list[dict[str, str]], refresh_key: str, new_refresh: str) -> None:
    for item in local_storage:
        if item.get('name') != refresh_key:
            continue
        try:
            entry = json.loads(item.get('value', ''))
        except Exception:
            entry = {'credentialType': 'RefreshToken'}
        entry['secret'] = new_refresh
        entry['lastUpdatedAt'] = str(int(time.time() * 1000))
        item['value'] = json.dumps(entry)
        return


def _set_skype_cookie(state: SessionState, skype_token: str, expires_in: int) -> None:
    expires = time.time() + expires_in
    for domain in _SKYPE_TOKEN_DOMAINS:
        _set_cookie(state, 'skypetoken_asm', skype_token, domain, expires, http_only=True)


def _set_auth_cookie(state: SessionState, spaces_token: str, expires_in: int) -> None:
    expires = time.time() + expires_in
    value = 'Bearer%3D' + quote(spaces_token, safe='')
    for domain in _AUTH_TOKEN_DOMAINS:
        _set_cookie(state, 'authtoken', value, domain, expires, http_only=False)


def _set_cookie(state: SessionState, name: str, value: str, domain: str, expires: float, *, http_only: bool) -> None:
    cookies = state.setdefault('cookies', [])
    for cookie in cookies:
        if cookie.get('name') == name and cookie.get('domain') == domain:
            cookie.update(value=value, expires=expires)
            return
    cookies.append(
        {
            'name': name,
            'value': value,
            'domain': domain,
            'path': '/',
            'expires': expires,
            'httpOnly': http_only,
            'secure': True,
            'sameSite': 'None',
        }
    )
