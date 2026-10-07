"""Atomic file writes shared across the write path.

A write lands in a temp file in the destination directory and is then
``os.replace``d over the target, so a reader never sees a half-written note and
a crash leaves either the old file or the new one -- never a third state.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path


def atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp file + ``os.replace``)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f'.{path.name}.', suffix='.tmp')
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(text)
        # Bound locally so the replace is not an attribute lookup.
        replace = os.replace
        replace(tmp, path)
    except BaseException:
        with suppress(FileNotFoundError):
            tmp.unlink()
        raise


__all__ = ['atomic_write']
