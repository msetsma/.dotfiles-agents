"""Tests for the qmd adapter (CONTRACT section 8).

Hermetic: the daemon HTTP call and the CLI subprocess call are both patched, so
no live daemon or real ``qmd`` binary is required.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError

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
    qmd_daemon_url: str | None = None
    qmd_timeout: float = search_mod.DEFAULT_TIMEOUT
    qmd_embed_on_write: bool = True


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


def _sse(results: list[dict]) -> str:
    payload = {'jsonrpc': '2.0', 'id': 1, 'result': {'structuredContent': {'results': results}}}
    return f'event: message\ndata: {json.dumps(payload)}\n\n'


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


class _HttpRecorder:
    """Monkeypatch target for search_mod._http_post_json."""

    def __init__(self, body: str):
        self.calls: list[tuple[str, dict, float]] = []
        self.body = body

    def __call__(self, url: str, payload: dict, *, timeout: float) -> str:
        self.calls.append((url, payload, timeout))
        return self.body


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(search_mod.shutil, 'which', lambda name: f'/fake/{name}')
    rec = _Recorder()
    monkeypatch.setattr(search_mod.subprocess, 'run', rec)
    return rec


def _arg_after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _daemon_args(rec: _HttpRecorder) -> dict:
    return rec.calls[0][1]['params']['arguments']


# --------------------------------------------------------------------------- #
# daemon path
# --------------------------------------------------------------------------- #
def test_daemon_hybrid_arguments_and_normalization(monkeypatch):
    results = [
        {'docid': '#1', 'file': 'notes/30 Resources/Tool.md', 'title': 'Tool', 'score': 0.5, 'snippet': 's'},
        {'docid': '#2', 'file': 'qmd://notes/notes/Welcome.md', 'title': 'Welcome', 'score': None, 'snippet': 'w'},
    ]
    rec = _HttpRecorder(_sse(results))
    monkeypatch.setattr(search_mod, '_http_post_json', rec)

    hits = Searcher(_Config(qmd_daemon_url='http://localhost:8181/mcp')).search('tools')

    url, payload, timeout = rec.calls[0]
    assert url == 'http://localhost:8181/mcp'
    assert timeout == search_mod.DEFAULT_TIMEOUT
    assert payload['method'] == 'tools/call'
    assert payload['params']['name'] == 'query'
    args = _daemon_args(rec)
    assert [s['type'] for s in args['searches']] == ['lex', 'vec']
    assert args['intent'] == 'tools'
    assert args['collections'] == ['notes']
    assert args['limit'] == 10

    assert [h.path for h in hits] == ['30 Resources/Tool.md', 'notes/Welcome.md']
    assert hits[0].score == pytest.approx(0.5)
    assert hits[1].score is None
    assert hits[0].title == 'Tool'
    assert hits[0].snippet == 's'
    assert all(isinstance(h, SearchHit) for h in hits)


def test_daemon_bm25_sends_lex_only(monkeypatch):
    rec = _HttpRecorder(_sse([{'file': 'notes/A.md', 'title': 'A', 'score': 1.0, 'snippet': 's'}]))
    monkeypatch.setattr(search_mod, '_http_post_json', rec)

    Searcher(_Config(qmd_daemon_url='http://x/mcp')).search('q', collection='other', limit=3, rerank=False)

    args = _daemon_args(rec)
    assert [s['type'] for s in args['searches']] == ['lex']
    assert args['collections'] == ['other']
    assert args['limit'] == 3


def test_daemon_tolerates_plain_json_body(monkeypatch):
    payload = {'result': {'structuredContent': {'results': [{'file': 'notes/A.md', 'score': 0.1}]}}}
    rec = _HttpRecorder(json.dumps(payload))
    monkeypatch.setattr(search_mod, '_http_post_json', rec)

    hits = Searcher(_Config(qmd_daemon_url='http://x/mcp')).search('q')

    assert [h.path for h in hits] == ['A.md']


def test_daemon_empty_results_is_not_a_failure(monkeypatch, fake):
    rec = _HttpRecorder(_sse([]))
    monkeypatch.setattr(search_mod, '_http_post_json', rec)

    hits = Searcher(_Config(qmd_daemon_url='http://x/mcp')).search('q')

    assert hits == []
    assert fake.calls == []  # daemon answered; CLI untouched


# --------------------------------------------------------------------------- #
# fallback to the CLI
# --------------------------------------------------------------------------- #
def test_daemon_failure_falls_back_to_cli(monkeypatch, fake):
    def boom(url: str, payload: dict, *, timeout: float) -> str:
        raise URLError('connection refused')

    monkeypatch.setattr(search_mod, '_http_post_json', boom)

    hits = Searcher(_Config(qmd_daemon_url='http://x/mcp')).search('tools')

    assert fake.calls, 'expected the CLI fallback to run'
    argv, _ = fake.calls[0]
    assert argv[1] == 'query'
    assert [h.path for h in hits] == ['30 Resources/32 Tools/32 Tools.md', 'notes/Welcome.md']


def test_daemon_configured_but_binary_missing_raises(monkeypatch):
    def boom(url: str, payload: dict, *, timeout: float) -> str:
        raise URLError('connection refused')

    monkeypatch.setattr(search_mod, '_http_post_json', boom)
    monkeypatch.setattr(search_mod.shutil, 'which', lambda name: None)

    with pytest.raises(NotesError) as excinfo:
        Searcher(_Config(qmd_daemon_url='http://x/mcp')).search('anything')

    assert excinfo.value.code == INDEX_UNAVAILABLE


def test_daemon_disabled_goes_straight_to_cli(monkeypatch, fake):
    def forbidden(url: str, payload: dict, *, timeout: float) -> str:
        raise AssertionError('daemon must not be called when disabled')

    monkeypatch.setattr(search_mod, '_http_post_json', forbidden)

    Searcher(_Config(qmd_daemon_url=None)).search('q')

    assert fake.calls


# --------------------------------------------------------------------------- #
# CLI subcommand selection + normalization
# --------------------------------------------------------------------------- #
def test_search_rerank_true_uses_query_and_normalizes(fake):
    hits = Searcher(_Config()).search('tools')

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
    Searcher(_Config()).search('q', collection='other', limit=3, rerank=False)

    argv, _ = fake.calls[0]
    assert argv[1] == 'search'
    assert _arg_after(argv, '-c') == 'other'
    assert _arg_after(argv, '-n') == '3'


def test_search_honors_config_timeout(fake):
    Searcher(_Config(qmd_timeout=12.5)).search('q')

    assert fake.calls[0][1]['timeout'] == 12.5


def test_search_rejects_removed_filter_kwarg(fake):
    with pytest.raises(TypeError):
        Searcher(_Config()).search('x', filter={'type': 'note'})


def test_search_rejects_unknown_kwargs(fake):
    with pytest.raises(TypeError):
        Searcher(_Config()).search('x', bogus=1)


# --------------------------------------------------------------------------- #
# CLI failure -> INDEX_UNAVAILABLE
# --------------------------------------------------------------------------- #
def test_missing_qmd_binary_raises_index_unavailable(monkeypatch):
    monkeypatch.setattr(search_mod.shutil, 'which', lambda name: None)

    with pytest.raises(NotesError) as excinfo:
        Searcher(_Config()).search('anything')

    assert excinfo.value.code == INDEX_UNAVAILABLE
    assert excinfo.value.hint


def test_nonzero_exit_raises_index_unavailable(monkeypatch):
    monkeypatch.setattr(search_mod.shutil, 'which', lambda name: f'/fake/{name}')
    monkeypatch.setattr(search_mod.subprocess, 'run', _Recorder(returncode=2, stdout='', stderr='model missing'))

    with pytest.raises(NotesError) as excinfo:
        Searcher(_Config()).search('anything')

    assert excinfo.value.code == INDEX_UNAVAILABLE
    assert 'model missing' in excinfo.value.message


def test_unparseable_json_raises_index_unavailable(monkeypatch):
    monkeypatch.setattr(search_mod.shutil, 'which', lambda name: f'/fake/{name}')
    monkeypatch.setattr(search_mod.subprocess, 'run', _Recorder(stdout='not json'))

    with pytest.raises(NotesError) as excinfo:
        Searcher(_Config()).search('anything')

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
def test_reindex_embeds_by_default(fake):
    Searcher(_Config(qmd_embed_on_write=True)).reindex()
    assert [call[0][1] for call in fake.calls] == ['update', 'embed']


def test_reindex_skips_embed_when_config_disables(fake):
    Searcher(_Config(qmd_embed_on_write=False)).reindex()
    assert [call[0][1] for call in fake.calls] == ['update']


def test_reindex_explicit_embed_overrides_config(fake):
    Searcher(_Config(qmd_embed_on_write=False)).reindex(embed=True)
    assert [call[0][1] for call in fake.calls] == ['update', 'embed']


def test_reindex_explicit_no_embed_overrides_config(fake):
    Searcher(_Config(qmd_embed_on_write=True)).reindex(embed=False)
    assert [call[0][1] for call in fake.calls] == ['update']


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
    assert health['daemon'] == {'up': False, 'url': None}


def test_health_daemon_up(monkeypatch):
    monkeypatch.setattr(search_mod.shutil, 'which', lambda name: None)
    monkeypatch.setattr(search_mod, '_http_get', lambda url, *, timeout: '{"status":"ok"}')

    health = Searcher(_Config(qmd_daemon_url='http://x:8181/mcp')).health()

    assert health['daemon'] == {'up': True, 'url': 'http://x:8181/mcp'}


def test_health_daemon_down_never_raises(monkeypatch):
    monkeypatch.setattr(search_mod.shutil, 'which', lambda name: None)

    def boom(url: str, *, timeout: float) -> str:
        raise URLError('refused')

    monkeypatch.setattr(search_mod, '_http_get', boom)

    health = Searcher(_Config(qmd_daemon_url='http://x:8181/mcp')).health()

    assert health['daemon']['up'] is False


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

    searcher = Searcher(_Config(qmd_daemon_url=None))
    hits = searcher.search('notes', rerank=False, limit=3)

    assert isinstance(hits, list)
    for hit in hits:
        assert not hit.path.startswith('qmd://')

    health = searcher.health()
    assert health['available'] is True
    assert health['version']
