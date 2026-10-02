import json

import pytest
from teams_browser.api.transcript import _normalise_offset, get_transcript
from teams_browser.errors import TranscriptUnavailable
from teams_browser.models import TokenInfo, TokenSet

from .helpers import make_jwt


ENTRIES = [
    {'startOffset': '00000', 'endOffset': '58400000', 'speakerDisplayName': 'Ada', 'text': 'Hi'},
    {'startOffset': '58400000', 'endOffset': '81200000', 'speakerDisplayName': 'Ada', 'text': 'there'},
    {'startOffset': '81200000', 'endOffset': '120000000', 'speakerDisplayName': 'Bob', 'text': 'Hello'},
]


def _payload(entries):
    return {
        'value': [
            {
                'ItemProperties': {
                    'Default': {
                        'TranscriptJson': json.dumps({'entries': entries}),
                        'RecordingStartDateTime': '2026-02-18T15:00:00Z',
                        'RecordingEndDateTime': '2026-02-18T16:00:00Z',
                    }
                },
                'Visualization': {'Title': 'Design Review'},
            }
        ]
    }


class _StubClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.payload


def _tokens():
    return TokenSet(substrate=TokenInfo(token=make_jwt({'aud': 'https://substrate.office.com'}), expires_at=None))


def test_get_transcript_parses_entries():
    client = _StubClient(_payload(ENTRIES))
    transcript = get_transcript(_tokens(), '19:meeting_xyz@thread.v2', client=client)
    assert transcript.meeting_subject == 'Design Review'
    assert len(transcript.entries) == 3
    assert transcript.speakers == ['Ada', 'Bob']
    assert transcript.entries[1].start == '5840'
    assert 'WorkingSetFiles' in client.calls[0][0]


def test_empty_entries_raises():
    client = _StubClient(_payload([]))
    with pytest.raises(TranscriptUnavailable):
        get_transcript(_tokens(), 'thread', client=client)


def test_no_items_raises():
    client = _StubClient({'value': []})
    with pytest.raises(TranscriptUnavailable):
        get_transcript(_tokens(), 'thread', client=client)


def test_missing_transcript_json_raises():
    payload = {'value': [{'ItemProperties': {'Default': {}}, 'Visualization': {}}]}
    with pytest.raises(TranscriptUnavailable):
        get_transcript(_tokens(), 'thread', client=_StubClient(payload))


def test_normalise_offset():
    assert _normalise_offset('58400000') == '5840'
    assert _normalise_offset('5840') == '5840'
    assert _normalise_offset('00:00:05.4230779') == '00:00:05.4230779'
    assert _normalise_offset(None) == ''
