"""Model Context Protocol server (stdio) exposing the same core as the CLI.

Run with teams-browser-mcp. Tools are read-only against Teams itself - they
only ever GET - and share the local browser session captured by
teams-browser login. A few tools (sync_archive, save_transcript, start_login)
write to *local disk only* or open a local browser window, which is why they
are annotated differently from the rest.

Besides tools the server exposes resources (today's meetings, archive stats,
recent transcripts) so a client can attach context without a tool round-trip,
and prompts for the common workflows.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from ..analytics import analyse, render_digest
from ..client import TeamsClient
from ..config import Paths
from ..digest import build_digest
from ..errors import AuthRequiredError, ResourceNotFoundError
from ..store import Store
from ..transcript_text import to_markdown


def _client() -> TeamsClient:
    # Never launch a headless browser from a tool call: a dead session would
    # otherwise block the call for ~60s and trip the MCP client's timeout.
    # The fast HTTP refresh still runs; if it fails the tool errors out fast
    # with instructions to re-login.
    return TeamsClient(auto_refresh=True, browser_refresh=False)


def _parse_day(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return datetime.combine(parsed, time.min, tzinfo=UTC)


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode='json')


def _resolve_transcript(client: TeamsClient, subject: str | None, date_str: str | None, thread_id: str | None):
    """Fetch by explicit thread id, else by subject (meetings and calls)."""
    if thread_id:
        return client.get_transcript(
            thread_id, subject=subject, meeting_date=_parse_day(date_str) if date_str else None
        )
    if not subject:
        raise ValueError('Provide a subject or a thread_id.')
    return client.get_transcript_for(subject, on_date=_parse_day(date_str))


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #


def list_meetings(start_date: str | None = None, end_date: str | None = None, limit: int = 25) -> dict[str, Any]:
    """List Teams meetings in a date range.

    Args:
        start_date: Start date (YYYY-MM-DD). Defaults to today.
        end_date: End date (YYYY-MM-DD). Defaults to start + 7 days.
        limit: Maximum number of meetings to return.
    """
    with _client() as client:
        start = _parse_day(start_date) or datetime.now(tz=UTC)
        end = _parse_day(end_date) or (start + timedelta(days=7))
        meetings = client.list_meetings(start=start, end=end, limit=limit)
        return {'count': len(meetings), 'meetings': [_dump(m) for m in meetings]}


def get_meeting(subject: str, date_str: str | None = None) -> dict[str, Any]:
    """Find meetings whose subject matches a substring.

    Args:
        subject: Case-insensitive substring to match against meeting subjects.
        date_str: Optional day (YYYY-MM-DD) to restrict the search.
    """
    with _client() as client:
        matches = client.find_meetings(subject, on_date=_parse_day(date_str))
        if not matches:
            raise ResourceNotFoundError(f"No meetings matched '{subject}'.")
        return {'count': len(matches), 'meetings': [_dump(m) for m in matches]}


def get_transcript(
    subject: str | None = None, date_str: str | None = None, thread_id: str | None = None
) -> dict[str, Any]:
    """Fetch a cleaned, speaker-attributed transcript for a meeting or call.

    Works for calendar meetings (match by subject) and for ad-hoc/1:1 calls that
    never appear in the calendar (pass the call's thread_id from list_calls).

    Args:
        subject: Case-insensitive substring matching the meeting subject or a
            call participant's name. Required unless thread_id is given.
        date_str: Optional day (YYYY-MM-DD) to pick the right occurrence.
        thread_id: Conversation/meeting thread id (from list_calls or a chat).
            Fetches that thread's transcript directly.
    """
    with _client() as client:
        transcript = _resolve_transcript(client, subject, date_str, thread_id)
        return {
            'meeting_subject': transcript.meeting_subject,
            'thread_id': transcript.thread_id,
            'speakers': transcript.speakers,
            'entry_count': len(transcript.entries),
            'text': transcript.text,
        }


def get_transcript_analytics(
    subject: str | None = None, date_str: str | None = None, thread_id: str | None = None
) -> dict[str, Any]:
    """Talk-time and word-count statistics for a meeting or call transcript.

    Cheaper than pulling the whole transcript when you only need to know who
    spoke and for how long.

    Args:
        subject: Case-insensitive substring matching the meeting subject or a
            call participant's name. Required unless thread_id is given.
        date_str: Optional day (YYYY-MM-DD) to pick the right occurrence.
        thread_id: Conversation/meeting thread id (from list_calls).
    """
    with _client() as client:
        transcript = _resolve_transcript(client, subject, date_str, thread_id)
        return _dump(analyse(transcript))


def list_calls(
    days: int = 7, participant: str | None = None, with_transcript: bool | None = None, limit: int = 50
) -> dict[str, Any]:
    """List recent Teams calls, including ad-hoc calls with no calendar entry.

    Each call carries a thread_id when it was recorded/transcribed; pass that to
    get_transcript to read the call. Use this when a transcript cannot be found
    by meeting subject (for example a 1:1 call started from a chat).

    Args:
        days: How far back to look. Use 0 for the full retained call history.
        participant: Optional case-insensitive name/MRI filter (e.g. "Kevin").
        with_transcript: Restrict to calls that have a transcript (True) or have
            none (False). Omit for all calls.
        limit: Maximum number of calls to return.
    """
    until = datetime.now(tz=UTC) + timedelta(days=1)
    since = until - timedelta(days=days) if days else None
    with _client() as client:
        calls = client.list_calls(
            limit=100, since=since, until=until, participant=participant, has_transcript=with_transcript
        )
    return {'count': len(calls), 'calls': [_dump(c) for c in calls[:limit]]}


def get_meeting_attachments(subject: str, date_str: str | None = None) -> dict[str, Any]:
    """List documents, images and links shared in a meeting.

    Recording metadata is excluded: this returns the useful material, not video.

    Args:
        subject: Case-insensitive substring matching the meeting subject.
        date_str: Optional day (YYYY-MM-DD) to pick the right occurrence.
    """
    with _client() as client:
        meeting = client.find_online_meeting(subject, on_date=_parse_day(date_str))
        if meeting is None:
            raise ResourceNotFoundError(f"No online meeting matched '{subject}'.")
        files = client.get_meeting_files(meeting.thread_id, include_recordings=False)
        return {
            'meeting_subject': meeting.subject,
            'thread_id': meeting.thread_id,
            'count': len(files),
            'files': [_dump(f) for f in files],
        }


def list_chats(kind: str | None = None, limit: int = 50) -> dict[str, Any]:
    """List conversations: 1:1s, group chats, channels and meeting chats.

    Args:
        kind: Optional filter - one_to_one, group, channel or meeting.
        limit: Maximum number of conversations to return.
    """
    with _client() as client:
        conversations = client.list_conversations(top=limit)
    if kind:
        conversations = [c for c in conversations if c.kind == kind]
    return {'count': len(conversations), 'conversations': [_dump(c) for c in conversations]}


def get_chat_messages(conversation: str, limit: int = 50) -> dict[str, Any]:
    """Read messages from a chat or channel, oldest first.

    Args:
        conversation: Conversation topic substring, or a thread id starting 19:.
        limit: Maximum number of messages to fetch.
    """
    with _client() as client:
        found, messages = client.get_messages_for(conversation, page_size=limit)
    messages = [m for m in messages if not m.is_system]
    return {'conversation': _dump(found), 'count': len(messages), 'messages': [_dump(m) for m in messages]}


def search_archive(query: str, source: str | None = None, limit: int = 25) -> dict[str, Any]:
    """Full-text search the local archive of transcripts, chats, meetings and files.

    Only searches what has been synced (see sync_archive); it does not touch
    the network.

    Args:
        query: Text to search for. Terms are ANDed; the last term is a prefix.
        source: Optional restriction - transcript, chat, meeting or file.
        limit: Maximum number of hits.
    """
    with Store(Paths.default().db_file) as store:
        hits = store.search(query, sources=[source] if source else None, limit=limit)
    return {'count': len(hits), 'hits': [_dump(h) for h in hits]}


def get_archive_digest(days: int = 7) -> dict[str, Any]:
    """Build a markdown digest of recent meetings and transcripts from the archive.

    Args:
        days: Size of the window in days.
    """
    end = datetime.now(tz=UTC)
    start = end - timedelta(days=days)
    with Store(Paths.default().db_file) as store:
        title, sections = build_digest(store, start=start, end=end)
    return {
        'title': title,
        'meetings': len(sections),
        'transcripts': sum(1 for _, t, _a in sections if t),
        'markdown': render_digest(title=title, sections=sections, include_transcripts=False),
    }


def sync_archive(days_back: int = 7, days_forward: int = 1, include_calls: bool = True) -> dict[str, Any]:
    """Mirror recent meetings, transcripts, chats and files into the local archive.

    Takes tens of seconds; run it before search_archive or
    get_archive_digest, or on a schedule. Writes to local disk only.

    Args:
        days_back: How far back to mirror.
        days_forward: How far ahead to mirror calendar items.
        include_calls: Also fetch transcripts for ad-hoc/1:1 calls (not in the
            calendar). Set False to skip the extra call-history request.
    """
    with _client() as client:
        report = client.sync(days_back=days_back, days_forward=days_forward, include_calls=include_calls)
    return _dump(report)


def save_transcript(
    path: str, subject: str | None = None, date_str: str | None = None, thread_id: str | None = None
) -> dict[str, Any]:
    """Fetch a transcript and write it to a local markdown file.

    Args:
        path: Destination file path (markdown).
        subject: Case-insensitive substring matching the meeting subject or a
            call participant's name. Required unless thread_id is given.
        date_str: Optional day (YYYY-MM-DD) to pick the right occurrence.
        thread_id: Conversation/meeting thread id (from list_calls).
    """
    with _client() as client:
        transcript = _resolve_transcript(client, subject, date_str, thread_id)
        Path(path).write_text(to_markdown(transcript), encoding='utf-8')
        return {'saved_to': path, 'entry_count': len(transcript.entries)}


def session_status() -> dict[str, Any]:
    """Report whether a usable Teams session exists and when tokens expire."""
    try:
        with _client() as client:
            return client.status()
    except AuthRequiredError as exc:
        return {'authenticated': False, 'error': str(exc), 'hint': 'Run `teams-browser login`.'}


# The detached login process, if one was started; held in a dict so it can be rebound without `global`.
_login: dict[str, subprocess.Popen | None] = {'process': None}


def start_login() -> dict[str, Any]:
    """Open a browser window on the user's machine for interactive Teams sign-in.

    Use this when other tools fail with AuthRequiredError ("run teams-browser
    login"). The login runs in a detached background process so this call
    returns immediately; the window stays open for the user to complete
    sign-in, including MFA/Conditional Access prompts. Afterwards call
    session_status to confirm, then retry the tool that failed.
    """
    process = _login['process']
    if process is not None and process.poll() is None:
        return {
            'started': False,
            'already_running': True,
            'hint': 'A login window is already open. Complete sign-in there, then call session_status.',
        }
    process = _login['process'] = subprocess.Popen(
        [sys.executable, '-m', 'teams_browser.cli', 'login'],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {
        'started': True,
        'pid': process.pid,
        'hint': (
            "A browser window opened on the user's machine for Teams sign-in. "
            'Ask the user to complete it, then call session_status to confirm.'
        ),
    }


# --------------------------------------------------------------------------- #
# Resources (attachable context, no tool call needed)
# --------------------------------------------------------------------------- #


def _meetings_markdown(items: list) -> str:
    if not items:
        return '_No meetings._'
    lines = ['| When | Subject | Organizer | Transcript |', '| --- | --- | --- | --- |']
    for m in items:
        when = m.start_time.strftime('%Y-%m-%d %H:%M') if m.start_time else '?'
        transcript = 'yes' if (m.is_online_meeting and m.thread_id) else '-'
        lines.append(f'| {when} | {m.subject} | {m.organizer_name or ""} | {transcript} |')
    return '\n'.join(lines)


def resource_meetings_today() -> str:
    """Today's Teams meetings."""
    with _client() as client:
        start = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return _meetings_markdown(client.list_meetings(start=start, end=start + timedelta(days=1)))


def resource_meetings_on(day: str) -> str:
    """Meetings on a given day (YYYY-MM-DD)."""
    start = _parse_day(day)
    if start is None:
        return f'_Invalid date: {day}_'
    with _client() as client:
        return _meetings_markdown(client.list_meetings(start=start, end=start + timedelta(days=1)))


def resource_archive_stats() -> str:
    """Counts of everything mirrored into the local archive."""
    with Store(Paths.default().db_file) as store:
        return json.dumps(store.stats(), indent=2)


def resource_recent_transcripts() -> str:
    """Index of transcripts stored in the local archive."""
    with Store(Paths.default().db_file) as store:
        rows = store.transcript_index(limit=50)
    if not rows:
        return '_No transcripts archived yet. Run `sync_archive`._'
    lines = ['| Date | Meeting | Entries |', '| --- | --- | --- |']
    lines.extend(f'| {(r["recording_start"] or "")[:10]} | {r["meeting_subject"]} | {r["entry_count"]} |' for r in rows)
    return '\n'.join(lines)


def resource_recent_chats() -> str:
    """Recent conversations from the local archive."""
    with Store(Paths.default().db_file) as store:
        conversations = store.conversations(limit=50)
    if not conversations:
        return '_No conversations archived yet. Run `sync_archive`._'
    lines = ['| Kind | Topic | Last activity |', '| --- | --- | --- |']
    for c in conversations:
        when = c.last_message_at.strftime('%Y-%m-%d %H:%M') if c.last_message_at else '?'
        lines.append(f'| {c.kind} | {c.topic or c.id[:32]} | {when} |')
    return '\n'.join(lines)


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #


def summarise_meeting(subject: str, date_str: str | None = None) -> str:
    """Summarise a meeting into notes, decisions and action items."""
    when = f' on {date_str}' if date_str else ''
    args = f'subject={subject!r}' + (f', date_str={date_str!r}' if date_str else '')
    return (
        f'Summarise the Teams meeting matching {subject!r}{when}.\n\n'
        f'1. Call get_transcript with {args}.\n'
        '2. Write: a three-sentence overview; decisions made; action items with owners; '
        'open questions.\n'
        '3. Attribute decisions and action items to the speaker who made them.\n'
        '4. Do not invent owners, dates or commitments that are not in the transcript.\n'
        'If the transcript is unavailable, say so and stop.'
    )


def weekly_digest(days: str = '7') -> str:
    """Produce a digest of recent meetings with talk-time highlights."""
    # Accept the window as text and coerce it. Some MCP clients pass command
    # placeholders (e.g. "$1") instead of a number; fall back to 7 rather than
    # failing schema validation.
    try:
        window = max(1, int(str(days).strip()))
    except TypeError, ValueError:
        window = 7
    return (
        f'Build a digest of my Teams meetings from the last {window} days.\n\n'
        '1. Call get_archive_digest (run sync_archive first if the archive looks stale).\n'
        '2. Highlight meetings that produced decisions or action items, using '
        'get_transcript for any that matter.\n'
        '3. Call out anything that looks like it needed follow-up but had none.'
    )


def find_decisions(topic: str) -> str:
    """Find where a topic was discussed and what was decided."""
    return (
        f'Find where {topic!r} was discussed across my meetings and chats.\n\n'
        f'1. Call search_archive with query={topic!r}.\n'
        '2. For transcript hits, pull the surrounding context with get_transcript.\n'
        '3. Report: what was decided, when, by whom, and what remains open.\n'
        'Distinguish clearly between discussion and actual decisions.'
    )


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #


def build_server() -> Any:
    try:
        from fastmcp import FastMCP
        from mcp.types import ToolAnnotations
    except ImportError as exc:  # pragma: no cover
        sys.stderr.write('fastmcp is not installed. Install the extra: `uv sync --extra mcp`.\n')
        raise SystemExit(2) from exc

    read = ToolAnnotations(read_only_hint=True, open_world_hint=True)
    read_local = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    write_local = ToolAnnotations(read_only_hint=False, idempotent_hint=True, open_world_hint=True)

    mcp = FastMCP('teams-browser')

    for fn in (
        list_meetings,
        get_meeting,
        get_transcript,
        get_transcript_analytics,
        get_meeting_attachments,
        list_chats,
        get_chat_messages,
        list_calls,
    ):
        mcp.tool(annotations=read)(fn)
    mcp.tool(annotations=read_local)(search_archive)
    mcp.tool(annotations=read_local)(get_archive_digest)
    mcp.tool(annotations=read_local)(session_status)
    mcp.tool(annotations=write_local)(sync_archive)
    mcp.tool(annotations=write_local)(save_transcript)
    mcp.tool(annotations=write_local)(start_login)

    mcp.resource('teams://meetings/today', mime_type='text/markdown')(resource_meetings_today)
    mcp.resource('teams://meetings/{day}', mime_type='text/markdown')(resource_meetings_on)
    mcp.resource('teams://archive/stats', mime_type='application/json')(resource_archive_stats)
    mcp.resource('teams://archive/transcripts', mime_type='text/markdown')(resource_recent_transcripts)
    mcp.resource('teams://archive/chats', mime_type='text/markdown')(resource_recent_chats)

    for fn in (summarise_meeting, weekly_digest, find_decisions):
        mcp.prompt()(fn)

    return mcp


def main() -> None:
    build_server().run()


__all__ = [
    'build_server',
    'get_archive_digest',
    'get_chat_messages',
    'get_meeting',
    'get_meeting_attachments',
    'get_transcript',
    'get_transcript_analytics',
    'list_calls',
    'list_chats',
    'list_meetings',
    'main',
    'save_transcript',
    'search_archive',
    'session_status',
    'start_login',
    'sync_archive',
]
