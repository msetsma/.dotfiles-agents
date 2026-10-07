"""Meeting transcripts via the Substrate ``WorkingSetFiles`` API.

Teams stores the transcript JSON on the recording's SharePoint item and exposes
it through Substrate::

    GET {substrate_base}/api/beta/me/WorkingSetFiles/
        ?$filter=ItemProperties/Default/MeetingThreadId eq '{threadId}'
        &$orderby=FileCreatedTime desc
        &$select=...,ItemProperties/Default/TranscriptJson,...

The transcript arrives embedded as a JSON string, so no Graph and no file
download is involved - the same Substrate token used for search is all we need.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from ..config import substrate_base_url
from ..errors import TranscriptUnavailableError
from ..models import TokenSet, Transcript, TranscriptEntry
from .http import HttpClient, substrate_headers
from .util import iso_utc, parse_dt


_SELECT = (
    'SharePointItem,'
    'Visualization,'
    'ItemProperties/Default/MeetingCallId,'
    'ItemProperties/Default/DriveId,'
    'ItemProperties/Default/RecordingStartDateTime,'
    'ItemProperties/Default/RecordingEndDateTime,'
    'ItemProperties/Default/TranscriptJson,'
    'ItemProperties/Default/DocumentLink'
)


def get_transcript(
    tokens: TokenSet,
    thread_id: str,
    *,
    thread_subject: str | None = None,
    meeting_date: datetime | None = None,
    client: HttpClient | None = None,
) -> Transcript:
    data = _fetch_carrier(tokens, thread_id, meeting_date, client)
    items = data.get('value') or []
    if not items:
        raise TranscriptUnavailableError(
            'No recording/transcript found for this meeting. Transcription may not have '
            'been enabled, or the recording has not finished processing.'
        )
    return _parse_carrier(items[0], thread_id, thread_subject)


def _fetch_carrier(
    tokens: TokenSet, thread_id: str, meeting_date: datetime | None, client: HttpClient | None
) -> dict[str, Any]:
    date_filter = ''
    if meeting_date:
        date_filter = (
            f' AND FileCreatedTime gt {iso_utc(meeting_date - timedelta(days=1))}'
            f' AND FileCreatedTime lt {iso_utc(meeting_date + timedelta(days=1))}'
        )
    params = {
        '$filter': f"ItemProperties/Default/MeetingThreadId eq '{thread_id}'{date_filter}",
        '$orderby': 'FileCreatedTime desc',
        '$select': _SELECT,
    }

    owns_client = client is None
    client = client or HttpClient()
    try:
        return client.get_json(
            f'{substrate_base_url()}/api/beta/me/WorkingSetFiles/', headers=substrate_headers(tokens), params=params
        )
    finally:
        if owns_client:
            client.close()


def _parse_carrier(item: dict[str, Any], thread_id: str, thread_subject: str | None) -> Transcript:
    props = (item.get('ItemProperties') or {}).get('Default') or {}
    visualization = item.get('Visualization') or {}
    transcript_json = props.get('TranscriptJson')
    if not transcript_json:
        raise TranscriptUnavailableError(
            'A recording exists but has no transcript. Transcription may not have been enabled for this meeting.'
        )

    try:
        parsed = json.loads(transcript_json)
    except (TypeError, ValueError) as exc:
        raise TranscriptUnavailableError(f'Could not parse transcript payload: {exc}') from exc

    entries = [_entry(e) for e in parsed.get('entries') or []]
    if not entries:
        raise TranscriptUnavailableError('The transcript is empty - no speech was detected.')

    return Transcript(
        meeting_subject=visualization.get('Title') or thread_subject,
        thread_id=thread_id,
        recording_start=parse_dt(props.get('RecordingStartDateTime')),
        recording_end=parse_dt(props.get('RecordingEndDateTime')),
        entries=entries,
        speakers=list(dict.fromkeys(e.speaker for e in entries if e.speaker)),
    )


def _entry(raw: dict[str, Any]) -> TranscriptEntry:
    return TranscriptEntry(
        start=_normalise_offset(raw.get('startOffset')),
        end=_normalise_offset(raw.get('endOffset')),
        speaker=(raw.get('speakerDisplayName') or '').strip(),
        text=(raw.get('text') or '').strip(),
    )


def _normalise_offset(value: Any) -> str:
    """Normalise a transcript offset.

    Substrate normally returns an ``HH:MM:SS.fffffff`` timecode; some tenants
    return microsecond integers instead, which we trim to milliseconds.
    """
    if not isinstance(value, str):
        return ''
    if ':' in value:
        return value
    return value.removesuffix('0000')
