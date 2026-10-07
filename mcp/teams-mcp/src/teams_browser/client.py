"""High-level facade used by both the CLI and the MCP server.

Ties together the stored session, token/region extraction and the individual API
clients. Swapping the browser-session backend for Microsoft Graph later is a
matter of reimplementing this class against the same method surface.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, date, datetime, time, timedelta

from .api import (
    calendar as calendar_api,
    calls as calls_api,
    chats as chats_api,
    files as files_api,
    transcript as transcript_api,
)
from .api.http import HttpClient
from .auth import login as login_module, refresh as refresh_module
from .auth.session import Paths, SessionState, load_session
from .auth.tokens import extract_region_config, extract_tokens, get_identity, require_region
from .config import region_override
from .errors import AuthRequiredError, ResourceNotFoundError, TeamsBrowserError
from .models import (
    Call,
    ChatMessage,
    Conversation,
    Meeting,
    RegionConfig,
    SearchHit,
    SharedFile,
    SyncReport,
    TokenSet,
    Transcript,
)
from .store import Store


class TeamsClient:
    def __init__(
        self,
        *,
        paths: Paths | None = None,
        auto_refresh: bool = True,
        browser_refresh: bool = True,
        client: HttpClient | None = None,
    ):
        self.paths = paths or Paths.default()
        self.auto_refresh = auto_refresh
        # When False (e.g. inside the MCP server) a failed HTTP refresh raises
        # immediately instead of blocking ~60s on a headless browser launch.
        self.browser_refresh = browser_refresh
        self._client = client or HttpClient()
        self._session: SessionState | None = None
        self._tokens: TokenSet | None = None
        self._store: Store | None = None

    # -- session ------------------------------------------------------------ #

    def _load(self) -> SessionState:
        if self._session is None:
            state = load_session(self.paths)
            if state is None:
                raise AuthRequiredError('No Teams session found. Run `teams-browser login` first.')
            self._session = state
        return self._session

    def reload(self) -> None:
        self._session = None
        self._tokens = None

    @property
    def tokens(self) -> TokenSet:
        if self._tokens is None:
            self._tokens = extract_tokens(self._load())
        return self._tokens

    def ensure_valid(self, *, require: tuple[str, ...] = ()) -> None:
        """Make sure we hold live tokens, refreshing if needed.

        ``require`` names tokens this particular call needs (e.g. ``skype_token``
        for chats, ``substrate`` for transcripts) so a call is not gated on an
        unrelated token that happens to be stale.
        """
        tokens = self.tokens
        needed = [t for t in (tokens.spaces, tokens.substrate) if t is not None]
        missing = [name for name in require if getattr(tokens, name, None) is None]
        stale = any(t.is_expired(skew_seconds=120) for t in needed) or not needed or bool(missing)
        if not stale:
            return
        if not self.auto_refresh:
            from .errors import TokenExpiredError

            raise TokenExpiredError('Session tokens are expired. Run `teams-browser login`.')
        self.refresh()
        self.reload()

    def refresh(self) -> str:
        """Attempt a silent, browserless refresh, then fall back to headless browser."""
        state = self._load()
        with contextlib.suppress(Exception):
            if refresh_module.refresh_via_http(state, self.paths):
                return 'http'
        if not self.browser_refresh:
            raise AuthRequiredError(
                'Session tokens are stale and the silent refresh failed. '
                'Run `teams-browser login` (or `teams-browser refresh`).'
            )
        try:
            login_module.refresh_session_headless(paths=self.paths)
        except Exception as exc:  # pragma: no cover - environment dependent
            raise AuthRequiredError(
                'Automatic token refresh failed (the sign-in is no longer valid). Run `teams-browser login` again.'
            ) from exc
        else:
            return 'browser'

    # -- context ------------------------------------------------------------ #

    @property
    def session_state(self) -> SessionState:
        return self._load()

    def region(self) -> RegionConfig:
        override = region_override()
        if override:
            return _region_from_override(override)
        return require_region(self._load())

    def identity(self) -> tuple[str | None, str | None]:
        return get_identity(self._load())

    def status(self) -> dict:
        state = self._load()
        tokens = extract_tokens(state)
        name, upn = get_identity(state)
        region = extract_region_config(state)
        return {
            'display_name': name,
            'upn': upn,
            'region': None if not region else region.region,
            'partition': None if not region else region.partition,
            'tokens': {
                'substrate': _tok(tokens.substrate),
                'spaces': _tok(tokens.spaces),
                'csa': _tok(tokens.csa),
                'graph': _tok(tokens.graph),
                'skypetoken': bool(tokens.skype_token),
                'authtoken': bool(tokens.auth_token),
            },
        }

    # -- meetings ----------------------------------------------------------- #

    def list_meetings(
        self, *, start: datetime | None = None, end: datetime | None = None, limit: int = 50
    ) -> list[Meeting]:
        self.ensure_valid(require=('skype_token', 'spaces'))
        return calendar_api.list_meetings(
            self.region(), self.tokens, start=start, end=end, limit=limit, client=self._client
        )

    def find_meetings(
        self,
        subject: str,
        *,
        on_date: date | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[Meeting]:
        if on_date is not None:
            start = datetime.combine(on_date, time.min, tzinfo=UTC)
            end = start + timedelta(days=1)
        meetings = self.list_meetings(start=start, end=end, limit=limit)
        needle = subject.lower()
        return [m for m in meetings if needle in (m.subject or '').lower()]

    def find_online_meeting(
        self,
        subject: str,
        *,
        on_date: date | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> Meeting | None:
        """First matching meeting that is a Teams online meeting with a thread."""
        matches = self.find_meetings(subject, on_date=on_date, start=start, end=end, limit=limit)
        return next((m for m in matches if m.thread_id), None)

    # -- calls -------------------------------------------------------------- #

    def list_calls(
        self,
        *,
        limit: int = 100,
        since: datetime | None = None,
        until: datetime | None = None,
        participant: str | None = None,
        has_transcript: bool | None = None,
    ) -> list[Call]:
        """Recent call history, including ad-hoc calls missing from the calendar."""
        self.ensure_valid(require=('skype_token',))
        calls = calls_api.list_calls(self.region(), self.tokens, limit=limit, client=self._client)
        me_name, _ = self.identity()
        for call in calls:
            call.title = call.title or calls_api.derive_title(call, me=me_name)
        if since or until:
            calls = [c for c in calls if _in_window(c.start_time, since, until)]
        if participant:
            calls = [c for c in calls if calls_api.matches(c, participant)]
        if has_transcript is not None:
            calls = [c for c in calls if c.has_transcript == has_transcript]
        return calls

    # -- transcripts -------------------------------------------------------- #

    def get_transcript(
        self, thread_id: str, *, subject: str | None = None, meeting_date: datetime | None = None
    ) -> Transcript:
        self.ensure_valid(require=('substrate',))
        return transcript_api.get_transcript(
            self.tokens, thread_id, thread_subject=subject, meeting_date=meeting_date, client=self._client
        )

    def get_transcript_for(
        self, subject: str, *, on_date: date | None = None, start: datetime | None = None, end: datetime | None = None
    ) -> Transcript:
        if on_date is not None:
            start = datetime.combine(on_date, time.min, tzinfo=UTC)
            end = start + timedelta(days=1)
        meeting = self.find_online_meeting(subject, start=start, end=end)
        if meeting:
            return self.get_transcript(meeting.thread_id, subject=meeting.subject, meeting_date=meeting.start_time)
        # Ad-hoc calls never appear in the calendar, so fall back to call history.
        call = self._find_call(subject, since=start, until=end)
        if call:
            return self.get_transcript(call.thread_id, subject=call.title or subject, meeting_date=call.start_time)
        raise ResourceNotFoundError(f"No meetings or calls with a transcript matched '{subject}'.")

    def _find_call(self, needle: str, *, since: datetime | None = None, until: datetime | None = None) -> Call | None:
        try:
            calls = self.list_calls(since=since, until=until, limit=100)
        except TeamsBrowserError:
            return None
        for call in calls:
            if call.thread_id and call.has_transcript and calls_api.matches(call, needle):
                return call
        return None

    # -- chats -------------------------------------------------------------- #

    def list_conversations(self, *, top: int = 50) -> list[Conversation]:
        self.ensure_valid(require=('skype_token',))
        return chats_api.list_conversations(self.region(), self.tokens, top=top, client=self._client)

    def list_messages(self, conversation_id: str, *, page_size: int = 50) -> list[ChatMessage]:
        self.ensure_valid(require=('skype_token',))
        return chats_api.list_messages(
            self.region(), self.tokens, conversation_id, page_size=page_size, client=self._client
        )

    def get_conversation(self, needle: str, *, top: int = 100) -> Conversation:
        self.ensure_valid(require=('skype_token',))
        return chats_api.find_conversation(self.region(), self.tokens, needle, top=top, client=self._client)

    def get_messages_for(self, needle: str, *, page_size: int = 50) -> tuple[Conversation, list[ChatMessage]]:
        conversation = self.get_conversation(needle)
        return conversation, self.list_messages(conversation.id, page_size=page_size)

    # -- files -------------------------------------------------------------- #

    def list_files(self, *, top: int = 50, include_recordings: bool = True) -> list[SharedFile]:
        self.ensure_valid(require=('substrate',))
        return files_api.list_files(self.tokens, top=top, include_recordings=include_recordings, client=self._client)

    def get_meeting_files(self, thread_id: str, *, include_recordings: bool = False) -> list[SharedFile]:
        self.ensure_valid(require=('substrate',))
        return files_api.list_thread_files(
            self.tokens, thread_id, include_recordings=include_recordings, client=self._client
        )

    # -- archive ------------------------------------------------------------ #

    @property
    def store(self) -> Store:
        if getattr(self, '_store', None) is None:
            self._store = Store(self.paths.db_file)
        return self._store

    def sync(self, **kwargs) -> SyncReport:
        from .sync import sync as run_sync

        return run_sync(self, self.store, **kwargs)

    def search(
        self, query: str, *, sources: list[str] | None = None, conversation_id: str | None = None, limit: int = 25
    ) -> list[SearchHit]:
        return self.store.search(query, sources=sources, conversation_id=conversation_id, limit=limit)

    def close(self) -> None:
        if getattr(self, '_store', None) is not None:
            self._store.close()
            self._store = None
        self._client.close()

    def __enter__(self) -> TeamsClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _in_window(value: datetime | None, since: datetime | None, until: datetime | None) -> bool:
    if value is None:
        return since is None and until is None
    if since is not None and value < since:
        return False
    return not (until is not None and value >= until)


def _tok(info) -> dict | None:
    if info is None:
        return None
    return {
        'expires_at': info.expires_at.isoformat() if info.expires_at else None,
        'expired': info.is_expired(skew_seconds=0),
    }


def _region_from_override(value: str) -> RegionConfig:
    from .config import teams_base_url

    has_partition = '-' in value
    region = value.split('-', 1)[0]
    base = teams_base_url()
    return RegionConfig(
        region=region,
        partition=value.split('-', 1)[1] if has_partition else '',
        region_partition=value,
        has_partition=has_partition,
        middle_tier_url='',
        chat_service_url=f'{base}/api/chatsvc/{region}',
        csa_service_url=f'{base}/api/csa/{region}',
        teams_base_url=base,
    )
