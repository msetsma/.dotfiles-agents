"""Transcript analytics: timecodes, merging, slicing, talk time, digest."""

from datetime import datetime, timezone

from teams_browser.analytics import (
    analyse,
    format_seconds,
    merge_consecutive,
    render_digest,
    slice_entries,
    speaker_stats,
    to_seconds,
)
from teams_browser.models import Meeting, Transcript, TranscriptEntry


def _entries():
    return [
        TranscriptEntry(start="00:00:00", end="00:00:10", speaker="Ada", text="one two"),
        TranscriptEntry(start="00:00:10", end="00:00:20", speaker="Ada", text="three"),
        TranscriptEntry(start="00:00:20", end="00:00:30", speaker="Bob", text="four five six"),
    ]


def test_to_seconds_formats():
    assert to_seconds("00:00:05.4230779") == 5.423
    assert to_seconds("01:02:03") == 3723
    assert to_seconds(58400000) == 58400.0
    assert to_seconds("5840") == 5.84
    assert to_seconds(None) is None
    assert to_seconds("") is None


def test_format_seconds():
    assert format_seconds(65) == "1m05s"
    assert format_seconds(30) == "0m30s"
    assert format_seconds(3661) == "1h01m"


def test_merge_consecutive_collapses_same_speaker():
    merged = merge_consecutive(_entries())
    assert [e.speaker for e in merged] == ["Ada", "Bob"]
    assert merged[0].text == "one two three"
    assert merged[0].end == "00:00:20"


def test_slice_entries_window():
    window = slice_entries(_entries(), start="00:00:08", end="00:00:25")
    assert [e.speaker for e in window] == ["Ada", "Ada", "Bob"]
    assert [e.speaker for e in merge_consecutive(window)] == ["Ada", "Bob"]


def test_slice_entries_drops_entries_before_window():
    window = slice_entries(_entries(), start="00:00:25")
    assert [e.speaker for e in window] == ["Bob"]


def test_slice_entries_drops_entries_after_window():
    window = slice_entries(_entries(), end="00:00:05")
    assert [e.speaker for e in window] == ["Ada"]


def test_slice_entries_no_window_returns_all():
    assert len(slice_entries(_entries())) == 3


def test_speaker_stats_talk_time_and_share():
    stats, total = speaker_stats(_entries())
    assert total == 30.0
    by_name = {s.speaker: s for s in stats}
    assert by_name["Ada"].talk_seconds == 20.0
    assert by_name["Ada"].turns == 1
    assert by_name["Bob"].words == 3
    assert by_name["Ada"].share == 0.6667
    assert stats[0].speaker == "Ada"  # sorted by talk time


def test_analyse_uses_recording_span_for_duration():
    transcript = Transcript(
        thread_id="19:t@thread.v2",
        meeting_subject="Design Review",
        recording_start=datetime(2026, 9, 15, 20, 30, tzinfo=timezone.utc),
        recording_end=datetime(2026, 9, 15, 21, 30, tzinfo=timezone.utc),
        entries=_entries(),
    )
    analytics = analyse(transcript)
    assert analytics.duration_seconds == 3600.0
    assert analytics.entry_count == 3
    assert analytics.word_count == 6


def test_render_digest_can_omit_transcripts():
    meeting = Meeting(id="1", subject="Design Review", start_time=datetime(2026, 9, 15, tzinfo=timezone.utc))
    transcript = Transcript(thread_id="19:t@thread.v2", entries=_entries(), speakers=["Ada", "Bob"])

    with_bodies = render_digest(title="T", sections=[(meeting, transcript, analyse(transcript))])
    without = render_digest(
        title="T",
        sections=[(meeting, transcript, analyse(transcript))],
        include_transcripts=False,
    )
    assert "<details>" in with_bodies
    assert "<details>" not in without
    assert "Design Review" in without
    assert "Ada" in without


def test_render_digest_handles_missing_transcript():
    meeting = Meeting(id="1", subject="Solo", start_time=datetime(2026, 9, 15, tzinfo=timezone.utc))
    rendered = render_digest(title="T", sections=[(meeting, None, None)])
    assert "not available" in rendered
