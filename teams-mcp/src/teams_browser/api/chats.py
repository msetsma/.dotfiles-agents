"""Chats and channel messages via the internal ``chatsvc`` API.

The Teams web client fetches every conversation - 1:1, group, meeting chat and
standard channel - from a single service::

    GET {chatServiceUrl}/v1/users/ME/conversations?view=msnp24Equivalent
    GET {chatServiceUrl}/v1/users/ME/conversations/{id}/messages?view=msnp24Equivalent

Auth is the ``skypetoken_asm`` cookie sent as ``Authentication: skypetoken=...``
(no bearer tokens involved). Channel threads are plain conversation ids ending
in ``@thread.tacv2`` and the same messages endpoint serves them, so no separate
CSA/middle-tier call is needed.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import unquote

from ..errors import ApiError, ResourceNotFound
from ..models import ChatMessage, Conversation, MessageAttachment, RegionConfig, TokenSet
from .http import HttpClient
from .util import parse_dt

_VIEW = 'msnp24Equivalent'

# Message types that are Teams bookkeeping rather than conversation
# (members added, calls started, etc.).
_SYSTEM_PREFIXES = ('ThreadActivity/', 'EventMessage')

_TAG = re.compile(r'<[^>]+>')
_AT = re.compile(r'<at[^>]*>(.*?)</at>', re.IGNORECASE | re.DOTALL)
_IMG = re.compile(r'<img\b[^>]*>', re.IGNORECASE)
_IMG_SRC = re.compile(r'src="([^"]+)"', re.IGNORECASE)
_IMG_ALT = re.compile(r'alt="([^"]*)"', re.IGNORECASE)
_ATTR_ID = re.compile(r'id="([^"]+)"', re.IGNORECASE)
_BREAK = re.compile(r'<\s*(br|/p|/div|/li)\s*/?>', re.IGNORECASE)


def _headers(tokens: TokenSet) -> dict[str, str]:
    if not tokens.skype_token:
        from ..errors import TokenExpired

        raise TokenExpired('No skypetoken in the session. Run `teams-browser login` to refresh.')
    return {'Authentication': f'skypetoken={tokens.skype_token}', 'Accept': 'application/json'}


# --------------------------------------------------------------------------- #
# HTML -> text
# --------------------------------------------------------------------------- #


def html_to_text(content: str | None) -> str:
    """Flatten a Teams message body to readable text.

    Teams encodes messages as a small HTML dialect: quoted replies in
    ``<blockquote>``, mentions as ``<at>``, inline images as ``<img>``. We keep
    the words and drop the markup, turning block boundaries into newlines.
    """
    if not content:
        return ''
    text = content
    # Drop quoted reply blocks entirely - they duplicate earlier messages.
    text = re.sub(r'<blockquote\b.*?</blockquote>', ' ', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<attachment\b[^>]*>(.*?)</attachment>', r' \1 ', text, flags=re.IGNORECASE | re.DOTALL)
    text = _BREAK.sub('\n', text)
    text = re.sub(r'<li\b[^>]*>', '\n- ', text, flags=re.IGNORECASE)
    text = _TAG.sub('', text)
    text = html.unescape(text)
    text = text.replace('\u200b', '').replace('\xa0', ' ')
    lines = [line.strip() for line in text.splitlines()]
    return '\n'.join(line for line in lines if line).strip()


def _extract_mentions(content: str | None) -> list[str]:
    if not content:
        return []
    seen: list[str] = []
    for raw in _AT.findall(content):
        name = html.unescape(_TAG.sub('', raw)).strip().lstrip('@')
        if name and name not in seen:
            seen.append(name)
    return seen


def _extract_images(content: str | None) -> list[MessageAttachment]:
    if not content:
        return []
    out: list[MessageAttachment] = []
    for tag in _IMG.findall(content):
        src = _IMG_SRC.search(tag)
        alt = _IMG_ALT.search(tag)
        out.append(
            MessageAttachment(
                name=(alt.group(1) if alt and alt.group(1) else None), url=(src.group(1) if src else None), kind='image'
            )
        )
    return out


def _extract_files(properties: dict[str, Any] | None) -> list[MessageAttachment]:
    """Parse the ``properties.files`` JSON blob Teams attaches to media messages."""
    raw = (properties or {}).get('files')
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    out: list[MessageAttachment] = []
    for item in parsed if isinstance(parsed, list) else []:
        if not isinstance(item, dict):
            continue
        out.append(
            MessageAttachment(
                name=item.get('title') or item.get('fileName'),
                url=item.get('fileUrl') or item.get('fileInfo', {}).get('fileUrl'),
                kind=(item.get('fileType') or 'file'),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def conversation_kind(raw: dict[str, Any]) -> str:
    props = raw.get('threadProperties') or {}
    cid = raw.get('id') or ''
    if props.get('productThreadType') == 'TeamsTeam' or props.get('threadType') == 'space':
        return 'channel'
    if cid.startswith('19:meeting_'):
        return 'meeting'
    if '@thread.tacv2' in cid:
        return 'channel'
    if '@unq.gbl.spaces' in cid:
        return 'one_to_one'
    if '@thread.v2' in cid:
        return 'group'
    return 'group'


def parse_conversation(raw: dict[str, Any]) -> Conversation:
    props = raw.get('threadProperties') or {}
    last = raw.get('lastMessage') or {}
    kind = conversation_kind(raw)
    topic = props.get('topic') or props.get('spaceThreadTopic') or None
    return Conversation(
        id=raw.get('id') or '',
        kind=kind,
        topic=topic,
        team_id=props.get('groupId'),
        last_message_at=parse_dt(last.get('composetime') or last.get('originalarrivaltime')),
        last_message_preview=html_to_text(last.get('content')) or None,
        last_sender=last.get('imdisplayname') or None,
        is_favorite=str((raw.get('properties') or {}).get('favorite', '')).lower() == 'true',
    )


def parse_message(raw: dict[str, Any], conversation_id: str | None = None) -> ChatMessage:
    message_type = raw.get('messagetype') or ''
    content = raw.get('content')
    is_system = message_type.startswith(_SYSTEM_PREFIXES) or raw.get('type') == 'EventMessage'

    attachments = _extract_files(raw.get('properties'))
    attachments.extend(_extract_images(content))

    reactions: dict[str, int] = {}
    for item in (raw.get('properties') or {}).get('emotions') or []:
        if isinstance(item, dict) and item.get('key'):
            reactions[str(item['key'])] = len(item.get('users') or [])

    return ChatMessage(
        id=str(raw.get('id') or ''),
        conversation_id=raw.get('conversationid') or conversation_id or '',
        sender=raw.get('imdisplayname') or None,
        sender_mri=raw.get('from') or None,
        timestamp=parse_dt(raw.get('composetime') or raw.get('originalarrivaltime')),
        text=html_to_text(content),
        content_type=raw.get('contenttype') or None,
        message_type=message_type or None,
        is_system=is_system,
        attachments=attachments,
        mentions=_extract_mentions(content),
        reactions=reactions,
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


def list_conversations(
    region: RegionConfig, tokens: TokenSet, *, top: int = 50, client: HttpClient | None = None
) -> list[Conversation]:
    owns = client is None
    client = client or HttpClient()
    try:
        try:
            data = client.get_json(
                f'{region.chat_service_url}/v1/users/ME/conversations',
                headers=_headers(tokens),
                params={'view': _VIEW, 'pageSize': str(top), '$top': str(top)},
            )
        except ApiError as exc:
            if exc.status in (401, 403):
                raise ResourceNotFound(
                    'The chatsvc service rejected the skypetoken. Run `teams-browser login`.'
                ) from exc
            raise
    finally:
        if owns:
            client.close()
    return [parse_conversation(c) for c in (data.get('conversations') or [])]


def list_raw_messages(
    region: RegionConfig,
    tokens: TokenSet,
    conversation_id: str,
    *,
    page_size: int = 50,
    client: HttpClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch a conversation's messages without flattening them.

    Call history (``48:calllogs``) needs the raw ``messagetype`` /
    ``properties`` / ``content`` shapes that the ``ChatMessage`` projection
    deliberately discards.
    """
    owns = client is None
    client = client or HttpClient()
    try:
        data = client.get_json(
            f'{region.chat_service_url}/v1/users/ME/conversations/{conversation_id}/messages',
            headers=_headers(tokens),
            params={'view': _VIEW, 'pageSize': str(page_size)},
        )
    finally:
        if owns:
            client.close()
    return list(data.get('messages') or [])


def list_messages(
    region: RegionConfig,
    tokens: TokenSet,
    conversation_id: str,
    *,
    page_size: int = 50,
    client: HttpClient | None = None,
) -> list[ChatMessage]:
    raw = list_raw_messages(region, tokens, conversation_id, page_size=page_size, client=client)
    messages = [parse_message(m, conversation_id) for m in raw]
    # chatsvc returns newest-first; present chronologically.
    messages.sort(key=lambda m: (m.timestamp is None, m.timestamp))
    return messages


def find_conversation(
    region: RegionConfig, tokens: TokenSet, needle: str, *, top: int = 100, client: HttpClient | None = None
) -> Conversation:
    """Find a conversation by exact id or case-insensitive topic substring."""
    if needle.startswith('19:') and ('@' in needle):
        candidate = Conversation(id=needle, kind='group')
        try:
            list_messages(region, tokens, needle, page_size=1, client=client)
            return candidate
        except ApiError as exc:
            raise ResourceNotFound(f"Conversation '{needle}' is not accessible.") from exc

    lowered = unquote(needle).lower()
    matches = [
        c for c in list_conversations(region, tokens, top=top, client=client) if lowered in (c.topic or '').lower()
    ]
    if not matches:
        raise ResourceNotFound(f"No conversation matched '{needle}'.")
    matches.sort(key=lambda c: (c.last_message_at is None, c.last_message_at), reverse=True)
    return matches[0]
