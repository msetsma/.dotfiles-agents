"""Domain models.

Pydantic models are used for anything surfaced through the CLI/MCP (they give
us free JSON serialisation and validation). Internal config carriers are plain
dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Internal auth carriers
# --------------------------------------------------------------------------- #


@dataclass
class TokenInfo:
    token: str
    expires_at: datetime | None
    resource: str = ''

    def is_expired(self, *, skew_seconds: int = 60) -> bool:
        if self.expires_at is None:
            return False
        return (self.expires_at - datetime.now(tz=self.expires_at.tzinfo)).total_seconds() < skew_seconds


@dataclass
class TokenSet:
    """All tokens harvested from a Teams session, keyed by audience."""

    substrate: TokenInfo | None = None
    spaces: TokenInfo | None = None
    csa: TokenInfo | None = None
    ic3: TokenInfo | None = None
    graph: TokenInfo | None = None
    skype_token: str | None = None
    auth_token: str | None = None

    def missing(self, *names: str) -> list[str]:
        return [n for n in names if getattr(self, n, None) is None]


@dataclass
class RegionConfig:
    region: str
    partition: str
    region_partition: str
    has_partition: bool
    middle_tier_url: str
    chat_service_url: str
    csa_service_url: str
    teams_base_url: str


@dataclass
class UserDetails:
    mri: str
    display_name: str | None = None
    upn: str | None = None
    region: str | None = None
    licenses: dict[str, bool] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# API-facing models
# --------------------------------------------------------------------------- #


class Meeting(BaseModel):
    id: str
    subject: str
    start_time: datetime | None = None
    end_time: datetime | None = None
    organizer_name: str | None = None
    organizer_address: str | None = None
    location: str | None = None
    is_online_meeting: bool = False
    join_url: str | None = None
    thread_id: str | None = None
    my_response: str = 'None'
    show_as: str = 'Unknown'
    is_organizer: bool = False
    event_type: str = 'Single'


class CallParticipant(BaseModel):
    id: str | None = None
    display_name: str | None = None


class Call(BaseModel):
    """A Teams call from the call history, scheduled or ad-hoc.

    Ad-hoc calls (a 1:1 "call with" or a started-from-chat group call) never
    appear in the calendar, so they are discovered from the ``48:calllogs``
    conversation instead. When a call was recorded or transcribed, ``thread_id``
    carries the conversation id the Substrate transcript lookup needs.
    """

    call_id: str
    thread_id: str | None = None
    title: str | None = None
    direction: str | None = None
    kind: str | None = None
    state: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    originator: CallParticipant | None = None
    target: CallParticipant | None = None
    participants: list[CallParticipant] = Field(default_factory=list)
    has_transcript: bool = False
    has_recording: bool = False

    @property
    def duration_seconds(self) -> float | None:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None


class TranscriptEntry(BaseModel):
    start: str = ''
    end: str = ''
    speaker: str = ''
    text: str = ''


class Transcript(BaseModel):
    meeting_subject: str | None = None
    thread_id: str
    recording_start: datetime | None = None
    recording_end: datetime | None = None
    entries: list[TranscriptEntry] = Field(default_factory=list)
    speakers: list[str] = Field(default_factory=list)

    @property
    def text(self) -> str:
        from .transcript_text import format_transcript

        return format_transcript(self.entries)


class SpeakerStat(BaseModel):
    speaker: str
    turns: int = 0
    words: int = 0
    talk_seconds: float = 0.0
    share: float = 0.0


class TranscriptAnalytics(BaseModel):
    thread_id: str
    meeting_subject: str | None = None
    duration_seconds: float = 0.0
    entry_count: int = 0
    word_count: int = 0
    speakers: list[SpeakerStat] = Field(default_factory=list)


class Conversation(BaseModel):
    id: str
    kind: str = 'group'
    topic: str | None = None
    team_id: str | None = None
    team_name: str | None = None
    last_message_at: datetime | None = None
    last_message_preview: str | None = None
    last_sender: str | None = None
    is_favorite: bool = False


class MessageAttachment(BaseModel):
    name: str | None = None
    url: str | None = None
    kind: str | None = None


class ChatMessage(BaseModel):
    id: str
    conversation_id: str
    sender: str | None = None
    sender_mri: str | None = None
    timestamp: datetime | None = None
    text: str = ''
    content_type: str | None = None
    message_type: str | None = None
    is_system: bool = False
    attachments: list[MessageAttachment] = Field(default_factory=list)
    mentions: list[str] = Field(default_factory=list)
    reactions: dict[str, int] = Field(default_factory=dict)


class SharedFile(BaseModel):
    id: str
    name: str | None = None
    extension: str | None = None
    kind: str | None = None
    created_at: datetime | None = None
    modified_at: datetime | None = None
    url: str | None = None
    preview_url: str | None = None
    thread_id: str | None = None
    is_recording: bool = False


class SearchHit(BaseModel):
    source: str
    ref: str
    title: str | None = None
    subtitle: str | None = None
    snippet: str | None = None
    timestamp: datetime | None = None
    score: float | None = None


class SyncReport(BaseModel):
    started_at: datetime
    finished_at: datetime | None = None
    meetings: int = 0
    transcripts: int = 0
    calls: int = 0
    conversations: int = 0
    messages: int = 0
    files: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)
