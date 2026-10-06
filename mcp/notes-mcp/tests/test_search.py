"""Tests for the qmd CLI adapter (CONTRACT section 8)."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from notes_mcp import search as search_mod
from notes_mcp.errors import INDEX_UNAVAILABLE, NotesError
from notes_mcp.search import Searcher, SearchHit


@dataclass
class _Config:
    """Duck-typed stand-in for notes_mcp.config.Config."""

    vault_path: Path = Path('/Users/msetsma/notes')
    qmd_collection: str = 'notes'
    qmd_embed_model: str | None = None
    qmd_rerank_model: str | None = None
    qmd_generate_model: str | None = None


SAMPLE = [
    {
        'docid': '#1',
        'score': 0.76,
        'file': 'qmd://notes/30 Resources/32 Tools/32 Tools.md',
        'line': 9,
        'title': '32 Tools',
        'context': 'ctx',
        'snippet': 'snip',
    },
    {
        'docid': '#2',
        'score': None,
        'file': 'qmd://notes/notes/Welcome.md',
        'line': 1,
        'title': 'Welcome',
        'context': 'ctx',
        'snippet': 'snip2',
    },
]


class _Recorder:
    """Monkeypatch target for subprocess.run; records argv/kwargs per call."""

    def __init__(self, *, returncode: int = 0, stdout: str | None = None, stderr: str = ''):
        self.calls: list[tuple[list[str], dict]] = []
        self.returncode = returncode
        self.stdout = json.dumps(SAMPLE) if stdout is None else stdout
        self.stderr = stderr

    def __call__(self, argv: list[str], **kwargs):
        self.calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, self.returncode, stdout=self.stdout, stderr=self.stderr)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(search_mod.shutil, 'which', lambda name: f'/fake/{name}')
    rec = _Recorder()
    monkeypatch.setattr(search_mod.subprocess, 'run', rec)
    return rec


def _arg_after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


# --------------------------------------------------------------------------- #
# subcommand selection + normalization
# --------------------------------------------------------------------------- #
def test_search_rerank_true_uses_query_and_normalizes(fake):
    searcher = Searcher(_Config())
    hits = searcher.search('tools')

    argv, kwargs = fake.calls[0]
    assert argv[1] == 'query'
    assert argv[2] == 'tools'
    assert _arg_after(argv, '-c') == 'notes'
    assert _arg_after(argv, '-n') == '10'
    assert kwargs['timeout'] == search_mod.DEFAULT_TIMEOUT
    assert kwargs['check'] is False

    assert [h.path for h in hits] == ['30 Resources/32 Tools/32 Tools.md', 'notes/Welcome.md']
    assert hits[0].score == pytest.approx(0.76)
    assert hits[1].score is None
    assert hits[0].title == '32 Tools'
    assert hits[0].snippet == 'snip'
    assert all(isinstance(h, SearchHit) for h in hits)


def test_search_rerank_false_uses_search_and_honors_args(fake):
    searcher = Searcher(_Config())
    searcher.search('q', collection='other', limit=3, rerank=False)

    argv, _ = fake.calls[0]
    assert argv[1] == 'search'
    assert _arg_after(argv, '-c') == 'other'
    assert _arg_after(argv, '-n') == '3'


def test_search_accepts_and_ignores_filter(fake):
    searcher = Searcher(_Config())
    searcher.search('x', filter={'type': 'note'})
    searcher.search('x', filter_={'type': 'note'})

    assert len(fake.calls) == 2
    for argv, _ in fake.calls:
        # qmd's --filter is unreliable, so it must never be passed through.
        assert '--filter' not in argv


def test_search_rejects_unknown_kwargs(fake):
    searcher = Searcher(_Config())
    with pytest.raises(TypeError):
        searcher.search('x', bogus=1)


# --------------------------------------------------------------------------- #
# failure -> INDEX_UNAVAILABLE
# --------------------------------------------------------------------------- #
def test_missing_qmd_binary_raises_index_unavailable(monkeypatch):
    monkeypatch.setattr(search_mod.shutil, 'which', lambda name: None)
    searcher = Searcher(_Config())

    with pytest.raises(NotesError) as excinfo:
        searcher.search('anything')

    assert excinfo.value.code == INDEX_UNAVAILABLE
    assert excinfo.value.hint


def test_nonzero_exit_raises_index_unavailable(monkeypatch):
    monkeypatch.setattr(search_mod.shutil, 'which', lambda name: f'/fake/{name}')
    monkeypatch.setattr(search_mod.subprocess, 'run', _Recorder(returncode=2, stdout='', stderr='model missing'))
    searcher = Searcher(_Config())

    with pytest.raises(NotesError) as excinfo:
        searcher.search('anything')

    assert excinfo.value.code == INDEX_UNAVAILABLE
    assert 'model missing' in excinfo.value.message


def test_unparseable_json_raises_index_unavailable(monkeypatch):
    monkeypatch.setattr(search_mod.shutil, 'which', lambda name: f'/fake/{name}')
    monkeypatch.setattr(search_mod.subprocess, 'run', _Recorder(stdout='not json'))
    searcher = Searcher(_Config())

    with pytest.raises(NotesError) as excinfo:
        searcher.search('anything')

    assert excinfo.value.code == INDEX_UNAVAILABLE


# --------------------------------------------------------------------------- #
# model env passthrough
# --------------------------------------------------------------------------- #
def test_model_env_passed_through(monkeypatch):
    monkeypatch.setattr(search_mod.shutil, 'which', lambda name: f'/fake/{name}')
    rec = _Recorder()
    monkeypatch.setattr(search_mod.subprocess, 'run', rec)
    config = _Config(qmd_embed_model='embed.gguf', qmd_rerank_model='rerank.gguf', qmd_generate_model='gen.gguf')

    Searcher(config).search('q')

    env = rec.calls[0][1]['env']
    assert env['QMD_EMBED_MODEL'] == 'embed.gguf'
    assert env['QMD_RERANK_MODEL'] == 'rerank.gguf'
    assert env['QMD_GENERATE_MODEL'] == 'gen.gguf'


# --------------------------------------------------------------------------- #
# reindex
# --------------------------------------------------------------------------- #
def test_reindex_runs_update_only_by_default(fake):
    Searcher(_Config()).reindex()
    assert [call[0][1] for call in fake.calls] == ['update']


def test_reindex_with_embed_runs_both(fake):
    Searcher(_Config()).reindex(embed=True)
    assert [call[0][1] for call in fake.calls] == ['update', 'embed']


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #
_VERSION = 'qmd 2.8.3 (facd35e)\n'
_STATUS = (
    'QMD Status\n'
    'Documents\n'
    '  Files:    13\n'
    '  Updated:  1h ago\n'
    '\n'
    'Models\n'
    '  Embedding:   /models/embed.gguf\n'
    '  Reranking:   /models/rerank.gguf\n'
    '  Generation:  /models/gen.gguf\n'
    '\n'
    'Examples\n'
)


class _HealthRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, argv: list[str], **kwargs):
        self.calls.append(argv)
        subcommand = argv[1] if len(argv) > 1 else ''
        stdout = _VERSION if subcommand == '--version' else _STATUS
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr='')


def test_health_parses_version_status_and_models(monkeypatch):
    monkeypatch.setattr(search_mod.shutil, 'which', lambda name: f'/fake/{name}')
    monkeypatch.setattr(search_mod.subprocess, 'run', _HealthRecorder())

    health = Searcher(_Config()).health()

    assert health['available'] is True
    assert health['version'] == '2.8.3'
    assert health['collection'] == 'notes'
    assert health['indexed_at'] == '1h ago'
    assert health['models']['embedding'] == '/models/embed.gguf'
    assert health['models']['generation'] == '/models/gen.gguf'


def test_health_never_raises_when_qmd_missing(monkeypatch):
    monkeypatch.setattr(search_mod.shutil, 'which', lambda name: None)

    health = Searcher(_Config()).health()

    assert health['available'] is False
    assert health['version'] is None


def test_health_never_raises_on_bad_status(monkeypatch):
    def boom(argv, **kwargs):
        raise OSError('qmd exploded')

    monkeypatch.setattr(search_mod.shutil, 'which', lambda name: f'/fake/{name}')
    monkeypatch.setattr(search_mod.subprocess, 'run', boom)

    health = Searcher(_Config()).health()

    assert health['available'] is True
    assert health['indexed_at'] is None


# --------------------------------------------------------------------------- #
# schedule_reindex never propagates
# --------------------------------------------------------------------------- #
def test_schedule_reindex_swallows_errors(monkeypatch):
    def boom(argv, **kwargs):
        raise OSError('no qmd')

    monkeypatch.setattr(search_mod.shutil, 'which', lambda name: f'/fake/{name}')
    monkeypatch.setattr(search_mod.subprocess, 'run', boom)

    searcher = Searcher(_Config())
    searcher.schedule_reindex(delay=0.01)  # must not raise
    searcher.schedule_reindex(delay=0.01)  # coalesces onto one pending timer


# --------------------------------------------------------------------------- #
# integration (real qmd + real vault)
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_integration_real_qmd_bm25():
    if shutil.which('qmd') is None:
        pytest.skip('qmd not installed')

    searcher = Searcher(_Config())
    hits = searcher.search('notes', rerank=False, limit=3)

    assert isinstance(hits, list)
    for hit in hits:
        assert not hit.path.startswith('qmd://')

    health = searcher.health()
    assert health['available'] is True
    assert health['version']
