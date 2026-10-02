"""Meeting/transcript digest assembly, shared by the CLI and the MCP tool.

Reads from the local archive only (no network): pick recent meetings, pair each
with its stored transcript, and hand the sections to ``analytics.render_digest``.
"""

from __future__ import annotations

from datetime import datetime

from .analytics import analyse, window_label
from .models import Meeting, Transcript, TranscriptAnalytics
from .store import Store

DigestSections = list[tuple[Meeting, Transcript | None, TranscriptAnalytics | None]]


def build_digest(
    store: Store, *, start: datetime, end: datetime, include_declined: bool = False, limit: int = 500
) -> tuple[str, DigestSections]:
    """Return ``(title, sections)`` for the window ``[start, end)``."""
    meetings = store.meetings(start=start, end=end, limit=limit)
    if not include_declined:
        meetings = [m for m in meetings if m.my_response != 'Declined']

    sections: DigestSections = []
    for meeting in meetings:
        transcript = store.transcript(meeting.thread_id) if meeting.thread_id else None
        sections.append((meeting, transcript, analyse(transcript) if transcript else None))

    return f'Teams digest - {window_label(start, end)}', sections
