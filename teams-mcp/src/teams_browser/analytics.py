"""Local transcript analytics and digest rendering.

Everything here operates on data we already downloaded - no API calls. The
metrics are deliberately simple and explainable (talk time, turns, words) rather
than trying to be clever: an LLM consuming this can do the interpretation.
"""

from __future__ import annotations

import re
from datetime import datetime

from .models import Meeting, SpeakerStat, Transcript, TranscriptAnalytics, TranscriptEntry


_TIMECODE = re.compile(r'^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:\.(\d+))?$')
_WORD = re.compile(r"[A-Za-z0-9']+")


def to_seconds(value: str | float | int | None) -> float | None:
    """Convert a timecode (``HH:MM:SS.fffffff``) or millisecond value to seconds."""
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1000.0
    match = _TIMECODE.match(str(value).strip())
    if not match:
        try:
            return float(value) / 1000.0
        except (TypeError, ValueError):
            return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    fraction = float('0.' + (match.group(4) or '0').ljust(3, '0')[:3])
    return hours * 3600 + minutes * 60 + seconds + fraction


def format_seconds(total: float) -> str:
    total = max(0.0, total)
    minutes, seconds = divmod(int(round(total)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f'{hours}h{minutes:02d}m'
    return f'{minutes}m{seconds:02d}s'


def merge_consecutive(entries: list[TranscriptEntry]) -> list[TranscriptEntry]:
    """Collapse runs of consecutive turns from the same speaker."""
    merged: list[TranscriptEntry] = []
    for entry in entries:
        text = entry.text.strip()
        if not text:
            continue
        if merged and merged[-1].speaker == entry.speaker:
            prev = merged[-1]
            prev.text = f'{prev.text} {text}'.strip()
            prev.end = entry.end or prev.end
            continue
        merged.append(entry.model_copy(update={'text': text}))
    return merged


def slice_entries(
    entries: list[TranscriptEntry], *, start: str | float | None = None, end: str | float | None = None
) -> list[TranscriptEntry]:
    """Keep entries overlapping the ``[start, end]`` window (timecodes or seconds)."""
    lower = to_seconds(start)
    upper = to_seconds(end)
    if lower is None and upper is None:
        return list(entries)
    out: list[TranscriptEntry] = []
    for entry in entries:
        begin = to_seconds(entry.start)
        finish = to_seconds(entry.end) or begin
        if begin is None:
            continue
        if lower is not None and finish is not None and finish < lower:
            continue
        if upper is not None and begin > upper:
            continue
        out.append(entry)
    return out


def speaker_stats(entries: list[TranscriptEntry]) -> tuple[list[SpeakerStat], float]:
    talk: dict[str, float] = {}
    turns: dict[str, int] = {}
    words: dict[str, int] = {}
    order: list[str] = []
    total = 0.0

    cleaned = merge_consecutive(entries)
    for index, entry in enumerate(cleaned):
        speaker = entry.speaker or 'Unknown'
        if speaker not in order:
            order.append(speaker)
        turns[speaker] = turns.get(speaker, 0) + 1
        words[speaker] = words.get(speaker, 0) + len(_WORD.findall(entry.text))

        begin = to_seconds(entry.start)
        finish = to_seconds(entry.end)
        if finish is None:
            nxt = cleaned[index + 1].start if index + 1 < len(cleaned) else None
            finish = to_seconds(nxt)
        duration = max(0.0, (finish - begin)) if (begin is not None and finish is not None) else 0.0
        talk[speaker] = talk.get(speaker, 0.0) + duration
        total += duration

    stats = [
        SpeakerStat(
            speaker=speaker,
            turns=turns.get(speaker, 0),
            words=words.get(speaker, 0),
            talk_seconds=round(talk.get(speaker, 0.0), 1),
            share=round(talk.get(speaker, 0.0) / total, 4) if total else 0.0,
        )
        for speaker in order
    ]
    stats.sort(key=lambda s: s.talk_seconds, reverse=True)
    return stats, total


def analyse(transcript: Transcript) -> TranscriptAnalytics:
    stats, total = speaker_stats(transcript.entries)
    words = sum(s.words for s in stats)
    span = _recording_span(transcript)
    return TranscriptAnalytics(
        thread_id=transcript.thread_id,
        meeting_subject=transcript.meeting_subject,
        duration_seconds=round(span or total, 1),
        entry_count=len(transcript.entries),
        word_count=words,
        speakers=stats,
    )


def _recording_span(transcript: Transcript) -> float:
    if transcript.recording_start and transcript.recording_end:
        return (transcript.recording_end - transcript.recording_start).total_seconds()
    return 0.0


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render_analytics(analytics: TranscriptAnalytics) -> str:
    lines = [
        f'# {analytics.meeting_subject or "Meeting"} - transcript analytics',
        '',
        f'- **Duration:** {format_seconds(analytics.duration_seconds)}',
        f'- **Entries:** {analytics.entry_count}',
        f'- **Words:** {analytics.word_count}',
        f'- **Speakers:** {len(analytics.speakers)}',
        '',
        '| Speaker | Talk time | Share | Turns | Words |',
        '| --- | --- | --- | --- | --- |',
    ]
    for stat in analytics.speakers:
        lines.append(
            f'| {stat.speaker} | {format_seconds(stat.talk_seconds)} | '
            f'{stat.share * 100:.0f}% | {stat.turns} | {stat.words} |'
        )
    return '\n'.join(lines) + '\n'


def render_digest(
    *,
    title: str,
    sections: list[tuple[Meeting, Transcript | None, TranscriptAnalytics | None]],
    include_transcripts: bool = True,
) -> str:
    lines = [f'# {title}', '']
    for meeting, transcript, analytics in sections:
        when = meeting.start_time.strftime('%a %d %b %Y %H:%M') if meeting.start_time else 'unknown'
        lines.append(f'## {meeting.subject or "Untitled meeting"}')
        lines.append('')
        lines.append(f'- **When:** {when}')
        if meeting.organizer_name:
            lines.append(f'- **Organizer:** {meeting.organizer_name}')
        if transcript is None:
            lines.append('- **Transcript:** not available')
            lines.append('')
            continue
        lines.append(f'- **Speakers:** {", ".join(transcript.speakers) or "unknown"}')
        if analytics:
            lines.append(f'- **Talk time:** {format_seconds(analytics.duration_seconds)}')
            lines.append(f'- **Words:** {analytics.word_count}')
        lines.append('')
        if analytics and analytics.speakers:
            lines.append('| Speaker | Talk time | Share |')
            lines.append('| --- | --- | --- |')
            for stat in analytics.speakers:
                lines.append(f'| {stat.speaker} | {format_seconds(stat.talk_seconds)} | {stat.share * 100:.0f}% |')
            lines.append('')
        if include_transcripts:
            lines.append('<details><summary>Transcript</summary>')
            lines.append('')
            lines.append(transcript.text)
            lines.append('')
            lines.append('</details>')
            lines.append('')
    return '\n'.join(lines)


def window_label(start: datetime | None, end: datetime | None) -> str:
    if not start:
        return 'all time'
    if not end:
        return start.strftime('%d %b %Y')
    if start.date() == end.date():
        return start.strftime('%d %b %Y')
    return f'{start.strftime("%d %b %Y")} - {end.strftime("%d %b %Y")}'
