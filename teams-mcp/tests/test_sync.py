"""Sync orchestration: counts, tolerance of per-item failures, state."""

from datetime import datetime, timedelta, timezone

import pytest

from teams_browser.client import TeamsClient
from teams_browser.errors import TranscriptUnavailable
from teams_browser.models import (
    Call,
    ChatMessage,
    Conversation,
    Meeting,
    SharedFile,
    Transcript,
    TranscriptEntry,
)
from teams_browser.store import Store
from teams_browser.sync import sync

PAST = datetime.now(tz=timezone.utc) - timedelta(days=1)
FUTURE = datetime.now(tz=timezone.utc) + timedelta(days=1)


class FakeClient:
    def __init__(self, *, transcript_error=None, ad_hoc_calls=None):
        self.transcript_error = transcript_error
        self.ad_hoc_calls = ad_hoc_calls or []
        self.calls = []

    def list_meetings(self, *, start, end, limit):
        self.calls.append("meetings")
        return [
            Meeting(id="m1", subject="Has transcript", start_time=PAST, thread_id="19:t1@thread.v2"),
            Meeting(id="m2", subject="Future", start_time=FUTURE, thread_id="19:t2@thread.v2"),
            Meeting(id="m3", subject="No thread", start_time=PAST),
        ]

    def get_transcript(self, thread_id, *, subject=None, meeting_date=None):
        self.calls.append(f"transcript:{thread_id}")
        if self.transcript_error:
            raise self.transcript_error
        return Transcript(
            thread_id=thread_id,
            meeting_subject=subject,
            entries=[TranscriptEntry(start="00:00:00", end="00:00:05", speaker="Ada", text="hi")],
            speakers=["Ada"],
        )

    def list_files(self, *, top):
        self.calls.append("files")
        return [SharedFile(id="f1", name="Deck.pptx"), SharedFile(id="f2", name="Rec.mp4", is_recording=True)]

    def list_conversations(self, *, top):
        self.calls.append("conversations")
        return [Conversation(id="c1", kind="channel", topic="Data Platform", last_message_at=PAST)]

    def list_calls(self, *, limit=100):
        self.calls.append("calls")
        return list(self.ad_hoc_calls)

    def list_messages(self, conversation_id, *, page_size):
        self.calls.append(f"messages:{conversation_id}")
        return [ChatMessage(id="msg1", conversation_id=conversation_id, sender="Ada", text="budget approved")]


@pytest.fixture()
def store(tmp_path):
    with Store(tmp_path / "archive.db") as s:
        yield s


def test_sync_populates_everything(store):
    report = sync(FakeClient(), store, pause=0, days_back=7)
    assert report.meetings == 3
    assert report.transcripts == 1  # future meeting and no-thread meeting skipped
    assert report.conversations == 1
    assert report.messages == 1
    assert report.files == 2
    assert report.errors == []
    assert store.get_state("last_sync") is not None


def test_sync_skips_unavailable_transcripts_quietly(store):
    report = sync(FakeClient(transcript_error=TranscriptUnavailable("none")), store, pause=0)
    assert report.transcripts == 0
    assert report.skipped == 1
    assert report.errors == []


def test_sync_tolerates_unexpected_errors(store):
    report = sync(FakeClient(transcript_error=RuntimeError("boom")), store, pause=0)
    assert report.transcripts == 0
    assert len(report.errors) == 1
    assert "boom" in report.errors[0]
    # The rest of the sync still completed.
    assert report.messages == 1


def test_sync_can_skip_sections(store):
    client = FakeClient()
    report = sync(client, store, pause=0, include_chats=False, include_files=False, include_transcripts=False)
    assert report.transcripts == 0
    assert report.messages == 0
    assert report.files == 0
    assert "conversations" not in client.calls


def test_synced_data_is_searchable(store):
    sync(FakeClient(), store, pause=0)
    assert store.search("budget", sources=["chat"])
    assert store.search("Deck", sources=["file"])
    assert store.search("Has transcript", sources=["meeting"])


def test_sync_fetches_ad_hoc_call_transcripts(store):
    client = FakeClient(
        ad_hoc_calls=[
            Call(
                call_id="c9",
                thread_id="19:call@unq.gbl.spaces",
                title="Call with Tian, Xueli (Kevin)",
                has_transcript=True,
                start_time=PAST,
            )
        ]
    )
    report = sync(client, store, pause=0)
    assert report.calls == 1
    assert report.transcripts == 2  # the calendar meeting + the ad-hoc call
    assert client.calls.count("transcript:19:call@unq.gbl.spaces") == 1
    assert store.transcript("19:call@unq.gbl.spaces") is not None


def test_sync_dedupes_call_threads_already_fetched_from_the_calendar(store):
    client = FakeClient(
        ad_hoc_calls=[
            Call(call_id="c9", thread_id="19:t1@thread.v2", has_transcript=True, start_time=PAST)
        ]
    )
    report = sync(client, store, pause=0)
    assert report.transcripts == 1
    assert client.calls.count("transcript:19:t1@thread.v2") == 1


def test_sync_can_skip_ad_hoc_calls(store):
    client = FakeClient(
        ad_hoc_calls=[Call(call_id="c9", thread_id="19:call@x", has_transcript=True, start_time=PAST)]
    )
    report = sync(client, store, pause=0, include_calls=False)
    assert report.calls == 0
    assert "calls" not in client.calls


def test_client_search_delegates_to_store(tmp_path):
    client = TeamsClient(paths=_paths(tmp_path))
    client.store.upsert_meetings([Meeting(id="m1", subject="Design Review")])
    hits = client.search("Design", sources=["meeting"])
    assert hits and hits[0].title == "Design Review"
    client.close()


def _paths(tmp_path):
    from teams_browser.config import Paths

    root = tmp_path / "home"
    return Paths(
        root=root,
        profile_dir=root / "profile",
        session_file=root / "session.enc",
        cache_file=root / "tokens.enc",
        key_file=root / "secret.key",
        db_file=root / "archive.db",
    )
