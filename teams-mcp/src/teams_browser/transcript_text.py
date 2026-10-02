"""Render transcript entries into readable text and interchange formats."""

from __future__ import annotations

import re

from .models import TranscriptEntry

_TIMECODE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:\.(\d+))?$")


def format_transcript(entries: list[TranscriptEntry], *, timestamps: bool = False) -> str:
    """Merge consecutive lines from the same speaker into paragraphs."""
    lines: list[str] = []
    prev_speaker: str | None = None
    buffer: list[str] = []
    buffer_start = ""

    def flush() -> None:
        if not buffer:
            return
        speaker = prev_speaker or "Unknown"
        text = " ".join(buffer).strip()
        if timestamps and buffer_start:
            lines.append(f"[{_fmt_ts(buffer_start)}] {speaker}: {text}")
        else:
            lines.append(f"{speaker}: {text}")

    for entry in entries:
        text = entry.text.strip()
        if not text:
            continue
        if entry.speaker == prev_speaker:
            buffer.append(text)
            continue
        flush()
        prev_speaker = entry.speaker
        buffer = [text]
        buffer_start = entry.start

    flush()
    return "\n".join(lines)


def to_vtt(entries: list[TranscriptEntry]) -> str:
    out = ["WEBVTT", ""]
    for entry in entries:
        out.append(f"{_fmt_ts(entry.start)} --> {_fmt_ts(entry.end)}")
        if entry.speaker:
            out.append(f"<v {entry.speaker}>{entry.text}")
        else:
            out.append(entry.text)
        out.append("")
    return "\n".join(out)


def to_markdown(transcript) -> str:
    header = [f"# {transcript.meeting_subject or 'Meeting transcript'}", ""]
    if transcript.recording_start:
        header.append(f"- **Started:** {transcript.recording_start.isoformat()}")
    if transcript.recording_end:
        header.append(f"- **Ended:** {transcript.recording_end.isoformat()}")
    if transcript.speakers:
        header.append(f"- **Speakers:** {', '.join(transcript.speakers)}")
    header.append("")
    header.append("---")
    header.append("")
    return "\n".join(header) + format_transcript(transcript.entries)


def _fmt_ts(value: str) -> str:
    """Format an offset as ``HH:MM:SS.mmm`` for WebVTT.

    Accepts either a Teams timecode (``HH:MM:SS.fffffff``) or a millisecond
    integer/float.
    """
    if not value:
        return "00:00:00.000"

    match = _TIMECODE.match(value)
    if match:
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        millis = (match.group(4) or "")[:3].ljust(3, "0")
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis}"

    try:
        ms = int(float(value))
    except ValueError:
        return value
    seconds, millis = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"
