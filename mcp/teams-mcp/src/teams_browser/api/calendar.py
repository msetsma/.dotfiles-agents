"""Calendar / meeting listing via the Teams middle-tier (``mt``) API.

Endpoint (partitioned tenants)::

    GET {teams_base}/api/mt/part/{region-partition}/v2.1/me/calendars/calendarView

Non-partitioned tenants drop the ``/part`` segment. Auth is the Skype token
cookie plus a Skype Spaces bearer token. This is the same call the Teams web
client makes to render the calendar, so it needs no Graph permission.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from ..models import Meeting, RegionConfig, TokenSet
from .http import HttpClient
from .util import iso_utc, parse_dt


_SELECT_FIELDS = [
    'endTime',
    'eventTimeZone',
    'eventType',
    'iCalUid',
    'isAllDayEvent',
    'isAppointment',
    'isCancelled',
    'isOnlineMeeting',
    'isOrganizer',
    'isPrivate',
    'lastModifiedTime',
    'location',
    'myResponseType',
    'objectId',
    'organizerAddress',
    'organizerName',
    'showAs',
    'skypeTeamsData',
    'skypeTeamsMeetingUrl',
    'startTime',
    'subject',
]

_FILTER = 'isAppointment eq false and isAllDayEvent eq false and isCancelled eq false'


def _calendar_view_url(region: RegionConfig) -> str:
    base = region.teams_base_url
    if region.has_partition:
        return f'{base}/api/mt/part/{region.region_partition}/v2.1/me/calendars/calendarView'
    return f'{base}/api/mt/{region.region_partition}/v2.1/me/calendars/calendarView'


def _headers(region: RegionConfig, tokens: TokenSet) -> dict[str, str]:
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Origin': region.teams_base_url,
        'Referer': f'{region.teams_base_url}/',
    }
    if tokens.skype_token:
        headers['Authentication'] = f'skypetoken={tokens.skype_token}'
    if tokens.spaces:
        headers['Authorization'] = f'Bearer {tokens.spaces.token}'
    return headers


def list_meetings(
    region: RegionConfig,
    tokens: TokenSet,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 50,
    client: HttpClient | None = None,
) -> list[Meeting]:
    now = datetime.now(tz=UTC)
    start = start or now
    end = end or (start + timedelta(days=7))

    params = {
        'startDate': iso_utc(start),
        'endDate': iso_utc(end),
        '$top': str(limit),
        '$count': 'true',
        '$skip': '0',
        '$orderby': 'startTime asc',
        '$filter': _FILTER,
        '$select': ','.join(_SELECT_FIELDS),
    }

    owns_client = client is None
    client = client or HttpClient()
    try:
        data = client.get_json(_calendar_view_url(region), headers=_headers(region, tokens), params=params)
    finally:
        if owns_client:
            client.close()

    raw = data.get('value') or []
    return [parse_meeting(item) for item in raw]


def parse_meeting(raw: dict[str, Any]) -> Meeting:
    thread_id = None
    skype_data = raw.get('skypeTeamsData')
    if isinstance(skype_data, str) and skype_data:
        try:
            thread_id = json.loads(skype_data).get('cid')
        except Exception:
            thread_id = None

    response = raw.get('myResponseType')
    if response in ('Accepted', 'Organizer'):
        my_response = 'Accepted'
    elif response in ('Tentative', 'TentativelyAccepted'):
        my_response = 'Tentative'
    elif response == 'Declined':
        my_response = 'Declined'
    else:
        my_response = 'None'

    show_as_raw = raw.get('showAs')
    show_as = {
        'Free': 'Free',
        'Busy': 'Busy',
        'Tentative': 'Tentative',
        'Oof': 'OutOfOffice',
        'OutOfOffice': 'OutOfOffice',
    }.get(show_as_raw, 'Unknown')

    return Meeting(
        id=raw.get('objectId') or '',
        subject=raw.get('subject') or '(No subject)',
        start_time=parse_dt(raw.get('startTime')),
        end_time=parse_dt(raw.get('endTime')),
        organizer_name=raw.get('organizerName'),
        organizer_address=raw.get('organizerAddress'),
        location=raw.get('location'),
        is_online_meeting=raw.get('isOnlineMeeting') is True,
        join_url=raw.get('skypeTeamsMeetingUrl'),
        thread_id=thread_id,
        my_response=my_response,
        show_as=show_as,
        is_organizer=raw.get('isOrganizer') is True,
        event_type=raw.get('eventType') or 'Single',
    )
