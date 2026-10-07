"""Small shared helpers for the internal API clients."""

from __future__ import annotations

import re
from datetime import UTC, datetime


_FRACTION = re.compile(r'(\.\d{6})\d+')


def parse_dt(value: object) -> datetime | None:
    """Parse a Teams/Substrate timestamp.

    Teammate timestamps often carry 7 fractional-second digits
    (``...T13:36:08.4380000Z``), which strict ISO parsers reject, so the
    fraction is trimmed to 6 digits first.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = _FRACTION.sub(r'\1', value.strip().replace('Z', '+00:00'))
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def from_epoch_ms(value: object) -> datetime | None:
    if not isinstance(value, (int, float)):
        if not isinstance(value, str) or not value.isdigit():
            return None
        value = int(value)
    if value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)
