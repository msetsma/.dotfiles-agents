"""Contract tests for the undocumented internal endpoints.

These are tripwires, not integration tests. Each assertion names a field that a
parser actually reads, so if the upstream API (or a regenerated fixture) drops or
renames it, the failure says exactly which assumption broke rather than surfacing
as a confusing KeyError in production.
"""

import json
import re
from pathlib import Path

FIXTURES = Path(__file__).parent / 'fixtures'


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


def _dig(obj, *path):
    for key in path:
        obj = obj[key]
    return obj


# -- chatsvc ---------------------------------------------------------------- #


def test_conversation_contract():
    conversations = _load('chatsvc_conversations.json')['conversations']

    channel = conversations[0]
    assert _dig(channel, 'threadProperties', 'spaceThreadTopic')
    assert _dig(channel, 'threadProperties', 'groupId')
    assert 'tacv2' in channel['id']
    assert _dig(channel, 'properties', 'favorite')
    assert _dig(channel, 'lastMessage', 'composetime')
    assert _dig(channel, 'lastMessage', 'imdisplayname')
    assert _dig(channel, 'lastMessage', 'content')

    meeting = conversations[1]
    assert _dig(meeting, 'threadProperties', 'topic')
    assert meeting['id'].startswith('19:meeting_')


def test_message_contract():
    messages = _load('chatsvc_messages.json')['messages']

    rich = messages[0]
    for field in ('id', 'conversationid', 'messagetype', 'contenttype', 'imdisplayname', 'composetime', 'content'):
        assert field in rich, f'chatsvc message lost required field {field!r}'
    assert '<at' in rich['content'] and '<blockquote' in rich['content']
    # `files` arrives as a JSON *string*, not an object - parsers rely on that.
    assert isinstance(rich['properties']['files'], str)
    json.loads(rich['properties']['files'])
    # `emotions` is already a list of {key, users}.
    assert rich['properties']['emotions'][0]['key']
    assert 'users' in rich['properties']['emotions'][0]

    assert messages[2]['messagetype'].startswith('ThreadActivity/')


# -- WorkingSetFiles --------------------------------------------------------- #


def test_working_set_files_contract():
    values = _load('working_set_files.json')['value']

    for item in values:
        assert 'Id' in item
        assert 'FileName' in item
        assert 'FileExtension' in item
        assert 'FileCreatedTime' in item
        assert 'Visualization' in item

    doc = values[0]
    assert doc['Visualization']['Type'] == 'Excel'
    assert doc['Visualization']['AccessUrl']

    recording = values[1]
    assert recording['FileExtension'] == 'mp4'
    assert recording['Visualization']['Type'] == 'Video'
    assert _dig(recording, 'ItemProperties', 'Default', 'MeetingThreadId')
    assert _dig(recording, 'ItemProperties', 'Default', 'RecordingStartDateTime')


# -- call logs --------------------------------------------------------------- #


def test_call_logs_contract():
    messages = _load('calllogs_messages.json')['messages']

    event = messages[0]
    assert event['messagetype'].startswith('RichText/Media_CallLog')
    payload = json.loads(event['content'])
    for field in ('CallId', 'ThreadId', 'ContentTypes'):
        assert field in payload, f'call-log event lost required field {field!r}'
    assert payload['ThreadId']

    entry = json.loads(messages[1]['properties']['call-log'])
    for field in ('callId', 'startTime', 'endTime', 'callDirection', 'callType', 'callState', 'originatorParticipant'):
        assert field in entry, f'call-log entry lost required field {field!r}'
    assert entry['originatorParticipant']['displayName']
    # `call-log` arrives as a JSON *string* inside properties.
    assert isinstance(messages[1]['properties']['call-log'], str)


# -- calendar ---------------------------------------------------------------- #


def test_calendar_view_contract():
    values = _load('calendar_view.json')['value']
    for item in values:
        for field in ('objectId', 'subject', 'startTime', 'endTime', 'myResponseType', 'showAs', 'isOnlineMeeting'):
            assert field in item, f'calendarView lost required field {field!r}'

    online = values[0]
    assert online['isOnlineMeeting'] is True
    # The thread id is buried inside a JSON string.
    assert json.loads(online['skypeTeamsData'])['cid'].startswith('19:meeting_')
    assert online['skypeTeamsMeetingUrl']


# -- cross-cutting ---------------------------------------------------------- #


def test_fixtures_contain_no_real_tenant_data():
    """Fixtures must stay scrubbed: placeholder emails and example hosts only."""
    for path in FIXTURES.glob('*.json'):
        text = path.read_text().lower()
        for domain in re.findall(r'@([a-z0-9.-]+\.[a-z]{2,})', text):
            ok = domain == 'example.com' or domain.endswith(('.gbl.spaces', 'thread.tacv'))
            assert ok, f'{path.name}: non-placeholder email domain {domain!r}'
        for host in re.findall(r'://([a-z0-9.-]*sharepoint\.com)/', text):
            assert host == 'example.sharepoint.com', f'{path.name}: live SharePoint host {host!r}'


def test_all_expected_fixtures_present():
    names = {p.name for p in FIXTURES.glob('*.json')}
    assert {
        'chatsvc_conversations.json',
        'chatsvc_messages.json',
        'calllogs_messages.json',
        'working_set_files.json',
        'calendar_view.json',
    } <= names
