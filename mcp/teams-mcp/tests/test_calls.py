"""Call history: parsing, joining on CallId, and ad-hoc transcript fallback."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from teams_browser.api.calls import derive_title, list_calls, matches, parse_call_log_message
from teams_browser.client import TeamsClient
from teams_browser.errors import ApiError, ResourceNotFoundError
from teams_browser.models import Call, CallParticipant, RegionConfig, TokenInfo, TokenSet, Transcript, TranscriptEntry

from .helpers import make_jwt


FIXTURES = Path(__file__).parent / 'fixtures'
PAST = datetime.now(tz=UTC) - timedelta(days=1)


def _region() -> RegionConfig:
    return RegionConfig(
        region='amer',
        partition='03',
        region_partition='amer-03',
        has_partition=True,
        middle_tier_url='https://teams.microsoft.com/api/mt/part/amer-03',
        chat_service_url='https://teams.microsoft.com/api/chatsvc/amer',
        csa_service_url='https://teams.microsoft.com/api/csa/amer',
        teams_base_url='https://teams.microsoft.com',
    )


def _tokens() -> TokenSet:
    return TokenSet(
        skype_token='skype-token-value', spaces=TokenInfo(token=make_jwt({'aud': 'spaces'}), expires_at=None)
    )


class _StubClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.payload


class _FailingClient:
    def get_json(self, url, **kwargs):
        raise ApiError(404, 'not found')


def _fixture():
    return json.loads((FIXTURES / 'calllogs_messages.json').read_text())


# -- parsing ----------------------------------------------------------------- #


def test_parse_call_log_message_ignores_unrelated():
    assert parse_call_log_message({'messagetype': 'RichText/Html', 'content': 'hi'}) is None


def test_parse_call_log_entry_captures_participants():
    raw = _fixture()['messages'][1]
    call = parse_call_log_message(raw)
    assert call.call_id == 'call-0001'
    assert call.direction == 'incoming'
    assert call.kind == 'multiParty'
    assert call.start_time is not None
    assert call.originator.display_name == 'Doe, Jane'
    assert len(call.participants) == 2


def test_parse_call_log_event_carries_thread():
    raw = _fixture()['messages'][0]
    call = parse_call_log_message(raw)
    assert call.thread_id.endswith('@unq.gbl.spaces')
    assert call.has_transcript is True
    assert call.has_recording is True


# -- listing / joining ------------------------------------------------------- #


def test_list_calls_joins_events_onto_log_entries():
    client = _StubClient(_fixture())
    calls = list_calls(_region(), _tokens(), client=client)
    assert [c.call_id for c in calls] == ['call-0001', 'call-0002']  # newest first

    recorded = calls[0]
    assert recorded.thread_id.endswith('@unq.gbl.spaces')
    assert recorded.has_transcript is True
    assert recorded.has_recording is True
    assert recorded.start_time.year == 2026

    plain = calls[1]
    assert plain.thread_id is None
    assert plain.has_transcript is False
    assert plain.direction == 'outgoing'


def test_list_calls_hits_the_call_logs_conversation():
    client = _StubClient(_fixture())
    list_calls(_region(), _tokens(), client=client)
    assert '48:calllogs/messages' in client.calls[0][0]


def test_list_calls_missing_conversation_is_empty():
    assert list_calls(_region(), _tokens(), client=_FailingClient()) == []


# -- matching / titling ------------------------------------------------------ #


def _call(**kwargs) -> Call:
    base = dict(
        call_id='call-1',
        thread_id='19:x@unq.gbl.spaces',
        has_transcript=True,
        originator=CallParticipant(id='8:orgid:1', display_name='Tian, Xueli (Kevin)'),
        target=CallParticipant(id='8:orgid:2', display_name='Setsma, Mitchell'),
    )
    base.update(kwargs)
    return Call(**base)


def test_matches_participant_name_and_call_id():
    call = _call()
    assert matches(call, 'kevin')
    assert matches(call, 'TIAN')
    assert matches(call, 'call-1')
    assert not matches(call, 'nobody')


def test_derive_title_skips_me():
    assert derive_title(_call(), me='Setsma, Mitchell') == 'Call with Tian, Xueli (Kevin)'
    anonymous = _call(originator=None, target=None)
    assert derive_title(anonymous, me='Setsma, Mitchell') is None


# -- client fallback --------------------------------------------------------- #


class _FakeTeamsClient(TeamsClient):
    def __init__(self, calls):
        super().__init__()
        self._calls = calls
        self.captured = None

    def find_meetings(self, *args, **kwargs):
        return []

    def list_calls(self, *, limit=100, since=None, until=None, participant=None, has_transcript=None):
        return self._calls

    def get_transcript(self, thread_id, *, subject=None, meeting_date=None):
        self.captured = (thread_id, subject, meeting_date)
        return Transcript(
            thread_id=thread_id,
            meeting_subject=subject,
            entries=[TranscriptEntry(start='00:00:00', end='00:00:05', speaker='Ada', text='hi')],
            speakers=['Ada'],
        )


def test_get_transcript_for_falls_back_to_ad_hoc_call():
    client = _FakeTeamsClient([_call(title='Call with Tian, Xueli (Kevin)', start_time=PAST)])
    try:
        transcript = client.get_transcript_for('Kevin')
    finally:
        client.close()
    assert transcript.thread_id == '19:x@unq.gbl.spaces'
    assert client.captured[0] == '19:x@unq.gbl.spaces'
    assert client.captured[1] == 'Call with Tian, Xueli (Kevin)'


def test_get_transcript_for_skips_calls_without_a_transcript():
    client = _FakeTeamsClient([_call(thread_id=None, has_transcript=False)])
    try:
        with pytest.raises(ResourceNotFoundError, match='No meetings or calls'):
            client.get_transcript_for('Kevin')
    finally:
        client.close()
