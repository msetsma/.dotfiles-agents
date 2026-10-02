from teams_browser.models import TranscriptEntry
from teams_browser.transcript_text import format_transcript, to_vtt


def _e(speaker, text, start='0', end='1000'):
    return TranscriptEntry(speaker=speaker, text=text, start=start, end=end)


def test_timecode_offsets_are_formatted():
    entries = [_e('Ada', 'Hi', start='00:00:05.4230779', end='00:00:22.2230779')]
    assert format_transcript(entries, timestamps=True).startswith('[00:00:05.423] Ada: Hi')
    vtt = to_vtt(entries)
    assert '00:00:05.423 --> 00:00:22.223' in vtt


def test_merges_consecutive_same_speaker():
    entries = [_e('Ada', 'Hello'), _e('Ada', 'world'), _e('Bob', 'Hi')]
    assert format_transcript(entries) == 'Ada: Hello world\nBob: Hi'


def test_skips_empty_text():
    entries = [_e('Ada', 'Hi'), _e('Bob', '  '), _e('Bob', 'there')]
    assert format_transcript(entries) == 'Ada: Hi\nBob: there'


def test_timestamps():
    entries = [_e('Ada', 'Hi', start='65000')]
    assert format_transcript(entries, timestamps=True) == '[00:01:05.000] Ada: Hi'


def test_vtt_output():
    entries = [_e('Ada', 'Hi', start='0', end='58400000')]
    vtt = to_vtt(entries)
    assert vtt.startswith('WEBVTT')
    assert '<v Ada>Hi' in vtt
