"""Opt-in live smoke test.

Requires a captured session (`teams-browser login`) and ``TEAMS_BROWSER_LIVE=1``.
Never runs in CI by default.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from teams_browser.analytics import analyse
from teams_browser.client import TeamsClient

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.environ.get('TEAMS_BROWSER_LIVE') != '1', reason='set TEAMS_BROWSER_LIVE=1 and run `teams-browser login` first'
)
def test_live_list_meetings():
    with TeamsClient() as client:
        meetings = client.list_meetings(
            start=datetime.now(tz=timezone.utc) - timedelta(days=14),
            end=datetime.now(tz=timezone.utc) + timedelta(days=14),
            limit=5,
        )
    assert isinstance(meetings, list)


@pytest.mark.skipif(
    os.environ.get('TEAMS_BROWSER_LIVE') != '1', reason='set TEAMS_BROWSER_LIVE=1 and run `teams-browser login` first'
)
def test_live_status():
    with TeamsClient() as client:
        status = client.status()
    assert 'tokens' in status


@pytest.mark.skipif(
    os.environ.get('TEAMS_BROWSER_LIVE') != '1', reason='set TEAMS_BROWSER_LIVE=1 and run `teams-browser login` first'
)
def test_live_chats_and_messages():
    with TeamsClient() as client:
        conversations = client.list_conversations(top=5)
        assert conversations, 'expected at least one conversation'
        assert all(c.id for c in conversations)
        messages = client.list_messages(conversations[0].id, page_size=5)
        assert isinstance(messages, list)


@pytest.mark.skipif(
    os.environ.get('TEAMS_BROWSER_LIVE') != '1', reason='set TEAMS_BROWSER_LIVE=1 and run `teams-browser login` first'
)
def test_live_files_are_classified():
    with TeamsClient() as client:
        files = client.list_files(top=10)
    assert isinstance(files, list)
    assert all(f.kind or f.extension for f in files)


@pytest.mark.skipif(
    os.environ.get('TEAMS_BROWSER_LIVE') != '1', reason='set TEAMS_BROWSER_LIVE=1 and run `teams-browser login` first'
)
def test_live_transcript_analytics():
    with TeamsClient() as client:
        meetings = client.list_meetings(
            start=datetime.now(tz=timezone.utc) - timedelta(days=14), end=datetime.now(tz=timezone.utc), limit=50
        )
        for meeting in meetings:
            if not meeting.thread_id or not meeting.start_time:
                continue
            try:
                transcript = client.get_transcript(
                    meeting.thread_id, subject=meeting.subject, meeting_date=meeting.start_time
                )
            except Exception:  # noqa: BLE001 - live smoke test: try the next meeting
                continue
            analytics = analyse(transcript)
            assert analytics.entry_count == len(transcript.entries)
            assert analytics.speakers
            return
    pytest.skip('no transcript available to analyse')


@pytest.mark.skipif(
    os.environ.get('TEAMS_BROWSER_LIVE') != '1', reason='set TEAMS_BROWSER_LIVE=1 and run `teams-browser login` first'
)
def test_live_sync_into_archive(tmp_path):
    from teams_browser.config import Paths
    from teams_browser.store import Store

    # Reuse the real session, but keep the archive throwaway.
    default = Paths.default()
    paths = Paths(
        root=default.root,
        profile_dir=default.profile_dir,
        session_file=default.session_file,
        cache_file=default.cache_file,
        key_file=default.key_file,
        db_file=tmp_path / 'archive.db',
    )
    with TeamsClient(paths=paths) as client, Store(paths.db_file) as store:
        report = client.sync(
            days_back=2,
            days_forward=0,
            include_transcripts=False,
            conversation_limit=3,
            messages_per_conversation=5,
            pause=0,
        )
        assert report.errors == []
        stats = store.stats()
        assert stats['meetings'] == report.meetings
        assert stats['messages'] == report.messages
        assert store.get_state('last_sync') is not None
