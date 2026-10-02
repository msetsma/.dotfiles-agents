"""Extract bearer tokens, region config and user identity from a captured session.

Teams has two token storage formats and we must handle both:

* **Classic MSAL** - localStorage entries whose JSON contains ``target`` (the
  scope) and ``secret`` (the JWT).
* **New Teams (``tmp.auth.v1.*``)** - ``*.Token.<RESOURCE>`` entries holding an
  AES-256-CBC encrypted JWT. The key is exported by Teams under
  ``ExportedEncryptionKey``.

Messaging/calendar auth additionally uses cookies (``skypetoken_asm``,
``authtoken``).
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import unquote, urlparse

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..config import teams_base_url
from ..errors import ConfigError
from ..models import RegionConfig, TokenInfo, TokenSet, UserDetails
from .session import SessionState, b64url_decode, cookies, local_storage

# Resource identifiers we recognise, longest/most-specific first.
_SUBSTRATE_MARKERS = ('substratesearch', 'substrate.office.com')
_SPACES_MARKERS = ('api.spaces.skype.com',)
_CSA_MARKERS = ('chatsvcagg.teams.microsoft.com',)
_IC3_MARKERS = ('ic3.teams.office.com',)
_GRAPH_MARKERS = ('graph.microsoft.com',)

# Teams auth cookie hosts.
_AUTH_COOKIE_DOMAINS = ('teams.cloud.microsoft', 'teams.microsoft.com', 'teams.microsoft.us')


# --------------------------------------------------------------------------- #
# JWT helpers
# --------------------------------------------------------------------------- #


def decode_jwt_payload(token: str) -> dict[str, Any] | None:
    try:
        parts = token.split('.')
        if len(parts) < 2:
            return None
        return json.loads(b64url_decode(parts[1]).decode('utf-8'))
    except Exception:
        return None


def jwt_expiry(token: str) -> datetime | None:
    payload = decode_jwt_payload(token)
    exp = payload.get('exp') if payload else None
    if not isinstance(exp, (int, float)):
        return None
    return datetime.fromtimestamp(exp, tz=timezone.utc)


def is_jwt(value: Any) -> bool:
    return isinstance(value, str) and value.startswith('ey')


# --------------------------------------------------------------------------- #
# tmp.auth encrypted tokens
# --------------------------------------------------------------------------- #


def extract_encryption_key(entries: Iterable[dict[str, str]]) -> bytes | None:
    for item in entries:
        if 'ExportedEncryptionKey' not in item.get('name', ''):
            continue
        try:
            parsed = json.loads(item.get('value', ''))
            key_b64 = (parsed.get('item') or {}).get('exportedKey')
            if not key_b64:
                continue
            key = b64url_or_std_b64decode(key_b64)
            if key and len(key) == 32:
                return key
        except Exception:
            continue
    return None


def b64url_or_std_b64decode(data: str) -> bytes | None:
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            return decoder(data + '=' * (-len(data) % 4))
        except Exception:
            continue
    return None


def decrypt_tmp_auth_token(encrypted_b64: str, iv_b64: str, key: bytes) -> str | None:
    try:
        encrypted = b64url_or_std_b64decode(encrypted_b64)
        iv = b64url_or_std_b64decode(iv_b64)
        if not encrypted or not iv or len(key) != 32 or len(iv) < 16:
            return None
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv[:16])).decryptor()
        plaintext = decryptor.update(encrypted) + decryptor.finalize()
        # Strip PKCS#7 padding.
        pad = plaintext[-1]
        if 1 <= pad <= 16:
            plaintext = plaintext[:-pad]
        token = plaintext.decode('utf-8', errors='ignore').strip()
        return token if token.startswith('ey') else None
    except Exception:
        return None


def _resolve_tmp_auth_item(item: dict[str, Any], key: bytes | None) -> TokenInfo | None:
    expires = item.get('expires')
    expiry = datetime.fromtimestamp(expires, tz=timezone.utc) if isinstance(expires, (int, float)) else None
    if expiry and expiry <= datetime.now(tz=timezone.utc):
        return None

    token = item.get('token')
    if is_jwt(token):
        return TokenInfo(token=token, expires_at=jwt_expiry(token) or expiry)

    if token == 'dummy-token':
        return None

    if key and item.get('encryptedToken') and item.get('iv'):
        decrypted = decrypt_tmp_auth_token(item['encryptedToken'], item['iv'], key)
        if decrypted:
            return TokenInfo(token=decrypted, expires_at=jwt_expiry(decrypted) or expiry)
    return None


# --------------------------------------------------------------------------- #
# Token discovery
# --------------------------------------------------------------------------- #


def find_token(state: SessionState, markers: tuple[str, ...]) -> TokenInfo | None:
    """Find the longest-lived token whose target/resource matches any marker."""
    entries = local_storage(state)
    key = extract_encryption_key(entries)
    lowered = tuple(m.lower() for m in markers)
    best: TokenInfo | None = None

    def consider(info: TokenInfo | None) -> None:
        nonlocal best
        if not info:
            return
        if info.is_expired(skew_seconds=0):
            return
        if best is None or (info.expires_at and best.expires_at and info.expires_at > best.expires_at):
            best = info

    for item in entries:
        name = item.get('name', '')
        value = item.get('value', '')

        # tmp.auth encrypted token: key looks like `tmp.auth.v1...Token.HTTPS://HOST`
        if '.Token.' in name:
            resource = name.split('.Token.', 1)[1]
            if any(m in resource.lower() for m in lowered):
                try:
                    parsed = json.loads(value)
                except Exception:
                    continue
                info = _resolve_tmp_auth_item(parsed.get('item') or {}, key)
                if info:
                    info.resource = resource
                consider(info)
            continue

        # Classic MSAL entry.
        if not value.startswith('{'):
            continue
        try:
            entry = json.loads(value)
        except Exception:
            continue
        target = entry.get('target')
        secret = entry.get('secret')
        if not isinstance(target, str) or not is_jwt(secret):
            continue
        if any(m in target.lower() for m in lowered):
            consider(TokenInfo(token=secret, expires_at=jwt_expiry(secret), resource=target))

    return best


def extract_tokens(state: SessionState) -> TokenSet:
    skype, auth = extract_message_cookies(state)
    return TokenSet(
        substrate=find_token(state, _SUBSTRATE_MARKERS),
        spaces=find_token(state, _SPACES_MARKERS),
        csa=find_token(state, _CSA_MARKERS),
        ic3=find_token(state, _IC3_MARKERS),
        graph=find_token(state, _GRAPH_MARKERS),
        skype_token=skype,
        auth_token=auth,
    )


def extract_message_cookies(state: SessionState) -> tuple[str | None, str | None]:
    """Return ``(skypetoken_asm, authtoken)`` from the captured cookies."""
    teams_cookies = [c for c in cookies(state) if any(d in (c.get('domain') or '') for d in _AUTH_COOKIE_DOMAINS)]

    skype_candidates = [c for c in teams_cookies if c.get('name') == 'skypetoken_asm' and c.get('value')]
    skype_candidates.sort(key=lambda c: (c.get('domain', '') or '').startswith('asyncgw'), reverse=True)
    skype = skype_candidates[0]['value'] if skype_candidates else None

    raw_auth = None
    for preferred in ('teams.cloud.microsoft', 'teams.microsoft.com'):
        for c in teams_cookies:
            if c.get('name') == 'authtoken' and preferred in (c.get('domain') or ''):
                raw_auth = c.get('value')
                break
        if raw_auth:
            break
    if raw_auth is None:
        for c in teams_cookies:
            if c.get('name') == 'authtoken':
                raw_auth = c.get('value')
                break

    auth = None
    if raw_auth:
        auth = unquote(raw_auth)
        if auth.startswith('Bearer='):
            auth = auth[len('Bearer=') :]
    return skype, auth


# --------------------------------------------------------------------------- #
# Region + identity
# --------------------------------------------------------------------------- #


_REGION_IN_CHATSVC = re.compile(r'/api/chatsvc/([a-z]+)$')
_PARTITION_IN_MIDDLE_TIER = re.compile(r'/api/mt/part/([a-z]+)-(\d+)$')
_REGION_IN_MIDDLE_TIER = re.compile(r'/api/mt/([a-z]+)$')


def extract_region_config(state: SessionState) -> RegionConfig | None:
    for item in local_storage(state):
        if 'DISCOVER-REGION-GTM' not in item.get('name', ''):
            continue
        try:
            data = json.loads(item.get('value', '')).get('item') or {}
        except Exception:
            continue
        chat_service_url = data.get('chatServiceAfd')
        if not chat_service_url:
            continue
        return _region_from_discovery(
            chat_service_url, data.get('middleTier', '') or '', data.get('chatSvcAggAfd', '') or ''
        )
    return None


def _region_from_discovery(chat_service_url: str, middle_tier_url: str, csa_service_url: str) -> RegionConfig | None:
    match = _REGION_IN_CHATSVC.search(chat_service_url)
    if not match:
        return None
    region = match.group(1)
    base = _base_url(chat_service_url)
    partition, region_partition, has_partition = _parse_partition(region, middle_tier_url)
    return RegionConfig(
        region=region,
        partition=partition,
        region_partition=region_partition,
        has_partition=has_partition,
        middle_tier_url=middle_tier_url,
        chat_service_url=chat_service_url,
        csa_service_url=csa_service_url or f'{base}/api/csa/{region}',
        teams_base_url=base,
    )


def _base_url(url: str) -> str:
    parsed = urlparse(url)
    return f'{parsed.scheme}://{parsed.netloc}' if parsed.netloc else teams_base_url()


def _parse_partition(region: str, middle_tier_url: str) -> tuple[str, str, bool]:
    part = _PARTITION_IN_MIDDLE_TIER.search(middle_tier_url)
    if part:
        return part.group(2), f'{part.group(1)}-{part.group(2)}', True
    simple = _REGION_IN_MIDDLE_TIER.search(middle_tier_url)
    return '', simple.group(1) if simple else region, False


def extract_user_details(state: SessionState) -> UserDetails | None:
    for item in local_storage(state):
        if 'DISCOVER-USER-DETAILS' not in item.get('name', ''):
            continue
        try:
            data = json.loads(item.get('value', '')).get('item') or {}
        except Exception:
            continue
        mri = data.get('id')
        if not mri:
            continue
        licenses = data.get('licenseDetails') or {}
        return UserDetails(
            mri=mri,
            region=data.get('region'),
            licenses={k: bool(v) for k, v in licenses.items() if isinstance(v, bool)},
        )

    # Fall back to claims from any token.
    tokens = extract_tokens(state)
    for info in (tokens.substrate, tokens.spaces, tokens.csa, tokens.ic3):
        if not info:
            continue
        payload = decode_jwt_payload(info.token) or {}
        oid = payload.get('oid')
        if oid:
            return UserDetails(
                mri=f'8:orgid:{oid}',
                display_name=payload.get('name'),
                upn=payload.get('upn') or payload.get('preferred_username'),
            )
    return None


def get_identity(state: SessionState) -> tuple[str | None, str | None]:
    """Best-effort ``(display_name, upn)`` from any token claim."""
    tokens = extract_tokens(state)
    for info in (tokens.spaces, tokens.substrate, tokens.csa, tokens.ic3, tokens.graph):
        if not info:
            continue
        payload = decode_jwt_payload(info.token) or {}
        name = payload.get('name')
        upn = payload.get('upn') or payload.get('preferred_username')
        if name or upn:
            return name, upn
    details = extract_user_details(state)
    if details and details.upn:
        return details.upn, details.upn
    return None, None


def require_region(state: SessionState) -> RegionConfig:
    region = extract_region_config(state)
    if not region:
        raise ConfigError('Could not determine Teams region/partition from the session. Try logging in again.')
    return region
