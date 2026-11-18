"""Put bin/ on sys.path so tests import the engine modules from where they live."""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
