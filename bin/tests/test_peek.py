"""Offline tests for bin/peek: each runs a tiny command in peek's own hidden tmux.

Run with: uv run --with pytest pytest bin/tests/test_peek.py
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PEEK = (Path(__file__).resolve().parents[1] / 'peek').resolve()

pytestmark = pytest.mark.skipif(shutil.which('tmux') is None, reason='tmux not installed')


def peek(*args: str) -> str:
    return subprocess.run([PEEK, *args], capture_output=True, text=True, check=True, timeout=60).stdout


def test_output_of_a_command_that_exits_at_once_and_its_status() -> None:
    assert peek('-s', '40x6', '--', 'sh', '-c', 'echo hello; exit 3') == 'hello\n\n[peek: exited 3]\n'


def test_terminal_has_the_requested_size() -> None:
    assert peek('-s', '33x7', '--', 'sh', '-c', 'tput cols; tput lines').startswith('33\n7\n')


def test_keys_are_typed_in_order_and_until_waits_for_the_result() -> None:
    out = peek('-s', '40x6', '-k', 'abc', '-K', 'Enter', '-u', 'got abc', '--', 'sh', '-c', 'read x; echo "got $x"; sleep 5')
    assert 'got abc' in out


def test_named_terminal_survives_between_calls() -> None:
    try:
        peek('-S', 'peek-test', '-s', '40x5', '--', 'sh', '-c', 'while read l; do echo "> $l"; done')
        assert '> hi' in peek('-S', 'peek-test', '-k', 'hi', '-K', 'Enter')
    finally:
        subprocess.run([PEEK, '--kill', 'peek-test'], check=False)
