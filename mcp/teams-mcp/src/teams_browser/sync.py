"""Incremental sync of meetings, transcripts, calls, chats and files into the archive.

This is the command that makes the archive trustworthy: transcripts expire, chat
history scrolls away, and hitting the internal APIs on every question is slow.
Run it from cron/launchd and everything else (``search``, ``digest``) becomes a
local read.

Failures are tolerated per-item - one dead transcript must not abort a sync.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from .errors import ResourceNotFoundError, TeamsBrowserError, TranscriptUnavailableError
from .models import Meeting, SyncReport
from .store import Store


if TYPE_CHECKING:  # pragma: no cover
    from .client import TeamsClient

_MAX_ERRORS = 20


class _TranscriptJob(NamedTuple):
    thread_id: str | None
    subject: str | None
    started_at: datetime | None
    label: str


def sync(
    client: TeamsClient,
    store: Store,
    *,
    days_back: int = 7,
    days_forward: int = 1,
    include_chats: bool = True,
    include_files: bool = True,
    include_transcripts: bool = True,
    include_calls: bool = True,
    conversation_limit: int = 50,
    messages_per_conversation: int = 50,
    files_limit: int = 100,
    pause: float = 0.15,
    log: Callable[[str], None] | None = None,
) -> SyncReport:
    started = datetime.now(tz=UTC)
    report = SyncReport(started_at=started)
    emit: Callable[[str], None] = log or (lambda _message: None)

    meetings = _sync_meetings(client, store, report, started, days_back, days_forward, emit)

    if include_transcripts:
        targets = _meeting_transcript_targets(meetings)
        if include_calls:
            targets += _call_transcript_targets(client, report, emit)
        _store_transcripts(client, store, report, targets, emit=emit, pause=pause)

    if include_files:
        _sync_files(client, store, report, files_limit, emit)
    if include_chats:
        _sync_chats(client, store, report, conversation_limit, messages_per_conversation, pause, emit)

    report.finished_at = datetime.now(tz=UTC)
    store.set_state('last_sync', report.finished_at.isoformat())
    return report


def _sync_meetings(
    client: TeamsClient,
    store: Store,
    report: SyncReport,
    started: datetime,
    days_back: int,
    days_forward: int,
    emit: Callable[[str], None],
) -> list[Meeting]:
    try:
        meetings = client.list_meetings(
            start=started - timedelta(days=days_back), end=started + timedelta(days=days_forward), limit=200
        )
    except TeamsBrowserError as exc:
        _record(report, emit, 'meetings', exc)
        return []
    report.meetings = store.upsert_meetings(meetings)
    emit(f'meetings: {report.meetings}')
    return meetings


def _meeting_transcript_targets(meetings: list[Meeting]) -> list[_TranscriptJob]:
    # Future meetings have no transcript yet; only past online meetings qualify.
    return [
        _TranscriptJob(m.thread_id, m.subject, m.start_time, 'transcript')
        for m in meetings
        if m.thread_id and m.start_time
    ]


def _call_transcript_targets(
    client: TeamsClient, report: SyncReport, emit: Callable[[str], None]
) -> list[_TranscriptJob]:
    try:
        calls = client.list_calls(limit=100)
    except TeamsBrowserError as exc:
        _record(report, emit, 'calls', exc)
        return []
    report.calls = len(calls)
    emit(f'calls: {report.calls}')
    return [
        _TranscriptJob(c.thread_id, c.title or c.call_id, c.start_time, 'call transcript')
        for c in calls
        if c.has_transcript
    ]


def _store_transcripts(
    client: TeamsClient,
    store: Store,
    report: SyncReport,
    targets: list[_TranscriptJob],
    *,
    emit: Callable[[str], None],
    pause: float,
) -> None:
    """Fetch and archive each target once; a repeated thread id is skipped."""
    seen: set[str] = set()
    for job in targets:
        if not job.thread_id or job.thread_id in seen:
            continue
        if job.started_at and job.started_at > report.started_at:
            continue
        try:
            transcript = client.get_transcript(job.thread_id, subject=job.subject, meeting_date=job.started_at)
        except TranscriptUnavailableError, ResourceNotFoundError:
            report.skipped += 1
            continue
        except Exception as exc:
            _record(report, emit, f'{job.label} {job.subject!r}', exc)
            continue
        store.upsert_transcript(transcript)
        seen.add(job.thread_id)
        report.transcripts += 1
        emit(f'  {job.label}: {job.subject}')
        _nap(pause)


def _sync_files(client: TeamsClient, store: Store, report: SyncReport, limit: int, emit: Callable[[str], None]) -> None:
    try:
        report.files = store.upsert_files(client.list_files(top=limit))
        emit(f'files: {report.files}')
    except TeamsBrowserError as exc:
        _record(report, emit, 'files', exc)


def _sync_chats(
    client: TeamsClient,
    store: Store,
    report: SyncReport,
    conversation_limit: int,
    messages_per_conversation: int,
    pause: float,
    emit: Callable[[str], None],
) -> None:
    try:
        conversations = client.list_conversations(top=conversation_limit)
    except TeamsBrowserError as exc:
        _record(report, emit, 'conversations', exc)
        return
    report.conversations = store.upsert_conversations(conversations)
    emit(f'conversations: {report.conversations}')
    for conversation in conversations:
        try:
            messages = client.list_messages(conversation.id, page_size=messages_per_conversation)
        except Exception as exc:
            _record(report, emit, f'messages {conversation.id}', exc)
            continue
        report.messages += store.upsert_messages(messages)
        _nap(pause)


def _record(report: SyncReport, emit: Callable[[str], None], context: str, exc: Exception) -> None:
    message = f'{context}: {exc}'
    if len(report.errors) < _MAX_ERRORS:
        report.errors.append(message)
    emit(f'  ! {message}')


def _nap(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)
