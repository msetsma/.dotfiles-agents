"""Provider seam.

The browser-session implementation is the default. When enterprise Graph read
access becomes available, implement the same method surface in a ``graph``
provider and swap it in behind :class:`teams_browser.client.TeamsClient` without
touching the CLI or MCP tools.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from ..models import Meeting, Transcript


class MeetingProvider(Protocol):
    def list_meetings(
        self, *, start: datetime | None = None, end: datetime | None = None, limit: int = 50
    ) -> list[Meeting]: ...

    def find_meetings(
        self,
        subject: str,
        *,
        on_date: date | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[Meeting]: ...

    def get_transcript(
        self, thread_id: str, *, subject: str | None = None, meeting_date: datetime | None = None
    ) -> Transcript: ...
