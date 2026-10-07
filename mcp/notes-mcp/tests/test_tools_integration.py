"""Read-only integration smoke against the real vault (opt-in).

Run with:  pytest tests/test_tools_integration.py -m integration
Skipped automatically when qmd is absent or the vault is missing. The daemon
tests skip when nothing is listening on ``QMD_DAEMON_URL``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from notes_mcp.config import Config
from notes_mcp.context import build_context
from notes_mcp.server import build_tools


pytestmark = pytest.mark.integration

REAL_VAULT = '/Users/msetsma/notes'


@pytest.fixture
def real_tools():
    if shutil.which('qmd') is None:
        pytest.skip('qmd not installed')
    if not Path(REAL_VAULT).is_dir():
        pytest.skip('real vault not present')
    ctx = build_context(Config.from_env({'VAULT_PATH': REAL_VAULT, 'AGENT_NAME': 'integration'}))
    return build_tools(ctx)


def test_index_reads_real_categories(real_tools):
    out = real_tools['notes_index']()
    assert out['ok'] is True
    assert '11' in {category['id'] for category in out['categories']}


def test_bm25_search_returns_hits(real_tools):
    out = real_tools['notes_search']('platform engineering', rerank=False, limit=3)
    assert out['ok'] is True
    assert out['count'] >= 1
    assert all(hit['path'].endswith('.md') for hit in out['hits'])


def test_hybrid_search_returns_hits(real_tools):
    out = real_tools['notes_search']('platform engineering ownership', rerank=True, limit=3)
    assert out['ok'] is True
    assert all(hit['path'].endswith('.md') for hit in out['hits'])


def test_tag_filter_oversampling(real_tools):
    out = real_tools['notes_search']('engineering', tags=['area'], limit=3)
    assert out['ok'] is True
    assert out['count'] <= 3


def test_status_reports_qmd_and_daemon_health(real_tools):
    out = real_tools['notes_status']()
    assert out['ok'] is True
    assert out['qmd']['available'] is True
    assert 'daemon' in out['qmd']
    assert out['qmd']['daemon']['url']
