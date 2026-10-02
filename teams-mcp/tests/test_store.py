"""Archive store: round-trips, full-text search, safety and stats."""

from datetime import datetime, timedelta, timezone

import pytest

from teams_browser.models import ChatMessage, Conversation, Meeting, SharedFile, Transcript, TranscriptEntry
from teams_browser.store import Store

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def store(tmp_path):
    with Store(tmp_path / "archive.db") as s:
        yield s


def _transcript(thread="19:t@thread.v2", speech="we should ship the roadmap next sprint"):
    return Transcript(
        thread_id=thread,
        meeting_subject="Design Review",
        recording_start=NOW,
        recording_end=NOW + timedelta(hours=1),
        entries=[TranscriptEntry(start="00:00:00", end="00:00:10", speaker="Ada", text=speech)],
        speakers=["Ada"],
    )


def test_transcript_round_trip_preserves_entries(store):
    store.upsert_transcript(_transcript(speech="hello"))
    loaded = store.transcript("19:t@thread.v2")
    assert loaded is not None
    assert loaded.speakers == ["Ada"]
    assert [e.text for e in loaded.entries] == ["hello"]
    assert loaded.meeting_subject == "Design Review"


def test_transcript_search_finds_and_snippets(store):
    store.upsert_transcript(_transcript())
    hits = store.search("roadmap", sources=["transcript"])
    assert len(hits) == 1
    assert hits[0].source == "transcript"
    assert "roadmap" in (hits[0].snippet or "")


def test_search_prefix_matches_partial_word(store):
    store.upsert_transcript(_transcript())
    assert store.search("road", sources=["transcript"])


def test_search_survives_fts_syntax_characters(store):
    store.upsert_transcript(_transcript())
    for query in ['a-b:c*', '"quoted"', 'NEAR(a b)', '*', '   ']:
        store.search(query)


def test_upsert_transcript_replaces_fts_row(store):
    store.upsert_transcript(_transcript(speech="alpha"))
    store.upsert_transcript(_transcript(speech="beta"))
    assert not store.search("alpha", sources=["transcript"])
    assert store.search("beta", sources=["transcript"])


def test_message_search_and_system_exclusion(store):
    store.upsert_messages(
        [
            ChatMessage(id="1", conversation_id="c1", sender="Ada", timestamp=NOW, text="budget approved"),
            ChatMessage(id="2", conversation_id="c1", sender=None, timestamp=NOW, text="budget", is_system=True),
        ]
    )
    hits = store.search("budget", sources=["chat"])
    assert [h.ref for h in hits] == ["1"]


def test_message_search_can_scope_to_conversation(store):
    store.upsert_messages(
        [
            ChatMessage(id="1", conversation_id="c1", sender="Ada", text="alpha"),
            ChatMessage(id="2", conversation_id="c2", sender="Ada", text="alpha"),
        ]
    )
    hits = store.search("alpha", sources=["chat"], conversation_id="c2")
    assert [h.ref for h in hits] == ["2"]


def test_meeting_and_file_like_search(store):
    store.upsert_meetings([Meeting(id="m1", subject="Design Review", start_time=NOW)])
    store.upsert_files([SharedFile(id="f1", name="Design Deck.pptx", kind="PowerPoint")])
    meeting_hits = store.search("Design", sources=["meeting"])
    file_hits = store.search("Design", sources=["file"])
    assert meeting_hits and meeting_hits[0].title == "Design Review"
    assert file_hits and file_hits[0].title == "Design Deck.pptx"


def test_meetings_date_filter(store):
    store.upsert_meetings(
        [
            Meeting(id="m1", subject="Old", start_time=NOW - timedelta(days=30)),
            Meeting(id="m2", subject="New", start_time=NOW),
        ]
    )
    recent = store.meetings(start=NOW - timedelta(days=1), end=NOW + timedelta(days=1))
    assert [m.subject for m in recent] == ["New"]


def test_conversations_ordered_by_recency(store):
    store.upsert_conversations(
        [
            Conversation(id="c1", kind="group", topic="Older", last_message_at=NOW - timedelta(days=1)),
            Conversation(id="c2", kind="channel", topic="Newer", last_message_at=NOW),
        ]
    )
    assert [c.topic for c in store.conversations()] == ["Newer", "Older"]
    assert [c.topic for c in store.conversations(kind="group")] == ["Older"]


def test_files_thread_filter(store):
    store.upsert_files(
        [
            SharedFile(id="f1", name="a.pptx", thread_id="t1"),
            SharedFile(id="f2", name="b.pptx", thread_id="t2"),
        ]
    )
    assert [f.name for f in store.files(thread_id="t1")] == ["a.pptx"]


def test_stats_and_state(store):
    store.upsert_transcript(_transcript())
    store.upsert_meetings([Meeting(id="m1", subject="s")])
    store.set_state("last_sync", NOW.isoformat())
    stats = store.stats()
    assert stats["transcripts"] == 1
    assert stats["meetings"] == 1
    assert stats["last_sync"] == NOW.isoformat()


def test_upsert_meetings_is_idempotent(store):
    store.upsert_meetings([Meeting(id="m1", subject="First")])
    store.upsert_meetings([Meeting(id="m1", subject="Second")])
    loaded = store.meetings()
    assert len(loaded) == 1
    assert loaded[0].subject == "Second"
