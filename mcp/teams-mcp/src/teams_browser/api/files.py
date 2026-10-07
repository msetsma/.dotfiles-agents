"""Files shared in meetings and chats, via the Substrate ``WorkingSetFiles`` API.

This is the same surface that carries transcripts, so one call returns
documents, images, links and (optionally) recordings::

    GET {substrate}/api/beta/me/WorkingSetFiles/?$select=...,Visualization,...

``Visualization.Type`` classifies each item (``Excel``, ``Pdf``, ``WebLink``,
``Video`` ...) and ``ItemProperties/Default/MeetingThreadId`` ties meeting files
back to their meeting. Recordings are video/audio material; we deliberately
surface their *metadata only* and never download content.
"""

from __future__ import annotations

from typing import Any

from ..config import substrate_base_url
from ..models import SharedFile, TokenSet
from .http import HttpClient, substrate_headers
from .util import parse_dt


_SELECT = (
    'Visualization,'
    'FileName,'
    'FileExtension,'
    'FileCreatedTime,'
    'LastModifiedDateTime,'
    'ItemProperties/Default/MeetingThreadId,'
    'ItemProperties/Default/DocumentLink,'
    'ItemProperties/Default/RecordingStartDateTime'
)

_RECORDING_EXTENSIONS = {'mp4', 'm4a', 'mov', 'wav', 'avi', 'mkv'}
_RECORDING_KINDS = {'Video', 'Audio'}


def is_recording(extension: str | None, kind: str | None) -> bool:
    ext = (extension or '').lower().lstrip('.')
    return ext in _RECORDING_EXTENSIONS or kind in _RECORDING_KINDS


def parse_file(raw: dict[str, Any]) -> SharedFile:
    visual = raw.get('Visualization') or {}
    props = (raw.get('ItemProperties') or {}).get('Default') or {}
    sharepoint = raw.get('SharePointItem') or {}
    extension = raw.get('FileExtension') or None
    kind = visual.get('Type') or None
    url = visual.get('AccessUrl') or sharepoint.get('FileUrl') or sharepoint.get('DefaultEncodingUrl')
    return SharedFile(
        id=str(raw.get('Id') or raw.get('@odata.id') or ''),
        name=raw.get('FileName') or visual.get('Title') or None,
        extension=extension,
        kind=kind,
        created_at=parse_dt(raw.get('FileCreatedTime')),
        modified_at=parse_dt(raw.get('LastModifiedDateTime')),
        url=url,
        preview_url=visual.get('PreviewImageUrl'),
        thread_id=props.get('MeetingThreadId'),
        is_recording=is_recording(extension, kind),
    )


def list_files(
    tokens: TokenSet,
    *,
    top: int = 50,
    thread_id: str | None = None,
    include_recordings: bool = True,
    client: HttpClient | None = None,
) -> list[SharedFile]:
    params: dict[str, str] = {'$top': str(top), '$orderby': 'FileCreatedTime desc', '$select': _SELECT}
    if thread_id:
        params['$filter'] = f"ItemProperties/Default/MeetingThreadId eq '{thread_id}'"

    owns = client is None
    client = client or HttpClient()
    try:
        data = client.get_json(
            f'{substrate_base_url()}/api/beta/me/WorkingSetFiles/', headers=substrate_headers(tokens), params=params
        )
    finally:
        if owns:
            client.close()

    files = [parse_file(item) for item in (data.get('value') or [])]
    if not include_recordings:
        files = [f for f in files if not f.is_recording]
    return files


def list_thread_files(
    tokens: TokenSet, thread_id: str, *, include_recordings: bool = False, client: HttpClient | None = None
) -> list[SharedFile]:
    """Files shared in a specific meeting (recordings excluded by default)."""
    return list_files(tokens, thread_id=thread_id, include_recordings=include_recordings, client=client)
