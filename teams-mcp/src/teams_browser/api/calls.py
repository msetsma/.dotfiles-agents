"""Call history (scheduled or ad-hoc) via the synthetic ``48:calllogs`` chat.

Ad-hoc calls - a 1:1 "call with" or a call started from a group chat - never
appear in the calendar, so the meeting-subject transcript lookup cannot find
them. Teams records every call in a ``48:calllogs`` conversation instead:

* a ``Text`` message whose ``properties.call-log`` JSON carries the times,
  direction, state and participant MRIs/names;
* ``RichText/Media_CallLog(Transcript|Recording)`` messages whose JSON body
  carries the ``ThreadId`` that the Substrate transcript lookup needs.

The two are joined on ``CallId``. A call only becomes translatable once an event
message supplies its thread id, so ``Call.thread_id`` is ``None`` for calls that
were never recorded.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..errors import ApiError
from ..models import Call, CallParticipant, RegionConfig, TokenSet
from .chats import list_raw_messages
from .http import HttpClient
from .util import parse_dt


CALL_LOGS_CONVERSATION = '48:calllogs'

_CALL_LOG_MEDIA = ('RichText/Media_CallLogTranscript', 'RichText/Media_CallLogRecording')


def _participant(raw: Any) -> CallParticipant | None:
    if not isinstance(raw, dict):
        return None
    return CallParticipant(id=raw.get('id') or raw.get('mri') or None, display_name=(raw.get('displayName') or None))


def _loads(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_call_log_entry(log: dict[str, Any]) -> Call | None:
    """Build a ::class:`Call` from a ``properties.call-log`` JSON object."""
    call_id = log.get('callId')
    if not call_id:
        return None
    participants = [p for p in map(_participant, log.get('participantList') or []) if p]
    return Call(
        call_id=str(call_id),
        direction=log.get('callDirection') or None,
        kind=log.get('callType') or None,
        state=log.get('callState') or None,
        start_time=parse_dt(log.get('startTime')),
        end_time=parse_dt(log.get('endTime')),
        originator=_participant(log.get('originatorParticipant')) or _participant(log.get('originator')),
        target=_participant(log.get('targetParticipant')) or _participant(log.get('target')),
        participants=participants,
    )


def parse_call_log_event(raw: dict[str, Any]) -> Call | None:
    """Build a ::class:`Call` from a ``Media_CallLog*`` event message."""
    message_type = raw.get('messagetype') or ''
    if message_type not in _CALL_LOG_MEDIA:
        return None
    payload = _loads(raw.get('content'))
    if not payload or not payload.get('CallId'):
        return None
    types = str(payload.get('ContentTypes') or '').lower()
    return Call(
        call_id=str(payload['CallId']),
        thread_id=payload.get('ThreadId') or None,
        has_transcript='transcript' in types or message_type.endswith('Transcript'),
        has_recording='recording' in types or message_type.endswith('Recording'),
    )


def parse_call_log_message(raw: dict[str, Any]) -> Call | None:
    """Parse any message in the call-logs conversation, or ``None``."""
    message_type = raw.get('messagetype') or ''
    if message_type in _CALL_LOG_MEDIA:
        return parse_call_log_event(raw)
    if message_type == 'Text':
        properties = raw.get('properties') or {}
        return parse_call_log_entry(_loads(properties.get('call-log')) or {})
    return None


def _merge(base: Call, update: Call) -> None:
    """Fold an event's thread/availability onto its call-log entry."""
    for field in ('thread_id', 'direction', 'kind', 'state', 'start_time', 'end_time', 'title'):
        if getattr(base, field) is None and getattr(update, field) is not None:
            setattr(base, field, getattr(update, field))
    base.has_transcript = base.has_transcript or update.has_transcript
    base.has_recording = base.has_recording or update.has_recording
    if not base.originator and update.originator:
        base.originator = update.originator
    if not base.target and update.target:
        base.target = update.target
    if not base.participants and update.participants:
        base.participants = update.participants


def list_calls(
    region: RegionConfig, tokens: TokenSet, *, limit: int = 100, client: HttpClient | None = None
) -> list[Call]:
    """Recent call history, newest first, joined on ``CallId``.

    Calls without a transcript are still returned (useful as history); callers
    filter on ``has_transcript``/``thread_id`` when they need text.
    """
    try:
        raw = list_raw_messages(region, tokens, CALL_LOGS_CONVERSATION, page_size=limit, client=client)
    except ApiError as exc:
        # Not every tenant provisions the synthetic call-logs chat.
        if exc.status in (403, 404):
            return []
        raise

    merged: dict[str, Call] = {}
    for message in raw:
        call = parse_call_log_message(message)
        if call is None:
            continue
        existing = merged.get(call.call_id)
        if existing is None:
            merged[call.call_id] = call
        else:
            _merge(existing, call)

    calls = list(merged.values())
    calls.sort(key=lambda c: c.start_time or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return calls


def _participants_of(call: Call) -> list[CallParticipant]:
    return [p for p in (call.originator, call.target, *call.participants) if p is not None]


def derive_title(call: Call, *, me: str | None = None) -> str | None:
    """Name a call after its remote participant, when one is known.

    The call log resolves the caller's display name but leaves the other side
    ``null`` (that side is usually you), so we skip our own name.
    """
    for participant in _participants_of(call):
        name = participant.display_name
        if name and (not me or name.lower() != me.lower()):
            return f'Call with {name}'
    return None


def matches(call: Call, needle: str) -> bool:
    """Case-insensitive match against title, participant names/MRIs or call id."""
    lowered = needle.lower()
    values = [call.call_id, call.title or '']
    for participant in _participants_of(call):
        values.append(participant.display_name or '')
        values.append(participant.id or '')
    return any(lowered in value.lower() for value in values if value)
