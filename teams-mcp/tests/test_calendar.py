import json

from teams_browser.api.calendar import parse_meeting

SAMPLE = {
    'objectId': 'abc-123',
    'subject': 'Design Review',
    'startTime': '2026-02-18T15:00:00Z',
    'endTime': '2026-02-18T16:00:00Z',
    'organizerName': 'Ada Lovelace',
    'organizerAddress': 'ada@example.com',
    'location': 'Teams Meeting',
    'isOnlineMeeting': True,
    'skypeTeamsMeetingUrl': 'https://teams.microsoft.com/l/meetup-join/19%3ameeting_xyz',
    'skypeTeamsData': json.dumps({'cid': '19:meeting_xyz@thread.v2'}),
    'myResponseType': 'Organizer',
    'showAs': 'Busy',
    'isOrganizer': True,
    'eventType': 'Single',
}


def test_parse_meeting_full():
    meeting = parse_meeting(SAMPLE)
    assert meeting.id == 'abc-123'
    assert meeting.subject == 'Design Review'
    assert meeting.thread_id == '19:meeting_xyz@thread.v2'
    assert meeting.is_online_meeting is True
    assert meeting.my_response == 'Accepted'
    assert meeting.show_as == 'Busy'
    assert meeting.is_organizer is True
    assert meeting.start_time is not None


def test_parse_meeting_minimal():
    meeting = parse_meeting({'objectId': '1'})
    assert meeting.subject == '(No subject)'
    assert meeting.thread_id is None
    assert meeting.my_response == 'None'
    assert meeting.show_as == 'Unknown'


def test_parse_meeting_bad_skype_data():
    meeting = parse_meeting({'objectId': '1', 'skypeTeamsData': 'not-json'})
    assert meeting.thread_id is None
