"""Local SQLite archive with full-text search.

Transcripts and recordings expire on tenant retention, and re-scraping chattier
surfaces (chats, files) on every query is slow and rude. So we keep a local
mirror and search it: everything the ``sync`` command writes lands here, and
``search`` never touches the network.

FTS5 (bundled with CPython's SQLite) provides ranked full-text matching with
snippets; non-text entities (meetings, files) are matched with ``LIKE``.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .api.util import iso_utc, parse_dt
from .models import ChatMessage, Conversation, Meeting, SearchHit, SharedFile, Transcript

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id                TEXT PRIMARY KEY,
    subject           TEXT,
    start_time        TEXT,
    end_time          TEXT,
    organizer_name    TEXT,
    organizer_address TEXT,
    location          TEXT,
    is_online_meeting INTEGER,
    join_url          TEXT,
    thread_id         TEXT,
    my_response       TEXT,
    show_as           TEXT,
    is_organizer      INTEGER,
    event_type        TEXT,
    fetched_at        TEXT
);

CREATE TABLE IF NOT EXISTS transcripts (
    thread_id       TEXT PRIMARY KEY,
    meeting_subject TEXT,
    recording_start TEXT,
    recording_end   TEXT,
    entry_count     INTEGER,
    speakers        TEXT,
    entries         TEXT,
    text            TEXT,
    fetched_at      TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
    thread_id UNINDEXED,
    meeting_subject,
    text,
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS conversations (
    id                   TEXT PRIMARY KEY,
    kind                 TEXT,
    topic                TEXT,
    team_id              TEXT,
    last_message_at      TEXT,
    last_message_preview TEXT,
    last_sender          TEXT,
    is_favorite          INTEGER,
    fetched_at           TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT,
    sender          TEXT,
    timestamp       TEXT,
    text            TEXT,
    is_system       INTEGER,
    fetched_at      TEXT
);

CREATE INDEX IF NOT EXISTS messages_conversation_idx
    ON messages (conversation_id, timestamp);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    id UNINDEXED,
    conversation_id UNINDEXED,
    sender,
    text,
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS files (
    id           TEXT PRIMARY KEY,
    name         TEXT,
    extension    TEXT,
    kind         TEXT,
    created_at   TEXT,
    modified_at  TEXT,
    url          TEXT,
    thread_id    TEXT,
    is_recording INTEGER,
    fetched_at   TEXT
);

CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    User input can contain characters that are FTS5 syntax (``-``, ``*``,
    ``:``), so every token is quoted. The final token gets a prefix wildcard so
    partial words still match.
    """
    tokens = [t for t in text.replace('"', ' ').split() if t]
    if not tokens:
        return ''
    quoted = [f'"{t}"' for t in tokens[:-1]]
    last = tokens[-1].replace('*', '')
    quoted.append(f'"{last}"*')
    return ' AND '.join(quoted)


def _now() -> str:
    return iso_utc(datetime.now(tz=timezone.utc))


class Store:
    """Thin, synchronous wrapper around the archive database."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute('PRAGMA foreign_keys=ON')
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @classmethod
    def default(cls) -> 'Store':
        from .config import Paths

        return cls(Paths.default().db_file)

    # -- plumbing ----------------------------------------------------------- #

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> 'Store':
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def set_state(self, key: str, value: str) -> None:
        with self._tx() as conn:
            conn.execute(
                'INSERT INTO sync_state (key, value) VALUES (?, ?) '
                'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                (key, value),
            )

    def get_state(self, key: str) -> str | None:
        row = self._conn.execute('SELECT value FROM sync_state WHERE key = ?', (key,)).fetchone()
        return row['value'] if row else None

    # -- writes ------------------------------------------------------------- #

    def upsert_meetings(self, meetings: Iterable[Meeting]) -> int:
        rows = [
            (
                m.id,
                m.subject,
                iso_utc(m.start_time) if m.start_time else None,
                iso_utc(m.end_time) if m.end_time else None,
                m.organizer_name,
                m.organizer_address,
                m.location,
                int(m.is_online_meeting),
                m.join_url,
                m.thread_id,
                m.my_response,
                m.show_as,
                int(m.is_organizer),
                m.event_type,
                _now(),
            )
            for m in meetings
        ]
        with self._tx() as conn:
            conn.executemany(
                'INSERT INTO meetings VALUES '
                '(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(id) DO UPDATE SET '
                'subject=excluded.subject, start_time=excluded.start_time, '
                'end_time=excluded.end_time, organizer_name=excluded.organizer_name, '
                'location=excluded.location, thread_id=excluded.thread_id, '
                'my_response=excluded.my_response, show_as=excluded.show_as, '
                'fetched_at=excluded.fetched_at',
                rows,
            )
        return len(rows)

    def upsert_transcript(self, transcript: Transcript) -> None:
        speakers = json.dumps(transcript.speakers)
        with self._tx() as conn:
            conn.execute(
                'INSERT INTO transcripts VALUES (?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(thread_id) DO UPDATE SET '
                'meeting_subject=excluded.meeting_subject, '
                'recording_start=excluded.recording_start, '
                'recording_end=excluded.recording_end, entry_count=excluded.entry_count, '
                'speakers=excluded.speakers, entries=excluded.entries, '
                'text=excluded.text, fetched_at=excluded.fetched_at',
                (
                    transcript.thread_id,
                    transcript.meeting_subject,
                    iso_utc(transcript.recording_start) if transcript.recording_start else None,
                    iso_utc(transcript.recording_end) if transcript.recording_end else None,
                    len(transcript.entries),
                    speakers,
                    json.dumps([e.model_dump() for e in transcript.entries]),
                    transcript.text,
                    _now(),
                ),
            )
            conn.execute('DELETE FROM transcripts_fts WHERE thread_id = ?', (transcript.thread_id,))
            conn.execute(
                'INSERT INTO transcripts_fts (thread_id, meeting_subject, text) VALUES (?,?,?)',
                (transcript.thread_id, transcript.meeting_subject or '', transcript.text),
            )

    def upsert_conversations(self, conversations: Iterable[Conversation]) -> int:
        rows = [
            (
                c.id,
                c.kind,
                c.topic,
                c.team_id,
                iso_utc(c.last_message_at) if c.last_message_at else None,
                c.last_message_preview,
                c.last_sender,
                int(c.is_favorite),
                _now(),
            )
            for c in conversations
        ]
        with self._tx() as conn:
            conn.executemany(
                'INSERT INTO conversations VALUES (?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(id) DO UPDATE SET '
                'kind=excluded.kind, topic=excluded.topic, team_id=excluded.team_id, '
                'last_message_at=excluded.last_message_at, '
                'last_message_preview=excluded.last_message_preview, '
                'last_sender=excluded.last_sender, is_favorite=excluded.is_favorite, '
                'fetched_at=excluded.fetched_at',
                rows,
            )
        return len(rows)

    def upsert_messages(self, messages: Iterable[ChatMessage]) -> int:
        items = list(messages)
        with self._tx() as conn:
            for m in items:
                conn.execute(
                    'INSERT INTO messages VALUES (?,?,?,?,?,?,?) '
                    'ON CONFLICT(id) DO UPDATE SET text=excluded.text, '
                    'timestamp=excluded.timestamp, sender=excluded.sender, '
                    'is_system=excluded.is_system, fetched_at=excluded.fetched_at',
                    (
                        m.id,
                        m.conversation_id,
                        m.sender,
                        iso_utc(m.timestamp) if m.timestamp else None,
                        m.text,
                        int(m.is_system),
                        _now(),
                    ),
                )
                conn.execute('DELETE FROM messages_fts WHERE id = ?', (m.id,))
                if not m.is_system and m.text:
                    conn.execute(
                        'INSERT INTO messages_fts (id, conversation_id, sender, text) VALUES (?,?,?,?)',
                        (m.id, m.conversation_id, m.sender or '', m.text),
                    )
        return len(items)

    def upsert_files(self, files: Iterable[SharedFile]) -> int:
        rows = [
            (
                f.id,
                f.name,
                f.extension,
                f.kind,
                iso_utc(f.created_at) if f.created_at else None,
                iso_utc(f.modified_at) if f.modified_at else None,
                f.url,
                f.thread_id,
                int(f.is_recording),
                _now(),
            )
            for f in files
        ]
        with self._tx() as conn:
            conn.executemany(
                'INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind, '
                'url=excluded.url, thread_id=excluded.thread_id, fetched_at=excluded.fetched_at',
                rows,
            )
        return len(rows)

    # -- reads -------------------------------------------------------------- #

    def meetings(
        self, *, start: datetime | None = None, end: datetime | None = None, limit: int = 100
    ) -> list[Meeting]:
        sql = 'SELECT * FROM meetings'
        clauses: list[str] = []
        params: list[Any] = []
        if start:
            clauses.append('start_time >= ?')
            params.append(iso_utc(start))
        if end:
            clauses.append('start_time < ?')
            params.append(iso_utc(end))
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        sql += ' ORDER BY start_time LIMIT ?'
        params.append(limit)
        return [_row_meeting(r) for r in self._conn.execute(sql, params)]

    def transcript(self, thread_id: str) -> Transcript | None:
        row = self._conn.execute('SELECT * FROM transcripts WHERE thread_id = ?', (thread_id,)).fetchone()
        if not row:
            return None
        from .models import TranscriptEntry

        try:
            raw_entries = json.loads(row['entries'] or '[]')
        except (TypeError, ValueError):
            raw_entries = []
        return Transcript(
            meeting_subject=row['meeting_subject'],
            thread_id=row['thread_id'],
            recording_start=parse_dt(row['recording_start']),
            recording_end=parse_dt(row['recording_end']),
            entries=[TranscriptEntry(**e) for e in raw_entries],
            speakers=json.loads(row['speakers'] or '[]'),
        )

    def conversations(self, *, kind: str | None = None, limit: int = 100) -> list[Conversation]:
        sql = 'SELECT * FROM conversations'
        params: list[Any] = []
        if kind:
            sql += ' WHERE kind = ?'
            params.append(kind)
        sql += " ORDER BY COALESCE(last_message_at, '') DESC LIMIT ?"
        params.append(limit)
        return [
            Conversation(
                id=r['id'],
                kind=r['kind'] or 'group',
                topic=r['topic'],
                team_id=r['team_id'],
                last_message_at=parse_dt(r['last_message_at']),
                last_message_preview=r['last_message_preview'],
                last_sender=r['last_sender'],
                is_favorite=bool(r['is_favorite']),
            )
            for r in self._conn.execute(sql, params)
        ]

    def messages(self, conversation_id: str, *, limit: int = 200, include_system: bool = False) -> list[ChatMessage]:
        sql = 'SELECT * FROM messages WHERE conversation_id = ?'
        params: list[Any] = [conversation_id]
        if not include_system:
            sql += ' AND is_system = 0'
        sql += " ORDER BY COALESCE(timestamp, '') ASC LIMIT ?"
        params.append(limit)
        return [
            ChatMessage(
                id=r['id'],
                conversation_id=r['conversation_id'],
                sender=r['sender'],
                timestamp=parse_dt(r['timestamp']),
                text=r['text'] or '',
                is_system=bool(r['is_system']),
            )
            for r in self._conn.execute(sql, params)
        ]

    def files(self, *, thread_id: str | None = None, limit: int = 200) -> list[SharedFile]:
        sql = 'SELECT * FROM files'
        params: list[Any] = []
        if thread_id:
            sql += ' WHERE thread_id = ?'
            params.append(thread_id)
        sql += " ORDER BY COALESCE(created_at, '') DESC LIMIT ?"
        params.append(limit)
        return [
            SharedFile(
                id=r['id'],
                name=r['name'],
                extension=r['extension'],
                kind=r['kind'],
                created_at=parse_dt(r['created_at']),
                modified_at=parse_dt(r['modified_at']),
                url=r['url'],
                thread_id=r['thread_id'],
                is_recording=bool(r['is_recording']),
            )
            for r in self._conn.execute(sql, params)
        ]

    def transcript_index(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            'SELECT thread_id, meeting_subject, entry_count, recording_start '
            "FROM transcripts ORDER BY COALESCE(recording_start, '') DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    # -- search ------------------------------------------------------------- #

    def search(
        self, query: str, *, sources: Iterable[str] | None = None, conversation_id: str | None = None, limit: int = 25
    ) -> list[SearchHit]:
        wanted = set(sources) if sources else {'transcript', 'chat', 'meeting', 'file'}
        match = _fts_query(query)
        hits: list[SearchHit] = []
        if match and 'transcript' in wanted:
            hits += self._search_transcripts(match, limit)
        if match and 'chat' in wanted:
            hits += self._search_chats(match, conversation_id, limit)
        if 'meeting' in wanted:
            hits += self._search_meetings(query, limit)
        if 'file' in wanted:
            hits += self._search_files(query, limit)
        hits.sort(key=lambda h: (h.score is None, h.score or 0.0))
        return hits[:limit]

    def _search_transcripts(self, match: str, limit: int) -> list[SearchHit]:
        rows = self._conn.execute(
            'SELECT transcripts_fts.thread_id AS ref, '
            'transcripts_fts.meeting_subject AS title, '
            "snippet(transcripts_fts, 2, '[', ']', ' ... ', 16) AS snip, "
            'transcripts.recording_start AS ts, bm25(transcripts_fts) AS score '
            'FROM transcripts_fts '
            'LEFT JOIN transcripts ON transcripts.thread_id = transcripts_fts.thread_id '
            'WHERE transcripts_fts MATCH ? ORDER BY score LIMIT ?',
            (match, limit),
        )
        return [
            SearchHit(
                source='transcript',
                ref=row['ref'],
                title=row['title'] or '(transcript)',
                subtitle='transcript',
                snippet=row['snip'],
                timestamp=parse_dt(row['ts']),
                score=row['score'],
            )
            for row in rows
        ]

    def _search_chats(self, match: str, conversation_id: str | None, limit: int) -> list[SearchHit]:
        sql = (
            'SELECT messages_fts.id AS ref, messages_fts.sender AS title, '
            'messages_fts.conversation_id AS conv, '
            "snippet(messages_fts, 3, '[', ']', ' ... ', 16) AS snip, "
            'messages.timestamp AS ts, bm25(messages_fts) AS score '
            'FROM messages_fts '
            'LEFT JOIN messages ON messages.id = messages_fts.id '
            'WHERE messages_fts MATCH ? '
        )
        params: list[Any] = [match]
        if conversation_id:
            sql += 'AND messages_fts.conversation_id = ? '
            params.append(conversation_id)
        sql += 'ORDER BY score LIMIT ?'
        params.append(limit)
        return [
            SearchHit(
                source='chat',
                ref=row['ref'],
                title=row['title'] or '(chat)',
                subtitle=row['conv'],
                snippet=row['snip'],
                timestamp=parse_dt(row['ts']),
                score=row['score'],
            )
            for row in self._conn.execute(sql, params)
        ]

    def _search_meetings(self, query: str, limit: int) -> list[SearchHit]:
        rows = self._conn.execute(
            'SELECT id, subject, start_time FROM meetings WHERE subject LIKE ? ORDER BY start_time DESC LIMIT ?',
            (f'%{query}%', limit),
        )
        return [
            SearchHit(
                source='meeting',
                ref=row['id'],
                title=row['subject'],
                subtitle='meeting',
                timestamp=parse_dt(row['start_time']),
            )
            for row in rows
        ]

    def _search_files(self, query: str, limit: int) -> list[SearchHit]:
        rows = self._conn.execute(
            'SELECT id, name, kind, created_at FROM files WHERE name LIKE ? ORDER BY created_at DESC LIMIT ?',
            (f'%{query}%', limit),
        )
        return [
            SearchHit(
                source='file',
                ref=row['id'],
                title=row['name'],
                subtitle=row['kind'] or 'file',
                timestamp=parse_dt(row['created_at']),
            )
            for row in rows
        ]

    # -- stats -------------------------------------------------------------- #

    def stats(self) -> dict[str, Any]:
        def count(table: str) -> int:
            return int(self._conn.execute(f'SELECT COUNT(*) AS n FROM {table}').fetchone()['n'])

        return {
            'path': str(self.path),
            'meetings': count('meetings'),
            'transcripts': count('transcripts'),
            'conversations': count('conversations'),
            'messages': count('messages'),
            'files': count('files'),
            'last_sync': self.get_state('last_sync'),
        }


def _row_meeting(row: sqlite3.Row) -> Meeting:
    return Meeting(
        id=row['id'],
        subject=row['subject'] or '',
        start_time=parse_dt(row['start_time']),
        end_time=parse_dt(row['end_time']),
        organizer_name=row['organizer_name'],
        organizer_address=row['organizer_address'],
        location=row['location'],
        is_online_meeting=bool(row['is_online_meeting']),
        join_url=row['join_url'],
        thread_id=row['thread_id'],
        my_response=row['my_response'] or 'None',
        show_as=row['show_as'] or 'Unknown',
        is_organizer=bool(row['is_organizer']),
        event_type=row['event_type'] or 'Single',
    )
