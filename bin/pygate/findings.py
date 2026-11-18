"""The one record every gate check returns."""

from dataclasses import dataclass
from pathlib import Path


__all__ = ['Finding']


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    col: int
    code: str
    message: str

    def render(self) -> str:
        return f'{self.path}:{self.line}:{self.col}: {self.code} {self.message}'
